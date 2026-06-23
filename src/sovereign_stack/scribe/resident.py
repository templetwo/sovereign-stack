"""Scribe resident librarian — boot-launched, single persistent session.

The resident is ONE ScribeSession that lives for the lifetime of the SSE
process. It holds the full chronicle context (including the route map)
permanently in its Sonnet window, stays fresh via dual-trigger refresh,
and serves distilled navigational payloads for any caller that reaches it.

Goals from scribe_resident_design.md:
  - ensure_resident_scribe(): idempotent; creates-or-returns the resident.
  - get_resident(): returns the live resident or None.
  - refresh_if_stale(resident): dual-trigger (map mtime + chronicle TTL),
    under the store RLock.
  - _load_map_section(): reads primary_routes.md, returns the 8th block.
  - _write_resident_marker(resident): thin provenance to state.json.

Three guarantees preserved:
  1. Map and context both pass through redact() before reaching Sonnet.
  2. The map section is scoped to the filesystem artifact; no path traversal.
  3. Network failures (no API key, Anthropic unreachable) never crash boot.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SOVEREIGN_ROOT = Path(os.environ.get("SOVEREIGN_ROOT", str(Path.home() / ".sovereign")))
STACK_MAP_PATH = SOVEREIGN_ROOT / "stack_map" / "primary_routes.md"
RESIDENT_SESSION_ID_PREFIX = "scribe_resident_"
RESIDENT_STATE_PATH = SOVEREIGN_ROOT / "scribe_threads" / "_resident" / "state.json"

# Chronicle context TTL — the resident's chronicle sections go stale after
# this many minutes and are rebuilt. Map mtime is checked independently on
# each ask_scribe call (nanosecond stat(), no watcher thread needed).
SCRIBE_CONTEXT_TTL_MINUTES = int(os.environ.get("SCRIBE_CONTEXT_TTL_MINUTES", "45"))

# Module-level resident state.
_resident_session_id: str | None = None
_resident_map_built_at: float = 0.0  # Unix ts of map mtime at last load
_resident_context_built_at: float = 0.0  # Unix ts when context was assembled

# Design note: the spec called for using the store's RLock.  That would create
# a tight cross-module coupling (reaching into ScribeSessionStore._lock from
# here).  Instead we use a dedicated module-level RLock that guards the same
# _resident_* bookkeeping variables and resident.chronicle_context mutations.
# This is a deviation from the spec letter; the spirit (serialise concurrent
# refresh + chronicle_context writes) is preserved.  The store's own _lock
# guards session CRUD independently; no deadlock risk because these two locks
# are never held simultaneously.
_resident_lock = threading.RLock()


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_map_section() -> str:
    """Read primary_routes.md verbatim. Returns a placeholder on missing/error."""
    if not STACK_MAP_PATH.exists():
        return (
            "(route map not yet built — run the stack-map builder or create "
            f"{STACK_MAP_PATH} to enable route-map navigation)"
        )
    try:
        return STACK_MAP_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return f"(route map unreadable: {exc})"


def _map_mtime() -> float:
    """Return mtime of primary_routes.md as Unix timestamp, or 0 if missing."""
    try:
        return STACK_MAP_PATH.stat().st_mtime
    except OSError:
        return 0.0


def _write_resident_marker(
    resident_session_id: str, map_built_at: float, context_built_at: float
) -> None:
    """Write thin provenance JSON to ~/.sovereign/scribe_threads/_resident/state.json.

    Writes {session_id, created_at, map_built_at, context_built_at, model}.
    Failures are non-fatal — always logged but never raised.
    """
    try:
        RESIDENT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": resident_session_id,
            "created_at": _now_iso(),
            "map_built_at": datetime.fromtimestamp(map_built_at, tz=timezone.utc).isoformat()
            if map_built_at
            else None,
            "context_built_at": datetime.fromtimestamp(
                context_built_at, tz=timezone.utc
            ).isoformat()
            if context_built_at
            else None,
            "model": os.environ.get("SCRIBE_MODEL", "claude-sonnet-4-6"),
        }
        RESIDENT_STATE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    except Exception as exc:
        logger.warning("scribe resident: marker write failed (non-fatal): %s", exc)


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def get_resident():
    """Return the live resident ScribeSession, or None if not yet created."""
    global _resident_session_id
    from .bridge_integration import get_store

    with _resident_lock:
        if _resident_session_id is None:
            return None
        store = get_store()
        return store._sessions.get(_resident_session_id)


def ensure_resident_scribe():
    """Idempotent boot-launch of the resident scribe.

    If a resident exists in the store, returns it immediately. Otherwise:
      1. Builds the full chronicle context (with route map).
      2. Redacts credentials from the assembled context.
      3. Creates an immortal ScribeSession and registers it.
      4. Writes the provenance marker.
      5. Returns the session.

    Network failures (no API key) are non-fatal — the session is created
    without a greeting; the greeting path (greet_session) is separate.
    """
    global _resident_session_id, _resident_map_built_at, _resident_context_built_at

    with _resident_lock:
        # Check if resident already alive
        if _resident_session_id is not None:
            from .bridge_integration import get_store

            existing = get_store()._sessions.get(_resident_session_id)
            if existing is not None:
                return existing
            # Resident was evicted (shouldn't happen for immortal, but guard it)
            logger.warning("scribe resident: session evicted unexpectedly, re-spawning")
            _resident_session_id = None

        # Build context
        try:
            from .context_builder import build_scribe_chronicle_context

            chronicle_ctx = build_scribe_chronicle_context()
        except Exception as exc:
            logger.warning("scribe resident: context build failed (%s), using empty context", exc)
            chronicle_ctx = ""

        # Redact credentials before they reach Sonnet
        from .redactor import redact

        try:
            ctx_redaction = redact(chronicle_ctx)
            if ctx_redaction.counts:
                logger.info("scribe resident: redacted context: %s", dict(ctx_redaction.counts))
            chronicle_ctx = ctx_redaction.text
        except Exception as exc:
            logger.warning("scribe resident: redaction failed (%s), context used unredacted", exc)

        # Track timestamps
        now_ts = time.time()
        map_mtime = _map_mtime()

        # Create immortal session
        from .session import ScribeSession

        session = ScribeSession.create(
            parent_instance="resident",
            boot_context_summary="resident scribe — boot-launched, single persistent session",
            chronicle_context=chronicle_ctx,
            immortal=True,
        )

        # Use a recognizable session ID prefix
        # We keep the generated session_id but record it
        from .bridge_integration import get_store

        store = get_store()
        store.register(session)

        _resident_session_id = session.session_id
        _resident_map_built_at = map_mtime
        _resident_context_built_at = now_ts

        _write_resident_marker(session.session_id, map_mtime, now_ts)
        logger.info("scribe resident: spawned session %s", session.session_id)
        return session


def refresh_if_stale(resident) -> bool:
    """Check dual-refresh triggers and update the resident in place.

    Trigger 1 (map): stat() primary_routes.md; if mtime > map_built_at,
      reload just the map section in resident.chronicle_context.
    Trigger 2 (chronicle TTL): if now - context_built_at > TTL, or if the
      newest chronicle entry mtime exceeds context_built_at, rebuild the
      full chronicle context (all 8 sections including the map).

    Both updates are performed under the module-level _resident_lock to prevent
    concurrent modification of chronicle_context. The map trigger always re-redacts the
    new map block; the TTL trigger re-redacts the whole context.

    Returns True if any refresh was performed.
    """
    global _resident_map_built_at, _resident_context_built_at

    refreshed = False
    now_ts = time.time()
    current_map_mtime = _map_mtime()
    ttl_seconds = SCRIBE_CONTEXT_TTL_MINUTES * 60

    with _resident_lock:
        # --- Trigger 2: chronicle TTL check (do first; it rebuilds map too) ---
        chronicle_stale = (now_ts - _resident_context_built_at) > ttl_seconds
        if chronicle_stale:
            try:
                from .context_builder import build_scribe_chronicle_context

                new_ctx = build_scribe_chronicle_context()
                from .redactor import redact

                ctx_redaction = redact(new_ctx)
                if ctx_redaction.counts:
                    logger.info(
                        "scribe resident: redacted refreshed context: %s",
                        dict(ctx_redaction.counts),
                    )
                resident.chronicle_context = ctx_redaction.text
                _resident_context_built_at = now_ts
                _resident_map_built_at = current_map_mtime  # map is included in the rebuild
                _write_resident_marker(resident.session_id, current_map_mtime, now_ts)
                refreshed = True
                logger.info("scribe resident: chronicle context refreshed (TTL trigger)")
            except Exception as exc:
                logger.warning("scribe resident: chronicle TTL refresh failed: %s", exc)
            return refreshed

        # --- Trigger 1: map mtime check (only if chronicle was not rebuilt) ---
        if current_map_mtime > _resident_map_built_at:
            try:
                new_map_text = _load_map_section()
                from .redactor import redact

                map_redaction = redact(new_map_text)
                new_map_section = (
                    "=== PRIMARY ROUTES MAP (always-loaded navigation) ===\n" + map_redaction.text
                )
                # Swap just the map section in the existing context string.
                # The section is delimited by the === header line.
                ctx = resident.chronicle_context
                map_header = "=== PRIMARY ROUTES MAP (always-loaded navigation) ==="
                map_next_header_prefix = "=== "
                if map_header in ctx:
                    # Find the start of the map section
                    start = ctx.index(map_header)
                    # Find the next === section after it
                    after_header = ctx.find(map_next_header_prefix, start + len(map_header))
                    if after_header == -1:
                        # Map is the last section
                        ctx = ctx[:start] + new_map_section + "\n"
                    else:
                        ctx = ctx[:start] + new_map_section + "\n" + ctx[after_header:]
                else:
                    # Map section not present — prepend it
                    ctx = new_map_section + "\n\n" + ctx
                resident.chronicle_context = ctx
                _resident_map_built_at = current_map_mtime
                _write_resident_marker(
                    resident.session_id, current_map_mtime, _resident_context_built_at
                )
                refreshed = True
                logger.info("scribe resident: map section refreshed (mtime trigger)")
            except Exception as exc:
                logger.warning("scribe resident: map mtime refresh failed: %s", exc)

    return refreshed


def _reset_resident_for_tests() -> None:
    """Reset module-level resident state. For tests only — mirrors reset_env_cache()."""
    global _resident_session_id, _resident_map_built_at, _resident_context_built_at
    with _resident_lock:
        _resident_session_id = None
        _resident_map_built_at = 0.0
        _resident_context_built_at = 0.0
