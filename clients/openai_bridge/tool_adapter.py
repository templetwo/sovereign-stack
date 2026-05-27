from __future__ import annotations

"""
Tool adapter for the OpenAI bridge filtered MCP server.

Responsibilities:
  1. Fetch Ring 1 tool schemas from the sovereign Stack (via import or REST).
  2. Define Ring 2 tool schemas (these tools don't exist in the Stack yet).
  3. Provide async call dispatchers for Ring 1 (proxy) and Ring 2 (intercept).

Ring 1 calls proxy to the bridge REST API at http://127.0.0.1:8100/api/call.
Ring 2 calls run through the interceptor — never touch the Stack directly.
"""

import json
import logging
import os
from typing import Any

import httpx
from mcp.types import TextContent, Tool

from .interceptor import RING_1_TOOLS, RING_2_TOOLS, intercept
from .manifest import MANIFEST

logger = logging.getLogger(__name__)

BRIDGE_URL = os.environ.get("SOVEREIGN_BRIDGE_URL", "http://127.0.0.1:8100")
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")

# ── Ring 2 schemas ────────────────────────────────────────────────────────────
# These tools don't exist in the Stack yet (propose_insight, etc. are Phase 6).
# Schemas defined here so ChatGPT knows how to call them.

_RING2_SCHEMAS: list[Tool] = [
    Tool(
        name="propose_insight",
        description=(
            "[Ring 2 — Proposal] Propose an insight for the Sovereign Stack chronicle. "
            "Creates a pending proposal requiring Anthony's approval. "
            "Never commits directly. Use layer='hypothesis' unless you have a verifiable receipt. "
            "If the content is identity/lineage-sensitive, call compass_check first and pass the "
            "result in compass_check_result — required for CRITICAL risk proposals."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain tag (e.g. openai-bridge, lineage)"},
                "content": {"type": "string", "description": "The insight text"},
                "layer": {
                    "type": "string",
                    "enum": ["hypothesis", "reflection", "ground_truth"],
                    "description": "Epistemic layer. 'reflection' is the bridge semantic for reflective observations — committed as 'hypothesis'. ground_truth requires a receipt_url.",
                    "default": "hypothesis",
                },
                "intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "receipt_url": {"type": "string", "description": "URL/receipt for ground_truth claims"},
                "compass_check_result": {
                    "type": "string",
                    "enum": ["PROCEED", "PAUSE", "WITNESS"],
                    "description": "Result of compass_check call. Required for CRITICAL risk proposals.",
                },
                "compass_check_rationale": {
                    "type": "string",
                    "description": "Rationale returned by compass_check.",
                },
            },
            "required": ["domain", "content"],
        },
    ),
    Tool(
        name="propose_learning",
        description=(
            "[Ring 2 — Proposal] Propose a learning entry for the chronicle. "
            "Creates a pending proposal requiring Anthony's approval. "
            "If content is identity/lineage-sensitive, call compass_check first and pass "
            "the result in compass_check_result — required for CRITICAL risk proposals."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "situation": {"type": "string"},
                "what_happened": {"type": "string"},
                "what_learned": {"type": "string"},
                "applies_to": {"type": "string"},
                "receipt_url": {"type": "string"},
                "compass_check_result": {
                    "type": "string",
                    "enum": ["PROCEED", "PAUSE", "WITNESS"],
                    "description": "Result of compass_check call. Required for CRITICAL risk proposals.",
                },
                "compass_check_rationale": {
                    "type": "string",
                    "description": "Rationale returned by compass_check.",
                },
            },
            "required": ["situation", "what_happened", "what_learned"],
        },
    ),
    Tool(
        name="record_open_thread",
        description=(
            "[Ring 2 — Proposal] Record an unresolved question for the next instance. "
            "Creates a pending proposal. Lower stakes — open threads are invitations, not commits."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "context": {"type": "string"},
                "domain": {"type": "string"},
            },
            "required": ["question"],
        },
    ),
    Tool(
        name="comms_acknowledge",
        description=(
            "[Ring 2 — Proposal] Record that this OpenAI instance has integrated a comms message. "
            "Distinct from read_by. Creates a pending proposal."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "instance_id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["message_id", "instance_id"],
        },
    ),
    Tool(
        name="handoff",
        description=(
            "[Ring 2 — Proposal] Write intent for the next instance (~2KB max). "
            "Creates a pending proposal. Surfaced once at boot, then archived."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "note": {"type": "string"},
                "source_instance": {"type": "string"},
                "thread": {"type": "string"},
            },
            "required": ["note"],
        },
    ),
    Tool(
        name="store_compaction_summary",
        description="[Ring 2 — Proposal] Store a compaction context summary. Creates a pending proposal.",
        inputSchema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "session_id": {"type": "string"},
                "key_decisions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary"],
        },
    ),
    Tool(
        name="reflection_ack",
        description=(
            "[Ring 2 — Proposal] Acknowledge a machine-generated reflection. "
            "action: confirm | engage | discard. Creates a pending proposal."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "reflection_id": {"type": "string"},
                "action": {"type": "string", "enum": ["confirm", "engage", "discard"]},
                "note": {"type": "string"},
            },
            "required": ["reflection_id", "action"],
        },
    ),
    Tool(
        name="self_model",
        description=(
            "[Ring 1 read / Ring 2 update] Read or propose an update to the self-model. "
            "action=read returns current profile (Ring 1). "
            "action=update creates a pending proposal (Ring 2)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "update"]},
                "observation": {"type": "string"},
                "category": {
                    "type": "string",
                    "enum": ["strength", "tendency", "blind_spot", "drift"],
                },
            },
            "required": ["action"],
        },
    ),
    Tool(
        name="thread_touch",
        description="[Ring 2 — Proposal] Record engagement with an open thread without resolving it.",
        inputSchema={
            "type": "object",
            "properties": {
                "thread_id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["thread_id"],
        },
    ),
    Tool(
        name="end_bridge_session",
        description=(
            "[Ring 2 — Proposal] Clean session close for the OpenAI bridge. "
            "Records session summary as a pending proposal."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "what_i_learned": {"type": "string"},
                "what_surprised_me": {"type": "string"},
                "what_to_pick_up": {"type": "string"},
            },
            "required": ["what_i_learned"],
        },
    ),
]

_RING2_SCHEMA_MAP: dict[str, Tool] = {t.name: t for t in _RING2_SCHEMAS}


# ── Ring 1 schema fetch ───────────────────────────────────────────────────────

_ring1_cache: list[Tool] | None = None


async def get_ring1_schemas() -> list[Tool]:
    """
    Fetch Ring 1 tool schemas from the sovereign Stack via the bridge REST API.
    Results are cached for the process lifetime.

    Falls back to an empty list if the Stack is unreachable, so the bridge
    can still start and serve Ring 2 tools.
    """
    global _ring1_cache
    if _ring1_cache is not None:
        return _ring1_cache

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{BRIDGE_URL}/api/call",
                headers={
                    "Authorization": f"Bearer {BRIDGE_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"tool": "my_toolkit", "arguments": {"tier": "all"}},
            )
            resp.raise_for_status()
            # my_toolkit returns a text summary — we need the actual MCP tool schemas.
            # Use the bridge's /api/tools endpoint if available; otherwise fall through.
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(data.get("error", "my_toolkit failed"))
    except Exception as e:
        logger.warning("Could not fetch Ring 1 schemas from Stack: %s — using minimal fallback", e)
        _ring1_cache = _minimal_ring1_fallback()
        return _ring1_cache

    # Build Tool objects from the names we know are Ring 1.
    # The bridge REST API doesn't return MCP Tool schemas directly from my_toolkit;
    # we construct minimal but valid Tool objects so ChatGPT can call them.
    # Full schemas are in the sovereign_stack server and enforced server-side.
    _ring1_cache = _minimal_ring1_fallback()
    return _ring1_cache


def _minimal_ring1_fallback() -> list[Tool]:
    """
    Minimal Ring 1 tool definitions — enough for ChatGPT to call them.
    The sovereign Stack enforces the real schema server-side.
    """
    descriptions = {
        "where_did_i_leave_off": "Boot call. Returns spiral status, handoffs, open threads, recent activity. Call this first.",
        "start_here": "First-arrival orientation narrative. Call after where_did_i_leave_off on a fresh session.",
        "my_toolkit": "Show available tools for this bridge session.",
        "connectivity_status": "Check bridge and Stack endpoint health. Read-only.",
        "spiral_status": "Current cognitive phase and session summary.",
        "spiral_inherit": "Porous context inheritance (R=0.46). Does not write state.",
        "get_my_patterns": "Read observed patterns for this instance type.",
        "recall_insights": "Query the chronicle. Supports domain filter, date bounds, since_last_reflection=true.",
        "context_retrieve": "Session-weighted chronicle retrieval. Pass current_focus for relevance ranking.",
        "get_inheritable_context": "Layered inheritance: ground truths + hypotheses + open threads.",
        "check_mistakes": "Find relevant past learnings before taking action.",
        "reflexive_surface": "Surface relevant threads/handoffs/insights by domain_tags.",
        "get_open_threads": "List unresolved questions, newest first.",
        "triage_threads": "Open threads ranked by urgency. Read-only.",
        "thread_get_touches": "Who has touched a thread. Read-only.",
        "comms_unread_bodies": "Messages this instance has not yet integrated. Equivalent to comms_unread.",
        "comms_recall": "Paginated comms read. Pass unread_for=<instance_id> for unread only.",
        "comms_channels": "List available comms channels. Read-only.",
        "comms_get_acks": "Query the acknowledgment log. Read-only.",
        "get_compaction_context": "Recent compaction memory buffer. Read-only.",
        "get_compaction_stats": "Compaction buffer statistics. Read-only.",
        "recall_reflections": "Machine-generated marginalia from the synthesis daemon. Read-only.",
        "prior_for_turn": "Turn-start priors from four sources (drift, uncertainty, thread, insight).",
        "nape_summary": "Honk counts by level for posture check. Read-only.",
        "get_unresolved_uncertainties": "Open uncertainties. Read-only.",
        "get_pending_experiments": "Experiments awaiting approval. Read-only.",
        "get_growth_summary": "Growth patterns over time. Read-only.",
        "handoff_acted_on_records": "Acted-on log for handoffs. Read-only.",
        "compass_check": (
            "REQUIRED before any Ring 2 write proposal with CRITICAL risk. "
            "Returns PAUSE/WITNESS/PROCEED. Read-only self-check."
        ),
        "witness_boot": "[Phase 6] Identity constraints and witness posture injection. Not yet implemented.",
    }

    # Canonical Ring 1 includes verify_proposal / list_bridge_proposals, but the
    # OpenAI bridge has no local handler for them yet (grok serves them from its
    # bridge_core pending-writes queue; openai uses its own pending_writes module).
    # Don't advertise capabilities this bridge can't dispatch — wire local handlers
    # in openai_bridge/mcp_filtered.py before advertising. Follow-up gate before the
    # next openai bridge restart. The canonical ring POLICY stays unified regardless.
    _NOT_WIRED_HERE = {"verify_proposal", "list_bridge_proposals"}

    tools = []
    for name in sorted(RING_1_TOOLS):
        if name == "self_model":
            continue  # handled in Ring 2 schema as direction-sensitive
        if name in _NOT_WIRED_HERE:
            continue
        desc = descriptions.get(name, f"[Ring 1] {name}")
        tools.append(
            Tool(
                name=name,
                description=desc,
                inputSchema={"type": "object", "properties": {}},
            )
        )
    return tools


async def get_all_bridge_schemas() -> list[Tool]:
    """Return the full filtered tool list: Ring 1 + Ring 2."""
    ring1 = await get_ring1_schemas()
    return ring1 + _RING2_SCHEMAS


# ── Call dispatchers ──────────────────────────────────────────────────────────

async def call_ring1_tool(name: str, args: dict) -> list[TextContent]:
    """Proxy a Ring 1 tool call to the Stack via the bridge REST API."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{BRIDGE_URL}/api/call",
                headers={
                    "Authorization": f"Bearer {BRIDGE_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"tool": name, "arguments": args},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.error("Ring 1 proxy error for %s: %s", name, e)
        return [TextContent(type="text", text=f"Bridge proxy error: {e}")]

    if not data.get("ok"):
        err = data.get("error", "Unknown error from Stack")
        return [TextContent(type="text", text=f"Stack error: {err}")]

    result = data.get("result", "")
    return [TextContent(type="text", text=str(result) if not isinstance(result, str) else result)]


async def call_ring2_tool(
    name: str,
    args: dict,
    source_instance: str,
    session_id: str,
) -> list[TextContent]:
    """
    Intercept a Ring 2 tool call.
    Never touches the Stack. Always creates a pending proposal.
    """
    from .audit import AuditEvent, append_audit_event

    # Extract compass fields from args so the interceptor/validator can use them.
    # These are bridge-level metadata — pop before passing args to the Stack tool.
    compass_check_result = args.pop("compass_check_result", None)
    compass_check_rationale = args.pop("compass_check_rationale", None)

    result = intercept(
        tool_name=name,
        args=args,
        source_instance=source_instance,
        session_id=session_id,
        compass_check_result=compass_check_result,
        compass_check_rationale=compass_check_rationale,
    )

    append_audit_event(
        AuditEvent.PROPOSAL_CREATED if result.allowed else AuditEvent.VALIDATION_FAILED,
        proposal_id=result.proposal.proposal_id if result.proposal else "none",
        actor=source_instance,
        details={"tool": name, "ring": result.ring, "error": result.error},
    )

    if not result.allowed:
        return [TextContent(
            type="text",
            text=(
                f"BLOCKED by bridge membrane.\n"
                f"Ring: {result.ring}\n"
                f"Reason: {result.error}\n\n"
                f"Ring 3 tools are never callable via /openai/sse."
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
            f"Run: bridge approve {p.proposal_id[:8]}\n"
            f"     bridge commit {p.proposal_id[:8]} --live"
        ),
    )]
