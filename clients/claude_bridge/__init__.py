"""
Sovereign Stack — Claude Bridge

The claude.ai / Claude-app MCP connector route. Unlike the openai and grok
bridges (ring-filtered surfaces over SSE), this bridge serves the NATIVE
tool surface over Streamable HTTP with a gated blast radius:

  - "Unfiltered identity, gated blast radius" (the ratified 2026-07-04 spec):
    a Claude seat authenticates via OAuth 2.1 (mandatory PKCE S256, RFC 8707
    audience-bound tokens, short-lived access + rotating refresh) and reaches
    every native tool EXCEPT a destructive tier that requires a step-up
    approval through the Door That Asks (ntfy tap on Anthony's phone).

On import:
  - Registers the claude-ai-bridge SubstrateIdentity with bridge_core's
    identity gate. Deliberately NO bearer_token_env: this substrate accepts
    only its own OAuth-issued, audience-bound tokens — never the master
    BRIDGE_TOKEN (which would give the public route master blast radius and
    make rotation a multi-system event).
  - Imports oauth.py, which registers the OAuth token validator and exposes
    the authorize / token / register / revoke / discovery ASGI handlers.
"""

from pathlib import Path

from bridge_core import SubstrateIdentity, register_substrate

_SOVEREIGN_ROOT = Path.home() / ".sovereign" / "claude_bridge"

CLAUDE_IDENTITY = SubstrateIdentity(
    substrate="claude-ai-bridge",
    bearer_token_env="",  # deliberately empty: OAuth-issued tokens only, never a static env token
    audit_path=str(_SOVEREIGN_ROOT / "audit"),
    pending_writes_path=str(_SOVEREIGN_ROOT / "pending_writes"),
    sessions_path=str(_SOVEREIGN_ROOT / "sessions"),
    session_id_pattern=None,
    session_id_required_in_first_call=False,
)

register_substrate(CLAUDE_IDENTITY)

# Import the OAuth module — registers the audience-checking token validator
# with the identity gate and exposes the ASGI handlers wired in sse_server.py.
from . import oauth  # noqa: F401, E402
