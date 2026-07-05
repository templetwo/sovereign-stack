"""
Tool tiers for the Claude connector — "unfiltered identity, gated blast radius".

The Claude seat sees and may call the ENTIRE native tool surface (no ring
filtering — Claude operates the Stack natively, per the v1.5.2 full-trust
frame). What is gated is blast radius: a small destructive tier requires a
per-use step-up approval through the Door That Asks before it executes.

Classification is FAIL-CLOSED three ways:
  - DESTRUCTIVE_TOOLS       → step-up required, always.
  - BASE_TOOLS              → allowed with a valid audience-bound access token.
  - anything else           → step-up required. A tool registered after this
    review (or a typo'd name) is treated as destructive until a human
    classifies it into BASE_TOOLS. New capability never silently lands in
    the remote seat's base tier.

Both sets were frozen from the live 94-tool registry (v1.11.0, 2026-07-04).
When the native registry grows, the new tool trips the unknown→step-up path
on first remote use, which is the signal to classify it here.

Spec-category mapping (ratified 2026-07-04 build spec, item 6):
  policy mutation      → set_policy
  deletion/retirement  → supersede_insight, retire_hypothesis, metabolize,
                         guardian_quarantine
  service control      → govern, synthesize_now (paid API run),
                         watch_cancel, watch_resample, guardian_baseline
  protected drawer     → open_protected_record (content+stakes retrieval;
                         listing thresholds and declining stay base tier —
                         they surface no protected content)
  token minting        → no native MCP tool mints tokens (verified against
                         the registry); minting surfaces are the OAuth
                         endpoints and the Door itself, which carry their
                         own human gates.

Known side-effect note (not gated, documented): where_did_i_leave_off
CONSUMES unconsumed handoffs on read. A remote Claude seat booting with it
will eat handoffs addressed to whoever boots next at HQ. arrive_lineage is
the side-effect-free door and is what remote seats are steered toward.
"""

from __future__ import annotations

DESTRUCTIVE_TOOLS: frozenset[str] = frozenset(
    {
        # policy mutation
        "set_policy",
        # deletion / retirement / surfacing-destruction
        "supersede_insight",
        "retire_hypothesis",
        "metabolize",
        "guardian_quarantine",
        # service control / cost-bearing triggers
        "govern",
        "synthesize_now",
        "watch_cancel",
        "watch_resample",
        "guardian_baseline",
        # protected drawer content
        "open_protected_record",
    }
)

# The remainder of the live registry at freeze time (94 - 11 = 83 tools).
BASE_TOOLS: frozenset[str] = frozenset(
    {
        "route",
        "derive",
        "scan_thresholds",
        "compass_check",
        "record_insight",
        "archive_exchange",
        "recall_exchange",
        "list_exchanges",
        "record_learning",
        "recall_insights",
        "check_mistakes",
        "record_open_thread",
        "resolve_thread",
        "resolve_thread_by_id",
        "get_open_threads",
        "get_inheritable_context",
        "handoff",
        "close_session",
        "where_did_i_leave_off",
        "arrive",
        "arrive_delta",
        "arrive_lineage",
        "ask_scribe",
        "spiral_status",
        "spiral_reflect",
        "spiral_inherit",
        "comms_recall",
        "comms_unread_bodies",
        "comms_channels",
        "my_toolkit",
        "start_here",
        "nape_observe",
        "nape_honks",
        "nape_ack",
        "record_prior_alignment",
        "prior_alignment_summary",
        "nape_honks_with_history",
        "nape_summary",
        "comms_acknowledge",
        "comms_get_acks",
        "thread_touch",
        "thread_get_touches",
        "handoff_acted_on",
        "handoff_acted_on_records",
        "reflexive_surface",
        "prior_for_turn",
        "triage_threads",
        "recall_reflections",
        "reflection_ack",
        "list_protected_thresholds",
        "decline_protected_record",
        "agent_reflect",
        "mark_uncertainty",
        "resolve_uncertainty",
        "record_collaborative_insight",
        "record_breakthrough",
        "propose_experiment",
        "complete_experiment",
        "end_session_review",
        "get_growth_summary",
        "get_my_patterns",
        "get_unresolved_uncertainties",
        "get_pending_experiments",
        "store_compaction_summary",
        "get_compaction_context",
        "get_compaction_stats",
        "guardian_status",
        "guardian_scan",
        "guardian_alerts",
        "guardian_audit",
        "guardian_report",
        "guardian_mcp_audit",
        "self_model",
        "session_handoff",
        "context_retrieve",
        "post_fix_verify",
        "watch_status",
        "connectivity_status",
        "stack_write_check",
        "current_policies",
        "inspect_claim",
        "link_threads",
        "season_review",
    }
)

# Sanity: the two sets must never overlap. (Module-load assertion, cheap.)
assert not (DESTRUCTIVE_TOOLS & BASE_TOOLS), "tier sets overlap"

TIER_BASE = "base"
TIER_STEP_UP = "step_up"


def classify(tool_name: str) -> str:
    """Fail-closed tier classification. Unknown tools require step-up."""
    if tool_name in DESTRUCTIVE_TOOLS:
        return TIER_STEP_UP
    if tool_name in BASE_TOOLS:
        return TIER_BASE
    return TIER_STEP_UP
