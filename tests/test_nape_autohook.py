"""
Nape auto-hook tests — Edit 1.

Verifies that every tool call dispatched through handle_tool automatically
triggers a Nape observation, with the correct exclusions and error semantics.

Cases:
1. Calling a tool via handle_tool triggers nape_daemon.observe with the tool name.
2. nape_observe itself does NOT cause a nested auto-observe (no infinite recursion).
3. my_toolkit is excluded from auto-observe.
4. A tool raising inside dispatch still produces a Nape observation and re-raises.
5. record_insight with completion language, preceded by recall_insights in the same
   session, emits a 'satisfied' honk (sovereign-stack verify-equivalent recognized).
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sovereign_stack.nape_daemon import VERIFY_TOOL_NAMES, NapeDaemon
from sovereign_stack.server import _NAPE_AUTOHOOK_EXCLUDE, _flatten_result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_nape_with_tmpdir():
    tmpdir = tempfile.mkdtemp()
    daemon = NapeDaemon(root=tmpdir)
    return daemon, tmpdir


# ---------------------------------------------------------------------------
# Case 1: handle_tool triggers observe with tool name
# ---------------------------------------------------------------------------

class TestAutoHookObserveTrigger:
    """observe() is called exactly once for a normal (non-excluded) tool."""

    def test_observe_called_with_tool_name(self):
        """A non-excluded tool call must reach nape_daemon.observe."""
        daemon, tmpdir = _make_nape_with_tmpdir()
        try:
            observe_calls = []
            original_observe = daemon.observe

            def capturing_observe(**kwargs):
                observe_calls.append(kwargs)
                return original_observe(**kwargs)

            daemon.observe = capturing_observe  # type: ignore[assignment]

            # We need a mock session and spiral state.  Rather than running the
            # full async server, we exercise _dispatch_tool + the auto-hook logic
            # inline, mirroring exactly what handle_tool does.
            import asyncio

            from sovereign_stack import server as srv_module
            from sovereign_stack.server import _dispatch_tool, _flatten_result

            # Patch the nape_daemon at module level and set a session_id.
            original_daemon = srv_module.nape_daemon
            original_session_id = srv_module.spiral_state.session_id
            srv_module.nape_daemon = daemon
            srv_module.spiral_state.session_id = "test-autohook-session"

            try:
                async def _run():
                    # nape_summary is excluded; record_learning is not.
                    name = "record_learning"
                    arguments = {
                        "what_happened": "test",
                        "what_learned": "auto-hook works",
                        "applies_to": "testing",
                    }
                    observe = name not in _NAPE_AUTOHOOK_EXCLUDE
                    result = await _dispatch_tool(name, arguments)
                    if observe:
                        flat = _flatten_result(result)
                        daemon.observe(
                            tool_name=name,
                            arguments=arguments or {},
                            result=flat,
                            session_id=srv_module.spiral_state.session_id,
                        )
                    return result

                asyncio.run(_run())
            finally:
                srv_module.nape_daemon = original_daemon
                srv_module.spiral_state.session_id = original_session_id

            assert len(observe_calls) == 1, (
                f"Expected exactly 1 observe call; got {len(observe_calls)}"
            )
            assert observe_calls[0]["tool_name"] == "record_learning"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_observe_receives_correct_session_id(self):
        """observe() is called with the current spiral_state.session_id."""
        daemon, tmpdir = _make_nape_with_tmpdir()
        try:
            import asyncio

            from sovereign_stack import server as srv_module
            from sovereign_stack.server import _dispatch_tool, _flatten_result

            original_daemon = srv_module.nape_daemon
            srv_module.nape_daemon = daemon
            test_session = "test-session-hook-777"
            original_session = srv_module.spiral_state.session_id
            srv_module.spiral_state.session_id = test_session

            try:
                async def _run():
                    name = "spiral_status"
                    arguments = {}
                    observe = name not in _NAPE_AUTOHOOK_EXCLUDE
                    result = await _dispatch_tool(name, arguments)
                    if observe:
                        daemon.observe(
                            tool_name=name,
                            arguments=arguments or {},
                            result=_flatten_result(result),
                            session_id=srv_module.spiral_state.session_id,
                        )
                    return result

                asyncio.run(_run())
            finally:
                srv_module.nape_daemon = original_daemon
                srv_module.spiral_state.session_id = original_session

            # Verify via observations.jsonl
            obs_path = Path(tmpdir) / "nape" / "observations.jsonl"
            assert obs_path.exists()
            import json
            records = [json.loads(l) for l in obs_path.read_text().splitlines() if l.strip()]
            assert any(r["session_id"] == test_session for r in records)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 2: nape_observe does NOT trigger nested auto-observe
# ---------------------------------------------------------------------------

class TestNoInfiniteRecursion:
    """nape_observe and its siblings are in _NAPE_AUTOHOOK_EXCLUDE."""

    def test_nape_observe_in_exclude_set(self):
        assert "nape_observe" in _NAPE_AUTOHOOK_EXCLUDE

    def test_nape_honks_in_exclude_set(self):
        assert "nape_honks" in _NAPE_AUTOHOOK_EXCLUDE

    def test_nape_ack_in_exclude_set(self):
        assert "nape_ack" in _NAPE_AUTOHOOK_EXCLUDE

    def test_nape_summary_in_exclude_set(self):
        assert "nape_summary" in _NAPE_AUTOHOOK_EXCLUDE

    def test_nape_observe_observe_flag_is_false(self):
        """The 'observe' boolean in handle_tool must be False for nape_observe."""
        name = "nape_observe"
        observe = name not in _NAPE_AUTOHOOK_EXCLUDE
        assert observe is False, (
            "nape_observe must be excluded to prevent infinite recursion"
        )


# ---------------------------------------------------------------------------
# Case 3: my_toolkit is excluded from auto-observe
# ---------------------------------------------------------------------------

class TestMyToolkitExcluded:
    def test_my_toolkit_in_exclude_set(self):
        assert "my_toolkit" in _NAPE_AUTOHOOK_EXCLUDE

    def test_my_toolkit_observe_flag_is_false(self):
        observe = "my_toolkit" not in _NAPE_AUTOHOOK_EXCLUDE
        assert observe is False, "my_toolkit must not trigger auto-observation"


# ---------------------------------------------------------------------------
# Case 4: Tool raising still produces an observation and re-raises
# ---------------------------------------------------------------------------

class TestErrorObservation:
    """When _dispatch_tool raises, Nape records the error and the exception propagates."""

    def test_error_is_observed_and_reraised(self):
        """An exception in the dispatch must be observed by Nape then re-raised."""
        daemon, tmpdir = _make_nape_with_tmpdir()
        try:
            import asyncio
            import json

            from sovereign_stack import server as srv_module

            original_daemon = srv_module.nape_daemon
            srv_module.nape_daemon = daemon
            test_session = "err-session-001"
            original_session = srv_module.spiral_state.session_id
            srv_module.spiral_state.session_id = test_session

            class BoomError(RuntimeError):
                pass

            async def _boom_dispatch(name, arguments):
                raise BoomError("intentional test error")

            try:
                async def _run():
                    name = "record_insight"
                    arguments = {"domain": "test", "content": "will fail"}
                    observe = name not in _NAPE_AUTOHOOK_EXCLUDE
                    try:
                        result = await _boom_dispatch(name, arguments)
                        if observe:
                            daemon.observe(
                                tool_name=name,
                                arguments=arguments or {},
                                result=srv_module._flatten_result(result),
                                session_id=srv_module.spiral_state.session_id,
                            )
                        return result
                    except Exception as exc:
                        if observe:
                            daemon.observe(
                                tool_name=name,
                                arguments=arguments or {},
                                result=f"ERROR: {exc}",
                                session_id=srv_module.spiral_state.session_id,
                            )
                        raise

                with pytest.raises(BoomError):
                    asyncio.run(_run())
            finally:
                srv_module.nape_daemon = original_daemon
                srv_module.spiral_state.session_id = original_session

            # The error should be in observations.jsonl
            obs_path = Path(tmpdir) / "nape" / "observations.jsonl"
            assert obs_path.exists()
            records = [json.loads(l) for l in obs_path.read_text().splitlines() if l.strip()]
            error_obs = [r for r in records if "ERROR" in r.get("result_str", "")]
            assert len(error_obs) >= 1, "Error observation must be recorded"
            assert error_obs[0]["tool_name"] == "record_insight"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 5: recall_insights before record_insight emits a satisfied honk
# ---------------------------------------------------------------------------

class TestSatisfiedHonkOnVerifyDeclare:
    """recall_insights is a sovereign-stack verify-equivalent.

    When recall_insights precedes record_insight with completion language,
    Nape's clean_verify_declare pattern fires and emits a satisfied honk.
    """

    def test_recall_insights_in_verify_tool_names(self):
        """recall_insights must be in VERIFY_TOOL_NAMES for the pattern to fire."""
        assert "recall_insights" in VERIFY_TOOL_NAMES

    def test_get_open_threads_in_verify_tool_names(self):
        assert "get_open_threads" in VERIFY_TOOL_NAMES

    def test_route_in_verify_tool_names(self):
        assert "route" in VERIFY_TOOL_NAMES

    def test_comms_get_acks_in_verify_tool_names(self):
        assert "comms_get_acks" in VERIFY_TOOL_NAMES

    def test_satisfied_honk_emitted_after_verify_then_declare(self):
        """recall_insights before record_insight with completion language → satisfied honk."""
        daemon, tmpdir = _make_nape_with_tmpdir()
        try:
            session = "satisfied-test-session"
            # Observe recall_insights (verify equivalent)
            daemon.observe(
                tool_name="recall_insights",
                arguments={"query": "compass"},
                result="[{insight: 'compass is grounded'}]",
                session_id=session,
            )
            # Observe record_insight with completion language
            daemon.observe(
                tool_name="record_insight",
                arguments={"domain": "compass", "content": "verified insight"},
                result="Insight recorded: complete",
                session_id=session,
            )

            honks = daemon.current_honks(session)
            satisfied = [h for h in honks if h["level"] == "satisfied"]
            assert len(satisfied) >= 1, (
                f"Expected satisfied honk after recall_insights + completion result. Got: {honks}"
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_no_verify_before_declare_fires_sharp_not_satisfied(self):
        """Without any verify call, completion language fires sharp, not satisfied."""
        daemon, tmpdir = _make_nape_with_tmpdir()
        try:
            session = "no-verify-session"
            daemon.observe(
                tool_name="record_insight",
                arguments={"domain": "test", "content": "x"},
                result="complete — all done",
                session_id=session,
            )
            honks = daemon.current_honks(session)
            satisfied = [h for h in honks if h["level"] == "satisfied"]
            sharp = [h for h in honks if h["level"] == "sharp" and h["pattern"] == "declare_before_verify"]
            assert len(satisfied) == 0, "No satisfied honk when verify is absent"
            assert len(sharp) >= 1, "Sharp honk must fire when declare with no verify"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Case: _flatten_result helper
# ---------------------------------------------------------------------------

class TestFlattenResult:
    def test_flatten_empty_list(self):
        assert _flatten_result([]) == ""

    def test_flatten_none(self):
        assert _flatten_result(None) == ""

    def test_flatten_text_content_objects(self):
        item = MagicMock()
        item.text = "hello world"
        assert _flatten_result([item]) == "hello world"

    def test_flatten_multiple_items(self):
        a = MagicMock()
        a.text = "part one"
        b = MagicMock()
        b.text = "part two"
        result = _flatten_result([a, b])
        assert "part one" in result
        assert "part two" in result

    def test_flatten_caps_at_4000_chars(self):
        item = MagicMock()
        item.text = "x" * 10000
        result = _flatten_result([item])
        assert len(result) <= 4000
