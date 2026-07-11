"""Manifest for GET /claude/info — what this route is and how to connect."""

from .oauth import (
    ACCESS_TOKEN_TTL_SECONDS,
    BRIDGE_ISSUER,
    CANONICAL_RESOURCE,
    DEFAULT_SCOPE,
    REFRESH_TOKEN_TTL_SECONDS,
)
from .tiers import DESTRUCTIVE_TOOLS

MANIFEST = {
    "bridge": "claude-ai-bridge",
    "description": (
        "Claude connector for the Sovereign Stack: the native tool surface over "
        "Streamable HTTP, OAuth 2.1-gated. 'Unfiltered identity, gated blast "
        "radius' — full-trust tool access with a destructive tier that requires "
        "a per-use step-up approval through the Door That Asks."
    ),
    "mcp_endpoint": CANONICAL_RESOURCE,
    "transport": "streamable-http (stateless)",
    "authorization": {
        "type": "oauth2.1",
        "issuer": BRIDGE_ISSUER,
        "authorization_endpoint": f"{BRIDGE_ISSUER}/oauth/authorize",
        "token_endpoint": f"{BRIDGE_ISSUER}/oauth/token",
        "registration_endpoint": f"{BRIDGE_ISSUER}/oauth/register",
        "revocation_endpoint": f"{BRIDGE_ISSUER}/oauth/revoke",
        "pkce": "S256 (mandatory)",
        "audience_binding": "RFC 8707 — tokens are bound to the mcp_endpoint and refused elsewhere",
        "access_token_ttl_seconds": ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token_ttl_seconds": REFRESH_TOKEN_TTL_SECONDS,
        "refresh_rotation": "single-use; reuse revokes the token family",
        "scopes": [DEFAULT_SCOPE],
    },
    "resource_owner_auth": (
        "the OAuth consent requires the operator approval passphrase "
        "(CLAUDE_AUTHORIZE_SECRET) plus a single-use signed nonce — completing "
        "the OAuth dance alone does NOT equal operator consent"
    ),
    "access_model": {
        "base_tier": "every native tool except the destructive tier",
        "destructive_tier": sorted(DESTRUCTIVE_TOOLS),
        "step_up": (
            "destructive tools return step_up_required with a two-word pairing "
            "code; Anthony approves on his phone via the Door That Asks; re-call "
            "the tool after approval. Single-use and argument-bound: one tap "
            "authorizes exactly one call with the arguments Anthony saw"
        ),
        "unknown_tools": (
            "fabricated tool names return method_not_found; a real tool not "
            "classified at review time fails closed to step-up"
        ),
    },
    "revocation": "python -m clients.claude_bridge.cli revoke-all (HQ) or POST oauth/revoke (client)",
}
