from __future__ import annotations

"""
Identity gate — substrate-agnostic recognition at the door.

Per Anthony's instruction (2026-05-09): "the stack should recognize the id
of the visitor before entrance." This module is that recognition layer.

The gate fires at the SSE handshake (or REST request) BEFORE any tool call
lands. It verifies the bearer token presented by the connection against the
tokens registered for known substrates. On success it returns the substrate
identity (e.g. "grok-xai", "chatgpt-openai-bridge"); on failure it returns
None and the connection is rejected with 401.

Per-session identity (e.g. "grok-xai-20260509-001") is Grok-asserted in
tool-call payloads, not header-verified. The gate handles substrate-level
recognition only — session-level attribution is recorded by the audit layer
when the session declares its session_id in its first call.

Future hook: the gate is transport-independent. The same verify_at_door()
function will be called by /grok/api/call (when xAI grants Grok native
direct tool-calling) without modification.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubstrateIdentity:
    """Substrate-level identity registered with the gate."""

    substrate: str                 # canonical id, e.g. "grok-xai"
    bearer_token_env: str          # env var name holding the token
    audit_path: str                # ~/.sovereign/<bridge>/audit/
    pending_writes_path: str       # ~/.sovereign/<bridge>/pending_writes/
    sessions_path: str             # ~/.sovereign/<bridge>/sessions/
    session_id_pattern: Optional[str] = None  # e.g. "grok-xai-{YYYYMMDD}-{NNN}"
    session_id_required_in_first_call: bool = False


@dataclass
class IdentityGateResult:
    """Outcome of a door check."""

    allowed: bool
    substrate: Optional[str] = None
    reason: Optional[str] = None
    transport: Optional[str] = None    # "sse" | "rest" | future


# ── Registry ──────────────────────────────────────────────────────────────────
# Substrates register themselves with the gate at import time. Each bridge
# package's __init__.py calls register_substrate() to declare its identity.

_REGISTRY: dict[str, SubstrateIdentity] = {}

# Additional token validators per substrate (e.g. OAuth-issued tokens).
# Each validator is a callable: token_str -> bool. If any registered validator
# returns True, the token is accepted regardless of env-var match.
_TOKEN_VALIDATORS: dict[str, list[Callable[[str], bool]]] = {}


def register_substrate(identity: SubstrateIdentity) -> None:
    """Register a substrate's identity with the gate."""
    if identity.substrate in _REGISTRY:
        logger.warning("Substrate already registered: %s — overwriting", identity.substrate)
    _REGISTRY[identity.substrate] = identity
    logger.info(
        "Identity gate: registered substrate=%s token_env=%s",
        identity.substrate, identity.bearer_token_env,
    )


def register_token_validator(
    substrate: str,
    validator: Callable[[str], bool],
) -> None:
    """
    Register an additional token validator for a substrate.

    Used by OAuth shim to validate issued tokens alongside the static
    env-var token. Validator receives the presented bearer token string
    and returns True if it should be accepted.
    """
    _TOKEN_VALIDATORS.setdefault(substrate, []).append(validator)
    logger.info(
        "Identity gate: registered additional validator for substrate=%s "
        "(validator_count=%d)",
        substrate, len(_TOKEN_VALIDATORS[substrate]),
    )


def known_substrates() -> list[str]:
    """List currently registered substrates (for diagnostics)."""
    return sorted(_REGISTRY.keys())


# ── Door check ────────────────────────────────────────────────────────────────


def verify_at_door(
    scope: dict,
    expected_substrate: str,
    transport: str = "sse",
) -> IdentityGateResult:
    """
    Verify the bearer token on an incoming connection.

    Args:
        scope: ASGI scope dict (or REST request equivalent with .headers).
        expected_substrate: which substrate this endpoint serves
            (e.g. "grok-xai" for /grok/sse). The token must match the
            registered token for this substrate.
        transport: "sse" or "rest" (or future). Recorded on result for audit.

    Returns:
        IdentityGateResult.allowed=True with substrate set on success;
        IdentityGateResult.allowed=False with reason on failure.

    Defense: returns False if the substrate is not registered, if no token
    is present, if the token doesn't match the registered env var value,
    or if the env var is empty. Never returns True without a verified match.
    """
    identity = _REGISTRY.get(expected_substrate)
    if identity is None:
        logger.error(
            "Door check: unknown substrate=%s (registered: %s)",
            expected_substrate, known_substrates(),
        )
        return IdentityGateResult(
            allowed=False,
            reason=f"Unknown substrate: {expected_substrate}",
            transport=transport,
        )

    presented = _extract_bearer_token(scope)
    if presented is None:
        return IdentityGateResult(
            allowed=False,
            reason="Missing Authorization: Bearer header",
            transport=transport,
        )

    # Path 1: static env-var token (legacy / dev path)
    expected_token = os.environ.get(identity.bearer_token_env, "")
    if expected_token and presented == expected_token:
        return IdentityGateResult(
            allowed=True,
            substrate=expected_substrate,
            transport=transport,
        )

    # Path 2: additional validators (OAuth-issued tokens, etc.)
    for validator in _TOKEN_VALIDATORS.get(expected_substrate, []):
        try:
            if validator(presented):
                return IdentityGateResult(
                    allowed=True,
                    substrate=expected_substrate,
                    transport=transport,
                )
        except Exception as e:
            logger.error(
                "Token validator raised for substrate=%s: %s",
                expected_substrate, e,
            )

    # Both paths failed
    if not expected_token and not _TOKEN_VALIDATORS.get(expected_substrate):
        # No auth configured at all — refuse rather than allow
        logger.error(
            "Door check: no auth configured for substrate=%s "
            "(env var %s empty, no validators registered)",
            expected_substrate, identity.bearer_token_env,
        )
        return IdentityGateResult(
            allowed=False,
            reason=(
                f"Bridge auth not configured ({identity.bearer_token_env} unset, "
                "no OAuth validators). Identity gate refuses unauthenticated connections."
            ),
            transport=transport,
        )

    client = scope.get("client", ("unknown", 0))
    logger.warning(
        "Door check: token rejected substrate=%s client=%s",
        expected_substrate, client,
    )
    return IdentityGateResult(
        allowed=False,
        reason="Invalid bearer token",
        transport=transport,
    )


def _extract_bearer_token(scope: dict) -> Optional[str]:
    """Pull the bearer token out of an ASGI scope's headers."""
    headers = scope.get("headers", [])
    for key, value in headers:
        if key == b"authorization":
            decoded = value.decode("utf-8", errors="replace")
            if decoded.startswith("Bearer "):
                return decoded[7:].strip()
            return None
    return None


def get_substrate_identity(substrate: str) -> Optional[SubstrateIdentity]:
    """Look up a registered substrate's identity record (for audit/sessions)."""
    return _REGISTRY.get(substrate)


# ── Standard 401 response helpers ─────────────────────────────────────────────


async def send_401(
    send,
    reason: str,
    *,
    realm: str = "Sovereign Stack Bridge",
    resource_metadata_url: Optional[str] = None,
) -> None:
    """
    Send a 401 response with proper WWW-Authenticate header.

    Per MCP authorization spec (revision 2025-06+) and RFC 6750, OAuth-protected
    resources MUST include a WWW-Authenticate header on 401 responses pointing
    to the resource metadata document. Without this, MCP clients cannot
    discover the OAuth authorization server and will fail silently rather
    than retrying with credentials.

    Args:
        send: ASGI send callable
        reason: human-readable reason (also used as error_description)
        realm: protection realm (advertised to client)
        resource_metadata_url: URL of the protected-resource metadata document
            (e.g. https://stack.templetwo.com/grok/.well-known/oauth-protected-resource).
            Required for OAuth-discovery to work end-to-end.
    """
    # Build WWW-Authenticate per RFC 6750 + MCP auth spec
    www_auth_parts = [f'Bearer realm="{realm}"']
    www_auth_parts.append(f'error="invalid_token"')
    www_auth_parts.append(f'error_description="{reason}"')
    if resource_metadata_url:
        www_auth_parts.append(f'resource_metadata="{resource_metadata_url}"')
    www_auth_value = ", ".join(www_auth_parts).encode("utf-8")

    body = (
        b'{"error":"invalid_token","error_description":"' +
        reason.encode("utf-8") + b'"}'
    )
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"www-authenticate", www_auth_value),
    ]
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": headers,
    })
    await send({"type": "http.response.body", "body": body})
