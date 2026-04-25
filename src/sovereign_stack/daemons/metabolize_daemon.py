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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..grounding import GroundingResult, grounded_extract
from .base import (
    BaseDaemon,
    COMPASS_PAUSE,
    COMPASS_PROCEED,
    COMPASS_WITNESS,
    CONSECUTIVE_UNACKED_THRESHOLD,
    DEFAULT_CHANNEL,
    DaemonState,
    OUTCOME_ALREADY_HALTED,
    OUTCOME_DRY_RUN,
    OUTCOME_GROUNDING_FAILED,
    OUTCOME_HALTED,
    OUTCOME_PAUSED,
    OUTCOME_POSTED,
    POSTED_DIGESTS_RETAINED,
    STATE_SCHEMA_VERSION,
)
from .senders import SENDER_METABOLIZE


# ── Daemon-specific tunables ──

MAX_DIGEST_ITEMS_PER_CATEGORY = 5
DEFAULT_TTL_DAYS = 14    # longer than uncertainty (3-day cadence surfaced
                          # more often) — nightly digests need more
                          # breathing room before TTL.


# ── Daemon-specific outcome codes ──

OUTCOME_NO_FINDINGS = "no_findings"
OUTCOME_NO_CHANGES = "no_changes"


@dataclass
class RunResult:
    outcome: str
    details: str = ""
    posted_message_id: Optional[str] = None
    halt_path: Optional[str] = None
    decision_path: Optional[str] = None
    compass_decision: Optional[str] = None
    grounding_reason: Optional[str] = None
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


def _contradiction_key(c: Dict) -> str:
    return _fingerprint(
        "contradiction",
        str(c.get("hypothesis_domain", "")),
        str(c.get("hypothesis_timestamp", "")),
        str(c.get("ground_truth_domain", "")),
        str(c.get("ground_truth_timestamp", "")),
    )


def _stale_thread_key(t: Dict) -> str:
    return _fingerprint(
        "stale_thread",
        str(t.get("domain", "")),
        str(t.get("question", ""))[:120],
    )


def _stale_hypothesis_key(h: Dict) -> str:
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
        evidence_paths: List[Path],
        compass_fn: Callable[..., Dict],
        detect_fn: Callable[[], Dict],
        comms_post_fn: Callable[..., Dict],
        comms_get_acks_fn: Callable[[str], List[Dict]],
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

    def _halt_what_tried(self) -> List[str]:
        return [
            "Post nightly metabolism digests surfacing new contradictions,",
            "stale threads, and aging hypotheses to prompt chronicle integration.",
        ]

    def _halt_blocked_downstream(self) -> List[str]:
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

        compass = self._compass_fn(
            action="metabolize_nightly",
            stakes="medium",
        ) or {}
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
            c for c in contradictions
            if _contradiction_key(c) not in prior_fingerprints
        ]
        stale_threads_new = [
            t for t in stale_threads
            if _stale_thread_key(t) not in prior_fingerprints
        ]
        stale_hypotheses_new = [
            h for h in stale_hypotheses
            if _stale_hypothesis_key(h) not in prior_fingerprints
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
        contradictions_new = contradictions_new[:self.max_items_per_category]
        stale_threads_new = stale_threads_new[:self.max_items_per_category]
        stale_hypotheses_new = stale_hypotheses_new[:self.max_items_per_category]

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
        contradictions: List[Dict],
        stale_threads: List[Dict],
        stale_hypotheses: List[Dict],
        stats: Dict,
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

        lines.extend([
            f"Acknowledge with comms_acknowledge(message_id=\"{message_id}\", "
            "instance_id=<your id>, note=<what was integrated>).",
            "",
            f"Full decision note alongside this post; see decision_path field.",
            "",
            f"{self.unacked_threshold} consecutive unacked digests "
            "triggers daemon halt.",
        ])
        return "\n".join(lines)

    # ── Decision-note write ──

    def _write_decision(
        self,
        *,
        contradictions: List[Dict],
        stale_threads: List[Dict],
        stale_hypotheses: List[Dict],
        stats: Dict,
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
            f"Acknowledge via: `comms_acknowledge(message_id=\"{message_id}\", instance_id=..., note=...)`",
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
                lines.extend([
                    "",
                    f"### {i}. [{c.get('hypothesis_domain', '?')}] vs "
                    f"[{c.get('ground_truth_domain', '?')}]",
                    f"- Overlap score: {c.get('overlap_score', '?')}",
                    f"- Hypothesis (timestamp {c.get('hypothesis_timestamp', '?')}):",
                    f"  > {c.get('hypothesis_preview', '')}",
                    f"- Ground truth (timestamp {c.get('ground_truth_timestamp', '?')}):",
                    f"  > {c.get('ground_truth_preview', '')}",
                ])
            lines.append("")

        if stale_threads:
            lines.append(f"## Stale threads ({len(stale_threads)} new)")
            for i, t in enumerate(stale_threads, start=1):
                lines.extend([
                    "",
                    f"### {i}. [{t.get('domain', '?')}] {t.get('age_days', '?')}d old",
                    f"- Timestamp: {t.get('timestamp', '?')}",
                    f"- Question: {t.get('question', '?')}",
                ])
            lines.append("")

        if stale_hypotheses:
            lines.append(f"## Aging hypotheses ({len(stale_hypotheses)} new)")
            for i, h in enumerate(stale_hypotheses, start=1):
                lines.extend([
                    "",
                    f"### {i}. [{h.get('domain', '?')}] {h.get('age_days', '?')}d old",
                    f"- Content: {h.get('content', '?')}",
                ])
            lines.append("")

        lines.extend([
            "## How to act on this",
            "- For each contradiction: either retire the hypothesis "
            "(`retire_hypothesis`) or update the ground_truth.",
            "- For each stale thread: resolve it (`resolve_thread_by_id`) "
            "or accept it as long-running and touch it (`thread_touch`).",
            "- For each aging hypothesis: promote, retire, or leave to age "
            "further — hypotheses don't expire automatically.",
            "",
        ])
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
