"""
UncertaintyResurfacer — the first scheduled reflection daemon (Step 3).

Every 3 days (via launchd), pulls the top 3 oldest unresolved uncertainties
and posts a digest to comms with ack_required=True. Three consecutive
unacked digests trigger a halt + alert.

After Step 4 (metabolize) shipped, the shared scaffolding lifted to
BaseDaemon: DaemonState, _load_state, _save_state, _count_recent_unacked,
_record_post, _perform_halt, _post_halt_alert. What remains here is the
daemon-specific work — uncertainty fetching, templated digest formatting,
and the per-daemon RunResult shape.

Daemon-specific design calls (load-bearing rationale; do not re-litigate):

  * Three-strikes threshold. 3 consecutive unacked ≈ 9 calendar days for a
    3-day cadence — long enough to be a real ack-hygiene signal, short
    enough that Anthony's weekly decisions/ review catches the halt.

  * Templated digest, no LLM call. The digest is a notification primitive,
    not an essay. Template output has zero hallucination surface and is
    fully auditable. Future natural-language passes belong behind a
    separate grounded_extract gate, not woven into this daemon's loop.

  * grounded_extract called even though there's no LLM output to verify.
    The invariant "every daemon calls grounded_extract before comms post"
    must hold uniformly — Step 5+ will rely on it.

  * Circuit breaker checks acks, not read_by. This is the load-bearing
    contract. The v1.3.1 acknowledgment split is the architectural reason
    halt-on-unack can work. Reading the digest does NOT advance the ack
    count. Only a deliberate comms_acknowledge does. The corresponding
    test (TestAckDistinctFromReadBy) is the canary for any refactor —
    including the BaseDaemon lift itself.
"""

from __future__ import annotations

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
from .senders import SENDER_UNCERTAINTY


# ── Daemon-specific tunables ──

MAX_DIGEST_UNCERTAINTIES = 3         # no flooding — top-3 oldest
DEFAULT_TTL_DAYS = 7


# ── Daemon-specific outcome codes ──

OUTCOME_NO_UNCERTAINTIES = "no_uncertainties"


@dataclass
class RunResult:
    outcome: str
    details: str = ""
    posted_message_id: Optional[str] = None
    halt_path: Optional[str] = None
    compass_decision: Optional[str] = None
    grounding_reason: Optional[str] = None
    uncertainties_included: int = 0


class UncertaintyResurfacer(BaseDaemon):
    """
    Scheduled daemon: surface aging unresolved uncertainties into comms,
    halt on repeated non-engagement.

    Inherits from BaseDaemon — see base.py for state schema, halt-write
    contract, ack-counting, and circuit-breaker semantics.

    Daemon-specific injected callables:
        uncertainty_fn() -> list[dict]
            Returns unresolved uncertainty records. Real wiring:
            meta.uncertainty_log.get_unresolved().

    Daemon-specific paths:
        uncertainty_log_path: Path passed to grounded_extract as evidence.
            Typically ~/.sovereign/consciousness/uncertainty_log.json.
    """

    SENDER = SENDER_UNCERTAINTY
    HALT_FILENAME_TAG = "uncertainty"
    HALT_SOURCE = "uncertainty_resurfacer"
    DAEMON_LABEL = "daemon.uncertainty"

    def __init__(
        self,
        *,
        state_path: Path,
        halt_dir: Path,
        uncertainty_log_path: Path,
        compass_fn: Callable[..., Dict],
        uncertainty_fn: Callable[[], List[Dict]],
        comms_post_fn: Callable[..., Dict],
        comms_get_acks_fn: Callable[[str], List[Dict]],
        grounding_fn: Callable[..., GroundingResult] = grounded_extract,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_fn=None,
        unacked_threshold: int = CONSECUTIVE_UNACKED_THRESHOLD,
        max_digest_uncertainties: int = MAX_DIGEST_UNCERTAINTIES,
        ttl_days: int = DEFAULT_TTL_DAYS,
        channel: str = DEFAULT_CHANNEL,
    ):
        # id_fn=None means "use the base default" (uuid4). Callers passing
        # an explicit factory get it forwarded; tests rely on this.
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

        self.uncertainty_log_path = Path(uncertainty_log_path)
        self._uncertainty_fn = uncertainty_fn
        self.max_digest_uncertainties = int(max_digest_uncertainties)
        self.ttl_days = int(ttl_days)

    # ── Halt-body hooks ──

    def _halt_what_tried(self) -> List[str]:
        return [
            "Post uncertainty digests to prompt integration of unresolved",
            "questions into the chronicle / uncertainty log.",
        ]

    def _halt_blocked_downstream(self) -> List[str]:
        return [
            "- Further uncertainty digests paused until manual reset.",
            "- Step 4 (metabolize) should NOT consume uncertainty_log for",
            "  consolidation until this halt is cleared, because any pattern",
            "  it detects there may reflect ack-hygiene drift, not real",
            "  cognitive state.",
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

        # Circuit breaker BEFORE detection — don't do work if we're about
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
                    f"posted uncertainty digests were not acknowledged by any "
                    f"instance within the observation window."
                ),
            )
            return RunResult(
                outcome=OUTCOME_HALTED,
                details=f"Halt written to {halt_path}.",
                halt_path=str(halt_path),
            )

        # Compass check.
        compass = self._compass_fn(
            action="uncertainty_resurface",
            stakes="medium",
        ) or {}
        decision = compass.get("decision", COMPASS_PROCEED)
        if decision == COMPASS_PAUSE:
            return RunResult(
                outcome=OUTCOME_PAUSED,
                details=f"compass_check returned PAUSE: {compass.get('rationale', '')}",
                compass_decision=COMPASS_PAUSE,
            )

        # Pull uncertainties.
        uncertainties = self._uncertainty_fn() or []
        if not uncertainties:
            return RunResult(
                outcome=OUTCOME_NO_UNCERTAINTIES,
                details="No unresolved uncertainties to surface.",
                compass_decision=decision,
            )

        # Oldest first; cap.
        uncertainties_sorted = sorted(
            uncertainties,
            key=lambda u: str(u.get("timestamp", "")),
        )[:self.max_digest_uncertainties]

        # Grounding gate.
        grounding = self._grounding_fn(
            claim="uncertainty resurfacer posting scheduled digest",
            evidence_paths=[str(self.uncertainty_log_path)],
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

        # Format and post.
        message_id = self._new_id()
        now = self._now()
        content = self._format_digest(uncertainties_sorted, message_id, now)

        if dry_run:
            return RunResult(
                outcome=OUTCOME_DRY_RUN,
                details=(
                    f"Would post {len(uncertainties_sorted)} uncertainties "
                    f"as message_id={message_id}."
                ),
                posted_message_id=message_id,
                compass_decision=decision,
                grounding_reason=grounding.reason,
                uncertainties_included=len(uncertainties_sorted),
            )

        self._comms_post_fn(
            sender=SENDER_UNCERTAINTY,
            content=content,
            channel=self.channel,
            message_id=message_id,
            extra_fields={
                "ack_required": True,
                "ttl_days": self.ttl_days,
                "daemon": "uncertainty_resurfacer",
            },
        )

        self._record_post(
            state,
            message_id=message_id,
            content=content,
            now=now,
        )

        return RunResult(
            outcome=OUTCOME_POSTED,
            details=f"Posted {len(uncertainties_sorted)} uncertainties.",
            posted_message_id=message_id,
            compass_decision=decision,
            grounding_reason=grounding.reason,
            uncertainties_included=len(uncertainties_sorted),
        )

    # ── Digest formatting (templated; no LLM call) ──

    def _format_digest(
        self,
        uncertainties: List[Dict],
        message_id: str,
        now: datetime,
    ) -> str:
        lines = [
            "Three oldest unresolved uncertainties",
            f"(posted {now.date().isoformat()} by {SENDER_UNCERTAINTY})",
            "",
        ]
        for i, unc in enumerate(uncertainties, start=1):
            what = str(unc.get("what", "")).strip() or "(no description)"
            why = str(unc.get("why", "")).strip()
            help_items = unc.get("what_would_help", []) or []
            confidence = unc.get("confidence")
            age_days = self._age_days(unc.get("timestamp"), now)

            confidence_str = ""
            if isinstance(confidence, (int, float)):
                confidence_str = f" | confidence {float(confidence):.2f}"

            lines.append(f"{i}. [{age_days}d{confidence_str}] {what}")
            if why:
                lines.append(f"   Why: {why}")
            if help_items:
                lines.append(f"   Would help: {'; '.join(help_items)}")
            lines.append("")

        lines.extend([
            f"Acknowledge with comms_acknowledge(message_id=\"{message_id}\", "
            "instance_id=<your id>, note=<what was integrated>).",
            "",
            f"{self.unacked_threshold} consecutive unacked digests "
            "triggers daemon halt.",
        ])
        return "\n".join(lines)

    @staticmethod
    def _age_days(ts: Optional[str], now: datetime) -> int:
        if not ts:
            return 0
        try:
            parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            return 0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = now - parsed
        return max(0, delta.days)
