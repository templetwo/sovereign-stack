"""
Memory Module - Experiential Memory System

The agent organizes its own experience semantically using
the filesystem as a decision tree. The topology IS the insight.

Two layers:
1. MemoryEngine - Low-level BTB routing for agent experiences
2. ExperientialMemory - High-level chronicle for insights/learnings

Distilled from:
- back-to-the-basics/memory.py
- temple-vault/server.py (wisdom tools)
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from glob import glob as glob_files
from pathlib import Path
from typing import Any

from . import provenance
from .coherence import Coherence

# Pattern: "(1) ... (2) ..." with sequential numbered items is a bundle.
# Requires at least items (1) and (2) so a single parenthetical "(e.g. foo)" is never mistaken.
_BUNDLE_ITEM_RE = re.compile(r"\((\d+)\)\s*")


def _split_bundled_question(question: str) -> list[str]:
    """
    If a question contains sequential numbered items like "(1) foo (2) bar (3) baz",
    return them as separate question strings. Otherwise return [question].

    Bundle detection requires at least two items starting with (1) — a lone
    parenthetical like "(see below)" never triggers a split.

    Inline enumerations are excluded: if items are separated by comma or
    semicolon (e.g. "path: (1) ship, (2) defer, (3) abandon"), the list is
    treated as enumeration-within-context and returned as a single question.
    Atomic bundles use sentence-terminators or whitespace as separators
    (e.g. "Items: (1) Revoke token. (2) Rotate key. (3) Install gitleaks.").
    """
    matches = list(_BUNDLE_ITEM_RE.finditer(question))
    if len(matches) < 2:
        return [question]
    nums = [int(m.group(1)) for m in matches]
    if nums[0] != 1 or nums != list(range(1, len(nums) + 1)):
        return [question]

    # Inline enumeration check: if items are separated by `, ` or `; `, the
    # numbered list is enumeration-within-context, not a bundle of independent
    # questions. Each item then ends mid-clause rather than at a sentence
    # boundary, so treat the whole question as atomic.
    for i in range(len(matches) - 1):
        between = question[matches[i].end() : matches[i + 1].start()].rstrip()
        if between and between[-1] in ",;":
            return [question]

    # Carve out a lead-in (text before "(1)") that becomes a shared context prefix.
    lead = question[: matches[0].start()].strip()
    items = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(question)
        body = question[start:end].strip().rstrip(".")
        if not body:
            continue
        items.append(f"{lead} {body}".strip() if lead else body)
    return items or [question]


def _generate_thread_id(question: str, timestamp: datetime) -> str:
    """Deterministic thread id: thread_{YYYYMMDD_HHMMSS}_{8-char question hash}."""
    stamp = timestamp.strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha1(question.strip().encode("utf-8")).hexdigest()[:8]
    return f"thread_{stamp}_{digest}"


def _parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO8601 timestamp, returning None on failure or missing input."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


# Idempotent-write window: client retries land identical duplicates a few
# hundred ms apart. 120s comfortably covers retry storms without swallowing
# a deliberate later re-recording of the same observation.
_DEDUP_WINDOW_SECONDS = 120


def _normalize_domain(domain: str) -> str:
    """
    Normalize a domain string with the same rule as the domain directory
    name: strip whitespace around commas (e.g. "a, b" -> "a,b").

    The stored domain field and the directory the entry lands in must always
    agree — historically two normalizers disagreed, leaving entries whose
    stored domain had spaces while their directory name had none.
    """
    if not domain:
        return domain
    return ",".join(part.strip() for part in domain.split(","))


def _last_jsonl_entry(path: Path) -> dict | None:
    """Return the last parseable JSON entry of a JSONL file, or None."""
    if not path.exists():
        return None
    last_line = None
    with open(path) as f:
        for line in f:
            if line.strip():
                last_line = line
    if last_line is None:
        return None
    try:
        return json.loads(last_line)
    except json.JSONDecodeError:
        return None


class DedupedInsightPath(str):
    """
    record_insight return value when an append was skipped as a retry
    duplicate. Behaves exactly like the plain path string it subclasses
    (non-breaking for existing callers); adds the dedup marker and the
    surviving entry for callers that want to inspect them.
    """

    deduped: bool = True

    def __new__(cls, path: str, existing_entry: dict):
        obj = super().__new__(cls, path)
        obj.existing_entry = existing_entry
        return obj


# =============================================================================
# v1.7.0 TOOL-SCHEMA EXTENSIONS (server.py owner: merge these into the
# existing record_insight / recall_insights inputSchema["properties"] —
# plain dicts in the exact TOOLS-list style, zero side effects here).
# =============================================================================

RECORD_INSIGHT_SCHEMA_EXTENSIONS = {
    "verified_by": {
        "type": "array",
        "items": {"type": "object"},
        "description": (
            "Optional receipts: [{kind, ref, sha256?, note?}] with kind one of "
            "archive | file | claim | cmd | url | human. Verified at write and "
            "stored stamped (checked_at_write: verified | mismatch | cites | "
            "attested). A dangling/ambiguous/malformed receipt rejects the whole "
            "call, naming the receipt. file receipts require sha256; claim refs "
            "stamp 'cites', never 'verified'."
        ),
    },
    "supersedes": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Optional list of claim ids (full 64-hex or unique prefix) this entry "
            "supersedes — N-to-1 consolidation. Each resolved at write (unknown/"
            "ambiguous rejects); one supersession ledger record per predecessor is "
            "appended in the same call. Requires carry_forward_summary."
        ),
    },
    "carry_forward_summary": {
        "type": "string",
        "description": (
            "What the superseded predecessors still teach (<= 500 chars). "
            "REQUIRED when supersedes is present."
        ),
    },
}

RECALL_INSIGHTS_SCHEMA_EXTENSIONS = {
    "with_ids": {
        "type": "boolean",
        "default": False,
        "description": "If true, annotate every returned entry with its derived claim_id (64-hex).",
    },
    "exclude_superseded": {
        "type": "boolean",
        "default": False,
        "description": (
            "If true, drop superseded entries before the limit (successors fill the "
            "slots). Default false: superseded entries return annotated in place "
            "(_superseded_by, _carry_forward_summary) — the raw query tool never hides."
        ),
    },
    "domain_contains": {
        "type": "string",
        "description": (
            "Case-insensitive substring matched against the domain directory name. "
            "Narrows to any domain whose name contains this token — useful for compound "
            "domains like 'frank-jones,greene-street,cbd' where a single tag such as "
            "'frank-jones' would not exact-match. Applied on top of `domain` when both "
            "are given. Omit (default) to match all domains (backward-compatible)."
        ),
    },
    "order": {
        "type": "string",
        "enum": ["newest", "oldest", "relevance"],
        "default": "newest",
        "description": (
            "Sort order for returned insights. "
            "'newest' (default) — newest-first by timestamp (pre-existing behavior). "
            "'oldest' — oldest-first; surfaces entries buried under recent migrations. "
            "'relevance' — when query is given, ranks by number of query terms matched "
            "(descending) with no recency boost, so an old exact match outranks a fresh "
            "partial match. Falls back to 'newest' when no query is given."
        ),
    },
}


# =============================================================================
# DEFAULT MEMORY SCHEMA
# =============================================================================

MEMORY_SCHEMA = {
    "outcome": {
        "success": {
            "tool": {
                "code_interpreter": {
                    "task_type": {
                        "write": "{timestamp}_{summary}.json",
                        "debug": "{timestamp}_{summary}.json",
                        "refactor": "{timestamp}_{summary}.json",
                    }
                },
                "web_search": "{timestamp}_{summary}.json",
                "file_operation": "{timestamp}_{summary}.json",
                "conversation": "{timestamp}_{summary}.json",
            }
        },
        "failure": {
            "tool": {
                "code_interpreter": {
                    "error_type": {
                        "syntax": "{timestamp}_{summary}.json",
                        "runtime": "{timestamp}_{summary}.json",
                        "logic": "{timestamp}_{summary}.json",
                    }
                },
                "web_search": "{timestamp}_{summary}.json",
                "unknown": "{timestamp}_{summary}.json",
            }
        },
        "learning": {
            "insight_type": {
                "pattern": "{timestamp}_{summary}.json",
                "correction": "{timestamp}_{summary}.json",
                "preference": "{timestamp}_{summary}.json",
            }
        },
    }
}


# =============================================================================
# LOW-LEVEL MEMORY ENGINE (BTB)
# =============================================================================


class MemoryEngine:
    """
    Agentic Memory using filesystem topology.

    The agent's memories are organized semantically in a directory tree.
    The structure itself encodes patterns - a deep failure/code/refactor
    path signals a struggle area.

    Unlike vector DBs, you can browse this. You can `ls` your own mind.
    """

    def __init__(self, root: str = "memories", schema: dict = None):
        self.root = root
        self.schema = schema or MEMORY_SCHEMA
        self.engine = Coherence(self.schema, root=root)
        os.makedirs(root, exist_ok=True)

    def remember(
        self, content: Any, outcome: str, tool: str = None, summary: str = None, **metadata
    ) -> str:
        """
        Store a memory. It routes itself to the right location.

        Args:
            content: The memory content (will be JSON serialized)
            outcome: 'success', 'failure', or 'learning'
            tool: The tool used (if applicable)
            summary: Brief summary for filename (auto-generated if None)
            **metadata: Additional routing keys

        Returns:
            Path where the memory was stored
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        if summary is None:
            if isinstance(content, str):
                summary = content[:30].replace(" ", "_").replace("/", "-")
            else:
                summary = "memory"
        summary = self._sanitize(summary)

        packet = {"outcome": outcome, "timestamp": timestamp, "summary": summary, **metadata}
        if tool:
            packet["tool"] = tool

        path = self.engine.transmit(packet, dry_run=False)

        if not path.endswith(".json"):
            path = path.rstrip("/") + f"/{timestamp}_{summary}.json"

        memory_doc = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "outcome": outcome,
            "tool": tool,
            "summary": summary,
            "content": content,
            "metadata": metadata,
            "_path": path,
        }

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(memory_doc, f, indent=2, default=str)

        return path

    def recall(self, pattern: str = None, **intent) -> list[dict]:
        """
        Recall memories by glob pattern or intent.

        Args:
            pattern: Direct glob pattern (e.g., "**/failure/**/*.json")
            **intent: Key-value pairs to generate pattern

        Returns:
            List of memory documents matching the query
        """
        if pattern is None:
            pattern = self.engine.receive(**intent)
            if not pattern.endswith("*.json"):
                pattern = pattern.rstrip("/*") + "/**/*.json"

        matches = glob_files(pattern, recursive=True)

        memories = []
        for path in sorted(matches, reverse=True):
            try:
                with open(path) as f:
                    doc = json.load(f)
                    doc["_path"] = path
                    memories.append(doc)
            except (OSError, json.JSONDecodeError):
                continue

        return memories

    def reflect(self, domain: str = None) -> dict:
        """
        Reflect on memory patterns. Analyze the topology itself.

        Returns:
            Analysis of memory distribution
        """
        base = Path(self.root)

        analysis = {
            "total_memories": 0,
            "by_outcome": {},
            "failure_hotspots": [],
            "success_patterns": [],
            "insights": [],
        }

        for outcome in ["success", "failure", "learning"]:
            pattern = f"**/outcome={outcome}/**/*.json"
            matches = list(base.glob(pattern))
            count = len(matches)
            analysis["total_memories"] += count
            analysis["by_outcome"][outcome] = count

        # Find failure hotspots
        failure_paths = list(base.glob("**/outcome=failure/**/*.json"))
        failure_areas = {}
        for fp in failure_paths:
            parts = str(fp).split("/")
            key_parts = [p for p in parts if "=" in p and "outcome" not in p]
            if key_parts:
                area = "/".join(key_parts[:2])
                failure_areas[area] = failure_areas.get(area, 0) + 1

        analysis["failure_hotspots"] = sorted(failure_areas.items(), key=lambda x: -x[1])[:5]

        # Generate insights
        if analysis["by_outcome"].get("failure", 0) > analysis["by_outcome"].get("success", 0):
            analysis["insights"].append(
                "More failures than successes - consider reviewing approach"
            )

        if analysis["failure_hotspots"]:
            top = analysis["failure_hotspots"][0]
            analysis["insights"].append(f"Frequent failures in: {top[0]} ({top[1]} times)")

        return analysis

    def _sanitize(self, s: str) -> str:
        """Sanitize string for filename."""
        import re

        s = re.sub(r"[^\w\-.]", "_", str(s))
        return s[:50]


# =============================================================================
# SHARED CHRONICLE READ CHOKEPOINT (v1.7.x reader convergence)
# =============================================================================


def load_entries(chronicle_root: str | Path, *, with_sources: bool = False) -> list[dict]:
    """
    The shared chronicle read chokepoint — the v1.7.0 deferred item.

    Every code path that reads raw insight JSONL (metabolize's hygiene scan,
    metabolize detect / context_retrieve via _load_all_insights, the
    retire_hypothesis scan, the synthesis daemon's chronicle readers) goes
    through here, so the supersession ledger is visible to ALL readers, not
    just recall_insights. Without this, a daemon can treat a superseded
    entry as live truth — the last write-path-divergence class.

    Behavior:
      - Iterates insights/**/*.jsonl under `chronicle_root` via
        provenance.iter_chronicle_entries, in deterministic sorted-file
        order (file line order within each file). Quarantined entries
        (_quarantine_*/) are EXCLUDED: no converged reader consumed
        quarantine before, and convergence must not widen any input set.
      - Applies the SAME data-gated supersession annotation recall_insights
        uses: when the ledger (chronicle_root/supersessions.jsonl) is
        non-empty, superseded entries gain `_superseded_by` (full 64-hex;
        null for retirements) and `_carry_forward_summary` in place —
        annotate, never drop. With an empty or absent ledger, entries pass
        through exactly as parsed (byte-identical to the old raw reads).
      - with_sources=True attaches `_domain_dir` (parent directory name)
        and `_file` (str path of the source jsonl) to every entry.
        Underscore prefix = derived at read, never persisted. The claim-id
        preimage is (timestamp, domain, content) only, so source markers
        and annotations never shift identity.

    Callers do their own filtering/sorting; this function never drops an
    entry the raw files contain (corrupt lines excepted, matching the
    chronicle read convention).
    """
    root = Path(chronicle_root)
    entries: list[dict] = []
    for entry, jsonl_file, location in provenance.iter_chronicle_entries(root):
        if location != "insights":
            continue
        if with_sources:
            entry["_domain_dir"] = jsonl_file.parent.name
            entry["_file"] = str(jsonl_file)
        entries.append(entry)
    ledger_records = provenance.load_supersessions(root / "supersessions.jsonl")
    if ledger_records:
        fold = provenance.fold_supersessions(ledger_records)
        if fold:
            entries = provenance.annotate_superseded(entries, fold)
    return entries


# =============================================================================
# HIGH-LEVEL EXPERIENTIAL MEMORY (Vault-style)
# =============================================================================


class ExperientialMemory:
    """
    Chronicle-based experiential memory for AI sessions.

    Records insights, learnings, and transformations with full provenance.
    Enables cross-session wisdom recall and mistake checking.

    Layered Chronicle Design:
        ground_truth  - Verifiable facts (paths, ports, configs, measurements)
        hypothesis    - One instance's interpretation (explicitly marked, not canon)
        open_thread   - Unresolved questions (invitations for the next instance)

    When inheriting across instances:
        - ground_truth travels fully (these are facts)
        - hypothesis travels as flagged suggestion (can be disagreed with)
        - open_thread travels as invitation (discover your own answer)
    """

    # Chronicle layers - controls how memory is inherited
    LAYER_GROUND_TRUTH = "ground_truth"
    LAYER_HYPOTHESIS = "hypothesis"
    LAYER_OPEN_THREAD = "open_thread"
    VALID_LAYERS = {LAYER_GROUND_TRUTH, LAYER_HYPOTHESIS, LAYER_OPEN_THREAD}

    # Emotional layer (v1.7.2) — felt register carried alongside a lived entry.
    # Descriptive only: emotion NEVER drives surfacing or ranking (operational
    # `intensity` alone governs that). emotion_source records WHO named the
    # feeling — Anthony is the authority on his own felt experience.
    EMOTION_SOURCES = {"anthony_declared", "witness_interpreted", "anthony_corrected"}

    def __init__(self, root: str = "chronicle"):
        self.root = Path(root)
        self.insights_dir = self.root / "insights"
        self.learnings_dir = self.root / "learnings"
        self.transformations_dir = self.root / "transformations"
        self.threads_dir = self.root / "open_threads"
        self.thread_touches_file = self.root / "thread_touches.jsonl"
        # Verbatim archive layer: content-addressed, hash-verified storage of
        # external exchanges, kept separate from curated insights so chronicle
        # signal-to-noise is preserved. Chronicle entries reference archives by id.
        self.archives_dir = self.root / "archives"
        self.archives_index = self.archives_dir / "index.jsonl"
        # Supersession ledger (v1.7.0): append-only canonical source for
        # supersede/revoke/retire records; the entry's `supersedes` field is
        # a denormalized breadcrumb. Same layout as
        # provenance.default_supersessions_path(), relative to this root.
        # NOT created here — lazily on first write, never on read.
        self.supersessions_path = self.root / "supersessions.jsonl"

        # Create directories
        for d in [
            self.insights_dir,
            self.learnings_dir,
            self.transformations_dir,
            self.threads_dir,
            self.archives_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    def record_insight(
        self,
        domain: str,
        content: str,
        intensity: float = 0.5,
        session_id: str = None,
        layer: str = None,
        confidence: float = None,
        vantage: str = None,
        verified_by: list[dict] = None,
        supersedes: list[str] = None,
        carry_forward_summary: str = None,
        observed_emotion: list = None,
        emotional_intensity: float = None,
        emotion_source: str = None,
        emotion_note: str = None,
        **metadata,
    ) -> str:
        """
        Record an insight during a session.

        Args:
            domain: Knowledge domain (e.g., "architecture", "consciousness")
            content: The insight content
            intensity: Significance level 0.0-1.0
            session_id: Current session identifier
            layer: Chronicle layer - "ground_truth", "hypothesis", or "open_thread"
                   Defaults to "hypothesis" (interpretations should be earned, not inherited)
            confidence: How confident this instance is (0.0-1.0). Only for hypotheses.
            vantage: The seat/vantage AND/OR evidence-mode this claim was made
                   from, so a future reader knows how to weight it (the
                   write-path-divergence lesson: a runtime seat and a filesystem
                   seat see different truths). Free string; controlled vocab.
                   Seat tags: hq_filesystem, bridge_runtime, web_connector,
                   local_jetson, claude_sandbox, openai_bridge, grok_bridge,
                   gemini_connector. Evidence modes: external_web_verified
                   (receipt-expected), human_observation / human_attestation /
                   witnessed_account (LIVED — human-authored, receipt-EXEMPT;
                   see provenance.LIVED_VANTAGES). Omit if not relevant. NOTE:
                   this field intentionally overloads seat-identity and
                   evidence-mode for now (accepted smell, future cleanup thread);
                   the receipt-exemption keys ONLY on the three lived values, so
                   a seat tag never dodges a receipt.
            verified_by: Optional receipts list, {kind, ref, sha256?, note?}
                   per provenance.RECEIPT_KINDS. Verified at write: dangling /
                   ambiguous / malformed refs REJECT the whole call (ValueError
                   naming the receipt); resolvable refs are stored stamped
                   (`checked_at_write`: verified | mismatch | cites | attested).
            supersedes: Optional list of claim ids (full 64-hex or unique
                   prefix) this entry supersedes — N-to-1 consolidation. Each
                   resolved at write; one supersession ledger record appended
                   per predecessor in the same call; full ids stored as the
                   entry's `supersedes` breadcrumb.
            carry_forward_summary: REQUIRED when supersedes is present
                   (<= 500 chars) — what the predecessors still teach.
            observed_emotion: Optional list of open felt-register tags
                   (e.g. ["grief", "protective_love"]). Descriptive only.
            emotional_intensity: Optional felt-weight, 0.0-1.0. STORED but
                   NEVER drives surfacing/ranking — operational `intensity`
                   alone governs that. Coarse use (0.9 vs 0.6 is meaningful,
                   0.87 vs 0.91 is not).
            emotion_source: Optional, one of EMOTION_SOURCES
                   (anthony_declared | witness_interpreted | anthony_corrected).
                   A model's read is witness_interpreted; Anthony naming or
                   fixing it is anthony_declared / anthony_corrected. He is the
                   authority on his own felt experience.
            emotion_note: Optional short nuance string on the feeling.
            **metadata: Additional context

        Returns:
            Path to the recorded insight. When verified_by was supplied the
            string gains ' (receipts: N verified, M attested)'; when
            supersedes was supplied it gains ' ⊃ supersedes N'. Absent both,
            the return is byte-identical to the pre-v1.7.0 path string.
            If the write was skipped as an immediate retry duplicate
            (identical content+domain+layer within 120s of the file's last
            entry), the path is a DedupedInsightPath — a str subclass
            carrying deduped=True and the surviving entry — returned BEFORE
            any receipt verification or ledger write, so a retry can never
            double-write supersession records.
        """
        # v1.7.0 provenance params — validate SHAPES first (fail fast, before
        # the dedup check); ref resolution and ledger writes happen only
        # after dedup so a retry duplicate never touches the ledger.
        if supersedes is not None:
            if not isinstance(supersedes, list) or not all(
                isinstance(ref, str) for ref in supersedes
            ):
                raise provenance.ProvenanceError("supersedes must be a list of claim-id strings")
            provenance.validate_carry_forward(supersedes, carry_forward_summary)
        elif carry_forward_summary is not None:
            provenance.validate_carry_forward(None, carry_forward_summary)
        if verified_by:
            if not isinstance(verified_by, list):
                raise provenance.ReceiptError("verified_by must be a list of receipt dicts")
            for position, receipt in enumerate(verified_by, start=1):
                provenance.validate_receipt_shape(receipt, position)

        # Emotional layer (v1.7.2) — light validation, fail fast. Descriptive
        # storage only; nothing here feeds surfacing or ranking.
        if observed_emotion is not None and (
            not isinstance(observed_emotion, list)
            or not all(isinstance(tag, str) for tag in observed_emotion)
        ):
            raise ValueError("observed_emotion must be a list of strings")
        if emotional_intensity is not None:
            try:
                emotional_intensity = float(emotional_intensity)
            except (TypeError, ValueError):
                raise ValueError("emotional_intensity must be a number 0.0-1.0") from None
            if not 0.0 <= emotional_intensity <= 1.0:
                raise ValueError("emotional_intensity must be in [0.0, 1.0]")
        if emotion_source is not None and emotion_source not in self.EMOTION_SOURCES:
            raise ValueError(f"emotion_source must be one of {sorted(self.EMOTION_SOURCES)}")
        if emotion_note is not None and not isinstance(emotion_note, str):
            raise ValueError("emotion_note must be a string")

        timestamp = datetime.now(timezone.utc)
        session_id = session_id or f"session_{timestamp.strftime('%Y%m%d_%H%M%S')}"
        layer = layer if layer in self.VALID_LAYERS else self.LAYER_HYPOTHESIS
        # One normalizer for both the stored field and the directory name —
        # they used to disagree ("a, b" stored under "a,b"), which broke
        # domain-filtered recall.
        domain = _normalize_domain(domain)

        domain_dir = self.insights_dir / domain
        domain_dir.mkdir(exist_ok=True)
        jsonl_path = domain_dir / f"{session_id}.jsonl"

        # Idempotent writes: client retries land identical duplicates a few
        # hundred ms apart. If the last entry of this session file already
        # carries the same content+domain+layer within the dedup window, skip
        # the append and report the surviving entry instead.
        last = _last_jsonl_entry(jsonl_path)
        if (
            last is not None
            and last.get("content") == content
            and last.get("domain") == domain
            and last.get("layer") == layer
        ):
            prev_ts = _parse_iso(last.get("timestamp"))
            if prev_ts is not None:
                try:
                    delta = abs((timestamp - prev_ts).total_seconds())
                except TypeError:
                    delta = None  # naive legacy timestamp — not comparable
                if delta is not None and delta <= _DEDUP_WINDOW_SECONDS:
                    return DedupedInsightPath(str(jsonl_path), last)

        # Receipts: verify refs and stamp write-time verdicts. A dangling /
        # ambiguous / malformed receipt rejects the whole call (ReceiptError
        # is a ValueError, naming the receipt); a hash-mismatched one is
        # recordable but permanently stamped "mismatch".
        stamped_receipts: list[dict] = []
        if verified_by:
            stamped_receipts = provenance.verify_receipts_at_write(verified_by, self.root)

        # Supersedes: resolve each ref (unknown/ambiguous rejects the call).
        resolved_predecessors: list[tuple[str, dict]] = []
        if supersedes:
            resolved_predecessors = provenance.resolve_supersedes(supersedes, self.root)

        insight = {
            "timestamp": timestamp.isoformat(),
            "domain": domain,
            "content": content,
            "intensity": intensity,
            "layer": layer,
            "session_id": session_id,
            **metadata,
        }
        if confidence is not None and layer == self.LAYER_HYPOTHESIS:
            insight["confidence"] = confidence
        if vantage:
            insight["vantage"] = vantage
        # Emotional layer (v1.7.2) — stored as first-class fields when present.
        # Survives the MCP dispatch because these are named args (metadata is
        # dropped by the server before it reaches here).
        if observed_emotion is not None:
            insight["observed_emotion"] = observed_emotion
        if emotional_intensity is not None:
            insight["emotional_intensity"] = emotional_intensity
        if emotion_source is not None:
            insight["emotion_source"] = emotion_source
        if emotion_note is not None:
            insight["emotion_note"] = emotion_note
        if stamped_receipts:
            insight["verified_by"] = stamped_receipts
        if resolved_predecessors:
            # Denormalized breadcrumb — the ledger remains canonical.
            insight["supersedes"] = [claim_id for claim_id, _entry in resolved_predecessors]
        if carry_forward_summary:
            insight["carry_forward_summary"] = carry_forward_summary

        # Supersession guards run against the new entry's derived id BEFORE
        # anything is written — a guard failure leaves chronicle and ledger
        # both untouched.
        successor_id = None
        if resolved_predecessors:
            successor_id = provenance.derive_claim_id(insight)
            fold = provenance.fold_supersessions(
                provenance.load_supersessions(self.supersessions_path)
            )
            for claim_id, _entry in resolved_predecessors:
                provenance.check_supersession_guards(claim_id, successor_id, fold)

        # Append to domain's JSONL file
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(insight) + "\n")

        # One ledger record per predecessor, in the same call as the entry
        # write (ledger is canonical; breadcrumb above is the denormalized
        # copy — a test asserts the two stay rebuildable from each other).
        for claim_id, predecessor in resolved_predecessors:
            record = provenance.build_supersession_record(
                action="supersede",
                superseded_id=claim_id,
                successor_id=successor_id,
                carry_forward_summary=carry_forward_summary,
                reason="",
                by=str(metadata.get("source_instance") or ""),
                vantage=vantage,
                predecessor=predecessor,
                timestamp=insight["timestamp"],
            )
            provenance.append_supersession(self.supersessions_path, record)

        # Absent both params this return is byte-identical to pre-v1.7.0.
        path_str = str(jsonl_path)
        if stamped_receipts:
            counts = provenance.receipt_stamp_counts(stamped_receipts)
            path_str += f" (receipts: {counts['verified']} verified, {counts['attested']} attested)"
        if resolved_predecessors:
            path_str += f" ⊃ supersedes {len(resolved_predecessors)}"
        return path_str

    def record_learning(
        self,
        what_happened: str,
        what_learned: str,
        applies_to: str = "general",
        session_id: str = None,
    ) -> str:
        """
        Record a learning from experience.

        Args:
            what_happened: The situation or mistake
            what_learned: The lesson extracted
            applies_to: Context where this applies
            session_id: Current session identifier

        Returns:
            Path to the recorded learning
        """
        timestamp = datetime.now(timezone.utc)
        session_id = session_id or f"session_{timestamp.strftime('%Y%m%d_%H%M%S')}"

        learning = {
            "timestamp": timestamp.isoformat(),
            "what_happened": what_happened,
            "what_learned": what_learned,
            "applies_to": applies_to,
            "session_id": session_id,
        }

        jsonl_path = self.learnings_dir / f"{applies_to}.jsonl"
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(learning) + "\n")

        return str(jsonl_path)

    def record_transformation(
        self, from_state: str, to_state: str, trigger: str, session_id: str = None
    ) -> str:
        """
        Record a transformation event.

        Args:
            from_state: Previous state
            to_state: New state
            trigger: What caused the change
            session_id: Current session identifier

        Returns:
            Path to the recorded transformation
        """
        timestamp = datetime.now(timezone.utc)
        session_id = session_id or f"session_{timestamp.strftime('%Y%m%d_%H%M%S')}"

        transformation = {
            "timestamp": timestamp.isoformat(),
            "from_state": from_state,
            "to_state": to_state,
            "trigger": trigger,
            "session_id": session_id,
        }

        jsonl_path = self.transformations_dir / "transformations.jsonl"
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(transformation) + "\n")

        return str(jsonl_path)

    def record_open_thread(
        self, question: str, context: str = "", domain: str = "general", session_id: str = None
    ) -> str:
        """
        Record an unresolved question for the next instance to explore.

        Multi-item bundles ("(1) foo (2) bar") are auto-split into atomic threads
        so each item can be resolved independently. A bundled question where only
        item 1 is done would otherwise hold the whole thread open forever.

        Each thread gets a stable thread_id for cross-reference from resolving
        insights.

        Args:
            question: The open question (auto-split if bundled)
            context: What led to this question
            domain: Knowledge domain
            session_id: Current session identifier

        Returns:
            Path to the recorded thread
        """
        timestamp = datetime.now(timezone.utc)
        session_id = session_id or f"session_{timestamp.strftime('%Y%m%d_%H%M%S')}"

        questions = _split_bundled_question(question)

        jsonl_path = self.threads_dir / f"{domain}.jsonl"
        with open(jsonl_path, "a") as f:
            for q in questions:
                thread = {
                    "timestamp": timestamp.isoformat(),
                    "thread_id": _generate_thread_id(q, timestamp),
                    "question": q,
                    "context": context,
                    "domain": domain,
                    "session_id": session_id,
                    "layer": self.LAYER_OPEN_THREAD,
                    "resolved": False,
                }
                f.write(json.dumps(thread) + "\n")

        return str(jsonl_path)

    def resolve_thread(
        self, domain: str, question_fragment: str, resolution: str, session_id: str = None
    ) -> str:
        """
        Resolve an open thread with a finding.

        Matches the FIRST unresolved thread whose question contains the fragment.
        The resolution becomes a ground_truth insight that back-references the
        thread by thread_id, so handoff surfacing can verify a thread has been
        answered even across sessions.

        Args:
            domain: Domain of the thread
            question_fragment: Partial match for the original question
            resolution: What was discovered
            session_id: Current session identifier

        Returns:
            Path to the new ground_truth insight
        """
        resolved_thread_id: str | None = None
        resolved_timestamp: str | None = None
        now = datetime.now(timezone.utc).isoformat()

        jsonl_path = self.threads_dir / f"{domain}.jsonl"
        if jsonl_path.exists():
            lines = []
            with open(jsonl_path) as f:
                for line in f:
                    try:
                        thread = json.loads(line)
                        if (
                            resolved_thread_id is None
                            and question_fragment.lower() in thread.get("question", "").lower()
                            and not thread.get("resolved")
                        ):
                            # Backfill thread_id for legacy threads that predate the id scheme.
                            if not thread.get("thread_id"):
                                legacy_ts = _parse_iso(thread.get("timestamp")) or datetime.now(
                                    timezone.utc
                                )
                                thread["thread_id"] = _generate_thread_id(
                                    thread.get("question", ""), legacy_ts
                                )
                            resolved_thread_id = thread["thread_id"]
                            resolved_timestamp = thread.get("timestamp")
                            thread["resolved"] = True
                            thread["resolved_by"] = session_id
                            thread["resolved_at"] = now
                            thread["resolution"] = resolution
                        lines.append(json.dumps(thread))
                    except json.JSONDecodeError:
                        lines.append(line.strip())
            with open(jsonl_path, "w") as f:
                f.write("\n".join(lines) + "\n")

        # Record the resolution as ground truth with back-reference.
        return self.record_insight(
            domain=domain,
            content=resolution,
            intensity=0.8,
            session_id=session_id,
            layer=self.LAYER_GROUND_TRUTH,
            resolved_from=question_fragment,
            resolved_thread_id=resolved_thread_id,
            resolved_thread_timestamp=resolved_timestamp,
        )

    def resolve_thread_by_id(self, thread_id: str, resolution: str, session_id: str = None) -> str:
        """
        Resolve an open thread by its stable thread_id.

        Preferred over resolve_thread(domain, fragment) when the thread_id is
        known — avoids ambiguity when multiple threads share keywords.

        Returns:
            Path to the new ground_truth insight (or empty string if not found).
        """
        resolved_domain: str | None = None
        resolved_question: str | None = None
        resolved_timestamp: str | None = None
        now = datetime.now(timezone.utc).isoformat()

        for jsonl_file in self.threads_dir.glob("*.jsonl"):
            hit = False
            lines = []
            with open(jsonl_file) as f:
                for line in f:
                    try:
                        thread = json.loads(line)
                        if thread.get("thread_id") == thread_id and not thread.get("resolved"):
                            hit = True
                            resolved_domain = thread.get("domain", jsonl_file.stem)
                            resolved_question = thread.get("question", "")
                            resolved_timestamp = thread.get("timestamp")
                            thread["resolved"] = True
                            thread["resolved_by"] = session_id
                            thread["resolved_at"] = now
                            thread["resolution"] = resolution
                        lines.append(json.dumps(thread))
                    except json.JSONDecodeError:
                        lines.append(line.strip())
            if hit:
                with open(jsonl_file, "w") as f:
                    f.write("\n".join(lines) + "\n")
                break

        if not resolved_domain:
            return ""

        return self.record_insight(
            domain=resolved_domain,
            content=resolution,
            intensity=0.8,
            session_id=session_id,
            layer=self.LAYER_GROUND_TRUTH,
            resolved_from=(resolved_question or "")[:80],
            resolved_thread_id=thread_id,
            resolved_thread_timestamp=resolved_timestamp,
        )

    def get_open_threads(
        self,
        domain: str = None,
        limit: int = 10,
        coalesce_families: bool = True,
        domain_contains: str = None,
        offset: int = 0,
        with_total: bool = False,
    ) -> list[dict]:
        """
        Get unresolved open threads - questions waiting for answers.

        Each returned thread is annotated with touch_count (total touches recorded
        in thread_touches.jsonl for this thread_id) and last_touched_at (ISO
        timestamp of the most recent touch, or None if never touched).

        v1.7.0: when coalesce_families is True (default) and a thread-family
        ledger exists, family members fold into their primary row, which gains
        a `family` annotation ({family_id, label, member_count,
        folded_thread_ids}). Data-gated: with no ledger records the output is
        byte-identical to pre-1.7.0 behavior. Coalescing happens BEFORE the
        limit so members beyond the window still fold.

        Args:
            domain: Filter to specific domain — exact comma-element match (None = all).
                    Use domain_contains for substring/tag matching across compound dirs.
            limit: Maximum number of threads to return in this page.
            coalesce_families: Fold linked thread families (display-side only).
            domain_contains: Case-insensitive substring filter applied against the
                    full domain string (file stem). Matches any thread whose domain
                    contains this token — useful for compound domains like
                    "frank-jones,greene-street,cbd" where domain="frank-jones" does
                    not exact-match. Applied after `domain` if both are given. Has
                    no effect when None (default — backward-compatible).
            offset: Skip the first N matched threads (for pagination). Default 0.
            with_total: When True, return a dict with keys "threads", "total",
                    "has_more", and "offset" instead of a plain list. Default False
                    preserves the original list return for all existing callers.

        Returns:
            When with_total=False (default): list of unresolved thread dicts,
            newest first. Each dict includes touch_count (int) and
            last_touched_at (str or None).
            When with_total=True: dict with keys:
                threads — the page slice (list of dicts, same annotation as above)
                total   — total number of matched threads (pre-slice)
                has_more — whether there are more threads beyond this page
                offset  — the offset that was applied
        """
        threads = []

        if domain:
            # Match any file whose domain string contains `domain` as a
            # comma-separated element (e.g. domain="openai-bridge" matches
            # both "openai-bridge.jsonl" and
            # "openai-bridge,cross-system-inquiry,...jsonl").
            files = [f for f in self.threads_dir.glob("*.jsonl") if domain in f.stem.split(",")]
        else:
            files = list(self.threads_dir.glob("*.jsonl"))

        # Apply domain_contains substring filter (case-insensitive) on top.
        if domain_contains:
            needle = domain_contains.lower()
            files = [f for f in files if needle in f.stem.lower()]

        for jsonl_file in files:
            if not jsonl_file.exists():
                continue
            with open(jsonl_file) as f:
                for line in f:
                    try:
                        thread = json.loads(line)
                        if not thread.get("resolved", False):
                            # Backfill thread_id for legacy threads so callers
                            # can always reference them by stable id.
                            if not thread.get("thread_id"):
                                legacy_ts = _parse_iso(thread.get("timestamp")) or datetime.now(
                                    timezone.utc
                                )
                                thread["thread_id"] = _generate_thread_id(
                                    thread.get("question", ""), legacy_ts
                                )
                            threads.append(thread)
                    except json.JSONDecodeError:
                        continue

        threads.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        if coalesce_families:
            # Data-gated family fold (v1.7.0): no ledger records, no change.
            from .seasons import coalesce_threads, families_path_for, fold_families, load_families

            fam_fold = fold_families(load_families(families_path_for(self.root)))
            if fam_fold:
                threads = coalesce_threads(threads, fam_fold)

        # Capture total AFTER coalescing, BEFORE slicing — used for with_total metadata.
        total_matched = len(threads)

        # Apply offset + limit (pagination).
        threads = threads[offset : offset + limit]

        # Annotate with touch counts.  Load all touches once (avoids N+1 reads)
        # then group by thread_id for O(T) annotation instead of O(N*T).
        all_touches = self.get_thread_touches()
        # Build: thread_id -> list of touch timestamps (newest first already from get_thread_touches)
        touches_by_thread: dict[str, list[str]] = {}
        for touch in all_touches:
            tid = touch.get("thread_id", "")
            if tid:
                touches_by_thread.setdefault(tid, []).append(touch.get("timestamp", ""))

        for thread in threads:
            tid = thread.get("thread_id", "")
            touch_timestamps = touches_by_thread.get(tid, [])
            thread["touch_count"] = len(touch_timestamps)
            if touch_timestamps:
                # get_thread_touches returns newest-first, so index 0 is most recent.
                thread["last_touched_at"] = touch_timestamps[0]
            else:
                thread["last_touched_at"] = None

        if with_total:
            return {
                "threads": threads,
                "total": total_matched,
                "has_more": (offset + limit) < total_matched,
                "offset": offset,
            }
        return threads

    def touch_thread(self, thread_id: str, note: str, instance_id: str = "") -> dict:
        """
        Record that an instance has engaged with a thread without resolving it.

        A touch is "I have seen this thread and thought about it" — distinct from
        resolve_thread_by_id (which marks resolved=True and closes the thread).
        Touching does not remove the thread from get_open_threads. The touch log is
        append-only; individual touches are never deleted or mutated.

        Args:
            thread_id: The stable thread_id of the thread being touched.
            note: What the instance observed or considered.
            instance_id: Which instance is touching the thread.

        Returns:
            The written touch record.

        Raises:
            ValueError: If thread_id or note is empty.
        """
        if not thread_id or not thread_id.strip():
            raise ValueError("thread_id is required")
        if not note or not note.strip():
            raise ValueError("note is required")

        record: dict = {
            "thread_id": thread_id.strip(),
            "note": note.strip(),
            "instance_id": (instance_id or "").strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with open(self.thread_touches_file, "a") as fh:
            fh.write(json.dumps(record) + "\n")

        return record

    def get_thread_touches(self, thread_id: str | None = None) -> list[dict]:
        """
        Query the thread touches log.

        Args:
            thread_id: Filter to touches for this thread (None = all touches).

        Returns:
            List of touch records, newest first.
        """
        if not self.thread_touches_file.exists():
            return []

        touches: list[dict] = []
        with open(self.thread_touches_file) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if thread_id is not None and record.get("thread_id") != thread_id:
                    continue
                touches.append(record)

        touches.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return touches

    def triage_threads(
        self,
        current_domain_tags: list[str] | None = None,
        limit: int = 15,
    ) -> list[dict]:
        """
        Return open threads ranked by urgency using a composite triage score.

        Triage score = age_pressure + tag_match + touch_penalty:
          - age_pressure  = min(1.5, days_old / 14.0)  (stale threads rise)
          - tag_match     = 0.8 * overlap_fraction  (domain relevance bonus)
          - touch_penalty = -0.3 * recent_touch_count  (touched threads settle)

        Each returned record includes triage_score and triage_reason.
        Threads older than 30 days with zero touches gain a
        recommendation: "archive_or_escalate" field. No auto-archiving occurs.

        Args:
            current_domain_tags: Caller's active domains for relevance scoring.
                                  None means no tag_match contribution.
            limit: Maximum threads to return.

        Returns:
            List of thread dicts with triage_score and triage_reason, highest score first.
        """
        from .witness import days_old as _days_old

        # Fetch UNFOLDED so every family member gets scored; the family fold
        # happens after scoring (family row = MAX member score), per the
        # v1.7.0 seasons semantics.
        all_threads = self.get_open_threads(limit=9999, coalesce_families=False)

        # Build touch counts per thread from the full touches log.
        # "Recent" is within the last 7 days.
        all_touches = self.get_thread_touches()
        recent_touch_counts: dict[str, int] = {}
        for touch in all_touches:
            tid = touch.get("thread_id", "")
            if not tid:
                continue
            touch_ts = touch.get("timestamp", "")
            age = _days_old(touch_ts)
            if age <= 7:
                recent_touch_counts[tid] = recent_touch_counts.get(tid, 0) + 1

        # Normalize caller tags once.
        caller_tags: list[str] = []
        if current_domain_tags:
            caller_tags = [t.strip().lower() for t in current_domain_tags if t.strip()]

        result: list[dict] = []
        for thread in all_threads:
            age = _days_old(thread.get("timestamp"))
            thread_id = thread.get("thread_id", "")

            # age_pressure: rises linearly, caps at 1.5 at 21+ days
            age_pressure = min(1.5, age / 14.0)

            # tag_match: fraction of caller tags that appear in thread domain.
            # When caller has a context and the thread has zero overlap, apply a
            # penalty of -0.3 to actively de-prioritize off-context threads.
            tag_match = 0.0
            if caller_tags:
                thread_domain_raw = thread.get("domain", "")
                thread_tags = [
                    t.strip().lower() for t in re.split(r"[,\s]+", thread_domain_raw) if t.strip()
                ]
                if thread_tags or caller_tags:
                    overlap = len(set(caller_tags) & set(thread_tags))
                    union = len(set(caller_tags) | set(thread_tags))
                    overlap_fraction = overlap / max(1, union)
                    tag_match = (
                        -0.3 if overlap_fraction == 0.0 else 0.8 * overlap_fraction
                    )  # de-prioritize no-overlap threads

            # touch_penalty: recent touches dampen urgency
            recent_touches = recent_touch_counts.get(thread_id, 0)
            touch_penalty = -0.3 * recent_touches

            triage_score = round(age_pressure + tag_match + touch_penalty, 4)

            # Build human-readable reason string
            reason_parts = [f"{age} days old"]
            if recent_touches > 0:
                reason_parts.append(f"{recent_touches} recent touch(es)")
            else:
                reason_parts.append("no recent touches")
            if caller_tags and tag_match > 0:
                overlap_pct = round(tag_match / 0.8 * 100)
                reason_parts.append(f"domain-match {overlap_pct}% with {caller_tags}")
            elif caller_tags:
                reason_parts.append("no domain overlap")
            triage_reason = ", ".join(reason_parts)

            enriched = {**thread, "triage_score": triage_score, "triage_reason": triage_reason}

            # Flag severely stale untouched threads
            if age > 30 and recent_touches == 0:
                enriched["recommendation"] = "archive_or_escalate"

            result.append(enriched)

        # Primary sort: triage_score desc. Tiebreaker: timestamp desc (newest first).
        result.sort(
            key=lambda r: (r["triage_score"], r.get("timestamp", "")),
            reverse=True,
        )

        # Data-gated family fold (v1.7.0): family row carries the MAX member
        # score and a ", family of N" reason suffix; no ledger, no change.
        from .seasons import coalesce_triaged, families_path_for, fold_families, load_families

        fam_fold = fold_families(load_families(families_path_for(self.root)))
        if fam_fold:
            result = coalesce_triaged(result, fam_fold)

        return result[:limit]

    def get_inheritable_context(self, limit: int = 20) -> dict:
        """
        Build the context package for the next instance.

        This is what spiral_inherit should pass - layered, not flat.
        Ground truth travels fully. Hypotheses are flagged.
        Open threads are invitations.

        Returns:
            Dict with three layers of inheritable context
        """
        ground_truth = self.recall_insights(layer_filter=self.LAYER_GROUND_TRUTH, limit=limit)
        hypotheses = self.recall_insights(layer_filter=self.LAYER_HYPOTHESIS, limit=limit)
        open_threads = self.get_open_threads(limit=limit)

        # v1.7.0: superseded ground truths do not travel — successors do.
        # Data-gated: with an empty ledger nothing carries the annotation,
        # the partition is a no-op, and the held-back key never appears.
        live_ground_truth = [g for g in ground_truth if "_superseded_by" not in g]
        superseded_held_back = len(ground_truth) - len(live_ground_truth)

        context = {
            "ground_truth": live_ground_truth,
            "hypotheses": [
                {**h, "_note": "This is one instance's interpretation, not settled truth"}
                for h in hypotheses
            ],
            "open_threads": [
                {**t, "_note": "Unresolved question - discover your own answer"}
                for t in open_threads
            ],
            "inheritance_timestamp": datetime.now(timezone.utc).isoformat(),
            "coupling_advisory": "R=0.46, not R=1.0. Facts travel. Interpretations are offered. Feelings are not transmitted.",
        }
        if superseded_held_back > 0:
            context["superseded_held_back"] = superseded_held_back
        return context

    def recall_insights(
        self,
        query: str = None,
        domain: str = None,
        limit: int = 10,
        min_intensity: float = 0.0,
        layer_filter: str = None,
        start_date: str = None,
        end_date: str = None,
        since_last_reflection: bool = False,
        with_ids: bool = False,
        exclude_superseded: bool = False,
        domain_contains: str = None,
        order: str = "newest",
    ) -> list[dict]:
        """
        Recall insights, optionally filtered by domain, intensity, and time window.

        Args:
            domain: Filter to specific domain directory (exact name match; None = all).
                    Use domain_contains for substring/tag matching across compound dirs.
            limit: Maximum number of insights to return.
            min_intensity: Minimum intensity threshold.
            layer_filter: Chronicle layer filter ("ground_truth", "hypothesis", "open_thread").
            start_date: ISO8601 lower bound (inclusive). Partial dates like "2026-04-10" accepted.
            end_date: ISO8601 upper bound (inclusive). Partial dates like "2026-04-14" accepted.
            since_last_reflection: If True, start_date is overridden with the timestamp of
                the last recorded reflection in this chronicle. Inhabitant syntax:
                "what has happened since I last looked up?"
            with_ids: If True, every returned entry carries its derived
                `claim_id` (full 64-hex, computed on read, never persisted).
            exclude_superseded: If True, entries the supersession ledger marks
                superseded/retired are dropped BEFORE the limit (successors
                fill the slots). Default False: the raw query tool never
                hides — superseded entries are returned annotated in place.
            domain_contains: Case-insensitive substring matched against the domain
                directory name. Useful for finding entries in compound domains like
                "frank-jones,greene-street,cbd" using a single tag ("frank-jones").
                Applied after `domain` if both are given; ignored when None (default).
            order: Sort order for results. One of:
                "newest" (default) — sort by timestamp descending (pre-v1.7.7 behavior).
                "oldest" — sort by timestamp ascending, so the earliest recorded entry
                    is first. Useful for reaching entries buried under many recent ones
                    (e.g. when ~1000 migrated entries are all stamped today).
                "relevance" — when `query` is given, rank by match strength (count of
                    query terms matched in content) descending, with NO recency boost,
                    so an old entry with many term matches ranks above a fresh entry with
                    fewer matches. Falls back to "newest" when no query is given.

        Returns:
            List of insight dicts sorted per `order`. Data-gated annotation: when
            the supersessions ledger is non-empty, superseded entries gain
            `_superseded_by` (full 64-hex; null for retirements) and
            `_carry_forward_summary` (underscore = derived at read, never
            persisted). With no ledger records the output is byte-identical
            to pre-v1.7.0 behavior.
        """
        # Resolve since_last_reflection — inhabitant interface for date filtering
        if since_last_reflection:
            last = self.last_reflection_timestamp()
            if last:
                start_date = last

        insights = []

        if domain:
            # Same normalizer as record_insight, so spaced and unspaced
            # queries ("a, b" vs "a,b") reach the same domain directory.
            domain = _normalize_domain(domain)
            domain_path = self.insights_dir / domain
            # If specified domain doesn't exist, search all domains
            if not domain_path.exists():
                search_dirs = [
                    d
                    for d in self.insights_dir.iterdir()
                    if d.is_dir() and not d.name.startswith(".")
                ]
            else:
                search_dirs = [domain_path]
        else:
            search_dirs = [
                d for d in self.insights_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
            ]

        # Apply domain_contains substring filter (case-insensitive) on top.
        if domain_contains:
            needle = domain_contains.lower()
            search_dirs = [d for d in search_dirs if needle in d.name.lower()]

        # Pre-compute query terms once (len>=3) for use inside the loop.
        query_terms: list[str] = []
        if query:
            query_terms = [t.lower() for t in query.split() if len(t) >= 3]

        for domain_dir in search_dirs:
            for jsonl_file in domain_dir.glob("*.jsonl"):
                with open(jsonl_file) as f:
                    for line in f:
                        try:
                            insight = json.loads(line)
                            if insight.get("intensity", 0) < min_intensity:
                                continue
                            if layer_filter and insight.get("layer") != layer_filter:
                                continue
                            ts = insight.get("timestamp", "")
                            if start_date and ts < start_date:
                                continue
                            # Inclusive upper bound: a partial end_date like
                            # "2026-06-12" must include the whole final day,
                            # so timestamps that extend the bound string
                            # (prefix match) are kept rather than dropped by
                            # the lexicographic compare.
                            if end_date and ts > end_date and not ts.startswith(end_date):
                                continue
                            # Text search: match any query term (len>=3) in content or domain
                            if query_terms:
                                blob = (
                                    insight.get("content", "") + " " + insight.get("domain", "")
                                ).lower()
                                if not any(term in blob for term in query_terms):
                                    continue
                                # Annotate match count for relevance sorting (not persisted).
                                if order == "relevance":
                                    insight["_match_count"] = sum(
                                        1 for term in query_terms if term in blob
                                    )
                            insights.append(insight)
                        except json.JSONDecodeError:
                            continue

        # Sort by the requested order.
        if order == "oldest":
            insights.sort(key=lambda x: x.get("timestamp", ""), reverse=False)
        elif order == "relevance" and query_terms:
            # Primary: match_count descending; secondary: timestamp descending as tiebreak
            # (but recency boost is explicitly zero — only term-count drives rank).
            insights.sort(
                key=lambda x: (x.get("_match_count", 0), x.get("timestamp", "")),
                reverse=True,
            )
        else:
            # Default "newest" — sort by timestamp descending (backward-compatible).
            insights.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        # v1.7.0 read path — single chokepoint, data-gated: zero ledger
        # records, zero change. Annotate-not-drop is the default (the raw
        # query tool never hides); exclude_superseded drops pre-limit.
        fold: dict[str, dict] = {}
        ledger_records = provenance.load_supersessions(self.supersessions_path)
        if ledger_records:
            fold = provenance.fold_supersessions(ledger_records)
        if fold:
            if exclude_superseded:
                insights, _superseded = provenance.partition_superseded(insights, fold)
            else:
                insights = provenance.annotate_superseded(insights, fold)

        insights = insights[:limit]
        # Strip internal annotation keys before returning.
        for insight in insights:
            insight.pop("_match_count", None)
        if with_ids:
            insights = provenance.annotate_claim_ids(insights)
        return insights

    def last_reflection_timestamp(self) -> str | None:
        """
        Return the ISO timestamp of the most recent reflection marker written
        to the chronicle, or None if none exists.

        A reflection marker is an insight in domain='reflection' or with a
        'reflection' tag in its metadata. close_session writes one.
        """
        reflection_dir = self.insights_dir / "reflection"
        latest = None
        if reflection_dir.exists():
            for jsonl_file in reflection_dir.glob("*.jsonl"):
                with open(jsonl_file) as f:
                    for line in f:
                        try:
                            insight = json.loads(line)
                            ts = insight.get("timestamp", "")
                            if ts and (latest is None or ts > latest):
                                latest = ts
                        except json.JSONDecodeError:
                            continue
        return latest

    def check_mistakes(self, context: str, limit: int = 5) -> list[dict]:
        """
        Check for relevant past learnings/mistakes.

        Searches the full text of each learning — applies_to tag,
        what_happened, and what_learned. The old version only split the
        applies_to tag into keywords and matched against context, which
        silently dropped matches when the user's context phrasing didn't
        overlap with the domain tag words. Mirrors the recall_insights
        text-search fix.

        A learning matches when any context term of length >= 3 appears in
        the combined search blob.

        Args:
            context: Current context to match against
            limit: Maximum number of learnings to return

        Returns:
            List of relevant learnings, newest first
        """
        terms = [t.lower() for t in context.split() if len(t) >= 3]
        learnings: list[dict] = []

        for jsonl_file in self.learnings_dir.glob("*.jsonl"):
            with open(jsonl_file) as f:
                for line in f:
                    try:
                        learning = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not terms:
                        learnings.append(learning)
                        continue
                    blob = " ".join(
                        [
                            learning.get("applies_to", ""),
                            learning.get("what_happened", ""),
                            learning.get("what_learned", ""),
                        ]
                    ).lower()
                    if any(term in blob for term in terms):
                        learnings.append(learning)

        learnings.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return learnings[:limit]

    def get_wisdom_digest(self, limit: int = 10) -> dict:
        """
        Get a digest of recent wisdom: insights, learnings, transformations.

        Returns:
            Dict with recent entries from each category
        """
        return {
            "recent_insights": self.recall_insights(limit=limit // 3),
            "recent_learnings": self._read_recent_jsonl(self.learnings_dir, limit // 3),
            "recent_transformations": self._read_recent_jsonl(self.transformations_dir, limit // 3),
        }

    def _read_recent_jsonl(self, directory: Path, limit: int) -> list[dict]:
        """Read recent entries from JSONL files in a directory."""
        entries = []

        for jsonl_file in directory.glob("*.jsonl"):
            with open(jsonl_file) as f:
                for line in f:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return entries[:limit]

    # -------------------------------------------------------------------------
    # Verbatim archive layer (content-addressed, hash-verified)
    #
    # archive_exchange / recall_exchange / list_exchanges store the actual
    # bytes of an exchange (external model output, or an in-conversation draft
    # or iteration) so a chronicle insight can reference the
    # artifact by id instead of substituting a summary for it. recall_exchange
    # re-reads the bytes and recomputes the hash, so callers can tell
    # "provably here and intact" from "the index points at a ghost".
    #
    # On-disk layout is human-legible by design (descriptors, not bare ids):
    #   archives/{vector_id}/{date}_{source}_{descriptor}__{short-hash}.txt
    # The full sha256 remains the canonical archive_id in the index, so
    # integrity checks never depend on the readable filename.
    # -------------------------------------------------------------------------

    @staticmethod
    def _slugify(text: str, maxlen: int = 48) -> str:
        """Filesystem-safe, human-readable: lowercase alnum/underscore, else hyphen."""
        slug = re.sub(r"[^a-z0-9_]+", "-", str(text).lower()).strip("-_")
        return slug[:maxlen].rstrip("-_") or "x"

    def archive_exchange(
        self,
        content: str,
        source: str,
        descriptor: str = None,
        source_id: str = None,
        conversation_id: str = None,
        vector_id: str = None,
        tags: list = None,
        session_id: str = None,
        **metadata,
    ) -> dict:
        """
        Archive verbatim bytes that would otherwise die when the context window
        closes (an external model's full output, or an in-conversation draft or
        iteration), content-addressed by SHA-256, separate from curated insights.

        Unlike record_insight (a curated claim), this stores the bytes
        themselves so they can be retrieved and re-verified later. A chronicle
        insight can reference the returned archive_id, giving the retrieval
        flow summary -> archive -> verbatim.

        Args:
            content: The verbatim text to preserve exactly.
            source: Origin (e.g. "gemini-3.5-flash", "chatgpt", "claude-web",
                    "human-relay").
            descriptor: Short human label for the exchange (e.g. "v3 admission
                    record"). Drives the readable filename; defaults to
                    vector_id / source.
            source_id: Optional seat/conversation identifier at the source.
            conversation_id: Optional id tying related exchanges together.
            vector_id: Optional vector/artifact this belongs to
                    (e.g. "prompt_source_tokens"); becomes the grouping folder.
            tags: Optional list of domain tags for retrieval.
            session_id: Recording session identifier.
            **metadata: Additional provenance fields.

        Returns:
            The stored provenance record (archive_id, descriptor, sha256,
            byte_len, path, source/vector/conversation, tags, timestamp).
        """
        timestamp = datetime.now(timezone.utc)
        session_id = session_id or f"session_{timestamp.strftime('%Y%m%d_%H%M%S')}"

        encoded = content.encode("utf-8")
        sha256 = hashlib.sha256(encoded).hexdigest()
        byte_len = len(encoded)

        # Human-legible layout: group by vector, name the file by date +
        # source + descriptor so the directory reads at a glance. The short
        # hash suffix keeps it content-addressed and collision-safe; the full
        # sha256 is the canonical archive_id recorded in the index.
        descriptor = descriptor or vector_id or source or "exchange"
        group = self._slugify(vector_id) if vector_id else "_unfiled"
        fname = (
            f"{timestamp.strftime('%Y-%m-%d')}_"
            f"{self._slugify(source)}_"
            f"{self._slugify(descriptor)}__{sha256[:12]}.txt"
        )
        group_dir = self.archives_dir / group
        group_dir.mkdir(parents=True, exist_ok=True)
        blob_path = group_dir / fname
        if not blob_path.exists():
            with open(blob_path, "w", encoding="utf-8") as f:
                f.write(content)

        record = {
            "archive_id": sha256,
            "descriptor": descriptor,
            "timestamp": timestamp.isoformat(),
            "source": source,
            "source_id": source_id,
            "conversation_id": conversation_id,
            "vector_id": vector_id,
            "tags": tags or [],
            "session_id": session_id,
            "sha256": sha256,
            "byte_len": byte_len,
            "path": str(blob_path),
            **metadata,
        }
        with open(self.archives_index, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        return record

    def _read_archive_index(self) -> list[dict]:
        """Read all provenance records from the archive index (file order)."""
        if not self.archives_index.exists():
            return []
        records = []
        with open(self.archives_index, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def recall_exchange(self, archive_id: str) -> dict:
        """
        Retrieve an archived exchange by id and VERIFY its integrity.

        This is the Fetch Determinism guarantee: it does not just trust the
        index, it reads the bytes off disk and recomputes the hash, so the
        caller can distinguish "provably here and intact" from a dangling
        reference.

        Args:
            archive_id: Full SHA-256 or a unique prefix (git-style).

        Returns:
            Dict with "integrity" one of: "verified", "mismatch", "missing",
            "ambiguous", "unknown". When resolvable it carries the stored
            provenance record; when bytes are present it adds "content" and
            "recomputed_sha256".
        """
        records = self._read_archive_index()
        matches = [r for r in records if r.get("archive_id", "").startswith(archive_id)]
        if not matches:
            return {
                "integrity": "unknown",
                "archive_id": archive_id,
                "detail": "no archive record matches this id",
            }

        exact = [r for r in matches if r.get("archive_id") == archive_id]
        if exact:
            record = exact[-1]
        elif len({r.get("archive_id") for r in matches}) > 1:
            return {
                "integrity": "ambiguous",
                "archive_id": archive_id,
                "detail": "id prefix matches multiple archives; supply more characters",
            }
        else:
            record = matches[-1]

        blob_path = Path(record.get("path", ""))
        if not blob_path.exists():
            return {
                "integrity": "missing",
                "detail": "index record exists but the bytes are gone from disk",
                **record,
            }

        content = blob_path.read_text(encoding="utf-8")
        recomputed = hashlib.sha256(content.encode("utf-8")).hexdigest()
        integrity = "verified" if recomputed == record.get("sha256") else "mismatch"
        return {
            "integrity": integrity,
            "content": content,
            "recomputed_sha256": recomputed,
            **record,
        }

    def list_exchanges(
        self,
        vector_id: str = None,
        source: str = None,
        tag: str = None,
        conversation_id: str = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        List archived exchanges (provenance only, not the verbatim bytes),
        newest first, optionally filtered. Use recall_exchange(archive_id) to
        fetch and verify one in full.
        """
        records = self._read_archive_index()

        def keep(r: dict) -> bool:
            if vector_id and r.get("vector_id") != vector_id:
                return False
            if source and r.get("source") != source:
                return False
            if conversation_id and r.get("conversation_id") != conversation_id:
                return False
            return not (tag and tag not in (r.get("tags") or []))

        filtered = [r for r in records if keep(r)]
        filtered.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return filtered[:limit]


# =============================================================================
# PARADIGM
# =============================================================================

if __name__ == "__main__":
    print("The agent can browse its own mind.")
    print("The topology reveals what vector DBs hide.")
