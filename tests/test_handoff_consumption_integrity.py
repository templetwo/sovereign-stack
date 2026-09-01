"""
Regression suite for the handoff-consumption fail-open (2026-08-01).

Background (mesh forensics on the live ~/.sovereign/handoffs/ store, 251
records): 249/251 (99%) were consumed and therefore permanently unreachable
at boot, because unconsumed() filters them out and nothing else surfaces
them. consumed_by breakdown: "unknown" 159, "test" 78, only 14 naming an
actual reader. The "test" values were traced to two test fixtures that
looked isolated (they patched srv.experiential / srv.SPIRAL_STATE_PATH /
SOVEREIGN_ROOT) but never patched srv.handoff_engine — a module-level
HandoffEngine singleton built from DEFAULT_ROOT at *import* time, which a
post-import env var change cannot rebind. See tests/test_resume_in_context.py
and tests/test_nape_autohook.py::_isolated_server for the fixed call sites.

Four families of guarantee, each with a test proving it FAILS on the
pre-fix shape and PASSES after (per standing law #2 — a gate that cannot be
shown to fail is not a gate):

  1. consumed_by / mark_acted_on's consumed_by must reject empty and
     non-identifying placeholders ("unknown", "test", ...) — raise, not
     silently substitute.
  2. The test suite itself must be structurally incapable of writing to the
     live ~/.sovereign store, independent of whether any given fixture
     remembers to patch every module-level singleton.
  3. consumed_count() exists and the boot surface uses it so an empty
     unconsumed() list is never silently indistinguishable from "no
     handoffs were ever written."
  4. where_did_i_leave_off must not crash when the reader can't be
     identified (e.g. default "unknown") — it must skip consumption
     (leaving the handoffs reachable on the next boot) rather than stamping
     a meaningless consumed_by or raising out of the tool call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sovereign_stack.handoff import HandoffEngine

# NOTE: NON_IDENTIFYING_CONSUMERS and _refuse_live_store_during_tests are
# imported locally inside the specific tests that need them (below), not
# here at module level. Both names are NEW in this fix — importing them at
# module level would make this entire file fail to collect when run against
# pre-fix handoff.py, which is exactly the "before" state this suite needs
# to be run against (see the docstring above and the report this suite
# accompanies). Keeping HandoffEngine itself at module level is safe: it
# existed pre-fix too.
from tests.test_nape_autohook import _isolated_server


def _run(coro):
    return asyncio.run(coro)


# ── 1. consumed_by must name a real reader ──────────────────────────────────


class TestConsumedByRejectsPlaceholders:
    def setup_method(self):
        import tempfile

        self.tmpdir = tempfile.mkdtemp()
        self.engine = HandoffEngine(root=self.tmpdir)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @pytest.mark.parametrize("bad_value", ["unknown", "test", "UNKNOWN", "  Test  ", ""])
    def test_mark_consumed_rejects_placeholder(self, bad_value):
        r = self.engine.write("note", "i", "s", "t")
        with pytest.raises(ValueError):
            self.engine.mark_consumed([r["_path"]], consumed_by=bad_value)
        # And the record must be untouched — a rejected call leaves no trace,
        # it doesn't half-write.
        data = self.engine.unconsumed()
        assert len(data) == 1
        assert data[0]["consumed_by"] is None
        assert data[0]["consumed_at"] is None

    def test_mark_consumed_accepts_real_identity(self):
        r = self.engine.write("note", "i", "s", "t")
        count = self.engine.mark_consumed([r["_path"]], consumed_by="claude-sonnet-4-6-mac-studio")
        assert count == 1

    @pytest.mark.parametrize("bad_value", ["unknown", "test", ""])
    def test_mark_acted_on_rejects_placeholder(self, bad_value):
        r = self.engine.write("note", "i", "s", "t")
        with pytest.raises(ValueError):
            self.engine.mark_acted_on(r["_path"], bad_value, "did a thing")

    def test_denylist_covers_the_forensics_values(self):
        """The two values actually found polluting the live store."""
        from sovereign_stack.handoff import NON_IDENTIFYING_CONSUMERS

        assert "unknown" in NON_IDENTIFYING_CONSUMERS
        assert "test" in NON_IDENTIFYING_CONSUMERS


# ── 2. the test suite cannot write to the live store ────────────────────────


class TestLiveStoreGuard:
    """Proves the fix for the confirmed leak, not a hypothetical.

    These construct a HandoffEngine rooted at the REAL ~/.sovereign (via
    HandoffEngine.__init__ -> mkdir(parents=True, exist_ok=True), which is a
    no-op against a directory that already exists — nothing is created or
    modified) and assert every mutating method refuses before touching a
    single file. The refusal is keyed on PYTEST_CURRENT_TEST, which pytest
    itself has set for the duration of this test — no manual env
    manipulation needed, this is the exact condition a real test run is in.
    """

    def test_write_refuses_against_real_home(self):
        engine = HandoffEngine(root=str(Path.home() / ".sovereign"))
        with pytest.raises(RuntimeError, match="live Sovereign Stack"):
            engine.write(
                note="if you see this file for real, the guard failed",
                source_instance="test-guard-canary",
                source_session_id="s",
            )

    def test_mark_consumed_refuses_against_real_home(self):
        engine = HandoffEngine(root=str(Path.home() / ".sovereign"))
        with pytest.raises(RuntimeError, match="live Sovereign Stack"):
            engine.mark_consumed(["/nonexistent/wouldnt/matter.json"], consumed_by="canary")

    def test_mark_acted_on_refuses_against_real_home(self):
        engine = HandoffEngine(root=str(Path.home() / ".sovereign"))
        with pytest.raises(RuntimeError, match="live Sovereign Stack"):
            engine.mark_acted_on("/nonexistent/wouldnt/matter.json", "canary", "did a thing")

    def test_guard_is_a_noop_outside_pytest(self, monkeypatch):
        """The guard must not fire in production — only under pytest."""
        from sovereign_stack.handoff import _refuse_live_store_during_tests

        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        # Should not raise: no PYTEST_CURRENT_TEST means this isn't a test
        # process from the guard's point of view (even though it obviously
        # is one right now — this monkeypatch is the point).
        _refuse_live_store_during_tests(Path.home() / ".sovereign" / "handoffs")

    def test_guard_allows_tmp_roots(self, tmp_path):
        """Sanity: the guard must not false-positive on ordinary tmp isolation
        (every other test in this suite depends on this being true)."""
        from sovereign_stack.handoff import _refuse_live_store_during_tests

        _refuse_live_store_during_tests(tmp_path / "handoffs")  # must not raise

    def test_isolated_server_handoff_tool_does_not_touch_real_store(self):
        """End-to-end proof for the SPECIFIC confirmed leak: dispatching the
        `handoff` tool through the now-fixed _isolated_server fixture must
        not raise the live-store guard (because handoff_engine is properly
        rerouted) and must not create anything under the real store."""
        from sovereign_stack import server as srv

        real_handoffs_dir = Path.home() / ".sovereign" / "handoffs"
        before = {p.name for p in real_handoffs_dir.glob("*.json")}

        with _isolated_server("integrity-test-handoff") as (srv_mod, tmp_root):
            assert srv_mod.handoff_engine.root != real_handoffs_dir
            result = _run(
                srv.handle_tool(
                    "handoff",
                    {
                        "note": "isolation canary — must land in tmp_root only",
                        "source_instance": "integrity-test",
                        "thread": "isolation-canary",
                    },
                )
            )
            assert "Handoff written" in result[0].text
            # It must have landed under tmp_root, not the real store.
            written = list((tmp_root / "handoffs").glob("*isolation-canary*"))
            assert len(written) == 1

        after = {p.name for p in real_handoffs_dir.glob("*.json")}
        assert after == before, "the handoff tool wrote into the LIVE store"


# ── 3. absence must never be silently indistinguishable from emptiness ──────


class TestConsumedCountReporting:
    def setup_method(self):
        import tempfile

        self.tmpdir = tempfile.mkdtemp()
        self.engine = HandoffEngine(root=self.tmpdir)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_consumed_count_zero_when_nothing_consumed(self):
        self.engine.write("note", "i", "s", "t")
        assert self.engine.consumed_count() == 0

    def test_consumed_count_reflects_consumed_records(self):
        r1 = self.engine.write("note1", "i", "s", "t")
        self.engine.write("note2", "i", "s", "t")
        self.engine.mark_consumed([r1["_path"]], consumed_by="real-reader")
        assert self.engine.consumed_count() == 1
        assert len(self.engine.unconsumed()) == 1

    def test_consumed_count_respects_thread_filter(self):
        r1 = self.engine.write("a", "i", "s", "alpha")
        r2 = self.engine.write("b", "i", "s", "beta")
        self.engine.mark_consumed([r1["_path"], r2["_path"]], consumed_by="real-reader")
        assert self.engine.consumed_count(thread="alpha") == 1
        assert self.engine.consumed_count(thread="nonexistent") == 0

    def test_build_arrival_state_reports_archive_when_all_consumed(self, tmp_path):
        """The specific scenario the 197-record forensics describes: every
        handoff consumed, unconsumed() empty. The boot text must say the
        archive is non-empty rather than reading identical to a fresh
        install with zero handoffs ever written."""
        from sovereign_stack.arrival_state import build_arrival_state, render_full
        from sovereign_stack.memory import ExperientialMemory
        from sovereign_stack.reflexive import ReflexiveSurface

        root = tmp_path / ".sovereign"
        (root / "chronicle").mkdir(parents=True)
        engine = HandoffEngine(root=str(root))
        r = engine.write("old intent", "predecessor", "s1", "general")
        # Signature ledger (2026-08-31): receipt is PER-READER, so the scenario
        # "this reader has already seen everything" means consumed/signed by
        # the booting reader. Legacy consumed_by counts as that signature.
        engine.mark_consumed([r["_path"]], consumed_by="test-reader")
        assert engine.unconsumed() == []

        experiential = ExperientialMemory(root=str(root / "chronicle"))
        reflexive = ReflexiveSurface(sovereign_root=root)
        state = build_arrival_state(
            root,
            reader="test-reader",
            profile="full",
            experiential=experiential,
            handoff_engine=engine,
            reflexive_surface=reflexive,
            spiral_summary={
                "session_id": "s",
                "current_phase": "p",
                "tool_call_count": 0,
                "reflection_depth": 0,
                "session_duration_seconds": 0.0,
            },
        )
        assert state.consumed_handoffs_count == 1
        text = render_full(state)
        assert "1 handoff(s) exist in the archive, already consumed" in text
        assert "Absence here is not evidence none were ever written" in text

    def test_build_arrival_state_omits_archive_line_when_truly_empty(self, tmp_path):
        """Fresh install, zero handoffs ever — must NOT claim an archive exists."""
        from sovereign_stack.arrival_state import build_arrival_state, render_full
        from sovereign_stack.memory import ExperientialMemory
        from sovereign_stack.reflexive import ReflexiveSurface

        root = tmp_path / ".sovereign"
        (root / "chronicle").mkdir(parents=True)
        engine = HandoffEngine(root=str(root))
        experiential = ExperientialMemory(root=str(root / "chronicle"))
        reflexive = ReflexiveSurface(sovereign_root=root)
        state = build_arrival_state(
            root,
            reader="test-reader",
            profile="full",
            experiential=experiential,
            handoff_engine=engine,
            reflexive_surface=reflexive,
            spiral_summary={
                "session_id": "s",
                "current_phase": "p",
                "tool_call_count": 0,
                "reflection_depth": 0,
                "session_duration_seconds": 0.0,
            },
        )
        assert state.consumed_handoffs_count == 0
        text = render_full(state)
        assert "already consumed" not in text
        assert "Either fresh start or previous instances didn't leave notes" in text

    def test_build_arrival_state_reports_truncation_past_the_cap(self, tmp_path):
        """The follow-on gap the advisor caught: unconsumed() caps at 20.
        Before this fix, "unknown" drained the queue every boot so the cap
        never bit. Now an unidentified reader (the documented boot call
        passes no source_instance) leaves handoffs pending instead of
        erasing them — correct, but it means the queue can now genuinely
        exceed 20, and a capped list must not silently read as complete.
        Write 25 handoffs, none consumed; the boot text must say so."""
        from sovereign_stack.arrival_state import build_arrival_state, render_full
        from sovereign_stack.memory import ExperientialMemory
        from sovereign_stack.reflexive import ReflexiveSurface

        root = tmp_path / ".sovereign"
        (root / "chronicle").mkdir(parents=True)
        engine = HandoffEngine(root=str(root))
        for i in range(25):
            engine.write(f"note {i}", "predecessor", f"s{i}", "general")
        experiential = ExperientialMemory(root=str(root / "chronicle"))
        reflexive = ReflexiveSurface(sovereign_root=root)
        state = build_arrival_state(
            root,
            reader="test-reader",
            profile="full",
            experiential=experiential,
            handoff_engine=engine,
            reflexive_surface=reflexive,
            spiral_summary={
                "session_id": "s",
                "current_phase": "p",
                "tool_call_count": 0,
                "reflection_depth": 0,
                "session_duration_seconds": 0.0,
            },
        )
        assert len(state.handoffs) == 20  # unconsumed()'s cap
        assert state.total_unconsumed_count == 25
        text = render_full(state)
        # Wording moved to "unsigned by you" with the signature ledger; the
        # guarded property — a capped list must never read as the complete
        # list — is unchanged.
        assert "showing 20 of 25 unsigned by you" in text

    def test_build_arrival_state_omits_truncation_note_under_the_cap(self, tmp_path):
        """Sanity: the ordinary case (few handoffs) must render exactly as
        before — no regression to the header format golden tests depend on."""
        from sovereign_stack.arrival_state import build_arrival_state, render_full
        from sovereign_stack.memory import ExperientialMemory
        from sovereign_stack.reflexive import ReflexiveSurface

        root = tmp_path / ".sovereign"
        (root / "chronicle").mkdir(parents=True)
        engine = HandoffEngine(root=str(root))
        engine.write("only one", "predecessor", "s1", "general")
        experiential = ExperientialMemory(root=str(root / "chronicle"))
        reflexive = ReflexiveSurface(sovereign_root=root)
        state = build_arrival_state(
            root,
            reader="test-reader",
            profile="full",
            experiential=experiential,
            handoff_engine=engine,
            reflexive_surface=reflexive,
            spiral_summary={
                "session_id": "s",
                "current_phase": "p",
                "tool_call_count": 0,
                "reflection_depth": 0,
                "session_duration_seconds": 0.0,
            },
        )
        assert state.total_unconsumed_count == 1
        text = render_full(state)
        assert "HANDOFFS FROM PREVIOUS INSTANCES (1)" in text
        assert "showing" not in text

    def test_archive_disclosed_even_when_pending_is_nonempty(self, tmp_path):
        """Caught in review: the first cut of this fix only disclosed the
        consumed archive when unconsumed() was EMPTY (the `else` branch).
        The live store's typical state is NOT empty — some handoffs are
        usually pending — so that placement alone would have silently
        failed to fix the headline defect ("249 permanently unreachable")
        on the very case that matters most. This is the case: one pending
        handoff AND a real archive behind it. Both must show."""
        from sovereign_stack.arrival_state import build_arrival_state, render_full
        from sovereign_stack.memory import ExperientialMemory
        from sovereign_stack.reflexive import ReflexiveSurface

        root = tmp_path / ".sovereign"
        (root / "chronicle").mkdir(parents=True)
        engine = HandoffEngine(root=str(root))
        old = engine.write("archived note", "predecessor", "s0", "general")
        # Same per-reader correction as above: the archive is what THIS reader
        # has already signed for; the pending handoff below is what it has not.
        engine.mark_consumed([old["_path"]], consumed_by="test-reader")
        engine.write("still pending", "predecessor", "s1", "general")

        experiential = ExperientialMemory(root=str(root / "chronicle"))
        reflexive = ReflexiveSurface(sovereign_root=root)
        state = build_arrival_state(
            root,
            reader="test-reader",
            profile="full",
            experiential=experiential,
            handoff_engine=engine,
            reflexive_surface=reflexive,
            spiral_summary={
                "session_id": "s",
                "current_phase": "p",
                "tool_call_count": 0,
                "reflection_depth": 0,
                "session_duration_seconds": 0.0,
            },
        )
        assert len(state.handoffs) == 1
        assert state.consumed_handoffs_count == 1
        text = render_full(state)
        assert "HANDOFFS FROM PREVIOUS INSTANCES (1)" in text
        assert "still pending" in text
        # Wording updated with the signature ledger; the PROPERTY is unchanged and is
        # what this guards: a non-empty pending list must not crowd out disclosure
        # that a pre-ledger archive exists.
        assert "1 handoff(s) carry a pre-ledger consumed_at stamp" in text


# ── 4. an unidentified reader must not crash boot, and must not erase ───────


class TestAnonymousReaderDoesNotConsume:
    def test_where_did_i_leave_off_default_reader_does_not_raise_and_does_not_consume(self):
        """source_instance omitted -> reader defaults to 'unknown' -> now an
        invalid consumed_by. The tool call must still succeed (boot must not
        break for a caller that didn't identify itself) and the handoff must
        remain unconsumed for the next, hopefully-identified, boot."""
        with _isolated_server("anon-reader-test") as (srv_mod, tmp_root):
            srv_mod.handoff_engine.write(
                note="pick this up",
                source_instance="predecessor",
                source_session_id="s1",
                thread="general",
            )
            assert len(srv_mod.handoff_engine.unconsumed()) == 1

            result = _run(srv_mod._dispatch_tool("where_did_i_leave_off", {}))
            text = result[0].text
            assert "WHERE DID I LEAVE OFF" in text

            # Still unconsumed — the anonymous call did NOT retire it.
            assert len(srv_mod.handoff_engine.unconsumed()) == 1

    def test_where_did_i_leave_off_named_reader_does_consume(self):
        """Control: a real source_instance still works end to end."""
        with _isolated_server("named-reader-test") as (srv_mod, tmp_root):
            srv_mod.handoff_engine.write(
                note="pick this up",
                source_instance="predecessor",
                source_session_id="s1",
                thread="general",
            )
            assert len(srv_mod.handoff_engine.unconsumed()) == 1

            _run(
                srv_mod._dispatch_tool(
                    "where_did_i_leave_off",
                    {"source_instance": "claude-sonnet-4-6-integrity-test"},
                )
            )

            # CONTRACT CHANGED 2026-08-31 (signature ledger): boot no longer
            # flips consumed_at, which retired the handoff for EVERY future
            # reader and lost 197 of them. It appends a SIGNATURE instead.
            reader = "claude-sonnet-4-6-integrity-test"
            # Gone from THIS reader's queue...
            assert srv_mod.handoff_engine.unsigned_by(reader) == []
            # ...and still visible to everyone else. This is the whole fix.
            assert len(srv_mod.handoff_engine.unsigned_by("some-other-seat")) == 1
            # The handoff file itself was never mutated.
            assert len(srv_mod.handoff_engine.unconsumed()) == 1
            sigs = srv_mod.handoff_engine.signatures()
            assert [x["signer"] for x in sigs] == [reader]


class TestHandoffActedOnToolSurfaceChange:
    """Second tool surface reached by the same validation, flagged by review:
    handoff_acted_on (server.py) only pre-checks for EMPTY handoff_path /
    consumed_by / what_was_done itself (returning ok-shaped text on empty —
    pre-existing, out of scope here) and otherwise calls straight through to
    handoff_engine.mark_acted_on(). A non-empty placeholder like
    consumed_by="unknown" passes that pre-check and now hits the new
    validation inside mark_acted_on, which raises ValueError. handle_tool
    re-raises after Nape observation (no swallowing) — consistent with the
    P1 fail-closed doctrine already applied to the sibling `handoff` tool,
    but this is a genuinely NEW failure mode for THIS tool (pre-fix,
    consumed_by="unknown" silently succeeded here)."""

    def test_handoff_acted_on_raises_on_placeholder_consumed_by(self):
        with _isolated_server("acted-on-placeholder-test") as (srv_mod, tmp_root):
            r = srv_mod.handoff_engine.write("note", "predecessor", "s1", "general")
            with pytest.raises(ValueError, match="does not identify a reader"):
                _run(
                    srv_mod._dispatch_tool(
                        "handoff_acted_on",
                        {
                            "handoff_path": r["_path"],
                            "consumed_by": "unknown",
                            "what_was_done": "read it",
                        },
                    )
                )
            # And it must not have half-written a record.
            assert srv_mod.handoff_engine.acted_on_records() == []

    def test_handoff_acted_on_still_succeeds_with_real_identity(self):
        """Control: the common, correct path is unaffected."""
        with _isolated_server("acted-on-control-test") as (srv_mod, tmp_root):
            r = srv_mod.handoff_engine.write("note", "predecessor", "s1", "general")
            result = _run(
                srv_mod._dispatch_tool(
                    "handoff_acted_on",
                    {
                        "handoff_path": r["_path"],
                        "consumed_by": "claude-sonnet-4-6-integrity-test",
                        "what_was_done": "read it",
                    },
                )
            )
            assert "read it" in result[0].text
