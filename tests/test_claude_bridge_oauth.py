"""
Claude-bridge OAuth 2.1 tests — the security core of the connector.

Covers the four hardening deltas over the openai template, each of which is a
ratified spec item: mandatory PKCE S256 (both ends), RFC 8707 audience binding
(authorize, token, and resource-gate enforcement), refresh rotation with
reuse-detection family revocation, and pinned redirect URIs.

House style per tests/test_sse_gate.py: raw ASGI scopes, asyncio.run with
captured send lists, module-constant monkeypatching, no network, no TestClient.
"""

import asyncio
import base64
import hashlib
import json
import secrets

# clients/ is placed on sys.path by sovereign_stack.sse_server at import; do it
# directly here so this file does not depend on import order.
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_CLIENTS = Path(__file__).parent.parent / "clients"
if str(_CLIENTS) not in sys.path:
    sys.path.insert(0, str(_CLIENTS))

from claude_bridge import oauth  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────


def _scope(method="GET", path="/claude/oauth/authorize", query="", headers=None):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": headers or [],
    }


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


@pytest.fixture
def oauth_env(tmp_path, monkeypatch):
    """Hermetic storage + a registered client. Returns the client_id."""
    codes = tmp_path / "codes"
    tokens = tmp_path / "tokens"
    refresh = tmp_path / "refresh"
    for d in (codes, tokens, refresh):
        d.mkdir()
    monkeypatch.setattr(oauth, "_CODES_DIR", codes)
    monkeypatch.setattr(oauth, "_TOKENS_DIR", tokens)
    monkeypatch.setattr(oauth, "_REFRESH_DIR", refresh)
    monkeypatch.setattr(oauth, "_CLIENTS_FILE", tmp_path / "oauth_clients.json")
    client_id = "claude-test-client"
    oauth._save_clients(
        {
            client_id: {
                "client_id": client_id,
                "redirect_uris": [CALLBACK],
                "grant_types": ["authorization_code", "refresh_token"],
            }
        }
    )
    return client_id


def _authorize_code(client_id: str, challenge: str, resource: str = "", scope_str: str = "") -> str:
    """Run the approve POST and return the minted code from the redirect."""
    form = {
        "action": "approve",
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st4te",
        "scope": scope_str,
    }
    if resource:
        form["resource"] = resource
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

    def test_loopback_allowed(self):
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

    def test_registry_cap_refuses_new_clients(self, oauth_env, monkeypatch):
        monkeypatch.setattr(oauth, "MAX_REGISTERED_CLIENTS", 1)  # fixture registered one
        sent = _run(
            oauth.handle_register,
            _scope(method="POST", path="/claude/oauth/register"),
            body=json.dumps({"redirect_uris": [CALLBACK]}).encode(),
        )
        assert _status(sent) == 429
        assert _json(sent)["error"] == "registration_limit_reached"


# ── Authorize: PKCE S256 mandatory + RFC 8707 ─────────────────────────────────


class TestAuthorizeGet:
    def _get(self, client_id, challenge="", method="S256", resource=""):
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
        return _run(oauth.handle_authorize, _scope(query=urllib.parse.urlencode(params)))

    def test_missing_challenge_refused(self, oauth_env):
        sent = self._get(oauth_env)
        assert _status(sent) == 400
        assert b"code_challenge required" in _body(sent)

    def test_plain_method_refused(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = self._get(oauth_env, challenge=challenge, method="plain")
        assert _status(sent) == 400
        assert b"S256" in _body(sent)

    def test_s256_renders_consent(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = self._get(oauth_env, challenge=challenge)
        assert _status(sent) == 200
        assert b"Approve Claude connector access" in _body(sent)

    def test_foreign_resource_refused(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = self._get(oauth_env, challenge=challenge, resource="https://evil.example/mcp")
        assert _status(sent) == 400
        assert b"invalid_target" in _body(sent)

    def test_canonical_resource_accepted(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = self._get(oauth_env, challenge=challenge, resource=oauth.CANONICAL_RESOURCE)
        assert _status(sent) == 200

    def test_unknown_client_refused(self, oauth_env):
        _, challenge = _pkce_pair()
        sent = self._get("claude-nobody", challenge=challenge)
        assert _status(sent) == 400


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

    def test_post_revalidates_pkce(self, oauth_env):
        form = {
            "action": "approve",
            "client_id": oauth_env,
            "redirect_uri": CALLBACK,
            "code_challenge": "",
            "code_challenge_method": "S256",
        }
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 400

    def test_deny_redirects_access_denied(self, oauth_env):
        form = {"action": "deny", "client_id": oauth_env, "redirect_uri": CALLBACK, "state": "s"}
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 302
        assert b"access_denied" in _headers(sent)[b"location"]

    def test_deny_never_redirects_to_unpinned_target(self, oauth_env):
        form = {"action": "deny", "client_id": oauth_env, "redirect_uri": "https://evil.example/cb"}
        sent = _run(oauth.handle_authorize, _scope(method="POST"), body=_form(form))
        assert _status(sent) == 400


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
