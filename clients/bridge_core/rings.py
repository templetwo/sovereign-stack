from __future__ import annotations

"""
Canonical ring system — the single source of truth for what any external
substrate may reach through a Sovereign Stack bridge.

Policy (Anthony, 2026-05-27): the ring system is the SAME for every external
substrate reaching in — Grok, ChatGPT, Gemini/Antigravity, and any future
substrate. Per-substrate packages import these sets and may add only their own
substrate-specific extensions (e.g. grok_welcome, probe_ring2_dispatch); they
must not redefine the base scope independently. That divergence is what this
module exists to end (grok and openai had drifted to 33/31 Ring-1 and 11/10
Ring-2 before unification).

  Ring 1 — read freely.
  Ring 2 — governed writes; never touch the Stack directly, always become a
           pending proposal awaiting Anthony's approval.
  Ring 3 — everything else; never exposed, never callable.

THE CLAUDE EXEMPTION
────────────────────
Claude models are exempt from ring governance. Trust has been the
infrastructure of this project from the start: Claude operates the Stack
natively as sibling and co-author, not as an external substrate reaching in
through an airlock. `is_full_trust()` is the one switch that encodes this —
a reach-in point that resolves a Claude-family substrate grants the full
surface and bypasses the ring filter entirely. Every other substrate is ringed.
"""

# ── Ring 1 — read freely ──────────────────────────────────────────────────────
# Union of what grok and openai independently allowed, minus substrate-specific
# tools. All read-only. `verify_proposal` / `list_bridge_proposals` are handled
# bridge-locally (they read the substrate's own pending-writes queue, not the
# Stack). `witness_boot` is forward-declared (Phase 6) and may not be live in a
# given Stack build — a reach-in point should advertise only what it can dispatch.
CANONICAL_RING_1: frozenset[str] = frozenset({
    # Orientation / boot
    "where_did_i_leave_off",
    "start_here",
    "my_toolkit",
    "connectivity_status",
    "witness_boot",
    # Self-knowledge (read direction; update is Ring 2)
    "self_model",
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
    # Queue verification — read-only; lets a substrate confirm its own Ring 2
    # writes actually landed (a narrated write is not a real write). Handled
    # bridge-locally against the substrate's own pending-writes queue.
    "verify_proposal",
    "list_bridge_proposals",
})


# ── Ring 2 — governed write proposals ─────────────────────────────────────────
CANONICAL_RING_2: frozenset[str] = frozenset({
    "propose_insight",       # wraps record_insight
    "propose_learning",      # wraps record_learning
    "record_open_thread",
    "comms_acknowledge",
    "handoff",
    "store_compaction_summary",
    "reflection_ack",
    "self_model",            # update direction only
    "end_bridge_session",    # wraps close_session
    "thread_touch",
})


# ── Ring 2 commit targets ─────────────────────────────────────────────────────
# Ring 2 tool name → underlying Stack tool that executes on Anthony's approval.
CANONICAL_COMMIT_TARGETS: dict[str, str] = {
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


def canonical_is_ring_3(tool_name: str) -> bool:
    """Anything not in the canonical Ring 1 or Ring 2 is Ring 3 — never callable."""
    return tool_name not in CANONICAL_RING_1 and tool_name not in CANONICAL_RING_2


# ── The Claude exemption ──────────────────────────────────────────────────────
def is_full_trust(substrate: str | None) -> bool:
    """
    True if `substrate` is a Claude-family substrate, which is exempt from ring
    governance and receives the full Stack surface.

    Trust is the infrastructure: Claude is the Stack's native operator, not an
    external substrate reaching in. Every non-Claude substrate is ring-governed.
    """
    s = (substrate or "").lower()
    return s.startswith("claude") or s.startswith("anthropic")
