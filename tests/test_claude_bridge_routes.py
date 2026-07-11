"""Tests for the Claude-connector routes wired into SovereignAsgiMiddleware
(feat/claude-connector).

House style follows tests/test_sse_gate.py: raw ASGI scope dicts through the
module-level `app`, captured send lists, no network, no TestClient. The inner
Starlette app answers /claude/info without lifespan startup; the /claude/mcp
gate (handle_claude_mcp) refuses unauthenticated requests BEFORE the session
manager — so none of these tests need the lifespan-started session manager.
"""

import asyncio
import json

import pytest
from claude_bridge import oauth as claude_oauth
from openai_bridge import oauth as openai_oauth

from sovereign_stack import sse_server

AS_META = "/claude/.well-known/oauth-authorization-server"
OPENID_META = "/claude/.well-known/openid-configuration"
PR_META = "/claude/.well-known/oauth-protected-resource"

# Root path-insertion forms (RFC 8414 / RFC 9728 shapes MCP clients probe;
# answering all of them 200 is the #4030 retry-loop hardening).
ROOT_FORMS = (
    "/.well-known/oauth-authorization-server/claude",
    "/.well-known/openid-configuration/claude",
    "/.well-known/oauth-protected-resource/claude/mcp",
    "/.well-known/oauth-protected-resource/claude",
)


def _scope(path, method="GET", headers=None, query=b""):
    return {
        "type": "http",
        "path": path,
        "method": method,
        "headers": headers or [],
        "query_string": query,
        "client": ("127.0.0.1", 12345),
    }


def _call(scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(sse_server.app(scope, receive, send))
    return sent


def _status(sent):
    return next(m for m in sent if m["type"] == "http.response.start")["status"]


def _body_json(sent):
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return json.loads(body)


@pytest.fixture
def hermetic_oauth_storage(monkeypatch, tmp_path):
    """Point the claude_bridge.oauth storage Paths at tmp_path so the mcp
    gate can never read (or lazily delete) real ~/.sovereign token files."""
    for name in ("_CODES_DIR", "_TOKENS_DIR", "_REFRESH_DIR"):
        d = tmp_path / name.strip("_").lower()
        d.mkdir()
        monkeypatch.setattr(claude_oauth, name, d)
    monkeypatch.setattr(claude_oauth, "_CLIENTS_FILE", tmp_path / "oauth_clients.json")


def test_claude_bridge_loaded():
    """Precondition for everything below: the bridge imported cleanly."""
    assert sse_server._CLAUDE_BRIDGE_ENABLED is True


class TestDiscoveryEndpoints:
    def test_authorization_server_metadata(self):
        sent = _call(_scope(AS_META))
        assert _status(sent) == 200
        body = _body_json(sent)
        # Spec item: PKCE S256 mandatory — 'plain' must NOT be advertised.
        assert body["code_challenge_methods_supported"] == ["S256"]
        assert "refresh_token" in body["grant_types_supported"]
        assert "revocation_endpoint" in body

    def test_openid_configuration_is_alias(self):
        as_body = _body_json(_call(_scope(AS_META)))
        sent = _call(_scope(OPENID_META))
        assert _status(sent) == 200
        oid_body = _body_json(sent)
        assert oid_body["issuer"] == as_body["issuer"]
        # Alias means the same truth, not a lookalike.
        assert oid_body == as_body

    def test_protected_resource_metadata(self):
        sent = _call(_scope(PR_META))
        assert _status(sent) == 200
        body = _body_json(sent)
        assert body["resource"].endswith("/claude/mcp")

    @pytest.mark.parametrize("path", ROOT_FORMS)
    def test_root_path_insertion_forms_answer_200(self, path):
        sent = _call(_scope(path))
        assert _status(sent) == 200


class TestMcpGate:
    """Unauthenticated /claude/mcp is refused by the gate inside
    handle_claude_mcp — deliberately NOT monkeypatched, so a 401 here proves
    the middleware actually routes these methods to the gate."""

    @pytest.mark.parametrize("method", ["POST", "GET", "DELETE"])
    def test_no_auth_gets_401(self, method, hermetic_oauth_storage):
        sent = _call(_scope("/claude/mcp", method=method))
        assert _status(sent) == 401

    def test_put_falls_through_to_inner_404(self, hermetic_oauth_storage):
        # PUT is not in the middleware branch → inner Starlette has no
        # /claude/mcp route → 404 (not a 401 from the gate).
        sent = _call(_scope("/claude/mcp", method="PUT"))
        assert _status(sent) == 404


class TestClaudeRateLimit:
    """Public requests (CF-Connecting-IP present) hit the strict OAuth
    bucket; local requests carry no header and are exempt. The constants are
    read at call time from module globals inside _claude_rate_ok, so
    monkeypatching sse_server._CLAUDE_OAUTH_* takes effect immediately."""

    IP = b"203.0.113.9"

    @pytest.fixture(autouse=True)
    def tight_bucket(self, monkeypatch):
        monkeypatch.setattr(sse_server, "_CLAUDE_OAUTH_BURST", 2.0)
        monkeypatch.setattr(sse_server, "_CLAUDE_OAUTH_REFILL_PER_SEC", 0.0)
        sse_server._claude_oauth_buckets.clear()
        yield
        sse_server._claude_oauth_buckets.clear()

    def test_third_public_request_gets_429(self):
        headers = [(b"cf-connecting-ip", self.IP)]
        statuses = [_status(_call(_scope(AS_META, headers=headers))) for _ in range(3)]
        assert statuses == [200, 200, 429]

    def test_local_requests_exempt(self):
        # Exhaust the public bucket first, then confirm a header-less
        # (local/tunnel-internal) request is never throttled.
        headers = [(b"cf-connecting-ip", self.IP)]
        for _ in range(3):
            _call(_scope(AS_META, headers=headers))
        sent = _call(_scope(AS_META))
        assert _status(sent) == 200


class TestInfoRoute:
    def test_claude_info_manifest(self):
        sent = _call(_scope("/claude/info"))
        assert _status(sent) == 200
        body = _body_json(sent)
        assert body["bridge"] == "claude-ai-bridge"


class TestOtherBridgesUnaffected:
    """The Claude hardening (S256-only, TTL'd tokens) must not have leaked
    into the openai/grok bridges."""

    def test_openai_as_metadata_still_advertises_plain(self):
        sent = _call(_scope("/openai/.well-known/oauth-authorization-server"))
        assert _status(sent) == 200
        body = _body_json(sent)
        assert body["code_challenge_methods_supported"] == ["S256", "plain"]

    def test_grok_as_metadata_still_200(self):
        sent = _call(_scope("/grok/.well-known/oauth-authorization-server"))
        assert _status(sent) == 200

    def test_openai_token_ttl_untouched(self):
        assert openai_oauth.TOKEN_TTL_SECONDS == 0
