"""Supersession-ledger read-path fail-open closes (D1 + D2).

The supersession ledger (`~/.sovereign/chronicle/supersessions.jsonl`) is an
append-only event log; `fold_supersessions` folds it in memory at read and
discards it — nothing authoritative is derived, which is deliberate. But
that means the ledger itself is the ONLY record of what is currently
superseded, and two failure shapes were previously silent:

  D1 — LEDGER LOSS reads as "nothing was ever superseded." A lost,
       mis-rooted, or partially-restored supersessions.jsonl folds to {},
       and `memory.finalize_read`'s Stage A was purely data-gated: empty
       fold -> entries pass through untouched, with zero signal that the
       ledger USED to say otherwise. Fix: if the fold is empty but a
       returned entry still carries its `supersedes` breadcrumb (denormalized
       onto the successor at write time, memory.py's record_insight), that
       entry is annotated `_ledger_suspect: True` and, when the caller wired
       an envelope (recall_insights), `partial_reasons` names it.

  D2 — CORRUPT LEDGER LINES were skipped silently by `load_supersessions`
       (matching the general chronicle read convention, which is fine for
       insight shards but not for the one file that decides what's live).
       A single truncated line for predecessor X drops X's supersession
       from the fold with ZERO signal, even though the ledger otherwise has
       other valid records (so D1's fold-empty path does NOT fire — this is
       the complementary gap). Fix: `load_supersessions` returns
       `(records, corrupt_count)`; a nonzero count surfaces as
       `supersession_ledger_corrupt_line_skipped:<n>` in `partial_reasons`.

Per standing law #2 (a gate must demonstrably be able to FAIL on the
unfixed base): every test below asserts the FIXED/desired behavior, which
must fail cleanly (AssertionError on a missing signal, not a TypeError on
an API shape) against the pre-fix code. Run in isolation against HEAD
b96efb8 (before this change) to see the red first.

Hermetic — everything lives under tmp_path; ~/.sovereign is never touched,
never even referenced.
"""

from pathlib import Path

import pytest

from sovereign_stack import provenance as prov
from sovereign_stack.memory import ExperientialMemory


@pytest.fixture
def chronicle(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def _claim_id_of(chronicle: ExperientialMemory, content: str) -> str:
    matches = [
        prov.derive_claim_id(entry)
        for entry, _file, _loc in prov.iter_chronicle_entries(chronicle.root)
        if entry.get("content") == content
    ]
    assert len(matches) == 1, f"expected exactly one entry with content {content!r}"
    return matches[0]


def _entry_with(insights: list[dict], content: str) -> dict:
    hits = [i for i in insights if i.get("content") == content]
    assert len(hits) == 1, f"expected exactly one item with content {content!r}, got {len(hits)}"
    return hits[0]


# ── D1 — ledger loss (fold reduces to empty) ────────────────────────────────


class TestLedgerLossDrill:
    def test_emptied_ledger_flags_the_orphaned_successor_and_names_it_in_partial_reasons(
        self, chronicle
    ):
        # Build a REAL supersession through the public write path.
        chronicle.record_insight(domain="pair", content="the predecessor", session_id="s1")
        pred_id = _claim_id_of(chronicle, "the predecessor")
        chronicle.record_insight(
            domain="pair",
            content="the successor",
            session_id="s2",
            supersedes=[pred_id],
            carry_forward_summary="what the predecessor still teaches",
        )

        # Sanity: with the ledger intact, today's annotation already works.
        env = chronicle.recall_insights(domain="pair", envelope=True)
        pred = _entry_with(env["items"], "the predecessor")
        assert pred["_superseded_by"]

        # Simulate ledger loss (moved/emptied/corrupted-to-nothing) —
        # entirely inside tmp_path, ~/.sovereign is never touched.
        chronicle.supersessions_path.write_text("", encoding="utf-8")

        env = chronicle.recall_insights(domain="pair", envelope=True)
        succ = _entry_with(env["items"], "the successor")

        # THE FIX'S CONTRACT — must fail on unfixed code (no `_ledger_suspect`
        # key is ever set today, and this reason string does not exist yet).
        assert succ.get("_ledger_suspect") is True, (
            "the successor still carries its `supersedes` breadcrumb; an "
            "empty fold must not read as 'nothing was ever superseded'"
        )
        assert "supersession-ledger-missing-but-chronicle-references-it" in env["partial_reasons"]

    def test_load_entries_still_annotates_even_without_an_envelope(self, chronicle):
        """finalize_read's other callers (load_entries, ground.py) don't
        build an envelope, but the annotation is data-level and must still
        land on the entry itself — the out-param is additive, never a
        prerequisite for the annotation."""
        from sovereign_stack.memory import load_entries

        chronicle.record_insight(domain="pair", content="the predecessor", session_id="s1")
        pred_id = _claim_id_of(chronicle, "the predecessor")
        chronicle.record_insight(
            domain="pair",
            content="the successor",
            session_id="s2",
            supersedes=[pred_id],
            carry_forward_summary="carried",
        )
        chronicle.supersessions_path.write_text("", encoding="utf-8")

        entries = load_entries(chronicle.root)
        succ = _entry_with(entries, "the successor")
        assert succ.get("_ledger_suspect") is True

    def test_never_superseded_chronicle_is_untouched_byte_identity_preserved(self, chronicle):
        """A chronicle that never called supersede has no `supersedes`
        breadcrumbs anywhere, so the D1 scan must be a true no-op — the
        v1.6.2 byte-identity guarantee (ledger-free chronicle reads exactly
        as raw JSONL) must survive this change."""
        chronicle.record_insight(domain="plain", content="just an entry", session_id="s1")
        env = chronicle.recall_insights(domain="plain", envelope=True)
        entry = _entry_with(env["items"], "just an entry")
        assert "_ledger_suspect" not in entry
        assert env["partial_reasons"] == []

    def test_ledger_suspect_annotation_is_a_copy_never_a_mutation(self, tmp_path):
        """Matches the sibling contract pinned in test_provenance.py
        (`assert "_superseded_by" not in pred  # copies, never mutation`):
        finalize_read must never write a derived key into the CALLER's
        original dict. `result = entries` at the top of finalize_read means
        the D1 branch is operating on the caller's own objects unless it
        explicitly copies before annotating."""
        from sovereign_stack.memory import finalize_read

        original = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "domain": "pair",
            "content": "the successor",
            "intensity": 0.5,
            "layer": "hypothesis",
            "supersedes": ["a" * 64],
        }
        root = tmp_path / "chronicle"
        root.mkdir()
        # No supersessions.jsonl at all -> fold is empty -> D1 branch runs.
        result = finalize_read([original], root)

        assert result[0].get("_ledger_suspect") is True  # the fix still fires
        assert "_ledger_suspect" not in original  # but never on the caller's dict
        assert result[0] is not original  # a distinct copy was returned


# ── D2 — corrupt ledger lines (fold stays non-empty; the complementary gap) ─


class TestCorruptLedgerLineDrill:
    def test_truncated_line_is_counted_and_surfaced_in_partial_reasons(self, chronicle):
        # Two REAL, independent supersessions -> two ledger lines.
        chronicle.record_insight(domain="a", content="pred one", session_id="s1")
        pred1 = _claim_id_of(chronicle, "pred one")
        chronicle.record_insight(
            domain="a",
            content="succ one",
            session_id="s1",
            supersedes=[pred1],
            carry_forward_summary="carried one",
        )
        chronicle.record_insight(domain="b", content="pred two", session_id="s1")
        pred2 = _claim_id_of(chronicle, "pred two")
        chronicle.record_insight(
            domain="b",
            content="succ two",
            session_id="s1",
            supersedes=[pred2],
            carry_forward_summary="carried two",
        )

        lines = chronicle.supersessions_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        # Truncate the SECOND record mid-JSON — pred_two's supersession
        # becomes unreadable while pred_one's stays intact, so the fold
        # is NON-empty overall (D1's fold-empty path does not fire here).
        lines[1] = lines[1][: len(lines[1]) // 2]
        chronicle.supersessions_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        env = chronicle.recall_insights(envelope=True)
        pred_one = _entry_with(env["items"], "pred one")
        pred_two = _entry_with(env["items"], "pred two")

        # Confirms the corpse: pred_two reads live because its ledger line
        # is gone, even though pred_one (the surviving line) still folds.
        assert pred_one["_superseded_by"]
        assert "_superseded_by" not in pred_two

        # THE FIX'S CONTRACT — must fail on unfixed code (load_supersessions
        # returns a bare list today; no corrupt-count exists anywhere).
        assert "supersession_ledger_corrupt_line_skipped:1" in env["partial_reasons"]

    def test_load_supersessions_returns_records_and_corrupt_count(self, tmp_path):
        """Direct unit check on the primitive itself, independent of the
        recall_insights envelope plumbing."""
        ledger = tmp_path / "supersessions.jsonl"
        good = prov.build_supersession_record(
            action="supersede",
            superseded_id="a" * 64,
            successor_id="b" * 64,
            carry_forward_summary="fine",
        )
        ledger.write_text(
            "\n".join(
                [
                    __import__("json").dumps(good),
                    "{this is not valid json",
                    "",  # blank lines are not corrupt, just skipped
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        records, corrupt_count = prov.load_supersessions(ledger)
        assert records == [good]
        assert corrupt_count == 1

    def test_missing_ledger_returns_empty_and_zero_corrupt(self, tmp_path):
        ledger = tmp_path / "nowhere" / "supersessions.jsonl"
        records, corrupt_count = prov.load_supersessions(ledger)
        assert records == []
        assert corrupt_count == 0
        assert not ledger.parent.exists()  # reading never creates
