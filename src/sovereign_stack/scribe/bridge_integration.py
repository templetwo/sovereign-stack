"""Bridge integration for the scribe.

Phase 1 of SCRIBE_SPEC.md, soft mode:
  - On every where_did_i_leave_off call, spawn a ScribeSession.
  - If the Haiku API key is available, generate a greeting and log it
    to ~/.sovereign/scribe_threads/_logs/<date>/<session>.log.
  - DO NOT inject the SCRIBE block into the boot output yet (that is
    Phase 2 after calibration).
  - Register the ask_scribe tool so claude-code seats can shake it down.

Phase 2 (later) flips a flag and injects the SCRIBE block into the boot
output, and writes encounter notes per session close.

This module centralizes the bridge-side logic so server.py stays
readable. The functions here are sync; async handlers can await them
via asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .encounter import write_encounter_note
from .redactor import redact
from .session import ScribeSession, ScribeSessionStore

logger = logging.getLogger(__name__)

SOVEREIGN_ROOT = Path(os.environ.get("SOVEREIGN_ROOT", str(Path.home() / ".sovereign")))
PHASE1_LOG_ROOT = SOVEREIGN_ROOT / "scribe_threads" / "_logs"


# ----------------------------------------------------------------------
# Singleton session store
# ----------------------------------------------------------------------


_scribe_store = ScribeSessionStore()


def get_store() -> ScribeSessionStore:
    """Module-level singleton store; one per server process."""
    return _scribe_store


# ----------------------------------------------------------------------
# Lazy Haiku client
# ----------------------------------------------------------------------


_client_cache: object = None  # None = not tried, False = tried + failed, else HaikuClient
_client_error: Optional[str] = None


def get_client():
    """Lazy-load HaikuClient. Returns None if init fails (no API key, etc)."""
    global _client_cache, _client_error
    if _client_cache is False:
        return None
    if _client_cache is not None:
        return _client_cache
    try:
        from .haiku_client import HaikuClient

        _client_cache = HaikuClient()
        return _client_cache
    except Exception as exc:
        _client_cache = False
        _client_error = f"{type(exc).__name__}: {exc}"
        logger.warning("scribe: HaikuClient init failed: %s", _client_error)
        return None


def client_status() -> dict:
    """Diagnostic — what state is the lazy client in?"""
    if _client_cache is None:
        return {"state": "not_initialized"}
    if _client_cache is False:
        return {"state": "failed", "error": _client_error}
    return {"state": "ready"}


# ----------------------------------------------------------------------
# Phase 1: boot spawn + greeting + log
# ----------------------------------------------------------------------


def _summarize_boot(boot_text: str) -> str:
    """Compress the full boot ritual into a ~200-char hint stored on the session."""
    head = boot_text.strip().split("\n")[:8]
    joined = " ".join(line.strip() for line in head if line.strip())
    return joined[:200]


def _log_phase1_greeting(session: ScribeSession, greeting_text: str, result_meta: dict) -> Path:
    """Write the Phase 1 greeting log to disk under _logs/<date>/<session>.log."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_dir = PHASE1_LOG_ROOT / today
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{session.session_id}.log"
    payload = {
        "session_id": session.session_id,
        "parent_instance": session.parent_instance,
        "created_at": session.created_at,
        "greeting": greeting_text,
        "meta": result_meta,
    }
    with open(log_path, "w") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2))
    return log_path


def boot_spawn(
    parent_instance: Optional[str],
    boot_text: str,
    ttl_minutes: int = 240,
) -> ScribeSession:
    """Spawn a ScribeSession at boot time. Always succeeds — does not
    attempt the Haiku call. Use greet_session() separately to call Haiku."""
    summary = _summarize_boot(boot_text)
    session = ScribeSession.create(
        parent_instance=parent_instance,
        boot_context_summary=summary,
        ttl_minutes=ttl_minutes,
        chronicle_context=boot_text,
    )
    _scribe_store.register(session)
    return session


def greet_session(session: ScribeSession) -> Optional[str]:
    """Run the Haiku greeting on this session. Best-effort: returns the
    greeting text on success, None on any failure. Logs result to disk."""
    client = get_client()
    if client is None:
        return None
    try:
        result = client.generate_greeting(
            boot_context_summary=session.boot_context_summary,
            chronicle_context=session.chronicle_context,
        )
    except Exception as exc:
        logger.warning("scribe: greeting generation failed: %s", exc)
        return None

    session.append_assistant_turn(
        result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
    )
    _log_phase1_greeting(
        session,
        result.text,
        {
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "tokens_cache_creation": result.tokens_cache_creation,
            "tokens_cache_read": result.tokens_cache_read,
            "cost_usd": result.cost_usd,
            "model": result.model,
            "stop_reason": result.stop_reason,
        },
    )
    return result.text


async def boot_spawn_and_greet_async(
    parent_instance: Optional[str],
    boot_text: str,
    ttl_minutes: int = 240,
) -> ScribeSession:
    """Async wrapper: spawn synchronously, run Haiku call on a thread.

    Always returns a session (with or without greeting). Safe to use
    inside async MCP handlers — the bridge does not block on Haiku
    latency for boot return; Phase 1 awaits the greeting because we want
    the log written before the handler returns, but Phase 2 may flip to
    fire-and-forget if latency proves painful.
    """
    session = boot_spawn(parent_instance, boot_text, ttl_minutes=ttl_minutes)
    await asyncio.to_thread(greet_session, session)
    return session


# ----------------------------------------------------------------------
# ask_scribe handler
# ----------------------------------------------------------------------


def _format_response(text: str, result_meta: dict, session: ScribeSession) -> str:
    """Compose the ask_scribe response with a small stats footer."""
    return (
        f"{text}\n\n"
        f"---\n"
        f"scribe: turn {session.turn_count}, "
        f"tokens {result_meta['tokens_in']}/{result_meta['tokens_out']} "
        f"(cache read {result_meta['tokens_cache_read']}), "
        f"this turn ${result_meta['cost_usd']:.4f}, "
        f"session total ${session.total_cost_usd:.4f}"
    )


def ask_scribe(session_id: Optional[str], message: str) -> str:
    """Handle an ask_scribe call. Returns plain text response.

    Session resolution: if session_id given, look up exact session.
    Otherwise, fall back to the most-recently-active session (single-
    process server pattern — works while one MCP server is serving one
    seat).
    """
    if not message or not message.strip():
        return "ask_scribe error: 'message' is required and must be non-empty"

    if session_id:
        session = _scribe_store.get(session_id)
        if session is None:
            return (
                f"ask_scribe error: session {session_id} not found or expired. "
                "Call where_did_i_leave_off to spawn a fresh scribe handle."
            )
    else:
        active = list(_scribe_store.active_sessions())
        if not active:
            return (
                "ask_scribe error: no active scribe session. "
                "Call where_did_i_leave_off to spawn one."
            )
        session = max(active, key=lambda s: s.last_message_at)

    client = get_client()
    if client is None:
        status = client_status()
        return (
            f"ask_scribe error: scribe unavailable ({status.get('state')}: "
            f"{status.get('error', 'no detail')}). Check ANTHROPIC_API_KEY_SCRIBE "
            f"in ~/.env."
        )

    # Redact the incoming message before it ever reaches Haiku.
    redacted = redact(message)
    session.append_user_turn(redacted.text, redaction_counts=redacted.counts)

    # Build conversation history (all turns BEFORE the just-added user turn).
    history = [
        {"role": turn.role, "content": turn.message} for turn in session.turns[:-1]
    ]

    try:
        result = client.generate_response(
            conversation_history=history,
            user_message=redacted.text,
            chronicle_context=session.chronicle_context,
        )
    except Exception as exc:
        return f"ask_scribe error: {type(exc).__name__}: {exc}"

    session.append_assistant_turn(
        result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
    )

    meta = {
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "tokens_cache_creation": result.tokens_cache_creation,
        "tokens_cache_read": result.tokens_cache_read,
        "cost_usd": result.cost_usd,
        "model": result.model,
        "stop_reason": result.stop_reason,
    }
    return _format_response(result.text, meta, session)


async def ask_scribe_async(session_id: Optional[str], message: str) -> str:
    """Async wrapper for the MCP handler."""
    return await asyncio.to_thread(ask_scribe, session_id, message)


# ----------------------------------------------------------------------
# Close / cleanup
# ----------------------------------------------------------------------


def close_session(session_id: str, write_encounter: bool = True) -> bool:
    """Close a scribe session: archive to disk, optionally write
    an encounter note to chronicle. Returns True if closed, False if
    session was unknown."""
    session = _scribe_store.get(session_id)
    if session is None:
        return False
    if write_encounter:
        try:
            write_encounter_note(session)
        except Exception as exc:
            logger.warning("scribe: encounter note write failed: %s", exc)
    return _scribe_store.close(session_id)
