"""Tests for the Claude connector's /claude/mcp gate (feat/claude-connector).

Covers clients/claude_bridge/mcp_native.py: the ASGI auth gate in front of
the Streamable HTTP session manager (RFC 8707 audience binding, lazy expiry
cleanup, RFC 9728 discovery pointer on 401) and the destructive-tier gate
inside claude_call_tool (base delegation, step-up refusal shape, audit hook,
fail-closed no-grant and unknown-tool paths).

Hermetic: all module-level storage Paths are monkeypatched into tmp_path;
the session manager and the native handlers are stubbed — no network.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp.types import TextContent

_CLIENTS = Path(__file__).parent.parent / "clients"
if _CLIENTS.exists() and str(_CLIENTS) not in sys.path:
    sys.path.insert(0, str(_CLIENTS))

from claude_bridge import elevation, mcp_native, oauth  # noqa: E402
from claude_bridge.elevation import ElevationStatus  # noqa: E402

FAMILY = "fam_test_0001"
CLIENT = "client_abc"
GRANT = {"family_id": FAMILY, "client_id": CLIENT, "scope": "native"}


@pytest.fixture(autouse=True)
def hermetic_storage(monkeypatch, tmp_path):
    """Redirect every module-level storage Path into tmp_path."""
    elev_dir = tmp_path / "elevations"
    audit_dir = tmp_path / "audit"
    tokens_dir = tmp_path / "oauth" / "tokens"
    refresh_dir = tmp_path / "oauth" / "refresh"
    codes_dir = tmp_path / "oauth" / "codes"
    for d in (elev_dir, audit_dir, tokens_dir, refresh_dir, codes_dir):
        d.mkdir(parents=True)
    monkeypatch.setattr(elevation, "_ELEV_DIR", elev_dir)
    monkeypatch.setattr(elevation, "_AUDIT_DIR", audit_dir)
    monkeypatch.setattr(elevation, "_AUDIT_LOG", audit_dir / "destructive_calls.jsonl")
    monkeypatch.setattr(oauth, "_TOKENS_DIR", tokens_dir)
    monkeypatch.setattr(oauth, "_REFRESH_DIR", refresh_dir)
    monkeypatch.setattr(oauth, "_CODES_DIR", codes_dir)
    monkeypatch.setattr(oauth, "_CLIENTS_FILE", tmp_path / "oauth_clients.json")
    return tmp_path


def _scope(headers=None):
    return {
        "type": "http",
        "path": "/claude/mcp",
        "method": "POST",
        "headers": headers or [],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
    }


def _mint_access(family_id=FAMILY, client_id=CLIENT):
    pair = oauth._mint_token_pair(client_id, "native", oauth.CANONICAL_RESOURCE, family_id)
    return pair["access_token"]


def _call_gate(scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(mcp_native.handle_claude_mcp(scope, receive, send))
    return sent


def _start(sent):
    return next(m for m in sent if m["type"] == "http.response.start")


class TestAuthGate:
    def test_no_authorization_header_gets_401_with_discovery_pointer(self):
        sent = _call_gate(_scope())
        start = _start(sent)
        assert start["status"] == 401
        headers = {k.lower(): v for k, v in start["headers"]}
        www = headers[b"www-authenticate"].decode()
        assert "resource_metadata" in www
        assert "/.well-known/oauth-protected-resource/claude/mcp" in www

    def test_garbage_bearer_gets_401(self):
        scope = _scope(headers=[(b"authorization", b"Bearer not-a-real-token")])
        start = _start(_call_gate(scope))
        assert start["status"] == 401

    def test_valid_token_binds_grant_for_the_request_only(self, monkeypatch):
        token = _mint_access()
        seen = {}

        async def stub_handle_request(scope, receive, send):
            seen["grant"] = mcp_native._current_grant.get()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        monkeypatch.setattr(mcp_native.session_manager, "handle_request", stub_handle_request)
        scope = _scope(headers=[(b"authorization", f"Bearer {token}".encode())])
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        async def run():
            await mcp_native.handle_claude_mcp(scope, receive, send)
            return mcp_native._current_grant.get()

        grant_after = asyncio.run(run())

        assert _start(sent)["status"] == 200
        assert seen["grant"]["family_id"] == FAMILY
        assert seen["grant"]["client_id"] == CLIENT
        assert grant_after is None  # reset once the request is done

    def test_tampered_audience_gets_401(self):
        token = _mint_access()
        path = oauth._token_path("access", token)
        data = json.loads(path.read_text())
        data["audience"] = "https://evil.example/mcp"
        path.write_text(json.dumps(data))
        scope = _scope(headers=[(b"authorization", f"Bearer {token}".encode())])
        start = _start(_call_gate(scope))
        assert start["status"] == 401

    def test_expired_token_gets_401_and_is_deleted(self):
        token = _mint_access()
        path = oauth._token_path("access", token)
        data = json.loads(path.read_text())
        data["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        path.write_text(json.dumps(data))
        scope = _scope(headers=[(b"authorization", f"Bearer {token}".encode())])
        start = _start(_call_gate(scope))
        assert start["status"] == 401
        assert not path.exists()  # lazy cleanup


@pytest.fixture
def native_calls(monkeypatch):
    """Stub only the native EXECUTION delegation; keep the REAL registry for
    list_tools so `_known_tool` validates against the live tool surface (an
    in-process call, no network). This exercises the fabricated-name rejection
    for the right reason — the name is genuinely absent from the registry, not
    because the registry was stubbed empty. Returns the handle_tool recorder."""
    from sovereign_stack.server import list_tools as real_list_tools

    calls = []

    async def fake_handle_tool(name, arguments):
        calls.append((name, arguments))
        return [TextContent(type="text", text="native-ok")]

    monkeypatch.setattr(mcp_native, "_native", lambda: (real_list_tools, fake_handle_tool))
    # Reset the module-global registry cache so it can't carry a stale (or
    # stubbed-empty) frozenset in from another test; monkeypatch restores it.
    monkeypatch.setattr(mcp_native, "_registry_names", None)
    return calls


def _call_tool(name, arguments, grant):
    async def run():
        ctx_token = None
        if grant is not None:
            ctx_token = mcp_native._current_grant.set(grant)
        try:
            return await mcp_native.claude_call_tool(name, arguments)
        finally:
            if ctx_token is not None:
                mcp_native._current_grant.reset(ctx_token)

    return asyncio.run(run())


class TestTierGate:
    def test_base_tool_delegates_to_native(self, native_calls):
        result = _call_tool("recall_insights", {"query": "spiral"}, GRANT)
        assert native_calls == [("recall_insights", {"query": "spiral"})]
        assert result[0].text == "native-ok"

    def test_destructive_tool_pending_returns_step_up_refusal(self, native_calls, monkeypatch):
        async def fake_ensure(tool, family_id, client_id, req_hash, summary=""):
            return ElevationStatus("pending", "waiting", code="oak-river")

        monkeypatch.setattr(mcp_native.elevation, "ensure_elevation", fake_ensure)
        result = _call_tool("set_policy", {}, GRANT)
        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["error"] == "step_up_required"
        assert payload["pairing_code"] == "oak-river"
        assert native_calls == []

    def test_step_up_refusal_carries_consume_reason_for_wdilo(self, native_calls, monkeypatch):
        # where_did_i_leave_off taps because it CONSUMES the handoffs addressed
        # to whoever boots next at HQ — the refusal 'detail' must SAY so (so a
        # remote seat, and the human it relays to, sees the tap is protecting
        # something specific), while keeping the refusal shape intact.
        async def fake_ensure(tool, family_id, client_id, req_hash, summary=""):
            return ElevationStatus("pending", "Pairing code: 'oak-river'.", code="oak-river")

        monkeypatch.setattr(mcp_native.elevation, "ensure_elevation", fake_ensure)
        result = _call_tool("where_did_i_leave_off", {}, GRANT)
        payload = json.loads(result[0].text)
        # Shape intact.
        assert set(payload) == {"error", "tool", "state", "pairing_code", "detail"}
        assert payload["error"] == "step_up_required"
        assert payload["pairing_code"] == "oak-river"
        # The reason is folded into detail, and the generic Door text survives.
        assert "consume" in payload["detail"].lower()
        assert "handoff" in payload["detail"].lower()
        assert "Pairing code: 'oak-river'." in payload["detail"]
        assert native_calls == []

    def test_step_up_refusal_has_no_reason_for_generic_destructive(self, native_calls, monkeypatch):
        # A destructive tool with no registered reason (set_policy) keeps the
        # bare Door detail — the reason folding is per-tool, not global.
        async def fake_ensure(tool, family_id, client_id, req_hash, summary=""):
            return ElevationStatus("pending", "waiting", code="oak-river")

        monkeypatch.setattr(mcp_native.elevation, "ensure_elevation", fake_ensure)
        payload = json.loads(_call_tool("set_policy", {}, GRANT)[0].text)
        assert payload["detail"] == "waiting"

    def test_destructive_tool_active_executes_and_audits(self, native_calls, monkeypatch):
        seen = {}

        async def fake_ensure(tool, family_id, client_id, req_hash, summary=""):
            # Capture what the gate computed so we can pin the binding contract.
            seen["ensure_req_hash"] = req_hash
            seen["summary"] = summary
            return ElevationStatus("active", "ok")

        recorded = []
        monkeypatch.setattr(mcp_native.elevation, "ensure_elevation", fake_ensure)
        monkeypatch.setattr(
            mcp_native.elevation,
            "record_destructive_execution",
            lambda **kw: recorded.append(kw),
        )
        result = _call_tool("set_policy", {"policy": "x"}, GRANT)
        assert native_calls == [("set_policy", {"policy": "x"})]
        assert result[0].text == "native-ok"
        assert len(recorded) == 1
        # The SAME computed argument-hash flows to both ensure_elevation and the
        # single-use consume — that binding is the point of summarize_and_hash.
        assert recorded[0]["req_hash"] == seen["ensure_req_hash"]
        # And it is the real hash of these exact arguments.
        _, expected_hash = elevation.summarize_and_hash("set_policy", {"policy": "x"})
        assert seen["ensure_req_hash"] == expected_hash

    def test_no_grant_bound_fails_closed(self, native_calls):
        result = _call_tool("recall_insights", {}, None)
        payload = json.loads(result[0].text)
        assert payload["error"] == "no_grant_bound"
        assert native_calls == []

    def test_unknown_tool_returns_method_not_found(self, native_calls, monkeypatch):
        # A fabricated tool name is rejected against the live registry BEFORE
        # tier classification, so it never reaches the Door / step-up path.
        # (Previously the fail-closed default treated it as destructive; now
        # the name must be a real registered tool to get that far.)
        seen = []

        async def fake_ensure(tool, family_id, client_id, req_hash, summary=""):
            seen.append(tool)
            return ElevationStatus("pending", "waiting", code="oak-river")

        monkeypatch.setattr(mcp_native.elevation, "ensure_elevation", fake_ensure)
        result = _call_tool("tool_added_next_release", {}, GRANT)
        payload = json.loads(result[0].text)
        assert payload["error"] == "method_not_found"
        assert seen == []  # never reached tier classification / the Door
        assert native_calls == []  # never delegated to the native handler
