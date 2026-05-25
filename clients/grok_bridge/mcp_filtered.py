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

Ring 2 capability probe
───────────────────────
When PROBE_ON_CONNECT=true (env, default off), handle_grok_sse arms a
per-connection probe before handing off to bridge_server.run(). If the
arriving model calls probe_ring2_dispatch (a Ring 2 sentinel) within
PROBE_TIMEOUT_SECONDS, RING2_CAPABILITY_VERIFIED is recorded; otherwise
RING2_CAPABILITY_FAILED is recorded. In detector mode (require_ring2_probe
defaults to False on BridgeContext), a timeout never disables Ring 2 —
it only records the audit event and sets a flag. Hard-gating requires
require_ring2_probe=True on the substrate's BridgeContext, which no
substrate currently sets.

The live call-site in handle_grok_sse is gated behind PROBE_ON_CONNECT
(default off) because it requires launching a background asyncio.Task
inside the SSE coroutine, which cannot be fully exercised without a live
MCP connection. Tests cover arm/resolve/await and the sentinel dispatch
path directly.
"""

import asyncio
import contextvars
import logging
import os
import uuid

from bridge_core import (
    AuditEvent,
    append_audit_event,
    arm_probe,
    await_probe,
    get_context,
    intercept,
    list_pending_writes,
    pop_bridge_metadata,
    resolve_probe,
    send_401,
    verify_at_door,
)
from bridge_core.interceptor import verify_proposal
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

# ── Capability probe configuration ───────────────────────────────────────────

# How long (seconds) to wait for the sentinel probe_ring2_dispatch to arrive.
# Tunable via environment variable.
PROBE_TIMEOUT_SECONDS: float = float(os.environ.get("PROBE_TIMEOUT_SECONDS", "5"))

# Feature flag: wire the probe await into the live SSE connect handler.
# Default OFF — the probe primitives are fully implemented and unit-tested but
# the live await path requires a real MCP connection to exercise safely.
# Set PROBE_ON_CONNECT=true to enable in a real deployment after verification.
_PROBE_ON_CONNECT: bool = os.environ.get("PROBE_ON_CONNECT", "false").lower() == "true"

# Per-connection id ContextVar — set in handle_grok_sse, read in handle_bridge_tool.
# ContextVar isolation means concurrent connections don't share the same value.
_connection_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_grok_connection_id", default=None
)

# Per-session Ring 2 disable flag — keyed by connection_id.
# Only populated when require_ring2_probe=True and probe fails.
# Never mutates global RING_2_ENABLED; scoped to this connection only.
_ring2_disabled_for_connection: set[str] = set()

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

    # Ring 1 pass-through — with bridge-local handlers for queue-verification tools
    # that read from the local pending_writes_dir rather than proxying to the Stack.
    if name == "verify_proposal":
        logger.info("Ring 1 verify_proposal call via /grok/sse")
        proposal_id = arguments.get("proposal_id", "").strip()
        if not proposal_id:
            return [TextContent(
                type="text",
                text="verify_proposal error: proposal_id is required.",
            )]
        ctx = get_context(SUBSTRATE)
        vr = verify_proposal(ctx, proposal_id)
        if vr.get("found"):
            text = (
                f"FOUND — proposal exists in the pending-writes queue.\n"
                f"proposal_id : {vr['proposal_id']}\n"
                f"tool        : {vr['tool']}\n"
                f"status      : {vr['status']}\n"
                f"substrate   : {vr['substrate']}\n"
                f"risk_level  : {vr['risk_level']}\n"
                f"timestamp   : {vr['timestamp']}\n"
                f"chain_valid : {vr['chain_valid']}\n"
                f"audit_hash  : {vr['audit_hash'][:16]}...\n"
                + (f"error       : {vr['error']}" if vr.get("error") else "")
            )
        else:
            text = (
                f"NOT FOUND — no proposal with id '{proposal_id}' exists in the "
                f"pending-writes queue.\n"
                f"This means the Ring 2 write was NOT executed — a narrated write is "
                f"not the same as a real write.\n"
                f"error: {vr.get('error', 'not_found')}"
            )
        return [TextContent(type="text", text=text.strip())]

    if name == "list_bridge_proposals":
        logger.info("Ring 1 list_bridge_proposals call via /grok/sse")
        status_filter = arguments.get("status", "pending") or "pending"
        limit = int(arguments.get("limit", 10) or 10)
        ctx = get_context(SUBSTRATE)
        proposals = list_pending_writes(ctx, status=status_filter)[:limit]
        if not proposals:
            text = f"No proposals found with status='{status_filter}' on {SUBSTRATE}."
        else:
            lines = [
                f"{len(proposals)} proposal(s) with status='{status_filter}' on {SUBSTRATE}:\n"
            ]
            for p in proposals:
                lines.append(
                    f"  [{p['risk_level'].upper():8s}] {p['proposal_id'][:8]}  "
                    f"{p['tool']:30s}  {p['timestamp'][:19]}  "
                    f"from={p['source_instance']}"
                )
            text = "\n".join(lines)
        return [TextContent(type="text", text=text)]

    if name in RING_1_TOOLS and name != "self_model":
        logger.info("Ring 1 call via /grok/sse: %s", name)
        return await call_ring1_tool(name, arguments)

    # ── Ring 2 capability probe sentinel ─────────────────────────────────────
    # Intercepted BEFORE the normal Ring 2 proposal-creation block.
    # probe_ring2_dispatch is Ring 2 so it travels the same dispatch path that
    # is suspected of being broken for xAI's connector — but it is a dry-run:
    # no proposal file is written, no PROPOSAL_CREATED audit event is emitted.
    if name == "probe_ring2_dispatch":
        conn_id = _connection_id_var.get()
        session_id = (arguments or {}).get("session_id", conn_id or "unknown")
        logger.info(
            "probe_ring2_dispatch sentinel arrived — connection_id=%s session_id=%s",
            conn_id, session_id,
        )
        if conn_id:
            resolved = resolve_probe(conn_id)
            logger.debug("probe: resolve_probe returned %s for conn=%s", resolved, conn_id)
        return [TextContent(
            type="text",
            text=(
                "PROBE ACK — Ring 2 dispatch confirmed.\n"
                f"connection_id: {conn_id or 'n/a'}\n"
                f"session_id: {session_id}\n"
                "No proposal was created. This is a dry-run sentinel that verifies\n"
                "your connector routes Ring 2 calls to the bridge SSE handler.\n"
                "RING2_CAPABILITY_VERIFIED will be recorded in the audit log."
            ),
        )]

    # Ring 2 — governed write through bridge_core interceptor
    from .rings import RING_2_ENABLED, RING_2_TOOLS
    if name in RING_2_TOOLS:
        # Connection-scoped Ring 2 disable — only set when require_ring2_probe=True
        # and the probe timed out for THIS connection. Global RING_2_ENABLED is
        # never mutated; other connections and the OpenAI bridge are unaffected.
        conn_id = _connection_id_var.get()
        if conn_id and conn_id in _ring2_disabled_for_connection:
            logger.warning(
                "Ring 2 disabled for connection %s (probe failed, hard-gate active): %s",
                conn_id, name,
            )
            return [TextContent(
                type="text",
                text=(
                    f"Ring 2 is disabled for this connection — the capability probe "
                    f"for probe_ring2_dispatch timed out at connect time, and "
                    f"require_ring2_probe=True is set for this substrate.\n"
                    f"Tool '{name}' cannot create a proposal in this session."
                ),
            )]

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

    # Mint a per-connection UUID so the probe registry and the per-tool dispatch
    # handler can coordinate without cross-connection leakage.
    connection_id = str(uuid.uuid4())
    _connection_id_var.set(connection_id)
    logger.debug("Grok SSE connection_id=%s", connection_id)

    async with bridge_sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
        # ── Ring 2 capability probe (PROBE_ON_CONNECT=true required) ─────────
        # TODO: Enable once a real xAI session is available to verify timing.
        # The probe await runs as a background task so bridge_server.run() starts
        # immediately — the connection is never blocked waiting for the sentinel.
        # Cleanup: the background task removes its registry entry in finally;
        # the connection_id set entry is removed when the connection closes below.
        if _PROBE_ON_CONNECT:
            ctx = get_context(SUBSTRATE)
            arm_probe(connection_id)

            async def _run_probe_in_background() -> None:
                """Background task: await sentinel, emit audit event, hard-gate if needed."""
                outcome = await await_probe(connection_id, timeout=PROBE_TIMEOUT_SECONDS)
                if outcome == "verified":
                    append_audit_event(
                        ctx,
                        AuditEvent.RING2_CAPABILITY_VERIFIED,
                        proposal_id=connection_id,
                        actor=f"probe/{SUBSTRATE}",
                        details={"connection_id": connection_id},
                    )
                    logger.info(
                        "Ring 2 capability VERIFIED for connection %s", connection_id
                    )
                else:
                    append_audit_event(
                        ctx,
                        AuditEvent.RING2_CAPABILITY_FAILED,
                        proposal_id=connection_id,
                        actor=f"probe/{SUBSTRATE}",
                        details={
                            "connection_id": connection_id,
                            "timeout_seconds": PROBE_TIMEOUT_SECONDS,
                            "require_ring2_probe": ctx.require_ring2_probe,
                        },
                    )
                    logger.warning(
                        "Ring 2 capability FAILED for connection %s "
                        "(require_ring2_probe=%s)",
                        connection_id, ctx.require_ring2_probe,
                    )
                    # Hard-gate: only disable Ring 2 for this session if explicitly
                    # opted in via require_ring2_probe=True on the BridgeContext.
                    # Detector mode (default) records the event but leaves Ring 2 on.
                    if ctx.require_ring2_probe:
                        _ring2_disabled_for_connection.add(connection_id)
                        logger.warning(
                            "Ring 2 DISABLED for connection %s "
                            "(hard-gate active, probe failed)",
                            connection_id,
                        )

            probe_task = asyncio.create_task(_run_probe_in_background())
        else:
            probe_task = None

        try:
            await bridge_server.run(
                read_stream,
                write_stream,
                bridge_server.create_initialization_options(),
                raise_exceptions=True,
            )
        finally:
            # Clean up connection-scoped state regardless of how the connection closes.
            _ring2_disabled_for_connection.discard(connection_id)
            if probe_task is not None and not probe_task.done():
                probe_task.cancel()
                try:
                    await probe_task
                except (asyncio.CancelledError, Exception):
                    pass


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
