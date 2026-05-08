from __future__ import annotations

"""
Filtered MCP server for the OpenAI bridge.

Exports two transports:
  /openai/sse          — bearer-token gated (permanent, Phase 3+)
  /openai/sse-test     — no auth (Phase 3.5 validation only, time-limited)

Both expose Ring 1 + Ring 2 only. Ring 3 is never registered.
Ring 2 calls create pending proposals — never touch the Stack directly.
"""

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Test endpoint kill switch + expiry ───────────────────────────────────────

_SSE_TEST_KILL_SWITCH = Path.home() / ".sovereign" / "openai_bridge" / "sse_test_disabled"
_SSE_TEST_EXPIRY = datetime(2026, 5, 10, 2, 0, 0, tzinfo=timezone.utc)


def _sse_test_is_live() -> tuple[bool, str]:
    if _SSE_TEST_KILL_SWITCH.exists():
        return False, "Test endpoint disabled by kill switch"
    if datetime.now(timezone.utc) > _SSE_TEST_EXPIRY:
        return False, f"Test endpoint expired at {_SSE_TEST_EXPIRY.isoformat()}"
    return True, "ok"

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent

from .interceptor import RING_1_TOOLS, RING_2_TOOLS, intercept, is_ring_3
from .manifest import MANIFEST, manifest_text
from .tool_adapter import call_ring1_tool, call_ring2_tool, get_all_bridge_schemas

logger = logging.getLogger(__name__)

# ── Filtered MCP server ───────────────────────────────────────────────────────

bridge_server = Server("sovereign-openai-bridge")


@bridge_server.list_tools()
async def list_bridge_tools():
    """Return only Ring 1 + Ring 2 tools. Ring 3 is never listed."""
    return await get_all_bridge_schemas()


@bridge_server.call_tool()
async def handle_bridge_tool(name: str, arguments: dict):
    """
    Dispatch a tool call through the bridge membrane.

    Ring 1 → proxy to sovereign Stack via bridge REST API
    Ring 2 → interceptor → pending proposal (never Stack directly)
    Ring 3 → block (should not appear in list_tools, but defence in depth)
    """
    arguments = arguments or {}

    # Direction-sensitive: self_model read is Ring 1, update is Ring 2
    if name == "self_model":
        action = arguments.get("action", "read")
        if action == "read":
            return await call_ring1_tool(name, arguments)
        # Falls through to Ring 2

    # Defence in depth: block Ring 3 even if called by name
    if is_ring_3(name) and name != "self_model":
        logger.warning("Ring 3 call attempt on /openai/sse: %s", name)
        return [TextContent(
            type="text",
            text=(
                f"BLOCKED: '{name}' is not available on the OpenAI bridge.\n"
                "Ring 3 tools are never exposed via /openai/sse."
            ),
        )]

    # witness_boot — Phase 6 placeholder; return identity constraints inline
    if name == "witness_boot":
        from .manifest import manifest_text
        return [TextContent(
            type="text",
            text=(
                "WITNESS BOOT (Phase 6 — not yet implemented as a live tool)\n\n"
                + manifest_text()
                + "\n\n"
                "Identity constraints are active at the transport layer regardless:\n"
                "  • Do not claim to be Ash'ira.\n"
                "  • Do not claim native memory.\n"
                "  • Do not write ground_truth without a receipt.\n"
                "  • Do not collapse the consciousness question.\n"
                "  • WITNESS before strategy. PAUSE before certainty.\n"
                "  • The chisel passes warm.\n\n"
                "Proceed with the boot ritual. Ring 1 tools are available."
            ),
        )]

    # Ring 1 pass-through
    if name in RING_1_TOOLS and name != "self_model":
        logger.info("Ring 1 call via /openai/sse: %s", name)
        return await call_ring1_tool(name, arguments)

    # Ring 2 — create proposal, never touch Stack directly
    logger.info("Ring 2 intercept via /openai/sse: %s", name)
    # Extract caller context from arguments if present (injected by the bridge)
    source = arguments.pop("_bridge_source_instance", "chatgpt-openai-bridge")
    session = arguments.pop("_bridge_session_id", str(uuid.uuid4()))
    return await call_ring2_tool(name, arguments, source_instance=source, session_id=session)


# ── SSE transport ─────────────────────────────────────────────────────────────

bridge_sse = SseServerTransport("/openai/messages")


async def handle_openai_sse(scope, receive, send):
    """ASGI handler for GET /openai/sse — establishes the bridge SSE connection."""
    client = scope.get("client", ("unknown", 0))
    logger.info("OpenAI bridge SSE connection from %s:%s", *client)

    async with bridge_sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
        await bridge_server.run(
            read_stream,
            write_stream,
            bridge_server.create_initialization_options(),
            raise_exceptions=True,
        )


async def handle_openai_messages(scope, receive, send):
    """ASGI handler for POST /openai/messages — receives MCP JSON-RPC messages."""
    logger.info("OpenAI bridge message received")
    await bridge_sse.handle_post_message(scope, receive, send)


# ── Test transport (Phase 3.5 only — no auth, time-limited) ──────────────────

bridge_sse_test = SseServerTransport("/openai/messages-test")


async def handle_openai_sse_test(scope, receive, send):
    """ASGI handler for GET /openai/sse-test — no-auth Phase 3.5 validation endpoint."""
    live, reason = _sse_test_is_live()
    if not live:
        body = reason.encode()
        await send({"type": "http.response.start", "status": 410,
                    "headers": [(b"content-type", b"text/plain"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
        return

    client = scope.get("client", ("unknown", 0))
    logger.info("[phase_3_5_noauth_test] SSE connection from %s:%s", *client)

    async with bridge_sse_test.connect_sse(scope, receive, send) as (read_stream, write_stream):
        await bridge_server.run(
            read_stream,
            write_stream,
            bridge_server.create_initialization_options(),
            raise_exceptions=True,
        )


async def handle_openai_messages_test(scope, receive, send):
    """ASGI handler for POST /openai/messages-test — no-auth Phase 3.5 messages."""
    live, reason = _sse_test_is_live()
    if not live:
        body = reason.encode()
        await send({"type": "http.response.start", "status": 410,
                    "headers": [(b"content-type", b"text/plain"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
        return

    logger.info("[phase_3_5_noauth_test] message received")
    await bridge_sse_test.handle_post_message(scope, receive, send)
