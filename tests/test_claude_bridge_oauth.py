"""
Claude-bridge OAuth 2.1 tests — the security core of the connector.

Covers the four hardening deltas over the openai template that are UNCHANGED
by the 2026-07 phone-tap swap (mandatory PKCE S256, RFC 8707 audience
binding, refresh rotation with reuse-detection family revocation, pinned
redirect URIs), plus the swap itself: the operator passphrase
(`CLAUDE_AUTHORIZE_SECRET`) and the GET->POST consent nonce are RETIRED. The
resource-owner control is now an ntfy phone-tap delegated to the bridge over
loopback — `GET /authorize` asks the bridge to create a pending approval and
push Anthony's phone; `POST /authorize` mints a code only when (a) the
submitted params bind (sha256, constant-time) to what the GET created and
(b) the bridge's atomic approved->consumed confirm returns
`{approved: true}`. The bridge itself is never real here — every test in
this file mocks `claude_bridge.oauth._bridge_approval_{request,status,
confirm}` (the handler-level seam) or, for the low-level network-path
tests, `httpx.AsyncClient` via `httpx.MockTransport` (no real network).

House style per tests/test_sse_gate.py: raw ASGI scopes, asyncio.run with
captured send lists, module-constant monkeypatching, no network, no TestClient.
"""

import asyncio
import base64
import hashlib
import json
import re
import secrets

# clients/ is placed on sys.path by sovereign_stack.sse_server at import; do it
# directly here so this file does not depend on import order.
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

_CLIENTS = Path(__file__).parent.parent / "clients"
if str(_CLIENTS) not in sys.path:
    sys.path.insert(0, str(_CLIENTS))

from claude_bridge import oauth  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────


def _scope(method="GET", path="/claude/oauth/authorize", query="", headers=None, client=None):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": headers or [],
    }
    if client is not None:
        scope["client"] = client
    return scope


def _run(handler, scope, body=b""):
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        sent.append(msg)

    asyncio.run(handler(scope, receive, send))
    return sent


def _status(sent):
    return sent[0]["status"]


def _headers(sent):
    return dict(sent[0].get("headers", []))


def _body(sent) -> bytes:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def _json(sent) -> dict:
    return json.loads(_body(sent))


def _form(d: dict) -> bytes:
    return urllib.parse.urlencode(d).encode()


def _pkce_pair():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


CALLBACK = "https://claude.ai/api/mcp/auth_callback"
CALLBACK_COM = "https://claude.com/api/mcp/auth_callback"


@pytest.fixture
def oauth_env(tmp_path, monkeypatch):
    """Hermetic storage + a registered client + a phone-tap bridge stub that
    approves by default (so the token/refresh/revoke suites, which only need
    a code minted and don't exercise the gate itself, keep working exactly
    as before). Gate-specific tests override the stubs per-case. Returns the
    client_id."""
    codes = tmp_path / "codes"
    tokens = tmp_path / "tokens"
    refresh = tmp_path / "refresh"
    approvals = tmp_path / "approvals"
    for d in (codes, tokens, refresh, approvals):
        d.mkdir()
    monkeypatch.setattr(oauth, "_CODES_DIR", codes)
    monkeypatch.setattr(oauth, "_TOKENS_DIR", tokens)
    monkeypatch.setattr(oauth, "_REFRESH_DIR", refresh)
    monkeypatch.setattr(oauth, "_APPROVALS_DIR", approvals)
    monkeypatch.setattr(oauth, "_CLIENTS_FILE", tmp_path / "oauth_clients.json")
    monkeypatch.setattr(oauth, "_BRIDGE_TOKEN", "test-bridge-token")

    async def _approve_request(
        *, summary, client_id, redirect_uri, audience, code, requester_ip=""
    ):
        return {
            "approval_id": "aid-" + secrets.token_urlsafe(12),
            "code": code,
            "notification_sent": True,
        }

    async def _approve_confirm(aid):
        return {"approved": True}

    async def _approve_status(aid):
        return {"status": "approved"}

    monkeypatch.setattr(oauth, "_bridge_approval_request", _approve_request)
    monkeypatch.setattr(oauth, "_bridge_approval_confirm", _approve_confirm)
    monkeypatch.setattr(oauth, "_bridge_approval_status", _approve_status)

    client_id = "claude-test-client"
    oauth._save_clients(
        {
            client_id: {
                "client_id": client_id,
                "redirect_uris": [CALLBACK],
                "grant_types": ["authorization_code", "refresh_token"],
                "registered_by": "dcr",
                "client_id_issued_at": 1,
            }
        }
    )
    return client_id


def _get_authorize(client_id, challenge="", method="S256", resource="", scope_str="", state=""):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": CALLBACK,
    }
    if challenge:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = method
    if resource:
        params["resource"] = resource
    if scope_str:
        params["scope"] = scope_str
    if state:
        params["state"] = state
    return _run(oauth.handle_authorize, _scope(query=urllib.parse.urlencode(params)))


_AID_RE = re.compile(rb'name="approval_id" value="([^"]*)"')


def _extract_aid(html: bytes) -> str:
    m = _AID_RE.search(html)
    assert m, html
    return m.group(1).decode()


def _complete_form(aid, client_id, challenge, resource="", scope_str="", state="st4te"):
    form = {
        "action": "approve",
        "approval_id": aid,
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": scope_str,
    }
    if resource:
        form["resource"] = resource
    return form


def _create_approval(client_id, challenge, resource="", scope_str="", state="st4te") -> str:
    """Run the GET (phone-tap request, mocked-approved by the fixture's
    default stub) and return the minted approval_id."""
    sent = _get_authorize(client_id, challenge, resource=resource, scope_str=scope_str, state=state)
    assert _status(sent) == 200, _body(sent)
    return _extract_aid(_body(sent))


def _authorize_code(
    client_id: str, challenge: str, resource: str = "", scope_str: str = "", state: str = "st4te"
) -> str:
    """Run the full GET (creates + bridge-approves the approval) then POST
    (bridge-confirms) and return the minted code from the redirect."""
    aid = _create_approval(
        client_id, challenge, resource=resource, scope_str=scope_str, state=state
    )
    form = _complete_form(
        aid, client_id, challenge, resource=resource, scope_str=scope_str, state=state
    )
    sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
    assert _status(sent) == 302, _body(sent)
    location = _headers(sent)[b"location"].decode()
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    return q["code"][0]


def _exchange(client_id: str, code: str, verifier: str, resource: str = ""):
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": CALLBACK,
        "client_id": client_id,
    }
    if resource:
        form["resource"] = resource
    return _run(
        oauth.handle_token, _scope(method="POST", path="/claude/oauth/token"), body=_form(form)
    )


def _full_grant(client_id: str):
    verifier, challenge = _pkce_pair()
    code = _authorize_code(client_id, challenge)
    sent = _exchange(client_id, code, verifier)
    assert _status(sent) == 200, _body(sent)
    return _json(sent)


# ── Redirect pinning ──────────────────────────────────────────────────────────


class TestRedirectPinning:
    def test_claude_callbacks_pinned(self):
        assert oauth.redirect_uri_pinned("https://claude.ai/api/mcp/auth_callback")
        assert oauth.redirect_uri_pinned("https://claude.com/api/mcp/auth_callback")

    def test_arbitrary_https_refused(self):
        assert not oauth.redirect_uri_pinned("https://evil.example/api/mcp/auth_callback")
        assert not oauth.redirect_uri_pinned("https://claude.ai.evil.example/callback")

    def test_loopback_default_off(self):
        # Hardened default: loopback redirects are OFF unless explicitly enabled
        # for local Claude Code dev (else a self-approved code can be read out of
        # the 302 by a curl-controlled endpoint).
        assert oauth._ALLOW_LOOPBACK is False
        assert not oauth.redirect_uri_pinned("http://localhost:8123/callback")

    def test_loopback_allowed_when_enabled(self, monkeypatch):
        monkeypatch.setattr(oauth, "_ALLOW_LOOPBACK", True)
        assert oauth.redirect_uri_pinned("http://localhost:8123/callback")
        assert oauth.redirect_uri_pinned("http://127.0.0.1:41999/callback")

    def test_loopback_optout(self, monkeypatch):
        monkeypatch.setattr(oauth, "_ALLOW_LOOPBACK", False)
        assert not oauth.redirect_uri_pinned("http://localhost:8123/callback")

    def test_http_non_loopback_refused(self):
        assert not oauth.redirect_uri_pinned("http://192.168.1.5/callback")
        assert not oauth.redirect_uri_pinned("http://claude.ai/api/mcp/auth_callback")


# ── Dynamic Client Registration ───────────────────────────────────────────────


class TestDCR:
    def test_pinned_redirect_registers(self, oauth_env):
        sent = _run(
            oauth.handle_register,
            _scope(method="POST", path="/claude/oauth/register"),
            body=json.dumps({"redirect_uris": [CALLBACK], "client_name": "claude.ai"}).encode(),
        )
        assert _status(sent) == 201
        body = _json(sent)
        assert body["client_id"].startswith("claude-")
        assert "refresh_token" in body["grant_types"]
        assert oauth._is_known_client(body["client_id"])

    def test_unpinned_redirect_refused(self, oauth_env):
        sent = _run(
            oauth.handle_register,
            _scope(method="POST", path="/claude/oauth/register"),
            body=json.dumps({"redirect_uris": ["https://evil.example/cb"]}).encode(),
        )
        assert _status(sent) == 400
        assert _json(sent)["error"] == "invalid_redirect_uri"

    def test_missing_redirects_refused(self, oauth_env):
        sent = _run(
            oauth.handle_register,
            _scope(method="POST", path="/claude/oauth/register"),
            body=b"{}",
        )
        assert _status(sent) == 400

    def _register(self, name):
        return _run(
            oauth.handle_register,
            _scope(method="POST", path="/claude/oauth/register"),
            body=json.dumps({"redirect_uris": [CALLBACK], "client_name": name}).encode(),
        )

    def test_identical_registration_is_idempotent(self, oauth_env):
        # An identical (redirect_uris, client_name) returns the SAME client_id
        # instead of growing the registry — collapses the retry/junk-fill vector.
        s1 = self._register("claude.ai")
        s2 = self._register("claude.ai")
        assert _status(s1) == 201 and _status(s2) == 201
        assert _json(s1)["client_id"] == _json(s2)["client_id"]

    def test_cap_evicts_stale_unused_client_not_lockout(self, oauth_env, monkeypatch):
        # At cap, a distinct new registration evicts the oldest stale (never
        # token-issued) client rather than 429-locking-out onboarding.
        monkeypatch.setattr(
            oauth, "MAX_REGISTERED_CLIENTS", 1
        )  # fixture registered one stale client
        sent = self._register("a-different-client")
        assert _status(sent) == 201
        # The stale fixture client was evicted to make room.
        assert not oauth._is_known_client(oauth_env)
        assert oauth._is_known_client(_json(sent)["client_id"])

    def test_cap_locks_out_only_when_all_active(self, oauth_env, monkeypatch):
        # If every client has issued a token (non-evictable), the cap 429s —
        # bounded growth without evicting a live client.
        monkeypatch.setattr(oauth, "MAX_REGISTERED_CLIENTS", 1)
        # give the fixture client a live token family so it cannot be evicted
        oauth._mint_token_pair(oauth_env, "native", oauth.CANONICAL_RESOURCE, secrets.token_hex(8))
        sent = self._register("cannot-fit")
        assert _status(sent) == 429
        assert _json(sent)["error"] == "registration_limit_reached"

    def test_oversize_body_413(self, oauth_env, monkeypatch):
        monkeypatch.setattr(oauth, "MAX_OAUTH_BODY_BYTES", 128)
        big = json.dumps({"redirect_uris": [CALLBACK], "junk": "x" * 500}).encode()
        sent = _run(
            oauth.handle_register, _scope(method="POST", path="/claude/oauth/register"), body=big
        )
        assert _status(sent) == 413


# ── Authorize: PKCE S256 mandatory + RFC 8707 ─────────────────────────────────


class TestAuthorizeGet:
    def test_missing_challenge_refused(self, oauth_env):
        sent = _get_authorize(oauth_env)
        assert _status(sent) == 400
        assert b"code_challenge required" in _body(sent)

    def test_plain_method_refused(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = _get_authorize(oauth_env, challenge=challenge, method="plain")
        assert _status(sent) == 400
        assert b"S256" in _body(sent)

    def test_s256_renders_waiting_page(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = _get_authorize(oauth_env, challenge=challenge)
        assert _status(sent) == 200
        assert b"Check your phone" in _body(sent)

    def test_foreign_resource_refused(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = _get_authorize(oauth_env, challenge=challenge, resource="https://evil.example/mcp")
        assert _status(sent) == 400
        assert b"invalid_target" in _body(sent)

    def test_canonical_resource_accepted(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = _get_authorize(oauth_env, challenge=challenge, resource=oauth.CANONICAL_RESOURCE)
        assert _status(sent) == 200

    def test_unknown_client_refused(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = _get_authorize("claude-nobody", challenge=challenge)
        assert _status(sent) == 400

    def test_invalid_params_never_reach_the_bridge(self, oauth_env, monkeypatch):
        # Params are validated BEFORE the bridge is ever contacted — an
        # invalid request must not cost Anthony a phone push.
        called = []

        async def _spy(**kwargs):
            called.append(kwargs)
            return {"approval_id": "x", "code": "a-b"}

        monkeypatch.setattr(oauth, "_bridge_approval_request", _spy)
        sent = _get_authorize(oauth_env)  # no challenge -> invalid, 400
        assert _status(sent) == 400
        assert called == []

    def _authorize_scope(self, client_id, challenge, headers=None, client=None):
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return _scope(query=urllib.parse.urlencode(params), headers=headers, client=client)

    def test_requester_ip_forwarded_from_cf_connecting_ip_header(self, oauth_env, monkeypatch):
        # FIX 3: the bridge's per-IP DoS caps are useless if every caller
        # collapses to the SSE's loopback address. The header (set by
        # Cloudflare on tunneled traffic) must win even when the ASGI
        # scope's own client is the tunnel's loopback hop.
        seen = {}

        async def _spy(*, requester_ip, **kwargs):
            seen["requester_ip"] = requester_ip
            return {"approval_id": "aid-cf", "code": "a-b", "notification_sent": True}

        monkeypatch.setattr(oauth, "_bridge_approval_request", _spy)
        _, challenge = _pkce_pair()
        scope = self._authorize_scope(
            oauth_env,
            challenge,
            headers=[(b"cf-connecting-ip", b"203.0.113.7")],
            client=("127.0.0.1", 54321),
        )
        sent = _run(oauth.handle_authorize, scope)
        assert _status(sent) == 200
        assert seen["requester_ip"] == "203.0.113.7"

    def test_requester_ip_falls_back_to_asgi_client_without_header(self, oauth_env, monkeypatch):
        seen = {}

        async def _spy(*, requester_ip, **kwargs):
            seen["requester_ip"] = requester_ip
            return {"approval_id": "aid-direct", "code": "a-b", "notification_sent": True}

        monkeypatch.setattr(oauth, "_bridge_approval_request", _spy)
        _, challenge = _pkce_pair()
        scope = self._authorize_scope(oauth_env, challenge, client=("198.51.100.9", 443))
        sent = _run(oauth.handle_authorize, scope)
        assert _status(sent) == 200
        assert seen["requester_ip"] == "198.51.100.9"

    def test_requester_ip_empty_when_neither_present(self, oauth_env, monkeypatch):
        seen = {}

        async def _spy(*, requester_ip, **kwargs):
            seen["requester_ip"] = requester_ip
            return {"approval_id": "aid-none", "code": "a-b", "notification_sent": True}

        monkeypatch.setattr(oauth, "_bridge_approval_request", _spy)
        _, challenge = _pkce_pair()
        scope = self._authorize_scope(oauth_env, challenge)
        sent = _run(oauth.handle_authorize, scope)
        assert _status(sent) == 200
        assert seen["requester_ip"] == ""


class TestRealClientIp:
    """Unit coverage for `_real_client_ip` (FIX 3) independent of the
    authorize handler."""

    def test_prefers_cf_connecting_ip_header_over_asgi_client(self):
        scope = _scope(headers=[(b"cf-connecting-ip", b"203.0.113.7")], client=("127.0.0.1", 1))
        assert oauth._real_client_ip(scope) == "203.0.113.7"

    def test_falls_back_to_asgi_client_host_without_header(self):
        scope = _scope(client=("198.51.100.9", 443))
        assert oauth._real_client_ip(scope) == "198.51.100.9"

    def test_empty_string_when_neither_present(self):
        assert oauth._real_client_ip(_scope()) == ""

    def test_blank_cf_header_falls_back_to_asgi_client(self):
        scope = _scope(headers=[(b"cf-connecting-ip", b"")], client=("198.51.100.9", 443))
        assert oauth._real_client_ip(scope) == "198.51.100.9"


class TestAuthorizePost:
    def test_approve_binds_canonical_audience_when_resource_absent(self, oauth_env):
        _, challenge = _pkce_pair()
        code = _authorize_code(oauth_env, challenge)
        data = oauth._load_code(code)
        assert data["audience"] == oauth.CANONICAL_RESOURCE
        assert data["code_challenge_method"] == "S256"

    def test_approve_normalizes_presented_resource(self, oauth_env):
        _, challenge = _pkce_pair()
        code = _authorize_code(oauth_env, challenge, resource=oauth.CANONICAL_RESOURCE + "/")
        data = oauth._load_code(code)
        assert data["audience"] == oauth._normalize_resource(oauth.CANONICAL_RESOURCE)

    def test_post_revalidates_challenge_method_even_after_binding_match(self, oauth_env):
        # code_challenge_method is NOT part of the binding hash (only the
        # challenge VALUE is, per build-spec §4) — a tampered method must
        # still be caught by the POST's defense-in-depth re-validation even
        # though the binding matches.
        _, challenge = _pkce_pair()
        aid = _create_approval(oauth_env, challenge)
        form = _complete_form(aid, oauth_env, challenge)
        form["code_challenge_method"] = "plain"
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 400
        assert b"S256" in _body(sent)

    def test_deny_redirects_access_denied(self, oauth_env):
        # Deny short-circuits before the approval gate entirely — no aid,
        # no bridge call, needed to say no.
        form = {"action": "deny", "client_id": oauth_env, "redirect_uri": CALLBACK, "state": "s"}
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 302
        assert b"access_denied" in _headers(sent)[b"location"]

    def test_deny_never_redirects_to_unpinned_target(self, oauth_env):
        form = {"action": "deny", "client_id": oauth_env, "redirect_uri": "https://evil.example/cb"}
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 400

    def test_deny_never_calls_the_bridge(self, oauth_env, monkeypatch):
        called = []

        async def _spy(aid):
            called.append(aid)
            return {"approved": True}

        monkeypatch.setattr(oauth, "_bridge_approval_confirm", _spy)
        form = {"action": "deny", "client_id": oauth_env, "redirect_uri": CALLBACK, "state": "s"}
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 302
        assert called == []


# ── Phone-tap approval: the load-bearing control (2026-07 swap) ───────────────


class TestPhoneTapApproval:
    """No passphrase, no nonce: the ntfy tap, delegated to the bridge over
    loopback, is the whole gate. approval_id (a bridge-minted capability) +
    the SSE-side binding check + the bridge's atomic confirm are what stand
    in the old design's place. Every case here runs against the mocked
    bridge (no network) — see TestBridgeApprovalCalls for the real network
    code path."""

    _, _CHAL = _pkce_pair()

    def test_passphrase_and_nonce_symbols_fully_removed(self):
        for name in (
            "CLAUDE_AUTHORIZE_SECRET",
            "_approval_secret_ok",
            "_mint_nonce",
            "_nonce_valid",
            "_consume_nonce",
            "_used_nonces",
            "NONCE_TTL_SECONDS",
            "_NONCE_SIGNING_KEY",
            "CLAUDE_AUTHORIZE_NONCE_KEY",
            "approval_enabled",
        ):
            assert not hasattr(oauth, name), f"{name} should have been removed"

    def test_get_creates_pending_approval_and_renders_waiting_page(self, oauth_env):
        sent = _get_authorize(oauth_env, challenge=self._CHAL)
        assert _status(sent) == 200
        body = _body(sent)
        assert b"Check your phone" in body
        assert b'name="approval_id"' in body
        # no trace of the retired passphrase/nonce UI
        assert b"approval_secret" not in body
        assert b'name="nonce"' not in body
        # the persisted approvals record actually exists, keyed by the aid
        # the (mocked) bridge minted
        aid = _extract_aid(body)
        assert oauth._load_approval(aid) is not None

    def test_get_fails_closed_when_bridge_unreachable(self, oauth_env, monkeypatch):
        async def _unreachable(**kwargs):
            return None  # what a connection error / non-2xx collapses to

        monkeypatch.setattr(oauth, "_bridge_approval_request", _unreachable)
        sent = _get_authorize(oauth_env, challenge=self._CHAL)
        assert _status(sent) == 503
        assert not list(oauth._APPROVALS_DIR.glob("*.json"))

    def test_get_fails_closed_when_bridge_response_missing_approval_id(
        self, oauth_env, monkeypatch
    ):
        async def _malformed(**kwargs):
            return {"code": "a-b"}  # 2xx-shaped but no approval_id

        monkeypatch.setattr(oauth, "_bridge_approval_request", _malformed)
        sent = _get_authorize(oauth_env, challenge=self._CHAL)
        assert _status(sent) == 503
        assert not list(oauth._APPROVALS_DIR.glob("*.json"))

    def test_post_missing_approval_id_refused(self, oauth_env):
        form = _complete_form("", oauth_env, self._CHAL)
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 403

    def test_post_unknown_approval_id_refused(self, oauth_env):
        form = _complete_form("never-issued-aid", oauth_env, self._CHAL)
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 403

    def test_post_binding_mismatch_wrong_client_refused(self, oauth_env):
        aid = _create_approval(oauth_env, self._CHAL)
        form = _complete_form(aid, "claude-someone-else", self._CHAL)
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 403
        assert b"does not match" in _body(sent)

    def test_post_binding_mismatch_wrong_challenge_refused(self, oauth_env):
        aid = _create_approval(oauth_env, self._CHAL)
        _, other_challenge = _pkce_pair()
        form = _complete_form(aid, oauth_env, other_challenge)
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 403

    def test_post_binding_mismatch_wrong_redirect_refused(self, oauth_env):
        # Both callbacks are independently pinned targets — the binding, not
        # merely pinning, is what catches the swap.
        aid = _create_approval(oauth_env, self._CHAL)
        form = _complete_form(aid, oauth_env, self._CHAL)
        form["redirect_uri"] = CALLBACK_COM
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 403

    def test_post_requires_bridge_approved_true(self, oauth_env, monkeypatch):
        aid = _create_approval(oauth_env, self._CHAL)

        async def _denied(_aid):
            return {"approved": False, "reason": "denied"}

        monkeypatch.setattr(oauth, "_bridge_approval_confirm", _denied)
        form = _complete_form(aid, oauth_env, self._CHAL)
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 403
        assert not list(oauth._CODES_DIR.glob("*.json"))
        # NOT consumed locally on a refused confirm (deleted only on the
        # success path) — a legitimate retry (e.g. Anthony taps a moment
        # later, or the browser resubmits) can still find the record.
        assert oauth._load_approval(aid) is not None

    def test_post_fails_closed_when_confirm_bridge_unreachable(self, oauth_env, monkeypatch):
        aid = _create_approval(oauth_env, self._CHAL)

        async def _unreachable(_aid):
            return None

        monkeypatch.setattr(oauth, "_bridge_approval_confirm", _unreachable)
        form = _complete_form(aid, oauth_env, self._CHAL)
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 403
        assert not list(oauth._CODES_DIR.glob("*.json"))

    def test_post_fails_closed_when_confirm_returns_unexpected_shape(self, oauth_env, monkeypatch):
        aid = _create_approval(oauth_env, self._CHAL)

        async def _weird(_aid):
            return {"approved": "yes"}  # truthy string, not the bool True

        monkeypatch.setattr(oauth, "_bridge_approval_confirm", _weird)
        form = _complete_form(aid, oauth_env, self._CHAL)
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 403
        assert not list(oauth._CODES_DIR.glob("*.json"))

    def test_post_succeeds_only_after_approved_true_and_consumes_locally(self, oauth_env):
        aid = _create_approval(oauth_env, self._CHAL)
        form = _complete_form(aid, oauth_env, self._CHAL)
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 302
        location = _headers(sent)[b"location"].decode()
        assert "code=" in location
        # single-use: the local approval record is gone after a successful mint
        assert oauth._load_approval(aid) is None

    def test_replaying_a_consumed_approval_id_refused(self, oauth_env):
        aid = _create_approval(oauth_env, self._CHAL)
        form = _complete_form(aid, oauth_env, self._CHAL)
        assert _status(_run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))) == 302
        # replay the identical POST with the now-consumed approval_id
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 403

    def test_approval_id_charset_is_not_assumed(self, oauth_env, monkeypatch):
        # The build-spec never pins the bridge's approval_id charset. The
        # local record is keyed on sha256(aid), not the raw string, so a
        # bridge that mints standard base64 (+ / =), a namespaced id
        # (":"), or anything else still round-trips correctly — a tap that
        # approved must never come back "unknown approval_id".
        odd_aid = "ns:req+7/9=="

        async def _request_odd_aid(**kwargs):
            return {"approval_id": odd_aid, "code": kwargs["code"], "notification_sent": True}

        monkeypatch.setattr(oauth, "_bridge_approval_request", _request_odd_aid)
        sent = _get_authorize(oauth_env, challenge=self._CHAL)
        assert _status(sent) == 200
        assert _extract_aid(_body(sent)) == odd_aid
        assert oauth._load_approval(odd_aid) is not None

        form = _complete_form(odd_aid, oauth_env, self._CHAL)
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 302, _body(sent)
        assert oauth._load_approval(odd_aid) is None  # consumed


class TestAuthorizeStatusEndpoint:
    """GET /claude/oauth/authorize/status — the waiting page's poll target.
    Read-only proxy to the bridge; never mints, never flips state."""

    def _poll(self, aid):
        return _run(
            oauth.handle_claude_oauth_authorize_status,
            _scope(method="GET", path="/claude/oauth/authorize/status", query=f"approval_id={aid}"),
        )

    def test_proxies_bridge_status(self, oauth_env):
        _, challenge = _pkce_pair()
        aid = _create_approval(oauth_env, challenge)
        sent = self._poll(aid)
        assert _status(sent) == 200
        assert _json(sent)["status"] == "approved"  # fixture's default stub

    def test_fails_closed_on_bridge_error(self, oauth_env, monkeypatch):
        async def _unreachable(_aid):
            return None

        monkeypatch.setattr(oauth, "_bridge_approval_status", _unreachable)
        sent = self._poll("aid-x")
        assert _status(sent) == 503
        assert _json(sent)["status"] == "unavailable"

    def test_requires_approval_id(self, oauth_env):
        sent = _run(
            oauth.handle_claude_oauth_authorize_status,
            _scope(method="GET", path="/claude/oauth/authorize/status", query=""),
        )
        assert _status(sent) == 400

    def test_rejects_non_get(self, oauth_env):
        sent = _run(
            oauth.handle_claude_oauth_authorize_status,
            _scope(method="POST", path="/claude/oauth/authorize/status", query="approval_id=aid-x"),
        )
        assert _status(sent) == 405

    def test_never_mints_or_flips_on_pending(self, oauth_env, monkeypatch):
        async def _pending(_aid):
            return {"status": "pending", "notification_sent": False}

        monkeypatch.setattr(oauth, "_bridge_approval_status", _pending)
        sent = self._poll("aid-x")
        assert _status(sent) == 200
        body = _json(sent)
        assert body["status"] == "pending"
        assert body["notification_sent"] is False
        assert not list(oauth._CODES_DIR.glob("*.json"))

    def test_unexpected_statuses_normalize_to_pending(self, oauth_env, monkeypatch):
        # FIX 1: the connector status endpoint is master-gated and its only
        # poller is the trusted SSE — arrival's anti-abuse poll-discipline
        # (which can emit `slow_down`) does not apply here, but even if a
        # `slow_down` or any other unrecognized status reaches this proxy it
        # must NEVER read as a decision and abort a live wait.
        for weird in ("slow_down", "consumed", "some_future_status"):

            async def _weird(_aid, weird=weird):
                return {"status": weird}

            monkeypatch.setattr(oauth, "_bridge_approval_status", _weird)
            sent = self._poll("aid-x")
            assert _status(sent) == 200, weird
            assert _json(sent)["status"] == "pending", weird

    def test_terminal_statuses_pass_through_unchanged(self, oauth_env, monkeypatch):
        for terminal in ("approved", "denied", "expired"):

            async def _terminal(_aid, terminal=terminal):
                return {"status": terminal}

            monkeypatch.setattr(oauth, "_bridge_approval_status", _terminal)
            sent = self._poll("aid-x")
            assert _json(sent)["status"] == terminal, terminal

    def test_poll_interval_seconds_passed_through_when_present(self, oauth_env, monkeypatch):
        async def _with_interval(_aid):
            return {"status": "pending", "poll_interval_seconds": 8}

        monkeypatch.setattr(oauth, "_bridge_approval_status", _with_interval)
        sent = self._poll("aid-x")
        assert _json(sent)["poll_interval_seconds"] == 8

    def test_poll_interval_seconds_absent_when_bridge_omits_it(self, oauth_env):
        # fixture's default stub ({"status": "approved"}) carries no
        # poll_interval_seconds — the proxy must not invent one.
        sent = self._poll("aid-x")
        assert "poll_interval_seconds" not in _json(sent)


class TestWaitingPageJS:
    """Regression coverage for FIX 1's JS-side status branching: only
    'approved' submits, only 'denied'/'expired' redirect to the deny URL,
    and everything else (pending, unavailable, or an unrecognized value
    that somehow reached the browser un-normalized) keeps polling."""

    def test_only_approved_submits_only_denied_expired_redirects(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = _get_authorize(oauth_env, challenge=challenge)
        html = _body(sent).decode()

        assert "status === 'approved'" in html
        assert "status === 'denied' || status === 'expired'" in html
        assert "document.getElementById('complete').submit();" in html
        assert "window.location.href = denyUrl;" in html

        # No other status is ever compared against explicitly — pending,
        # unavailable, slow_down, consumed, or anything unrecognized all
        # fall through to the bare `else` (keep polling), by construction,
        # not by an enumerated clause.
        for other in ("pending", "unavailable", "slow_down", "consumed"):
            assert f"status === '{other}'" not in html


class TestComputeBinding:
    def test_deterministic(self):
        a = oauth._compute_binding("c", "r", "aud", "chal")
        b = oauth._compute_binding("c", "r", "aud", "chal")
        assert a == b

    def test_sensitive_to_client_id(self):
        base = oauth._compute_binding("c", "r", "aud", "chal")
        assert oauth._compute_binding("x", "r", "aud", "chal") != base

    def test_sensitive_to_redirect_uri(self):
        base = oauth._compute_binding("c", "r", "aud", "chal")
        assert oauth._compute_binding("c", "x", "aud", "chal") != base

    def test_sensitive_to_audience(self):
        base = oauth._compute_binding("c", "r", "aud", "chal")
        assert oauth._compute_binding("c", "r", "x", "chal") != base

    def test_sensitive_to_code_challenge(self):
        base = oauth._compute_binding("c", "r", "aud", "chal")
        assert oauth._compute_binding("c", "r", "aud", "x") != base


class TestBridgeApprovalCalls:
    """Low-level network-path coverage for the SSE->bridge phone-tap calls —
    exercises the REAL httpx code path via httpx.MockTransport (no real
    network), verifying fail-closed on connection error / non-2xx / bad
    JSON, and the outbound request shape (URL, Authorization header,
    body)."""

    def _patched(self, monkeypatch, handler):
        # oauth.httpx IS the httpx module object (not a copy) — capture the
        # real AsyncClient BEFORE patching, else the factory recurses into
        # itself the moment it's substituted for the name it's calling.
        real_async_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)

        def _factory(*args, **kwargs):
            return real_async_client(transport=transport)

        monkeypatch.setattr(oauth.httpx, "AsyncClient", _factory)
        return transport

    def test_request_success_returns_body_and_sends_bearer_token(self, monkeypatch):
        monkeypatch.setattr(oauth, "_BRIDGE_TOKEN", "tok123")
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"approval_id": "aid-1", "code": "amber-cove"})

        self._patched(monkeypatch, handler)
        result = asyncio.run(
            oauth._bridge_approval_request(
                summary="s", client_id="c", redirect_uri=CALLBACK, audience="aud", code="amber-cove"
            )
        )
        assert result == {"approval_id": "aid-1", "code": "amber-cove"}
        assert seen["auth"] == "Bearer tok123"
        assert seen["url"].endswith("/api/approval/request")
        assert seen["body"]["client_id"] == "c"
        assert seen["body"]["code"] == "amber-cove"
        # requester_ip defaults to "" (not omitted) when the caller doesn't
        # pass one — the bridge's per-IP caps always see the key present.
        assert seen["body"]["requester_ip"] == ""

    def test_request_forwards_requester_ip_in_body(self, monkeypatch):
        monkeypatch.setattr(oauth, "_BRIDGE_TOKEN", "tok123")
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"approval_id": "aid-1", "code": "amber-cove"})

        self._patched(monkeypatch, handler)
        asyncio.run(
            oauth._bridge_approval_request(
                summary="s",
                client_id="c",
                redirect_uri=CALLBACK,
                audience="aud",
                code="amber-cove",
                requester_ip="203.0.113.7",
            )
        )
        assert seen["body"]["requester_ip"] == "203.0.113.7"

    def test_request_connection_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(oauth, "_BRIDGE_TOKEN", "tok123")

        def handler(request):
            raise httpx.ConnectError("boom", request=request)

        self._patched(monkeypatch, handler)
        result = asyncio.run(
            oauth._bridge_approval_request(
                summary="s", client_id="c", redirect_uri=CALLBACK, audience="aud", code="x-y"
            )
        )
        assert result is None

    def test_status_non_2xx_returns_none(self, monkeypatch):
        monkeypatch.setattr(oauth, "_BRIDGE_TOKEN", "tok123")
        self._patched(monkeypatch, lambda request: httpx.Response(500, json={"error": "boom"}))
        assert asyncio.run(oauth._bridge_approval_status("aid-1")) is None

    def test_confirm_bad_json_body_returns_none(self, monkeypatch):
        monkeypatch.setattr(oauth, "_BRIDGE_TOKEN", "tok123")
        self._patched(monkeypatch, lambda request: httpx.Response(200, content=b"not json"))
        assert asyncio.run(oauth._bridge_approval_confirm("aid-1")) is None

    def test_status_uses_path_param(self, monkeypatch):
        monkeypatch.setattr(oauth, "_BRIDGE_TOKEN", "tok123")
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"status": "pending"})

        self._patched(monkeypatch, handler)
        asyncio.run(oauth._bridge_approval_status("aid-42"))
        assert seen["url"].endswith("/api/approval/status/aid-42")

    def test_confirm_sends_approval_id_body(self, monkeypatch):
        monkeypatch.setattr(oauth, "_BRIDGE_TOKEN", "tok123")
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"approved": True})

        self._patched(monkeypatch, handler)
        result = asyncio.run(oauth._bridge_approval_confirm("aid-7"))
        assert result == {"approved": True}
        assert seen["url"].endswith("/api/approval/confirm")
        assert seen["body"] == {"approval_id": "aid-7"}

    def test_no_bridge_token_never_touches_the_network(self, monkeypatch):
        monkeypatch.setattr(oauth, "_BRIDGE_TOKEN", "")

        def handler(request):
            raise AssertionError("must not reach the network without BRIDGE_TOKEN")

        self._patched(monkeypatch, handler)
        assert asyncio.run(oauth._bridge_approval_confirm("aid-1")) is None
        assert asyncio.run(oauth._bridge_approval_status("aid-1")) is None


# ── Token endpoint: authorization_code grant ──────────────────────────────────


class TestTokenAuthorizationCode:
    def test_happy_path_mints_bound_pair(self, oauth_env):
        verifier, challenge = _pkce_pair()
        code = _authorize_code(oauth_env, challenge)
        sent = _exchange(oauth_env, code, verifier)
        assert _status(sent) == 200
        body = _json(sent)
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == oauth.ACCESS_TOKEN_TTL_SECONDS
        assert body["refresh_token"].startswith("cbr_")
        record = oauth._load_record("access", body["access_token"])
        assert record["audience"] == oauth.CANONICAL_RESOURCE
        assert record["family_id"]
        refresh = oauth._load_record("refresh", body["refresh_token"])
        assert refresh["family_id"] == record["family_id"]
        assert refresh["status"] == "active"

    def test_code_single_use(self, oauth_env):
        verifier, challenge = _pkce_pair()
        code = _authorize_code(oauth_env, challenge)
        assert _status(_exchange(oauth_env, code, verifier)) == 200
        sent = _exchange(oauth_env, code, verifier)
        assert _status(sent) == 400
        assert _json(sent)["error"] == "invalid_grant"

    def test_wrong_verifier_refused_and_code_consumed(self, oauth_env):
        verifier, challenge = _pkce_pair()
        code = _authorize_code(oauth_env, challenge)
        sent = _exchange(oauth_env, code, "not-the-verifier")
        assert _status(sent) == 400
        assert "PKCE" in _json(sent)["error_description"]
        # single-use: even the right verifier cannot use the code now
        assert _status(_exchange(oauth_env, code, verifier)) == 400

    def test_expired_code_refused(self, oauth_env):
        verifier, challenge = _pkce_pair()
        code = _authorize_code(oauth_env, challenge)
        data = oauth._load_code(code)
        data["issued_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=oauth.CODE_TTL_SECONDS + 5)
        ).isoformat()
        oauth._save_code(code, data)
        sent = _exchange(oauth_env, code, verifier)
        assert _status(sent) == 400
        assert "expired" in _json(sent)["error_description"].lower()

    def test_client_id_mismatch_refused(self, oauth_env):
        verifier, challenge = _pkce_pair()
        code = _authorize_code(oauth_env, challenge)
        sent = _exchange("claude-other", code, verifier)
        assert _status(sent) == 400

    def test_redirect_mismatch_refused(self, oauth_env):
        verifier, challenge = _pkce_pair()
        code = _authorize_code(oauth_env, challenge)
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": "https://claude.com/api/mcp/auth_callback",
            "client_id": oauth_env,
        }
        sent = _run(oauth.handle_token, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 400

    def test_foreign_resource_at_token_refused(self, oauth_env):
        verifier, challenge = _pkce_pair()
        code = _authorize_code(oauth_env, challenge)
        sent = _exchange(oauth_env, code, verifier, resource="https://evil.example/mcp")
        assert _status(sent) == 400
        assert _json(sent)["error"] == "invalid_target"

    def test_challengeless_code_fails_closed(self, oauth_env):
        # A code that somehow lacks a stored challenge must never exchange.
        code = "forged-" + secrets.token_urlsafe(8)
        oauth._save_code(
            code,
            {
                "client_id": oauth_env,
                "redirect_uri": CALLBACK,
                "code_challenge": "",
                "code_challenge_method": "S256",
                "scope": "native",
                "audience": oauth.CANONICAL_RESOURCE,
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "substrate": oauth.SUBSTRATE,
            },
        )
        sent = _exchange(oauth_env, code, "any-verifier")
        assert _status(sent) == 400

    def test_unsupported_grant_type(self, oauth_env):
        sent = _run(
            oauth.handle_token,
            _scope(method="POST"),
            body=_form({"grant_type": "client_credentials"}),
        )
        assert _status(sent) == 400
        assert _json(sent)["error"] == "unsupported_grant_type"


# ── Token endpoint: refresh rotation + reuse detection ────────────────────────


class TestRefreshRotation:
    def _refresh(self, client_id, refresh_token, resource=""):
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if resource:
            form["resource"] = resource
        return _run(oauth.handle_token, _scope(method="POST"), body=_form(form))

    def test_rotation_mints_successor_and_retires_predecessor(self, oauth_env):
        grant = _full_grant(oauth_env)
        sent = self._refresh(oauth_env, grant["refresh_token"])
        assert _status(sent) == 200
        rotated = _json(sent)
        assert rotated["refresh_token"] != grant["refresh_token"]
        assert rotated["access_token"] != grant["access_token"]
        old = oauth._load_record("refresh", grant["refresh_token"])
        assert old["status"] == "rotated"
        new = oauth._load_record("refresh", rotated["refresh_token"])
        assert new["status"] == "active"
        assert new["family_id"] == old["family_id"]

    def test_reuse_of_rotated_token_revokes_family(self, oauth_env):
        grant = _full_grant(oauth_env)
        rotated = _json(self._refresh(oauth_env, grant["refresh_token"]))
        # replay the OLD refresh token
        sent = self._refresh(oauth_env, grant["refresh_token"])
        assert _status(sent) == 400
        assert "reuse" in _json(sent)["error_description"].lower()
        # the whole family is dead: successor access token deleted,
        # successor refresh tombstoned, resource gate refuses.
        assert oauth.load_valid_access(rotated["access_token"]) is None
        successor = oauth._load_record("refresh", rotated["refresh_token"])
        assert successor["status"] == "revoked"
        assert _status(self._refresh(oauth_env, rotated["refresh_token"])) == 400

    def test_expired_refresh_refused(self, oauth_env):
        grant = _full_grant(oauth_env)
        rec = oauth._load_record("refresh", grant["refresh_token"])
        rec["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        oauth._save_record("refresh", grant["refresh_token"], rec)
        assert _status(self._refresh(oauth_env, grant["refresh_token"])) == 400

    def test_client_mismatch_refused(self, oauth_env):
        grant = _full_grant(oauth_env)
        assert _status(self._refresh("claude-other", grant["refresh_token"])) == 400

    def test_foreign_resource_refused(self, oauth_env):
        grant = _full_grant(oauth_env)
        sent = self._refresh(oauth_env, grant["refresh_token"], resource="https://evil.example/x")
        assert _status(sent) == 400
        assert _json(sent)["error"] == "invalid_target"

    def test_unknown_refresh_refused(self, oauth_env):
        assert _status(self._refresh(oauth_env, "cbr_never-issued")) == 400


# ── Resource-gate enforcement (RFC 8707 at the route) ─────────────────────────


class TestLoadValidAccess:
    def test_valid_token_returns_record(self, oauth_env):
        grant = _full_grant(oauth_env)
        record = oauth.load_valid_access(grant["access_token"])
        assert record is not None
        assert record["client_id"] == oauth_env

    def test_expired_token_refused_and_cleaned(self, oauth_env):
        grant = _full_grant(oauth_env)
        token = grant["access_token"]
        rec = oauth._load_record("access", token)
        rec["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        oauth._save_record("access", token, rec)
        assert oauth.load_valid_access(token) is None
        assert not oauth._token_path("access", token).exists()

    def test_tampered_audience_refused(self, oauth_env):
        grant = _full_grant(oauth_env)
        token = grant["access_token"]
        rec = oauth._load_record("access", token)
        rec["audience"] = "https://evil.example/mcp"
        oauth._save_record("access", token, rec)
        assert oauth.load_valid_access(token) is None

    def test_foreign_substrate_refused(self, oauth_env):
        grant = _full_grant(oauth_env)
        token = grant["access_token"]
        rec = oauth._load_record("access", token)
        rec["substrate"] = "chatgpt-openai-bridge"
        oauth._save_record("access", token, rec)
        assert oauth.load_valid_access(token) is None

    def test_empty_and_unknown_tokens_refused(self, oauth_env):
        assert oauth.load_valid_access("") is None
        assert oauth.load_valid_access("never-issued") is None


# ── Revocation ────────────────────────────────────────────────────────────────


class TestRevocation:
    def _revoke(self, token, client_id=""):
        form = {"token": token}
        if client_id:
            form["client_id"] = client_id
        return _run(
            oauth.handle_revoke,
            _scope(method="POST", path="/claude/oauth/revoke"),
            body=_form(form),
        )

    def test_always_200_even_for_unknown_token(self, oauth_env):
        assert _status(self._revoke("never-issued")) == 200

    def test_revokes_whole_family(self, oauth_env):
        grant = _full_grant(oauth_env)
        assert _status(self._revoke(grant["access_token"], client_id=oauth_env)) == 200
        assert oauth.load_valid_access(grant["access_token"]) is None
        refresh = oauth._load_record("refresh", grant["refresh_token"])
        assert refresh["status"] == "revoked"

    def test_wrong_client_does_not_revoke(self, oauth_env):
        grant = _full_grant(oauth_env)
        assert _status(self._revoke(grant["access_token"], client_id="claude-other")) == 200
        assert oauth.load_valid_access(grant["access_token"]) is not None

    def test_empty_family_id_never_sweeps(self, oauth_env):
        grant = _full_grant(oauth_env)
        assert oauth.revoke_family("") == 0
        assert oauth.load_valid_access(grant["access_token"]) is not None


# ── Discovery metadata ────────────────────────────────────────────────────────


class TestDiscovery:
    def test_as_metadata_is_strict(self, oauth_env):
        sent = _run(oauth.handle_authorization_server_metadata, _scope())
        body = _json(sent)
        assert body["code_challenge_methods_supported"] == ["S256"]
        assert set(body["grant_types_supported"]) == {"authorization_code", "refresh_token"}
        assert body["revocation_endpoint"].endswith("/oauth/revoke")
        assert body["issuer"] == oauth.BRIDGE_ISSUER

    def test_pr_metadata_names_canonical_resource(self, oauth_env):
        sent = _run(oauth.handle_protected_resource_metadata, _scope())
        body = _json(sent)
        assert body["resource"] == oauth.CANONICAL_RESOURCE
        assert body["authorization_servers"] == [oauth.BRIDGE_ISSUER]


# ── PKCE primitive ────────────────────────────────────────────────────────────


class TestVerifyPkce:
    def test_s256_roundtrip(self):
        verifier, challenge = _pkce_pair()
        assert oauth._verify_pkce_s256(verifier, challenge)

    def test_wrong_verifier_fails(self):
        _, challenge = _pkce_pair()
        assert not oauth._verify_pkce_s256("wrong", challenge)

    def test_empty_inputs_fail(self):
        assert not oauth._verify_pkce_s256("", "")
        assert not oauth._verify_pkce_s256("x", "")
