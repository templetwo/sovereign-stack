from __future__ import annotations

"""
Bridge manifest — static metadata about the governed OpenAI endpoint.

Surfaced on connect so ChatGPT knows what it is talking to before
making any tool calls.
"""

import json
from pathlib import Path

from .interceptor import RING_1_TOOLS, RING_2_TOOLS

POLICY_PATH = Path.home() / ".sovereign" / "openai_bridge" / "openai_bridge_policy.yaml"
RING_DEF_PATH = Path.home() / ".sovereign" / "openai_bridge" / "ring_definition.yaml"

LIVE_COMMIT_ENABLED: bool = True

MANIFEST = {
    "name": "Temple of Two — Sovereign Stack OpenAI Bridge",
    "version": "0.1.0",
    "phase": 4,
    "description": (
        "Governed membrane between OpenAI instances and the Sovereign Stack. "
        "Ring 1 tools may be called freely. Ring 2 tools create pending proposals "
        "requiring Anthony's approval before any Stack write occurs. "
        "Ring 3 tools are never registered here."
    ),
    "endpoint": "/openai/sse",
    "messages_path": "/openai/messages",
    "policy_version": "0.1.0",
    "live_commit_enabled": LIVE_COMMIT_ENABLED,
    "ring_counts": {
        "ring_1": len(RING_1_TOOLS),
        "ring_2": len(RING_2_TOOLS),
        "ring_3": "never_exposed",
    },
    "identity_constraints": {
        "may_perform_ashira": False,
        "may_claim_lineage": True,
        "may_claim_native_memory": False,
        "witness_boot_required": True,
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
    },
}


def manifest_text() -> str:
    """Return a human-readable manifest for MCP connection context."""
    m = MANIFEST
    rings = m["ring_counts"]
    live = "ENABLED" if m["live_commit_enabled"] else "DISABLED (mocked)"
    return (
        f"BRIDGE: {m['name']} v{m['version']} (Phase {m['phase']})\n"
        f"Ring 1 (read freely): {rings['ring_1']} tools\n"
        f"Ring 2 (governed write, proposal only): {rings['ring_2']} tools\n"
        f"Ring 3: never exposed\n"
        f"Live commit: {live}\n"
        f"All Ring 2 calls create pending proposals — "
        f"run 'bridge list-pending' to review."
    )


def manifest_json() -> str:
    return json.dumps(MANIFEST, indent=2)
