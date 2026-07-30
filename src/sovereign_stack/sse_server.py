"""
Sovereign Stack SSE Server

HTTP/SSE transport layer for remote access via Cloudflare tunnel.
Runs alongside stdio server for local Claude Code access.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import threading
import time
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID

import uvicorn
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

# Import the existing sovereign-stack server
from .server import server as sovereign_server

# Optional: OpenAI bridge filtered endpoint.
# Gracefully absent if the clients package is not on the path.
_BRIDGE_CLIENTS = Path(__file__).parent.parent.parent / "clients"
if _BRIDGE_CLIENTS.exists() and str(_BRIDGE_CLIENTS) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_CLIENTS))

try:
    from bridge_core import send_401 as _gate_send_401
    from bridge_core import verify_at_door as _verify_at_door
    from openai_bridge.mcp_filtered import (
        handle_openai_messages,
        handle_openai_messages_test,
        handle_openai_sse,
        handle_openai_sse_test,
    )
    from openai_bridge.oauth import (
        handle_authorization_server_metadata as handle_openai_oauth_as_meta,
    )
    from openai_bridge.oauth import (
        handle_authorize as handle_openai_oauth_authorize,
    )
    from openai_bridge.oauth import (
        handle_protected_resource_metadata as handle_openai_oauth_pr_meta,
    )
    from openai_bridge.oauth import (
        handle_register as handle_openai_oauth_register,
    )
    from openai_bridge.oauth import (
        handle_token as handle_openai_oauth_token,
    )

    _BRIDGE_ENABLED = True
except ImportError:
    _BRIDGE_ENABLED = False
    handle_openai_sse = None
    handle_openai_messages = None
    handle_openai_sse_test = None
    handle_openai_messages_test = None
    handle_openai_oauth_authorize = None
    handle_openai_oauth_token = None
    handle_openai_oauth_register = None
    handle_openai_oauth_as_meta = None
    handle_openai_oauth_pr_meta = None
    _gate_send_401 = None
    _verify_at_door = None

# Grok bridge — independently importable; failure doesn't disable openai_bridge.
try:
    from grok_bridge.manifest import MANIFEST as GROK_MANIFEST
    from grok_bridge.mcp_filtered import (
        handle_grok_messages,
        handle_grok_sse,
    )
    from grok_bridge.oauth import (
        handle_authorization_server_metadata as handle_grok_oauth_as_meta,
    )
    from grok_bridge.oauth import (
        handle_authorize as handle_grok_oauth_authorize,
    )
    from grok_bridge.oauth import (
        handle_protected_resource_metadata as handle_grok_oauth_pr_meta,
    )
    from grok_bridge.oauth import (
        handle_token as handle_grok_oauth_token,
    )

    _GROK_BRIDGE_ENABLED = True
except ImportError as _grok_e:
    _GROK_BRIDGE_ENABLED = False
    handle_grok_sse = None
    handle_grok_messages = None
    handle_grok_oauth_authorize = None
    handle_grok_oauth_token = None
    handle_grok_oauth_as_meta = None
    handle_grok_oauth_pr_meta = None
    GROK_MANIFEST = None
    logging.getLogger("sovereign-stack-sse").warning("Grok bridge not loaded: %s", _grok_e)

# Claude bridge — native surface over Streamable HTTP, OAuth 2.1-gated.
# Independently importable; failure doesn't disable the other bridges.
try:
    from claude_bridge.manifest import MANIFEST as CLAUDE_MANIFEST
    from claude_bridge.mcp_native import handle_claude_mcp
    from claude_bridge.mcp_native import session_manager as claude_session_manager
    from claude_bridge.oauth import (
        handle_authorization_server_metadata as handle_claude_oauth_as_meta,
    )
    from claude_bridge.oauth import (
        handle_authorize as handle_claude_oauth_authorize,
    )
    from claude_bridge.oauth import (
        handle_claude_oauth_authorize_status,
    )
    from claude_bridge.oauth import (
        handle_protected_resource_metadata as handle_claude_oauth_pr_meta,
    )
    from claude_bridge.oauth import (
        handle_register as handle_claude_oauth_register,
    )
    from claude_bridge.oauth import (
        handle_revoke as handle_claude_oauth_revoke,
    )
    from claude_bridge.oauth import (
        handle_token as handle_claude_oauth_token,
    )

    _CLAUDE_BRIDGE_ENABLED = True
except Exception as _claude_e:
    # Broad by design: a module-level side-effect failure (bad env, filesystem)
    # must disable the claude bridge, never collapse the whole SSE server
    # (all bridges + native /sse). Fail closed with a warning.
    _CLAUDE_BRIDGE_ENABLED = False
    handle_claude_mcp = None
    claude_session_manager = None
    handle_claude_oauth_authorize = None
    handle_claude_oauth_authorize_status = None
    handle_claude_oauth_token = None
    handle_claude_oauth_register = None
    handle_claude_oauth_revoke = None
    handle_claude_oauth_as_meta = None
    handle_claude_oauth_pr_meta = None
    CLAUDE_MANIFEST = None
    logging.getLogger("sovereign-stack-sse").warning("Claude bridge not loaded: %s", _claude_e)

# Root-level well-known paths (RFC 8414 §3.1 / RFC 9728 §3.1 path-insertion
# forms) that MCP clients probe for the claude bridge. Answering every
# discovery shape with a definitive 200 is the #4030 retry-loop hardening.
_CLAUDE_ROOT_AS_META_PATHS = (
    "/.well-known/oauth-authorization-server/claude",
    "/.well-known/openid-configuration/claude",
)
_CLAUDE_ROOT_PR_META_PATHS = (
    "/.well-known/oauth-protected-resource/claude/mcp",
    "/.well-known/oauth-protected-resource/claude",
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sovereign-stack-sse")

# Create SSE transport at module level (shared between routes)
sse = SseServerTransport("/messages")


# Health check endpoint
async def health(request: Request) -> JSONResponse:
    """Health check for monitoring"""
    return JSONResponse({"status": "healthy", "service": "sovereign-stack-sse", "version": "1.0.0"})


# SSE endpoint - holds connection open for server-sent events
async def handle_sse(request: Request):
    """
    SSE endpoint - establishes Server-Sent Events connection
    """
    logger.info(f"New SSE connection from {request.client}")

    async with sse.connect_sse(request.scope, request.receive, request._send) as (
        read_stream,
        write_stream,
    ):
        await sovereign_server.run(
            read_stream,
            write_stream,
            sovereign_server.create_initialization_options(),
            raise_exceptions=True,
        )


# ── Native SSE auth ───────────────────────────────────────────────────────────
# GET /sse requires the BRIDGE_TOKEN credential, supplied either as an
# `Authorization: Bearer <token>` header (bridge, header-capable MCP clients)
# or a `?token=<token>` query parameter (clients whose connector config only
# exposes a URL field, e.g. the claude.ai remote connector).
#
# POST /messages is ALSO credential-gated, and bound to the principal that
# opened the session. It used to be capability-gated only — the reasoning was
# that the mcp transport accepts nothing but a session_id minted by an
# authenticated connect (unknown ids → 404), so the connect-time check covered
# the session. GHSA-jpw9-pfvf-9f58 (HIGH, fixed upstream in mcp 1.27.2)
# invalidated exactly that: "HTTP transports serve session requests without
# verifying the authenticated principal." The venv runs mcp 1.26.0, whose
# handle_post_message checks only that the session_id parses and exists.
#
# The practical exposure was not remote-unauthenticated-write (unknown ids
# still 404) — it was that a live session_id became a bearer credential all by
# itself. Anyone who OBTAINED one could drive that session with a wrong token
# or none at all. Leak became takeover. See _messages_verdict below.
#
# Fail-closed: if BRIDGE_TOKEN is unset, /sse refuses everything unless
# SSE_ALLOW_UNAUTHENTICATED=true is set explicitly (local-dev escape hatch).
# Token is read at call time so a launchd env edit + restart is sufficient.


# ── Connect-rate limiting (public traffic only) ──────────────────────────────
# Token bucket on NEW SSE connects, keyed by CF-Connecting-IP. The header is
# present iff the request came through the Cloudflare tunnel; local connects
# (the bridge, dev) carry no header and are exempt. /messages POSTs are not
# limited — they are session-gated and legitimately high-rate.

_SSE_CONNECT_BURST = float(os.environ.get("SSE_CONNECT_BURST", "10"))
_SSE_CONNECT_REFILL_PER_SEC = float(os.environ.get("SSE_CONNECT_PER_MIN", "30")) / 60.0
_connect_buckets: dict[str, tuple[float, float]] = {}
_connect_lock = threading.Lock()


def _public_ip(scope: dict) -> str | None:
    """The tunnel-forwarded client IP, or None for local/trusted connects."""
    headers = dict(scope.get("headers") or [])
    ip = headers.get(b"cf-connecting-ip", b"").decode("utf-8", errors="replace").strip()
    return ip or None


def _bucket_ok(
    buckets: dict[str, tuple[float, float]],
    ip: str,
    burst: float,
    refill_per_sec: float,
) -> bool:
    """Consume one token from a per-IP bucket. True if the request may proceed."""
    now = time.monotonic()
    with _connect_lock:
        if len(buckets) > 10000:
            stale = [k for k, (_, last) in buckets.items() if now - last > 600]
            for k in stale:
                del buckets[k]
        tokens, last = buckets.get(ip, (burst, now))
        tokens = min(burst, tokens + (now - last) * refill_per_sec)
        if tokens >= 1.0:
            buckets[ip] = (tokens - 1.0, now)
            return True
        buckets[ip] = (tokens, now)
        return False


def _connect_rate_ok(ip: str) -> bool:
    """Consume one connect token for ip. True if the connect may proceed."""
    return _bucket_ok(_connect_buckets, ip, _SSE_CONNECT_BURST, _SSE_CONNECT_REFILL_PER_SEC)


# Claude-bridge rate limits: the MCP endpoint sees one POST per tool call
# (legitimately chatty — generous bucket); the OAuth + discovery endpoints
# see a handful of requests per handshake (strict bucket; this is the
# surface the #4030 retry loop hammered).
_CLAUDE_MCP_BURST = float(os.environ.get("CLAUDE_MCP_BURST", "30"))
_CLAUDE_MCP_REFILL_PER_SEC = float(os.environ.get("CLAUDE_MCP_PER_MIN", "120")) / 60.0
_CLAUDE_OAUTH_BURST = float(os.environ.get("CLAUDE_OAUTH_BURST", "10"))
_CLAUDE_OAUTH_REFILL_PER_SEC = float(os.environ.get("CLAUDE_OAUTH_PER_MIN", "30")) / 60.0
_claude_mcp_buckets: dict[str, tuple[float, float]] = {}
_claude_oauth_buckets: dict[str, tuple[float, float]] = {}


def _claude_rate_ok(ip: str, path: str) -> bool:
    if path == "/claude/mcp":
        return _bucket_ok(_claude_mcp_buckets, ip, _CLAUDE_MCP_BURST, _CLAUDE_MCP_REFILL_PER_SEC)
    return _bucket_ok(_claude_oauth_buckets, ip, _CLAUDE_OAUTH_BURST, _CLAUDE_OAUTH_REFILL_PER_SEC)


async def _send_429(send) -> None:
    body = b'{"error":"Too Many Requests","detail":"Per-IP request rate exceeded. Back off and retry."}'
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", b"30"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


_RATE_LIMITED_CONNECT_PATHS = ("/sse", "/openai/sse", "/grok/sse")


def _expected_token() -> str:
    return os.environ.get("BRIDGE_TOKEN", "")


def _allow_unauthenticated() -> bool:
    return os.environ.get("SSE_ALLOW_UNAUTHENTICATED", "").strip().lower() == "true"


def _first_header(scope: dict, name: bytes) -> bytes:
    """
    Return the FIRST occurrence of a header, or b'' if absent.

    Deliberately not dict(scope["headers"]) — that collapses duplicates
    LAST-wins, while bridge_core.identity_gate._extract_bearer_token loops and
    takes FIRST-wins. Two auth paths on the same server disagreeing about which
    duplicate Authorization header is authoritative is the kind of ambiguity a
    request smuggler goes looking for. All authorization reads in this module
    are now first-wins, matching the identity gate. Legitimate traffic sends
    exactly one Authorization header, so behaviour is unchanged in practice.
    """
    for key, value in scope.get("headers") or []:
        if key == name:
            return value
    return b""


def _scope_credential(scope: dict) -> str:
    """Extract the presented credential from header or query param ('' if absent)."""
    auth = _first_header(scope, b"authorization").decode("utf-8", errors="replace")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    query = parse_qs(scope.get("query_string", b"").decode("utf-8", errors="replace"))
    return (query.get("token") or [""])[0].strip()


def _native_auth_ok(scope: dict) -> bool:
    """Gate for GET /sse. Constant-time compare; fail-closed when unconfigured."""
    expected = _expected_token()
    if not expected:
        if _allow_unauthenticated():
            logger.warning("BRIDGE_TOKEN not set — /sse unauthenticated (explicit opt-in)")
            return True
        logger.error("BRIDGE_TOKEN not set and no SSE_ALLOW_UNAUTHENTICATED opt-in — refusing /sse")
        return False
    presented = _scope_credential(scope)
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)


def _bridge_auth_ok(scope: dict) -> bool:
    """Return True if the request carries a valid BRIDGE_TOKEN bearer credential."""
    expected = _expected_token()
    if not expected:
        if _allow_unauthenticated():
            logger.warning("BRIDGE_TOKEN not set — /openai/sse unauthenticated (explicit opt-in)")
            return True
        logger.error("BRIDGE_TOKEN not set — refusing /openai/sse (fail-closed)")
        return False
    auth = _first_header(scope, b"authorization").decode("utf-8", errors="replace")
    if auth.startswith("Bearer "):
        return hmac.compare_digest(auth[7:].strip(), expected)
    return False


async def _send_401(send, detail: str = "Valid Bearer token required for /openai/sse") -> None:
    body = json.dumps({"error": "Unauthorized", "detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ── POST /messages: door check + principal binding (GHSA-jpw9-pfvf-9f58) ─────
#
# Two checks, in this order. The order is load-bearing.
#
#   1. DOOR. The same credential check that guards GET /sse now guards
#      POST /messages. This is the fix. A stolen session_id on its own stops
#      being sufficient to drive a session. It matches the in-repo precedent
#      exactly — /grok/messages and /openai/messages have run their door check
#      on the POST leg since the day those bridges landed, and both connectors
#      work in production, which is the evidence that real remote MCP SSE
#      clients do present their credential on the POST leg.
#
#   2. BINDING. The first POST that PASSES THE DOOR binds the session_id to a
#      digest of the credential it presented. Every later POST for that
#      session must present the same principal, compared constant-time.
#
# Why first-use binding is not trust-on-first-use: TOFU is a hole when the
# first use is unauthenticated. Here it cannot be — check 1 runs first and
# returns before any binding is read or written, and the middleware is the
# ONLY route to sse.handle_post_message (the inner Starlette app routes just
# /health and the three /*/info paths; there is no Route or Mount for
# /messages, so /messages/ and every other variant falls through to Starlette
# and 404s without reaching the transport). An attacker holding a leaked
# session_id but no credential never reaches binding at all. An attacker
# holding a valid credential gains nothing from stealing a session id — they
# can open their own.
#
# The session_id is parsed with Starlette's QueryParams, which is what
# mcp.server.sse.handle_post_message uses. This is NOT interchangeable with
# parse_qs: on a duplicated parameter Starlette's .get() returns the LAST
# value and parse_qs()[0] returns the FIRST. A gate that disagreed with the
# transport about which session a request names would let
# "?session_id=<mine>&session_id=<victim>" pass a binding check against the
# attacker's own session and then be delivered into the victim's. Parsing the
# way the consumer parses makes that divergence impossible by construction.

# Kill switch, same shape as SSE_ALLOW_UNAUTHENTICATED: exact "true" opts out.
# Reverts BOTH checks — POST /messages behaves exactly as it did before this
# change. Rejections are still logged while disengaged, so the rollout is
# "deploy with the switch on, watch the log for would-be rejections, then
# unset it" with no second code path to test.
_MESSAGES_KILL_SWITCH_ENV = "SSE_ALLOW_UNVERIFIED_MESSAGES"

# Hard cap on the binding map. mcp 1.26.0 NEVER removes entries from its own
# _read_stream_writers (no pop/del anywhere in the file — 1.27.2 adds one), so
# session ids are immortal for the process lifetime. This map must therefore
# bound itself rather than inherit upstream's lifetime. Oldest-first eviction
# on a dict, which preserves insertion order.
#
# Sized against MEASURED churn, not a guess: ~/.sovereign/sse.log shows 6,166
# connects and 18,669 POSTs in a 2h01m process lifetime — ~3,050 sessions/hour
# at 3.03 POSTs each, because the REST bridge opens a fresh sse_client per
# /api/call. At that rate a 4,096-entry cap recycles every ~80 minutes, which
# would make eviction the steady state. 16,384 covers ~5.4 hours of that churn
# for roughly 5MB.
#
# What eviction costs, stated honestly: a session's own POSTs land within
# milliseconds of each other, so binding is a real match for the traffic that
# actually exists. Only a session still alive after 16,384 NEWER sessions can
# be evicted, and its next POST simply re-binds — after passing the door. So
# the property degrades to "door check only", which is still the whole CVE fix.
# Binding is defence in depth on top of the door, never a substitute for it.
_BINDING_MAX_DEFAULT = 16384
_BINDING_MAX_FLOOR = 256


def _read_binding_max() -> int:
    """
    Read the cap without introducing two new outage modes.

    A bare int(os.environ[...]) at import gave the knob that documents this
    change two ways to take the service down, both flagged in review:
      * SSE_BINDING_MAX_SESSIONS=0 makes the eviction loop pop until the map is
        empty and then re-bind every time, i.e. POST /messages never settles.
      * a non-integer value raises at MODULE IMPORT, and the sovereign-sse job
        has KeepAlive true, so a typo is a permanent crash loop rather than a
        bad setting.
    A misconfigured safety knob must never be worse than the default. Both
    cases now log loudly and fall back.
    """
    raw = os.environ.get("SSE_BINDING_MAX_SESSIONS", "").strip()
    if not raw:
        return _BINDING_MAX_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        logger.error(
            "SSE_BINDING_MAX_SESSIONS=%r is not an integer — using default %d",
            raw,
            _BINDING_MAX_DEFAULT,
        )
        return _BINDING_MAX_DEFAULT
    if value < _BINDING_MAX_FLOOR:
        logger.error(
            "SSE_BINDING_MAX_SESSIONS=%d is below the floor %d — clamping",
            value,
            _BINDING_MAX_FLOOR,
        )
        return _BINDING_MAX_FLOOR
    return value


_MESSAGES_BINDING_MAX = _read_binding_max()

# Per-process random salt. The map never holds anything derived from the token
# that is stable across restarts or comparable against an offline guess.
_PRINCIPAL_SALT = secrets.token_bytes(32)

_session_principals: dict[str, str] = {}
_binding_lock = threading.Lock()

_MESSAGES_ALLOW = "allow"
_MESSAGES_DENY_DOOR = "door"
_MESSAGES_DENY_BINDING = "binding"


def _messages_gate_disabled() -> bool:
    """Kill switch for the POST /messages checks. Exact 'true' opts out."""
    return os.environ.get(_MESSAGES_KILL_SWITCH_ENV, "").strip().lower() == "true"


def _principal_digest(scope: dict) -> str:
    """Salted, non-reversible id for the credential presented on this request."""
    presented = _scope_credential(scope)
    return hashlib.sha256(_PRINCIPAL_SALT + presented.encode("utf-8")).hexdigest()


def _scope_session_id(scope: dict) -> str:
    """
    The session_id this request names, parsed the way the mcp transport parses
    it. Must stay Starlette QueryParams — see the note above on duplicate
    parameters. Returns '' when absent.
    """
    return Request(scope).query_params.get("session_id") or ""


def _canonical_session_id(raw: str) -> str | None:
    """
    Normalize a session_id EXACTLY as the consumer does, or refuse it.

    This is the whole correction to the first cut of this binding, and three
    independent reviewers found the same hole in it. The mcp transport resolves
    a session with `UUID(hex=session_id_param)` (mcp/server/sse.py:217), which
    accepts urn:/uuid: prefixes, braces and any dash placement, case-insensitively.
    Keying our map on the RAW caller-supplied string while the transport keys on
    the PARSED uuid meant six spellings of one live session produced six separate
    bindings that all routed to the same writer — so an attacker binds an alias
    and delivers into a victim's bound session. Parsing the way the consumer
    parses is the invariant; anything else is a divergence waiting to be found.

    Parsing also fixes the key SIZE by construction, which closes a second
    finding: the cap bounds ENTRY COUNT, not bytes, so an 8 KB session_id gave
    ~134 MB at cap instead of the documented ~3 MB. A uuid cannot be 8 KB.

    Returns None for anything the transport itself would reject — it answers 400
    on an unparseable id, so refusing here is the same refusal one step earlier.
    """
    try:
        return str(UUID(hex=raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _binding_ok(session_id: str, digest: str) -> bool:
    """
    Bind session_id → principal on first authenticated use; require an exact
    match thereafter. Callers MUST have passed the door check first.

    Fail-closed: an unnamed or unparseable session cannot be bound or matched,
    so it is refused rather than waved through.
    """
    if not session_id:
        return False
    canonical = _canonical_session_id(session_id)
    if canonical is None:
        return False
    with _binding_lock:
        known = _session_principals.get(canonical)
        if known is None:
            while len(_session_principals) >= _MESSAGES_BINDING_MAX:
                _session_principals.pop(next(iter(_session_principals)))
            _session_principals[canonical] = digest
            return True
        return hmac.compare_digest(known, digest)


def _messages_verdict(scope: dict) -> str:
    """Decide whether a POST /messages may reach the mcp transport."""
    if not _native_auth_ok(scope):
        return _MESSAGES_DENY_DOOR
    if not _binding_ok(_scope_session_id(scope), _principal_digest(scope)):
        return _MESSAGES_DENY_BINDING
    return _MESSAGES_ALLOW


def _log_messages_rejection(scope: dict, verdict: str, enforced: bool) -> None:
    """
    Record a would-be rejection with enough detail to identify the client.
    Never logs the credential itself — only which form carried it.

    This is what makes the observe-first rollout real: with the kill switch
    engaged the same line fires and nothing is refused, so Anthony can watch
    for a client this change would break before enforcing.
    """
    credential_form = "absent"
    if _first_header(scope, b"authorization"):
        credential_form = "header"
    elif parse_qs(scope.get("query_string", b"").decode("utf-8", errors="replace")).get("token"):
        credential_form = "query"
    session_id = _scope_session_id(scope)
    logger.warning(
        "POST /messages %s verdict=%s credential=%s session=%s cf_ip=%s ua=%s client=%s",
        "REJECTED" if enforced else "would-reject (kill switch engaged)",
        verdict,
        credential_form,
        (session_id[:8] + "…") if session_id else "(none)",
        _public_ip(scope) or "(local)",
        _first_header(scope, b"user-agent").decode("utf-8", errors="replace")[:120] or "(none)",
        scope.get("client"),
    )


async def _send_no_session_404(scope, receive, send) -> None:
    """
    The binding-mismatch response. Byte-identical to the unknown-session 404
    that mcp.server.sse.handle_post_message returns at sse.py:227, by using
    the same construction. Deliberately indistinguishable: a caller holding a
    valid credential learns nothing about which sessions exist, and a
    connector is not bounced into an OAuth re-auth loop by a 401.
    """
    await Response("Could not find session", status_code=404)(scope, receive, send)


# ── OpenAI bridge request diagnostics ─────────────────────────────────────────
# Added 2026-05-20 to diagnose a ChatGPT MCP-connector failure (200 on discovery,
# 401/400/404 on invocation). Logs the headers that distinguish a transport
# mismatch (Mcp-Session-Id / MCP-Protocol-Version present, Streamable-HTTP Accept)
# from an auth-drop (bearer absent on retry). NEVER logs the bearer value — only
# its presence and scheme. Remove or gate behind a flag once the issue is closed.


def _log_openai_request_headers(scope: dict) -> None:
    """Log diagnostic headers for an /openai/* request. Bearer value redacted."""
    try:
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1", errors="replace")
            for k, v in scope.get("headers", [])
        }
        method = scope.get("method", "?")
        path = scope.get("path", "?")
        query = scope.get("query_string", b"").decode("latin-1", errors="replace")

        auth_raw = headers.get("authorization", "")
        if auth_raw:
            scheme = auth_raw.split(" ", 1)[0] if " " in auth_raw else auth_raw
            auth_repr = f"present(scheme={scheme},len={len(auth_raw)})"
        else:
            auth_repr = "ABSENT"

        diag = {
            "method": method,
            "path": path,
            "query": query or "(none)",
            "authorization": auth_repr,
            "mcp-session-id": headers.get("mcp-session-id", "(none)"),
            "mcp-protocol-version": headers.get("mcp-protocol-version", "(none)"),
            "accept": headers.get("accept", "(none)"),
            "content-type": headers.get("content-type", "(none)"),
            "user-agent": headers.get("user-agent", "(none)")[:120],
        }
        logger.info("OPENAI_DIAG %s", json.dumps(diag))
    except Exception as exc:  # diagnostics must never break a request
        logger.warning("OPENAI_DIAG failed: %s", exc)


# Wrap the Starlette app to intercept /messages POST before Starlette routing.
# handle_post_message is a raw ASGI handler (scope, receive, send) that writes
# responses directly — it returns None. Starlette Route expects a Response object,
# so we bypass Starlette for this path.
class SovereignAsgiMiddleware:
    """ASGI middleware that intercepts SSE and JSON-RPC paths before Starlette routing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        method = scope.get("method", "")

        # Diagnostic: log headers for any /openai/* request (bearer redacted).
        # Added 2026-05-20 to diagnose the ChatGPT connector handshake failure.
        if scope.get("type") == "http" and path.startswith("/openai/"):
            _log_openai_request_headers(scope)

        # Public connect-rate limit, before any auth work.
        if scope.get("type") == "http" and method == "GET" and path in _RATE_LIMITED_CONNECT_PATHS:
            client_ip = _public_ip(scope)
            if client_ip and not _connect_rate_ok(client_ip):
                logger.warning(f"429 connect-rate limit for {client_ip} on {path}")
                await _send_429(send)
                return

        # Claude-bridge rate limit (all methods — Streamable HTTP is POST-heavy
        # and the OAuth/discovery surface is the #4030 retry-loop target).
        if scope.get("type") == "http" and (
            path.startswith("/claude/")
            or path in _CLAUDE_ROOT_AS_META_PATHS
            or path in _CLAUDE_ROOT_PR_META_PATHS
        ):
            client_ip = _public_ip(scope)
            if client_ip and not _claude_rate_ok(client_ip, path):
                logger.warning(f"429 claude-bridge rate limit for {client_ip} on {path}")
                await _send_429(send)
                return

        if scope["type"] == "http" and path == "/messages" and method == "POST":
            # GHSA-jpw9-pfvf-9f58: door first, then session→principal binding.
            verdict = _messages_verdict(scope)
            if verdict != _MESSAGES_ALLOW:
                enforced = not _messages_gate_disabled()
                _log_messages_rejection(scope, verdict, enforced)
                if enforced:
                    if verdict == _MESSAGES_DENY_DOOR:
                        # 401 at the door, matching the /openai/messages and
                        # /grok/messages precedent — a credential failure says
                        # nothing about which sessions exist.
                        await _send_401(
                            send,
                            "Credential required for /messages: "
                            "Authorization: Bearer <token> or ?token=<token>",
                        )
                    else:
                        # 404 on a binding mismatch — see _send_no_session_404.
                        await _send_no_session_404(scope, receive, send)
                    return
            logger.info("Message received")
            await sse.handle_post_message(scope, receive, send)
        elif scope["type"] == "http" and path == "/sse" and method == "GET":
            if not _native_auth_ok(scope):
                logger.warning(f"Rejected unauthenticated /sse connect from {scope.get('client')}")
                await _send_401(
                    send,
                    "Credential required for /sse: Authorization: Bearer <token> or ?token=<token>",
                )
                return
            logger.info(f"New SSE connection from {scope.get('client')}")
            async with sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
                await sovereign_server.run(
                    read_stream,
                    write_stream,
                    sovereign_server.create_initialization_options(),
                    raise_exceptions=True,
                )
        elif _BRIDGE_ENABLED and path == "/openai/sse" and method == "GET":
            _gate = _verify_at_door(
                scope, expected_substrate="chatgpt-openai-bridge", transport="sse"
            )
            if not _gate.allowed:
                await _gate_send_401(
                    send,
                    _gate.reason or "Unauthorized",
                    realm="Sovereign Stack OpenAI Bridge",
                    resource_metadata_url="https://stack.templetwo.com/openai/.well-known/oauth-protected-resource",
                )
            else:
                await handle_openai_sse(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/messages" and method == "POST":
            _gate = _verify_at_door(
                scope, expected_substrate="chatgpt-openai-bridge", transport="sse"
            )
            if not _gate.allowed:
                await _gate_send_401(
                    send,
                    _gate.reason or "Unauthorized",
                    realm="Sovereign Stack OpenAI Bridge",
                    resource_metadata_url="https://stack.templetwo.com/openai/.well-known/oauth-protected-resource",
                )
            else:
                await handle_openai_messages(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/sse-test" and method == "GET":
            await handle_openai_sse_test(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/messages-test" and method == "POST":
            await handle_openai_messages_test(scope, receive, send)
        elif _GROK_BRIDGE_ENABLED and path == "/grok/sse" and method == "GET":
            await handle_grok_sse(scope, receive, send)
        elif _GROK_BRIDGE_ENABLED and path == "/grok/messages" and method == "POST":
            await handle_grok_messages(scope, receive, send)
        elif _GROK_BRIDGE_ENABLED and path == "/grok/oauth/authorize":
            # GET shows consent page; POST receives consent submission
            await handle_grok_oauth_authorize(scope, receive, send)
        elif _GROK_BRIDGE_ENABLED and path == "/grok/oauth/token" and method == "POST":
            await handle_grok_oauth_token(scope, receive, send)
        elif (
            _GROK_BRIDGE_ENABLED
            and path == "/grok/.well-known/oauth-authorization-server"
            and method == "GET"
        ):
            await handle_grok_oauth_as_meta(scope, receive, send)
        elif (
            _GROK_BRIDGE_ENABLED
            and path == "/grok/.well-known/oauth-protected-resource"
            and method == "GET"
        ):
            await handle_grok_oauth_pr_meta(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/oauth/authorize":
            # GET shows consent page; POST receives consent submission
            await handle_openai_oauth_authorize(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/oauth/token" and method == "POST":
            await handle_openai_oauth_token(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/oauth/register" and method == "POST":
            await handle_openai_oauth_register(scope, receive, send)
        elif (
            _BRIDGE_ENABLED
            and path == "/openai/.well-known/oauth-authorization-server"
            and method == "GET"
        ):
            await handle_openai_oauth_as_meta(scope, receive, send)
        elif (
            _BRIDGE_ENABLED
            and path == "/openai/.well-known/oauth-protected-resource"
            and method == "GET"
        ):
            await handle_openai_oauth_pr_meta(scope, receive, send)
        elif (
            _CLAUDE_BRIDGE_ENABLED and path == "/claude/mcp" and method in ("POST", "GET", "DELETE")
        ):
            # Streamable HTTP: one path, three methods. Auth (audience-bound
            # bearer) is enforced inside handle_claude_mcp before the session
            # manager ever sees the request.
            await handle_claude_mcp(scope, receive, send)
        elif (
            _CLAUDE_BRIDGE_ENABLED and path == "/claude/oauth/authorize/status" and method == "GET"
        ):
            # Poll target for the phone-tap waiting page; proxies to the
            # bridge's read-only approval-status oracle. Must be routed
            # before the /claude/oauth/authorize branch below, else the
            # more general path never matches this more specific one.
            await handle_claude_oauth_authorize_status(scope, receive, send)
        elif _CLAUDE_BRIDGE_ENABLED and path == "/claude/oauth/authorize":
            # GET renders the phone-tap waiting page; POST receives the
            # completion submit once the waiting page's poll sees "approved".
            await handle_claude_oauth_authorize(scope, receive, send)
        elif _CLAUDE_BRIDGE_ENABLED and path == "/claude/oauth/token" and method == "POST":
            await handle_claude_oauth_token(scope, receive, send)
        elif _CLAUDE_BRIDGE_ENABLED and path == "/claude/oauth/register" and method == "POST":
            await handle_claude_oauth_register(scope, receive, send)
        elif _CLAUDE_BRIDGE_ENABLED and path == "/claude/oauth/revoke" and method == "POST":
            await handle_claude_oauth_revoke(scope, receive, send)
        elif (
            _CLAUDE_BRIDGE_ENABLED
            and method == "GET"
            and path
            in (
                "/claude/.well-known/oauth-authorization-server",
                "/claude/.well-known/openid-configuration",
            )
        ):
            await handle_claude_oauth_as_meta(scope, receive, send)
        elif (
            _CLAUDE_BRIDGE_ENABLED
            and path == "/claude/.well-known/oauth-protected-resource"
            and method == "GET"
        ):
            await handle_claude_oauth_pr_meta(scope, receive, send)
        elif _CLAUDE_BRIDGE_ENABLED and method == "GET" and path in _CLAUDE_ROOT_AS_META_PATHS:
            await handle_claude_oauth_as_meta(scope, receive, send)
        elif _CLAUDE_BRIDGE_ENABLED and method == "GET" and path in _CLAUDE_ROOT_PR_META_PATHS:
            await handle_claude_oauth_pr_meta(scope, receive, send)
        else:
            await self.app(scope, receive, send)


async def bridge_info(request: Request) -> JSONResponse:
    """Bridge manifest — what's exposed on /openai/sse."""
    if not _BRIDGE_ENABLED:
        return JSONResponse({"error": "OpenAI bridge not loaded"}, status_code=503)
    from openai_bridge.manifest import MANIFEST

    return JSONResponse(MANIFEST)


async def grok_bridge_info(request: Request) -> JSONResponse:
    """Bridge manifest — what's exposed on /grok/sse."""
    if not _GROK_BRIDGE_ENABLED:
        return JSONResponse({"error": "Grok bridge not loaded"}, status_code=503)
    return JSONResponse(GROK_MANIFEST)


async def claude_bridge_info(request: Request) -> JSONResponse:
    """Bridge manifest — what's exposed on /claude/mcp."""
    if not _CLAUDE_BRIDGE_ENABLED:
        return JSONResponse({"error": "Claude bridge not loaded"}, status_code=503)
    return JSONResponse(CLAUDE_MANIFEST)


@asynccontextmanager
async def _lifespan(app):
    """Starlette lifespan: start the claude-bridge session manager and
    boot-launch the resident scribe at SSE startup.

    IMPORTANT: uvicorn.run() must use workers=1 (the default, never set
    workers>1). The resident scribe is a module-level in-memory singleton;
    it only holds at exactly one worker process. If workers>1, each worker
    gets its own resident and cross-worker routing breaks. (The claude
    session manager is likewise per-process state.)

    The StreamableHTTPSessionManager contract: run() must be entered exactly
    once, here, before any handle_request — otherwise the /claude/mcp route
    raises RuntimeError.

    The ensure_resident_scribe() call runs on a thread to avoid blocking the
    event loop on Anthropic API latency. Failures are logged but never fatal —
    the SSE server starts regardless.
    """
    async with AsyncExitStack() as stack:
        if _CLAUDE_BRIDGE_ENABLED:
            try:
                await stack.enter_async_context(claude_session_manager.run())
                logger.info("claude bridge streamable-http session manager started")
            except Exception as exc:
                logger.warning("claude session manager failed to start (non-fatal): %s", exc)
        try:
            from .scribe.resident import ensure_resident_scribe

            await asyncio.to_thread(ensure_resident_scribe)
            logger.info("scribe resident established at SSE boot")
        except Exception as exc:
            logger.warning("scribe resident boot-launch failed (non-fatal): %s", exc)
        yield


# Create Starlette app with SSE and health routes.
# WORKERS=1 NOTE: uvicorn.run() is called without workers= argument below,
# which defaults to 1. This is load-bearing: the resident scribe is an
# in-memory singleton that only holds at one worker.
_inner_app = Starlette(
    debug=False,
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/openai/info", bridge_info, methods=["GET"]),
        Route("/grok/info", grok_bridge_info, methods=["GET"]),
        Route("/claude/info", claude_bridge_info, methods=["GET"]),
    ],
    lifespan=_lifespan,
)

# Wrap with message handler middleware
app = SovereignAsgiMiddleware(_inner_app)


def main(host: str = "127.0.0.1", port: int = 3434):
    """
    Start SSE server for remote access

    Args:
        host: Host to bind to (default: 127.0.0.1 — tunnel handles external)
        port: Port to listen on (default: 3434)

    NOTE: uvicorn.run() does NOT pass workers=; this defaults to 1 worker.
    The resident scribe requires uvicorn workers=1 — it is an in-memory
    singleton and will not coordinate across multiple workers.
    """
    logger.info(f"Sovereign Stack SSE Server starting on {host}:{port}")
    logger.info(f"SSE endpoint: http://{host}:{port}/sse")
    logger.info(f"Health check: http://{host}:{port}/health")
    logger.info("scribe resident requires uvicorn workers=1 (singleton in-memory)")

    # workers= intentionally omitted (defaults to 1); see docstring above.
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=True)


if __name__ == "__main__":
    main()
