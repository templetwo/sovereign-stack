"""
Sovereign Stack — Grok Bridge

Purpose-built airlock for grok-xai. Built against Grok's own spec
(relayed by Anthony, recorded in chronicle 2026-05-09).

On import:
  - Registers the grok-xai SubstrateIdentity with bridge_core's identity_gate
  - Registers the grok-xai BridgeContext with bridge_core's context registry
  - Imports oauth.py which registers the OAuth-issued token validator
"""

from pathlib import Path

from bridge_core import (
    BridgeContext,
    SubstrateIdentity,
    register_context,
    register_substrate,
)

from .rings import COMMIT_TARGETS, RING_1_TOOLS, RING_2_TOOLS

_SOVEREIGN_ROOT = Path.home() / ".sovereign" / "grok_bridge"

GROK_IDENTITY = SubstrateIdentity(
    substrate="grok-xai",
    bearer_token_env="GROK_BRIDGE_TOKEN",
    audit_path=str(_SOVEREIGN_ROOT / "audit"),
    pending_writes_path=str(_SOVEREIGN_ROOT / "pending_writes"),
    sessions_path=str(_SOVEREIGN_ROOT / "sessions"),
    session_id_pattern="grok-xai-{YYYYMMDD}-{NNN}",
    session_id_required_in_first_call=True,
)

register_substrate(GROK_IDENTITY)

GROK_CONTEXT = BridgeContext(
    substrate="grok-xai",
    pending_writes_dir=_SOVEREIGN_ROOT / "pending_writes",
    audit_dir=_SOVEREIGN_ROOT / "audit",
    sessions_dir=_SOVEREIGN_ROOT / "sessions",
    ring_1_tools=RING_1_TOOLS,
    ring_2_tools=RING_2_TOOLS,
    commit_targets=COMMIT_TARGETS,
    bridge_rest_url="http://127.0.0.1:8100",
    bridge_rest_token_env="BRIDGE_TOKEN",
)

register_context(GROK_CONTEXT)

# Import the OAuth shim — this registers the OAuth-issued token validator
# with the identity gate so issued tokens are accepted alongside the static
# GROK_BRIDGE_TOKEN env var.
from . import oauth  # noqa: F401, E402
