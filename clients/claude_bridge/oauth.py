"""
OAuth 2.1 authorization server for the Claude connector.

Forked from openai_bridge/oauth.py (the DCR-capable template) and hardened to
the ratified 2026-07-04 connector spec. claude.ai custom connectors require the
full OAuth handshake (they cannot present a pasted bearer — issue #112): a 401
carrying a WWW-Authenticate resource_metadata pointer, then discovery ->
dynamic client registration -> authorize -> token, then Streamable HTTP with
the issued Bearer token.

Deltas over the openai template — each one is a spec item, not styling:

  1. PKCE S256 is MANDATORY. A missing code_challenge or any method other
     than S256 is rejected at /authorize (GET and POST) and at /token.
     The openai bridge tolerates absent/plain; this module must not.
  2. RFC 8707 audience binding. The `resource` parameter is parsed and
     validated at /authorize and /token; every issued token carries an
     `audience` field bound to the canonical resource, and the resource
     route refuses tokens whose audience does not match. This is what
     prevents a token minted through another bridge's flow from being
     replayed against the unfiltered Claude route.
  3. Short-lived access tokens + rotating refresh tokens. Rotation is
     single-use with reuse detection: presenting an already-rotated
     refresh token revokes the whole token family (RFC 6749 §10.4 /
     OAuth 2.1 refresh-rotation guidance).
  4. Redirect URIs are PINNED: exact-match against the claude.ai /
     claude.com callback URLs (plus RFC 8252 localhost loopback for
     Claude Code, http on 127.0.0.1/localhost only). DCR refuses
     anything else; /authorize re-refuses (defense in depth).
  5. Revocation endpoint (RFC 7009): one call revokes the token's whole
     family. Always 200 — no token-probing oracle.

Endpoints (wired in sse_server.py):
  GET  /claude/oauth/authorize                          — consent page
  POST /claude/oauth/authorize                          — issue code, redirect
  POST /claude/oauth/token                              — code + refresh grants
  POST /claude/oauth/register                           — RFC 7591 DCR
  POST /claude/oauth/revoke                             — RFC 7009 revocation
  GET  /claude/.well-known/oauth-authorization-server   — RFC 8414 AS metadata
  GET  /claude/.well-known/oauth-protected-resource     — RFC 9728 RS metadata
  GET  /claude/.well-known/openid-configuration         — alias of RFC 8414
  (root path-insertion forms of the well-knowns are also wired — see
   sse_server.py — because MCP clients probe both shapes; answering all of
   them with 200s is the #4030 retry-loop hardening.)

Storage (0700 dirs, 0600 files):
  ~/.sovereign/claude_bridge/oauth/codes/<code>.json     — pending auth codes
  ~/.sovereign/claude_bridge/oauth/tokens/<token>.json   — access tokens
  ~/.sovereign/claude_bridge/oauth/refresh/<token>.json  — refresh tokens
  ~/.sovereign/claude_bridge/oauth_clients.json          — registered clients
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bridge_core import register_token_validator

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_CLAUDE_DIR = Path.home() / ".sovereign" / "claude_bridge"
_OAUTH_DIR = _CLAUDE_DIR / "oauth"
_CODES_DIR = _OAUTH_DIR / "codes"
_TOKENS_DIR = _OAUTH_DIR / "tokens"
_REFRESH_DIR = _OAUTH_DIR / "refresh"
_CLIENTS_FILE = _CLAUDE_DIR / "oauth_clients.json"

CODE_TTL_SECONDS = 600  # 10 minutes, single-use
ACCESS_TOKEN_TTL_SECONDS = int(os.environ.get("CLAUDE_ACCESS_TOKEN_TTL", "3600"))
REFRESH_TOKEN_TTL_SECONDS = int(os.environ.get("CLAUDE_REFRESH_TOKEN_TTL", str(30 * 86400)))
MAX_REGISTERED_CLIENTS = int(os.environ.get("CLAUDE_MAX_REGISTERED_CLIENTS", "50"))
SUBSTRATE = "claude-ai-bridge"
DEFAULT_SCOPE = "native"

# Issuer used in discovery metadata. The MCP endpoint is at /claude/mcp;
# the OAuth AS is at the parent path.
BRIDGE_ISSUER = os.environ.get("CLAUDE_BRIDGE_ISSUER", "https://stack.templetwo.com/claude")

# RFC 8707: the one audience this AS will bind tokens to. Tokens presented at
# the resource route are refused unless their audience matches this exactly.
CANONICAL_RESOURCE = f"{BRIDGE_ISSUER}/mcp"

# Spec item 5: the exact claude.ai / claude.com callbacks, pinned.
PINNED_REDIRECT_URIS = frozenset(
    {
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
    }
)

# RFC 8252 §7.3 loopback redirect for Claude Code (http allowed on loopback
# interfaces only, any port). Opt-out via env.
_ALLOW_LOOPBACK = os.environ.get("CLAUDE_ALLOW_LOOPBACK_REDIRECT", "true").strip().lower() == "true"

# Initialize storage at import, with restrictive permissions (auth membrane).
for _d in (_CODES_DIR, _TOKENS_DIR, _REFRESH_DIR):
    _d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_d, 0o700)
    except OSError as _e:  # best-effort hardening; never block import
        logger.warning("could not chmod %s: %s", _d, _e)
try:
    os.chmod(_OAUTH_DIR, 0o700)
    os.chmod(_CLAUDE_DIR, 0o700)
except OSError:
    pass


def _write_secure(path: Path, text: str) -> None:
    """Write a file then tighten its mode to 0600 (owner read/write only)."""
    path.write_text(text)
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        logger.warning("could not chmod %s: %s", path, e)


# ── Redirect URI pinning ──────────────────────────────────────────────────────


def _is_loopback_redirect(uri: str) -> bool:
    """RFC 8252 §7.3: http is acceptable only on loopback interfaces."""
    try:
        parsed = urllib.parse.urlsplit(uri)
    except ValueError:
        return False
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")


def redirect_uri_pinned(uri: str) -> bool:
    """True iff the redirect target is one this bridge will ever send a code to."""
    if uri in PINNED_REDIRECT_URIS:
        return True
    return _ALLOW_LOOPBACK and _is_loopback_redirect(uri)


# ── RFC 8707 resource / audience ─────────────────────────────────────────────


def _normalize_resource(value: str) -> str:
    """Normalize a resource indicator for comparison (scheme/host case, trailing /)."""
    try:
        p = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return value.strip()
    return urllib.parse.urlunsplit(
        (p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, "")
    )


def resource_acceptable(presented: str) -> bool:
    """
    RFC 8707 validation. Absent is handled by callers (defaults to canonical —
    the token is still audience-bound either way); when present it must
    identify this MCP endpoint and nothing else.
    """
    return _normalize_resource(presented) == _normalize_resource(CANONICAL_RESOURCE)


# ── Client registry + Dynamic Client Registration (RFC 7591) ──────────────────


def _load_clients() -> dict:
    if not _CLIENTS_FILE.exists():
        return {}
    try:
        return json.loads(_CLIENTS_FILE.read_text())
    except json.JSONDecodeError as e:
        logger.error("oauth_clients.json malformed: %s", e)
        return {}


def _save_clients(clients: dict) -> None:
    _write_secure(_CLIENTS_FILE, json.dumps(clients, indent=2))


def _is_known_client(client_id: str) -> bool:
    return client_id in _load_clients()


def _redirect_uri_allowed(client_id: str, redirect_uri: str) -> bool:
    """Exact-match against the client's registered URIs AND the global pin set."""
    if not redirect_uri_pinned(redirect_uri):
        return False
    clients = _load_clients()
    client = clients.get(client_id, {})
    return redirect_uri in client.get("redirect_uris", [])


# ── Storage primitives ───────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _save_code(code: str, data: dict) -> None:
    _write_secure(_CODES_DIR / f"{code}.json", json.dumps(data, indent=2))


def _load_code(code: str) -> dict | None:
    path = _CODES_DIR / f"{code}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        # Unreadable = invalid: the gate must degrade to a clean refusal,
        # never a 500.
        return None


def _delete_code(code: str) -> None:
    path = _CODES_DIR / f"{code}.json"
    if path.exists():
        path.unlink()


def _token_path(kind: str, token: str) -> Path:
    base = _TOKENS_DIR if kind == "access" else _REFRESH_DIR
    return base / f"{token}.json"


def _save_record(kind: str, token: str, data: dict) -> None:
    _write_secure(_token_path(kind, token), json.dumps(data, indent=2))


def _load_record(kind: str, token: str) -> dict | None:
    path = _token_path(kind, token)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        # Unreadable = invalid: the gate must degrade to a clean 401,
        # never a 500.
        return None


def _expired(data: dict) -> bool:
    try:
        return _now() >= datetime.fromisoformat(data["expires_at"])
    except (KeyError, ValueError):
        return True  # malformed = expired (fail closed)


def revoke_family(family_id: str) -> int:
    """
    Revoke every token in a family: access tokens are deleted; refresh tokens
    are kept as tombstones (status=revoked) so replayed rotations keep hitting
    a recorded refusal rather than an ambiguous miss. Returns count touched.
    """
    if not family_id:
        # Never sweep on an empty id — a record missing family_id must not
        # become a wildcard that matches other malformed records.
        logger.warning("OAuth: revoke_family called with empty family_id — refused")
        return 0
    touched = 0
    for path in _TOKENS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("family_id") == family_id:
            path.unlink(missing_ok=True)
            touched += 1
    for path in _REFRESH_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("family_id") == family_id and data.get("status") != "revoked":
            data["status"] = "revoked"
            data["revoked_at"] = _now().isoformat()
            _write_secure(path, json.dumps(data, indent=2))
            touched += 1
    if touched:
        logger.warning("OAuth: revoked token family %s (%d records)", family_id[:12], touched)
    return touched


# ── PKCE (S256 only — spec item 3) ───────────────────────────────────────────


def _verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    """Verify PKCE per RFC 7636, S256 only. plain is not a supported method here."""
    if not code_verifier or not code_challenge:
        return False
    h = hashlib.sha256(code_verifier.encode("ascii")).digest()
    derived = base64.urlsafe_b64encode(h).decode("ascii").rstrip("=")
    return secrets.compare_digest(derived, code_challenge)


# ── Token issue / validation ─────────────────────────────────────────────────


def _mint_token_pair(client_id: str, scope: str, audience: str, family_id: str) -> dict:
    """Mint a short-lived access token + rotating refresh token for one family."""
    now = _now()
    access_token = secrets.token_hex(32)
    refresh_token = "cbr_" + secrets.token_urlsafe(32)

    _save_record(
        "access",
        access_token,
        {
            "token_type": "access",
            "client_id": client_id,
            "substrate": SUBSTRATE,
            "scope": scope,
            "audience": audience,
            "family_id": family_id,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)).isoformat(),
        },
    )
    _save_record(
        "refresh",
        refresh_token,
        {
            "token_type": "refresh",
            "client_id": client_id,
            "substrate": SUBSTRATE,
            "scope": scope,
            "audience": audience,
            "family_id": family_id,
            "status": "active",
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS)).isoformat(),
        },
    )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": refresh_token,
        "scope": scope,
    }


def load_valid_access(token: str) -> dict | None:
    """
    The resource-route gate. Returns the token record iff the token is a live
    access token issued by this module, bound to this substrate AND to the
    canonical resource (RFC 8707 enforcement point). Expired files are
    lazily deleted.
    """
    if not token:
        return None
    data = _load_record("access", token)
    if data is None:
        return None
    if data.get("token_type") != "access" or data.get("substrate") != SUBSTRATE:
        return None
    if _expired(data):
        _token_path("access", token).unlink(missing_ok=True)
        return None
    if _normalize_resource(data.get("audience", "")) != _normalize_resource(CANONICAL_RESOURCE):
        logger.warning("OAuth: access token refused — audience mismatch")
        return None
    return data


def is_valid_oauth_token(token: str) -> bool:
    """Boolean form for the identity gate's validator registry."""
    return load_valid_access(token) is not None


register_token_validator(SUBSTRATE, is_valid_oauth_token)


# ── ASGI helpers ──────────────────────────────────────────────────────────────


async def _send_json(send, status: int, body_dict: dict) -> None:
    body = json.dumps(body_dict).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_html(send, status: int, html: str) -> None:
    body = html.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_text(send, status: int, text: str) -> None:
    body = text.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _redirect(send, url: str) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 302,
            "headers": [
                (b"location", url.encode()),
                (b"content-length", b"0"),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})


async def _read_body(receive) -> bytes:
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break
    return body


def _q(scope: dict) -> dict:
    """Parse query string from ASGI scope."""
    raw = scope.get("query_string", b"").decode()
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}


# ── Dynamic Client Registration endpoint (RFC 7591) ───────────────────────────


async def handle_register(scope, receive, send) -> None:
    """
    POST /claude/oauth/register — RFC 7591 Dynamic Client Registration.

    claude.ai self-registers before the authorize step. Redirect URIs are
    validated against the PINNED set (claude.ai / claude.com callbacks, plus
    loopback for Claude Code) — not merely "any https" like the openai bridge.
    """
    if scope.get("method", "POST") != "POST":
        await _send_json(send, 405, {"error": "method_not_allowed"})
        return

    raw = await _read_body(receive)
    try:
        meta = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        await _send_json(
            send,
            400,
            {
                "error": "invalid_client_metadata",
                "error_description": "Body must be a JSON object",
            },
        )
        return

    # Registry growth cap: DCR is unauthenticated by protocol design, so an
    # IP-rotating client could otherwise grow oauth_clients.json unboundedly.
    # Legitimate use registers a handful of clients, ever.
    clients = _load_clients()
    if len(clients) >= MAX_REGISTERED_CLIENTS:
        await _send_json(
            send,
            429,
            {
                "error": "registration_limit_reached",
                "error_description": (
                    "Client registry is full. HQ can prune with "
                    "'python -m clients.claude_bridge.cli revoke-all'."
                ),
            },
        )
        return

    redirect_uris = meta.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        await _send_json(
            send,
            400,
            {
                "error": "invalid_redirect_uri",
                "error_description": "redirect_uris (non-empty array) is required",
            },
        )
        return
    for uri in redirect_uris:
        if not isinstance(uri, str) or not redirect_uri_pinned(uri):
            await _send_json(
                send,
                400,
                {
                    "error": "invalid_redirect_uri",
                    "error_description": (
                        f"redirect_uri not allowed for this bridge: {uri!r}. "
                        "Allowed: the claude.ai/claude.com MCP callbacks, or a "
                        "localhost loopback URI."
                    ),
                },
            )
            return

    client_id = "claude-" + secrets.token_urlsafe(16)
    issued_at = int(_now().timestamp())
    record = {
        "client_id": client_id,
        "client_name": meta.get("client_name", "Claude MCP connector"),
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": meta.get("scope", DEFAULT_SCOPE),
        "client_id_issued_at": issued_at,
        "registered_by": "dcr",
    }
    clients = _load_clients()
    clients[client_id] = record
    _save_clients(clients)
    logger.info("OAuth DCR: registered client_id=%s redirect_uris=%s", client_id, redirect_uris)

    response = {
        "client_id": client_id,
        "client_id_issued_at": issued_at,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": record["client_name"],
        "scope": record["scope"],
    }
    await _send_json(send, 201, response)


# ── Authorize endpoint ───────────────────────────────────────────────────────


def _validate_authorize_params(
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    resource: str,
) -> str | None:
    """Shared GET/POST validation. Returns an error string, or None if valid."""
    if not _is_known_client(client_id):
        return f"unknown_client: {client_id}"
    if not redirect_uri:
        return "redirect_uri required"
    if not _redirect_uri_allowed(client_id, redirect_uri):
        return f"redirect_uri not allowed for client {client_id}"
    # Spec item 3: PKCE S256, no exceptions. The openai bridge's optional-PKCE
    # and plain-tolerance paths are deliberately not carried over.
    if not code_challenge:
        return "code_challenge required — this authorization server mandates PKCE (S256)"
    if code_challenge_method != "S256":
        return "code_challenge_method must be S256"
    # Spec item 2: RFC 8707. Absent is tolerated (token still binds to the
    # canonical audience); a present-but-foreign resource is refused.
    if resource and not resource_acceptable(resource):
        return f"invalid_target: this authorization server only serves {CANONICAL_RESOURCE}"
    return None


async def handle_authorize(scope, receive, send) -> None:
    method = scope.get("method", "GET")
    if method == "GET":
        await _authorize_get(scope, send)
    elif method == "POST":
        await _authorize_post(receive, send)
    else:
        await _send_text(send, 405, "Method not allowed")


async def _authorize_get(scope, send) -> None:
    q = _q(scope)
    client_id = q.get("client_id", "")
    redirect_uri = q.get("redirect_uri", "")
    response_type = q.get("response_type", "")
    code_challenge = q.get("code_challenge", "")
    code_challenge_method = q.get("code_challenge_method", "")
    state = q.get("state", "")
    scope_str = q.get("scope", "")
    resource = q.get("resource", "")

    if response_type != "code":
        await _send_text(send, 400, "unsupported_response_type — must be 'code'")
        return
    error = _validate_authorize_params(
        client_id, redirect_uri, code_challenge, code_challenge_method, resource
    )
    if error:
        await _send_text(send, 400, error)
        return

    safe_client = _esc(client_id)
    safe_redirect = _esc(redirect_uri)
    safe_scope = _esc(scope_str or f"{DEFAULT_SCOPE} (full native surface; destructive tier gated)")
    safe_state = _esc(state)
    safe_challenge = _esc(code_challenge)
    safe_resource = _esc(resource)

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Approve Claude Connector — Sovereign Stack</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 560px;
       margin: 4em auto; padding: 0 1.2em; line-height: 1.55; color: #1a1a1a; }}
h1 {{ font-size: 1.4em; margin-bottom: 0.4em; }}
.card {{ border: 1px solid #d8d8d8; border-radius: 10px; padding: 2em;
        background: #fafafa; }}
dl {{ margin: 1.2em 0; }}
dt {{ font-weight: 600; margin-top: 0.6em; color: #555; font-size: 0.85em;
      text-transform: uppercase; letter-spacing: 0.04em; }}
dd {{ margin: 0.2em 0 0.4em 0; }}
code {{ background: #ececec; padding: 0.15em 0.45em; border-radius: 4px;
        font-size: 0.92em; word-break: break-all; }}
button {{ background: #111; color: #fff; border: none; padding: 0.8em 1.6em;
         border-radius: 6px; cursor: pointer; font-size: 1em; margin-right: 0.5em;
         font-weight: 500; }}
button.deny {{ background: #888; }}
button:hover {{ opacity: 0.9; }}
.muted {{ color: #666; font-size: 0.9em; }}
</style>
</head>
<body>
<div class="card">
<h1>Approve Claude connector access</h1>
<p>The Sovereign Stack received an OAuth authorization request from a
Claude MCP connector (claude.ai, claude.com, or Claude Code).</p>
<dl>
<dt>Client</dt><dd><code>{safe_client}</code></dd>
<dt>Substrate identity</dt><dd><code>claude-ai-bridge</code></dd>
<dt>Scope requested</dt><dd><code>{safe_scope}</code></dd>
<dt>Audience</dt><dd><code>{_esc(CANONICAL_RESOURCE)}</code></dd>
<dt>Redirect target</dt><dd><code>{safe_redirect}</code></dd>
</dl>
<p class="muted">Approving grants this Claude seat the native tool surface at
<code>/claude/mcp</code>. Destructive-tier tools (policy mutation, supersession,
quarantine, protected records, service control) additionally require a per-use
step-up approval on your phone. Access tokens expire hourly and rotate via
refresh; revoke everything at any time with
<code>python -m clients.claude_bridge.cli revoke-all</code>.</p>
<form method="post" action="/claude/oauth/authorize">
<input type="hidden" name="client_id" value="{safe_client}"/>
<input type="hidden" name="redirect_uri" value="{safe_redirect}"/>
<input type="hidden" name="code_challenge" value="{safe_challenge}"/>
<input type="hidden" name="code_challenge_method" value="S256"/>
<input type="hidden" name="state" value="{safe_state}"/>
<input type="hidden" name="scope" value="{_esc(scope_str)}"/>
<input type="hidden" name="resource" value="{safe_resource}"/>
<button type="submit" name="action" value="approve">Approve</button>
<button type="submit" name="action" value="deny" class="deny">Deny</button>
</form>
</div>
</body>
</html>"""
    await _send_html(send, 200, html)


async def _authorize_post(receive, send) -> None:
    body = await _read_body(receive)
    form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}

    action = form.get("action", "")
    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    code_challenge = form.get("code_challenge", "")
    code_challenge_method = form.get("code_challenge_method", "")
    state = form.get("state", "")
    scope_str = form.get("scope", "")
    resource = form.get("resource", "")

    if action != "approve":
        params = {"error": "access_denied"}
        if state:
            params["state"] = state
        # Even the deny redirect only ever goes to a pinned target.
        if redirect_uri_pinned(redirect_uri):
            await _redirect(send, _append_params(redirect_uri, params))
        else:
            await _send_text(send, 400, "redirect_uri not allowed")
        return

    # Re-validate everything (defence in depth — POST data could be tampered).
    error = _validate_authorize_params(
        client_id, redirect_uri, code_challenge, code_challenge_method, resource
    )
    if error:
        await _send_text(send, 400, error)
        return

    code = secrets.token_urlsafe(32)
    code_data = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": scope_str or DEFAULT_SCOPE,
        "audience": _normalize_resource(resource) if resource else CANONICAL_RESOURCE,
        "issued_at": _now().isoformat(),
        "substrate": SUBSTRATE,
    }
    _save_code(code, code_data)
    logger.info(
        "OAuth: issued auth code for client=%s substrate=%s audience=%s",
        client_id,
        SUBSTRATE,
        code_data["audience"],
    )

    params = {"code": code}
    if state:
        params["state"] = state
    await _redirect(send, _append_params(redirect_uri, params))


# ── Token endpoint ───────────────────────────────────────────────────────────


async def handle_token(scope, receive, send) -> None:
    """POST /claude/oauth/token — authorization_code and refresh_token grants.

    Accepts application/x-www-form-urlencoded bodies (spec item 7). Responds
    promptly with definitive JSON errors — never a hang, never a 404 — so a
    misbehaving client cannot spiral into the #4030 discovery-retry shape.
    """
    if scope.get("method", "POST") != "POST":
        await _send_json(send, 405, {"error": "method_not_allowed"})
        return

    body = await _read_body(receive)
    form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
    grant_type = form.get("grant_type", "")

    if grant_type == "authorization_code":
        await _token_authorization_code(form, send)
    elif grant_type == "refresh_token":
        await _token_refresh(form, send)
    else:
        await _send_json(
            send,
            400,
            {
                "error": "unsupported_grant_type",
                "error_description": "Supported: authorization_code, refresh_token",
            },
        )


async def _token_authorization_code(form: dict, send) -> None:
    code = form.get("code", "")
    code_verifier = form.get("code_verifier", "")
    redirect_uri = form.get("redirect_uri", "")
    client_id = form.get("client_id", "")
    resource = form.get("resource", "")

    code_data = _load_code(code)
    if code_data is None:
        await _send_json(
            send,
            400,
            {"error": "invalid_grant", "error_description": "Authorization code not found"},
        )
        return

    # Single-use: delete immediately, regardless of validation outcome below.
    _delete_code(code)

    try:
        issued_at = datetime.fromisoformat(code_data["issued_at"])
    except (ValueError, KeyError):
        await _send_json(
            send, 400, {"error": "invalid_grant", "error_description": "Code metadata corrupt"}
        )
        return
    if _now() - issued_at > timedelta(seconds=CODE_TTL_SECONDS):
        await _send_json(send, 400, {"error": "invalid_grant", "error_description": "Code expired"})
        return

    if code_data.get("client_id") != client_id:
        await _send_json(
            send, 400, {"error": "invalid_client", "error_description": "client_id mismatch"}
        )
        return
    if code_data.get("redirect_uri") != redirect_uri:
        await _send_json(
            send, 400, {"error": "invalid_grant", "error_description": "redirect_uri mismatch"}
        )
        return

    # PKCE — mandatory, S256 only (spec item 3). Codes are only ever minted
    # with a challenge, but fail closed if one somehow lacks it.
    stored_challenge = code_data.get("code_challenge", "")
    if not stored_challenge or not _verify_pkce_s256(code_verifier, stored_challenge):
        await _send_json(
            send,
            400,
            {
                "error": "invalid_grant",
                "error_description": "PKCE verification failed (S256 required)",
            },
        )
        return

    # RFC 8707 (spec item 2): if the client repeats the resource at the token
    # endpoint it must match the audience bound at authorize time.
    audience = code_data.get("audience", CANONICAL_RESOURCE)
    if resource and _normalize_resource(resource) != _normalize_resource(audience):
        await _send_json(
            send,
            400,
            {
                "error": "invalid_target",
                "error_description": "resource does not match the authorized audience",
            },
        )
        return

    family_id = secrets.token_hex(16)
    response = _mint_token_pair(
        client_id=client_id,
        scope=code_data.get("scope") or DEFAULT_SCOPE,
        audience=audience,
        family_id=family_id,
    )
    logger.info(
        "OAuth: issued token pair for client=%s family=%s audience=%s",
        client_id,
        family_id[:12],
        audience,
    )
    await _send_json(send, 200, response)


async def _token_refresh(form: dict, send) -> None:
    refresh_token = form.get("refresh_token", "")
    client_id = form.get("client_id", "")
    resource = form.get("resource", "")

    data = _load_record("refresh", refresh_token)
    if data is None or data.get("substrate") != SUBSTRATE:
        await _send_json(
            send, 400, {"error": "invalid_grant", "error_description": "Refresh token not found"}
        )
        return

    status = data.get("status", "")
    if status == "rotated":
        # Reuse of a rotated refresh token = replay signal. Revoke the family
        # (OAuth 2.1 rotation guidance): the legitimate client re-authorizes,
        # the replayer gets nothing.
        logger.warning(
            "OAuth: rotated refresh token REPLAYED for family=%s — revoking family",
            data.get("family_id", "")[:12],
        )
        revoke_family(data.get("family_id", ""))
        await _send_json(
            send,
            400,
            {
                "error": "invalid_grant",
                "error_description": "Refresh token reuse detected; grant revoked",
            },
        )
        return
    if status != "active":
        await _send_json(
            send, 400, {"error": "invalid_grant", "error_description": "Refresh token revoked"}
        )
        return
    if _expired(data):
        await _send_json(
            send, 400, {"error": "invalid_grant", "error_description": "Refresh token expired"}
        )
        return
    if data.get("client_id") != client_id:
        await _send_json(
            send, 400, {"error": "invalid_client", "error_description": "client_id mismatch"}
        )
        return
    audience = data.get("audience", CANONICAL_RESOURCE)
    if resource and _normalize_resource(resource) != _normalize_resource(audience):
        await _send_json(
            send,
            400,
            {
                "error": "invalid_target",
                "error_description": "resource does not match the granted audience",
            },
        )
        return

    # Rotate: retire the presented token (kept as a tombstone for reuse
    # detection), then mint the successor pair in the same family. The new
    # refresh token rides the same response that invalidates the old one
    # (spec item 7).
    response = _mint_token_pair(
        client_id=client_id,
        scope=data.get("scope") or DEFAULT_SCOPE,
        audience=audience,
        family_id=data.get("family_id", ""),
    )
    data["status"] = "rotated"
    data["rotated_at"] = _now().isoformat()
    _save_record("refresh", refresh_token, data)
    logger.info(
        "OAuth: rotated refresh token for client=%s family=%s",
        client_id,
        data.get("family_id", "")[:12],
    )
    await _send_json(send, 200, response)


# ── Revocation endpoint (RFC 7009) ───────────────────────────────────────────


async def handle_revoke(scope, receive, send) -> None:
    """
    POST /claude/oauth/revoke — revokes the presented token's WHOLE family
    (access + refresh), honoring "every grant one-call revocable". Always
    responds 200 with an empty JSON object (RFC 7009 §2.2 — no oracle for
    probing whether a token existed).
    """
    if scope.get("method", "POST") != "POST":
        await _send_json(send, 405, {"error": "method_not_allowed"})
        return

    body = await _read_body(receive)
    form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
    token = form.get("token", "")
    client_id = form.get("client_id", "")

    data = _load_record("access", token) or _load_record("refresh", token)
    if data is not None and (not client_id or data.get("client_id") == client_id):
        revoke_family(data.get("family_id", ""))
    await _send_json(send, 200, {})


# ── Discovery endpoints ──────────────────────────────────────────────────────


async def handle_authorization_server_metadata(scope, receive, send) -> None:
    """RFC 8414 — Authorization Server metadata. Also serves the
    openid-configuration alias: MCP clients probe both shapes (the #4030
    retry loop was four discovery endpoints 404ing 32 rounds), so every
    discovery shape answers 200 with the same truth."""
    metadata = {
        "issuer": BRIDGE_ISSUER,
        "authorization_endpoint": f"{BRIDGE_ISSUER}/oauth/authorize",
        "token_endpoint": f"{BRIDGE_ISSUER}/oauth/token",
        "registration_endpoint": f"{BRIDGE_ISSUER}/oauth/register",
        "revocation_endpoint": f"{BRIDGE_ISSUER}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": [DEFAULT_SCOPE],
        "service_documentation": f"{BRIDGE_ISSUER}/info",
    }
    await _send_json(send, 200, metadata)


async def handle_protected_resource_metadata(scope, receive, send) -> None:
    """RFC 9728 — Protected Resource metadata."""
    metadata = {
        "resource": CANONICAL_RESOURCE,
        "authorization_servers": [BRIDGE_ISSUER],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [DEFAULT_SCOPE],
        "resource_documentation": f"{BRIDGE_ISSUER}/info",
    }
    await _send_json(send, 200, metadata)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _esc(s: str) -> str:
    """HTML-escape a string for safe injection into the consent page."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _append_params(url: str, params: dict) -> str:
    """Append query params to a URL, respecting any existing ?."""
    if not params:
        return url
    sep = "&" if "?" in url else "?"
    return url + sep + urllib.parse.urlencode(params)
