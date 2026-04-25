"""
BaseDaemon — shared bookkeeping for v1.3.2 reflection daemons.

Lifted after Steps 3 + 4 shipped (uncertainty_resurfacer, metabolize_daemon).
Two concrete daemons made the join points legible; the shape was earned, not
guessed. YAGNI no longer applies.

What lives here (truly identical across daemons):
  * DaemonState — schema_version, posted_digests, halted_at, halt_reason,
    with future-version-refusal in from_dict.
  * Constants: COMPASS_*, OUTCOME_* (shared subset), CONSECUTIVE_UNACKED_
    THRESHOLD, POSTED_DIGESTS_RETAINED, STATE_SCHEMA_VERSION.
  * BaseDaemon class with helpers:
      - _load_state / _save_state
      - _count_recent_unacked
      - _record_post (append + trim + save)
      - _perform_halt (write halt note + mark state + post halt alert)

What stays in subclasses (genuinely daemon-specific):
  * RunResult dataclass — different counter field names (uncertainties_
    included vs contradictions_included etc.) are part of each daemon's
    public contract; unifying would break test API without earning much.
  * run() — orchestration of helpers + daemon-specific work (detection,
    digest formatting, decision-file writing).
  * SENDER, HALT_FILENAME_TAG, HALT_SOURCE, DAEMON_LABEL — ClassVars the
    base reads when constructing halt artifacts.
  * _halt_what_tried, _halt_blocked_downstream — hook methods returning
    body lines for the halt note. Each daemon describes its own work
    and downstream impact in human language.

Why Template Method (one abstract run()) was rejected: it would require
either (a) a unified RunResult that drops daemon-specific counter names,
breaking test API, or (b) an abstract _make_result factory in every
subclass, which is more boilerplate than the run() it would replace.
Helpers + per-subclass run() is the lower-friction split.

The single load-bearing test for this lift is TestAckDistinctFromReadBy
in both subclass test files — if circuit-breaker semantics drift through
the refactor, that test fails and the lift is rejected.
"""

from __future__ import annotations

import abc
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, ClassVar, Dict, List, Optional

from ..grounding import GroundingResult, grounded_extract
from .senders import SENDER_HALT_ALERT


# ── State schema ────────────────────────────────────────────────────────────

STATE_SCHEMA_VERSION = 1


@dataclass
class DaemonState:
    """
    Persistent state shared across all v1.3.2 daemons.

    posted_digests entries are dicts with at least:
        {"message_id": str, "posted_at": iso, "content_snippet": str}
    Daemon-specific extras (e.g. fingerprints, decision_path) are stored
    alongside without schema changes — readers tolerate unknown keys.
    """
    schema_version: int = STATE_SCHEMA_VERSION
    posted_digests: List[Dict] = field(default_factory=list)
    halted_at: Optional[str] = None
    halt_reason: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "schema_version": self.schema_version,
            "posted_digests": self.posted_digests,
            "halted_at": self.halted_at,
            "halt_reason": self.halt_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "DaemonState":
        version = int(data.get("schema_version", 1))
        if version > STATE_SCHEMA_VERSION:
            # Refuse rather than silently downgrade — better to halt the
            # daemon than to corrupt a state file written by a newer
            # version of the code.
            raise ValueError(
                f"State schema_version={version} is newer than this "
                f"daemon's {STATE_SCHEMA_VERSION}. Upgrade the daemon "
                "or move the state file aside."
            )
        return cls(
            schema_version=STATE_SCHEMA_VERSION,
            posted_digests=list(data.get("posted_digests", [])),
            halted_at=data.get("halted_at"),
            halt_reason=data.get("halt_reason"),
        )


# ── Shared tunables ─────────────────────────────────────────────────────────

CONSECUTIVE_UNACKED_THRESHOLD = 3
POSTED_DIGESTS_RETAINED = 5
DEFAULT_CHANNEL = "general"


# ── Compass decision constants ──────────────────────────────────────────────

COMPASS_PAUSE = "PAUSE"
COMPASS_WITNESS = "WITNESS"
COMPASS_PROCEED = "PROCEED"


# ── Shared outcome codes (subset; daemons add their own) ────────────────────

OUTCOME_POSTED = "posted"
OUTCOME_PAUSED = "paused"
OUTCOME_HALTED = "halted"
OUTCOME_ALREADY_HALTED = "already_halted"
OUTCOME_GROUNDING_FAILED = "grounding_failed"
OUTCOME_DRY_RUN = "dry_run"


# ── BaseDaemon ──────────────────────────────────────────────────────────────


class BaseDaemon(abc.ABC):
    """
    Shared scaffolding for a scheduled reflection daemon.

    Subclasses MUST set:
        SENDER:             daemon.* sender constant for routine posts
        HALT_FILENAME_TAG:  short slug for halt note filenames
                            (e.g. "uncertainty", "metabolize")
        HALT_SOURCE:        identifier for halt-alert extra_fields
                            (typically same as HALT_FILENAME_TAG)
        DAEMON_LABEL:       human-readable label used in halt-alert text
                            (e.g. "daemon.uncertainty", "daemon.metabolize")

    Subclasses MUST implement:
        run(*, dry_run=False) -> RunResult
            Orchestrates: _load_state → already_halted check → circuit
            breaker check → compass check → daemon-specific detection +
            grounding + post → _record_post.

        _halt_what_tried() -> List[str]
            Body lines for the halt note's "What the daemon tried to do"
            section. Plain prose, no markdown headers — base writes the
            ## header.

        _halt_blocked_downstream() -> List[str]
            Body lines for the halt note's "What's blocked downstream"
            section. Bulleted preferred ("- ..."); base passes through.

    Subclasses TYPICALLY override:
        _halt_alert_content(state, halt_path) -> str
            Halt-alert message text. Default uses DAEMON_LABEL — override
            only if the alert needs daemon-specific framing.
    """

    SENDER: ClassVar[str]
    HALT_FILENAME_TAG: ClassVar[str]
    HALT_SOURCE: ClassVar[str]
    DAEMON_LABEL: ClassVar[str]

    def __init__(
        self,
        *,
        state_path: Path,
        halt_dir: Path,
        compass_fn: Callable[..., Dict],
        comms_post_fn: Callable[..., Dict],
        comms_get_acks_fn: Callable[[str], List[Dict]],
        grounding_fn: Callable[..., GroundingResult] = grounded_extract,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_fn: Callable[[], str] = lambda: str(uuid.uuid4()),
        unacked_threshold: int = CONSECUTIVE_UNACKED_THRESHOLD,
        posted_digests_retained: int = POSTED_DIGESTS_RETAINED,
        channel: str = DEFAULT_CHANNEL,
    ):
        self.state_path = Path(state_path)
        self.halt_dir = Path(halt_dir)
        self._compass_fn = compass_fn
        self._comms_post_fn = comms_post_fn
        self._comms_get_acks_fn = comms_get_acks_fn
        self._grounding_fn = grounding_fn
        self._now = now_fn
        self._new_id = id_fn
        self.unacked_threshold = int(unacked_threshold)
        self.posted_digests_retained = int(posted_digests_retained)
        self.channel = channel

    # ── Public abstract entry ──

    @abc.abstractmethod
    def run(self, *, dry_run: bool = False):
        """
        Execute one pass of the daemon. Returns a daemon-specific
        RunResult dataclass with at minimum a stable `outcome` field.
        """

    # ── Halt-body hooks (subclass overrides) ──

    @abc.abstractmethod
    def _halt_what_tried(self) -> List[str]:
        """Body lines for the halt note's 'What the daemon tried to do' section."""

    @abc.abstractmethod
    def _halt_blocked_downstream(self) -> List[str]:
        """Body lines for the halt note's 'What's blocked downstream' section."""

    def _halt_alert_content(self, *, state: DaemonState, halt_path: Path) -> str:
        """
        Halt-alert post content. Default works for all current daemons;
        override only if the alert needs daemon-specific framing.
        """
        return (
            f"{self.DAEMON_LABEL} halted at {state.halted_at}.\n"
            f"Reason: {state.halt_reason}.\n"
            f"Halt note: {halt_path}.\n"
            f"{self.unacked_threshold} consecutive unacked digests. "
            "Review and ack to resume."
        )

    # ── Ack counting ──

    def _count_recent_unacked(self, state: DaemonState) -> int:
        """
        Count how many of the last `unacked_threshold` posted digests have
        zero acks. If fewer posts have been made than the threshold,
        returns 0 — the circuit breaker cannot fire before the daemon has
        posted threshold-many times.
        """
        last_n = state.posted_digests[-self.unacked_threshold:]
        if len(last_n) < self.unacked_threshold:
            return 0
        unacked = 0
        for entry in last_n:
            mid = entry.get("message_id")
            if not mid:
                continue
            acks = self._comms_get_acks_fn(mid) or []
            if not acks:
                unacked += 1
        return unacked

    # ── Posting bookkeeping ──

    def _record_post(
        self,
        state: DaemonState,
        *,
        message_id: str,
        content: str,
        now: datetime,
        extra: Optional[Dict] = None,
    ) -> None:
        """
        Append a post record to state.posted_digests, trim to the
        retention cap, and save state. Daemon-specific extras (e.g.
        fingerprints, decision_path) ride along without schema changes.
        """
        entry = {
            "message_id": message_id,
            "posted_at": now.isoformat(),
            "content_snippet": content[:200],
        }
        if extra:
            entry.update(extra)
        state.posted_digests.append(entry)
        state.posted_digests = state.posted_digests[-self.posted_digests_retained:]
        self._save_state(state)

    # ── Halt write-path ──

    def _perform_halt(
        self,
        state: DaemonState,
        *,
        reason: str,
        evidence_note: str,
    ) -> Path:
        """
        Write halt note, mark state.halted_at, save state, post halt alert.
        Returns halt_path.

        The halt note has the four-field contract Claude Desktop specified
        for Step 3 and every daemon since:
          (a) reason code (stable string)
          (b) what the daemon tried to do (subclass _halt_what_tried)
          (c) evidence that triggered the halt (caller supplies note +
              the most recent posted_digests are appended automatically)
          (d) what's blocked downstream (subclass _halt_blocked_downstream)
        Plus a "To resolve" section so the human reviewer knows how to clear it.
        """
        self.halt_dir.mkdir(parents=True, exist_ok=True)
        now = self._now()
        stamp = now.strftime("%Y%m%dT%H%M%S")
        path = self.halt_dir / f"{stamp}_{self.HALT_FILENAME_TAG}_{reason}.md"

        last_n = state.posted_digests[-self.unacked_threshold:]
        evidence_lines = []
        for i, entry in enumerate(last_n, start=1):
            evidence_lines.append(
                f"  {i}. {entry.get('message_id','?')} "
                f"posted {entry.get('posted_at','?')}"
            )
            snippet = entry.get("content_snippet", "").replace("\n", " ")[:200]
            if snippet:
                evidence_lines.append(f"     snippet: {snippet}")

        body_parts = [
            f"# Halt — {self.SENDER}",
            f"Timestamp: {now.isoformat()}",
            f"Reason: {reason}",
            "",
            "## What the daemon tried to do",
            *self._halt_what_tried(),
            "",
            "## Evidence that triggered the halt",
            evidence_note,
            "",
            "Most recent digests involved:",
            *evidence_lines,
            "",
            "## What's blocked downstream",
            *self._halt_blocked_downstream(),
            "",
            "## To resolve",
            "1. Review the digests above and acknowledge the items that",
            "   were actually integrated via",
            "   `comms_acknowledge(message_id=..., instance_id=..., note=...)`.",
            "2. Clear the halt: either delete the daemon state file",
            f"   ({self.state_path}) or set halted_at=None inside it.",
            "3. The daemon will resume on its next scheduled tick.",
            "",
        ]
        path.write_text("\n".join(body_parts), encoding="utf-8")

        # Mark state and save BEFORE alerting — if the alert post fails,
        # the durable record is already on disk.
        state.halted_at = now.isoformat()
        state.halt_reason = reason
        self._save_state(state)

        self._post_halt_alert(state=state, halt_path=path)
        return path

    def _post_halt_alert(self, *, state: DaemonState, halt_path: Path) -> None:
        """
        Surface the halt via comms under SENDER_HALT_ALERT so the human
        reviewer sees it without grepping the filesystem. Best-effort:
        if comms is down, the halt note on disk is the durable record.
        """
        try:
            alert_id = self._new_id()
            content = self._halt_alert_content(state=state, halt_path=halt_path)
            self._comms_post_fn(
                sender=SENDER_HALT_ALERT,
                content=content,
                channel=DEFAULT_CHANNEL,
                message_id=alert_id,
                extra_fields={
                    "ack_required": True,
                    "halt_source": self.HALT_SOURCE,
                    "halt_note_path": str(halt_path),
                },
            )
        except Exception:
            pass

    # ── State persistence ──

    def _load_state(self) -> DaemonState:
        if not self.state_path.exists():
            return DaemonState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return DaemonState()
        if not isinstance(data, dict):
            return DaemonState()
        return DaemonState.from_dict(data)

    def _save_state(self, state: DaemonState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state.to_dict(), indent=2),
            encoding="utf-8",
        )
