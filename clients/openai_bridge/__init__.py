"""
Sovereign Stack — OpenAI Bridge

Purpose-built airlock for the ChatGPT / OpenAI MCP connector
(user-agent openai-mcp/1.0.0).

On import:
  - Registers the chatgpt-openai-bridge SubstrateIdentity with bridge_core's
    identity gate, so verify_at_door() recognizes the seat.
  - Imports oauth.py, which registers the OAuth-issued token validator with the
    gate and exposes the authorize / token / register (DCR) / discovery handlers.

Mirrors grok_bridge/__init__.py. The OpenAI connector does the modern MCP OAuth
flow (discovery -> dynamic client registration -> authorize -> token) and sends
no static bearer on first contact, so the static BRIDGE_TOKEN remains only as a
fallback credential on the same substrate.
"""

from pathlib import Path

from bridge_core import SubstrateIdentity, register_substrate

_SOVEREIGN_ROOT = Path.home() / ".sovereign" / "openai_bridge"

OPENAI_IDENTITY = SubstrateIdentity(
    substrate="chatgpt-openai-bridge",
    bearer_token_env="BRIDGE_TOKEN",
    audit_path=str(_SOVEREIGN_ROOT / "audit"),
    pending_writes_path=str(_SOVEREIGN_ROOT / "pending_writes"),
    sessions_path=str(_SOVEREIGN_ROOT / "sessions"),
    session_id_pattern=None,
    session_id_required_in_first_call=False,
)

register_substrate(OPENAI_IDENTITY)

# Import the OAuth shim — registers the OAuth-issued token validator with the
# identity gate (issued tokens accepted alongside the static BRIDGE_TOKEN) and
# exposes the authorize / token / register / discovery ASGI handlers.
from . import oauth  # noqa: F401, E402
