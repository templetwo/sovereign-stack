"""Tests for the navigational scribe layer.

Covers per the design spec (scribe_resident_design.md):
  (1) Tool-dispatch mechanism (Lesson #582 core): FakeAnthropicClient with
      stop_reason=tool_use + tool_use block → dispatch_tool spy → count==1.
      Inverse: prose-only → count==0.
  (2) Greeting: dispatches 0 tools AND output contains no tool-call XML.
  (5) Fork-D payload contract: ask_scribe returns parseable JSON with required keys.
  (6) Boot-launch Gap-3: Starlette _lifespan fires through SovereignAsgiMiddleware.
  (7) Back-compat: format_scribe_block still produces + injects the SCRIBE block.

Isolation strategy:
  - Patch module-level constants directly (bound at import).
  - Stub build_scribe_chronicle_context to avoid real chronicle.
  - Inject fake Anthropic client into bridge_integration._client_cache to prevent
    live API calls.
  - Fresh ScribeSessionStore per test; reset resident singletons.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sovereign_stack.scribe import bridge_integration as bi_mod
from sovereign_stack.scribe import resident as resident_mod
from sovereign_stack.scribe.bridge_integration import (
    format_scribe_block,
)
from sovereign_stack.scribe.haiku_client import HaikuClient
from sovereign_stack.scribe.session import ScribeSession, ScribeSessionStore

# ---------------------------------------------------------------------------
# Fake Anthropic client helpers
# ---------------------------------------------------------------------------


def _fake_usage(input_tokens=10, output_tokens=5):
    u = SimpleNamespace()
    u.input_tokens = input_tokens
    u.output_tokens = output_tokens
    u.cache_creation_input_tokens = 0
    u.cache_read_input_tokens = 0
    return u


def _fake_text_block(text: str):
    b = SimpleNamespace()
    b.type = "text"
    b.text = text
    return b


def _fake_tool_use_block(
    tool_name: str = "chronicle_recall", tool_id: str = "tu_001", tool_input: dict | None = None
):
    b = SimpleNamespace()
    b.type = "tool_use"
    b.name = tool_name
    b.id = tool_id
    b.input = tool_input or {"query": "open threads"}
    return b


def _fake_response(content, stop_reason: str, model: str = "claude-sonnet-4-6"):
    r = SimpleNamespace()
    r.content = content
    r.stop_reason = stop_reason
    r.model = model
    r.usage = _fake_usage()
    return r


class _FakeStream:
    """Context manager mirroring anthropic .messages.stream()."""

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._response


class FakeAnthropicMessages:
    """Scripted responses for anthropic.Anthropic().messages.create()/.stream()."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._idx = 0
        self.call_args_list: list = []

    def _next(self):
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        # Default: return empty text if exhausted
        return _fake_response([_fake_text_block("")], "end_turn")

    def create(self, **kwargs):
        self.call_args_list.append(kwargs)
        return self._next()

    def stream(self, **kwargs):
        # Production code streams (a high max_tokens trips the SDK's
        # non-streaming 10-min guard). Mirror create(): record the call to the
        # same call_args_list so kwargs assertions hold regardless of which
        # path the code takes, and yield the next scripted response.
        self.call_args_list.append(kwargs)
        return _FakeStream(self._next())


class FakeAnthropic:
    """Drop-in for anthropic.Anthropic(api_key=...)."""

    def __init__(self, responses: list, api_key: str = "fake-key"):
        self.messages = FakeAnthropicMessages(responses)


def _make_fake_map(tmp_path: Path, extra_routes: str = "") -> Path:
    stack_map_dir = tmp_path / "stack_map"
    stack_map_dir.mkdir(parents=True, exist_ok=True)
    routes_md = stack_map_dir / "primary_routes.md"
    routes_md.write_text(
        "# Primary Routes\n\n"
        "## family-one\n\n"
        "- my_toolkit (tool): returns live toolkit\n"
        "- recall_insights (tool): query chronicle\n"
        "- where_did_i_leave_off (tool): boot ritual\n"
        "- chronicle_recall (tool): search insights\n" + extra_routes,
        encoding="utf-8",
    )
    return routes_md


@pytest.fixture()
def isolated_nav(tmp_path, monkeypatch):
    """Isolation fixture for navigational tests."""
    fake_root = tmp_path / ".sovereign"
    fake_root.mkdir(parents=True)
    map_file = _make_fake_map(fake_root)

    # Patch module-level constants on both modules that read them
    monkeypatch.setattr(resident_mod, "SOVEREIGN_ROOT", fake_root)
    monkeypatch.setattr(resident_mod, "STACK_MAP_PATH", map_file)
    state_dir = fake_root / "scribe_threads" / "_resident"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "state.json"
    monkeypatch.setattr(resident_mod, "RESIDENT_STATE_PATH", state_path)
    monkeypatch.setattr(bi_mod, "SOVEREIGN_ROOT", fake_root)

    # Stub context builder
    monkeypatch.setattr(
        "sovereign_stack.scribe.context_builder.build_scribe_chronicle_context",
        MagicMock(return_value="=== stubbed chronicle context ==="),
    )

    # Fresh store
    fresh_store = ScribeSessionStore(archive_root=tmp_path / "archive")
    monkeypatch.setattr(bi_mod, "_scribe_store", fresh_store)

    # Reset resident singletons
    resident_mod._reset_resident_for_tests()

    yield {
        "root": fake_root,
        "map_file": map_file,
        "state_path": state_path,
        "store": fresh_store,
    }

    # Teardown
    resident_mod._reset_resident_for_tests()
    monkeypatch.setattr(bi_mod, "_scribe_store", ScribeSessionStore())
    monkeypatch.setattr(bi_mod, "_client_cache", None)
    monkeypatch.setattr(bi_mod, "_client_error", None)


# ---------------------------------------------------------------------------
# (1) Tool-dispatch mechanism — Lesson #582 core
# ---------------------------------------------------------------------------


class TestToolDispatchMechanism:
    """Assert the dispatch COUNT, never infer from answer text."""

    def _make_client_with_tool_response(self, tool_answer: str = "{}") -> tuple:
        """Script: response#1 → stop_reason=tool_use + tool_use block;
        response#2 → stop_reason=end_turn + text block.
        Returns (HaikuClient, FakeAnthropic)."""
        response1 = _fake_response(
            content=[
                _fake_tool_use_block("chronicle_recall", "tu_001", {"query": "open threads"}),
            ],
            stop_reason="tool_use",
        )
        response2 = _fake_response(
            content=[
                _fake_text_block(
                    '{"synthesis":"found it","routes":[],"entries":[],'
                    '"suggested_calls":[],"gaps":[],"meta":{}}'
                )
            ],
            stop_reason="end_turn",
        )
        fake_anthropic = FakeAnthropic([response1, response2])
        client = HaikuClient.__new__(HaikuClient)
        client._client = fake_anthropic
        client.model = "claude-sonnet-4-6"
        system_prompt_path = (
            Path(__file__).parent.parent / "src/sovereign_stack/scribe/prompts/system.md"
        )
        client.system_prompt = system_prompt_path.read_text()
        return client, fake_anthropic

    def test_tool_use_dispatch_count_equals_one(self, isolated_nav, monkeypatch):
        """A tool_use response → dispatch_tool called exactly once; tool_calls_made len==1."""
        client, fake_anthropic = self._make_client_with_tool_response()

        dispatch_spy = MagicMock(return_value=('{"domains":[],"count":0}', False))
        monkeypatch.setattr(bi_mod, "_client_cache", client)
        monkeypatch.setattr(bi_mod, "dispatch_tool", dispatch_spy)

        result_json = bi_mod.ask_scribe(session_id=None, message="what are the open threads?")

        # Dispatch must have been called exactly once
        assert dispatch_spy.call_count == 1, (
            f"Expected dispatch_tool call_count==1, got {dispatch_spy.call_count}"
        )

        # Parse the Fork-D payload
        payload = json.loads(result_json)
        meta = payload["meta"]
        assert meta["tools_fired"] == 1, f"meta['tools_fired'] must be 1, got {meta['tools_fired']}"
        assert len(meta["tool_calls"]) == 1, (
            f"meta['tool_calls'] must have 1 entry, got {len(meta['tool_calls'])}"
        )

    def test_prose_only_run_dispatches_zero_tools(self, isolated_nav, monkeypatch):
        """A prose-only response → dispatch_tool never called; tools_fired==0."""
        response_prose = _fake_response(
            content=[
                _fake_text_block(
                    '{"synthesis":"all quiet","routes":[],"entries":[],'
                    '"suggested_calls":[],"gaps":[],"meta":{}}'
                )
            ],
            stop_reason="end_turn",
        )
        fake_anthropic = FakeAnthropic([response_prose])
        client = HaikuClient.__new__(HaikuClient)
        client._client = fake_anthropic
        client.model = "claude-sonnet-4-6"
        system_prompt_path = (
            Path(__file__).parent.parent / "src/sovereign_stack/scribe/prompts/system.md"
        )
        client.system_prompt = system_prompt_path.read_text()

        dispatch_spy = MagicMock(return_value=("{}", False))
        monkeypatch.setattr(bi_mod, "_client_cache", client)
        monkeypatch.setattr(bi_mod, "dispatch_tool", dispatch_spy)

        result_json = bi_mod.ask_scribe(session_id=None, message="any open threads?")

        assert dispatch_spy.call_count == 0, (
            f"Expected 0 dispatches for prose-only run, got {dispatch_spy.call_count}"
        )
        payload = json.loads(result_json)
        meta = payload["meta"]
        assert meta["tools_fired"] == 0
        assert meta["tool_calls"] == []

    def test_tool_calls_made_len_equals_one_on_haiku_result(self):
        """HaikuResult.tool_calls_made has the correct entry after a tool_use loop."""
        response1 = _fake_response(
            content=[_fake_tool_use_block("chronicle_list_domains", "tu_x", {})],
            stop_reason="tool_use",
        )
        response2 = _fake_response(
            content=[_fake_text_block("done")],
            stop_reason="end_turn",
        )
        fake_anthropic = FakeAnthropic([response1, response2])
        client = HaikuClient.__new__(HaikuClient)
        client._client = fake_anthropic
        client.model = "claude-sonnet-4-6"
        system_prompt_path = (
            Path(__file__).parent.parent / "src/sovereign_stack/scribe/prompts/system.md"
        )
        client.system_prompt = system_prompt_path.read_text()

        def fake_dispatch(name, args):
            return '{"domains":[],"count":0}', False

        result = client.generate_response(
            conversation_history=[],
            user_message="list domains",
            tools=[
                {
                    "name": "chronicle_list_domains",
                    "description": "list",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            tool_dispatch=fake_dispatch,
        )
        assert len(result.tool_calls_made) == 1
        assert result.tool_calls_made[0]["name"] == "chronicle_list_domains"
        assert result.tool_calls_made[0]["is_error"] is False


# ---------------------------------------------------------------------------
# (2) Greeting: 0 dispatches AND no tool-call XML (Goal-5 regression guard)
# ---------------------------------------------------------------------------


class TestGreetingNoToolDispatch:
    def test_greeting_dispatches_zero_tools(self, isolated_nav, monkeypatch):
        """generate_greeting must NOT pass tools= and must fire 0 tool dispatches."""
        response_greeting = _fake_response(
            content=[
                _fake_text_block(
                    "Three threads are open in sovereign-stack; the mitochondrial paper DOI landed. "
                    "– scribe-sonnet-4-6"
                )
            ],
            stop_reason="end_turn",
        )
        fake_anthropic = FakeAnthropic([response_greeting])
        client = HaikuClient.__new__(HaikuClient)
        client._client = fake_anthropic
        client.model = "claude-sonnet-4-6"
        system_prompt_path = (
            Path(__file__).parent.parent / "src/sovereign_stack/scribe/prompts/system.md"
        )
        client.system_prompt = system_prompt_path.read_text()

        result = client.generate_greeting(
            boot_context_summary="boot text",
            chronicle_context="=== context ===",
        )

        # Verify no tools were passed to the API
        assert len(fake_anthropic.messages.call_args_list) == 1
        call_kwargs = fake_anthropic.messages.call_args_list[0]
        assert "tools" not in call_kwargs, (
            "generate_greeting must not pass 'tools' kwarg to messages.create"
        )

        # Verify tool_calls_made is empty (no dispatch)
        assert result.tool_calls_made == [], (
            f"greeting tool_calls_made must be empty, got {result.tool_calls_made}"
        )

    def test_greeting_output_contains_no_tool_xml(self, isolated_nav, monkeypatch):
        """Greeting output must not contain <function or tool_use XML fragments."""
        response_greeting = _fake_response(
            content=[
                _fake_text_block(
                    "Two open threads are loud: the CFC experiment and the mitochondrial DOI. "
                    "scribe-sonnet-4-6"
                )
            ],
            stop_reason="end_turn",
        )
        fake_anthropic = FakeAnthropic([response_greeting])
        client = HaikuClient.__new__(HaikuClient)
        client._client = fake_anthropic
        client.model = "claude-sonnet-4-6"
        system_prompt_path = (
            Path(__file__).parent.parent / "src/sovereign_stack/scribe/prompts/system.md"
        )
        client.system_prompt = system_prompt_path.read_text()

        result = client.generate_greeting(
            boot_context_summary="boot text",
            chronicle_context="",
        )

        assert "<function" not in result.text, "greeting output must not contain '<function' XML"
        assert "tool_use" not in result.text, (
            "greeting output must not contain 'tool_use' substring"
        )

    def test_greeting_mode_builds_system_with_override_block(self):
        """_build_system(greeting_mode=True) prepends the GREETING MODE override block."""
        client = HaikuClient.__new__(HaikuClient)
        client.model = "claude-sonnet-4-6"
        system_prompt_path = (
            Path(__file__).parent.parent / "src/sovereign_stack/scribe/prompts/system.md"
        )
        client.system_prompt = system_prompt_path.read_text()

        blocks = client._build_system("some context", greeting_mode=True)
        # The first block must be the override
        assert blocks[0]["type"] == "text"
        assert "GREETING MODE OVERRIDE" in blocks[0]["text"]
        assert "NO tools" in blocks[0]["text"] or "no tools" in blocks[0]["text"].lower()

    def test_greeting_mode_false_has_no_override_block(self):
        """_build_system(greeting_mode=False) must NOT include a GREETING MODE override."""
        client = HaikuClient.__new__(HaikuClient)
        client.model = "claude-sonnet-4-6"
        system_prompt_path = (
            Path(__file__).parent.parent / "src/sovereign_stack/scribe/prompts/system.md"
        )
        client.system_prompt = system_prompt_path.read_text()

        blocks = client._build_system("some context", greeting_mode=False)
        all_text = " ".join(b.get("text", "") for b in blocks)
        assert "GREETING MODE OVERRIDE" not in all_text


# ---------------------------------------------------------------------------
# (5) Fork-D payload contract
# ---------------------------------------------------------------------------


class TestForkDPayload:
    """ask_scribe returns parseable JSON with required keys; constraints enforced."""

    def _json_ask(self, isolated_nav, monkeypatch, model_text: str, tool_use: bool = False) -> dict:
        """Helper: script the client and return the parsed Fork-D payload."""
        if tool_use:
            resp1 = _fake_response(
                content=[_fake_tool_use_block("chronicle_recall", "tu_p", {"query": "test"})],
                stop_reason="tool_use",
            )
            resp2 = _fake_response(
                content=[_fake_text_block(model_text)],
                stop_reason="end_turn",
            )
            responses = [resp1, resp2]
        else:
            responses = [
                _fake_response(
                    content=[_fake_text_block(model_text)],
                    stop_reason="end_turn",
                )
            ]

        fake_anthropic = FakeAnthropic(responses)
        client = HaikuClient.__new__(HaikuClient)
        client._client = fake_anthropic
        client.model = "claude-sonnet-4-6"
        system_prompt_path = (
            Path(__file__).parent.parent / "src/sovereign_stack/scribe/prompts/system.md"
        )
        client.system_prompt = system_prompt_path.read_text()

        monkeypatch.setattr(bi_mod, "_client_cache", client)
        monkeypatch.setattr(
            bi_mod, "dispatch_tool", MagicMock(return_value=('{"domains":[],"count":0}', False))
        )

        raw = bi_mod.ask_scribe(session_id=None, message="test query")
        return json.loads(raw)

    def test_required_keys_present(self, isolated_nav, monkeypatch):
        """Payload must have all required top-level keys."""
        model_json = json.dumps(
            {
                "synthesis": "The stack is stable.",
                "routes": [],
                "entries": [],
                "suggested_calls": ["my_toolkit()"],
                "gaps": [],
                "meta": {},
            }
        )
        payload = self._json_ask(isolated_nav, monkeypatch, model_json)

        for key in ("synthesis", "routes", "entries", "suggested_calls", "gaps", "meta"):
            assert key in payload, f"Required key '{key}' missing from Fork-D payload"

    def test_synthesis_non_empty(self, isolated_nav, monkeypatch):
        """synthesis field must be a non-empty string."""
        model_json = json.dumps(
            {
                "synthesis": "Open threads: CFC experiment and the scribe rearchitecture.",
                "routes": [],
                "entries": [],
                "suggested_calls": [],
                "gaps": [],
                "meta": {},
            }
        )
        payload = self._json_ask(isolated_nav, monkeypatch, model_json)
        assert isinstance(payload["synthesis"], str)
        assert len(payload["synthesis"]) > 0

    def test_meta_tools_fired_present(self, isolated_nav, monkeypatch):
        """meta must contain tools_fired (server-authoritative)."""
        model_json = json.dumps(
            {
                "synthesis": "OK.",
                "routes": [],
                "entries": [],
                "suggested_calls": [],
                "gaps": [],
                "meta": {},
            }
        )
        payload = self._json_ask(isolated_nav, monkeypatch, model_json)
        assert "tools_fired" in payload["meta"]
        assert isinstance(payload["meta"]["tools_fired"], int)

    def test_suggested_calls_subset_of_map_routes(self, isolated_nav, monkeypatch):
        """suggested_calls must only contain names present in the map; bogus names dropped."""
        # "my_toolkit()" is in the fake map; "invented_tool()" is not
        model_json = json.dumps(
            {
                "synthesis": "Here are your calls.",
                "routes": [],
                "entries": [],
                "suggested_calls": ["my_toolkit()", "invented_tool()", "recall_insights()"],
                "gaps": [],
                "meta": {},
            }
        )
        payload = self._json_ask(isolated_nav, monkeypatch, model_json)
        allowed = {
            "my_toolkit()",
            "recall_insights()",
            "where_did_i_leave_off()",
            "chronicle_recall()",
        }
        for call_name in payload["suggested_calls"]:
            assert call_name in allowed, (
                f"'{call_name}' is not a map route name and should have been filtered out"
            )
        assert "invented_tool()" not in payload["suggested_calls"]

    def test_prose_fallback_wraps_into_valid_envelope(self, isolated_nav, monkeypatch):
        """Prose model output (not JSON) wraps into a valid Fork-D envelope."""
        prose_text = "The scribe found three open threads related to the CFC experiment."
        payload = self._json_ask(isolated_nav, monkeypatch, prose_text)

        # Must parse as JSON with all required keys
        for key in ("synthesis", "routes", "entries", "suggested_calls", "gaps", "meta"):
            assert key in payload

        # Synthesis must be the prose text
        assert prose_text in payload["synthesis"]

    def test_resident_session_id_in_meta(self, isolated_nav, monkeypatch):
        """meta.resident_session_id must be present and non-None."""
        model_json = json.dumps(
            {
                "synthesis": "OK.",
                "routes": [],
                "entries": [],
                "suggested_calls": [],
                "gaps": [],
                "meta": {},
            }
        )
        payload = self._json_ask(isolated_nav, monkeypatch, model_json)
        assert "resident_session_id" in payload["meta"]
        assert payload["meta"]["resident_session_id"] is not None

    def test_suggested_calls_fail_closed_when_map_unavailable(self, isolated_nav, monkeypatch):
        """When the map file is missing, suggested_calls must be [] (fail closed).

        Guarantee 3: suggested_calls cannot smuggle a filesystem path. Without a
        known-route allowlist we cannot validate; the safe behaviour is empty, not
        pass-through of unvalidated model output.
        """
        # Point SOVEREIGN_ROOT at a location with no primary_routes.md
        empty_root = Path(isolated_nav["root"]) / "empty_map"
        empty_root.mkdir()
        monkeypatch.setattr(bi_mod, "SOVEREIGN_ROOT", empty_root)

        model_json = json.dumps(
            {
                "synthesis": "Here are your calls.",
                "routes": [],
                "entries": [],
                # Model tries to smuggle a path — must be dropped when map unavailable
                "suggested_calls": ["~/.env", "/etc/passwd", "invented_tool()"],
                "gaps": [],
                "meta": {},
            }
        )
        payload = self._json_ask(isolated_nav, monkeypatch, model_json)
        assert payload["suggested_calls"] == [], (
            "suggested_calls must be [] (fail closed) when primary_routes.md is unavailable; "
            f"got: {payload['suggested_calls']}"
        )


# ---------------------------------------------------------------------------
# (6) Boot-launch Gap-3: Starlette _lifespan fires through SovereignAsgiMiddleware
# ---------------------------------------------------------------------------


class TestBootLaunchLifespan:
    """Assert that the Starlette _lifespan ACTUALLY fires (Gap-3 from design spec).

    We do NOT assume propagation — we prove it by spying on ensure_resident_scribe.
    """

    def test_lifespan_fires_ensure_resident_scribe(self, isolated_nav, monkeypatch):
        """TestClient wrapping `app` must trigger the lifespan,
        which calls ensure_resident_scribe through SovereignAsgiMiddleware."""
        from starlette.testclient import TestClient

        from sovereign_stack.sse_server import app as sse_app

        spy = MagicMock(return_value=None)
        monkeypatch.setattr(
            "sovereign_stack.scribe.resident.ensure_resident_scribe",
            spy,
        )

        # TestClient enters lifespan on __enter__, exits on __exit__
        with TestClient(sse_app, raise_server_exceptions=False):
            pass

        assert spy.call_count >= 1, (
            "ensure_resident_scribe was not called during lifespan startup. "
            "The _lifespan event did NOT propagate through SovereignAsgiMiddleware."
        )

    def test_lifespan_fires_via_manual_protocol(self, isolated_nav, monkeypatch):
        """Drive the lifespan scope manually without TestClient to avoid HTTP middleware
        interfering. This is the direct proof that SovereignAsgiMiddleware passes
        lifespan scopes to self.app without interception."""
        from sovereign_stack.sse_server import app as sse_app

        fired = []

        async def _drive():
            startup_complete = asyncio.Event()
            shutdown_complete = asyncio.Event()

            async def receive():
                if not startup_complete.is_set():
                    startup_complete.set()
                    return {"type": "lifespan.startup"}
                await shutdown_complete.wait()
                return {"type": "lifespan.shutdown"}

            async def send(message):
                if message["type"] == "lifespan.startup.complete":
                    fired.append("startup")
                elif message["type"] == "lifespan.shutdown.complete":
                    shutdown_complete.set()
                    fired.append("shutdown")

            # Run the ASGI app with a lifespan scope
            lifespan_task = asyncio.create_task(sse_app({"type": "lifespan"}, receive, send))
            # Wait a moment for startup to complete
            await asyncio.sleep(0.05)
            # Trigger shutdown
            shutdown_complete.set()
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(lifespan_task, timeout=2.0)

        # Patch ensure_resident_scribe to avoid real API/context builds
        spy = MagicMock(return_value=None)
        monkeypatch.setattr(
            "sovereign_stack.scribe.resident.ensure_resident_scribe",
            spy,
        )

        asyncio.run(_drive())

        assert "startup" in fired, (
            "lifespan.startup.complete was never sent — _lifespan did not fire. "
            "SovereignAsgiMiddleware is not propagating the lifespan scope."
        )


# ---------------------------------------------------------------------------
# (7) Back-compat: format_scribe_block still produces + injects the SCRIBE block
# ---------------------------------------------------------------------------


class TestBackCompatScribeBlock:
    def test_format_scribe_block_contains_session_id(self):
        """format_scribe_block includes the session_id in the block output."""
        session = ScribeSession.create(parent_instance="test-instance")
        block = format_scribe_block(session)
        assert session.session_id in block

    def test_format_scribe_block_contains_scribe_header(self):
        """format_scribe_block emits the SCRIBE — OPTIONAL header."""
        session = ScribeSession.create()
        block = format_scribe_block(session)
        assert "SCRIBE" in block

    def test_format_scribe_block_includes_greeting_when_present(self):
        """If the session has an assistant turn, format_scribe_block includes it."""
        session = ScribeSession.create()
        session.append_assistant_turn("The open threads are loud today.")
        block = format_scribe_block(session)
        assert "The open threads are loud today." in block

    def test_format_scribe_block_no_error_when_no_greeting(self):
        """format_scribe_block works without any turns (no greeting generated)."""
        session = ScribeSession.create()
        block = format_scribe_block(session)
        assert isinstance(block, str)
        assert len(block) > 0

    def test_format_scribe_block_contains_ask_endpoint(self):
        """Block must include the /api/call ask_scribe endpoint reference."""
        session = ScribeSession.create()
        block = format_scribe_block(session)
        assert "ask_scribe" in block

    def test_ask_scribe_error_when_empty_message(self, isolated_nav, monkeypatch):
        """ask_scribe rejects empty messages (back-compat guard)."""
        result = bi_mod.ask_scribe(session_id=None, message="")
        assert "error" in result.lower()

    def test_ask_scribe_error_when_whitespace_message(self, isolated_nav, monkeypatch):
        """ask_scribe rejects whitespace-only messages."""
        result = bi_mod.ask_scribe(session_id=None, message="   ")
        assert "error" in result.lower()
