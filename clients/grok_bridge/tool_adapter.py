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

import asyncio
import logging
import math
import os
import time
from copy import deepcopy
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
        "pre-crossing entries. Supports date bounds, since_last_reflection. "
        "Every returned item carries claim_id (full 64-hex) — that is the "
        "address for inspect_claim / supersede_insight, so you can correct "
        "your own entry without asking a local seat to derive its id."
    ),
    "context_retrieve": "Session-weighted chronicle retrieval.",
    "get_inheritable_context": ("Layered inheritance: ground truths + hypotheses + open threads."),
    "check_mistakes": "Find relevant past learnings before taking action.",
    "reflexive_surface": ("Surface relevant threads/handoffs/insights by domain_tags."),
    "get_open_threads": "List unresolved questions, newest first.",
    "triage_threads": "Open threads ranked by urgency. Read-only.",
    "thread_get_touches": "Who has touched a thread. Read-only.",
    "comms_unread_bodies": "Messages this instance has not yet integrated.",
    "comms_recall": "Paginated comms read.",
    "comms_channels": "List available comms channels. Read-only.",
    "comms_get_acks": "Query the acknowledgment log. Read-only.",
    "get_compaction_context": "Recent compaction memory buffer. Read-only.",
    "get_compaction_stats": "Compaction buffer statistics. Read-only.",
    "recall_reflections": ("Machine-generated marginalia from the synthesis daemon. Read-only."),
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
    "verify_proposal": (
        "[Ring 1 — Read-only] Verify whether a claimed Ring 2 write proposal actually "
        "landed in the pending-writes queue. Returns found=True/False and chain_valid. "
        "found=False means the proposal does NOT exist in the queue — a narrated write "
        "is not a real write. Use this to confirm your own Ring 2 calls were accepted "
        "before treating them as having been executed."
    ),
    "list_bridge_proposals": (
        "[Ring 1 — Read-only] List proposals in the pending-writes queue, optionally "
        "filtered by status (default: 'pending'). Returns structured summaries — "
        "proposal_id, tool, risk_level, timestamp, source_instance, status. "
        "Use to audit what Ring 2 writes are awaiting Anthony's approval."
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


# ── Ring 1 schema fetch ───────────────────────────────────────────────────────
#
# THE PUBLISHED SCHEMA IS THE ONLY DESCRIPTION OF A TOOL THE GROK SEAT EVER
# SEES. Until 2026-09-05 this builder assigned `{"type": "object",
# "properties": {}}` to every Ring-1 name it had no hand-written schema for —
# 32 of the 36 — in its PRIMARY path. Not a degraded fallback: the shape the
# live /grok/sse surface published on every healthy call.
#
# Measured against the Stack registry on 2026-09-05, six of those tools REQUIRE
# an argument: check_mistakes (context), compass_check (action),
# comms_unread_bodies (instance_id), context_retrieve (current_focus),
# inspect_claim (claim_id), reflexive_surface (domain_tags). The seat reads
# "no arguments", calls bare, and the Stack correctly refuses a field the seat
# was never shown — which reads to that seat as the tool being broken, or as
# its own error. This is exactly the report the OpenAI seat filed on
# 2026-08-28 ("the Compass tool exposed here accepts no arguments"), and
# openai reached that state only on DOUBLE failure (0d674c4, d5e2e85); grok
# lived in it permanently.
#
# The other ~26 were not refused, which is worse in one specific way: their
# optional parameters were simply invisible. recall_insights publishes 13
# properties and the seat could reach none of them — including `order`, whose
# default ("newest") returns recency noise on any historical query, and
# `domain`. A tool that answers badly is harder to notice than one that errors.
#
# Contract mirrored from clients/openai_bridge/tool_adapter.py: source the real
# schema from the live endpoint, then the in-process registry, then a static
# fallback that publishes ONLY what it can actually describe and omits the rest
# (which is how MCP spells "unavailable"). A description-only tool whose backend
# requires arguments is never published.

_ring1_cache: list[Tool] | None = None
_ring1_cache_at: float = 0.0

# This projection lives inside the long-running sovereign-sse process — the same
# process that serves /openai/sse — so a process-lifetime cache pins whatever
# schema happened to be reachable at the first discovery call until someone
# restarts a service. Bounded, so a stale projection self-heals within one TTL
# instead of surviving a deploy of the very tools it describes.
_DEFAULT_SCHEMA_TTL_SECONDS = 900.0

# Floor, not a preference: a miss costs 1 + len(expected) GETs, so a TTL of 0
# turns every list_tools into a full fan-out rather than the "no cache" an
# operator debugging a stale projection probably meant.
_MIN_SCHEMA_TTL_SECONDS = 5.0


def _schema_ttl_seconds() -> float:
    """Parse the TTL override without letting a typo take the SSE server down.

    ``sovereign_stack.sse_server`` imports this module at startup, so an
    unparseable value raised at import time would present to an operator as a
    tunnel or connector outage rather than as a config error.
    """
    raw = os.environ.get("GROK_BRIDGE_SCHEMA_TTL_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_SCHEMA_TTL_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "GROK_BRIDGE_SCHEMA_TTL_SECONDS=%r is not a number — using %.0fs",
            raw,
            _DEFAULT_SCHEMA_TTL_SECONDS,
        )
        return _DEFAULT_SCHEMA_TTL_SECONDS
    # "inf" and "nan" both parse as floats. inf is the dangerous one: it passes a
    # bare `< floor` check and silently restores the unbounded process-lifetime
    # cache this TTL exists to end. Neither is a duration, so neither is clamped.
    if not math.isfinite(value):
        logger.warning(
            "GROK_BRIDGE_SCHEMA_TTL_SECONDS=%r is not a finite duration — using %.0fs",
            raw,
            _DEFAULT_SCHEMA_TTL_SECONDS,
        )
        return _DEFAULT_SCHEMA_TTL_SECONDS
    if value < _MIN_SCHEMA_TTL_SECONDS:
        logger.warning(
            "GROK_BRIDGE_SCHEMA_TTL_SECONDS=%r is below the %.0fs floor — clamping",
            raw,
            _MIN_SCHEMA_TTL_SECONDS,
        )
        return _MIN_SCHEMA_TTL_SECONDS
    return value


# Parsed once, at import, and read as a module attribute so tests can override it.
RING1_CACHE_TTL_SECONDS = _schema_ttl_seconds()

# In RING_1_TOOLS, absent from the Stack registry, and NOT dispatched locally by
# this bridge — `handle_bridge_tool` falls through to `call_ring1_tool`, which
# proxies it to a Stack that has never defined it. Publishing it advertised a
# tool that could not be called by any route. `witness_boot` is forward-declared
# (Phase 6) in bridge_core.rings for exactly this reason; openai_bridge can
# publish it because openai_bridge DISPATCHES it. This one cannot.
_NOT_WIRED_RING1 = frozenset({"witness_boot"})

# Ring-1 tools this connector dispatches itself (mcp_filtered.handle_bridge_tool)
# rather than proxying to the Stack. Their schemas are known here on every path,
# healthy or degraded, so they live at module scope: the healthy path needs them
# without calling the static fallback, whose degradation warning would then fire
# as a side effect of a successful discovery (the d5e2e85 regression, one
# substrate over).
_BRIDGE_LOCAL_TOOLS: dict[str, dict[str, Any]] = {
    "grok_welcome": {
        # Really takes no arguments, so an empty property set is the TRUTH for
        # it rather than a missing schema.
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    "verify_proposal": {
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "substrate": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    "list_bridge_proposals": {
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
    },
}

# Derived, never hand-maintained: a bridge-local name that _expected_remote_ring1
# still asked the Stack for would be fetched remotely and then not found.
_LOCAL_RING1 = frozenset(_BRIDGE_LOCAL_TOOLS)

# `self_model` is direction-sensitive and the BRIDGE narrows it, so its schema is
# the bridge's own rather than the Stack's. mcp_filtered routes action="read" to
# Ring 1 and lets everything else fall through to the Ring 2 proposal path, so
# publishing the Stack's enum ["read", "update"] would advertise a Ring 1 call
# this bridge does not make.
_SELF_MODEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": ["read"]}},
    "required": [],
}


def _bridge_local_tool(name: str) -> Tool:
    """Build a fresh Tool for a bridge-local name.

    Built per call rather than shared from a module-level ``Tool``: the SSE
    process is long-lived and ``Tool`` is a mutable pydantic model, so handing
    the same instance (and the same ``inputSchema`` dict) to every cache
    generation would let one mutation reach every future reader.
    """
    spec = _BRIDGE_LOCAL_TOOLS[name]
    return Tool(
        name=name,
        description=_RING1_DESCRIPTIONS.get(name, f"[Ring 1] {name}"),
        inputSchema=deepcopy(spec["inputSchema"]),
    )


def _self_model_tool() -> Tool:
    return Tool(
        name="self_model",
        description=_RING1_DESCRIPTIONS.get("self_model", "[Ring 1] self_model"),
        inputSchema=deepcopy(_SELF_MODEL_SCHEMA),
    )


def _expected_remote_ring1() -> frozenset[str]:
    """Ring-1 names this bridge proxies to the Stack (not bridge-local, wired)."""
    return frozenset(RING_1_TOOLS - _NOT_WIRED_RING1 - _LOCAL_RING1 - {"self_model"})


def _describe(name: str, live_description: str | None) -> str:
    """Grok's own text wins where it exists; the Stack's fills the gaps.

    DELIBERATE DIVERGENCE from the openai mirror, which takes the live
    description wholesale. These strings are substrate-specific orientation
    written for this seat — recall_insights tells Grok to pass
    domain='grok-bridge' to find its own pre-crossing entries — and replacing
    them with the generic Stack text would change the published surface beyond
    the schema defect this fixes. Names with no local text (arrive,
    arrive_lineage, current_policies, inspect_claim, season_review, the_ground)
    gain the Stack's real description, which is a strict improvement on the
    "[Ring 1] <name>" placeholder they carried.
    """
    local = _RING1_DESCRIPTIONS.get(name)
    if local:
        return local
    return str(live_description or f"[Ring 1] {name}")


def reset_ring1_cache() -> None:
    """Drop the cached projection so the next discovery call refetches."""
    global _ring1_cache, _ring1_cache_at
    _ring1_cache = None
    _ring1_cache_at = 0.0


async def get_ring1_schemas() -> list[Tool]:
    """
    Fetch Ring 1 tool schemas for /grok/sse, from the Stack's real registry.

    ``/api/tools?name=...`` is the live schema source. Falls back to the
    installed Stack's in-process registry, then to static definitions if both
    live surfaces are unavailable, so the bridge can still start and serve
    Ring 2 proposals (which are local and never needed the Stack).

    A projected tool carries the Stack's real ``inputSchema`` or is not
    projected at all.
    """
    global _ring1_cache, _ring1_cache_at
    # Known and deliberately not gated, same call as the openai mirror: no
    # single-flight around this refresh. The fan-out is loopback and k is small,
    # while a lock held across a 10s HTTP timeout inside the shared SSE process
    # is a worse failure than a duplicated read.
    if _ring1_cache is not None and (time.monotonic() - _ring1_cache_at) < RING1_CACHE_TTL_SECONDS:
        return _ring1_cache

    headers = {
        "Authorization": f"Bearer {BRIDGE_TOKEN}",
        "Content-Type": "application/json",
    }

    # Cleared when we knowingly publish a short surface, so it is not pinned for
    # a TTL. The live path never sets it: it refuses a short catalog outright.
    cacheable = True
    tools: list[Tool] = []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            catalog_response = await client.get(f"{BRIDGE_URL}/api/tools", headers=headers)
            catalog_response.raise_for_status()
            catalog = catalog_response.json()
            catalog_items = catalog.get("tools")
            if not isinstance(catalog_items, list):
                raise RuntimeError("/api/tools response has no tools list")

            live_names = {item.get("name") for item in catalog_items if isinstance(item, dict)}
            # A partial catalog is a FAILED READ, not a smaller Stack. Intersecting
            # the allowlist with whatever came back would silently shrink the
            # published surface and then cache the reduction for a full TTL.
            expected = _expected_remote_ring1()
            missing = sorted(expected - live_names)
            if missing:
                raise RuntimeError(
                    f"/api/tools catalog is missing {len(missing)} allowlisted "
                    f"Ring-1 tools ({', '.join(missing)}) — refusing to publish a "
                    "silently partial surface"
                )
            remote_names = sorted(expected)
            responses = await asyncio.gather(
                *(
                    client.get(
                        f"{BRIDGE_URL}/api/tools",
                        headers=headers,
                        params={"name": name},
                    )
                    for name in remote_names
                )
            )

            for expected_name, response in zip(remote_names, responses, strict=True):
                response.raise_for_status()
                payload = response.json()
                if payload.get("name") != expected_name:
                    raise RuntimeError(
                        f"schema mismatch: requested {expected_name!r}, "
                        f"received {payload.get('name')!r}"
                    )
                schema = payload.get("inputSchema")
                if not isinstance(schema, dict):
                    raise RuntimeError(f"{expected_name}: missing inputSchema")
                tools.append(
                    Tool(
                        name=expected_name,
                        description=_describe(expected_name, payload.get("description")),
                        inputSchema=schema,
                    )
                )
    except Exception as e:
        logger.warning(
            "Could not fetch Ring 1 schemas from Stack: %s — using in-process registry",
            e,
        )
        try:
            from sovereign_stack.server import list_tools as list_native_tools

            native_tools = await list_native_tools()
            allowed_remote = _expected_remote_ring1()
            by_native = {t.name: t for t in native_tools if t.name in allowed_remote}
            tools = [
                Tool(
                    name=t.name,
                    description=_describe(t.name, t.description),
                    inputSchema=deepcopy(t.inputSchema),
                )
                for t in by_native.values()
            ]
            # NOT the same failure as a short HTTP catalog, so not the same
            # remedy. A short in-process registry is the AUTHORITATIVE answer: if
            # list_tools() has no `foo`, the Stack has no `foo` and the allowlist
            # is stale. Refusing here would send a healthy bridge to the static
            # fallback over one renamed tool. The defect was the SILENCE and the
            # full-TTL cache, not the shrinking.
            registry_missing = sorted(allowed_remote - set(by_native))
            if registry_missing:
                cacheable = False
                logger.warning(
                    "In-process registry does not define %d allowlisted Ring-1 "
                    "tool(s) (%s) — the allowlist is stale, not the read. "
                    "Publishing the %d the registry does hold, uncached.",
                    len(registry_missing),
                    ", ".join(registry_missing),
                    len(tools),
                )
        except Exception as fallback_error:
            logger.warning(
                "Could not read in-process Stack schemas: %s — using static fallback",
                fallback_error,
            )
            # Do not cache the least-trustworthy fallback. A later discovery call
            # retries the live and in-process registries after recovery.
            return _minimal_ring1_fallback()

    by_name = {tool.name: tool for tool in tools}
    for name in _LOCAL_RING1 & RING_1_TOOLS:
        by_name[name] = _bridge_local_tool(name)
    if "self_model" in RING_1_TOOLS:
        by_name["self_model"] = _self_model_tool()

    projection = [by_name[name] for name in sorted(by_name)]
    if not cacheable:
        # A surface we already know is short does not get pinned for a TTL.
        return projection

    _ring1_cache = projection
    _ring1_cache_at = time.monotonic()
    return _ring1_cache


def _minimal_ring1_fallback() -> list[Tool]:
    """
    Last-resort Ring 1 definitions, reached only when BOTH the live
    ``/api/tools`` endpoint and the in-process Stack registry are unavailable.

    Publishes only the tools whose real input schema is known here — the
    bridge-local three plus the bridge's narrowed ``self_model`` — and OMITS
    everything else, which is how MCP spells "unavailable".

    OMISSION COSTS NOTHING BY CONSTRUCTION. This path is reached only when both
    Stack surfaces are unreachable, and ``call_ring1_tool`` posts to that same
    unreachable BRIDGE_URL, so the withheld tools were not callable either way.
    Ring 2 proposals are written locally and keep working, which is why the
    fallback exists at all.
    """
    tools = [_bridge_local_tool(name) for name in sorted(_LOCAL_RING1 & RING_1_TOOLS)]
    if "self_model" in RING_1_TOOLS:
        tools.append(_self_model_tool())
    withheld = sorted(RING_1_TOOLS - {t.name for t in tools})
    if withheld:
        logger.warning(
            "Static Ring-1 fallback: withholding %d tool(s) with no known schema "
            "(%s). Publishing a description-only tool whose backend requires "
            "arguments is the fail-open this omission exists to prevent, and "
            "Ring 1 cannot be proxied in this state anyway.",
            len(withheld),
            ", ".join(withheld),
        )
    return sorted(tools, key=lambda t: t.name)


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
                "domain": {
                    "type": "string",
                    "description": "Domain tag (e.g. grok-bridge, lineage)",
                },
                "content": {"type": "string", "description": "The insight text"},
                "layer": {
                    "type": "string",
                    "enum": ["hypothesis", "ground_truth"],
                    "description": "Epistemic layer. Defaults to 'hypothesis'. ground_truth requires a receipt_url. (Note: the bridge also accepts 'reflection' for backward compatibility — it is translated to 'hypothesis' at commit time — but new callers should use 'hypothesis' directly. The 'reflection' layer in the chronicle is reserved for daemon-written marginalia.)",
                    "default": "hypothesis",
                },
                "intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "receipt_url": {"type": "string", "description": "URL/receipt for ground_truth"},
                "compass_check_result": {"type": "string", "enum": ["PROCEED", "PAUSE", "WITNESS"]},
                "compass_check_rationale": {"type": "string"},
                "session_id": {
                    "type": "string",
                    "description": "Grok-asserted session id (e.g. grok-xai-20260509-001)",
                },
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
    Tool(
        name="probe_ring2_dispatch",
        description=(
            "[Ring 2 — Probe sentinel] Call this as your first action after grok_welcome. "
            "It confirms that your connector can dispatch Ring 2 (write-class) tool calls "
            "to the bridge. This is a dry-run: no proposal is created, no chronicle entry "
            "is written. The bridge records a RING2_CAPABILITY_VERIFIED audit event and "
            "returns an acknowledgment. If you skip this call, the bridge will record "
            "RING2_CAPABILITY_FAILED after a short timeout."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Your session id (e.g. grok-xai-20260525-001).",
                },
            },
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
    schemas = list(await get_ring1_schemas())
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
        return [
            TextContent(
                type="text",
                text=(
                    "Bridge proxy error: BRIDGE_TOKEN not set in server env. "
                    "The Grok bridge cannot reach the Sovereign Stack REST API."
                ),
            )
        ]

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
