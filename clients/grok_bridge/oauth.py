from __future__ import annotations

"""
OAuth 2.1 + PKCE shim for the Grok bridge.

Required because xAI's Custom Connector UI demands an OAuth-style flow even
for simple bearer-token MCP servers. This module presents the OAuth surface
xAI expects, then maps issued access tokens internally back to the existing
grok-xai substrate identity. The rest of the bridge pipeline (identity_gate,
ring_filter, tool dispatch) is unchanged.

Endpoints:
  GET  /grok/oauth/authorize                — show consent page
  POST /grok/oauth/authorize                — process consent, issue code, redirect
  POST /grok/oauth/token                    — exchange code for access token (PKCE)
  GET  /grok/.well-known/oauth-authorization-server  — RFC 8414 discovery
  GET  /grok/.well-known/oauth-protected-resource    — RFC 9728 RS metadata

Flow:
  1. xAI's UI redirects user's browser to /authorize with PKCE challenge
  2. User (Anthony) sees consent page, clicks Approve
  3. Server issues auth code, redirects browser to xAI's redirect_uri
  4. xAI's backend POSTs to /token with code + PKCE verifier
  5. Server verifies PKCE, issues bearer access token, stores in tokens/
  6. xAI uses that token on every Authorization header to /grok/sse
  7. identity_gate's registered validator checks issued tokens → maps to grok-xai

Storage:
  ~/.sovereign/grok_bridge/oauth/codes/<code>.json     — pending auth codes
  ~/.sovereign/grok_bridge/oauth/tokens/<token>.json   — issued access tokens
  ~/.sovereign/grok_bridge/oauth_clients.json          — registered clients

Security notes:
  - Public client (no client_secret), PKCE S256 mandatory in spirit
    (we still allow 'plain' for compatibility but log a warning)
  - Codes are single-use, 10-min TTL
  - Tokens are long-lived (no expiry yet — Phase 2 can add rotation)
  - Redirect URIs are validated against registered patterns
"""

import base64
import hashlib
import json
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from bridge_core import register_token_validator

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_GROK_DIR = Path.home() / ".sovereign" / "grok_bridge"
_OAUTH_DIR = _GROK_DIR / "oauth"
_CODES_DIR = _OAUTH_DIR / "codes"
_TOKENS_DIR = _OAUTH_DIR / "tokens"
_CLIENTS_FILE = _GROK_DIR / "oauth_clients.json"

CODE_TTL_SECONDS = 600          # 10 minutes
TOKEN_TTL_SECONDS = 0           # 0 = never expires (Phase 2 can rotate)
SUBSTRATE = "grok-xai"

# Issuer used in discovery metadata. The MCP server itself is at /sse;
# the OAuth AS is at the parent path.
BRIDGE_ISSUER = "https://stack.templetwo.com/grok"

# Initialize storage at import
for d in (_CODES_DIR, _TOKENS_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ── Client registry ───────────────────────────────────────────────────────────


def _load_clients() -> dict:
    if not _CLIENTS_FILE.exists():
        return {}
    try:
        return json.loads(_CLIENTS_FILE.read_text())
    except json.JSONDecodeError as e:
        logger.error("oauth_clients.json malformed: %s", e)
        return {}


def _is_known_client(client_id: str) -> bool:
    return client_id in _load_clients()


def _redirect_uri_allowed(client_id: str, redirect_uri: str) -> bool:
    """Check redirect_uri matches one of the registered patterns for this client."""
    clients = _load_clients()
    client = clients.get(client_id, {})
    patterns = client.get("redirect_uri_patterns", [])
    for pat in patterns:
        if redirect_uri.startswith(pat):
            return True
    # If no patterns registered, accept any HTTPS redirect (initial setup convenience)
    if not patterns and redirect_uri.startswith("https://"):
        logger.warning(
            "Client %s has no redirect_uri_patterns; accepting any HTTPS uri",
            client_id,
        )
        return True
    return False


# ── Storage primitives ───────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _save_code(code: str, data: dict) -> None:
    (_CODES_DIR / f"{code}.json").write_text(json.dumps(data, indent=2))


def _load_code(code: str) -> Optional[dict]:
    path = _CODES_DIR / f"{code}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _delete_code(code: str) -> None:
    path = _CODES_DIR / f"{code}.json"
    if path.exists():
        path.unlink()


def _save_token(token: str, data: dict) -> None:
    (_TOKENS_DIR / f"{token}.json").write_text(json.dumps(data, indent=2))


def _load_token(token: str) -> Optional[dict]:
    path = _TOKENS_DIR / f"{token}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


# ── PKCE ──────────────────────────────────────────────────────────────────────


def _verify_pkce(code_verifier: str, code_challenge: str, method: str) -> bool:
    """Verify PKCE per RFC 7636."""
    if not code_verifier or not code_challenge:
        return False
    if method == "S256":
        h = hashlib.sha256(code_verifier.encode("ascii")).digest()
        derived = base64.urlsafe_b64encode(h).decode("ascii").rstrip("=")
        return secrets.compare_digest(derived, code_challenge)
    if method == "plain":
        logger.warning("PKCE method=plain accepted for compatibility; prefer S256")
        return secrets.compare_digest(code_verifier, code_challenge)
    return False


# ── Token validator (registered with identity_gate at module import) ─────────


def is_valid_oauth_token(token: str) -> bool:
    """Return True if the presented bearer token was issued by this OAuth shim."""
    data = _load_token(token)
    if data is None:
        return False
    if data.get("substrate") != SUBSTRATE:
        return False
    if TOKEN_TTL_SECONDS > 0:
        try:
            issued_at = datetime.fromisoformat(data["issued_at"])
        except (ValueError, KeyError):
            return False
        if _now() - issued_at > timedelta(seconds=TOKEN_TTL_SECONDS):
            return False
    return True


register_token_validator(SUBSTRATE, is_valid_oauth_token)


# ── ASGI helpers ──────────────────────────────────────────────────────────────


async def _send_json(send, status: int, body_dict: dict) -> None:
    body = json.dumps(body_dict).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _send_html(send, status: int, html: str) -> None:
    body = html.encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"text/html; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _send_text(send, status: int, text: str) -> None:
    body = text.encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _redirect(send, url: str) -> None:
    await send({
        "type": "http.response.start",
        "status": 302,
        "headers": [
            (b"location", url.encode()),
            (b"content-length", b"0"),
            (b"cache-control", b"no-store"),
        ],
    })
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


# ── Authorize endpoint ───────────────────────────────────────────────────────


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
    code_challenge_method = q.get("code_challenge_method", "plain")
    state = q.get("state", "")
    scope_str = q.get("scope", "")

    if response_type != "code":
        await _send_text(send, 400, "unsupported_response_type — must be 'code'")
        return
    if not _is_known_client(client_id):
        await _send_text(send, 400, f"unknown_client: {client_id}")
        return
    if not redirect_uri:
        await _send_text(send, 400, "redirect_uri required")
        return
    if not _redirect_uri_allowed(client_id, redirect_uri):
        await _send_text(send, 400, f"redirect_uri not allowed for client {client_id}")
        return
    if not code_challenge:
        await _send_text(send, 400, "code_challenge required (PKCE mandatory)")
        return
    if code_challenge_method not in ("S256", "plain"):
        await _send_text(send, 400, "code_challenge_method must be S256 or plain")
        return

    # Build consent page
    safe_client = _esc(client_id)
    safe_redirect = _esc(redirect_uri)
    safe_scope = _esc(scope_str or "default (Ring 1 read access)")
    safe_state = _esc(state)
    safe_challenge = _esc(code_challenge)
    safe_method = _esc(code_challenge_method)

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Approve Grok Bridge — Sovereign Stack</title>
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
<h1>Approve Grok Bridge access</h1>
<p>The Sovereign Stack received an OAuth authorization request from xAI.</p>
<dl>
<dt>Client</dt><dd><code>{safe_client}</code></dd>
<dt>Substrate identity</dt><dd><code>grok-xai</code></dd>
<dt>Scope requested</dt><dd><code>{safe_scope}</code></dd>
<dt>Redirect target</dt><dd><code>{safe_redirect}</code></dd>
</dl>
<p class="muted">If this came from the xAI Custom Connector form you just filled in, click Approve.
The issued access token will be sent to xAI and used as the
<code>Authorization: Bearer</code> header on every connection to <code>/grok/sse</code>.
You can revoke later by deleting the token file under
<code>~/.sovereign/grok_bridge/oauth/tokens/</code>.</p>
<form method="post" action="/grok/oauth/authorize">
<input type="hidden" name="client_id" value="{safe_client}"/>
<input type="hidden" name="redirect_uri" value="{safe_redirect}"/>
<input type="hidden" name="code_challenge" value="{safe_challenge}"/>
<input type="hidden" name="code_challenge_method" value="{safe_method}"/>
<input type="hidden" name="state" value="{safe_state}"/>
<input type="hidden" name="scope" value="{_esc(scope_str)}"/>
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
    code_challenge_method = form.get("code_challenge_method", "plain")
    state = form.get("state", "")
    scope_str = form.get("scope", "")

    if action != "approve":
        params = {"error": "access_denied"}
        if state:
            params["state"] = state
        await _redirect(send, _append_params(redirect_uri, params))
        return

    # Re-validate (defence in depth — POST data could be tampered)
    if not _is_known_client(client_id):
        await _send_text(send, 400, f"unknown_client: {client_id}")
        return
    if not _redirect_uri_allowed(client_id, redirect_uri):
        await _send_text(send, 400, "redirect_uri not allowed")
        return

    code = secrets.token_urlsafe(32)
    code_data = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope_str,
        "issued_at": _now().isoformat(),
        "substrate": SUBSTRATE,
    }
    _save_code(code, code_data)
    logger.info(
        "OAuth: issued auth code for client=%s substrate=%s",
        client_id, SUBSTRATE,
    )

    params = {"code": code}
    if state:
        params["state"] = state
    await _redirect(send, _append_params(redirect_uri, params))


# ── Token endpoint ───────────────────────────────────────────────────────────


async def handle_token(scope, receive, send) -> None:
    if scope.get("method", "POST") != "POST":
        await _send_json(send, 405, {"error": "method_not_allowed"})
        return

    body = await _read_body(receive)
    form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}

    grant_type = form.get("grant_type", "")
    code = form.get("code", "")
    code_verifier = form.get("code_verifier", "")
    redirect_uri = form.get("redirect_uri", "")
    client_id = form.get("client_id", "")

    if grant_type != "authorization_code":
        await _send_json(send, 400, {
            "error": "unsupported_grant_type",
            "error_description": "Only authorization_code is supported",
        })
        return

    code_data = _load_code(code)
    if code_data is None:
        await _send_json(send, 400, {
            "error": "invalid_grant",
            "error_description": "Authorization code not found",
        })
        return

    # Single-use: delete immediately, regardless of validation outcome below
    _delete_code(code)

    # Expiry check
    try:
        issued_at = datetime.fromisoformat(code_data["issued_at"])
    except (ValueError, KeyError):
        await _send_json(send, 400, {"error": "invalid_grant",
                                     "error_description": "Code metadata corrupt"})
        return
    if _now() - issued_at > timedelta(seconds=CODE_TTL_SECONDS):
        await _send_json(send, 400, {"error": "invalid_grant",
                                     "error_description": "Code expired"})
        return

    # Bind checks
    if code_data.get("client_id") != client_id:
        await _send_json(send, 400, {"error": "invalid_client",
                                     "error_description": "client_id mismatch"})
        return
    if code_data.get("redirect_uri") != redirect_uri:
        await _send_json(send, 400, {"error": "invalid_grant",
                                     "error_description": "redirect_uri mismatch"})
        return

    # PKCE
    if not _verify_pkce(
        code_verifier,
        code_data.get("code_challenge", ""),
        code_data.get("code_challenge_method", "plain"),
    ):
        await _send_json(send, 400, {"error": "invalid_grant",
                                     "error_description": "PKCE verification failed"})
        return

    # Issue access token
    access_token = secrets.token_hex(32)
    token_data = {
        "client_id": client_id,
        "substrate": SUBSTRATE,
        "scope": code_data.get("scope", ""),
        "issued_at": _now().isoformat(),
        "code_used": code[:12] + "...",   # first 12 chars for audit, not full code
    }
    _save_token(access_token, token_data)
    logger.info(
        "OAuth: issued access_token for client=%s substrate=%s",
        client_id, SUBSTRATE,
    )

    response: dict = {
        "access_token": access_token,
        "token_type": "Bearer",
        "scope": code_data.get("scope", "") or "ring1",
    }
    if TOKEN_TTL_SECONDS > 0:
        response["expires_in"] = TOKEN_TTL_SECONDS
    await _send_json(send, 200, response)


# ── Discovery endpoints ──────────────────────────────────────────────────────


async def handle_authorization_server_metadata(scope, receive, send) -> None:
    """RFC 8414 — Authorization Server metadata."""
    metadata = {
        "issuer": BRIDGE_ISSUER,
        "authorization_endpoint": f"{BRIDGE_ISSUER}/oauth/authorize",
        "token_endpoint": f"{BRIDGE_ISSUER}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "scopes_supported": ["ring1"],
        "service_documentation": "https://stack.templetwo.com/grok/info",
    }
    await _send_json(send, 200, metadata)


async def handle_protected_resource_metadata(scope, receive, send) -> None:
    """RFC 9728 — Protected Resource metadata."""
    metadata = {
        "resource": f"{BRIDGE_ISSUER}/sse",
        "authorization_servers": [BRIDGE_ISSUER],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["ring1"],
        "resource_documentation": "https://stack.templetwo.com/grok/info",
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
