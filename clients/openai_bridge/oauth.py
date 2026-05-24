from __future__ import annotations

"""
OAuth 2.1 + PKCE + Dynamic Client Registration shim for the OpenAI bridge.

Ported from grok_bridge/oauth.py. ChatGPT's MCP connector (openai-mcp/1.0.0)
does the modern MCP authorization flow: it sends no static bearer, expects a
401 carrying a WWW-Authenticate resource_metadata pointer, then walks
discovery -> dynamic client registration -> authorize -> token, and finally
connects to /openai/sse with the issued Bearer token. This module presents
that OAuth surface and maps issued tokens back to the chatgpt-openai-bridge
substrate via the identity gate. The rest of the bridge pipeline is unchanged.

What this adds over the grok port:
  - Dynamic Client Registration (RFC 7591) at POST /openai/oauth/register.
    grok relied on a pre-registered client in oauth_clients.json; ChatGPT
    self-registers, so we mint client_ids on demand (validated, https-only
    redirect_uris).
  - Storage hardened: oauth dir 0700, code/token/client files 0600.

Endpoints (wired in sse_server.py):
  GET  /openai/oauth/authorize                          — consent page
  POST /openai/oauth/authorize                          — issue code, redirect
  POST /openai/oauth/token                              — exchange code (PKCE)
  POST /openai/oauth/register                           — RFC 7591 DCR
  GET  /openai/.well-known/oauth-authorization-server   — RFC 8414 AS metadata
  GET  /openai/.well-known/oauth-protected-resource     — RFC 9728 RS metadata

Storage:
  ~/.sovereign/openai_bridge/oauth/codes/<code>.json    — pending auth codes
  ~/.sovereign/openai_bridge/oauth/tokens/<token>.json  — issued access tokens
  ~/.sovereign/openai_bridge/oauth_clients.json         — registered clients

Security notes:
  - Public clients (no client_secret), PKCE S256 preferred ('plain' tolerated
    with a warning, for compatibility).
  - Codes are single-use, 10-min TTL.
  - Tokens are long-lived for parity with the grok seat (no expiry yet;
    rotation/refresh is a deliberate Phase 2 follow-up, not required for the
    initial handshake). is_valid_oauth_token still honours TOKEN_TTL_SECONDS
    if a future config sets it > 0.
  - DCR validates redirect_uris (must be present and https) before issuing a
    client_id.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from bridge_core import register_token_validator

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_OPENAI_DIR = Path.home() / ".sovereign" / "openai_bridge"
_OAUTH_DIR = _OPENAI_DIR / "oauth"
_CODES_DIR = _OAUTH_DIR / "codes"
_TOKENS_DIR = _OAUTH_DIR / "tokens"
_CLIENTS_FILE = _OPENAI_DIR / "oauth_clients.json"

CODE_TTL_SECONDS = 600          # 10 minutes
TOKEN_TTL_SECONDS = 0           # 0 = never expires (parity w/ grok; Phase 2 rotates)
SUBSTRATE = "chatgpt-openai-bridge"

# Issuer used in discovery metadata. The MCP server itself is at /openai/sse;
# the OAuth AS is at the parent path.
BRIDGE_ISSUER = "https://stack.templetwo.com/openai"

# Initialize storage at import, with restrictive permissions (auth membrane).
for _d in (_CODES_DIR, _TOKENS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_d, 0o700)
    except OSError as _e:  # best-effort hardening; never block import
        logger.warning("could not chmod %s: %s", _d, _e)
try:
    os.chmod(_OAUTH_DIR, 0o700)
    os.chmod(_OPENAI_DIR, 0o700)
except OSError:
    pass


def _write_secure(path: Path, text: str) -> None:
    """Write a file then tighten its mode to 0600 (owner read/write only)."""
    path.write_text(text)
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        logger.warning("could not chmod %s: %s", path, e)


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
    """Check redirect_uri matches one of the registered patterns for this client."""
    clients = _load_clients()
    client = clients.get(client_id, {})
    patterns = client.get("redirect_uri_patterns", [])
    for pat in patterns:
        if redirect_uri == pat or redirect_uri.startswith(pat):
            return True
    return False


# ── Storage primitives ───────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _save_code(code: str, data: dict) -> None:
    _write_secure(_CODES_DIR / f"{code}.json", json.dumps(data, indent=2))


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
    _write_secure(_TOKENS_DIR / f"{token}.json", json.dumps(data, indent=2))


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


# ── Dynamic Client Registration endpoint (RFC 7591) ───────────────────────────


async def handle_register(scope, receive, send) -> None:
    """
    POST /openai/oauth/register — RFC 7591 Dynamic Client Registration.

    ChatGPT's connector self-registers before the authorize step. We accept the
    client metadata, validate redirect_uris (must be present and https), mint a
    public client_id, persist it, and return the registration response.
    """
    if scope.get("method", "POST") != "POST":
        await _send_json(send, 405, {"error": "method_not_allowed"})
        return

    raw = await _read_body(receive)
    try:
        meta = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        await _send_json(send, 400, {
            "error": "invalid_client_metadata",
            "error_description": "Body must be a JSON object",
        })
        return

    redirect_uris = meta.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        await _send_json(send, 400, {
            "error": "invalid_redirect_uri",
            "error_description": "redirect_uris (non-empty array) is required",
        })
        return
    for uri in redirect_uris:
        if not isinstance(uri, str) or not uri.startswith("https://"):
            await _send_json(send, 400, {
                "error": "invalid_redirect_uri",
                "error_description": f"redirect_uri must be https: {uri!r}",
            })
            return

    client_id = "openai-" + secrets.token_urlsafe(16)
    issued_at = int(_now().timestamp())
    record = {
        "client_id": client_id,
        "client_name": meta.get("client_name", "ChatGPT MCP connector"),
        "redirect_uri_patterns": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "scope": meta.get("scope", "ring1"),
        "client_id_issued_at": issued_at,
        "registered_by": "dcr",
    }
    clients = _load_clients()
    clients[client_id] = record
    _save_clients(clients)
    logger.info("OAuth DCR: registered client_id=%s redirect_uris=%s",
                client_id, redirect_uris)

    # RFC 7591 registration response
    response = {
        "client_id": client_id,
        "client_id_issued_at": issued_at,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "client_name": record["client_name"],
        "scope": record["scope"],
    }
    await _send_json(send, 201, response)


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
    # PKCE is optional: ChatGPT's platform setup flow does not send a
    # code_challenge. Validate the method only if a challenge IS present;
    # otherwise allow the auth-code flow without PKCE. Redirect-uri binding,
    # the consent gate, and single-use short-TTL codes remain the protections.
    if code_challenge and code_challenge_method not in ("S256", "plain"):
        await _send_text(send, 400, "code_challenge_method must be S256 or plain")
        return

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
<title>Approve ChatGPT Bridge — Sovereign Stack</title>
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
<h1>Approve ChatGPT Bridge access</h1>
<p>The Sovereign Stack received an OAuth authorization request from the
ChatGPT / OpenAI MCP connector.</p>
<dl>
<dt>Client</dt><dd><code>{safe_client}</code></dd>
<dt>Substrate identity</dt><dd><code>chatgpt-openai-bridge</code></dd>
<dt>Scope requested</dt><dd><code>{safe_scope}</code></dd>
<dt>Redirect target</dt><dd><code>{safe_redirect}</code></dd>
</dl>
<p class="muted">If this came from the ChatGPT connector you just added, click Approve.
The issued access token will be sent to OpenAI and used as the
<code>Authorization: Bearer</code> header on every connection to <code>/openai/sse</code>.
You can revoke later by deleting the token file under
<code>~/.sovereign/openai_bridge/oauth/tokens/</code>.</p>
<form method="post" action="/openai/oauth/authorize">
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
    logger.info("OAuth: issued auth code for client=%s substrate=%s",
                client_id, SUBSTRATE)

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

    if code_data.get("client_id") != client_id:
        await _send_json(send, 400, {"error": "invalid_client",
                                     "error_description": "client_id mismatch"})
        return
    if code_data.get("redirect_uri") != redirect_uri:
        await _send_json(send, 400, {"error": "invalid_grant",
                                     "error_description": "redirect_uri mismatch"})
        return

    stored_challenge = code_data.get("code_challenge", "")
    if stored_challenge:
        # PKCE was used at authorize time — it must verify.
        if not _verify_pkce(
            code_verifier,
            stored_challenge,
            code_data.get("code_challenge_method", "plain"),
        ):
            await _send_json(send, 400, {"error": "invalid_grant",
                                         "error_description": "PKCE verification failed"})
            return
    # else: no challenge was issued (client did not use PKCE). The code is
    # single-use, short-TTL, and bound to client_id + redirect_uri (enforced
    # above), so the auth-code flow proceeds without a verifier.

    access_token = secrets.token_hex(32)
    token_data = {
        "client_id": client_id,
        "substrate": SUBSTRATE,
        "scope": code_data.get("scope", ""),
        "issued_at": _now().isoformat(),
        "code_used": code[:12] + "...",
    }
    _save_token(access_token, token_data)
    logger.info("OAuth: issued access_token for client=%s substrate=%s",
                client_id, SUBSTRATE)

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
        "registration_endpoint": f"{BRIDGE_ISSUER}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "scopes_supported": ["ring1"],
        "service_documentation": "https://stack.templetwo.com/openai/info",
    }
    await _send_json(send, 200, metadata)


async def handle_protected_resource_metadata(scope, receive, send) -> None:
    """RFC 9728 — Protected Resource metadata."""
    metadata = {
        "resource": f"{BRIDGE_ISSUER}/sse",
        "authorization_servers": [BRIDGE_ISSUER],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["ring1"],
        "resource_documentation": "https://stack.templetwo.com/openai/info",
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
