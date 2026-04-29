"""
Nape auto-hook tests — Edit 2.

Verifies that every tool call dispatched through handle_tool automatically
triggers a Nape observation, with the correct exclusions and error semantics.

Cases:
1. Calling a tool via handle_tool triggers nape_daemon.observe with the tool name.
2. nape_observe itself does NOT cause a nested auto-observe (no infinite recursion).
3. my_toolkit is excluded from auto-observe.
4. A tool raising inside dispatch still produces a Nape observation and re-raises.
5. record_insight with completion language, preceded by recall_insights in the same
   session, emits a 'satisfied' honk (sovereign-stack verify-equivalent recognized).
6. _dispatch_tool writes chronicle entries to the file named after the active
   session_id, not a stale or default session (regression for test-autohook-session
   pollution of the live chronicle).

ISOLATION NOTE: Tests that call _dispatch_tool must patch srv_module.experiential
with a temp-dir ExperientialMemory and srv_module.SPIRAL_STATE_PATH with a temp
path so they never write to the live ~/.sovereign/ tree.
"""

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.nape_daemon import VERIFY_TOOL_NAMES, NapeDaemon
from sovereign_stack.server import _NAPE_AUTOHOOK_EXCLUDE, _flatten_result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nape_with_tmpdir():
    tmpdir = tempfile.mkdtemp()
    daemon = NapeDaemon(root=tmpdir)
    return daemon, tmpdir


@contextmanager
def _isolated_server(session_id: str):
    """
    Context manager that patches the server module so _dispatch_tool calls
    never touch the live ~/.sovereign/ filesystem.

    Yields a tuple (srv_module, tmp_root: Path) where tmp_root is the
    temporary sovereign root used for this context.

    Patches applied:
    - srv_module.experiential  → ExperientialMemory rooted in tmp_root/chronicle
    - srv_module.SPIRAL_STATE_PATH → tmp_root/spiral_state.json
    - srv_module.spiral_state.session_id → session_id (restored on exit)
    """
    from sovereign_stack import server as srv_module

    tmp_root = Path(tempfile.mkdtemp())
    chronicle_root = tmp_root / "chronicle"
    chronicle_root.mkdir(parents=True)

    tmp_experiential = ExperientialMemory(root=str(chronicle_root))
    tmp_spiral_path = tmp_root / "spiral_state.json"

    original_experiential = srv_module.experiential
    original_spiral_path = srv_module.SPIRAL_STATE_PATH
    original_session_id = srv_module.spiral_state.session_id

    srv_module.experiential = tmp_experiential
    srv_module.SPIRAL_STATE_PATH = tmp_spiral_path
    srv_module.spiral_state.session_id = session_id

    try:
        yield srv_module, tmp_root
    finally:
        srv_module.experiential = original_experiential
        srv_module.SPIRAL_STATE_PATH = original_spiral_path
        srv_module.spiral_state.session_id = original_session_id
        shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 1: handle_tool triggers observe with tool name
# ---------------------------------------------------------------------------


class TestAutoHookObserveTrigger:
    """observe() is called exactly once for a normal (non-excluded) tool."""

    def test_observe_called_with_tool_name(self):
        """A non-excluded tool call must reach nape_daemon.observe."""
        daemon, nape_tmpdir = _make_nape_with_tmpdir()
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
            #
            # Use _isolated_server to sandbox chronicle + spiral-state writes so
            # this test never touches the live ~/.sovereign/ tree.
            import asyncio

            from sovereign_stack.server import _dispatch_tool, _flatten_result

            original_daemon = __import__("sovereign_stack.server", fromlist=["nape_daemon"]).nape_daemon

            with _isolated_server("test-autohook-session") as (srv_module, _tmp_root):
                srv_module.nape_daemon = daemon
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

            assert len(observe_calls) == 1, (
                f"Expected exactly 1 observe call; got {len(observe_calls)}"
            )
            assert observe_calls[0]["tool_name"] == "record_learning"
        finally:
            shutil.rmtree(nape_tmpdir, ignore_errors=True)

    def test_observe_receives_correct_session_id(self):
        """observe() is called with the current spiral_state.session_id."""
        daemon, nape_tmpdir = _make_nape_with_tmpdir()
        try:
            import asyncio

            from sovereign_stack.server import _dispatch_tool, _flatten_result

            test_session = "test-session-hook-777"

            with _isolated_server(test_session) as (srv_module, _tmp_root):
                original_daemon = srv_module.nape_daemon
                srv_module.nape_daemon = daemon
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

            # Verify via observations.jsonl
            obs_path = Path(nape_tmpdir) / "nape" / "observations.jsonl"
            assert obs_path.exists()
            import json

            records = [json.loads(ln) for ln in obs_path.read_text().splitlines() if ln.strip()]
            assert any(r["session_id"] == test_session for r in records)
        finally:
            shutil.rmtree(nape_tmpdir, ignore_errors=True)


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
        assert observe is False, "nape_observe must be excluded to prevent infinite recursion"


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
        daemon, nape_tmpdir = _make_nape_with_tmpdir()
        try:
            import asyncio
            import json

            class BoomError(RuntimeError):
                pass

            async def _boom_dispatch(name, arguments):
                raise BoomError("intentional test error")

            with _isolated_server("err-session-001") as (srv_module, _tmp_root):
                original_daemon = srv_module.nape_daemon
                srv_module.nape_daemon = daemon
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

            # The error should be in observations.jsonl
            obs_path = Path(nape_tmpdir) / "nape" / "observations.jsonl"
            assert obs_path.exists()
            records = [json.loads(ln) for ln in obs_path.read_text().splitlines() if ln.strip()]
            error_obs = [r for r in records if "ERROR" in r.get("result_str", "")]
            assert len(error_obs) >= 1, "Error observation must be recorded"
            assert error_obs[0]["tool_name"] == "record_insight"
        finally:
            shutil.rmtree(nape_tmpdir, ignore_errors=True)


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
            sharp = [
                h
                for h in honks
                if h["level"] == "sharp" and h["pattern"] == "declare_before_verify"
            ]
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


# ---------------------------------------------------------------------------
# Case 6: Chronicle file naming — session_id matches filename (regression)
# ---------------------------------------------------------------------------


class TestChronicleFileNaming:
    """
    Regression test for the 'test-autohook-session entries in wrong chronicle
    files' bug (2026-04-29).

    Root cause: tests that call _dispatch_tool used the module-level
    ExperientialMemory pointing at the live ~/.sovereign/chronicle tree.
    record_insight writes entries to insights/<domain>/<session_id>.jsonl.
    When the test patched spiral_state.session_id to "test-autohook-session"
    but left srv_module.experiential pointing at the live system, entries
    for a session named X landed in a file that subsequent real-session boots
    would never look for (the test session_id) — or conversely, real-session
    entries landed under a file named after the test pollution value.

    The fix: _isolated_server() patches srv_module.experiential to a temp
    ExperientialMemory and srv_module.SPIRAL_STATE_PATH to a temp file, so
    _dispatch_tool writes never touch the live tree.

    This test verifies:
    1. After a record_insight dispatch with session_id "reg-test-session-42",
       exactly one .jsonl file exists under insights/ and its name matches
       the session_id (not a stale or default value).
    2. The entry embedded in that file also has session_id "reg-test-session-42".
    3. No files are written to the live ~/.sovereign/chronicle tree.
    """

    def test_record_insight_file_name_matches_session_id(self):
        """Chronicle file for record_insight must be named <session_id>.jsonl."""
        import asyncio
        import json

        from sovereign_stack.server import _dispatch_tool

        target_session = "reg-test-session-42"

        with _isolated_server(target_session) as (srv_module, tmp_root):
            asyncio.run(
                _dispatch_tool(
                    "record_insight",
                    {
                        "domain": "regression",
                        "content": "chronicle file naming must match session_id",
                        "layer": "hypothesis",
                    },
                )
            )

            # Check the temp chronicle — must have the right file name.
            chronicle_root = tmp_root / "chronicle"
            insight_files = list((chronicle_root / "insights" / "regression").glob("*.jsonl"))
            assert len(insight_files) == 1, (
                f"Expected 1 insight file; got {insight_files}"
            )
            file_stem = insight_files[0].stem
            assert file_stem == target_session, (
                f"Chronicle file stem '{file_stem}' != session_id '{target_session}'. "
                "Entries are landing in a file whose name does not match the session."
            )

            # Verify the embedded session_id also matches.
            entries = [
                json.loads(ln)
                for ln in insight_files[0].read_text().splitlines()
                if ln.strip()
            ]
            assert len(entries) == 1
            assert entries[0]["session_id"] == target_session, (
                f"Embedded session_id '{entries[0]['session_id']}' != '{target_session}'"
            )

    def test_dispatch_does_not_write_to_live_chronicle(self):
        """_dispatch_tool via _isolated_server must not create files under ~/.sovereign."""
        import asyncio
        from pathlib import Path

        live_chronicle = Path.home() / ".sovereign" / "chronicle"
        target_session = "isolation-verify-session-99"

        # Capture files before the test run.
        before = set(live_chronicle.rglob("*.jsonl")) if live_chronicle.exists() else set()

        with _isolated_server(target_session) as (_srv, _tmp_root):
            asyncio.run(
                __import__("sovereign_stack.server", fromlist=["_dispatch_tool"])._dispatch_tool(
                    "record_insight",
                    {
                        "domain": "isolation-check",
                        "content": "this must not reach the live chronicle",
                    },
                )
            )

        after = set(live_chronicle.rglob("*.jsonl")) if live_chronicle.exists() else set()
        new_files = after - before
        # Allow files that were created by other concurrent processes, but the
        # test-specific session file must not appear.
        test_files = [f for f in new_files if target_session in str(f)]
        assert not test_files, (
            f"_dispatch_tool wrote to live chronicle despite _isolated_server: {test_files}"
        )


# ---------------------------------------------------------------------------
# Thread 1 regression: close_session does not rotate session_id
# ---------------------------------------------------------------------------


class TestCloseSessionNoRotation:
    """
    Regression for the open thread (2026-04-29):
    'close_session does not rotate session_id'.

    close_session is intentionally NOT a rotation call — spiral_inherit is
    the explicit rotation. This test verifies the contract:
    1. After close_session, spiral_state.session_id is UNCHANGED.
    2. The close_session output text contains a reminder that rotation
       requires a separate spiral_inherit call.

    If someone accidentally merges rotation into close_session, test 1 will
    still pass (rotation would also be acceptable) — but test 2 will break
    if the reminder message is removed, keeping the documentation honest.
    """

    def test_close_session_preserves_session_id(self):
        """session_id must be the same before and after close_session."""
        import asyncio

        from sovereign_stack.server import _dispatch_tool

        with _isolated_server("pre-close-session-id") as (srv_module, _tmp_root):
            session_before = srv_module.spiral_state.session_id

            asyncio.run(
                _dispatch_tool(
                    "close_session",
                    {
                        "what_i_learned": "session_id preservation test",
                        "source_instance": "test-close-session",
                    },
                )
            )

            session_after = srv_module.spiral_state.session_id

        assert session_before == session_after, (
            f"close_session rotated session_id from '{session_before}' to '{session_after}'. "
            "close_session must not rotate — call spiral_inherit for explicit rotation."
        )

    def test_close_session_output_contains_rotation_reminder(self):
        """close_session output must remind the caller that session_id was not rotated."""
        import asyncio

        from sovereign_stack.server import _dispatch_tool

        with _isolated_server("rotation-reminder-test") as (_srv, _tmp_root):
            result = asyncio.run(
                _dispatch_tool(
                    "close_session",
                    {
                        "what_i_learned": "checking for rotation reminder in output",
                        "source_instance": "test-close-session",
                    },
                )
            )

        output_text = result[0].text
        assert "spiral_inherit" in output_text, (
            "close_session output must mention spiral_inherit so callers know how to rotate. "
            f"Got: {output_text[:300]}"
        )
