"""
Streamable HTTP MCP route for the Claude connector — the native surface with
a gated blast radius.

Transport: mcp.server.streamable_http (the current remote transport since MCP
spec 2025-03-26; the SSE transport the other bridges use is deprecated), in
STATELESS mode — one transport per request, no server-side session registry.
Stateless is a deliberate security choice, not a shortcut:

  - Every request re-validates the bearer at the door, so revocation and
    expiry take effect on the next request, not the next session.
  - The auth grant travels to the tool dispatch via a contextvar set in the
    request task; the SDK spawns the per-request server task from within
    that request task, so the contextvars copy is correct per request.
    (In stateful mode the server task is created once per SESSION at
    initialize time, which would freeze the first request's auth context —
    wrong for step-up, wrong for rotation.)
  - Unauthenticated requests are rejected BEFORE the session manager sees
    them, so discovery storms and junk requests (the #4030 shape) can never
    allocate transports or tasks.

Surface: a dedicated lowlevel Server ("sovereign-stack-claude") that delegates
list_tools and call_tool to the NATIVE handlers in sovereign_stack.server —
the full 94-tool surface, not a ring-filtered subset. The delegation wrapper
is where the destructive-tier gate lives; the native /sse and stdio transports
never pass through it.
"""

from __future__ import annotations

import contextvars
import json
import logging
import urllib.parse

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent

from . import elevation, oauth, tiers

logger = logging.getLogger(__name__)

# The authenticated grant for the current request, set by the ASGI gate before
# the session manager runs and inherited by the per-request server task.
_current_grant: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "claude_bridge_grant", default=None
)


def _native():
    """Late import of the native handlers — avoids import-order coupling with
    sse_server (which imports sovereign_stack.server before this package)."""
    from sovereign_stack.server import handle_tool, list_tools

    return list_tools, handle_tool


_registry_names: frozenset[str] | None = None


async def _known_tool(name: str) -> bool:
    """True iff `name` is a real tool in the live native registry. Cached — the
    registry is static after import. Used to reject fabricated tool names BEFORE
    tier classification, so an attacker cannot author the Door approval text via
    an arbitrary tool name (a bogus name never reaches the Door)."""
    global _registry_names
    if _registry_names is None:
        native_list_tools, _ = _native()
        tools = await native_list_tools()
        _registry_names = frozenset(t.name for t in tools)
    return name in _registry_names


def _err(payload: dict):
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


claude_server: Server = Server("sovereign-stack-claude")


@claude_server.list_tools()
async def claude_list_tools():
    native_list_tools, _ = _native()
    return await native_list_tools()


@claude_server.call_tool()
async def claude_call_tool(name: str, arguments: dict):
    arguments = arguments or {}
    grant = _current_grant.get()
    if grant is None:
        # The gate always sets a grant before handle_request; reaching this
        # means a wiring bug. Fail closed, loudly.
        logger.error("claude_call_tool reached with no grant bound — refusing %s", name)
        return _err(
            {
                "error": "no_grant_bound",
                "detail": "Internal gate error: no authenticated grant for this request.",
            }
        )

    # Reject fabricated tool names before anything else: a name that is not a
    # real registered tool never reaches the tier gate or the Door push.
    if not await _known_tool(name):
        return _err({"error": "method_not_found", "detail": f"Unknown tool: {name}"})

    if tiers.classify(name) == tiers.TIER_STEP_UP:
        summary, req_hash = elevation.summarize_and_hash(name, arguments)
        status = await elevation.ensure_elevation(
            tool=name,
            family_id=grant.get("family_id", ""),
            client_id=grant.get("client_id", ""),
            req_hash=req_hash,
            summary=summary,
        )
        if status.state != "active":
            # Structured refusal instead of an exception: the calling model
            # reads this, relays the pairing code to the human, and re-calls
            # the tool after approval. An MCP client cannot block on a human.
            # Fold in the per-tool WHY (e.g. where_did_i_leave_off taps because
            # it CONSUMES handoffs meant for whoever boots next at HQ) ahead of
            # the generic Door messaging, keeping the {error, tool, state,
            # pairing_code, detail} shape intact.
            detail = status.detail
            reason = tiers.step_up_reason(name)
            if reason:
                detail = f"{reason} {detail}"
            return _err(
                {
                    "error": "step_up_required",
                    "tool": name,
                    "state": status.state,
                    "pairing_code": status.code,
                    "detail": detail,
                }
            )
        # Consume the single-use elevation as we execute (per-use, not
        # per-window): the next call re-prompts for a fresh tap.
        elevation.record_destructive_execution(
            tool=name,
            family_id=grant.get("family_id", ""),
            client_id=grant.get("client_id", ""),
            req_hash=req_hash,
        )

    _, native_handle_tool = _native()
    return await native_handle_tool(name, arguments)


# One manager per process; run() must be entered exactly once, inside the
# sse_server lifespan. security_settings=None: DNS-rebinding protection stays
# off (status quo behind the Cloudflare tunnel); constructing a bare
# TransportSecuritySettings() would 421 everything — do not "enable" casually.
session_manager = StreamableHTTPSessionManager(
    app=claude_server,
    event_store=None,
    json_response=False,
    stateless=True,
    security_settings=None,
)


def _bearer_from_scope(scope: dict) -> str:
    """Header-only credential extraction (house rule for bridge paths: no
    ?token= query form — OAuth clients always send the Authorization header,
    and query strings leak into logs)."""
    headers = dict(scope.get("headers") or [])
    auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ""


async def handle_claude_mcp(scope, receive, send) -> None:
    """
    ASGI entry for POST/GET/DELETE /claude/mcp.

    Auth gate first (RFC 8707 enforcement point — load_valid_access refuses
    any token whose audience is not this endpoint), then the grant is bound
    for the request and the session manager takes over.
    """
    from bridge_core import send_401  # late import mirrors sse_server's pattern

    token = _bearer_from_scope(scope)
    record = oauth.load_valid_access(token)
    if record is None:
        # RFC 9728 path-insertion form — /.well-known/oauth-protected-resource
        # inserted between the host and the resource path. Derived from the
        # canonical resource URL itself so it stays correct under any
        # CLAUDE_BRIDGE_ISSUER override.
        parts = urllib.parse.urlsplit(oauth.CANONICAL_RESOURCE)
        metadata_url = (
            f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource{parts.path}"
        )
        await send_401(
            send,
            "Valid audience-bound Bearer token required for /claude/mcp",
            realm="Sovereign Stack Claude Bridge",
            resource_metadata_url=metadata_url,
        )
        return

    grant = {
        "family_id": record.get("family_id", ""),
        "client_id": record.get("client_id", ""),
        "scope": record.get("scope", ""),
    }
    ctx_token = _current_grant.set(grant)
    try:
        await session_manager.handle_request(scope, receive, send)
    finally:
        # Resets only this request task's view; the spawned server task holds
        # its own contextvars copy for the lifetime of the request.
        _current_grant.reset(ctx_token)
