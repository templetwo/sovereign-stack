"""
original_timestamp — BACKDATE IN PLACE, and keep the write instant beside it.

TASK #7, decided by Anthony 2026-06-19 and unbuilt until now. `record_insight`
stamped `datetime.now()` and offered no way to say otherwise, so anything filed
after the fact — a bridge proposal drained days later, an imported note, a
lived entry written up the next morning — landed under the date it was typed.

THE RULING REJECTS THE OBVIOUS ALTERNATIVE BY NAME. "Add an occurred_at and
leave timestamp alone" was considered and refused, because it "lets any
un-taught reader silently miss them": recency-ordered recall, the date-bounded
readers, the boot surfaces and the dashboards all key on `timestamp`, and
teaching each of them a second field is a migration with a long tail of readers
that never learn — every one of which goes on being confidently wrong. So
`timestamp` is set to the authorship time (zero readers to teach), `occurred_at`
keeps the real write instant, and `timestamp_source` says out loud that the row
was stamped rather than observed. Both axes survive. This is the shape the
2026-06-19 vault restore already used on 1,024 rows.

THE VALIDATION IS THE POINT. A silently-dropped original_timestamp is worse
than no parameter at all: the caller is told ok:true and the entry carries the
filing instant as if it were the authorship time. Every rejection below is
asserted, because a gate never shown to reject is decoration (experimental
law #2).

THE COST, PINNED HERE SO IT CANNOT BE REDISCOVERED THE HARD WAY: `timestamp` is
in `derive_claim_id`'s preimage, so backdating CHANGES the entry's claim id.
Harmless on a new write — no id existed yet. Load-bearing on a backfill over
entries other records already cite, which is why
scripts/backfill_occurred_at.py reports citations and defaults to a dry run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory


def _mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(str(tmp_path / ".sovereign"))


def _last_entry(path: str) -> dict:
    lines = [ln for ln in Path(path).read_text().splitlines() if ln.strip()]
    return json.loads(lines[-1])


class TestBothAxesSurvive:
    def test_timestamp_becomes_the_authorship_time(self, tmp_path):
        """The half that needs no reader taught anything."""
        mem = _mem(tmp_path)
        entry = _last_entry(
            mem.record_insight("d", "c", original_timestamp="2026-06-19T05:18:19+00:00")
        )
        assert entry["timestamp"] == "2026-06-19T05:18:19+00:00"

    def test_occurred_at_keeps_the_real_write_instant(self, tmp_path):
        """The provenance half. Without it the backdate is a lie with no
        receipt — the ruling insists both axes are kept."""
        mem = _mem(tmp_path)
        before = datetime.now(timezone.utc)
        entry = _last_entry(mem.record_insight("d", "c", original_timestamp="2025-03-04"))
        after = datetime.now(timezone.utc)
        written = datetime.fromisoformat(entry["occurred_at"])
        assert before <= written <= after

    def test_timestamp_source_names_the_substitution(self, tmp_path):
        """A reader must be able to tell a stamped row from an observed one
        WITHOUT inferring it from a date mismatch."""
        mem = _mem(tmp_path)
        entry = _last_entry(mem.record_insight("d", "c", original_timestamp="2025-03-04"))
        assert entry["timestamp_source"] == "original_timestamp"

    def test_date_only_is_stored_verbatim(self, tmp_path):
        """`ground.record_catch` writes date-only values and its readers slice
        [:10]. Normalizing here would invent a time-of-day nobody claimed."""
        mem = _mem(tmp_path)
        assert (
            _last_entry(mem.record_insight("d", "c", original_timestamp="2026-07-12"))["timestamp"]
            == "2026-07-12"
        )

    def test_absent_leaves_the_entry_exactly_as_before(self, tmp_path):
        mem = _mem(tmp_path)
        entry = _last_entry(mem.record_insight("d", "c"))
        assert "occurred_at" not in entry
        assert "timestamp_source" not in entry
        assert entry["timestamp"].startswith(str(datetime.now(timezone.utc).year))

    def test_an_ordinary_reader_finds_it_at_the_authorship_date(self, tmp_path):
        """The whole justification for backdating, exercised rather than
        asserted: a date-bounded recall that knows nothing about occurred_at
        still returns the entry under the date the thing happened."""
        mem = _mem(tmp_path)
        mem.record_insight("dom", "backdated body", original_timestamp="2025-03-04T12:00:00+00:00")
        hits = mem.recall_insights(
            domain="dom", start_date="2025-03-01", end_date="2025-03-31", limit=50
        )
        bodies = [
            h.get("content") for h in (hits.get("insights") if isinstance(hits, dict) else hits)
        ]
        assert "backdated body" in bodies


class TestItRejectsRatherThanDrops:
    @pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45", "", "   ", "19/06/2026"])
    def test_unparseable_is_refused(self, tmp_path, bad):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError, match="original_timestamp"):
            mem.record_insight("d", "c", original_timestamp=bad)

    def test_non_string_is_refused(self, tmp_path):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError, match="original_timestamp"):
            mem.record_insight("d", "c", original_timestamp=20260619)

    def test_before_the_floor_is_refused(self, tmp_path):
        """A mistyped year is far likelier than a genuine pre-2024 observation,
        and a bad one sorts to the bottom of every surface forever."""
        mem = _mem(tmp_path)
        with pytest.raises(ValueError, match="before 2024-01-01"):
            mem.record_insight("d", "c", original_timestamp="1026-06-19")

    def test_far_future_is_refused(self, tmp_path):
        mem = _mem(tmp_path)
        ahead = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        with pytest.raises(ValueError, match="in the future"):
            mem.record_insight("d", "c", original_timestamp=ahead)

    def test_small_clock_skew_is_allowed(self, tmp_path):
        """A seat on a slightly fast clock must not be refused."""
        mem = _mem(tmp_path)
        ahead = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        assert _last_entry(mem.record_insight("d", "c", original_timestamp=ahead))["timestamp"] == (
            ahead
        )

    def test_a_refused_value_writes_nothing(self, tmp_path):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError):
            mem.record_insight("dom", "c", original_timestamp="nope")
        assert not (tmp_path / ".sovereign" / "chronicle" / "insights" / "dom").exists()


class TestTheClaimIdConsequenceIsReal:
    """Not a hazard to be avoided — a fact to be stated, because the backfill
    script's whole citation report exists to price it."""

    def test_backdating_changes_the_claim_id(self, tmp_path):
        from sovereign_stack import provenance

        mem = _mem(tmp_path)
        plain = _last_entry(mem.record_insight("d", "c"))
        stamped = _last_entry(mem.record_insight("d", "c2", original_timestamp="2025-03-04"))
        assert provenance.derive_claim_id(plain) != provenance.derive_claim_id(stamped)

    def test_the_id_derives_from_the_authorship_stamp_not_the_write_instant(self, tmp_path):
        from sovereign_stack import provenance

        mem = _mem(tmp_path)
        entry = _last_entry(mem.record_insight("d", "c", original_timestamp="2025-03-04"))
        expected = provenance.derive_claim_id(
            {"timestamp": "2025-03-04", "domain": "d", "content": "c"}
        )
        assert provenance.derive_claim_id(entry) == expected

    def test_occurred_at_is_outside_the_preimage(self, tmp_path):
        """So the provenance half can be added to an existing row without
        re-addressing it — the ONLY reason a backfill can ever be partial."""
        from sovereign_stack import provenance

        base = {"timestamp": "2026-09-01T00:00:00+00:00", "domain": "d", "content": "c"}
        assert provenance.derive_claim_id(base) == provenance.derive_claim_id(
            dict(base, occurred_at="2026-06-19", timestamp_source="original_timestamp")
        )


class TestTheSiblingWritePaths:
    def test_open_thread_backdates_and_keeps_both(self, tmp_path):
        mem = _mem(tmp_path)
        entry = _last_entry(
            mem.record_open_thread("q?", "ctx", "dom", original_timestamp="2026-06-19")
        )
        assert entry["timestamp"] == "2026-06-19"
        assert entry["timestamp_source"] == "original_timestamp"
        assert entry["occurred_at"]

    def test_open_thread_id_stays_on_the_write_instant(self, tmp_path):
        """thread_id is an opaque address other records point at, not a
        sortable field. Re-deriving it from a backdated stamp would move an id
        for no reader's benefit."""
        mem = _mem(tmp_path)
        entry = _last_entry(
            mem.record_open_thread("q?", "ctx", "dom", original_timestamp="2026-06-19")
        )
        assert entry["thread_id"]

    def test_learning_backdates_and_keeps_both(self, tmp_path):
        mem = _mem(tmp_path)
        entry = _last_entry(
            mem.record_learning("happened", "learned", "dom", original_timestamp="2026-06-19")
        )
        assert entry["timestamp"] == "2026-06-19"
        assert entry["occurred_at"]
        assert entry["timestamp_source"] == "original_timestamp"

    @pytest.mark.parametrize("bad", ["whenever", "1999-01-01"])
    def test_both_siblings_refuse_a_bad_value(self, tmp_path, bad):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError, match="original_timestamp"):
            mem.record_open_thread("q?", "", "dom", original_timestamp=bad)
        with pytest.raises(ValueError, match="original_timestamp"):
            mem.record_learning("h", "l", "dom", original_timestamp=bad)


class TestTheSchemaDeclaresIt:
    """The bridge-blindness lesson, with teeth since `_reject_unknown_params`:
    a parameter absent from the published schema is not merely unreachable by
    schema-constrained callers, it is actively REFUSED on arrival."""

    @pytest.mark.parametrize(
        "tool_name", ["record_insight", "record_learning", "record_open_thread"]
    )
    def test_declared(self, tool_name):
        import asyncio

        from sovereign_stack import server

        tool = next(t for t in asyncio.run(server.list_tools()) if t.name == tool_name)
        assert "original_timestamp" in (tool.inputSchema or {}).get("properties", {})

    def test_record_insight_no_longer_rejects_it_as_unknown(self):
        import asyncio

        from sovereign_stack import server

        asyncio.run(
            server._reject_unknown_params(
                "record_insight",
                {"domain": "d", "content": "c", "original_timestamp": "2026-06-19"},
            )
        )

    def test_the_guard_still_rejects_a_genuinely_unknown_key(self):
        """Law #2 on the guard itself: shown to accept, and shown to refuse."""
        import asyncio

        from sovereign_stack import server

        with pytest.raises(ValueError, match="unknown parameter"):
            asyncio.run(
                server._reject_unknown_params(
                    "record_insight", {"domain": "d", "content": "c", "originl_timestamp": "x"}
                )
            )


class TestTheSchemaSaysTheClaimIdConsequenceOnlyWhereItIsTRUE:
    """One description, three tools, and the claim-id clause is NOT shared.

    The three inputSchema entries were byte-identical 1,490-char pastes, and
    all three carried "NOTE: `timestamp` is in the claim_id preimage, so this
    changes the id this entry would otherwise have had."

    THAT CLAUSE IS FALSE FOR TWO OF THEM. `derive_claim_id` is only ever
    applied to entries reached through `provenance.iter_chronicle_entries`,
    which globs `insights/**/*.jsonl` + `_quarantine_*/**/*.jsonl` under the
    chronicle root. `record_learning` writes to `learnings/<applies_to>.jsonl`
    and `record_open_thread` to `open_threads/<domain>.jsonl` — outside that
    glob, and never passed to derive_claim_id anywhere in the tree. Telling a
    caller that backdating a learning changes an id it does not have is false
    precision, and false precision on a cost is what stops a correct call from
    being made.

    The tests below assert the WRITE LOCATIONS, not just the prose, so the
    schema cannot quietly become right-for-the-wrong-reason if a write path
    moves.
    """

    _CLAIM_CLAUSE = "`timestamp` is in the claim_id preimage"

    @staticmethod
    def _description(tool_name: str) -> str:
        import asyncio

        from sovereign_stack import server

        tools = asyncio.run(server.list_tools())
        tool = next(t for t in tools if t.name == tool_name)
        return tool.inputSchema["properties"]["original_timestamp"]["description"]

    def test_record_insight_states_it(self):
        assert self._CLAIM_CLAUSE in self._description("record_insight")

    @pytest.mark.parametrize("tool", ["record_learning", "record_open_thread"])
    def test_the_siblings_do_not_state_it(self, tool):
        d = self._description(tool)
        assert self._CLAIM_CLAUSE not in d
        assert "NOT addressed by a derived claim_id" in d

    def test_all_three_still_share_the_common_contract(self):
        """Hoisting must not have let the three texts drift; only the tail
        differs."""
        common = "the real write instant is kept as `occurred_at`"
        for tool in ("record_insight", "record_learning", "record_open_thread"):
            d = self._description(tool)
            assert common in d
            assert "must be >= 2024-01-01" in d
            assert "REJECTS" in d

    def test_a_learning_lands_OUTSIDE_the_tree_claim_ids_are_derived_over(self, tmp_path):
        """The fact the schema now rests on."""
        mem = _mem(tmp_path)
        path = Path(mem.record_learning("applies", "lesson", original_timestamp="2026-05-25"))
        root = tmp_path / ".sovereign"
        assert path.parent == root / "learnings"
        assert path not in set(root.glob("insights/**/*.jsonl"))
        assert path not in set(root.glob("_quarantine_*/**/*.jsonl"))

    def test_an_open_thread_lands_outside_it_too(self, tmp_path):
        mem = _mem(tmp_path)
        path = Path(mem.record_open_thread("q", "ctx", "d", original_timestamp="2026-05-25"))
        root = tmp_path / ".sovereign"
        assert path.parent == root / "open_threads"
        assert path not in set(root.glob("insights/**/*.jsonl"))

    def test_iter_chronicle_entries_does_not_see_either_of_them(self, tmp_path):
        """The mechanism, asserted directly rather than inferred from paths."""
        from sovereign_stack import provenance

        mem = _mem(tmp_path)
        mem.record_insight("dom", "an insight")
        mem.record_learning("applies", "a learning")
        mem.record_open_thread("a thread question", "ctx", "dom")
        seen = [e for e, _f, _loc in provenance.iter_chronicle_entries(tmp_path / ".sovereign")]
        contents = {e.get("content") for e in seen}
        assert "an insight" in contents
        assert "a learning" not in contents
        assert all(e.get("question") != "a thread question" for e in seen)

    def test_an_insight_IS_addressed_by_a_derived_id(self, tmp_path):
        """The inverse: the clause is true where it is stated."""
        from sovereign_stack import provenance

        mem = _mem(tmp_path)
        mem.record_insight("dom", "an insight")
        seen = list(provenance.iter_chronicle_entries(tmp_path / ".sovereign"))
        assert len(seen) == 1
        assert provenance.derive_claim_id(seen[0][0])
