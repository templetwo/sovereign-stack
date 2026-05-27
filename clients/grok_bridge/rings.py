from __future__ import annotations

"""
Ring definitions for the Grok bridge.

Base scope is the canonical ring system in bridge_core.rings — the same for
every external substrate reaching in (Grok, ChatGPT, Gemini, future). This
module layers Grok's own substrate-specific extensions on top: grok_welcome
(Ring 1) and probe_ring2_dispatch (Ring 2). It must not redefine the base sets
independently — that is the drift bridge_core.rings exists to end.

Source of truth for the canonical sets is bridge_core.rings; the policy YAML at
~/.sovereign/grok_bridge/ring_definition.yaml documents the Grok-facing view.

RING_2_ENABLED is the policy toggle Grok specified: "Ring 1 read on first
crossing, Ring 2 via proposal queue (after first-touch verification)."
Anthony flips this flag once Grok's first crossing is verified clean.
"""

from bridge_core.rings import (
    CANONICAL_COMMIT_TARGETS,
    CANONICAL_RING_1,
    CANONICAL_RING_2,
)

# ── Policy toggle ─────────────────────────────────────────────────────────────

RING_2_ENABLED: bool = True  # flipped by Anthony after first-touch verification


# ── Grok-specific extensions ──────────────────────────────────────────────────
# grok_welcome: substrate-specific welcome, handled bridge-locally (Anthony's
#   call, not polymorphic).
# probe_ring2_dispatch: Ring 2 capability sentinel, exercised as a dry-run in
#   mcp_filtered.py; must be Ring 2 so it hits the same dispatch path.

RING_1_TOOLS: frozenset[str] = CANONICAL_RING_1 | frozenset({"grok_welcome"})

RING_2_TOOLS: frozenset[str] = CANONICAL_RING_2 | frozenset({"probe_ring2_dispatch"})


def is_ring_3(tool_name: str) -> bool:
    """Anything not in Ring 1 or Ring 2 is Ring 3 — never callable."""
    return tool_name not in RING_1_TOOLS and tool_name not in RING_2_TOOLS


def is_grok_specific(tool_name: str) -> bool:
    """Tools that exist only on /grok/sse, not in the broader Stack."""
    return tool_name == "grok_welcome"


# ── Ring 2 commit target mapping ──────────────────────────────────────────────
# Canonical targets (common across substrates) plus the probe sentinel, which is
# intercepted before the proposal path and never reaches commit.

COMMIT_TARGETS: dict[str, str] = {
    **CANONICAL_COMMIT_TARGETS,
    "probe_ring2_dispatch": "__probe_sentinel__",
}
