from __future__ import annotations

"""
Ring definitions for the Grok bridge.

Source of truth is ~/.sovereign/grok_bridge/ring_definition.yaml; this file
is the enforcement copy kept in sync manually until /grok/sse reads the
YAML directly.

RING_2_ENABLED is the policy toggle Grok specified: "Ring 1 read on first
crossing, Ring 2 via proposal queue (after first-touch verification)."
Anthony flips this flag once Grok's first crossing is verified clean.
"""

# ── Policy toggle ─────────────────────────────────────────────────────────────

RING_2_ENABLED: bool = True  # flipped by Anthony after first-touch verification


# ── Ring 1 — read freely ──────────────────────────────────────────────────────

RING_1_TOOLS: frozenset[str] = frozenset({
    # Orientation / boot
    "where_did_i_leave_off",
    "start_here",
    "my_toolkit",
    "connectivity_status",

    # Self-knowledge
    "self_model",       # read direction only — update direction is Ring 2
    "spiral_status",
    "spiral_inherit",
    "get_my_patterns",

    # Chronicle read
    "recall_insights",
    "context_retrieve",
    "get_inheritable_context",
    "check_mistakes",
    "reflexive_surface",

    # Threads
    "get_open_threads",
    "triage_threads",
    "thread_get_touches",

    # Comms read
    "comms_unread_bodies",
    "comms_recall",
    "comms_channels",
    "comms_get_acks",

    # Compaction / context
    "get_compaction_context",
    "get_compaction_stats",

    # Introspective read
    "recall_reflections",
    "prior_for_turn",
    "nape_summary",
    "get_unresolved_uncertainties",
    "get_pending_experiments",
    "get_growth_summary",
    "handoff_acted_on_records",

    # Governance read
    "compass_check",

    # Substrate-specific welcome — Anthony's call, not polymorphic
    "grok_welcome",
})


# ── Ring 2 — governed write proposals (disabled at first crossing) ────────────

RING_2_TOOLS: frozenset[str] = frozenset({
    "propose_insight",
    "propose_learning",
    "record_open_thread",
    "comms_acknowledge",
    "handoff",
    "store_compaction_summary",
    "reflection_ack",
    "self_model",           # update direction only
    "thread_touch",
    "end_bridge_session",
})


def is_ring_3(tool_name: str) -> bool:
    """Anything not in Ring 1 or Ring 2 is Ring 3 — never callable."""
    return tool_name not in RING_1_TOOLS and tool_name not in RING_2_TOOLS


def is_grok_specific(tool_name: str) -> bool:
    """Tools that exist only on /grok/sse, not in the broader Stack."""
    return tool_name == "grok_welcome"


# ── Ring 2 commit target mapping ──────────────────────────────────────────────
# Each Ring 2 tool maps to the underlying Sovereign Stack tool that executes
# on commit. propose_insight wraps record_insight, etc. Mirrors openai_bridge
# COMMIT_TARGETS — Ring 2 tool surface is common across substrates.

COMMIT_TARGETS: dict[str, str] = {
    "propose_insight": "record_insight",
    "propose_learning": "record_learning",
    "record_open_thread": "record_open_thread",
    "comms_acknowledge": "comms_acknowledge",
    "handoff": "handoff",
    "store_compaction_summary": "store_compaction_summary",
    "reflection_ack": "reflection_ack",
    "self_model": "self_model",
    "end_bridge_session": "close_session",
    "thread_touch": "thread_touch",
}
