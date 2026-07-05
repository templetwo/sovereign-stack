"""
Step-up elevation for the Claude connector's destructive tier, riding the
Door That Asks — one code path, zero changes to the Door itself.

Flow (all against the REST bridge on 127.0.0.1:8100, whose /api/arrival/*
endpoints are deliberately unauthenticated for requests and polls):

  1. A destructive tool call arrives without a live elevation. We POST
     /api/arrival/request with seat_description "STEP-UP: <tool> …". The
     Door pushes the Approve/Deny ntfy to Anthony's phone (same confirm-page
     flow as arrivals, decided_via lands as ntfy_tap) and hands back a
     two-word pairing code. The tool call returns a structured refusal
     carrying that code — the MCP client cannot sit in a blocking call
     waiting on a human (10s/30s client timeouts), so approval is async
     and the model simply re-calls the tool after Anthony approves.
  2. On re-call we poll /api/arrival/poll/<rid> (self-throttled to the
     Door's 5s discipline — over-polling voids the request server-side).
     When the poll returns approved, the Door has ALREADY chronicled the
     grant server-side and minted a receipt token. We record the token_id
     as the elevation receipt and DISCARD the plaintext session token
     unused (plaintext-once invariant: it is never stored, logged, or
     presented anywhere by this module).
  3. The elevation is then ACTIVE for this (tool, token-family) pair for
     ELEVATION_TTL seconds (default 15 min — deliberately tighter than the
     Door's 1h token floor). Every destructive execution under it is
     appended to the local audit log.

Failure mode is closed: Door unreachable / gate disabled / denied / expired
all mean the destructive call refuses with a human-readable reason. Base-tier
tools are never touched by this module.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_CLAUDE_DIR = Path.home() / ".sovereign" / "claude_bridge"
_ELEV_DIR = _CLAUDE_DIR / "elevations"
_AUDIT_DIR = _CLAUDE_DIR / "audit"
_AUDIT_LOG = _AUDIT_DIR / "destructive_calls.jsonl"

DOOR_BASE_URL = os.environ.get("CLAUDE_DOOR_BASE_URL", "http://127.0.0.1:8100")
ELEVATION_TTL_SECONDS = int(os.environ.get("CLAUDE_ELEVATION_TTL", "900"))
_DOOR_TIMEOUT_SECONDS = 10.0
# The Door voids requests polled faster than its 5s discipline; leave margin.
_MIN_POLL_INTERVAL_SECONDS = 6.0

for _d in (_ELEV_DIR, _AUDIT_DIR):
    _d.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(_d, 0o700)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write_secure(path: Path, text: str) -> None:
    path.write_text(text)
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        logger.warning("could not chmod %s: %s", path, e)


def audit(event: str, **fields) -> None:
    """Append one line to the destructive-tier audit log. Never raises."""
    try:
        record = {"ts": _now().isoformat(), "event": event, **fields}
        with _AUDIT_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
        os.chmod(_AUDIT_LOG, 0o600)
    except Exception as exc:  # audit is best-effort; it must never break a call
        logger.warning("elevation audit write failed: %s", exc)


# ── Elevation records ─────────────────────────────────────────────────────────
# One JSON file per elevation, keyed by (tool, family_id). States:
#   pending  — Door request created, awaiting Anthony's tap
#   active   — approved; valid until approved_at + ELEVATION_TTL
#   denied   — Anthony denied, or the Door expired/voided the request


def _elev_path(tool: str, family_id: str) -> Path:
    # Deterministic per (tool, family) so re-calls find the same record.
    return _ELEV_DIR / f"{family_id[:16]}__{tool}.json"


def _load_elevation(tool: str, family_id: str) -> dict | None:
    path = _elev_path(tool, family_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _save_elevation(tool: str, family_id: str, data: dict) -> None:
    _write_secure(_elev_path(tool, family_id), json.dumps(data, indent=2))


@dataclass
class ElevationStatus:
    state: str  # "active" | "pending" | "unavailable" | "denied"
    detail: str
    code: str | None = None  # the two-word pairing code, when relevant


# ── Door client ───────────────────────────────────────────────────────────────


async def _door_request(tool: str, family_id: str, client_id: str) -> ElevationStatus:
    """Create the step-up request at the Door and record it pending."""
    payload = {
        "source_instance": "claude-ai-bridge",
        "seat_description": (
            f"STEP-UP: destructive tool '{tool}' — claude.ai connector, "
            f"grant {family_id[:8]}, client {client_id[:20]}"
        ),
        # The svs_ token the Door mints at approval is a RECEIPT here, not a
        # credential we use — request the minimum scope the Door grants.
        "requested_scope": ["read"],
        "requested_ttl_hours": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=_DOOR_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{DOOR_BASE_URL}/api/arrival/request", json=payload)
    except httpx.HTTPError as exc:
        logger.warning("Door That Asks unreachable for step-up: %s", exc)
        return ElevationStatus(
            "unavailable",
            "The approval service (Door That Asks) is unreachable; destructive tier stays closed.",
        )
    if resp.status_code == 404:
        return ElevationStatus(
            "unavailable",
            "The arrival gate is disabled on this stack; destructive tier stays closed.",
        )
    if resp.status_code == 429:
        return ElevationStatus(
            "unavailable",
            "The approval service is rate-limited right now; retry in a few minutes.",
        )
    if resp.status_code not in (200, 201):
        return ElevationStatus("unavailable", f"Approval service returned HTTP {resp.status_code}.")

    body = resp.json()
    rid = body.get("arrival_request_id", "")
    code = body.get("code", "")
    if not rid or not code:
        return ElevationStatus("unavailable", "Approval service returned an unexpected shape.")

    _save_elevation(
        tool,
        family_id,
        {
            "elevation_id": secrets.token_hex(8),
            "tool": tool,
            "family_id": family_id,
            "client_id": client_id,
            "rid": rid,
            "code": code,
            "status": "pending",
            "requested_at": _now().isoformat(),
            "last_poll_at": None,
            "notification_sent": bool(body.get("notification_sent")),
        },
    )
    audit("step_up_requested", tool=tool, family=family_id[:12], rid=rid, code=code)
    note = (
        ""
        if body.get("notification_sent")
        else (
            " (Phone notification could not be delivered — Anthony can approve from HQ "
            "with the arrival admin endpoint.)"
        )
    )
    return ElevationStatus(
        "pending",
        f"Step-up approval requested. Pairing code: '{code}'. Tell Anthony, wait for "
        f"his approval tap, then call this tool again.{note}",
        code=code,
    )


async def _door_poll(rec: dict, tool: str, family_id: str) -> ElevationStatus:
    """Poll a pending request, honoring the Door's polling discipline."""
    code = rec.get("code", "")
    last_poll = rec.get("last_poll_at")
    if last_poll:
        try:
            elapsed = (_now() - datetime.fromisoformat(last_poll)).total_seconds()
        except ValueError:
            elapsed = _MIN_POLL_INTERVAL_SECONDS
        if elapsed < _MIN_POLL_INTERVAL_SECONDS:
            # Too soon to ask the Door again — report pending without polling
            # (over-polling voids the request server-side).
            return ElevationStatus(
                "pending",
                f"Step-up '{code}' is still awaiting Anthony's approval. "
                "Wait a few seconds and call the tool again.",
                code=code,
            )

    rec["last_poll_at"] = _now().isoformat()
    _save_elevation(tool, family_id, rec)

    try:
        async with httpx.AsyncClient(timeout=_DOOR_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{DOOR_BASE_URL}/api/arrival/poll/{rec['rid']}")
    except httpx.HTTPError as exc:
        logger.warning("Door poll failed: %s", exc)
        return ElevationStatus(
            "pending",
            f"Step-up '{code}' status could not be checked (approval service hiccup); "
            "call the tool again shortly.",
            code=code,
        )
    if resp.status_code != 200:
        return ElevationStatus(
            "pending",
            f"Step-up '{code}' status check returned HTTP {resp.status_code}; "
            "call the tool again shortly.",
            code=code,
        )

    body = resp.json()
    status = body.get("status", "")

    if status in ("pending", "slow_down"):
        return ElevationStatus(
            "pending",
            f"Step-up '{code}' is still awaiting Anthony's approval.",
            code=code,
        )
    if status == "approved":
        # The Door has minted + chronicled the grant. Keep the token_id as the
        # receipt; the plaintext session token is deliberately dropped here and
        # nowhere stored (plaintext-once invariant — it is a receipt, not a
        # credential this module uses).
        rec["status"] = "active"
        rec["approved_at"] = _now().isoformat()
        rec["receipt_token_id"] = body.get("token_id", "")
        rec["decided_via"] = (body.get("grant") or {}).get("decided_via", "")
        _save_elevation(tool, family_id, rec)
        audit(
            "step_up_approved",
            tool=tool,
            family=family_id[:12],
            rid=rec.get("rid"),
            receipt_token_id=rec["receipt_token_id"],
            decided_via=rec["decided_via"],
        )
        return ElevationStatus("active", f"Step-up '{code}' approved.", code=code)
    if status == "consumed":
        # Someone else won the poll race (shouldn't happen — we are the only
        # poller for this rid) or a previous poll already flipped it. If we
        # already recorded approval, honor it; otherwise treat as denied.
        if rec.get("status") == "active":
            return ElevationStatus("active", f"Step-up '{code}' approved.", code=code)
        rec["status"] = "denied"
        _save_elevation(tool, family_id, rec)
        return ElevationStatus(
            "denied", f"Step-up '{code}' was already consumed; request again.", code=code
        )

    # denied / expired / anything else → closed.
    rec["status"] = "denied"
    rec["resolved_status"] = status
    _save_elevation(tool, family_id, rec)
    audit("step_up_refused", tool=tool, family=family_id[:12], door_status=status)
    return ElevationStatus(
        "denied",
        f"Step-up '{code}' was {status or 'refused'} at the Door. "
        "Request again if this was unintended.",
        code=code,
    )


# ── Public surface ────────────────────────────────────────────────────────────


async def ensure_elevation(tool: str, family_id: str, client_id: str) -> ElevationStatus:
    """
    The one call the tier gate makes for a destructive tool. Returns the
    current elevation state for (tool, token-family), advancing it where
    possible: active → allow; pending → poll; absent/stale → request anew.
    """
    rec = _load_elevation(tool, family_id)

    if rec is not None and rec.get("status") == "active":
        try:
            approved_at = datetime.fromisoformat(rec["approved_at"])
        except (KeyError, ValueError):
            approved_at = None
        if approved_at and _now() - approved_at < timedelta(seconds=ELEVATION_TTL_SECONDS):
            return ElevationStatus("active", "Elevation active.", code=rec.get("code"))
        # Expired elevation → fall through to a fresh request.
        rec = None

    if rec is not None and rec.get("status") == "pending":
        # Give up on pending requests older than the Door's 15-min (900s)
        # pending window, with ~100s of margin so the Door's own expiry
        # (surfaced via poll) wins over our local staleness guard.
        try:
            requested_at = datetime.fromisoformat(rec["requested_at"])
            stale = _now() - requested_at > timedelta(seconds=1000)
        except (KeyError, ValueError):
            stale = True
        if stale:
            rec = None
        else:
            return await _door_poll(rec, tool, family_id)

    if rec is not None and rec.get("status") == "denied":
        # A denied/expired record does not block a fresh request — Anthony may
        # simply have missed the first push. Fall through.
        pass

    return await _door_request(tool, family_id, client_id)


def record_destructive_execution(tool: str, family_id: str, client_id: str) -> None:
    """Audit-log an actual destructive-tier execution under a live elevation."""
    rec = _load_elevation(tool, family_id) or {}
    audit(
        "destructive_executed",
        tool=tool,
        family=family_id[:12],
        client=client_id[:20],
        elevation_id=rec.get("elevation_id"),
        receipt_token_id=rec.get("receipt_token_id"),
    )


def revoke_all_elevations() -> int:
    """One-call local revocation: delete every elevation record."""
    count = 0
    for path in _ELEV_DIR.glob("*.json"):
        path.unlink(missing_ok=True)
        count += 1
    if count:
        audit("elevations_revoked_all", count=count)
    return count
