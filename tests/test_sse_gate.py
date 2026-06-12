"""Tests for the native /sse auth gate (feat/sse-native-gate).

The gate closes the unauthenticated public surface found in the 2026-06-12
audit: GET /sse (and therefore every tool, including writes) was reachable
through the Cloudflare tunnel with no credential. /messages stays
capability-gated by the mcp transport's session_id check, so the connect-time
gate covers the session.
"""

import pytest

from sovereign_stack import sse_server

TOKEN = "test-token-0123456789abcdef0123456789abcdef"


def _scope(headers=None, query=b"", path="/sse", method="GET"):
    return {
        "type": "http",
        "path": path,
        "method": method,
        "headers": headers or [],
        "query_string": query,
        "client": ("127.0.0.1", 12345),
    }


@pytest.fixture
def token_env(monkeypatch):
    monkeypatch.setenv("BRIDGE_TOKEN", TOKEN)
    monkeypatch.delenv("SSE_ALLOW_UNAUTHENTICATED", raising=False)


class TestNativeAuthOk:
    def test_valid_bearer_header(self, token_env):
        scope = _scope(headers=[(b"authorization", f"Bearer {TOKEN}".encode())])
        assert sse_server._native_auth_ok(scope) is True

    def test_valid_query_param(self, token_env):
        scope = _scope(query=f"token={TOKEN}".encode())
        assert sse_server._native_auth_ok(scope) is True

    def test_wrong_bearer_rejected(self, token_env):
        scope = _scope(headers=[(b"authorization", b"Bearer wrong-token")])
        assert sse_server._native_auth_ok(scope) is False

    def test_wrong_query_param_rejected(self, token_env):
        scope = _scope(query=b"token=wrong-token")
        assert sse_server._native_auth_ok(scope) is False

    def test_no_credential_rejected(self, token_env):
        assert sse_server._native_auth_ok(_scope()) is False

    def test_empty_query_token_rejected(self, token_env):
        assert sse_server._native_auth_ok(_scope(query=b"token=")) is False

    def test_non_bearer_scheme_rejected(self, token_env):
        scope = _scope(headers=[(b"authorization", f"Basic {TOKEN}".encode())])
        assert sse_server._native_auth_ok(scope) is False

    def test_header_wins_over_query(self, token_env):
        # A valid header is accepted regardless of query noise.
        scope = _scope(
            headers=[(b"authorization", f"Bearer {TOKEN}".encode())],
            query=b"token=wrong",
        )
        assert sse_server._native_auth_ok(scope) is True

    def test_fail_closed_when_token_unset(self, monkeypatch):
        monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
        monkeypatch.delenv("SSE_ALLOW_UNAUTHENTICATED", raising=False)
        scope = _scope(headers=[(b"authorization", b"Bearer anything")])
        assert sse_server._native_auth_ok(scope) is False

    def test_explicit_unauthenticated_opt_in(self, monkeypatch):
        monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
        monkeypatch.setenv("SSE_ALLOW_UNAUTHENTICATED", "true")
        assert sse_server._native_auth_ok(_scope()) is True

    def test_opt_in_requires_exact_true(self, monkeypatch):
        monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
        monkeypatch.setenv("SSE_ALLOW_UNAUTHENTICATED", "yes")
        assert sse_server._native_auth_ok(_scope()) is False


class TestBridgeAuthOk:
    def test_valid_bearer(self, token_env):
        scope = _scope(headers=[(b"authorization", f"Bearer {TOKEN}".encode())], path="/openai/sse")
        assert sse_server._bridge_auth_ok(scope) is True

    def test_query_param_not_accepted_on_openai_path(self, token_env):
        # The /openai/* surface is header-only by design (OAuth shim handles the rest).
        scope = _scope(query=f"token={TOKEN}".encode(), path="/openai/sse")
        assert sse_server._bridge_auth_ok(scope) is False

    def test_fail_closed_when_token_unset(self, monkeypatch):
        monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
        monkeypatch.delenv("SSE_ALLOW_UNAUTHENTICATED", raising=False)
        assert sse_server._bridge_auth_ok(_scope(path="/openai/sse")) is False


class TestMiddlewareGate:
    """ASGI-level: unauthenticated GET /sse gets 401; /health stays open."""

    @staticmethod
    def _call(scope):
        import asyncio

        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(sse_server.app(scope, receive, send))
        return sent

    def test_unauthenticated_sse_gets_401(self, token_env):
        sent = self._call(_scope())
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 401

    def test_wrong_token_sse_gets_401(self, token_env):
        sent = self._call(_scope(query=b"token=wrong"))
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 401

    def test_health_stays_open(self, token_env):
        sent = self._call(_scope(path="/health"))
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 200
