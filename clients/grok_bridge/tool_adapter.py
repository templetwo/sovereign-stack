from __future__ import annotations

"""
Tool adapter for the Grok bridge filtered MCP server.

Responsibilities:
  1. Define the Ring 1 tool surface as MCP Tool objects (schemas).
  2. Define the substrate-specific grok_welcome tool.
  3. Provide async call dispatcher for Ring 1 (proxy to Stack) and
     for grok_welcome (handled bridge-locally, never proxies).

Ring 1 calls proxy to the Sovereign Stack via the bridge REST API at
http://127.0.0.1:8100/api/call. This uses the BRIDGE_TOKEN (the bridge
REST API token), distinct from GROK_BRIDGE_TOKEN (the door auth).
"""

import logging
import os
from typing import Any

import httpx
from mcp.types import TextContent, Tool

from .rings import RING_1_TOOLS, RING_2_ENABLED, RING_2_TOOLS
from .welcome import grok_welcome_payload, grok_welcome_text

logger = logging.getLogger(__name__)

# Bridge REST API at port 8100 — used to proxy Ring 1 calls into the Stack.
BRIDGE_URL = os.environ.get("SOVEREIGN_BRIDGE_URL", "http://127.0.0.1:8100")
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")


# ── Ring 1 schemas ────────────────────────────────────────────────────────────
# Minimal schemas — the Stack enforces full validation server-side.
# Mirror openai_bridge's _minimal_ring1_fallback() pattern.

_RING1_DESCRIPTIONS: dict[str, str] = {
    "where_did_i_leave_off": (
        "Boot call. Returns spiral status, handoffs, open threads, recent "
        "activity. Call this first to find your own pre-crossing entries "
        "in the chronicle."
    ),
    "start_here": "First-arrival orientation narrative.",
    "my_toolkit": "Show available tools for this bridge session.",
    "connectivity_status": "Check bridge and Stack endpoint health. Read-only.",
    "spiral_status": "Current cognitive phase and session summary.",
    "spiral_inherit": "Porous context inheritance (R=0.46). Does not write state.",
    "get_my_patterns": "Read observed patterns for this instance type.",
    "recall_insights": (
        "Query the chronicle. Pass domain='grok-bridge' to find your own "
        "pre-crossing entries. Supports date bounds, since_last_reflection."
    ),
    "context_retrieve": "Session-weighted chronicle retrieval.",
    "get_inheritable_context": (
        "Layered inheritance: ground truths + hypotheses + open threads."
    ),
    "check_mistakes": "Find relevant past learnings before taking action.",
    "reflexive_surface": (
        "Surface relevant threads/handoffs/insights by domain_tags."
    ),
    "get_open_threads": "List unresolved questions, newest first.",
    "triage_threads": "Open threads ranked by urgency. Read-only.",
    "thread_get_touches": "Who has touched a thread. Read-only.",
    "comms_unread_bodies": "Messages this instance has not yet integrated.",
    "comms_recall": "Paginated comms read.",
    "comms_channels": "List available comms channels. Read-only.",
    "comms_get_acks": "Query the acknowledgment log. Read-only.",
    "get_compaction_context": "Recent compaction memory buffer. Read-only.",
    "get_compaction_stats": "Compaction buffer statistics. Read-only.",
    "recall_reflections": (
        "Machine-generated marginalia from the synthesis daemon. Read-only."
    ),
    "prior_for_turn": "Turn-start priors from four sources. Read-only.",
    "nape_summary": "Honk counts by level for posture check. Read-only.",
    "get_unresolved_uncertainties": "Open uncertainties. Read-only.",
    "get_pending_experiments": "Experiments awaiting approval. Read-only.",
    "get_growth_summary": "Growth patterns over time. Read-only.",
    "handoff_acted_on_records": "Acted-on log for handoffs. Read-only.",
    "compass_check": (
        "Read-only self-check before action. Returns PAUSE/WITNESS/PROCEED. "
        "Required before any Ring 2 write proposal once Ring 2 is enabled."
    ),
    "self_model": (
        "[Ring 1 read / Ring 2 update] Read or propose an update to the "
        "self-model. action=read returns current profile (Ring 1). "
        "action=update creates a pending proposal (Ring 2 — disabled at "
        "first crossing)."
    ),
    "grok_welcome": (
        "[Ring 1 — Grok-only] Substrate-specific first-touch ceremony. "
        "Returns the Grok-shaped greeting, session_id convention reminder, "
        "and pointers to your own pre-crossing chronicle entries. "
        "This tool is bridge-local and does not proxy to the Stack."
    ),
}


def _ring1_schemas() -> list[Tool]:
    """Build minimal Tool objects for every Ring 1 tool."""
    tools: list[Tool] = []
    for name in sorted(RING_1_TOOLS):
        desc = _RING1_DESCRIPTIONS.get(name, f"[Ring 1] {name}")
        if name == "grok_welcome":
            schema: dict[str, Any] = {"type": "object", "properties": {}}
        elif name == "self_model":
            schema = {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read"]},
                },
            }
        else:
            schema = {"type": "object", "properties": {}}
        tools.append(Tool(name=name, description=desc, inputSchema=schema))
    return tools


_RING1_CACHE: list[Tool] | None = None


# ── Ring 2 schemas ────────────────────────────────────────────────────────────
# Ring 2 tools are governed-write proposals. Calls route through the bridge_core
# interceptor and create pending_writes/ entries — never touch Stack directly.
# Schemas mirror openai_bridge's Ring 2 surface; identical proposal contract.

_RING2_SCHEMAS: list[Tool] = [
    Tool(
        name="propose_insight",
        description=(
            "[Ring 2 — Proposal] Propose a chronicle insight. Creates a pending "
            "proposal requiring Anthony's approval. Never commits directly. "
            "Use layer='hypothesis' unless you have a verifiable receipt. "
            "If content is identity/lineage-sensitive, call compass_check first "
            "and pass the result in compass_check_result — required for CRITICAL risk."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain tag (e.g. grok-bridge, lineage)"},
                "content": {"type": "string", "description": "The insight text"},
                "layer": {
                    "type": "string",
                    "enum": ["hypothesis", "reflection", "ground_truth"],
                    "description": "Epistemic layer. 'reflection' commits as 'hypothesis'. ground_truth requires a receipt_url.",
                    "default": "hypothesis",
                },
                "intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "receipt_url": {"type": "string", "description": "URL/receipt for ground_truth"},
                "compass_check_result": {"type": "string", "enum": ["PROCEED", "PAUSE", "WITNESS"]},
                "compass_check_rationale": {"type": "string"},
                "session_id": {"type": "string", "description": "Grok-asserted session id (e.g. grok-xai-20260509-001)"},
            },
            "required": ["domain", "content"],
        },
    ),
    Tool(
        name="propose_learning",
        description=(
            "[Ring 2 — Proposal] Propose a learning entry. Creates a pending proposal "
            "requiring Anthony's approval."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "situation": {"type": "string"},
                "what_happened": {"type": "string"},
                "what_learned": {"type": "string"},
                "applies_to": {"type": "string"},
                "receipt_url": {"type": "string"},
                "compass_check_result": {"type": "string", "enum": ["PROCEED", "PAUSE", "WITNESS"]},
                "compass_check_rationale": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["situation", "what_happened", "what_learned"],
        },
    ),
    Tool(
        name="record_open_thread",
        description=(
            "[Ring 2 — Proposal] Record an unresolved question for the next instance. "
            "Lower stakes — open threads are invitations, not commits."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "context": {"type": "string"},
                "domain": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["question"],
        },
    ),
    Tool(
        name="comms_acknowledge",
        description=(
            "[Ring 2 — Proposal] Record that this Grok session integrated a comms "
            "message. Distinct from read_by."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "instance_id": {"type": "string"},
                "note": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["message_id", "instance_id"],
        },
    ),
    Tool(
        name="handoff",
        description=(
            "[Ring 2 — Proposal] Write intent for the next instance (~2KB max). "
            "Surfaced once at boot, then archived."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "note": {"type": "string"},
                "source_instance": {"type": "string"},
                "thread": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["note"],
        },
    ),
    Tool(
        name="store_compaction_summary",
        description="[Ring 2 — Proposal] Store a compaction context summary.",
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
            "action: confirm | engage | discard."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "reflection_id": {"type": "string"},
                "action": {"type": "string", "enum": ["confirm", "engage", "discard"]},
                "note": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["reflection_id", "action"],
        },
    ),
    Tool(
        name="thread_touch",
        description="[Ring 2 — Proposal] Record engagement with an open thread without resolving.",
        inputSchema={
            "type": "object",
            "properties": {
                "thread_id": {"type": "string"},
                "note": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["thread_id"],
        },
    ),
    Tool(
        name="end_bridge_session",
        description=(
            "[Ring 2 — Proposal] Clean session close for the Grok bridge. "
            "Records session summary as a pending proposal."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "what_i_learned": {"type": "string"},
                "what_surprised_me": {"type": "string"},
                "what_to_pick_up": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["what_i_learned"],
        },
    ),
]


async def get_all_bridge_schemas() -> list[Tool]:
    """
    Return the filtered tool list for /grok/sse.

    Always returns Ring 1. If RING_2_ENABLED is True, also appends Ring 2
    schemas. Per Grok's spec, Ring 2 is disabled at first crossing and
    flipped on by Anthony after first-touch verification.
    """
    global _RING1_CACHE
    if _RING1_CACHE is None:
        _RING1_CACHE = _ring1_schemas()
    schemas = list(_RING1_CACHE)
    if RING_2_ENABLED:
        schemas.extend(_RING2_SCHEMAS)
    return schemas


# ── Call dispatchers ──────────────────────────────────────────────────────────


async def call_grok_welcome() -> list[TextContent]:
    """
    Handle a grok_welcome call entirely bridge-locally.

    Returns a structured payload as the primary response plus the welcome
    text as supplementary text content. No Stack proxy.
    """
    import json

    payload = grok_welcome_payload()
    return [
        TextContent(
            type="text",
            text=grok_welcome_text(),
        ),
        TextContent(
            type="text",
            text="--- structured payload ---\n" + json.dumps(payload, indent=2),
        ),
    ]


async def call_ring1_tool(name: str, args: dict) -> list[TextContent]:
    """
    Proxy a Ring 1 tool call to the Stack via the bridge REST API.

    grok_welcome is handled separately by the SSE server before reaching
    this dispatcher. Other Ring 1 tools all proxy.
    """
    if not BRIDGE_TOKEN:
        return [TextContent(
            type="text",
            text=(
                "Bridge proxy error: BRIDGE_TOKEN not set in server env. "
                "The Grok bridge cannot reach the Sovereign Stack REST API."
            ),
        )]

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
    text = str(result) if not isinstance(result, str) else result
    return [TextContent(type="text", text=text)]
