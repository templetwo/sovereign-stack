"""ScribeSession lifecycle: creation, turn-tracking, TTL, archive.

Per SCRIBE_SPEC.md: each session is per-arriving-instance, per-boot.
Sessions live in memory inside the bridge process, archive to disk on
close or TTL expiry, and never write to chronicle except via the
encounter-note path (encounter.py).

Phase 0: no LLM client yet. This module owns metadata, turn history,
TTL, and archive. Phase 1 wires haiku_client.py to consume the
conversation history and produce responses.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCRIBE_ATTRIBUTION = "scribe-haiku-4-5"
DEFAULT_TTL_MINUTES = 240
ARCHIVE_ROOT = (
    Path(os.environ.get("SOVEREIGN_ROOT", str(Path.home() / ".sovereign"))) / "scribe_threads"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session_id() -> str:
    """Generate a scribe session_id of shape
    scribe_<YYYYMMDD>_<HHMMSS>_<8-char-hash>."""
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(4)  # 8 hex chars
    return f"scribe_{stamp}_{rand}"


@dataclass
class ScribeTurn:
    """One turn in the scribe conversation thread."""

    timestamp: str
    role: str  # "user" | "assistant"
    message: str
    redaction_counts: dict[str, int] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass
class ScribeSession:
    """One scribe session, per arriving-instance boot.

    Holds metadata + conversation thread. Pure data + light methods;
    archival and LLM interaction live elsewhere.
    """

    session_id: str
    parent_instance: str | None
    boot_context_summary: str  # short hint of what was in the boot, not the full thing
    created_at: str
    last_message_at: str
    ttl_minutes: int
    attribution: str = SCRIBE_ATTRIBUTION
    turns: list[ScribeTurn] = field(default_factory=list)
    closed: bool = False
    archived_at: str | None = None
    # Full chronicle context (typically the joined boot ritual text) — sent
    # to Haiku as a cache-controlled system block so multi-turn sessions
    # reuse it cheaply. Stored on the session so ask_scribe turns can pass
    # the same context the boot used.
    chronicle_context: str = ""

    # ----- Lifecycle ---------------------------------------------------

    @classmethod
    def create(
        cls,
        parent_instance: str | None = None,
        boot_context_summary: str = "",
        ttl_minutes: int = DEFAULT_TTL_MINUTES,
        chronicle_context: str = "",
    ) -> ScribeSession:
        now = _now_iso()
        return cls(
            session_id=_new_session_id(),
            parent_instance=parent_instance,
            boot_context_summary=boot_context_summary,
            created_at=now,
            last_message_at=now,
            ttl_minutes=ttl_minutes,
            chronicle_context=chronicle_context,
        )

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def expires_at_unix(self) -> float:
        """Unix timestamp of expiry, based on last message + TTL."""
        last = datetime.fromisoformat(self.last_message_at)
        return last.timestamp() + (self.ttl_minutes * 60)

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at_unix

    @property
    def total_cost_usd(self) -> float:
        return sum(t.cost_usd for t in self.turns)

    @property
    def total_tokens_in(self) -> int:
        return sum(t.tokens_in for t in self.turns)

    @property
    def total_tokens_out(self) -> int:
        return sum(t.tokens_out for t in self.turns)

    # ----- Turn append --------------------------------------------------

    def append_user_turn(
        self, message: str, redaction_counts: dict[str, int] | None = None
    ) -> ScribeTurn:
        turn = ScribeTurn(
            timestamp=_now_iso(),
            role="user",
            message=message,
            redaction_counts=redaction_counts or {},
        )
        self.turns.append(turn)
        self.last_message_at = turn.timestamp
        return turn

    def append_assistant_turn(
        self,
        message: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> ScribeTurn:
        turn = ScribeTurn(
            timestamp=_now_iso(),
            role="assistant",
            message=message,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )
        self.turns.append(turn)
        self.last_message_at = turn.timestamp
        return turn

    # ----- Serialization ------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def handle_payload(self) -> dict:
        """The JSON handle handed to the arriving instance in the boot."""
        return {
            "session_id": self.session_id,
            "endpoint": "/api/call ask_scribe",
            "ttl_minutes": self.ttl_minutes,
        }


# ----------------------------------------------------------------------
# In-memory session store with TTL eviction
# ----------------------------------------------------------------------


class ScribeSessionStore:
    """Thread-safe in-memory registry of active scribe sessions.

    The bridge holds one store for the lifetime of the process. Sessions
    register on creation, look up by session_id, evict on TTL expiry or
    explicit close. Eviction archives the session to disk.
    """

    def __init__(self, archive_root: Path = ARCHIVE_ROOT):
        self._sessions: dict[str, ScribeSession] = {}
        self._lock = threading.RLock()
        self._archive_root = archive_root

    def register(self, session: ScribeSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get(self, session_id: str) -> ScribeSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.expired and not session.closed:
                # TTL expired silently; evict + archive.
                self._evict_locked(session_id, reason="ttl_expired")
                return None
            return session

    def close(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            self._evict_locked(session_id, reason="explicit_close")
            return True

    def sweep(self) -> int:
        """Evict all expired sessions. Returns count of evicted sessions."""
        evicted = 0
        with self._lock:
            for session_id in list(self._sessions.keys()):
                session = self._sessions[session_id]
                if session.expired:
                    self._evict_locked(session_id, reason="sweep_ttl_expired")
                    evicted += 1
        return evicted

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def active_sessions(self) -> Iterator[ScribeSession]:
        with self._lock:
            return iter(list(self._sessions.values()))

    # ----- Eviction / archive ------------------------------------------

    def _evict_locked(self, session_id: str, reason: str) -> None:
        """Must hold self._lock."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        session.closed = True
        session.archived_at = _now_iso()
        # Archive failure should not crash the bridge; log and move on.
        # In Phase 1 we wire this to the dashboard halt-alert.
        with contextlib.suppress(OSError):
            archive_session(session, self._archive_root, eviction_reason=reason)


# ----------------------------------------------------------------------
# Archive
# ----------------------------------------------------------------------


def archive_session(
    session: ScribeSession,
    archive_root: Path = ARCHIVE_ROOT,
    eviction_reason: str = "unknown",
) -> Path:
    """Write the scribe session as a JSONL file under
    archive_root/<YYYY-MM-DD>/<session_id>.jsonl.

    Each line is a JSON object: the session header on line 1, then one
    line per turn.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dir_path = archive_root / today
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{session.session_id}.jsonl"

    header = {
        "type": "header",
        "session_id": session.session_id,
        "parent_instance": session.parent_instance,
        "boot_context_summary": session.boot_context_summary,
        "created_at": session.created_at,
        "last_message_at": session.last_message_at,
        "ttl_minutes": session.ttl_minutes,
        "attribution": session.attribution,
        "turn_count": session.turn_count,
        "total_cost_usd": session.total_cost_usd,
        "total_tokens_in": session.total_tokens_in,
        "total_tokens_out": session.total_tokens_out,
        "eviction_reason": eviction_reason,
        "archived_at": session.archived_at,
    }

    with open(file_path, "w") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for turn in session.turns:
            f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")

    return file_path
