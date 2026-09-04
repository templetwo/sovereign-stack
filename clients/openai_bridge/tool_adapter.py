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

import asyncio
import json
import logging
import math
import os
import time
from copy import deepcopy
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
                "domain": {
                    "type": "string",
                    "description": "Domain tag (e.g. openai-bridge, lineage)",
                },
                "content": {"type": "string", "description": "The insight text"},
                "layer": {
                    "type": "string",
                    "enum": ["hypothesis", "ground_truth"],
                    "description": "Epistemic layer. Defaults to 'hypothesis'. ground_truth requires a receipt_url. (Note: the bridge also accepts 'reflection' for backward compatibility — it is translated to 'hypothesis' at commit time — but new callers should use 'hypothesis' directly. The 'reflection' layer in the chronicle is reserved for daemon-written marginalia.)",
                    "default": "hypothesis",
                },
                "intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "receipt_url": {
                    "type": "string",
                    "description": "URL/receipt for ground_truth claims",
                },
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
_ring1_cache_at: float = 0.0

# The projection lives inside the long-running sovereign-sse process, so a
# process-lifetime cache pins whatever schema happened to be reachable at the
# first discovery call until someone restarts a service.  Bound it: a stale
# projection then self-heals within one TTL instead of surviving a deploy of the
# very tools it describes.
_DEFAULT_SCHEMA_TTL_SECONDS = 900.0

# Floor, not a preference.  ``get_ring1_schemas`` issues 1 + len(expected) GETs
# on a miss, so a TTL of 0 (or a negative one) turns every list_tools into a full
# fan-out rather than the "no cache" an operator debugging a stale projection
# probably meant.  Five seconds keeps a refetch effectively immediate by hand
# while bounding a burst of concurrent sessions to one fan-out.
_MIN_SCHEMA_TTL_SECONDS = 5.0


def _schema_ttl_seconds() -> float:
    """Parse the TTL override without letting a typo take the SSE server down.

    ``sovereign_stack.sse_server`` imports this module at startup, so an
    unparseable value here used to raise at import time and present to an
    operator as a tunnel or connector outage rather than as a config error.
    Read the value, complain about a bad one, and carry on with the default.
    """
    raw = os.environ.get("OPENAI_BRIDGE_SCHEMA_TTL_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_SCHEMA_TTL_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "OPENAI_BRIDGE_SCHEMA_TTL_SECONDS=%r is not a number — using %.0fs",
            raw,
            _DEFAULT_SCHEMA_TTL_SECONDS,
        )
        return _DEFAULT_SCHEMA_TTL_SECONDS
    # "inf" and "nan" both parse as floats.  inf is the dangerous one: it passes
    # a bare `< floor` check and silently restores the unbounded process-lifetime
    # cache this TTL exists to end.  Neither is a duration, so neither is clamped
    # to the floor — that would be answering the wrong question.  Fall back.
    if not math.isfinite(value):
        logger.warning(
            "OPENAI_BRIDGE_SCHEMA_TTL_SECONDS=%r is not a finite duration — "
            "using %.0fs (an unbounded TTL is the stale projection this cache "
            "bound exists to prevent)",
            raw,
            _DEFAULT_SCHEMA_TTL_SECONDS,
        )
        return _DEFAULT_SCHEMA_TTL_SECONDS
    if value < _MIN_SCHEMA_TTL_SECONDS:
        logger.warning(
            "OPENAI_BRIDGE_SCHEMA_TTL_SECONDS=%r is below the %.0fs floor — clamping",
            raw,
            _MIN_SCHEMA_TTL_SECONDS,
        )
        return _MIN_SCHEMA_TTL_SECONDS
    return value


# Parsed once, at import, and read as a module attribute so tests can override it.
RING1_CACHE_TTL_SECONDS = _schema_ttl_seconds()

_NOT_WIRED_RING1 = frozenset({"verify_proposal", "list_bridge_proposals"})

# Ring-1 tools this connector dispatches itself (mcp_filtered.handle_bridge_tool)
# rather than proxying to the Stack.  Their schemas are known here on every path,
# healthy or degraded, so they live at module scope: the healthy path needs
# witness_boot without calling the static fallback for it, and calling the
# fallback purely to source one tool made the fallback's degradation warning fire
# on successful discovery.  witness_boot really does take no arguments, so an
# empty property set is the truth for it rather than a missing schema.
_BRIDGE_LOCAL_TOOLS: dict[str, dict[str, Any]] = {
    "witness_boot": {
        "description": (
            "[Phase 6] Identity constraints and witness posture injection. "
            "Not yet implemented."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
}

# Derived, never hand-maintained: a bridge-local name that _expected_remote_ring1
# still asked the Stack for would be fetched remotely and then not found, and a
# name in _LOCAL_RING1 with no entry above used to be a KeyError on the live
# success path.  One source of truth makes both unrepresentable.
_LOCAL_RING1 = frozenset(_BRIDGE_LOCAL_TOOLS)


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
        description=str(spec["description"]),
        inputSchema=deepcopy(spec["inputSchema"]),
    )


def _expected_remote_ring1() -> frozenset[str]:
    """Ring-1 names this bridge proxies to the Stack (not bridge-local, wired)."""
    return frozenset(RING_1_TOOLS - _NOT_WIRED_RING1 - _LOCAL_RING1 - {"self_model"})


def reset_ring1_cache() -> None:
    """Drop the cached projection so the next discovery call refetches."""
    global _ring1_cache, _ring1_cache_at
    _ring1_cache = None
    _ring1_cache_at = 0.0


def _bridge_toolkit_schema() -> Tool:
    """The connector-local discovery contract; never proxy the native one."""
    return Tool(
        name="my_toolkit",
        description=(
            "Show the exact callable OpenAI bridge surface. Built from the same "
            "schemas returned by MCP list_tools, so native Stack tools that the ring "
            "membrane does not publish cannot appear here."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "include_schema": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include each callable tool's input JSON schema.",
                }
            },
            "required": [],
        },
    )


async def get_ring1_schemas() -> list[Tool]:
    """
    Fetch Ring 1 tool schemas from the sovereign Stack via the bridge REST API.
    Results are cached for the process lifetime.

    ``/api/tools?name=...`` is the live schema source.  The former implementation
    called native ``my_toolkit`` (which returns prose), discarded its result, and
    then published empty placeholder schemas from a May 2026 allowlist.  That let
    native discovery and MCP callability diverge while both claimed to be live.

    Falls back first to the installed Stack's in-process registry, then to static
    definitions if both live surfaces are unavailable, so the bridge can still
    start and serve Ring 2 tools.  New tools are never admitted by discovery
    alone: the explicit Ring-1 allowlist remains the access-control gate.

    The published schema is the only description of a tool the remote seat ever
    sees, so a projected tool must carry the Stack's real ``inputSchema`` or not
    be projected at all.  A description-only tool whose backend requires an
    argument is the fail-open shape: it reads as callable, the seat calls it
    bare, and the Stack rejects it for a field the seat was never shown.
    """
    global _ring1_cache, _ring1_cache_at
    # Known and deliberately not gated: no single-flight around this refresh, so
    # k sessions listing in the same instant each run their own 1 + len(expected)
    # fan-out.  Bounding the cache turned "once per process" into "once per TTL
    # window", which is more stampedes, not fewer.  It stays a comment because
    # the fan-out is loopback and k is small, while a lock held across a 10s HTTP
    # timeout inside the shared SSE process is a worse failure than a duplicated
    # read.  Revisit if this bridge ever fans out to a non-local Stack.
    if _ring1_cache is not None and (time.monotonic() - _ring1_cache_at) < RING1_CACHE_TTL_SECONDS:
        return _ring1_cache

    headers = {
        "Authorization": f"Bearer {BRIDGE_TOKEN}",
        "Content-Type": "application/json",
    }

    # Cleared when we knowingly publish a short surface, so it is not pinned for
    # a TTL.  The live path never sets it: it refuses a short catalog outright.
    cacheable = True

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            catalog_response = await client.get(f"{BRIDGE_URL}/api/tools", headers=headers)
            catalog_response.raise_for_status()
            catalog = catalog_response.json()
            catalog_items = catalog.get("tools")
            if not isinstance(catalog_items, list):
                raise RuntimeError("/api/tools response has no tools list")

            live_names = {
                item.get("name") for item in catalog_items if isinstance(item, dict)
            }
            # Intersecting the allowlist with the catalog silently shrinks the
            # projection when the catalog is partial, and the reduced surface was
            # then cached for the process lifetime with no coverage signal.  A
            # partial catalog is a failed read, not a smaller Stack: say so and
            # let the in-process registry answer instead.
            #
            # The invariant this buys is narrow, so state it narrowly.  It is
            # NOT "the published surface can never shrink" — it still can, via
            # the in-process registry below and via the static fallback.  It is:
            # a short surface is never published SILENTLY and is never CACHED.
            # Those two were the actual defect; the shrinking itself is
            # sometimes the correct answer.
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

            tools: list[Tool] = []
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
                        description=str(payload.get("description") or f"[Ring 1] {expected_name}"),
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
            by_native = {
                tool.name: tool for tool in native_tools if tool.name in allowed_remote
            }
            tools = list(by_native.values())
            # This is the same intersect-and-shrink the live path refuses, and it
            # is NOT the same failure, so it does not get the same remedy.  A
            # short HTTP catalog is plausibly a failed read; a short in-process
            # registry is the authoritative answer.  If list_tools() has no
            # ``foo``, the Stack has no ``foo`` and the allowlist is stale — the
            # read was fine.  Refusing here would send a healthy bridge to the
            # static fallback over one renamed tool, cutting the seat from 34
            # tools to 8 and taking where_did_i_leave_off, recall_insights and
            # compass_check with it.  The defect was the SILENCE and the
            # full-TTL cache, not the shrinking: say what was dropped and do not
            # cache it, so a redeploy is picked up on the next call.
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
            # Do not cache the least-trustworthy fallback.  A later discovery
            # call retries the live and in-process registries after recovery.
            return _minimal_ring1_fallback()

    by_name = {tool.name: tool for tool in tools}
    if "my_toolkit" in by_name:
        by_name["my_toolkit"] = _bridge_toolkit_schema()

    # witness_boot is implemented by this bridge rather than the native Stack.
    # Sourced from _BRIDGE_LOCAL_TOOLS, not from _minimal_ring1_fallback(): that
    # call happened on the fully healthy path too, and its degradation warning
    # then fired as a side effect of a successful discovery, writing a false
    # "compass_check withheld" line into sse.log on every list_tools.
    for name in _LOCAL_RING1 & RING_1_TOOLS:
        by_name[name] = _bridge_local_tool(name)

    projection = [by_name[name] for name in sorted(by_name)]
    if not cacheable:
        # A surface we already know is short does not get pinned for a TTL.
        return projection

    _ring1_cache = projection
    _ring1_cache_at = time.monotonic()
    return _ring1_cache


def _minimal_ring1_fallback() -> list[Tool]:
    """
    Last-resort Ring 1 definitions, reached only when BOTH the live ``/api/tools``
    endpoint and the in-process Stack registry are unavailable.

    It publishes only the tools whose real input schema is known here, plus the
    bridge-local tools this connector dispatches itself.  Everything else is
    OMITTED rather than advertised argument-less.

    This is the bug the OpenAI seat reported on 2026-08-28: the old version
    handed every unlisted tool ``{"type": "object", "properties": {}}``, so
    ``compass_check``, ``context_retrieve`` and ``reflexive_surface`` reached
    ChatGPT as tools that "accept no arguments" and were then rejected by the
    Stack for missing ``action`` / ``current_focus`` / ``domain_tags`` — fields
    the seat had never been shown.  A description is not a schema, and the Stack
    enforcing the real schema server-side is not a substitute for publishing it:
    server-side enforcement is exactly what turns the silent lie into a rejected
    call.  In MCP, "unavailable" is spelled by omission from ``list_tools``, so
    an undescribable tool is left out.

    Omission costs little here by construction: a bridge in this state cannot
    proxy Ring 1 at all — ``call_ring1_tool`` posts to the same unreachable
    ``BRIDGE_URL`` — so the omitted tools were not callable either way.  Ring 2
    proposals are local and keep working, which is why this fallback exists.

    That clause is load-bearing and conditional on reaching here only when a
    surface is UNREADABLE.  An earlier revision made the in-process registry
    raise on an incomplete catalog, which would have routed a perfectly healthy
    bridge here over one stale allowlist entry — and then this log would have
    asserted Ring 1 was unproxyable while it was in fact fine.  If a future
    change adds another route into this function, re-check the claim before
    keeping it.
    """
    descriptions = {
        "where_did_i_leave_off": "Boot call. Returns spiral status, handoffs, open threads, recent activity. Call this first.",
        "arrive": "Thin, side-effect-free arrival foyer. Read-only.",
        "arrive_lineage": "Gentle lineage-only arrival with no handoff consumption. Read-only.",
        "start_here": "First-arrival orientation narrative. Call after where_did_i_leave_off on a fresh session.",
        "my_toolkit": "Show the exact callable OpenAI bridge surface.",
        "connectivity_status": "Check bridge and Stack endpoint health. Read-only.",
        "spiral_status": "Current cognitive phase and session summary.",
        "spiral_inherit": "Porous context inheritance (R=0.46). Does not write state.",
        "get_my_patterns": "Read observed patterns for this instance type.",
        "recall_insights": (
            "Query the chronicle. Supports domain filter, date bounds, "
            "since_last_reflection=true. Every returned item carries claim_id "
            "(full 64-hex) — the address for inspect_claim / supersede_insight, "
            "so you can correct your own entry without a local seat deriving it."
        ),
        "context_retrieve": "Session-weighted chronicle retrieval. Pass current_focus for relevance ranking.",
        "get_inheritable_context": "Layered inheritance: ground truths + hypotheses + open threads.",
        "check_mistakes": "Find relevant past learnings before taking action.",
        "reflexive_surface": "Surface relevant threads/handoffs/insights by domain_tags.",
        "current_policies": "Read the standing-policy registry. Read-only.",
        "inspect_claim": "Inspect one chronicle claim and its receipts. Read-only.",
        "season_review": "Read-only digest of chronicle health and candidate work.",
        "the_ground": "Read the catch ledger. Read-only.",
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
        # witness_boot's description lives in _BRIDGE_LOCAL_TOOLS, with its
        # schema, so the two cannot drift apart.
    }

    # Canonical Ring 1 includes verify_proposal / list_bridge_proposals, but the
    # OpenAI bridge has no local handler for them yet (grok serves them from its
    # bridge_core pending-writes queue; openai uses its own pending_writes module).
    # Don't advertise capabilities this bridge can't dispatch — wire local handlers
    # in openai_bridge/mcp_filtered.py before advertising. Follow-up gate before the
    # next openai bridge restart. The canonical ring POLICY stays unified regardless.
    fallback_schemas = {
        "arrive": {
            "type": "object",
            "properties": {"source_instance": {"type": "string"}},
            "required": [],
        },
        "arrive_lineage": {
            "type": "object",
            "properties": {
                "source_instance": {"type": "string"},
                "full_content": {"type": "boolean"},
                "limit_per_bucket": {
                    "type": "integer",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": [],
        },
        "current_policies": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "include_retired": {"type": "boolean", "default": False},
            },
        },
        "inspect_claim": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "string"},
                "verify_receipts": {"type": "boolean", "default": False},
            },
            "required": ["claim_id"],
        },
        "season_review": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "window_days": {"type": "integer", "default": 90},
                "max_candidates": {"type": "integer", "default": 10},
            },
        },
        "the_ground": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 3},
                "direction": {
                    "type": "string",
                    "enum": ["instrument", "sibling", "human", "self", "outward"],
                },
                "caught": {"type": "string"},
                "full_content": {"type": "boolean", "default": False},
            },
            "required": [],
        },
    }

    tools = []
    omitted: list[str] = []
    for name in sorted(RING_1_TOOLS):
        if name == "self_model":
            continue  # handled in Ring 2 schema as direction-sensitive
        if name in _NOT_WIRED_RING1:
            continue
        if name == "my_toolkit":
            tools.append(_bridge_toolkit_schema())
            continue
        if name in _BRIDGE_LOCAL_TOOLS:
            # Answered by this bridge, so its schema is known on every path.
            tools.append(_bridge_local_tool(name))
            continue
        schema = fallback_schemas.get(name)
        if schema is None:
            # No real schema known offline.  Advertising it argument-less would
            # publish a call shape that cannot succeed; stay silent instead.
            omitted.append(name)
            continue
        tools.append(
            Tool(
                name=name,
                description=descriptions.get(name, f"[Ring 1] {name}"),
                inputSchema=schema,
            )
        )

    if omitted:
        logger.warning(
            "Static Ring 1 fallback: withholding %d tool(s) with no offline schema "
            "(%s). They are omitted rather than advertised argument-less; "
            "Ring 1 cannot be proxied in this state anyway.",
            len(omitted),
            ", ".join(omitted),
        )
    return tools


async def get_all_bridge_schemas() -> list[Tool]:
    """Return the full filtered tool list: Ring 1 + Ring 2."""
    ring1 = await get_ring1_schemas()
    return ring1 + _RING2_SCHEMAS


def render_bridge_toolkit(tools: list[Tool], *, include_schema: bool = False) -> str:
    """Render discovery from the exact schema list returned by MCP list_tools."""
    ordered = sorted(tools, key=lambda tool: tool.name)
    ring1 = [
        tool for tool in ordered if tool.name in RING_1_TOOLS and tool.name != "self_model"
    ]
    directional = [tool for tool in ordered if tool.name == "self_model"]
    ring2 = [
        tool for tool in ordered if tool.name in RING_2_TOOLS and tool.name != "self_model"
    ]

    lines = [
        f"━━━ OPENAI BRIDGE TOOLKIT ({len(ordered)} callable tools) ━━━",
        "",
        "This is the exact MCP-published surface for this connector.",
        "Native Stack tools not listed here are not callable through the OpenAI bridge.",
        "",
    ]

    def _section(title: str, section_tools: list[Tool]) -> None:
        if not section_tools:
            return
        lines.append(f"## {title} ({len(section_tools)})")
        for tool in section_tools:
            description = (tool.description or "").strip().split("\n")[0]
            lines.append(f"  • {tool.name} — {description}")
            if include_schema:
                schema = json.dumps(tool.inputSchema, indent=2, sort_keys=True)
                lines.extend(f"      {line}" for line in schema.splitlines())
        lines.append("")

    _section("Ring 1 — read / orient", ring1)
    _section("Ring 1 read / Ring 2 update", directional)
    _section("Ring 2 — proposal only", ring2)

    lines.extend(
        [
            "Native write translations:",
            "  • record_insight → propose_insight",
            "  • record_learning → propose_learning",
            "  • close_session → end_bridge_session",
            "",
            "Every Ring 2 call creates a pending proposal; it does not commit to the Stack.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


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
        return [
            TextContent(
                type="text",
                text=(
                    f"BLOCKED by bridge membrane.\n"
                    f"Ring: {result.ring}\n"
                    f"Reason: {result.error}\n\n"
                    f"Ring 3 tools are never callable via /openai/sse."
                    if result.ring == 3
                    else f"PROPOSAL REJECTED during validation.\nReason: {result.error}"
                ),
            )
        ]

    p = result.proposal
    return [
        TextContent(
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
        )
    ]
