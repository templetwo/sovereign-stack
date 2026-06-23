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

from .context_builder import build_scribe_chronicle_context
from .encounter import write_encounter_note
from .redactor import redact
from .session import ScribeSession, ScribeSessionStore
from .tools import anthropic_tool_definitions, dispatch_tool

logger = logging.getLogger(__name__)

SOVEREIGN_ROOT = Path(os.environ.get("SOVEREIGN_ROOT", str(Path.home() / ".sovereign")))
PHASE1_LOG_ROOT = SOVEREIGN_ROOT / "scribe_threads" / "_logs"


def boot_inject_enabled() -> bool:
    """Phase 2 feature flag. Default ON.

    Set SCRIBE_BOOT_INJECT=off (or 0 / false / no) to disable boot
    injection while keeping Phase 1 session-spawn + greeting-log active.
    Useful as a kill switch if the scribe destabilizes a seat.
    """
    raw = os.environ.get("SCRIBE_BOOT_INJECT", "on").strip().lower()
    return raw not in {"off", "0", "false", "no", "disabled"}


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
_client_error: str | None = None


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
    parent_instance: str | None,
    boot_text: str,
    ttl_minutes: int = 240,
) -> ScribeSession:
    """Spawn a ScribeSession at boot time. Always succeeds — does not
    attempt the Haiku call. Use greet_session() separately to call Haiku.

    The scribe gets a FULL-CONTENT chronicle context (via
    build_scribe_chronicle_context), not the caller's possibly-truncated
    boot_text. This addresses the 2026-05-19 iPhone-seat finding that
    the scribe was inheriting the caller's truncated view and silently
    failing on content that had been clipped at 120 chars in the boot.

    The boot_text is kept as boot_context_summary (a 200-char hint) so
    the scribe knows what the caller saw, but its working surface is
    the full chronicle slice.
    """
    summary = _summarize_boot(boot_text)
    try:
        chronicle_ctx = build_scribe_chronicle_context()
    except Exception as exc:
        # Never let context-build failure break boot. Fall back to the
        # caller's boot_text so the scribe at least has SOMETHING.
        logger.warning(
            "scribe: full-context build failed (%s); falling back to boot_text",
            exc,
        )
        chronicle_ctx = boot_text
    # The redaction gate covered ask_scribe messages and tool results but NOT
    # this boot-assembled block, which reached Haiku raw (found 2026-06-12).
    # Credentials in chronicle entries must never leave the machine.
    ctx_redaction = redact(chronicle_ctx)
    if ctx_redaction.counts:
        logger.info("scribe: redacted boot context: %s", dict(ctx_redaction.counts))
    chronicle_ctx = ctx_redaction.text
    session = ScribeSession.create(
        parent_instance=parent_instance,
        boot_context_summary=summary,
        ttl_minutes=ttl_minutes,
        chronicle_context=chronicle_ctx,
    )
    _scribe_store.register(session)
    return session


def greet_session(session: ScribeSession) -> str | None:
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
    parent_instance: str | None,
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
# Phase 2: format the SCRIBE block for boot injection
# ----------------------------------------------------------------------


def format_scribe_block(session: ScribeSession) -> str:
    """Render the SCRIBE — OPTIONAL section that gets injected into the
    boot ritual when SCRIBE_BOOT_INJECT is enabled.

    Leads with "this is optional" so an arriving instance knows they can
    ignore the section if they don't need help landing. Includes the
    greeting (if generated), the conversation handle, and quick-start
    examples. Never blocks the boot if the greeting is missing — just
    presents the handle so the instance can engage on demand.
    """
    handle = session.handle_payload()
    handle_json = json.dumps(handle, indent=4).replace("\n", "\n  ")

    # Greeting may be empty if Haiku init failed; the block still ships.
    greeting = ""
    for turn in session.turns:
        if turn.role == "assistant" and turn.message.strip():
            greeting = turn.message.strip()
            break

    lines: list[str] = [
        "━━━ SCRIBE — OPTIONAL ━━━",
        "",
        "  A Sonnet 4.6 scribe has been reading alongside you. It is here",
        "  to help you land well, not to direct. You can ignore this",
        "  entire section. The boot above is complete; the scribe is an",
        "  additional resource if you want one.",
        "",
        "  Use it for arrival orientation, chronicle synthesis, or quick",
        "  answers about stack state. It is read-only — it cannot retire,",
        "  resolve, modify, or delete anything. Brief by design.",
        "",
        f"  scribe_handle: {handle_json}",
        "",
    ]

    if greeting:
        lines.append("  The scribe says:")
        for greeting_line in greeting.split("\n"):
            lines.append(f"    {greeting_line}")
        lines.append("")

    lines.extend(
        [
            "  To engage:",
            "    POST /api/call ask_scribe",
            f'    {{ "session_id": "{session.session_id}", "message": "<your question>" }}',
            "",
            "  Example asks:",
            '    - "summarize the open threads tagged sovereign-stack"',
            '    - "who wrote the witness-recognition entry and when"',
            '    - "is there a handoff I have not read yet?"',
            "",
            "  If you do not need the scribe, just proceed. The work is yours.",
        ]
    )
    return "\n".join(lines)


# ----------------------------------------------------------------------
# ask_scribe handler — Fork-D navigational payload
# ----------------------------------------------------------------------


def _known_map_route_names() -> set[str]:
    """Read the primary_routes.md and extract route names (first word on bullet lines).

    Used to constrain suggested_calls so they cannot be invented by the model.
    Returns empty set if the map is unavailable.
    """
    map_path = SOVEREIGN_ROOT / "stack_map" / "primary_routes.md"
    if not map_path.exists():
        return set()
    try:
        text = map_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            # Route lines: "- route_name (kind): description..."
            body = stripped[2:]
            name_part = body.split(" ")[0].split("(")[0].strip()
            if name_part:
                names.add(name_part)
                # Also add the tool call form
                names.add(f"{name_part}()")
    return names


def _format_navigational_payload(result, session: ScribeSession, meta: dict) -> str:
    """Build the Fork-D JSON-in-text navigational payload.

    The model's text is expected to be a JSON object with keys:
      synthesis, routes, entries, suggested_calls, gaps, meta

    If the model returned plain prose (not JSON), it is wrapped into the
    envelope so the contract never breaks for prose-treating callers.

    meta is built server-side and is authoritative (the model's meta, if any,
    is ignored to prevent model-injected data from overriding cost/token info).
    """
    import re

    known_routes = _known_map_route_names()
    text = result.text.strip() if result.text else ""

    parsed: dict = {}
    # Try to parse JSON from the model output
    try:
        # Strip markdown code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
        parsed = json.loads(cleaned.strip())
        if not isinstance(parsed, dict):
            parsed = {}
    except (json.JSONDecodeError, ValueError):
        pass

    # Extract fields; synthesize defaults when absent
    synthesis = (parsed.get("synthesis") or text or "").strip()
    if not synthesis:
        synthesis = "(no synthesis returned)"

    routes = parsed.get("routes") or []
    if not isinstance(routes, list):
        routes = []

    entries = parsed.get("entries") or []
    if not isinstance(entries, list):
        entries = []

    gaps = parsed.get("gaps") or []
    if not isinstance(gaps, list):
        gaps = []

    # Constrain suggested_calls to names present in the map (def #582 + spec)
    raw_calls = parsed.get("suggested_calls") or []
    if not isinstance(raw_calls, list):
        raw_calls = []
    if known_routes:

        def _in_map(c: str) -> bool:
            # Exact match first (e.g. "my_toolkit()")
            if c in known_routes:
                return True
            # Strip everything from the first '(' to handle parameterized calls
            # e.g. "recall_insights(query=...)" -> "recall_insights"
            base = c.split("(")[0].strip()
            return base in known_routes or f"{base}()" in known_routes

        suggested_calls = [c for c in raw_calls if _in_map(c)]
    else:
        # Map unavailable: fail closed. Without a known-route allowlist we
        # cannot prevent a model-generated path or invented tool name from
        # appearing in suggested_calls. Empty is safe; unvalidated raw_calls
        # is not (Guarantee 3 — suggested_calls cannot smuggle a path).
        suggested_calls = []

    payload = {
        "synthesis": synthesis,
        "routes": routes,
        "entries": entries,
        "suggested_calls": suggested_calls,
        "gaps": gaps,
        "meta": meta,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def ask_scribe(session_id: str | None, message: str) -> str:
    """Handle an ask_scribe call. Returns a Fork-D navigational JSON payload.

    Session resolution (Fork A semantics — no 404):
      - Resolve the resident via ensure_resident_scribe() (idempotent).
      - If session_id is given, it is a caller-thread namespace (optional).
        Unknown or expired session_id never 404s — silently uses resident.
      - If session_id is omitted, single-shot against the resident.

    The resident holds the shared chronicle context. Per-session turn history
    is lightweight and layered on top of the shared context.

    Returns the Fork-D JSON string (synthesis-first) or a plain error string
    on hard failure (client init failed, etc.).
    """
    if not message or not message.strip():
        return "ask_scribe error: 'message' is required and must be non-empty"

    # Resolve (or spawn) the resident — idempotent, never raises
    try:
        from .resident import ensure_resident_scribe, refresh_if_stale

        resident = ensure_resident_scribe()
        if resident is not None:
            refresh_if_stale(resident)
    except Exception as exc:
        logger.warning("scribe: resident resolution failed: %s", exc)
        resident = None

    # Determine the conversation session and whether this is a stateful or
    # stateless (single-shot) call.
    #
    # Fork A semantics — no 404:
    #   - session_id given AND found → stateful multi-turn (mutate that session)
    #   - session_id given BUT unknown/expired → stateless single-shot against
    #     resident context (never 404; resident accumulates nothing)
    #   - session_id omitted → stateless single-shot against resident context
    #
    # "Stateless single-shot" means: use resident.chronicle_context as the
    # cached system block, pass empty history, and do NOT append turns to
    # the resident.  This prevents cross-seat bleed and unbounded turn growth
    # on the process-lifetime session (spec Fork A, line 17).

    stateless = False  # True → do not mutate any session's turn list

    if session_id:
        session = _scribe_store.get(session_id)
        if session is None:
            # Unknown or expired id: use resident context, single-shot
            session = resident
            stateless = True
    else:
        # No id: single-shot against resident
        session = resident
        stateless = True

    # Last-resort fallback: if still no session (resident spawn failed), try
    # to find any active session for this process.
    if session is None:
        active = list(_scribe_store.active_sessions())
        if active:
            session = max(active, key=lambda s: s.last_message_at)
            # Only mark stateless if we fell back to the resident's kind of session
            if getattr(session, "immortal", False):
                stateless = True

    client = get_client()
    if client is None:
        status = client_status()
        return (
            f"ask_scribe error: scribe unavailable ({status.get('state')}: "
            f"{status.get('error', 'no detail')}). Check ANTHROPIC_API_KEY_SCRIBE "
            f"in ~/.env."
        )

    if session is None:
        return (
            "ask_scribe error: no active scribe session and resident spawn failed. "
            "Restart the stack or call where_did_i_leave_off to spawn a session."
        )

    # Redact the incoming message before it ever reaches Sonnet.
    redacted = redact(message)

    if stateless:
        # Single-shot: empty history (no prior turns shared with this caller),
        # resident chronicle_context as the cached system block.
        history: list[dict] = []
        chronicle_context = session.chronicle_context
        turn_count = 0
    else:
        # Stateful multi-turn: append user turn, build history from previous turns.
        session.append_user_turn(redacted.text, redaction_counts=redacted.counts)
        history = [{"role": turn.role, "content": turn.message} for turn in session.turns[:-1]]
        chronicle_context = session.chronicle_context
        turn_count = session.turn_count

    try:
        result = client.generate_response(
            conversation_history=history,
            user_message=redacted.text,
            chronicle_context=chronicle_context,
            tools=anthropic_tool_definitions(),
            tool_dispatch=dispatch_tool,
            max_tool_iterations=5,
        )
    except Exception as exc:
        return f"ask_scribe error: {type(exc).__name__}: {exc}"

    if not stateless:
        # Only persist the assistant turn for stateful sessions
        session.append_assistant_turn(
            result.text,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
        )
        turn_count = session.turn_count

    # Build authoritative meta (server-side; model meta never trusted)
    resident_session_id = getattr(resident, "session_id", None)
    meta: dict = {
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "tokens_cache_creation": result.tokens_cache_creation,
        "tokens_cache_read": result.tokens_cache_read,
        "cost_usd": result.cost_usd,
        "model": result.model,
        "stop_reason": result.stop_reason,
        "tools_fired": len(result.tool_calls_made),
        "tool_calls": result.tool_calls_made,
        "resident_session_id": resident_session_id,
        "turn": turn_count,
        "stateless": stateless,
    }
    return _format_navigational_payload(result, session, meta)


async def ask_scribe_async(session_id: str | None, message: str) -> str:
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
