"""
Handoff forward-correction linkage (feat/handoff-forward-links, 2026-09-05).

THE GAP, found by a gpt-6-astra (Codex) seat auditing the newest 25 handoffs
against a "Set-Down" criterion and confirmed by HQ from disk: a handoff record
carried exactly seven fields (timestamp, source_instance, source_session_id,
thread, note, consumed_at, consumed_by) and NO correction linkage, so an older
handoff a later one had already corrected still read as current on every
surface. The live specimen is 52 seconds wide: ``20260902T112749_*`` says
"hq_module_audit exit 0"; ``20260902T112841_*`` corrects it to exit 1; nothing
connected them.

The shape of the fix mirrors the chronicle's own rule — corrections supersede,
never erase. The NEW record carries a backward pointer; the forward index is
COMPUTED at read time from the records themselves. There is no index file,
nothing to keep in sync, and the superseded file is never reopened for writing.

Containment: every test here is rooted at tmp_path. The engine additionally
refuses any write under the live ~/.sovereign during a pytest run
(_refuse_live_store_during_tests), and the byte-identity test below is the
direct proof that a superseding write does not touch its predecessor.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from sovereign_stack.handoff import HandoffEngine, format_handoff_for_surface

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def engine(tmp_path: Path) -> HandoffEngine:
    return HandoffEngine(root=str(tmp_path / ".sovereign"))


def _id(record: dict) -> str:
    return Path(record["_path"]).name


def _write(engine: HandoffEngine, note: str, **kw) -> dict:
    kw.setdefault("source_instance", "seat-under-test")
    kw.setdefault("source_session_id", "spiral_test")
    return engine.write(note=note, **kw)


# ── 1. The argument: accepted, validated, stored on the NEW record only ──────


class TestSupersedesArgument:
    def test_supersedes_accepted_and_stored(self, engine):
        first = _write(engine, "hq_module_audit exit 0")
        second = _write(engine, "correction: exit 1", supersedes=_id(first))

        assert second["supersedes"] == _id(first)
        on_disk = json.loads(Path(second["_path"]).read_text())
        assert on_disk["supersedes"] == _id(first)

    def test_supersedes_accepts_the_stem_without_json(self, engine):
        """A handoff gets named in prose without its extension — the audit
        that prompted this feature cited ``20260902T112749_*``. Both forms
        resolve to the store's own id (the filename), never to two ids."""
        first = _write(engine, "original")
        stem = _id(first).removesuffix(".json")
        second = _write(engine, "correction", supersedes=stem)
        assert second["supersedes"] == _id(first)

    def test_supersedes_accepts_a_full_path(self, engine):
        first = _write(engine, "original")
        second = _write(engine, "correction", supersedes=first["_path"])
        assert second["supersedes"] == _id(first)

    def test_supersedes_rejected_for_nonexistent_id(self, engine):
        with pytest.raises(ValueError, match="does not name a handoff in this store"):
            _write(engine, "correction", supersedes="20260101T000000_000000_ghost_general_abcdef")

    def test_list_shaped_reference_is_refused_with_a_readable_error(self, engine):
        """record_insight's `supersedes` is a list[str] (memory.py:1069) and
        this one is not, so a caller who knows the sibling API will send a
        list here. Before the type check that raised AttributeError, which
        server.py's `except ValueError` does not catch — the caller got a
        traceback where a refusal belonged. The REST path does not validate
        against the inputSchema, so "type": "string" does not close it."""
        first = _write(engine, "original")
        with pytest.raises(ValueError, match="must be a single handoff id"):
            _write(engine, "correction", supersedes=[_id(first)])

    def test_non_string_references_are_refused_not_crashed(self, engine):
        for bad in (123, {"id": "x"}, object()):
            with pytest.raises(ValueError, match="must be a single handoff id"):
                _write(engine, "correction", supersedes=bad)

    def test_rejected_supersedes_writes_no_file(self, engine):
        """Fail CLOSED. Validation happens before the write, so a bad
        correction link costs nothing and leaves no orphan behind — the
        opposite of writing first and discovering the link is dead later."""
        before = sorted(p.name for p in engine.root.glob("*.json"))
        with pytest.raises(ValueError):
            _write(engine, "correction", supersedes="nope")
        assert sorted(p.name for p in engine.root.glob("*.json")) == before

    def test_empty_supersedes_is_refused_not_dropped(self, engine):
        """An explicitly-passed empty link is a caller error, and a silently
        dropped one would report success on a correction that never landed —
        the fail-open shape this house hunts."""
        with pytest.raises(ValueError, match="names nothing"):
            _write(engine, "correction", supersedes="")

    def test_omitting_supersedes_leaves_the_seven_field_shape_untouched(self, engine):
        """The key is absent, not null. The 2026-09-05 audit measured the
        seven-field shape; an always-present "supersedes": null would change
        every record on disk for the sake of the rare one."""
        record = _write(engine, "ordinary handoff")
        on_disk = json.loads(Path(record["_path"]).read_text())
        assert "supersedes" not in on_disk
        assert set(on_disk) == {
            "timestamp",
            "source_instance",
            "source_session_id",
            "thread",
            "note",
            "consumed_at",
            "consumed_by",
        }


# ── 2. Corrections supersede, never erase ───────────────────────────────────


class TestPredecessorIsNeverMutated:
    def test_old_file_is_byte_identical_after_a_superseding_write(self, engine):
        first = _write(engine, "hq_module_audit exit 0")
        path = Path(first["_path"])
        before_bytes = path.read_bytes()
        before_mtime = path.stat().st_mtime_ns

        _write(engine, "correction: exit 1", supersedes=_id(first))

        assert path.read_bytes() == before_bytes
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            hashlib.sha256(before_bytes).hexdigest()
        )
        assert path.stat().st_mtime_ns == before_mtime

    def test_reading_the_index_does_not_touch_any_file(self, engine):
        first = _write(engine, "original")
        _write(engine, "correction", supersedes=_id(first))
        before = {p.name: p.read_bytes() for p in engine.root.glob("*.json")}

        engine.supersession_index()
        engine.all()
        engine.unconsumed()

        assert {p.name: p.read_bytes() for p in engine.root.glob("*.json")} == before

    def test_no_index_file_is_created(self, engine):
        """The forward index is computed, not stored. New state on disk would
        be one more thing that can go stale against the records."""
        first = _write(engine, "original")
        second = _write(engine, "correction", supersedes=_id(first))
        engine.supersession_index()
        engine.all()
        # Exactly the two handoff records — no index.json, no links.jsonl, no
        # sidecar of any kind. Asserted as an equality, not an absence of one
        # guessed filename, so a future index file of ANY name fails here.
        assert {p.name for p in engine.root.iterdir()} == {_id(first), _id(second)}


# ── 3. The computed forward index ───────────────────────────────────────────


class TestForwardIndex:
    def test_index_maps_superseded_id_to_its_correctors(self, engine):
        first = _write(engine, "original")
        second = _write(engine, "correction", supersedes=_id(first))

        index = engine.supersession_index()
        assert list(index) == [_id(first)]
        assert [s["handoff_id"] for s in index[_id(first)]] == [_id(second)]
        assert index[_id(first)][0]["timestamp"] == second["timestamp"]

    def test_records_carry_corrected_by_when_read(self, engine):
        first = _write(engine, "original")
        second = _write(engine, "correction", supersedes=_id(first))

        by_id = {_id(r): r for r in engine.all()}
        assert [s["handoff_id"] for s in by_id[_id(first)]["_corrected_by"]] == [_id(second)]
        # The corrector itself is not corrected by anyone.
        assert "_corrected_by" not in by_id[_id(second)]

    def test_uncorrected_records_gain_no_annotation(self, engine):
        _write(engine, "lonely handoff")
        assert "_corrected_by" not in engine.all()[0]

    def test_a_chain_links_each_link_separately(self, engine):
        first = _write(engine, "v1")
        second = _write(engine, "v2", supersedes=_id(first))
        third = _write(engine, "v3", supersedes=_id(second))

        index = engine.supersession_index()
        assert [s["handoff_id"] for s in index[_id(first)]] == [_id(second)]
        assert [s["handoff_id"] for s in index[_id(second)]] == [_id(third)]

    def test_two_correctors_of_one_record_are_listed_newest_first(self, engine):
        first = _write(engine, "original")
        a = _write(engine, "correction A", supersedes=_id(first))
        b = _write(engine, "correction B", supersedes=_id(first))

        stubs = engine.supersession_index()[_id(first)]
        assert [s["handoff_id"] for s in stubs] == [_id(b), _id(a)]

    def test_index_carries_stubs_not_copies_of_the_note(self, engine):
        """Point at the source, never mirror it (SOP #4): a cached copy of
        another record's body is a stale mirror waiting to happen."""
        first = _write(engine, "original")
        _write(engine, "the corrected text nobody should see duplicated", supersedes=_id(first))
        stub = engine.supersession_index()[_id(first)][0]
        assert set(stub) == {"handoff_id", "timestamp", "source_instance", "thread"}
        assert "note" not in stub


# ── 4. What a reader actually sees ──────────────────────────────────────────


class TestRenderedSurface:
    def test_superseded_record_renders_corrected_by(self, engine):
        first = _write(engine, "hq_module_audit exit 0")
        second = _write(engine, "correction: exit 1", supersedes=_id(first))

        by_id = {_id(r): r for r in engine.all()}
        rendered = format_handoff_for_surface(by_id[_id(first)])

        assert f"CORRECTED BY {_id(second)}" in rendered
        assert second["timestamp"] in rendered

    def test_the_banner_precedes_the_stale_note(self, engine):
        """Order is the whole point. A correction printed after the claim it
        corrects arrives once the reader has already believed the claim."""
        first = _write(engine, "hq_module_audit exit 0")
        _write(engine, "correction: exit 1", supersedes=_id(first))

        rendered = format_handoff_for_surface({r["_path"]: r for r in engine.all()}[first["_path"]])
        assert rendered.index("CORRECTED BY") < rendered.index("hq_module_audit exit 0")

    def test_superseding_record_renders_supersedes(self, engine):
        first = _write(engine, "original")
        second = _write(engine, "correction", supersedes=_id(first))

        by_id = {_id(r): r for r in engine.all()}
        assert f"supersedes {_id(first)}" in format_handoff_for_surface(by_id[_id(second)])

    def test_uncorrected_record_renders_exactly_as_before(self, engine):
        """Non-breaking: a record nobody corrected must render byte-identically
        to the pre-feature format, or every boot door's output shifts."""
        record = _write(engine, "ordinary", thread="some-thread")
        rendered = format_handoff_for_surface(engine.all()[0])
        expected = (
            f"• [thread: some-thread] Previous instance seat-under-test "
            f"(session spiral_test, {record['timestamp']}) left this note:\n"
            f'    "ordinary"'
        )
        assert rendered == expected

    def test_render_survives_a_record_read_straight_off_disk(self, engine):
        """A caller that json.loads a file itself gets no annotation and must
        still render — the banner is an addition, never a requirement."""
        first = _write(engine, "original")
        _write(engine, "correction", supersedes=_id(first))
        raw = json.loads(Path(first["_path"]).read_text())
        assert "CORRECTED BY" not in format_handoff_for_surface(raw)


# ── 5. End to end: the boot door a real seat reads ──────────────────────────


class TestBootDoorShowsTheCorrection:
    def test_where_did_i_leave_off_renders_both_directions(self, tmp_path: Path):
        import _phase4_fixture as fx

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)

        engine = HandoffEngine(root=str(root))
        first = engine.write(
            note="hq_module_audit exit 0",
            source_instance="hq-seat",
            source_session_id="spiral_test",
        )
        second = engine.write(
            note="correction: hq_module_audit exit 1",
            source_instance="hq-seat",
            source_session_id="spiral_test",
            supersedes=_id(first),
        )

        text = fx.run_door(
            root,
            "where_did_i_leave_off",
            {"consume": False, "source_instance": "phase4-reader", "full_content": False},
        )

        assert f"CORRECTED BY {_id(second)}" in text
        assert f"supersedes {_id(first)}" in text
        assert text.index("CORRECTED BY") < text.index("hq_module_audit exit 0")

    def test_handoff_archaeology_carries_the_linkage(self, tmp_path: Path):
        """The list surface inherits the index from the same read chokepoint —
        a correction visible on the boot door and invisible in the archive
        would be the fix-written-and-half-connected shape (SOP #12)."""
        import _phase4_fixture as fx

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)

        engine = HandoffEngine(root=str(root))
        first = engine.write(
            note="original", source_instance="hq-seat", source_session_id="spiral_test"
        )
        second = engine.write(
            note="correction",
            source_instance="hq-seat",
            source_session_id="spiral_test",
            supersedes=_id(first),
        )

        payload = json.loads(fx.run_door(root, "handoff_archaeology", {"limit": 50}))
        by_id = {Path(r["_path"]).name: r for r in payload["records"]}
        assert by_id[_id(second)]["supersedes"] == _id(first)
        assert [s["handoff_id"] for s in by_id[_id(first)]["_corrected_by"]] == [_id(second)]


# ── 6. The author guard (part C) ────────────────────────────────────────────


class TestAuthorGuardThroughTheTool:
    def test_handoff_tool_refuses_an_unnamed_author(self, tmp_path: Path):
        import _phase4_fixture as fx

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        before = len(list((root / "handoffs").glob("*.json")))

        with pytest.raises(ValueError, match="source_instance is required"):
            fx.run_door(root, "handoff", {"note": "an anonymous note"})

        assert len(list((root / "handoffs").glob("*.json"))) == before

    def test_handoff_tool_accepts_a_named_author(self, tmp_path: Path):
        import _phase4_fixture as fx

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)

        text = fx.run_door(
            root,
            "handoff",
            {"note": "a signed note", "source_instance": "HQ Mac Studio — claude-fable-5-1"},
        )
        assert "Handoff written" in text
        assert "HQ Mac Studio — claude-fable-5-1" in text

    def test_handoff_tool_reports_the_link_it_made(self, tmp_path: Path):
        import _phase4_fixture as fx

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        engine = HandoffEngine(root=str(root))
        first = engine.write(
            note="original", source_instance="hq-seat", source_session_id="spiral_test"
        )

        text = fx.run_door(
            root,
            "handoff",
            {"note": "correction", "source_instance": "hq-seat", "supersedes": _id(first)},
        )
        assert f"supersedes: {_id(first)}" in text

    def test_close_session_refuses_only_its_handoff_leg(self, tmp_path: Path):
        """close_session writes three things through two engines. An unnamed
        author must not cost the reflection — it costs the handoff, visibly.

        This is the one place the guard degrades rather than raises, and the
        degradation has to be LOUD: a swallowed rejection here would be the
        fail-open the guard exists to close, wearing a different hat.
        """
        import _phase4_fixture as fx

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        before = len(list((root / "handoffs").glob("*.json")))

        text = fx.run_door(
            root,
            "close_session",
            {"what_i_learned": "something real", "what_to_pick_up": "an anonymous note"},
        )

        assert "Reflection recorded" in text
        assert "Handoff rejected" in text
        assert "source_instance is required" in text
        assert len(list((root / "handoffs").glob("*.json"))) == before

    def test_close_session_writes_the_handoff_when_named(self, tmp_path: Path):
        import _phase4_fixture as fx

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        before = len(list((root / "handoffs").glob("*.json")))

        text = fx.run_door(
            root,
            "close_session",
            {
                "what_i_learned": "something real",
                "what_to_pick_up": "a signed note",
                "source_instance": "hq-seat",
            },
        )

        assert "Handoff written" in text
        assert len(list((root / "handoffs").glob("*.json"))) == before + 1

    def test_handoff_tool_declares_supersedes_in_its_schema(self):
        """A tool that accepts an argument its schema does not declare is
        unreachable by every well-behaved caller (and, since fd73258's
        unknown-key rejection, a hard error for the guarded ones)."""
        import asyncio

        from sovereign_stack import server as srv

        tools = {t.name: t for t in asyncio.run(srv.list_tools())}
        props = tools["handoff"].inputSchema["properties"]
        assert "supersedes" in props
        assert props["supersedes"]["type"] == "string"
        # No "default" key: test_contract_walker walks every default-bearing
        # param and would try to call handoff with it.
        assert "default" not in props["supersedes"]
