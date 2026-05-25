from __future__ import annotations

"""
BridgeContext — substrate-specific configuration for the Ring 2 pipeline.

Each substrate (grok-xai, future) creates one BridgeContext at import time
and passes it to bridge_core's interceptor / pending_writes / audit / cli
functions. This keeps bridge_core fully substrate-agnostic.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BridgeContext:
    """Per-substrate configuration for Ring 2 governance."""

    # Substrate identity (matches identity_gate's SubstrateIdentity.substrate)
    substrate: str

    # Filesystem state directories (substrate-scoped)
    pending_writes_dir: Path
    audit_dir: Path
    sessions_dir: Path

    # Ring sets — what tools are exposed at each level
    ring_1_tools: frozenset[str]
    ring_2_tools: frozenset[str]

    # Ring 2 tool name → underlying Stack tool name for commit
    commit_targets: dict[str, str]

    # Bridge REST API for live commits (where Stack actually executes)
    bridge_rest_url: str = "http://127.0.0.1:8100"
    bridge_rest_token_env: str = "BRIDGE_TOKEN"

    # Bridge-layer → Stack-layer label translation
    # (e.g. bridge "reflection" semantically maps to Stack "hypothesis")
    layer_translation: dict[str, str] = field(default_factory=lambda: {"reflection": "hypothesis"})

    # Ring 2 capability probe — DEFAULTS TO FALSE (detector mode).
    #
    # When False (default): a probe timeout records an audit event and sets a
    # capability flag, but NEVER disables Ring 2 for this connection. The OpenAI
    # bridge leaves this False; its Ring 2 dispatch path is byte-for-byte unchanged.
    #
    # When True (opt-in hard-gate): a probe timeout disables Ring 2 for this
    # connection/session only. Global module state is never mutated.
    require_ring2_probe: bool = False

    # Convenience accessors

    @property
    def audit_log_path(self) -> Path:
        return self.audit_dir / "audit.jsonl"

    def is_ring_3(self, tool_name: str) -> bool:
        """Anything not in Ring 1 or Ring 2 is Ring 3 (never callable)."""
        return tool_name not in self.ring_1_tools and tool_name not in self.ring_2_tools


# ── Registry ──────────────────────────────────────────────────────────────────

_CONTEXTS: dict[str, BridgeContext] = {}


def register_context(ctx: BridgeContext) -> None:
    """Register a substrate's BridgeContext. Called by per-substrate package init."""
    _CONTEXTS[ctx.substrate] = ctx


def get_context(substrate: str) -> BridgeContext:
    """Look up a registered substrate's context, or raise KeyError."""
    if substrate not in _CONTEXTS:
        raise KeyError(
            f"No BridgeContext registered for substrate '{substrate}'. "
            f"Known: {sorted(_CONTEXTS.keys())}"
        )
    return _CONTEXTS[substrate]


def known_contexts() -> list[str]:
    """List substrates with registered contexts."""
    return sorted(_CONTEXTS.keys())
