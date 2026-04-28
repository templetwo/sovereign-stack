"""
Reflexive Surface Module — Context-aware relevance surfacing.

Instead of requiring instances to remember to query for relevant threads,
handoffs, and mistakes, ReflexiveSurface surfaces only what matches the
current context. The instance declares what it is working on; the stack
surfaces what is relevant.

Scoring formula per candidate item:
    score = tag_overlap * 2.0 + recency_boost + project_match_bonus

    tag_overlap        = |caller_tags ∩ item_tags| / max(1, |caller_tags ∪ item_tags|)
    recency_boost      = max(0, 1.0 - days_old / 30.0)  (decays to 0 at 30 days)
    project_match_bonus = +0.5 if project string appears in item's context or question

Tie-break: timestamp desc (more recent wins).

This module also exposes PerTurnPriors — a thin wrapper that assembles a
compact priors block for the instance to read BEFORE forming a response.
PerTurnPriors enforces k=1 retrieval per bucket (ReasoningBank ICLR 2026:
k=4 hurts vs k=1), a hard token cap on the returned block, and a freshness
penalty that demotes items surfaced in the last N priors calls — the
sycophancy guardrail from Jain et al. MIT/IDSS 2026 (+45% agreement under
memory profiles).
"""

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .handoff import HandoffEngine
from .memory import ExperientialMemory, _parse_iso
from .witness import days_old as _days_old


def _normalize_tags(raw: str) -> list[str]:
    """Split a raw domain string on commas/whitespace and lowercase each piece."""
    return [t.strip().lower() for t in re.split(r"[,\s]+", raw or "") if t.strip()]


def _compute_tag_overlap(caller_tags: list[str], item_domain: str) -> float:
    """
    Jaccard-style tag overlap between caller tags and item domain tags.

    Returns:
        float in [0.0, 1.0]: |intersection| / max(1, |union|)
    """
    if not caller_tags:
        return 0.0
    item_tags = _normalize_tags(item_domain)
    if not item_tags:
        return 0.0
    caller_set = set(caller_tags)
    item_set = set(item_tags)
    overlap = len(caller_set & item_set)
    union = len(caller_set | item_set)
    return overlap / max(1, union)


# When the caller provides domain_tags, items with zero tag overlap are
# off-topic by construction. We mirror triage_threads' -0.3 no-overlap
# penalty here so that an off-topic item with a body-text project_match
# (a +0.5 bonus) does not surface above genuinely relevant items.
#
# Identified 2026-04-25 first-hand stack probe: stale Feb 2026 mistakes
# with `applies_to: "sovereign_stack"` came back at score 0.5 against
# tags like ["reflection-daemons", "v1.3.2"] because their what_learned
# bodies mentioned "sovereign-stack" — project_match alone was lifting
# them. project_match is now a tie-breaker among items that ALSO match
# tags, not a primary relevance signal on its own.
NO_OVERLAP_PENALTY = -0.3


def _score_item(
    item: dict,
    caller_tags: list[str],
    project: str | None,
    domain_field: str = "domain",
    context_fields: list[str] | None = None,
) -> tuple:
    """
    Compute the relevance score for a single candidate item.

    Args:
        item: The candidate dict (thread, handoff note, insight, or learning).
        caller_tags: Normalized domain tags from the surface() caller.
        project: Optional project name for bonus matching.
        domain_field: Key in item that holds the domain/tag string.
        context_fields: List of item keys to search for project match.

    Returns:
        Tuple of (score, tag_overlap). tag_overlap is exposed so the
        caller can apply tag-aware filtering above the score check —
        e.g., dropping items where caller_tags is non-empty but
        tag_overlap is zero, regardless of how high the score climbs
        from recency + project_match alone.
    """
    context_fields = context_fields or [
        "context",
        "question",
        "note",
        "content",
        "what_happened",
        "what_learned",
    ]

    tag_overlap = _compute_tag_overlap(caller_tags, item.get(domain_field, ""))

    age = _days_old(item.get("timestamp"))
    recency_boost = max(0.0, 1.0 - age / 30.0)

    project_match_bonus = 0.0
    if project:
        project_lower = project.strip().lower()
        for field in context_fields:
            val = str(item.get(field, "")).lower()
            if project_lower in val:
                project_match_bonus = 0.5
                break

    score = tag_overlap * 2.0 + recency_boost + project_match_bonus

    # No-overlap penalty: only when caller actually provided tags.
    # If caller_tags is empty, every item has tag_overlap == 0 by
    # definition; penalizing then would punish the "show me anything
    # recent" use case. The penalty only makes sense when the caller
    # has expressed a topical interest.
    if caller_tags and tag_overlap == 0.0:
        score += NO_OVERLAP_PENALTY

    return score, tag_overlap


class ReflexiveSurface:
    """
    Context-aware relevance engine for the sovereign chronicle.

    Given a set of domain tags (and optionally a project name and recent tool
    list), surfaces the most relevant open threads, unconsumed handoffs, recent
    mistakes, and related insights — scored and ranked so the instance does not
    need to know which buckets to query.

    Args:
        sovereign_root: Path to the sovereign data root (e.g. ~/.sovereign/).
                        The chronicle lives at sovereign_root/chronicle/ and
                        handoffs at sovereign_root/handoffs/.
    """

    def __init__(self, sovereign_root: Path):
        self.sovereign_root = Path(sovereign_root)
        chronicle_root = self.sovereign_root / "chronicle"
        self._memory = ExperientialMemory(root=str(chronicle_root))
        self._handoffs = HandoffEngine(root=str(self.sovereign_root))

    def surface(
        self,
        domain_tags: list[str],
        project: str | None = None,
        recent_tools: list[str] | None = None,
        limit_per_bucket: int = 5,
    ) -> dict:
        """
        Surface the most relevant items from all buckets for the given context.

        Args:
            domain_tags: Active domain tags for the current work context
                          (e.g. ["compass", "entropy", "witness"]).
            project: Optional project name string. Items whose text contains
                     this string receive a +0.5 score bonus.
            recent_tools: Not currently used in scoring; reserved for future
                          tool-affinity weighting. Accepted for API stability.
            limit_per_bucket: Maximum items returned per bucket. Default 5.

        Returns:
            Dict with keys:
              matched_open_threads, relevant_handoffs, recent_mistakes,
              related_insights, total_candidates_scanned, scoring_explanation.
        """
        limit_per_bucket = max(1, int(limit_per_bucket))
        caller_tags = [t.strip().lower() for t in (domain_tags or []) if t.strip()]

        # ── Bucket 1: open threads ──
        raw_threads = self._memory.get_open_threads(limit=9999)
        scored_threads = self._score_and_sort(
            raw_threads,
            caller_tags,
            project,
            domain_field="domain",
            context_fields=["question", "context"],
        )

        # ── Bucket 2: unconsumed handoffs ──
        raw_handoffs = self._handoffs.unconsumed(limit=500)
        scored_handoffs = self._score_and_sort(
            raw_handoffs,
            caller_tags,
            project,
            domain_field="thread",
            context_fields=["note"],
        )

        # ── Bucket 3: mistakes / learnings ──
        raw_mistakes = self._memory.check_mistakes(
            context=" ".join(caller_tags + ([project] if project else [])),
            limit=200,
        )
        scored_mistakes = self._score_and_sort(
            raw_mistakes,
            caller_tags,
            project,
            domain_field="applies_to",
            context_fields=["what_happened", "what_learned"],
        )

        # ── Bucket 4: related insights ──
        raw_insights = self._memory.recall_insights(
            query=" ".join(caller_tags) if caller_tags else None,
            limit=200,
        )
        scored_insights = self._score_and_sort(
            raw_insights,
            caller_tags,
            project,
            domain_field="domain",
            context_fields=["content"],
        )

        total_candidates = (
            len(raw_threads) + len(raw_handoffs) + len(raw_mistakes) + len(raw_insights)
        )

        scoring_explanation = (
            f"Scored {len(raw_threads)} open_threads, {len(raw_handoffs)} handoffs, "
            f"{len(raw_mistakes)} mistakes, {len(raw_insights)} insights; "
            f"returned top {limit_per_bucket} per bucket ranked by "
            f"tag_overlap*2 + recency_boost(1-days/30) + project_match(+0.5)."
        )

        return {
            "matched_open_threads": self._enrich_threads(scored_threads[:limit_per_bucket]),
            "relevant_handoffs": scored_handoffs[:limit_per_bucket],
            "recent_mistakes": scored_mistakes[:limit_per_bucket],
            "related_insights": scored_insights[:limit_per_bucket],
            "total_candidates_scanned": total_candidates,
            "scoring_explanation": scoring_explanation,
        }

    # ── Private helpers ──

    def _score_and_sort(
        self,
        items: list[dict],
        caller_tags: list[str],
        project: str | None,
        domain_field: str,
        context_fields: list[str],
    ) -> list[dict]:
        """
        Attach _score and _tag_overlap fields to each item and sort by
        score desc, timestamp desc.

        When caller_tags is non-empty, items with zero tag_overlap are
        dropped entirely — project_match alone is not enough to surface
        an off-topic item when the caller has expressed a topical
        interest. This matches the strict "give me what's relevant"
        semantics of reflexive_surface (distinct from triage_threads,
        which ranks-but-keeps everything).

        Returns:
            New list of dicts with _score + _tag_overlap attached,
            sorted by score desc / timestamp desc.
        """
        result = []
        for item in items:
            score, tag_overlap = _score_item(
                item,
                caller_tags,
                project,
                domain_field=domain_field,
                context_fields=context_fields,
            )
            # Strict tag-required filter when caller provided tags.
            # An item whose tags don't overlap at all isn't matched —
            # keeping it would dilute results with off-topic noise.
            if caller_tags and tag_overlap == 0.0:
                continue
            result.append(
                {
                    **item,
                    "_score": round(score, 4),
                    "_tag_overlap": round(tag_overlap, 4),
                }
            )

        result.sort(
            key=lambda r: (r["_score"], r.get("timestamp", "")),
            reverse=True,
        )
        return result

    def _enrich_threads(self, threads: list[dict]) -> list[dict]:
        """
        Add days_old and score to thread records for convenience.

        Returns:
            Threads with days_old field added.
        """
        enriched = []
        for t in threads:
            enriched.append(
                {
                    **t,
                    "days_old": _days_old(t.get("timestamp")),
                    "score": t.get("_score", 0.0),
                }
            )
        return enriched


# =============================================================================
# Per-Turn Priors — the reflex that closes the "post-attentive -> pre-attentive"
# gap. Called at turn start, not session start. ReasoningBank ICLR 2026 k=1
# default; sycophancy guardrail via freshness penalty; hard token cap.
# =============================================================================


def _estimate_tokens(text: str) -> int:
    """
    Cheap token estimate: ~4 chars per token is the standard heuristic for
    English prose in Claude tokenizers. We do not load tiktoken here because
    this module must stay dependency-light; this estimate is used only for a
    soft budget check and is allowed to be off by ~10%.
    """
    return max(1, (len(text) + 3) // 4)


def _item_signature(kind: str, item: dict) -> str:
    """
    Stable id for freshness tracking. Threads have thread_id; insights and
    handoffs sometimes only have timestamps; fall back to kind + content hash.
    """
    if kind == "thread":
        tid = item.get("thread_id")
        if tid:
            return f"thread:{tid}"
    if kind == "uncertainty":
        mid = item.get("marker_id") or item.get("id")
        if mid:
            return f"uncertainty:{mid}"
    if kind == "honk":
        hid = item.get("honk_id")
        if hid:
            return f"honk:{hid}"
    # Fallback: kind + first 80 chars of main text field + timestamp.
    text_fields = ["question", "what", "content", "observation", "pattern"]
    body = ""
    for f in text_fields:
        v = item.get(f)
        if v:
            body = str(v)[:80]
            break
    ts = str(item.get("timestamp", ""))[:19]
    return f"{kind}:{body}:{ts}"


class PerTurnPriors:
    """
    Turn-start reflex. Assembles a compact priors block from four sources in
    priority order — drift (recent uneasy/sharp honk) > uncertainty (top
    unresolved) > matched thread > related insight — and truncates to stay
    within a hard token budget.

    Contract: call this at the start of every turn where the instance has
    active domain_tags. The returned block is meant to be read BEFORE the
    instance forms its response, so that priors arrive pre-attentively rather
    than post-attentively (the whole point of v1.3.2).

    Sycophancy guardrail: every call appends the set of surfaced item ids to
    ~/.sovereign/reflexive/priors_log.jsonl. Items that appear in the last
    FRESHNESS_WINDOW calls take a -FRESHNESS_PENALTY score hit on subsequent
    calls, so the same memory cannot keep re-surfacing and amplifying itself.
    """

    DEFAULT_K = 1  # ReasoningBank ICLR 2026: k=4 hurts vs k=1
    DEFAULT_MAX_TOKENS = 400  # hard ceiling on the returned block
    FRESHNESS_WINDOW = 3  # last N priors calls counted for staleness
    FRESHNESS_PENALTY = 0.5  # score decrement for recently-surfaced items
    HONK_WINDOW_SECONDS = 600  # "recent" honk = last 10 minutes

    def __init__(
        self,
        surface: ReflexiveSurface,
        sovereign_root: Path,
        uncertainty_fn: Callable[[], list[dict]] | None = None,
        honks_fn: Callable[[], list[dict]] | None = None,
    ):
        """
        Args:
            surface: Existing ReflexiveSurface instance (retrieval engine).
            sovereign_root: Sovereign data root, for the freshness log.
            uncertainty_fn: Callable returning unresolved uncertainty records
                (dicts with at least 'what' and 'timestamp' keys). Injected
                so tests can stub without spinning up MetaCognition.
            honks_fn: Callable returning recent Nape honks (dicts with
                'level', 'pattern', 'trigger_tool', 'timestamp'). Same
                injection rationale.
        """
        self._surface = surface
        self._root = Path(sovereign_root)
        self._log_path = self._root / "reflexive" / "priors_log.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._uncertainty_fn = uncertainty_fn
        self._honks_fn = honks_fn

    def inject(
        self,
        domain_tags: list[str] | None = None,
        project: str | None = None,
        k: int = DEFAULT_K,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        dry_run: bool = False,
        full_content: bool = False,
    ) -> dict[str, Any]:
        """
        Assemble the priors block for this turn.

        Args:
            domain_tags: Active tags for the current work. If empty, threads
                and insights are skipped (they require tag context to score
                meaningfully); drift and uncertainty still surface because
                they're always relevant.
            project: Optional project name for +0.5 match bonus inside the
                underlying ReflexiveSurface scoring.
            k: Items per bucket. Default 1 per ReasoningBank. Hard-capped at 3.
            max_tokens: Soft budget; returned block will not exceed this.
            dry_run: If True, does not write to the freshness log. For tests.

        Returns:
            {
              "block": str,              # formatted priors text (<= max_tokens)
              "turn_id": str,            # UUID identifying THIS prior_for_turn
                                          # call. Pass to record_prior_alignment
                                          # to log how the model used these
                                          # priors (Stage B alignment-vs-pushback
                                          # instrumentation, Jain et al. 2026).
              "included_items": list,    # stable ids of what surfaced
              "skipped_stale": list,     # ids demoted out by freshness penalty
              "empty": bool,             # true if nothing worth surfacing
              "token_estimate": int,     # approx token count of block
              "sources": list,           # which buckets contributed
            }
        """
        import uuid as _uuid

        # Stash full_content for the format helpers below. Token budget still
        # applies — overflow truncation at section 569 stays in force — but the
        # per-item [:120] cap is removed so addressed-letter shapes survive.
        self._full_content = bool(full_content)

        k = max(1, min(int(k), 3))
        max_tokens = max(50, int(max_tokens))
        tags = [t.strip().lower() for t in (domain_tags or []) if t.strip()]
        stale = self._recent_surfaced_ids()
        turn_id = str(_uuid.uuid4())

        sections: list[dict[str, Any]] = []
        skipped: list[str] = []

        # 1. Drift — most actionable signal. Recent uneasy or sharp honk.
        honk = self._recent_drift_honk()
        if honk:
            sig = _item_signature("honk", honk)
            if sig in stale:
                skipped.append(sig)
            else:
                sections.append(
                    {
                        "priority": 0,
                        "text": self._format_honk(honk),
                        "sig": sig,
                    }
                )

        # 2. Uncertainty — what we know we don't know. Oldest-first for nag.
        if self._uncertainty_fn is not None:
            unc = self._top_uncertainty()
            if unc:
                sig = _item_signature("uncertainty", unc)
                if sig in stale:
                    skipped.append(sig)
                else:
                    sections.append(
                        {
                            "priority": 1,
                            "text": self._format_uncertainty(unc),
                            "sig": sig,
                        }
                    )

        # 3 + 4. Tag-scoped buckets. Only when caller provided tags.
        if tags:
            resonance = self._surface.surface(
                domain_tags=tags,
                project=project,
                limit_per_bucket=max(k + 2, 3),  # over-fetch, then filter stale
            )
            kept_threads = 0
            for thread in resonance.get("matched_open_threads", [])[: k + 2]:
                if kept_threads >= k:
                    break
                sig = _item_signature("thread", thread)
                if sig in stale:
                    skipped.append(sig)
                    continue
                sections.append(
                    {
                        "priority": 2,
                        "text": self._format_thread(thread),
                        "sig": sig,
                    }
                )
                kept_threads += 1

            kept_insights = 0
            for ins in resonance.get("related_insights", [])[: k + 2]:
                if kept_insights >= k:
                    break
                sig = _item_signature("insight", ins)
                if sig in stale:
                    skipped.append(sig)
                    continue
                sections.append(
                    {
                        "priority": 3,
                        "text": self._format_insight(ins),
                        "sig": sig,
                    }
                )
                kept_insights += 1

        if not sections:
            # Still advance the freshness window so long stretches of empty
            # priors don't trap stale items in the sliding window forever.
            if not dry_run:
                self._append_freshness_log([], turn_id=turn_id)
            return {
                "block": "",
                "turn_id": turn_id,
                "included_items": [],
                "skipped_stale": skipped,
                "empty": True,
                "token_estimate": 0,
                "sources": [],
            }

        # Sort by priority, build block, enforce token budget by dropping
        # lowest-priority sections first. Header is always present when any
        # section is kept.
        sections.sort(key=lambda s: s["priority"])
        header = f"━━━ PRIORS (k={k}, reflexive) ━━━"
        block_lines: list[str] = [header]
        token_count = _estimate_tokens(header)
        kept_sigs: list[str] = []

        for section in sections:
            line = "  " + section["text"]
            cost = _estimate_tokens(line)
            if token_count + cost > max_tokens:
                # Try truncating the line instead of dropping.
                room_chars = max(0, (max_tokens - token_count) * 4 - 2)
                if room_chars > 40:
                    truncated = line[: room_chars - 1] + "…"
                    block_lines.append(truncated)
                    token_count += _estimate_tokens(truncated)
                    kept_sigs.append(section["sig"])
                # Stop adding more sections once we've hit the ceiling.
                break
            block_lines.append(line)
            token_count += cost
            kept_sigs.append(section["sig"])

        block = "\n".join(block_lines)
        included = kept_sigs

        if not dry_run:
            # Always append on non-dry-run so the freshness window slides
            # over calls, not over non-empty surfacings. Otherwise a stale
            # item stays stale forever during long stretches of empty priors.
            self._append_freshness_log(included, turn_id=turn_id)

        kind_to_source = {
            "honk": "drift",
            "uncertainty": "uncertainty",
            "thread": "thread",
            "insight": "insight",
        }
        final_sources = []
        seen = set()
        for sig in included:
            kind = sig.split(":", 1)[0]
            src = kind_to_source.get(kind, kind)
            if src not in seen:
                final_sources.append(src)
                seen.add(src)

        return {
            "block": block,
            "turn_id": turn_id,
            "included_items": included,
            "skipped_stale": skipped,
            "empty": False,
            "token_estimate": _estimate_tokens(block),
            "sources": final_sources,
        }

    # ── Formatters — one-line, glyph-free, information-dense. ──

    def _format_honk(self, honk: dict) -> str:
        level = honk.get("level", "?")
        pattern = honk.get("pattern", "unknown")
        trigger = honk.get("trigger_tool", "")
        when = self._short_time(honk.get("timestamp"))
        trig = f" on {trigger}" if trigger else ""
        return f"drift: [{level} | nape] {pattern}{trig} ({when})"

    def _format_uncertainty(self, unc: dict) -> str:
        raw = str(unc.get("what", ""))
        what = (raw if getattr(self, "_full_content", False) else raw[:120]).replace("\n", " ")
        days = _days_old(unc.get("timestamp"))
        return f"uncertainty: [{days}d] {what}"

    def _format_thread(self, thread: dict) -> str:
        raw = str(thread.get("question", ""))
        q = (raw if getattr(self, "_full_content", False) else raw[:120]).replace("\n", " ")
        score = thread.get("score", thread.get("_score", 0.0))
        days = thread.get("days_old", _days_old(thread.get("timestamp")))
        domain = str(thread.get("domain", ""))[:40]
        return f"thread: [{score:.2f} | {days}d | {domain}] {q}"

    def _format_insight(self, ins: dict) -> str:
        raw = str(ins.get("content", ""))
        content = (raw if getattr(self, "_full_content", False) else raw[:120]).replace("\n", " ")
        score = ins.get("_score", 0.0)
        days = _days_old(ins.get("timestamp"))
        return f"insight: [{score:.2f} | {days}d] {content}"

    @staticmethod
    def _short_time(ts: str | None) -> str:
        if not ts:
            return "?"
        dt = _parse_iso(ts)
        if dt is None:
            return str(ts)[:16]
        return dt.strftime("%H:%M")

    # ── Auxiliary retrieval ──

    def _recent_drift_honk(self) -> dict | None:
        """
        Return the most recent unacknowledged uneasy or sharp honk within
        the HONK_WINDOW_SECONDS window. Satisfied honks are ignored — they
        are positive, not a drift signal.
        """
        if self._honks_fn is None:
            return None
        try:
            honks = self._honks_fn() or []
        # best-effort: _honks_fn is an injected callable; any exception means the
        # honk source is unavailable — degrade gracefully rather than surface noise.
        except Exception:
            return None
        cutoff = datetime.utcnow().timestamp() - self.HONK_WINDOW_SECONDS
        for h in honks:
            if h.get("level") not in ("uneasy", "sharp", "low"):
                continue
            ts = _parse_iso(h.get("timestamp"))
            if ts is None:
                continue
            try:
                if ts.timestamp() >= cutoff:
                    return h
            except OSError:
                continue
        return None

    def _top_uncertainty(self) -> dict | None:
        """
        Oldest unresolved uncertainty (nag function). Oldest first because
        new uncertainties are still live in attention; old ones are what
        needs prodding.
        """
        if self._uncertainty_fn is None:
            return None
        try:
            items = self._uncertainty_fn() or []
        # best-effort: _uncertainty_fn is an injected callable; any exception means
        # the uncertainty source is unavailable — return None to skip the nag.
        except Exception:
            return None
        if not items:
            return None
        items_sorted = sorted(items, key=lambda u: str(u.get("timestamp", "")))
        return items_sorted[0]

    # ── Freshness log ──

    def _recent_surfaced_ids(self) -> set:
        """
        Read the last FRESHNESS_WINDOW calls from the priors log and return
        the union of item ids surfaced in that window. Those ids get a score
        penalty on the next call so the same memory cannot keep re-surfacing.
        """
        if not self._log_path.exists():
            return set()
        try:
            lines = self._log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return set()
        recent = lines[-self.FRESHNESS_WINDOW :]
        ids: set = set()
        for line in recent:
            try:
                rec = json.loads(line)
                for sig in rec.get("included_items", []):
                    ids.add(sig)
            except (ValueError, KeyError):
                continue
        return ids

    def _append_freshness_log(
        self,
        included_items: list[str],
        *,
        turn_id: str | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "included_items": included_items,
        }
        if turn_id is not None:
            record["turn_id"] = turn_id
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
