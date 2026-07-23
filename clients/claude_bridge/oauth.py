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
  6. Phone-tap resource-owner authentication (2026-07 swap; supersedes the
     original operator-passphrase + consent-nonce design). GET /authorize no
     longer renders a consent form — it delegates a fresh approval request to
     the sovereign-bridge (port 8100, loopback, master BRIDGE_TOKEN) and
     renders a "check your phone" waiting page that polls for Anthony's ntfy
     tap. POST /authorize mints a code only when the submitted params bind
     (sha256, constant-time) to the approval the GET created AND the
     bridge's atomic approved→consumed confirm returns {approved: true}.
     No passphrase, no admin-approve fallback — the tap is the only gate.

Endpoints (wired in sse_server.py):
  GET  /claude/oauth/authorize                          — phone-tap waiting page
  POST /claude/oauth/authorize                          — issue code, redirect
  GET  /claude/oauth/authorize/status                    — poll target for the waiting page
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
  ~/.sovereign/claude_bridge/oauth/codes/<code>.json      — pending auth codes
  ~/.sovereign/claude_bridge/oauth/tokens/<token>.json    — access tokens
  ~/.sovereign/claude_bridge/oauth/refresh/<token>.json   — refresh tokens
  ~/.sovereign/claude_bridge/oauth/approvals/<aid>.json   — pending phone-tap
                                                             approval bindings
  ~/.sovereign/claude_bridge/oauth_clients.json           — registered clients
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from bridge_core import register_token_validator

logger = logging.getLogger(__name__)


# ── Phone-tap approval delegation (bridge, port 8100) ──────────────────────────
#
# The passphrase + consent nonce this section used to hold are RETIRED
# (2026-07 phone-tap swap; HQ ruling: nonce fully retired, no replacement key).
# The ntfy tap on Anthony's phone is now the load-bearing "is this really
# Anthony" control, delegated to the bridge over loopback exactly as the
# destructive-tier step-up already does (see elevation.py). The connector
# never holds NTFY_TOPIC or ARRIVAL_DECIDE_SECRET — it only ever speaks
# request/status/confirm, authenticated outbound with the BRIDGE_TOKEN it
# already carries for other loopback calls.

_DOOR_BASE_URL = os.environ.get("CLAUDE_DOOR_BASE_URL", "http://127.0.0.1:8100")
_BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")
_BRIDGE_CALL_TIMEOUT_SECONDS = 10.0

# A modest, unambiguous word bank for the human-matching code shown on both
# the waiting page and Anthony's phone push. This is NOT a security boundary
# (the approval_id capability + SSE-side binding check are) — it exists only
# so Anthony can tell which pending push corresponds to which open browser
# tab when two authorize attempts race (spec §7 threat 4).
_CODE_WORDS = [
    "amber",
    "ash",
    "birch",
    "cedar",
    "coral",
    "cove",
    "delta",
    "ember",
    "fern",
    "flint",
    "garnet",
    "harbor",
    "hazel",
    "indigo",
    "ivory",
    "jasper",
    "kestrel",
    "lagoon",
    "maple",
    "nectar",
    "onyx",
    "opal",
    "pearl",
    "quartz",
    "raven",
    "sable",
    "slate",
    "teal",
    "umber",
    "violet",
    "willow",
    "aspen",
    "basalt",
    "cinder",
    "dune",
    "ebony",
    "feather",
    "granite",
    "heron",
    "island",
    "juniper",
    "lark",
    "meadow",
    "nimbus",
    "otter",
    "plume",
    "ridge",
    "sparrow",
    "thistle",
]


def _mint_tap_code() -> str:
    """A fresh two-word code, regenerated per authorize GET — never reused."""
    return f"{secrets.choice(_CODE_WORDS)}-{secrets.choice(_CODE_WORDS)}"


def _compute_binding(
    client_id: str, redirect_uri: str, bound_audience: str, code_challenge: str
) -> str:
    """The canonical connector-authorize binding (HQ req #3 / build-spec §4):
    sha256 over the four fields that must match between the GET that created
    the approval and the POST that redeems it. Both call sites use this SAME
    derivation so absent-resource normalization agrees."""
    binding_src = (
        "connector-authorize\nv1\n"
        + client_id
        + "\n"
        + redirect_uri
        + "\n"
        + bound_audience
        + "\n"
        + code_challenge
    )
    return hashlib.sha256(binding_src.encode()).hexdigest()


async def _bridge_call(method: str, path: str, json_body: dict | None = None) -> dict | None:
    """POST/GET a loopback call to the bridge's /api/approval/* surface,
    authenticated with the master BRIDGE_TOKEN (HQ ruling: master-gated,
    zero new secrets — the SSE plist already carries it).

    Returns the parsed JSON body on any 2xx response; None on ANY failure —
    connection error, timeout, non-2xx status, or an unparseable body. Every
    caller MUST treat None as "mint/advance nothing" (fail-closed). Never
    logs BRIDGE_TOKEN.
    """
    if not _BRIDGE_TOKEN:
        logger.warning("OAuth: BRIDGE_TOKEN not set — cannot reach the phone-tap approval gate")
        return None
    headers = {"Authorization": f"Bearer {_BRIDGE_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=_BRIDGE_CALL_TIMEOUT_SECONDS) as client:
            resp = await client.request(
                method, f"{_DOOR_BASE_URL}{path}", json=json_body, headers=headers
            )
    except httpx.HTTPError as exc:
        logger.warning("OAuth: approval-bridge call %s %s failed: %s", method, path, exc)
        return None
    if resp.status_code // 100 != 2:
        logger.warning(
            "OAuth: approval-bridge call %s %s returned HTTP %s", method, path, resp.status_code
        )
        return None
    try:
        return resp.json()
    except ValueError:
        logger.warning("OAuth: approval-bridge call %s %s returned a non-JSON body", method, path)
        return None


async def _bridge_approval_request(
    *,
    summary: str,
    client_id: str,
    redirect_uri: str,
    audience: str,
    code: str,
    requester_ip: str = "",
) -> dict | None:
    """POST /api/approval/request — create the pending approval + push ntfy.
    Returns {approval_id, code, ...} on success, None on any failure.

    `requester_ip` (FIX 3) is the REAL browser client IP for the /authorize
    request that triggered this call (see `_real_client_ip`) — forwarded so
    the bridge's per-IP create-rate cap and per-IP pending cap key on the
    actual caller instead of collapsing to the SSE's loopback address (every
    caller would otherwise share one bucket, including Anthony's own
    attempts). The bridge trusts this value because the call itself is
    already master-BRIDGE_TOKEN-authed loopback from the SSE — no new trust
    surface, per HQ ruling #3."""
    return await _bridge_call(
        "POST",
        "/api/approval/request",
        {
            "summary": summary,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "audience": audience,
            "code": code,
            "requester_ip": requester_ip,
        },
    )


async def _bridge_approval_status(aid: str) -> dict | None:
    """GET /api/approval/status/{aid} — read-only, no flip, no mint."""
    return await _bridge_call("GET", f"/api/approval/status/{urllib.parse.quote(aid)}")


async def _bridge_approval_confirm(aid: str) -> dict | None:
    """POST /api/approval/confirm — the atomic approved→consumed flip. Only
    a returned {approved: true} may ever proceed to mint a code."""
    return await _bridge_call("POST", "/api/approval/confirm", {"approval_id": aid})


# ── Config ────────────────────────────────────────────────────────────────────

_CLAUDE_DIR = Path.home() / ".sovereign" / "claude_bridge"
_OAUTH_DIR = _CLAUDE_DIR / "oauth"
_CODES_DIR = _OAUTH_DIR / "codes"
_TOKENS_DIR = _OAUTH_DIR / "tokens"
_REFRESH_DIR = _OAUTH_DIR / "refresh"
_APPROVALS_DIR = _OAUTH_DIR / "approvals"
_CLIENTS_FILE = _CLAUDE_DIR / "oauth_clients.json"

CODE_TTL_SECONDS = 600  # 10 minutes, single-use
ACCESS_TOKEN_TTL_SECONDS = int(os.environ.get("CLAUDE_ACCESS_TOKEN_TTL", "3600"))
REFRESH_TOKEN_TTL_SECONDS = int(os.environ.get("CLAUDE_REFRESH_TOKEN_TTL", str(30 * 86400)))
MAX_REGISTERED_CLIENTS = int(os.environ.get("CLAUDE_MAX_REGISTERED_CLIENTS", "50"))
MAX_OAUTH_BODY_BYTES = int(os.environ.get("CLAUDE_MAX_OAUTH_BODY_BYTES", str(64 * 1024)))
SUBSTRATE = "claude-ai-bridge"
DEFAULT_SCOPE = "native"

# ── Resource-owner authentication (the load-bearing control) ──────────────────
# OAuth authenticates the *client*, never the human. Without a live phone-tap
# from Anthony, completing the OAuth dance would equal "Anthony consented", so
# ANY internet caller could self-approve, mint a token, and reach the base
# tool tier (full chronicle read + write). The gate is now the ntfy tap: GET
# /authorize delegates a fresh approval request to the bridge over loopback
# (POST /api/approval/request, master BRIDGE_TOKEN); only Anthony's tap on his
# phone can flip that approval to approved (bridge-side, HMAC-signed,
# POST-only — see sovereign-bridge). POST /authorize mints a code ONLY when
# (a) the submitted params bind, constant-time, to the approval the GET
# render created, AND (b) the bridge's atomic approved→consumed confirm
# returns {approved: true}. FAIL CLOSED: any bridge unreachability, non-2xx,
# or {approved: false} means no code is ever minted. There is no operator
# passphrase and no admin-approve fallback — the phone tap is the ONLY way to
# authorize this connector (HQ ruling, 2026-07 phone-tap swap; no break-glass).

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

# RFC 8252 §7.3 loopback redirect for Claude Code (http on loopback interfaces
# only). DEFAULT OFF on a public deployment: a loopback redirect lets a
# self-approved code be read straight out of the 302 by a curl-controlled
# endpoint, so it is opt-in for local Claude Code dev only.
_ALLOW_LOOPBACK = (
    os.environ.get("CLAUDE_ALLOW_LOOPBACK_REDIRECT", "false").strip().lower() == "true"
)

# Initialize storage at import, with restrictive permissions (auth membrane).
# A filesystem error here must NOT crash import — that would take down the whole
# SSE server (all bridges + native /sse). Fail soft; the handlers surface errors
# per-request instead.
for _d in (_CODES_DIR, _TOKENS_DIR, _REFRESH_DIR, _APPROVALS_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
        os.chmod(_d, 0o700)
    except OSError as _e:  # best-effort hardening; never block import
        logger.warning("claude_bridge storage init could not prepare %s: %s", _d, _e)
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


# ── Pending phone-tap approval records (GET → POST binding carrier) ───────────
# Keyed by the approval_id the bridge mints at create time. Holds the binding
# hash (§4) the POST recomputes and compares constant-time. Storage-layer
# only — the approval's actual pending/approved/denied STATE lives on the
# bridge; this is just "what the GET render promised", never authoritative
# for whether a human tapped anything.

def _approval_path(aid: str) -> Path | None:
    """None only for an empty aid. The filename is sha256(aid), NOT aid
    itself — the build-spec never pins the bridge's approval_id charset, and
    keying on the raw string would coupled this module to whatever the
    (separately-built) bridge happens to mint. Hashing sidesteps that
    entirely: any string round-trips safely as a filename, GET and POST
    still hash the identical aid and therefore still find the same record,
    and there is no path-traversal surface regardless of what the bridge
    ever puts in `approval_id`."""
    if not aid:
        return None
    return _APPROVALS_DIR / f"{hashlib.sha256(aid.encode()).hexdigest()}.json"


def _save_approval(aid: str, data: dict) -> None:
    path = _approval_path(aid)
    if path is None:
        logger.warning("OAuth: refused to persist approval record for malformed aid")
        return
    _write_secure(path, json.dumps(data, indent=2))


def _load_approval(aid: str) -> dict | None:
    path = _approval_path(aid)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        # Unreadable = invalid: the gate must degrade to a clean refusal,
        # never a 500.
        return None


def _delete_approval(aid: str) -> None:
    path = _approval_path(aid)
    if path is not None and path.exists():
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


def _prune_expired(limit: int = 500) -> None:
    """Opportunistic sweep of expired access tokens, expired refresh tokens, and
    long-dead refresh tombstones. Called on each mint so the token dirs cannot
    grow without bound between the periodic revoke-all — a lightweight guard
    against disk/inode exhaustion on an unauthenticated mint surface. Bounded by
    `limit` files per dir per call so a mint never turns into a huge scan."""
    now = _now()
    tombstone_cutoff = now - timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS)
    for base, is_refresh in ((_TOKENS_DIR, False), (_REFRESH_DIR, True)):
        for i, path in enumerate(base.glob("*.json")):
            if i >= limit:
                break
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            drop = _expired(data)
            if is_refresh and not drop and data.get("status") in ("rotated", "revoked"):
                # Retire tombstones once they are older than a full refresh TTL:
                # by then no legitimate rotation could still reference them.
                try:
                    drop = datetime.fromisoformat(data["issued_at"]) < tombstone_cutoff
                except (KeyError, ValueError):
                    drop = True
            if drop:
                path.unlink(missing_ok=True)


def _mint_token_pair(client_id: str, scope: str, audience: str, family_id: str) -> dict:
    """Mint a short-lived access token + rotating refresh token for one family."""
    _prune_expired()
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


class _BodyTooLarge(Exception):
    """Raised when a request body exceeds MAX_OAUTH_BODY_BYTES."""


async def _read_body(receive) -> bytes:
    """Read the request body with a hard byte ceiling. These endpoints are
    unauthenticated and public; do not rely on Cloudflare to cap them."""
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if len(body) > MAX_OAUTH_BODY_BYTES:
            raise _BodyTooLarge()
        if not msg.get("more_body", False):
            break
    return body


async def _read_body_or_413(receive, send) -> bytes | None:
    """_read_body wrapper that emits a 413 and returns None on overflow."""
    try:
        return await _read_body(receive)
    except _BodyTooLarge:
        await _send_json(send, 413, {"error": "request_entity_too_large"})
        return None


def _q(scope: dict) -> dict:
    """Parse query string from ASGI scope."""
    raw = scope.get("query_string", b"").decode()
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}


def _real_client_ip(scope: dict) -> str:
    """The real browser client IP for a request hitting this ASGI app
    (FIX 3). Cloudflare terminates TLS and forwards over the tunnel to
    loopback, so `scope['client']` alone is always 127.0.0.1 for tunneled
    traffic — the true caller only survives in the `cf-connecting-ip`
    header (same precedence sse_server.py's `_public_ip` already uses for
    the connect-rate limiter). Falls back to the ASGI socket's client host
    for local/dev traffic with no Cloudflare in front. Returns "" if
    neither is present; callers must treat that as "unknown", never as
    127.0.0.1 standing in for a real address."""
    headers = dict(scope.get("headers") or [])
    cf_ip = headers.get(b"cf-connecting-ip", b"").decode("utf-8", errors="replace").strip()
    if cf_ip:
        return cf_ip
    client = scope.get("client")
    if client:
        return str(client[0])
    return ""


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

    raw = await _read_body_or_413(receive, send)
    if raw is None:
        return
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

    client_name = meta.get("client_name", "Claude MCP connector")
    clients = _load_clients()

    # Idempotent DCR: an identical (redirect_uris, client_name) request returns
    # the existing registration instead of minting a new one. Collapses the
    # retry/junk-fill vector — repeated identical registrations do not grow the
    # registry (openai/grok-style clients re-register on reconnect).
    for existing_id, rec in clients.items():
        if rec.get("redirect_uris") == redirect_uris and rec.get("client_name") == client_name:
            await _send_json(send, 201, _dcr_response(existing_id, rec))
            return

    # Bounded registry: rather than hard-locking-out legitimate onboarding when
    # full (an availability DoS an unauthenticated attacker could trigger),
    # LRU-evict the oldest client that never completed a token exchange. A
    # client that HAS issued a token (has a live family on disk) is never
    # evicted this way.
    if len(clients) >= MAX_REGISTERED_CLIENTS and not _evict_one_stale_client(clients):
        await _send_json(
            send,
            429,
            {
                "error": "registration_limit_reached",
                "error_description": (
                    "Client registry is full of active clients. HQ can prune "
                    "with 'python -m clients.claude_bridge.cli revoke-all'."
                ),
            },
        )
        return

    client_id = "claude-" + secrets.token_urlsafe(16)
    issued_at = int(_now().timestamp())
    record = {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": meta.get("scope", DEFAULT_SCOPE),
        "client_id_issued_at": issued_at,
        "registered_by": "dcr",
    }
    clients[client_id] = record
    _save_clients(clients)
    logger.info("OAuth DCR: registered client_id=%s redirect_uris=%s", client_id, redirect_uris)

    await _send_json(send, 201, _dcr_response(client_id, record))


def _dcr_response(client_id: str, record: dict) -> dict:
    """RFC 7591 registration response for a stored client record."""
    return {
        "client_id": client_id,
        "client_id_issued_at": record.get("client_id_issued_at"),
        "redirect_uris": record.get("redirect_uris", []),
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": record.get("client_name"),
        "scope": record.get("scope", DEFAULT_SCOPE),
    }


def _client_has_live_family(client_id: str) -> bool:
    """True if this client has ever been issued a token (a token file names it).
    Such clients are never LRU-evicted by a registration flood."""
    for base in (_TOKENS_DIR, _REFRESH_DIR):
        for path in base.glob("*.json"):
            try:
                if json.loads(path.read_text()).get("client_id") == client_id:
                    return True
            except (OSError, json.JSONDecodeError):
                continue
    return False


def _evict_one_stale_client(clients: dict) -> bool:
    """Evict the oldest DCR client that never completed a token exchange.
    Mutates `clients`. Returns True if one was evicted (room made)."""
    candidates = [
        (rec.get("client_id_issued_at", 0), cid)
        for cid, rec in clients.items()
        if rec.get("registered_by") == "dcr" and not _client_has_live_family(cid)
    ]
    if not candidates:
        return False
    candidates.sort()
    _, victim = candidates[0]
    clients.pop(victim, None)
    logger.warning("OAuth DCR: LRU-evicted stale unused client %s to make room", victim)
    return True


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

    bound_audience = _normalize_resource(resource) if resource else CANONICAL_RESOURCE
    tap_code = _mint_tap_code()
    summary = f"Approve Claude connector · client {client_id} → {redirect_uri}"
    requester_ip = _real_client_ip(scope)

    # Delegate the human-approval decision to the bridge's phone-tap gate
    # (loopback, master BRIDGE_TOKEN). Fail closed on ANY error/non-2xx: no
    # approval record is persisted, no waiting page that could ever complete
    # is rendered, no code — mirrors the old unset-passphrase 503 exactly.
    approval = await _bridge_approval_request(
        summary=summary,
        client_id=client_id,
        redirect_uri=redirect_uri,
        audience=bound_audience,
        code=tap_code,
        requester_ip=requester_ip,
    )
    aid = (approval or {}).get("approval_id", "")
    if not approval or not aid:
        await _send_text(
            send,
            503,
            "Authorization is unavailable: the phone-tap approval service could not be "
            "reached. No connector can be authorized until it is.",
        )
        return

    display_code = approval.get("code") or tap_code
    binding = _compute_binding(client_id, redirect_uri, bound_audience, code_challenge)
    _save_approval(
        aid,
        {
            "aid": aid,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "bound_audience": bound_audience,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "scope": scope_str,
            "state": state,
            "resource": resource,
            "code": display_code,
            "binding": binding,
            "created_at": _now().isoformat(),
        },
    )

    html = _render_waiting_page(
        aid=aid,
        code=display_code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        bound_audience=bound_audience,
        code_challenge=code_challenge,
        scope_str=scope_str,
        state=state,
        resource=resource,
        notification_sent=bool(approval.get("notification_sent", True)),
    )
    await _send_html(send, 200, html)


def _render_waiting_page(
    *,
    aid: str,
    code: str,
    client_id: str,
    redirect_uri: str,
    bound_audience: str,
    code_challenge: str,
    scope_str: str,
    state: str,
    resource: str,
    notification_sent: bool,
) -> str:
    """The 'check your phone' waiting page. All server-known values are
    HTML-escaped via `_esc` — no value the JS reads is ever interpolated
    into a script string literal; it is carried via HTML-attribute
    (`data-*`) round-tripping instead (`_esc` is attribute-safe; a JS
    string-literal is not the same escaping context)."""
    deny_params = {"error": "access_denied"}
    if state:
        deny_params["state"] = state
    deny_url = _append_params(redirect_uri, deny_params)
    status_url = "/claude/oauth/authorize/status?approval_id=" + urllib.parse.quote(aid)

    safe_aid = _esc(aid)
    safe_code = _esc(code)
    safe_client = _esc(client_id)
    safe_redirect = _esc(redirect_uri)
    safe_audience = _esc(bound_audience)
    safe_scope = _esc(scope_str or f"{DEFAULT_SCOPE} (full native surface; destructive tier gated)")
    safe_state = _esc(state)
    safe_challenge = _esc(code_challenge)
    safe_resource = _esc(resource)
    safe_deny_url = _esc(deny_url)
    safe_status_url = _esc(status_url)
    notify_note = (
        ""
        if notification_sent
        else '<p class="muted" id="notify-note">Push notification may not have arrived — '
        "open the ntfy app and check the topic directly.</p>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Check your phone — Sovereign Stack</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 560px;
       margin: 4em auto; padding: 0 1.2em; line-height: 1.55; color: #1a1a1a; }}
h1 {{ font-size: 1.4em; margin-bottom: 0.4em; }}
.card {{ border: 1px solid #d8d8d8; border-radius: 10px; padding: 2em;
        background: #fafafa; text-align: center; }}
dl {{ margin: 1.2em 0; text-align: left; }}
dt {{ font-weight: 600; margin-top: 0.6em; color: #555; font-size: 0.85em;
      text-transform: uppercase; letter-spacing: 0.04em; }}
dd {{ margin: 0.2em 0 0.4em 0; }}
code {{ background: #ececec; padding: 0.15em 0.45em; border-radius: 4px;
        font-size: 0.92em; word-break: break-all; }}
.tapcode {{ font-size: 1.8em; font-weight: 700; letter-spacing: 0.03em;
           margin: 0.6em 0; color: #111; }}
.spinner {{ width: 28px; height: 28px; margin: 1em auto; border-radius: 50%;
           border: 3px solid #ddd; border-top-color: #111;
           animation: spin 0.8s linear infinite; }}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.muted {{ color: #666; font-size: 0.9em; }}
.terminal {{ display: none; }}
</style>
</head>
<body>
<div class="card" id="wait" data-aid="{safe_aid}" data-deny-url="{safe_deny_url}"
     data-status-url="{safe_status_url}">
<h1>Check your phone</h1>
<p>A Claude MCP connector is requesting access to the Sovereign Stack.
Only Anthony's tap on his phone can approve it.</p>
<div class="tapcode" id="tapcode">{safe_code}</div>
<p class="muted">Match this code to the one on your phone before tapping Approve.</p>
<div class="spinner" id="spinner"></div>
<p class="muted" id="waiting-note">Waiting for approval…</p>
{notify_note}
<dl>
<dt>Client</dt><dd><code>{safe_client}</code></dd>
<dt>Substrate identity</dt><dd><code>claude-ai-bridge</code></dd>
<dt>Scope requested</dt><dd><code>{safe_scope}</code></dd>
<dt>Audience</dt><dd><code>{safe_audience}</code></dd>
<dt>Redirect target</dt><dd><code>{safe_redirect}</code></dd>
</dl>
<p class="terminal muted" id="terminal-note">This request expired. Reload the page to try again.</p>
<form method="post" action="/claude/oauth/authorize" id="complete">
<input type="hidden" name="action" value="approve"/>
<input type="hidden" name="approval_id" value="{safe_aid}"/>
<input type="hidden" name="client_id" value="{safe_client}"/>
<input type="hidden" name="redirect_uri" value="{safe_redirect}"/>
<input type="hidden" name="code_challenge" value="{safe_challenge}"/>
<input type="hidden" name="code_challenge_method" value="S256"/>
<input type="hidden" name="state" value="{safe_state}"/>
<input type="hidden" name="scope" value="{_esc(scope_str)}"/>
<input type="hidden" name="resource" value="{safe_resource}"/>
</form>
</div>
<script>
(function () {{
  var el = document.getElementById('wait');
  var statusUrl = el.getAttribute('data-status-url');
  var denyUrl = el.getAttribute('data-deny-url');
  var spinner = document.getElementById('spinner');
  var waitingNote = document.getElementById('waiting-note');
  var terminalNote = document.getElementById('terminal-note');
  var elapsedMs = 0;
  var intervalMs = 5000;
  var maxMs = 900000; // 900s pending window
  var timer = null;

  function stop(message) {{
    if (timer) {{ clearInterval(timer); timer = null; }}
    if (spinner) {{ spinner.style.display = 'none'; }}
    if (message && waitingNote) {{ waitingNote.textContent = message; }}
  }}

  function poll() {{
    elapsedMs += intervalMs;
    if (elapsedMs > maxMs) {{
      stop('This request expired.');
      if (terminalNote) {{ terminalNote.style.display = 'block'; }}
      return;
    }}
    fetch(statusUrl, {{ cache: 'no-store' }})
      .then(function (r) {{ return r.json(); }})
      .then(function (body) {{
        var status = body && body.status;
        if (status === 'approved') {{
          stop('Approved — completing…');
          document.getElementById('complete').submit();
        }} else if (status === 'denied' || status === 'expired') {{
          // Only these two are an actual decision the phone made. The
          // server already normalizes anything else (including a stray
          // slow_down or an unrecognized future value) to 'pending', but
          // this allowlist is belt-and-suspenders: an unexpected status
          // must NEVER abort a live wait, so only an explicit denied or
          // expired ever redirects.
          stop('Not approved.');
          window.location.href = denyUrl;
        }} else {{
          // pending | unavailable | anything unexpected: keep polling.
          // 'unavailable' is a transient bridge hiccup, not a decision.
          if (body && body.notification_sent === false && waitingNote) {{
            waitingNote.textContent = 'Waiting — push may not have arrived. Open your ntfy app.';
          }}
        }}
      }})
      .catch(function () {{ /* transient poll hiccup — try again next tick */ }});
  }}

  timer = setInterval(poll, intervalMs);
}})();
</script>
</body>
</html>"""


async def _authorize_post(receive, send) -> None:
    body = await _read_body_or_413(receive, send)
    if body is None:
        return
    form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}

    action = form.get("action", "")
    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    code_challenge = form.get("code_challenge", "")
    code_challenge_method = form.get("code_challenge_method", "")
    state = form.get("state", "")
    scope_str = form.get("scope", "")
    resource = form.get("resource", "")
    aid = form.get("approval_id", "")

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

    # THE resource-owner authentication. OAuth authenticates the client; the
    # ntfy phone-tap (delegated to the bridge, over loopback) authenticates
    # the human. No known approval_id => no code, ever.
    record = _load_approval(aid) if aid else None
    if record is None:
        await _send_text(
            send, 403, "Approval refused: unknown, expired, or already-used approval_id."
        )
        return

    # Binding check (SSE-side, authoritative — the SSE is the only party that
    # mints the code): recompute from the submitted form and compare
    # constant-time to what the GET render stored (HQ req #3 / spec §4). A
    # mismatch means this POST does not describe the request Anthony saw and
    # tapped approve on — replay/tamper signal.
    bound_audience = _normalize_resource(resource) if resource else CANONICAL_RESOURCE
    recomputed_binding = _compute_binding(client_id, redirect_uri, bound_audience, code_challenge)
    if not hmac.compare_digest(recomputed_binding, record.get("binding", "")):
        logger.warning(
            "OAuth: authorize binding MISMATCH aid=%s client=%s — refused",
            aid[:12],
            client_id,
        )
        await _send_text(
            send, 403, "Approval refused: submitted request does not match the approved one."
        )
        return

    # Re-validate everything (defence in depth — POST data could be tampered).
    error = _validate_authorize_params(
        client_id, redirect_uri, code_challenge, code_challenge_method, resource
    )
    if error:
        await _send_text(send, 400, error)
        return

    # The bridge's atomic approved→consumed flip (loopback, master
    # BRIDGE_TOKEN). Only a returned {approved: true} may proceed to mint —
    # any bridge error, non-2xx, or {approved: false} fails closed.
    confirmation = await _bridge_approval_confirm(aid)
    if confirmation is None or confirmation.get("approved") is not True:
        logger.warning(
            "OAuth: authorize approval NOT confirmed aid=%s client=%s reason=%s",
            aid[:12],
            client_id,
            (confirmation or {}).get("reason", "bridge_unreachable_or_non_2xx"),
        )
        await _send_text(
            send,
            403,
            "Approval refused: the phone tap was not confirmed (denied, expired, or already used).",
        )
        return

    # Belt-and-suspenders single-use on top of the bridge's atomic flip.
    # Deleted only on the success path (spec §3c step 4) — a transient
    # confirm hiccup must not strand the local record before a legitimate
    # retry finds it again.
    _delete_approval(aid)

    # THE only code-mint site in this module, reachable only through the
    # {approved: true} branch above.
    code = secrets.token_urlsafe(32)
    code_data = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": scope_str or DEFAULT_SCOPE,
        "audience": bound_audience,
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


# Only these three ever count as a DECISION to the polling browser. The
# connector-authorize status endpoint is master-gated and the only poller is
# the trusted SSE (~5s, on behalf of one browser) — arrival's anti-abuse
# poll-discipline (which can emit `slow_down`) does not apply to this path,
# but even so an unexpected/unrecognized status (a bridge hiccup, a future
# status value, a `slow_down` that reaches here anyway) must NEVER read as a
# refusal and abort a live wait (FIX 1). Anything outside this set normalizes
# to "pending" — the 900s pending-window expiry is what eventually ends a
# truly stuck wait, not a misread status.
_TERMINAL_STATUSES = frozenset({"approved", "denied", "expired"})


async def handle_claude_oauth_authorize_status(scope, receive, send) -> None:
    """GET /claude/oauth/authorize/status?approval_id=<aid> — the waiting
    page's poll target. Proxies to the bridge's read-only status oracle
    (GET /api/approval/status/{aid}); never flips state, never mints. Fails
    closed to a non-approved status on any bridge error — an unreachable
    bridge must never read as 'approved' to the polling browser. Any status
    the bridge returns that is NOT approved/denied/expired is normalized to
    'pending' here (FIX 1, belt-and-suspenders against a bridge-side
    `slow_down` or any other unexpected value aborting the wait)."""
    if scope.get("method", "GET") != "GET":
        await _send_json(send, 405, {"error": "method_not_allowed"})
        return
    aid = _q(scope).get("approval_id", "")
    if not aid:
        await _send_json(send, 400, {"error": "approval_id required"})
        return
    body = await _bridge_approval_status(aid)
    if body is None:
        await _send_json(send, 503, {"status": "unavailable"})
        return
    raw_status = body.get("status", "pending")
    resp = {"status": raw_status if raw_status in _TERMINAL_STATUSES else "pending"}
    if "notification_sent" in body:
        resp["notification_sent"] = body["notification_sent"]
    if "poll_interval_seconds" in body:
        resp["poll_interval_seconds"] = body["poll_interval_seconds"]
    await _send_json(send, 200, resp)


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

    body = await _read_body_or_413(receive, send)
    if body is None:
        return
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

    body = await _read_body_or_413(receive, send)
    if body is None:
        return
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
