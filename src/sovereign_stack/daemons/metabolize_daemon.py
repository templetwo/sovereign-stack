"""
MetabolizeDaemon — Step 4 of v1.3.2. Nightly chronicle digestion.

Once per night (via launchd at 03:17), surface NEW contradictions, stale
threads, and aging hypotheses to comms with ack_required=True, and write
a decision note to ~/.sovereign/decisions/metabolize_<ts>.md for Anthony's
weekly review. Three consecutive unacked digests triggers a halt + alert,
inheriting the load-bearing circuit-breaker pattern from Step 3.

After the BaseDaemon lift (post-Step 4), the shared scaffolding lives in
base.py: DaemonState, _load_state, _save_state, _count_recent_unacked,
_record_post, _perform_halt, _post_halt_alert. What remains here is the
daemon-specific work — detection delegation, delta filtering, decision-
file writing, and the per-daemon RunResult shape.

Daemon-specific design calls (load-bearing rationale; do not re-litigate):

  * No LLM in v1. The Step 3 handoff projected an LLM-using metabolize
    daemon. Anthony's call: "no llm." Templated digest, zero hallucination
    surface. Step 4b can layer LLM-extracted strategies behind a separate
    grounded_extract gate later if observation week shows the digest is
    too noisy.

  * Delta-only digests. Each posted digest fingerprints its items and
    stores them in posted_digests. The next run subtracts items whose
    fingerprint already appeared in the most recent prior digest. If the
    delta is empty, OUTCOME_NO_CHANGES — distinct from OUTCOME_NO_FINDINGS.
    This is what "delta-only" means in a no-LLM daemon: the surface to
    Anthony is delta-shaped, not nightly-rerun.

  * Two output sinks. Comms post (live nudge) AND ~/.sovereign/decisions/
    metabolize_<ts>.md (durable record, fuller content with cross-
    reference back to comms message_id).

  * Detection delegated, not duplicated. detect_fn callable returns the
    same dict shape as metabolize(action='detect'); entrypoint.py wires
    a sync version of metabolism.handle_metabolism_tool's detect branch.

  * Evidence path is a non-chronicle file (metabolism_log.jsonl), not
    chronicle directories. grounded_extract treats chronicle paths as
    JSONL files needing layer inspection — passing a directory to that
    code path returns PATH_UNREADABLE. The TestRealGrounding integration
    test in tests/test_metabolize_daemon.py is the canary.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import provenance
from ..grounding import GroundingResult, grounded_extract
from ..memory import ExperientialMemory, load_entries
from .base import (
    COMPASS_PAUSE,
    COMPASS_PROCEED,
    CONSECUTIVE_UNACKED_THRESHOLD,
    DEFAULT_CHANNEL,
    OUTCOME_ALREADY_HALTED,
    OUTCOME_DRY_RUN,
    OUTCOME_GROUNDING_FAILED,
    OUTCOME_HALTED,
    OUTCOME_PAUSED,
    OUTCOME_POSTED,
    STATE_SCHEMA_VERSION,  # noqa: F401 — re-exported; tests import from here
    BaseDaemon,
    DaemonState,
)
from .senders import SENDER_METABOLIZE

# ── Daemon-specific tunables ──

MAX_DIGEST_ITEMS_PER_CATEGORY = 5
DEFAULT_TTL_DAYS = 14  # longer than uncertainty (3-day cadence surfaced
# more often) — nightly digests need more
# breathing room before TTL.


# ── Daemon-specific outcome codes ──

OUTCOME_NO_FINDINGS = "no_findings"
OUTCOME_NO_CHANGES = "no_changes"


@dataclass
class RunResult:
    outcome: str
    details: str = ""
    posted_message_id: str | None = None
    halt_path: str | None = None
    decision_path: str | None = None
    compass_decision: str | None = None
    grounding_reason: str | None = None
    contradictions_included: int = 0
    stale_threads_included: int = 0
    stale_hypotheses_included: int = 0


# ── Fingerprint helpers (delta filter) ──


def _fingerprint(*parts: str) -> str:
    """
    Stable short hash of a tuple of strings. Truncated to 16 hex chars —
    collision probability is negligible at the volumes we care about
    (hundreds of items per digest).
    """
    h = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return h[:16]


def _contradiction_key(c: dict) -> str:
    return _fingerprint(
        "contradiction",
        str(c.get("hypothesis_domain", "")),
        str(c.get("hypothesis_timestamp", "")),
        str(c.get("ground_truth_domain", "")),
        str(c.get("ground_truth_timestamp", "")),
    )


def _stale_thread_key(t: dict) -> str:
    return _fingerprint(
        "stale_thread",
        str(t.get("domain", "")),
        str(t.get("question", ""))[:120],
    )


def _stale_hypothesis_key(h: dict) -> str:
    return _fingerprint(
        "stale_hypothesis",
        str(h.get("domain", "")),
        str(h.get("content", ""))[:120],
    )


class MetabolizeDaemon(BaseDaemon):
    """
    Scheduled daemon: nightly chronicle digestion. Surfaces NEW
    contradictions / stale threads / aging hypotheses since the last
    posted digest. Halts on three-consecutive-unacked.

    Inherits from BaseDaemon — see base.py for state schema, halt-write
    contract, ack-counting, and circuit-breaker semantics.

    Daemon-specific injected callables:
        detect_fn() -> dict with keys "contradictions", "stale_threads",
                       "stale_hypotheses", "stats" (matches the existing
                       metabolize(action='detect') return shape).

    Daemon-specific paths:
        decisions_dir: Where nightly decision notes are written.
        evidence_paths: List of paths passed to grounded_extract. Default
            wiring uses metabolism_log.jsonl — non-chronicle, structural
            evidence, accepted by grounded_extract on existence alone.
    """

    SENDER = SENDER_METABOLIZE
    HALT_FILENAME_TAG = "metabolize"
    HALT_SOURCE = "metabolize"
    DAEMON_LABEL = "daemon.metabolize"

    def __init__(
        self,
        *,
        state_path: Path,
        halt_dir: Path,
        decisions_dir: Path,
        evidence_paths: list[Path],
        compass_fn: Callable[..., dict],
        detect_fn: Callable[[], dict],
        comms_post_fn: Callable[..., dict],
        comms_get_acks_fn: Callable[[str], list[dict]],
        grounding_fn: Callable[..., GroundingResult] = grounded_extract,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_fn=None,
        unacked_threshold: int = CONSECUTIVE_UNACKED_THRESHOLD,
        max_items_per_category: int = MAX_DIGEST_ITEMS_PER_CATEGORY,
        ttl_days: int = DEFAULT_TTL_DAYS,
        channel: str = DEFAULT_CHANNEL,
    ):
        kwargs = {
            "state_path": state_path,
            "halt_dir": halt_dir,
            "compass_fn": compass_fn,
            "comms_post_fn": comms_post_fn,
            "comms_get_acks_fn": comms_get_acks_fn,
            "grounding_fn": grounding_fn,
            "now_fn": now_fn,
            "unacked_threshold": unacked_threshold,
            "channel": channel,
        }
        if id_fn is not None:
            kwargs["id_fn"] = id_fn
        super().__init__(**kwargs)

        self.decisions_dir = Path(decisions_dir)
        self.evidence_paths = [Path(p) for p in evidence_paths]
        self._detect_fn = detect_fn
        self.max_items_per_category = int(max_items_per_category)
        self.ttl_days = int(ttl_days)

    # ── Halt-body hooks ──

    def _halt_what_tried(self) -> list[str]:
        return [
            "Post nightly metabolism digests surfacing new contradictions,",
            "stale threads, and aging hypotheses to prompt chronicle integration.",
        ]

    def _halt_blocked_downstream(self) -> list[str]:
        return [
            "- Further metabolism digests paused until manual reset.",
            "- Aging hypotheses and stale threads will continue to drift.",
            "- Chronicle hygiene work has no scheduled prompt until this clears.",
        ]

    # ── Public entry point ──

    def run(self, *, dry_run: bool = False) -> RunResult:
        state = self._load_state()

        if state.halted_at:
            return RunResult(
                outcome=OUTCOME_ALREADY_HALTED,
                details=(
                    f"Halt standing since {state.halted_at} "
                    f"(reason={state.halt_reason}). "
                    "Clear state file or set halted_at=None to resume."
                ),
            )

        # Circuit breaker first — don't do detection work if we're about
        # to halt anyway.
        unacked = self._count_recent_unacked(state)
        if unacked >= self.unacked_threshold:
            if dry_run:
                return RunResult(
                    outcome=OUTCOME_HALTED,
                    details=(
                        f"Would halt: {unacked} of last "
                        f"{self.unacked_threshold} digests unacked. "
                        "(dry_run=True, not writing)"
                    ),
                )
            halt_path = self._perform_halt(
                state,
                reason="consecutive_unacked_threshold_reached",
                evidence_note=(
                    f"{unacked} of the last {self.unacked_threshold} "
                    f"posted metabolism digests were not acknowledged by any "
                    f"instance within the observation window."
                ),
            )
            return RunResult(
                outcome=OUTCOME_HALTED,
                details=f"Halt written to {halt_path}.",
                halt_path=str(halt_path),
            )

        compass = (
            self._compass_fn(
                action="metabolize_nightly",
                stakes="medium",
            )
            or {}
        )
        decision = compass.get("decision", COMPASS_PROCEED)
        if decision == COMPASS_PAUSE:
            return RunResult(
                outcome=OUTCOME_PAUSED,
                details=f"compass_check returned PAUSE: {compass.get('rationale', '')}",
                compass_decision=COMPASS_PAUSE,
            )

        # Run detection.
        digest = self._detect_fn() or {}
        contradictions = list(digest.get("contradictions", []))
        stale_threads = list(digest.get("stale_threads", []))
        stale_hypotheses = list(digest.get("stale_hypotheses", []))

        if not (contradictions or stale_threads or stale_hypotheses):
            return RunResult(
                outcome=OUTCOME_NO_FINDINGS,
                details="Chronicle clean: no contradictions, stale threads, or aging hypotheses.",
                compass_decision=decision,
            )

        # Delta filter.
        prior_fingerprints = self._most_recent_fingerprints(state)
        contradictions_new = [
            c for c in contradictions if _contradiction_key(c) not in prior_fingerprints
        ]
        stale_threads_new = [
            t for t in stale_threads if _stale_thread_key(t) not in prior_fingerprints
        ]
        stale_hypotheses_new = [
            h for h in stale_hypotheses if _stale_hypothesis_key(h) not in prior_fingerprints
        ]

        if not (contradictions_new or stale_threads_new or stale_hypotheses_new):
            return RunResult(
                outcome=OUTCOME_NO_CHANGES,
                details=(
                    f"All {len(contradictions)} contradictions, "
                    f"{len(stale_threads)} stale threads, "
                    f"{len(stale_hypotheses)} aging hypotheses already "
                    "surfaced in the previous digest."
                ),
                compass_decision=decision,
            )

        # Cap each category for digest readability.
        contradictions_new = contradictions_new[: self.max_items_per_category]
        stale_threads_new = stale_threads_new[: self.max_items_per_category]
        stale_hypotheses_new = stale_hypotheses_new[: self.max_items_per_category]

        # Grounding gate.
        grounding = self._grounding_fn(
            claim="metabolize daemon posting nightly digest",
            evidence_paths=[str(p) for p in self.evidence_paths],
        )
        if not grounding:
            return RunResult(
                outcome=OUTCOME_GROUNDING_FAILED,
                details=(
                    f"grounded_extract rejected the digest "
                    f"(reason={grounding.reason}). Skipping post, "
                    "not counting toward unacked threshold."
                ),
                compass_decision=decision,
                grounding_reason=grounding.reason,
            )

        message_id = self._new_id()
        now = self._now()
        content = self._format_digest(
            contradictions=contradictions_new,
            stale_threads=stale_threads_new,
            stale_hypotheses=stale_hypotheses_new,
            stats=digest.get("stats", {}),
            message_id=message_id,
            now=now,
        )

        if dry_run:
            return RunResult(
                outcome=OUTCOME_DRY_RUN,
                details=(
                    f"Would post {len(contradictions_new)} contradictions, "
                    f"{len(stale_threads_new)} stale threads, "
                    f"{len(stale_hypotheses_new)} aging hypotheses "
                    f"as message_id={message_id}."
                ),
                posted_message_id=message_id,
                compass_decision=decision,
                grounding_reason=grounding.reason,
                contradictions_included=len(contradictions_new),
                stale_threads_included=len(stale_threads_new),
                stale_hypotheses_included=len(stale_hypotheses_new),
            )

        # Decision note (durable record).
        decision_path = self._write_decision(
            contradictions=contradictions_new,
            stale_threads=stale_threads_new,
            stale_hypotheses=stale_hypotheses_new,
            stats=digest.get("stats", {}),
            message_id=message_id,
            now=now,
        )

        # Comms post.
        self._comms_post_fn(
            sender=SENDER_METABOLIZE,
            content=content,
            channel=self.channel,
            message_id=message_id,
            extra_fields={
                "ack_required": True,
                "ttl_days": self.ttl_days,
                "daemon": "metabolize",
                "decision_path": str(decision_path),
            },
        )

        # Record fingerprints so the next run's delta filter works.
        fingerprints = (
            [_contradiction_key(c) for c in contradictions_new]
            + [_stale_thread_key(t) for t in stale_threads_new]
            + [_stale_hypothesis_key(h) for h in stale_hypotheses_new]
        )
        self._record_post(
            state,
            message_id=message_id,
            content=content,
            now=now,
            extra={
                "fingerprints": fingerprints,
                "decision_path": str(decision_path),
            },
        )

        return RunResult(
            outcome=OUTCOME_POSTED,
            details=(
                f"Posted {len(contradictions_new)} contradictions, "
                f"{len(stale_threads_new)} stale threads, "
                f"{len(stale_hypotheses_new)} aging hypotheses."
            ),
            posted_message_id=message_id,
            decision_path=str(decision_path),
            compass_decision=decision,
            grounding_reason=grounding.reason,
            contradictions_included=len(contradictions_new),
            stale_threads_included=len(stale_threads_new),
            stale_hypotheses_included=len(stale_hypotheses_new),
        )

    # ── Delta-filter helper ──

    def _most_recent_fingerprints(self, state: DaemonState) -> set:
        """
        Return the set of fingerprints from the most recent posted digest
        (or empty set if none). Only the latest is used — delta semantics
        are "since last notification," not "ever."
        """
        if not state.posted_digests:
            return set()
        last = state.posted_digests[-1]
        return set(last.get("fingerprints", []))

    # ── Digest formatting ──

    def _format_digest(
        self,
        *,
        contradictions: list[dict],
        stale_threads: list[dict],
        stale_hypotheses: list[dict],
        stats: dict,
        message_id: str,
        now: datetime,
    ) -> str:
        lines = [
            "Nightly metabolism digest — new since last cycle",
            f"(posted {now.date().isoformat()} by {SENDER_METABOLIZE})",
        ]
        if stats:
            lines.append(
                f"Chronicle: {stats.get('total_insights', 0)} insights "
                f"({stats.get('ground_truths', 0)} ground truth, "
                f"{stats.get('hypotheses', 0)} hypotheses), "
                f"{stats.get('open_threads', 0)} open threads."
            )
        lines.append("")

        if contradictions:
            lines.append(f"⚠ {len(contradictions)} new contradiction(s):")
            for i, c in enumerate(contradictions, start=1):
                lines.append(
                    f"  {i}. [{c.get('hypothesis_domain', '?')}] "
                    f"{str(c.get('hypothesis_preview', ''))[:100]}"
                )
                lines.append(
                    f"     vs ground truth [{c.get('ground_truth_domain', '?')}]: "
                    f"{str(c.get('ground_truth_preview', ''))[:100]}"
                )
                lines.append(f"     overlap={c.get('overlap_score', '?')}")
            lines.append("")

        if stale_threads:
            lines.append(f"🕸 {len(stale_threads)} new stale thread(s):")
            for i, t in enumerate(stale_threads, start=1):
                lines.append(
                    f"  {i}. [{t.get('domain', '?')}] "
                    f"{str(t.get('question', ''))[:100]} "
                    f"({t.get('age_days', '?')}d old)"
                )
            lines.append("")

        if stale_hypotheses:
            lines.append(f"📜 {len(stale_hypotheses)} new aging hypothesis(es):")
            for i, h in enumerate(stale_hypotheses, start=1):
                lines.append(
                    f"  {i}. [{h.get('domain', '?')}] "
                    f"{str(h.get('content', ''))[:100]} "
                    f"({h.get('age_days', '?')}d old)"
                )
            lines.append("")

        lines.extend(
            [
                f'Acknowledge with comms_acknowledge(message_id="{message_id}", '
                "instance_id=<your id>, note=<what was integrated>).",
                "",
                "Full decision note alongside this post; see decision_path field.",
                "",
                f"{self.unacked_threshold} consecutive unacked digests triggers daemon halt.",
            ]
        )
        return "\n".join(lines)

    # ── Decision-note write ──

    def _write_decision(
        self,
        *,
        contradictions: list[dict],
        stale_threads: list[dict],
        stale_hypotheses: list[dict],
        stats: dict,
        message_id: str,
        now: datetime,
    ) -> Path:
        self.decisions_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%S")
        path = self.decisions_dir / f"metabolize_{stamp}.md"

        lines = [
            f"# Metabolism digest — {now.date().isoformat()}",
            f"Timestamp: {now.isoformat()}",
            f"Comms message: {message_id}",
            f'Acknowledge via: `comms_acknowledge(message_id="{message_id}", instance_id=..., note=...)`',
            "",
            "## Chronicle stats",
            f"- Total insights: {stats.get('total_insights', '?')}",
            f"- Ground truths: {stats.get('ground_truths', '?')}",
            f"- Hypotheses: {stats.get('hypotheses', '?')}",
            f"- Open threads: {stats.get('open_threads', '?')}",
            "",
        ]

        if contradictions:
            lines.append(f"## Contradictions ({len(contradictions)} new)")
            for i, c in enumerate(contradictions, start=1):
                lines.extend(
                    [
                        "",
                        f"### {i}. [{c.get('hypothesis_domain', '?')}] vs "
                        f"[{c.get('ground_truth_domain', '?')}]",
                        f"- Overlap score: {c.get('overlap_score', '?')}",
                        f"- Hypothesis (timestamp {c.get('hypothesis_timestamp', '?')}):",
                        f"  > {c.get('hypothesis_preview', '')}",
                        f"- Ground truth (timestamp {c.get('ground_truth_timestamp', '?')}):",
                        f"  > {c.get('ground_truth_preview', '')}",
                    ]
                )
            lines.append("")

        if stale_threads:
            lines.append(f"## Stale threads ({len(stale_threads)} new)")
            for i, t in enumerate(stale_threads, start=1):
                lines.extend(
                    [
                        "",
                        f"### {i}. [{t.get('domain', '?')}] {t.get('age_days', '?')}d old",
                        f"- Timestamp: {t.get('timestamp', '?')}",
                        f"- Question: {t.get('question', '?')}",
                    ]
                )
            lines.append("")

        if stale_hypotheses:
            lines.append(f"## Aging hypotheses ({len(stale_hypotheses)} new)")
            for i, h in enumerate(stale_hypotheses, start=1):
                lines.extend(
                    [
                        "",
                        f"### {i}. [{h.get('domain', '?')}] {h.get('age_days', '?')}d old",
                        f"- Content: {h.get('content', '?')}",
                    ]
                )
            lines.append("")

        lines.extend(
            [
                "## How to act on this",
                "- For each contradiction: either retire the hypothesis "
                "(`retire_hypothesis`) or update the ground_truth.",
                "- For each stale thread: resolve it (`resolve_thread_by_id`) "
                "or accept it as long-running and touch it (`thread_touch`).",
                "- For each aging hypothesis: promote, retire, or leave to age "
                "further — hypotheses don't expire automatically.",
                "",
            ]
        )
        path.write_text("\n".join(lines), encoding="utf-8")
        return path


# ═══════════════════════════════════════════════════════════════════════════
# NREM PRUNE PASS  (dream-layer, phase 1 — DETECTOR / PROPOSE-ONLY)
# ═══════════════════════════════════════════════════════════════════════════
#
# Anthony's design: "prune the old to grow taller." Real sleep is NREM
# (consolidate / clean / prune HYGIENE) then REM (dream). The dream daemon
# (REM, 04:47) ships. This is the NREM pass that runs BEFORE it (~04:07) so
# the dream associates over cleaner memory — the same merge/dedupe/prune/
# reindex hygiene Anthropic's Auto Dream describes.
#
# REVIVAL FRAMING (read this before wiring anything):
#   The original MetabolizeDaemon above (nightly comms digest, halted since
#   2026-05-21, run via `entrypoint metabolize`) is LEFT HALTED BY DESIGN.
#   The prune pass is the revival-in-a-new-role, birthed as a SIBLING with
#   its own module main() + its own UNLOADED plist — mirroring how the dream
#   and synthesis daemons each own their entrypoint rather than sharing
#   entrypoint.py's log_line. `entrypoint metabolize` is NOT repurposed.
#
# THE SAFETY LAW (violate NONE — the chronicle is PUBLIC, append-only,
# hash-anchored, governed by standing experimental law):
#   * The chronicle NEVER DELETES. "Corrections supersede, never erase."
#     No destructive deletion, no in-place edit of any existing entry, EVER.
#   * Dedupe/merge = via the EXISTING SUPERSESSION mechanism ONLY (mark a
#     duplicate superseded by a canonical entry — append-only, revocable,
#     mandatory carry-forward). Executed by HQ via supersede_insight().
#   * "Prune stale" = RETENTION / SURFACING marking only — an old low-value
#     entry stops SURFACING on the rail but STAYS on disk (exactly like the
#     dream 45-day rail-retirement). It never removes the entry.
#   * PHASE 1 IS DETECTOR / DRY-RUN / PROPOSE-ONLY. It scans, identifies
#     candidates, and writes a PROPOSAL file for HQ/human review. It does
#     NOT execute destructive actions and does NOT auto-supersede on its own
#     judgment. There is NO executor code path in phase 1 at all.
#   * Fail-soft: any error -> clean skip, never crash, never partial-mutate.
#   * Reads through the canonical load_entries chokepoint (supersession-safe);
#     writes ONLY the proposal file (which lives OUTSIDE the chronicle tree).
#
# The detection mirrors seasons.season_review's supersession-candidate logic
# (skip same-claim-id, dedupe pairs, skip already-folded predecessors, honor
# the supersession guards), so every proposed supersede is provably runnable
# by HQ as supersede_insight(predecessor_id=..., successor_id=...,
# carry_forward_summary=...).


# ── Prune tunables (env-overridable via main()) ──

PRUNE_SIMILARITY_THRESHOLD = 0.72  # token-Jaccard floor for a near-dup pair
PRUNE_RETENTION_DAYS = 45  # stale-if-older-than (matches the dream rail-retirement horizon)
PRUNE_INTENSITY_CEILING = 0.30  # low-value-if-below (only aging low-intensity entries retire)
PRUNE_MAX_CANDIDATES_PER_TYPE = 200  # keep a night's proposal reviewable
PRUNE_SUPERSEDED_SURFACING_CAP = 50  # the fuzziest category — capped hard

# Env-var names.
ENV_PRUNE_ENABLED = "METABOLIZE_PRUNE"  # "off" disables the whole pass
ENV_PRUNE_DRY_RUN = "METABOLIZE_PRUNE_DRY_RUN"  # default TRUE; phase 1 is always propose-only
ENV_PRUNE_RETENTION_DAYS = "METABOLIZE_PRUNE_RETENTION_DAYS"
ENV_PRUNE_INTENSITY_CEILING = "METABOLIZE_PRUNE_INTENSITY_CEILING"
ENV_PRUNE_SIMILARITY = "METABOLIZE_PRUNE_SIMILARITY"

# Prune outcome codes (distinct from the digest daemon's above).
OUTCOME_PRUNE_DISABLED = "prune_disabled"  # METABOLIZE_PRUNE=off
OUTCOME_PRUNE_NO_CHRONICLE = "no_chronicle"  # insights/ dir absent
OUTCOME_PRUNE_NO_CANDIDATES = "no_candidates"  # clean scan (empty proposal still written)
OUTCOME_PRUNE_PROPOSED = "proposed"  # >=1 candidate, proposal written
OUTCOME_PRUNE_ERROR = "error"  # fail-soft catch — never raised

PRUNE_PROPOSAL_SCHEMA_VERSION = 1


@dataclass
class PruneRunResult:
    """
    Outcome of one NREM prune-pass run. RunResult-shaped like the sibling
    daemons (synthesis/dream): a stable `outcome` field plus counters. No
    comms/halt fields — the prune pass posts nothing and cannot halt; it
    only writes a proposal for HQ.
    """

    outcome: str
    details: str = ""
    proposal_path: str | None = None
    entry_count: int = 0
    near_duplicate: int = 0
    superseded_still_surfacing: int = 0
    stale_low_intensity: int = 0
    total_candidates: int = 0
    dry_run: bool = True


# ── Detection helpers (pure, per-entry fail-soft) ──


def _prune_env_disabled() -> bool:
    """True when METABOLIZE_PRUNE is explicitly off/0/false/disabled."""
    return os.environ.get(ENV_PRUNE_ENABLED, "").strip().lower() in {
        "off",
        "0",
        "false",
        "no",
        "disabled",
    }


def _prune_dry_run_default() -> bool:
    """
    DRY_RUN defaults TRUE. Only an explicit off/0/false flips it — and even
    then phase 1 has NO executor, so the pass stays propose-only regardless
    (the flag is plumbed so phase 2 inherits a safe default).
    """
    raw = os.environ.get(ENV_PRUNE_DRY_RUN)
    if raw is None:
        return True
    return raw.strip().lower() not in {"off", "0", "false", "no"}


def _safe_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _entry_age_days(entry: dict, now: datetime) -> float | None:
    """Age in days from the entry's ISO timestamp, or None if unparseable."""
    ts = entry.get("timestamp")
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds() / 86400.0


def _domain_tags(entry: dict) -> set[str]:
    """
    Split a compound domain ("a,b,c") into a tag set for overlap bucketing.
    Uses the stored `domain` field, falling back to the `_domain_dir`
    read-annotation. Lowercased, whitespace-trimmed, empties dropped.
    """
    raw = entry.get("domain") or entry.get("_domain_dir") or ""
    if not isinstance(raw, str):
        raw = str(raw)
    return {tag.strip().lower() for tag in raw.split(",") if tag.strip()}


def _is_lived(entry: dict) -> bool:
    """
    True when the entry carries a LIVED vantage (human_observation /
    human_attestation / witnessed_account). Lived sentinels keep their pin —
    they are NEVER proposed for supersession or rail-retirement, independent
    of intensity. (See CLAUDE.md lived-ground-truth law.)
    """
    vantage = entry.get("vantage")
    return isinstance(vantage, str) and vantage in provenance.LIVED_VANTAGES


def _preview(text: object, n: int = 120) -> str:
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    return s[:n]


def _entry_ref(entry: dict) -> dict:
    """Compact, HQ-actionable reference to one entry (full + display id)."""
    claim_id = provenance.derive_claim_id(entry)
    return {
        "claim_id": claim_id,  # full 64-hex — supersede_insight needs the full id
        "display_id": provenance.display_id(claim_id),
        "domain": entry.get("domain", entry.get("_domain_dir", "?")),
        "timestamp": entry.get("timestamp", "?"),
        "layer": entry.get("layer", "?"),
        "intensity": _safe_float(entry.get("intensity"), 0.5),
        "preview": _preview(entry.get("content")),
    }


def _similarity_confidence(similarity: float, threshold: float) -> float:
    """Map a similarity in [threshold, 1.0] onto a confidence in [0.5, 0.95]."""
    span = max(1e-9, 1.0 - threshold)
    frac = max(0.0, min(1.0, (similarity - threshold) / span))
    return round(0.5 + frac * 0.45, 3)


def detect_near_duplicates(
    entries: list[dict],
    sup_fold: dict[str, dict],
    *,
    similarity_threshold: float = PRUNE_SIMILARITY_THRESHOLD,
    min_domain_overlap: int = 1,
    max_candidates: int = PRUNE_MAX_CANDIDATES_PER_TYPE,
) -> tuple[list[dict], set[str]]:
    """
    Detect near-duplicate insight pairs that share >=min_domain_overlap
    domain tags AND have content token-Jaccard >= similarity_threshold.

    Proposes a SUPERSEDE candidate per pair: the survivor (canonical =
    higher operational intensity, tie-break newer) becomes the successor,
    the other becomes the predecessor to be superseded. Every emitted
    candidate has passed provenance.check_supersession_guards, so HQ can run
    it verbatim as supersede_insight(); pairs that would self-supersede,
    double-supersede, or cycle are silently dropped.

    Bucketing by domain tag keeps this near-linear on the real chronicle
    (785 high-cardinality compound-domain dirs => tiny buckets).

    Returns (candidates, proposed_predecessor_ids) — the id set lets the
    retire detectors skip entries already headed for supersession.
    """
    # Skip entries that are already superseded (annotated by load_entries),
    # lived sentinels, or protected. Build tag buckets over the rest.
    buckets: dict[str, list[dict]] = {}
    for entry in entries:
        try:
            if "_superseded_by" in entry or _is_lived(entry):
                continue
            for tag in _domain_tags(entry):
                buckets.setdefault(tag, []).append(entry)
        except Exception:
            continue  # one bad entry never sinks the scan

    candidates: list[dict] = []
    seen_pairs: set[frozenset] = set()
    proposed_predecessors: set[str] = set()

    for _tag, bucket in buckets.items():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                if len(candidates) >= max_candidates:
                    return candidates, proposed_predecessors
                a, b = bucket[i], bucket[j]
                try:
                    a_id = provenance.derive_claim_id(a)
                    b_id = provenance.derive_claim_id(b)
                    if a_id == b_id:
                        # Byte-identical timestamp+domain+content => one claim
                        # id. Un-supersedable (self-supersession is refused);
                        # this is exact-dup noise, not an actionable pair.
                        continue
                    pair = frozenset((a_id, b_id))
                    if pair in seen_pairs:
                        continue
                    # Domain-overlap gate (same/overlapping domain).
                    if len(_domain_tags(a) & _domain_tags(b)) < min_domain_overlap:
                        continue
                    similarity = provenance.token_overlap(
                        a.get("content", ""), b.get("content", "")
                    )
                    if similarity < similarity_threshold:
                        continue
                    seen_pairs.add(pair)

                    # Canonical (survivor) = higher intensity, tie-break newer.
                    a_int = _safe_float(a.get("intensity"), 0.5)
                    b_int = _safe_float(b.get("intensity"), 0.5)
                    if (a_int, str(a.get("timestamp", ""))) >= (
                        b_int,
                        str(b.get("timestamp", "")),
                    ):
                        canonical, duplicate = a, b
                        canonical_id, duplicate_id = a_id, b_id
                    else:
                        canonical, duplicate = b, a
                        canonical_id, duplicate_id = b_id, a_id

                    # Predecessor already formalized? skip (ledger is canonical).
                    if duplicate_id in sup_fold:
                        continue
                    # Only emit if the supersession is provably runnable.
                    try:
                        provenance.check_supersession_guards(
                            duplicate_id, canonical_id, sup_fold
                        )
                    except provenance.SupersessionError:
                        continue

                    exact = similarity >= 0.999
                    candidates.append(
                        {
                            "candidate_type": "near_duplicate",
                            "action": "supersede_insight",
                            "predecessor": _entry_ref(duplicate),
                            "successor": _entry_ref(canonical),
                            "similarity": round(similarity, 4),
                            "confidence": _similarity_confidence(
                                similarity, similarity_threshold
                            ),
                            "safe_auto": bool(exact),
                            "rationale": (
                                f"Near-duplicate (token-Jaccard {similarity:.2f}) within "
                                f"shared domain. Propose superseding the lower-intensity/"
                                f"older entry by the survivor. Both stay on disk; the "
                                f"ledger records the merge. "
                                + (
                                    "Content is byte-identical (differing only by "
                                    "timestamp) — safe_auto candidate, but HQ still acts."
                                    if exact
                                    else "HQ confirms the survivor before linking."
                                )
                            ),
                            "hq_call": (
                                'supersede_insight(predecessor_id="'
                                f'{duplicate_id}", successor_id="{canonical_id}", '
                                'carry_forward_summary="<what the duplicate still teaches>")'
                            ),
                            "suggested_carry_forward_summary": (
                                f"Near-duplicate of {provenance.display_id(canonical_id)} "
                                f"(sim {similarity:.2f}); original wording preserved in ledger."
                            )[:500],
                        }
                    )
                    proposed_predecessors.add(duplicate_id)
                except Exception:
                    continue

    return candidates, proposed_predecessors


def detect_superseded_still_surfacing(
    entries: list[dict],
    *,
    skip_ids: set[str] | None = None,
    max_candidates: int = PRUNE_SUPERSEDED_SURFACING_CAP,
) -> list[dict]:
    """
    Flag entries the ledger already marks superseded (they carry
    `_superseded_by` from load_entries). These are only "still surfacing" in
    RAW reads — the boot rail already filters them via exclude_superseded.
    So this is the FUZZIEST category: LOW confidence, hard cap, and the
    rationale says plainly that supersession already deprioritizes them. It
    exists so HQ can spot-audit that nothing leaks past the rail; it is not a
    live-leak alarm. Action is a non-destructive surfacing-retention check.
    """
    skip_ids = skip_ids or set()
    out: list[dict] = []
    for entry in entries:
        try:
            if "_superseded_by" not in entry:
                continue
            ref = _entry_ref(entry)
            if ref["claim_id"] in skip_ids:
                continue
            out.append(
                {
                    "candidate_type": "superseded_still_surfacing",
                    "action": "confirm_rail_retired",  # non-destructive; verify-only
                    "target": ref,
                    "superseded_by": entry.get("_superseded_by"),
                    "confidence": 0.35,
                    "safe_auto": False,
                    "rationale": (
                        "Ledger marks this superseded; the boot rail already filters "
                        "superseded entries (exclude_superseded), so this is a "
                        "spot-audit candidate, NOT a live leak. HQ confirms it does "
                        "not surface anywhere it shouldn't. Entry stays on disk."
                    ),
                }
            )
            if len(out) >= max_candidates:
                break
        except Exception:
            continue
    return out


def detect_stale_low_intensity(
    entries: list[dict],
    now: datetime,
    *,
    retention_days: int = PRUNE_RETENTION_DAYS,
    intensity_ceiling: float = PRUNE_INTENSITY_CEILING,
    skip_ids: set[str] | None = None,
    max_candidates: int = PRUNE_MAX_CANDIDATES_PER_TYPE,
) -> list[dict]:
    """
    Flag entries older than retention_days AND below intensity_ceiling for
    RAIL-RETIREMENT (stop surfacing, stay on disk — the dream 45-day pattern).

    Excludes, by law: ground_truth facts (a port number is still a port
    number when old), lived sentinels (kept-pin), already-superseded entries,
    and anything already proposed for supersession this run.
    """
    skip_ids = skip_ids or set()
    out: list[dict] = []
    for entry in entries:
        try:
            if "_superseded_by" in entry or _is_lived(entry):
                continue
            if entry.get("layer") == ExperientialMemory.LAYER_GROUND_TRUTH:
                continue
            intensity = _safe_float(entry.get("intensity"), 0.5)
            if intensity >= intensity_ceiling:
                continue
            age = _entry_age_days(entry, now)
            if age is None or age <= retention_days:
                continue
            ref = _entry_ref(entry)
            if ref["claim_id"] in skip_ids:
                continue
            out.append(
                {
                    "candidate_type": "stale_low_intensity",
                    "action": "retire_from_surfacing",  # retention marking; NOT deletion
                    "target": ref,
                    "age_days": round(age, 1),
                    "confidence": round(min(0.7, 0.4 + (age - retention_days) / 365.0), 3),
                    "safe_auto": False,
                    "rationale": (
                        f"Aging low-value entry: {round(age)}d old, intensity "
                        f"{intensity:.2f} < {intensity_ceiling}. Propose retiring it "
                        f"from rail surfacing (like the dream 45-day retirement). It "
                        f"STAYS on disk and remains recallable; it just stops "
                        f"cluttering what surfaces."
                    ),
                }
            )
            if len(out) >= max_candidates:
                break
        except Exception:
            continue
    return out


@dataclass
class MetabolizePrunePass:
    """
    NREM prune pass — phase 1, propose-only. Standalone (NOT a BaseDaemon):
    it posts nothing, halts nothing, and only ever writes a proposal file
    OUTSIDE the chronicle tree. Constructor-injected paths + now_fn keep it
    testable exactly like SynthesisDaemon.

    chronicle_root is the directory CONTAINING insights/ and
    supersessions.jsonl (load_entries' contract). proposals_dir is where the
    HQ-review JSON lands — it MUST live outside chronicle_root so a write
    there can never perturb chronicle bytes.
    """

    chronicle_root: Path = field(default_factory=lambda: Path.home() / ".sovereign" / "chronicle")
    proposals_dir: Path = field(
        default_factory=lambda: Path.home() / ".sovereign" / "prune_proposals"
    )
    similarity_threshold: float = PRUNE_SIMILARITY_THRESHOLD
    retention_days: int = PRUNE_RETENTION_DAYS
    intensity_ceiling: float = PRUNE_INTENSITY_CEILING
    min_domain_overlap: int = 1
    max_candidates_per_type: int = PRUNE_MAX_CANDIDATES_PER_TYPE
    dry_run: bool = True
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def run(self) -> PruneRunResult:
        """
        One propose-only pass. Fully fail-soft: any unexpected error returns
        OUTCOME_PRUNE_ERROR, never raises, and never mutates the chronicle
        (the pass has no code path that writes inside chronicle_root).
        """
        # Kill switch first — before any read.
        if _prune_env_disabled():
            return PruneRunResult(
                outcome=OUTCOME_PRUNE_DISABLED,
                details=f"{ENV_PRUNE_ENABLED}=off — prune pass bypassed, nothing scanned or written.",
                dry_run=self.dry_run,
            )

        try:
            root = Path(self.chronicle_root)
            if not (root / "insights").exists():
                return PruneRunResult(
                    outcome=OUTCOME_PRUNE_NO_CHRONICLE,
                    details=f"No insights/ under {root} — nothing to scan.",
                    dry_run=self.dry_run,
                )

            # Canonical supersession-safe read chokepoint.
            entries = load_entries(root, with_sources=True)
            sup_fold = provenance.fold_supersessions(
                provenance.load_supersessions(root / "supersessions.jsonl")
            )
            now = self.now_fn()

            near_dups, proposed_pred_ids = detect_near_duplicates(
                entries,
                sup_fold,
                similarity_threshold=self.similarity_threshold,
                min_domain_overlap=self.min_domain_overlap,
                max_candidates=self.max_candidates_per_type,
            )
            superseded = detect_superseded_still_surfacing(
                entries, skip_ids=proposed_pred_ids
            )
            stale = detect_stale_low_intensity(
                entries,
                now,
                retention_days=self.retention_days,
                intensity_ceiling=self.intensity_ceiling,
                skip_ids=proposed_pred_ids,
                max_candidates=self.max_candidates_per_type,
            )

            candidates = near_dups + superseded + stale
            proposal = self._build_proposal(
                now=now,
                root=root,
                entry_count=len(entries),
                near_dups=near_dups,
                superseded=superseded,
                stale=stale,
            )
            proposal_path = self._write_proposal(proposal, now)

            outcome = OUTCOME_PRUNE_PROPOSED if candidates else OUTCOME_PRUNE_NO_CANDIDATES
            return PruneRunResult(
                outcome=outcome,
                details=(
                    f"Scanned {len(entries)} entries; proposed "
                    f"{len(near_dups)} dedupe, {len(superseded)} superseded-audit, "
                    f"{len(stale)} stale-retire candidates -> {proposal_path}. "
                    "PROPOSE-ONLY: zero chronicle mutations."
                ),
                proposal_path=str(proposal_path),
                entry_count=len(entries),
                near_duplicate=len(near_dups),
                superseded_still_surfacing=len(superseded),
                stale_low_intensity=len(stale),
                total_candidates=len(candidates),
                dry_run=self.dry_run,
            )
        except Exception as exc:  # fail-soft — never crash the 04:07 firing
            return PruneRunResult(
                outcome=OUTCOME_PRUNE_ERROR,
                details=f"fail-soft: {type(exc).__name__}: {exc}",
                dry_run=self.dry_run,
            )

    def _build_proposal(
        self,
        *,
        now: datetime,
        root: Path,
        entry_count: int,
        near_dups: list[dict],
        superseded: list[dict],
        stale: list[dict],
    ) -> dict:
        executor_note = (
            "PHASE 1 IS PROPOSE-ONLY. No chronicle entry was created, edited, "
            "moved, or deleted by this pass; the ONLY write is this proposal "
            "file, which lives outside the chronicle tree. HQ/human reviews and "
            "executes: near_duplicate -> supersede_insight(predecessor_id, "
            "successor_id, carry_forward_summary); stale_low_intensity / "
            "superseded_still_surfacing -> rail-retention marking (entry stays "
            "on disk). The chronicle NEVER deletes — corrections supersede, "
            "never erase."
        )
        if not self.dry_run:
            executor_note += (
                " NOTE: dry_run=False was requested, but phase 1 has NO executor "
                "code path — the pass remains propose-only regardless."
            )
        return {
            "schema_version": PRUNE_PROPOSAL_SCHEMA_VERSION,
            "kind": "nrem_prune_proposal",
            "phase": 1,
            "generated_at": now.isoformat(),
            "generated_by": "daemon.metabolize_prune",
            "dry_run": self.dry_run,
            "chronicle_root": str(root),
            "entry_count": entry_count,
            "thresholds": {
                "similarity_threshold": self.similarity_threshold,
                "retention_days": self.retention_days,
                "intensity_ceiling": self.intensity_ceiling,
                "min_domain_overlap": self.min_domain_overlap,
            },
            "summary": {
                "near_duplicate": len(near_dups),
                "superseded_still_surfacing": len(superseded),
                "stale_low_intensity": len(stale),
                "total": len(near_dups) + len(superseded) + len(stale),
            },
            "candidates": near_dups + superseded + stale,
            "note": executor_note,
        }

    def _write_proposal(self, proposal: dict, now: datetime) -> Path:
        """
        Write the proposal JSON to proposals_dir. Timestamped filename so a
        second run the same night never clobbers the first. This is the ONLY
        write the prune pass performs, and it is outside the chronicle tree.
        """
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
        path = self.proposals_dir / f"nrem_prune_{stamp}.json"
        path.write_text(json.dumps(proposal, indent=2, ensure_ascii=False), encoding="utf-8")
        return path


def _prune_root_from_env() -> Path:
    """Honor SOVEREIGN_ROOT (set by the plist), default ~/.sovereign."""
    return Path(os.environ.get("SOVEREIGN_ROOT", str(Path.home() / ".sovereign")))


def build_prune_pass() -> MetabolizePrunePass:
    """Production wiring: env-driven paths + tunables, dry_run default TRUE."""
    root = _prune_root_from_env()
    return MetabolizePrunePass(
        chronicle_root=root / "chronicle",
        proposals_dir=root / "prune_proposals",
        similarity_threshold=_safe_float(
            os.environ.get(ENV_PRUNE_SIMILARITY), PRUNE_SIMILARITY_THRESHOLD
        ),
        retention_days=int(
            _safe_float(os.environ.get(ENV_PRUNE_RETENTION_DAYS), PRUNE_RETENTION_DAYS)
        ),
        intensity_ceiling=_safe_float(
            os.environ.get(ENV_PRUNE_INTENSITY_CEILING), PRUNE_INTENSITY_CEILING
        ),
        dry_run=_prune_dry_run_default(),
    )


def main() -> int:
    """
    Manual / launchd entry for the NREM prune pass:
        python -m sovereign_stack.daemons.metabolize_daemon [--run]

    Own main() (like dream_daemon / synthesis_daemon) — does NOT touch the
    shared entrypoint.py or the still-halted `entrypoint metabolize` digest
    daemon. Prints one structured JSON line for launchd logs. Exit 0 on any
    designed outcome (including prune_disabled / error — fail-soft is not a
    launchd-level failure); the outcome field carries the real status.
    """
    pruner = build_prune_pass()
    result = pruner.run()
    print(
        json.dumps(
            {
                "daemon": "metabolize_prune",
                "outcome": result.outcome,
                "details": result.details,
                "proposal_path": result.proposal_path,
                "entry_count": result.entry_count,
                "near_duplicate": result.near_duplicate,
                "superseded_still_surfacing": result.superseded_still_surfacing,
                "stale_low_intensity": result.stale_low_intensity,
                "total_candidates": result.total_candidates,
                "dry_run": result.dry_run,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
