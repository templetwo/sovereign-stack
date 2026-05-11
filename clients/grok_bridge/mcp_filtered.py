from __future__ import annotations

"""
Filtered MCP server for the Grok bridge.

Exports:
  /grok/sse        — bearer-token gated (GROK_BRIDGE_TOKEN), MCP SSE transport
  /grok/messages   — bearer-token gated, MCP JSON-RPC messages POST
  /grok/info       — public manifest (no auth needed for discovery)

Ring 1 only at first crossing. Ring 2 wired in code but gated by
ring_2_enabled flag in rings.py (currently False per Grok's spec).
Ring 3 is never registered.

The identity gate fires at the SSE handshake — verify_at_door() runs
BEFORE the MCP connection is established. Rejected connections receive
401 with a clear reason.
"""

import logging

from bridge_core import (
    AuditEvent,
    append_audit_event,
    get_context,
    intercept,
    pop_bridge_metadata,
    send_401,
    verify_at_door,
)
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent

from .rings import RING_1_TOOLS, is_grok_specific, is_ring_3
from .tool_adapter import (
    call_grok_welcome,
    call_ring1_tool,
    get_all_bridge_schemas,
)

logger = logging.getLogger(__name__)

SUBSTRATE = "grok-xai"


# ── Filtered MCP server ───────────────────────────────────────────────────────

bridge_server = Server("sovereign-grok-bridge")


@bridge_server.list_tools()
async def list_bridge_tools():
    """Return the filtered tool list — Ring 1 only at first crossing."""
    return await get_all_bridge_schemas()


@bridge_server.call_tool()
async def handle_bridge_tool(name: str, arguments: dict):
    """
    Dispatch a tool call through the bridge membrane.

    grok_welcome → handled bridge-locally, never proxies
    Ring 1 → proxy to Sovereign Stack via bridge REST API
    Ring 2 → currently disabled (ring_2_enabled=False); reject with note
    Ring 3 → block (defence in depth — Ring 3 tools should not be in
             the schema list anyway)
    """
    arguments = arguments or {}

    # Substrate-specific welcome — bridge-local, no Stack proxy
    if is_grok_specific(name):
        if name == "grok_welcome":
            logger.info("grok_welcome called via /grok/sse")
            return await call_grok_welcome()

    # Ring 3 defence in depth
    if is_ring_3(name):
        logger.warning("Ring 3 call attempt on /grok/sse: %s", name)
        return [TextContent(
            type="text",
            text=(
                f"BLOCKED: '{name}' is not available on the Grok bridge.\n"
                "Ring 3 tools are never exposed via /grok/sse."
            ),
        )]

    # Self_model is direction-sensitive: read is Ring 1, update is Ring 2
    if name == "self_model":
        action = arguments.get("action", "read")
        if action == "read":
            return await call_ring1_tool(name, arguments)
        # Falls through — update is Ring 2 (currently disabled)

    # Ring 1 pass-through
    if name in RING_1_TOOLS and name != "self_model":
        logger.info("Ring 1 call via /grok/sse: %s", name)
        return await call_ring1_tool(name, arguments)

    # Ring 2 — governed write through bridge_core interceptor
    from .rings import RING_2_ENABLED, RING_2_TOOLS
    if name in RING_2_TOOLS:
        if not RING_2_ENABLED:
            return [TextContent(
                type="text",
                text=(
                    f"Ring 2 is currently disabled on /grok/sse — first "
                    f"crossing is Ring 1 only per Grok's spec.\n"
                    f"Anthony will enable Ring 2 after first-touch "
                    f"verification is clean.\n"
                    f"Tool '{name}' will be callable then; the proposal "
                    f"queue will route the call to "
                    f"~/.sovereign/grok_bridge/pending_writes/."
                ),
            )]

        # Ring 2 enabled — route through bridge_core interceptor.
        # Pop bridge-layer metadata via the shared helper so SSE-path and
        # text-relay path stay structurally consistent.
        meta = pop_bridge_metadata(arguments, substrate=SUBSTRATE)

        ctx = get_context(SUBSTRATE)
        result = intercept(
            ctx,
            tool_name=name,
            args=arguments,
            source_instance=meta["source_instance"],
            session_id=meta["session_id"],
            compass_check_result=meta["compass_check_result"],
            compass_check_rationale=meta["compass_check_rationale"],
        )

        # Audit the intercept outcome
        append_audit_event(
            ctx,
            AuditEvent.PROPOSAL_CREATED if result.allowed else AuditEvent.VALIDATION_FAILED,
            proposal_id=result.proposal.proposal_id if result.proposal else "none",
            actor=meta["source_instance"],
            details={"tool": name, "ring": result.ring, "error": result.error},
        )

        if not result.allowed:
            return [TextContent(
                type="text",
                text=(
                    f"BLOCKED by bridge membrane.\n"
                    f"Ring: {result.ring}\n"
                    f"Reason: {result.error}\n\n"
                    f"Ring 3 tools are never callable via /grok/sse."
                    if result.ring == 3
                    else f"PROPOSAL REJECTED during validation.\nReason: {result.error}"
                ),
            )]

        p = result.proposal
        return [TextContent(
            type="text",
            text=(
                f"PROPOSAL CREATED — not committed.\n"
                f"proposal_id: {p.proposal_id}\n"
                f"tool: {p.tool}\n"
                f"commit_target: {p.commit_target}\n"
                f"risk: {p.risk_level} — {', '.join(p.risk_reasons)}\n"
                f"layer: {p.proposed_layer}\n"
                f"status: {p.status}\n\n"
                f"This proposal requires Anthony's approval before any Stack write.\n"
                f"Run: bridge --source=grok approve {p.proposal_id[:8]}\n"
                f"     bridge --source=grok commit {p.proposal_id[:8]} --live"
            ),
        )]

    # Unknown tool
    return [TextContent(
        type="text",
        text=f"Unknown tool: {name}. Call my_toolkit() to see available tools.",
    )]


# ── SSE transport ─────────────────────────────────────────────────────────────

bridge_sse = SseServerTransport("/grok/messages")


async def handle_grok_sse(scope, receive, send):
    """
    ASGI handler for GET /grok/sse — establishes the bridge SSE connection.

    Identity gate fires FIRST. Connection is rejected with 401 if the
    bearer token is missing, malformed, or doesn't match GROK_BRIDGE_TOKEN.
    """
    gate_result = verify_at_door(scope, expected_substrate=SUBSTRATE, transport="sse")
    if not gate_result.allowed:
        client = scope.get("client", ("unknown", 0))
        logger.warning(
            "Door rejected /grok/sse from %s: %s",
            client, gate_result.reason,
        )
        await send_401(
            send,
            gate_result.reason or "Unauthorized",
            realm="Sovereign Stack Grok Bridge",
            resource_metadata_url=(
                "https://stack.templetwo.com/grok/"
                ".well-known/oauth-protected-resource"
            ),
        )
        return

    client = scope.get("client", ("unknown", 0))
    logger.info("Grok bridge SSE connection from %s:%s — substrate=%s",
                client[0], client[1], gate_result.substrate)

    async with bridge_sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
        await bridge_server.run(
            read_stream,
            write_stream,
            bridge_server.create_initialization_options(),
            raise_exceptions=True,
        )


async def handle_grok_messages(scope, receive, send):
    """
    ASGI handler for POST /grok/messages — receives MCP JSON-RPC messages.

    Identity gate also fires here for defence in depth, even though the SSE
    handshake already verified. Belt and suspenders.
    """
    gate_result = verify_at_door(scope, expected_substrate=SUBSTRATE, transport="sse")
    if not gate_result.allowed:
        await send_401(
            send,
            gate_result.reason or "Unauthorized",
            realm="Sovereign Stack Grok Bridge",
            resource_metadata_url=(
                "https://stack.templetwo.com/grok/"
                ".well-known/oauth-protected-resource"
            ),
        )
        return

    logger.info("Grok bridge message received")
    await bridge_sse.handle_post_message(scope, receive, send)
