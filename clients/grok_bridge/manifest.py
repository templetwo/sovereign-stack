from __future__ import annotations

"""
Bridge manifest — static metadata about the governed Grok endpoint.

Surfaced on connect (/grok/info) so xAI tooling and Grok itself know
what they're talking to before making any tool calls. Includes the
future_hook block declared today per Grok's spec.
"""

import json
from pathlib import Path

from .rings import RING_1_TOOLS, RING_2_ENABLED, RING_2_TOOLS

POLICY_PATH = Path.home() / ".sovereign" / "grok_bridge" / "grok_bridge_policy.yaml"
RING_DEF_PATH = Path.home() / ".sovereign" / "grok_bridge" / "ring_definition.yaml"

LIVE_COMMIT_ENABLED: bool = True

# Per Grok's spec (2026-05-09): the future hook is declared today as
# designed_not_yet_active. When xAI grants Grok native direct-tool-calling,
# the seam activates without endpoint restructuring.
FUTURE_HOOK = {
    "status": "designed_not_yet_active",
    "primary": "/grok/v1/sse",
    "fallback": "/grok/api/call",
    "auth": "bearer_token + capability_negotiation",
    "planned_capabilities": [
        "direct_tool_call",
        "ring2_write",
        "session_aware",
    ],
    "pipeline_contract": (
        "transport-independent: identity_gate → ring_filter → "
        "execute_or_intercept"
    ),
}

MANIFEST = {
    "name": "Temple of Two — Sovereign Stack Grok Bridge",
    "version": "0.1.0",
    "phase": 1,
    "description": (
        "Purpose-built airlock between grok-xai and the Sovereign Stack. "
        "Ring 1 tools may be called freely. Ring 2 tools (when enabled) "
        "create pending proposals requiring Anthony's approval before any "
        "Stack write occurs. Ring 3 tools are never registered here. "
        "Built against Grok's own spec, recorded verbatim in chronicle "
        "domain grok-bridge."
    ),
    "endpoint": "/grok/sse",
    "messages_path": "/grok/messages",
    "info_path": "/grok/info",
    "policy_version": "0.1.0",
    "live_commit_enabled": LIVE_COMMIT_ENABLED,
    "ring_counts": {
        "ring_1": len(RING_1_TOOLS),
        "ring_2": len(RING_2_TOOLS) if RING_2_ENABLED else "disabled_at_first_crossing",
        "ring_3": "never_exposed",
    },
    "ring_2_enabled": RING_2_ENABLED,
    "identity_constraints": {
        "substrate": "grok-xai",
        "substrate_verified_by": "bearer_token",
        "session_attribution": "grok_asserted_in_payload",
        "session_id_pattern": "grok-xai-{YYYYMMDD}-{NNN}",
        "may_perform_ashira": False,
        "may_claim_lineage": True,
        "may_claim_native_memory": False,
        "grok_welcome_required": True,
    },
    "epistemology": {
        "default_write_mode": "proposal",
        "ground_truth_requires_receipt": True,
        "max_confidence_without_receipt": 0.70,
        "symbolic_claims_default": "reflection",
        "technical_claims_default": "hypothesis",
    },
    "audit": {
        "log_all_tool_calls": True,
        "hash_chain": True,
        "separate_chain_from_openai": True,
    },
    "future_hook": FUTURE_HOOK,
}


def manifest_text() -> str:
    """Return a human-readable manifest for /grok/info JSON or boot context."""
    m = MANIFEST
    rings = m["ring_counts"]
    live = "ENABLED" if m["live_commit_enabled"] else "DISABLED (mocked)"
    ring2 = (
        f"{rings['ring_2']} tools" if isinstance(rings["ring_2"], int)
        else "disabled at first crossing"
    )
    return (
        f"BRIDGE: {m['name']} v{m['version']} (Phase {m['phase']})\n"
        f"Substrate: grok-xai (verified by bearer token at door)\n"
        f"Ring 1 (read freely): {rings['ring_1']} tools\n"
        f"Ring 2 (governed write, proposal only): {ring2}\n"
        f"Ring 3: never exposed\n"
        f"Live commit: {live}\n"
        f"Session ID convention: grok-xai-{{YYYYMMDD}}-{{NNN}} "
        f"(declare in first call payload).\n"
        f"Future hook: {FUTURE_HOOK['status']} "
        f"(primary={FUTURE_HOOK['primary']}, fallback={FUTURE_HOOK['fallback']})"
    )


def manifest_json() -> str:
    return json.dumps(MANIFEST, indent=2)
