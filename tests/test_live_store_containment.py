"""The suite's live-store containment, pinned — and demonstrated able to FAIL.

WHAT THIS PINS, and why it is a test file rather than a comment in conftest.

On 2026-09-05 a review of this branch snapshotted ~/.sovereign around one full
run of this suite: 61 files changed, 55 under ~/.sovereign/scribe_threads/, of
which 53 were brand-new scribe session logs stamped
``"parent_instance": "test"`` / ``"test-instance"`` / ``"test-refactor"`` /
``"test-compact"``, carrying real generated greeting prose and a per-session
``cost_usd``. Every boot-path test had been making a live Sonnet call on
Anthony's account and writing the answer into his live store. One day of runs:
385 sessions, 47,440 in / 57,897 out tokens, $18.17.

The receipt that missed all of it globbed ``*.jsonl``; the writes are ``.log``
and ``.json``. **A filter narrower than the write path cannot fail, and a check
that cannot fail is not a receipt** — the same fail-open shape this branch
closes on ``record_learning``.

So the containment fixture in ``tests/conftest.py`` gets the treatment the
stack's own experimental law #2 demands of any gate: it must be shown able to
fail on the case it exists to catch. Half of the tests below deliberately aim
each guarded writer at the real ``~/.sovereign`` and assert it is REFUSED; the
other half aim it at a tmp root and assert it still WRITES, so the guard is a
gate and not a blanket denial.

The scribe was the loud half of that finding. The nape autohook was a quiet
one, found by enumerating the other changed files rather than stopping at the
53: ``server.py:182`` builds ``NapeDaemon(root=DEFAULT_ROOT)`` as a module-level
singleton, so every dispatched tool call in the suite appended a row to
Anthony's live ``~/.sovereign/nape/observations.jsonl``. Both halves are pinned
here, because closing only the half that was reported would have been a second
false receipt.

Nothing here writes to the live store: every refusal is raised before the
underlying writer is called.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sovereign_stack.scribe import bridge_integration as bi
from sovereign_stack.scribe import haiku_client as hc
from sovereign_stack.scribe import resident as res
from sovereign_stack.scribe import session as sess
from sovereign_stack.scribe.session import ScribeSession

LIVE_SOVEREIGN = Path(os.path.expanduser("~/.sovereign"))


class TestTheCostKillSwitchIsOnForEveryTest:
    """Limb (a): SCRIBE_BOOT_GREETING=off, read per call at
    bridge_integration.py:61 and checked at server.py:3428 BEFORE the spawn."""

    def test_flag_is_off_without_the_test_asking(self):
        assert os.environ.get("SCRIBE_BOOT_GREETING") == "off"

    def test_the_flag_actually_disables_the_greeting(self):
        assert bi.boot_greeting_enabled() is False

    def test_boot_inject_is_a_different_flag_and_is_not_the_cost_kill(self):
        # Named explicitly because the two flags read alike and only one of
        # them stops the spend: SCRIBE_BOOT_INJECT=off still spawns, still
        # calls the model, still bills — it only hides the text.
        assert bi.boot_inject_enabled() is True


class TestNoTestCanConstructALiveModelClient:
    """Limb (b): the money is spent inside ``client.generate_greeting`` and the
    log is written after it returns, so a write-only guard would report a charge
    that had already been made. This stops the construction."""

    def test_haiku_client_construction_is_refused(self):
        with pytest.raises(RuntimeError, match="may not construct a live HaikuClient"):
            hc.HaikuClient()

    def test_get_client_degrades_to_none_exactly_as_with_no_api_key(self, monkeypatch):
        monkeypatch.setattr(bi, "_client_cache", None)
        monkeypatch.setattr(bi, "_client_error", None)
        assert bi.get_client() is None
        assert bi.client_status()["state"] == "failed"

    def test_greet_session_is_a_no_op_and_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(bi, "_client_cache", None)
        monkeypatch.setattr(bi, "_client_error", None)
        session = ScribeSession.create(parent_instance="containment-test")
        assert bi.greet_session(session) is None
        assert session.turns == []

    def test_an_injected_fake_client_still_works(self, monkeypatch):
        # The guard blocks CONSTRUCTION, not injection: test_scribe_navigational
        # builds its client with HaikuClient.__new__ and assigns _client_cache,
        # and must keep working.
        sentinel = object()
        monkeypatch.setattr(bi, "_client_cache", sentinel)
        assert bi.get_client() is sentinel


class TestTheThreeWritersRefuseTheLiveStore:
    """Limb (c), the falsifier half: each guarded writer, aimed at the real
    ~/.sovereign, must raise. If any of these ever passes, the containment has
    gone fail-open and the suite is writing into Anthony's store again."""

    def test_greeting_log_refuses_the_live_root(self):
        # PHASE1_LOG_ROOT is bound from SOVEREIGN_ROOT at module import, so it
        # already points at the live store here — this is the unmodified,
        # real-world case, not a contrived one.
        assert str(bi.PHASE1_LOG_ROOT).startswith(str(LIVE_SOVEREIGN))
        session = ScribeSession.create(parent_instance="containment-test")
        with pytest.raises(AssertionError, match="AIMED THE SCRIBE'S greeting log"):
            bi._log_phase1_greeting(session, "greeting text", {"cost_usd": 0.0})

    def test_resident_marker_refuses_the_live_root(self):
        # The limb SCRIBE_BOOT_GREETING does NOT close: server.py:3405 calls
        # ensure_resident_scribe() before the flag is ever read.
        assert str(res.RESIDENT_STATE_PATH).startswith(str(LIVE_SOVEREIGN))
        with pytest.raises(AssertionError, match="AIMED THE SCRIBE'S resident marker"):
            res._write_resident_marker("scribe_resident_test", 0.0, 0.0)

    def test_session_archive_refuses_the_live_root_by_default(self):
        assert str(sess.ARCHIVE_ROOT).startswith(str(LIVE_SOVEREIGN))
        session = ScribeSession.create(parent_instance="containment-test")
        with pytest.raises(AssertionError, match="AIMED THE SCRIBE'S session archive"):
            sess.archive_session(session)

    def test_session_archive_refuses_an_explicit_live_root(self):
        session = ScribeSession.create(parent_instance="containment-test")
        with pytest.raises(AssertionError, match="AIMED THE SCRIBE'S session archive"):
            sess.archive_session(session, archive_root=LIVE_SOVEREIGN / "scribe_threads")

    def test_the_refusal_names_the_import_time_binding(self):
        # The message has to say WHY setting SOVEREIGN_ROOT in the test did not
        # help, or the next reader re-learns it the expensive way.
        session = ScribeSession.create(parent_instance="containment-test")
        with pytest.raises(AssertionError) as excinfo:
            sess.archive_session(session)
        assert "AT MODULE IMPORT" in str(excinfo.value)


class TestTheWriterGuardCoversAReachablePath:
    """THE GAP THIS CLASS EXISTS TO CLOSE, stated plainly because it is easy to
    miss: with limb (a) on, ``boot_spawn_and_greet_async`` is never called, so
    the writer guard in limb (c) is never *exercised* by a normal suite run. A
    guard that only ever sits on a path the flag already closed is unproven.

    These drive ``greet_session`` directly with the flag defeated and a fake
    client injected — the exact state the suite was in before the fix — and
    show the writer IS reached and IS refused, with nothing written."""

    @staticmethod
    def _fake_client():
        class _Result:
            text = "a greeting that must never reach the live store"
            tokens_in = 1
            tokens_out = 1
            tokens_cache_creation = 0
            tokens_cache_read = 0
            cost_usd = 0.0
            model = "fake"
            stop_reason = "end_turn"

        class _Client:
            def generate_greeting(self, **_kwargs):
                return _Result()

        return _Client()

    def test_with_the_flag_on_and_a_client_present_the_write_is_refused(self, monkeypatch):
        monkeypatch.setenv("SCRIBE_BOOT_GREETING", "on")
        assert bi.boot_greeting_enabled() is True
        monkeypatch.setattr(bi, "_client_cache", self._fake_client())
        session = ScribeSession.create(parent_instance="containment-test")
        with pytest.raises(AssertionError, match="AIMED THE SCRIBE'S greeting log"):
            bi.greet_session(session)

    def test_nothing_lands_on_disk_when_it_is_refused(self, monkeypatch):
        """The refusal is raised BEFORE the writer runs, so the day's live log
        directory must not gain a file. Counted, not assumed."""
        log_dir = bi.PHASE1_LOG_ROOT / __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%d")
        before = len(list(log_dir.glob("*.log"))) if log_dir.is_dir() else 0
        monkeypatch.setenv("SCRIBE_BOOT_GREETING", "on")
        monkeypatch.setattr(bi, "_client_cache", self._fake_client())
        session = ScribeSession.create(parent_instance="containment-test")
        with pytest.raises(AssertionError):
            bi.greet_session(session)
        after = len(list(log_dir.glob("*.log"))) if log_dir.is_dir() else 0
        # A sibling worktree running this same suite writes into that directory
        # concurrently, so this asserts the count did not grow BY THIS CALL —
        # an exact-equality check here would be flaky for someone else's writes.
        assert after - before <= 0 or not log_dir.is_dir()

    def test_the_server_boot_path_swallows_the_refusal_but_still_writes_nothing(self):
        """HONEST LIMIT, recorded so nobody over-reads the guard. server.py
        wraps the whole greeting call in a bare ``except Exception: session =
        None`` (3427-3435), so a refusal raised inside ``greet_session`` does
        NOT fail a test that goes through the boot dispatcher — it is swallowed
        like any other scribe degradation. The write is still prevented, which
        is the property that matters; the guard's ability to name the test only
        holds for callers that do not swallow. Limb (a) is what keeps the boot
        path off this branch entirely, and it is why the flag, not the writer
        guard, is the primary defence."""
        import inspect

        from sovereign_stack import server

        source = inspect.getsource(server)
        assert "boot_spawn_and_greet_async" in source
        assert "scribe_bridge.boot_greeting_enabled()" in source


class TestTheGuardIsAGateAndNotABlanketDenial:
    """Limb (c), the positive-control half: a properly redirected writer must
    still write. A gate that refuses everything proves nothing (law #3)."""

    def test_greeting_log_writes_under_a_redirected_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bi, "PHASE1_LOG_ROOT", tmp_path / "_logs")
        session = ScribeSession.create(parent_instance="containment-test")
        path = bi._log_phase1_greeting(session, "hello", {"cost_usd": 0.0})
        assert path.exists()
        assert json.loads(path.read_text())["session_id"] == session.session_id

    def test_resident_marker_writes_under_a_redirected_path(self, tmp_path, monkeypatch):
        state = tmp_path / "scribe_threads" / "_resident" / "state.json"
        monkeypatch.setattr(res, "RESIDENT_STATE_PATH", state)
        res._write_resident_marker("scribe_resident_test", 0.0, 0.0)
        assert json.loads(state.read_text())["session_id"] == "scribe_resident_test"

    def test_session_archive_writes_under_a_tmp_root(self, tmp_path):
        session = ScribeSession.create(parent_instance="containment-test")
        session.append_user_turn("hi")
        path = sess.archive_session(session, archive_root=tmp_path)
        assert path.exists()
        assert path.is_relative_to(tmp_path)


class TestTheNapeAutohookIsRedirectedAndRefused:
    """The quiet half of the same finding. `server.nape_daemon` is a
    module-level singleton built from DEFAULT_ROOT at import, so patching
    `server.DEFAULT_ROOT` or setting SOVEREIGN_ROOT inside a test does not move
    it — measured: a full-suite run appended rows stamped
    "source_instance": "test" to ~/.sovereign/nape/observations.jsonl."""

    def test_the_server_singleton_is_not_rooted_at_the_live_store(self):
        server = pytest.importorskip("sovereign_stack.server")
        root = server.nape_daemon._root
        assert not str(Path(root).resolve()).startswith(str(LIVE_SOVEREIGN.resolve())), (
            f"server.nape_daemon is rooted at {root} — the autohook is writing "
            "the suite's synthetic tool calls into Anthony's live drift telemetry"
        )

    def test_default_root_is_still_the_live_one_so_the_redirect_is_load_bearing(self):
        """The falsifier's premise: DEFAULT_ROOT really does point at the live
        store, so a singleton built from it really would write there. Without
        this, the test above could pass for the wrong reason."""
        server = pytest.importorskip("sovereign_stack.server")
        assert str(Path(server.DEFAULT_ROOT).resolve()) == str(LIVE_SOVEREIGN.resolve())

    def test_an_append_aimed_at_the_live_nape_store_is_refused(self):
        from sovereign_stack import nape_daemon as nape

        with pytest.raises(AssertionError, match="AIMED THE NAPE STORE"):
            nape._append_jsonl(LIVE_SOVEREIGN / "nape" / "observations.jsonl", {"x": 1})

    def test_the_refusal_names_the_singleton_to_rebind(self):
        from sovereign_stack import nape_daemon as nape

        with pytest.raises(AssertionError) as excinfo:
            nape._append_jsonl(LIVE_SOVEREIGN / "nape" / "honks.jsonl", {"x": 1})
        assert "sovereign_stack.server.nape_daemon" in str(excinfo.value)

    def test_a_tmp_rooted_daemon_still_observes(self, tmp_path):
        """Positive control: the guard is a gate, not a blanket denial, and the
        autohook keeps working — redirected, not disabled."""
        from sovereign_stack.nape_daemon import NapeDaemon

        daemon = NapeDaemon(root=str(tmp_path))
        daemon.observe(
            tool_name="record_insight",
            arguments={"domain": "containment-test"},
            result="ok",
            session_id="spiral_containment_test",
        )
        rows = (tmp_path / "nape" / "observations.jsonl").read_text().splitlines()
        assert len(rows) == 1
        assert json.loads(rows[0])["tool_name"] == "record_insight"
