"""
The retry-dedup guard must read the WRITE INSTANT on both sides — including
out of a backdated row, where it is not in `timestamp`.

THE DEFECT. `record_insight(original_timestamp=...)` implements Anthony's
2026-06-19 backdate-in-place ruling: the AUTHORSHIP time goes into `timestamp`
(so no reader has to be taught a second field) and the real write instant moves
to `occurred_at`, with `timestamp_source` naming the substitution. `_dedup_hit`
was never taught the other half of that trade: it compared the incoming write's
"now" against the stored `timestamp`, which for every backdated row is the
authorship time. A proposal filed in May and drained in September produced a
delta of ~100 days against a 120-second window, so the guard could not fire —
on precisely the writes the house had just taught to carry provenance. A client
retry landed as a second byte-identical entry, silently, and dedup reported
nothing because dedup never ran.

WHY THE FIX IS CONDITIONAL, AND WHY THAT CONDITION IS THE TEST THAT MATTERS.
"Prefer `occurred_at` when present" is the obvious repair and it is wrong.
`occurred_at` is an overloaded field: `ground.record_catch` writes it as the
human EVENT date — routinely date-only, "2026-08-14" — and stamps no
`timestamp_source`. On the live store all 1,059 rows carrying `occurred_at`
lack that marker. Reading it unconditionally would hand the probe a naive date
for every Ground row and every legacy import and turn a working guard off
across the whole corpus. `timestamp_source` is the only field that asserts
"this row's `timestamp` is not its write instant", so it is the only thing
allowed to redirect the read. TestTheConditionIsLoadBearing is the pin: drop
the condition and it goes red.

Naive/date-only stored values are answered with "no dedup hit", never with a
TypeError out of a read-only probe.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sovereign_stack.memory import TIMESTAMP_SOURCE_ORIGINAL, ExperientialMemory


def _mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(str(tmp_path / ".sovereign"))


def _shard(tmp_path: Path, domain: str) -> Path:
    return tmp_path / ".sovereign" / "insights" / domain


def _rows(tmp_path: Path, domain: str) -> list[dict]:
    d = _shard(tmp_path, domain)
    out: list[dict] = []
    for f in sorted(d.glob("*.jsonl")):
        out += [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
    return out


# The dedup probe reads the last line of ONE shard — `insights/<domain>/
# <session_id>.jsonl` — so a seeded fixture row only reaches the probe if it
# sits in the shard the subsequent write will open. Every seeded test therefore
# names its session explicitly on both sides.
SEEDED_SESSION = "session_seeded_fixture"


def _seed_row(tmp_path: Path, domain: str, row: dict, session: str = SEEDED_SESSION) -> Path:
    """Write ONE hand-built last-entry, so the probe's input is exact.

    Going through record_insight to build the fixture would couple the test to
    the very write path under test; a row placed by hand states the on-disk
    shape the probe must cope with and nothing else.
    """
    d = _shard(tmp_path, domain)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{session}.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return path


class TestABackdatedRetryDedups:
    """(a) — the defect, stated as the two calls that produced it."""

    def test_two_identical_backdated_writes_land_once(self, tmp_path):
        # session_id pinned: the shard name is second-granular, so an unpinned
        # pair straddling a second boundary opens a second file and could not
        # dedup for a reason that has nothing to do with what is under test.
        mem = _mem(tmp_path)
        first = mem.record_insight(
            "dom", "same content", original_timestamp="2026-05-25", session_id=SEEDED_SESSION
        )
        second = mem.record_insight(
            "dom", "same content", original_timestamp="2026-05-25", session_id=SEEDED_SESSION
        )

        rows = _rows(tmp_path, "dom")
        assert len(rows) == 1, f"backdated retry appended a duplicate: {rows}"
        # And the caller is TOLD it deduped rather than silently handed a path.
        assert getattr(second, "deduped", False) is True
        assert getattr(first, "deduped", False) is False

    def test_the_surviving_entry_is_the_first_one(self, tmp_path):
        mem = _mem(tmp_path)
        mem.record_insight(
            "dom", "c", original_timestamp="2026-05-25T14:00:00+00:00", session_id=SEEDED_SESSION
        )
        hit = mem.record_insight(
            "dom", "c", original_timestamp="2026-05-25T14:00:00+00:00", session_id=SEEDED_SESSION
        )
        assert hit.existing_entry["timestamp"] == "2026-05-25T14:00:00+00:00"
        assert hit.existing_entry["timestamp_source"] == TIMESTAMP_SOURCE_ORIGINAL

    def test_a_full_iso_backdate_dedups_too(self, tmp_path):
        """Date-only is the sharper case (naive), but the aware spelling is the
        one the bridges actually forward — both must dedup."""
        mem = _mem(tmp_path)
        ts = "2026-05-25T14:00:00+00:00"
        mem.record_insight("dom", "c", original_timestamp=ts, session_id=SEEDED_SESSION)
        mem.record_insight("dom", "c", original_timestamp=ts, session_id=SEEDED_SESSION)
        assert len(_rows(tmp_path, "dom")) == 1

    def test_the_guard_can_still_NOT_fire(self, tmp_path):
        """Law #2 on the dedup probe: shown to fire, and shown to hold off.

        A stale backdated row — same content, write instant far outside the
        window — is a deliberate re-recording, not a retry, and must append.
        """
        old = datetime.now(timezone.utc) - timedelta(hours=6)
        _seed_row(
            tmp_path,
            "dom",
            {
                "timestamp": "2026-05-25",
                "occurred_at": old.isoformat(),
                "timestamp_source": TIMESTAMP_SOURCE_ORIGINAL,
                "domain": "dom",
                "content": "c",
                "layer": "hypothesis",
            },
        )
        _mem(tmp_path).record_insight(
            "dom", "c", original_timestamp="2026-05-25", session_id=SEEDED_SESSION
        )
        assert len(_rows(tmp_path, "dom")) == 2


class TestTheConditionIsLoadBearing:
    """(b) — the Ground shape. THIS IS THE REGRESSION PIN.

    Remove the `timestamp_source == TIMESTAMP_SOURCE_ORIGINAL` condition from
    `_write_instant_of` — i.e. prefer `occurred_at` whenever it is present —
    and every test in this class goes red, because a Ground row's `occurred_at`
    is a naive human event date and its `timestamp` is the aware write instant.
    """

    def test_a_ground_shaped_row_still_dedups_a_retry(self, tmp_path):
        now = datetime.now(timezone.utc)
        _seed_row(
            tmp_path,
            "the-ground,catch,human",
            {
                # The write instant, where an unstamped row always keeps it.
                "timestamp": now.isoformat(),
                # The HUMAN EVENT DATE. Date-only, naive, and NOT a write
                # instant — the field record_catch has always written.
                "occurred_at": "2026-08-14",
                "domain": "the-ground,catch,human",
                "content": "caught it",
                "layer": "ground_truth",
            },
        )
        _mem(tmp_path).record_insight(
            "the-ground,catch,human",
            "caught it",
            layer="ground_truth",
            session_id=SEEDED_SESSION,
        )
        rows = _rows(tmp_path, "the-ground,catch,human")
        assert len(rows) == 1, (
            "an unstamped occurred_at was read as the write instant — the dedup "
            f"guard is off for every Ground row and every legacy import: {rows}"
        )

    def test_the_live_shape_via_record_catch(self, tmp_path):
        """The same thing through the real writer, not a hand-built row.

        TWO THINGS THIS TEST HAS TO SAY OUT LOUD OR IT PROVES NOTHING.

        1. `record_catch` RETURNS its rejections as a string; it does not
           raise. A typo'd argument therefore makes both calls no-ops, the
           store stays empty, and a naive "no duplicate" assertion passes on a
           write that never happened. The first draft of this test did exactly
           that (anthony_present="yes" is not in the enum). Both returns are
           asserted to be the success shape first.
        2. THE SHARD IS SECOND-GRANULAR. `record_insight` names the file
           `session_<YYYYmmdd_HHMMSS>.jsonl` when no session_id is given, and
           the dedup probe reads the last line of THAT ONE FILE — so a retry
           straddling a second boundary opens a fresh shard and cannot dedup at
           all. `record_catch` forwards no session_id, so a straddled pair
           legitimately writes two rows. Retry on a fresh root rather than
           assert through the race; name it if it wins every attempt.
        """
        from sovereign_stack import ground

        def _pair(root: Path) -> tuple[bool, int]:
            kwargs = {
                "caught": "a wrong number",
                "caught_by": "anthony",
                "direction": "human",
                "occurred_at": "2026-08-14",
                "would_have_cost": "a false claim in a filing",
                "actual_cost": "none",
                "anthony_present": "present",
                "content": "Anthony caught the seat quoting an unmeasured total.",
                "vantage": "human_attestation",
                "chronicle_root": str(root),
            }
            first = ground.record_catch(**kwargs)
            second = ground.record_catch(**kwargs)
            for r in (first, second):
                assert r.startswith("\u2693"), f"record_catch refused instead of writing: {r}"
            same_shard = first.rsplit(" ", 1)[-1] == second.rsplit(" ", 1)[-1]
            d = root / "insights" / "the-ground,catch,human"
            written = sum(
                1
                for f in sorted(d.glob("*.jsonl"))
                for ln in f.read_text().splitlines()
                if ln.strip()
            )
            return same_shard, written

        for attempt in range(4):
            same_shard, written = _pair(tmp_path / f"root{attempt}")
            if same_shard:
                assert written == 1, f"record_catch retry duplicated: {written} rows"
                return
        raise AssertionError("every attempt straddled a second boundary — nothing was exercised")


class TestNaiveStoredValuesDoNotRaise:
    """(c) — a pin, not a demonstration of a new fix.

    `_dedup_hit` already carried a `try/except TypeError` before this change,
    so a naive stored `timestamp` did not crash on the unfixed code either;
    nothing pinned it. This class is that pin, and it also covers the new
    aware/naive mismatch branch, which returns before the subtraction rather
    than relying on the exception.
    """

    def test_a_naive_previous_timestamp_appends_instead_of_raising(self, tmp_path):
        _seed_row(
            tmp_path,
            "dom",
            {
                "timestamp": "2026-09-02T10:00:00",  # no offset — naive
                "domain": "dom",
                "content": "c",
                "layer": "hypothesis",
            },
        )
        _mem(tmp_path).record_insight("dom", "c")  # must not raise
        assert len(_rows(tmp_path, "dom")) == 2

    def test_a_date_only_previous_timestamp_appends_instead_of_raising(self, tmp_path):
        _seed_row(
            tmp_path,
            "dom",
            {
                "timestamp": "2026-09-02",
                "domain": "dom",
                "content": "c",
                "layer": "hypothesis",
            },
        )
        _mem(tmp_path).record_insight("dom", "c", session_id=SEEDED_SESSION)
        assert len(_rows(tmp_path, "dom")) == 2

    def test_a_backdated_row_with_no_occurred_at_falls_back_and_does_not_raise(self, tmp_path):
        """A malformed row — the marker set, the field missing — is read
        exactly as it was before `_write_instant_of` existed."""
        _seed_row(
            tmp_path,
            "dom",
            {
                "timestamp": "2026-05-25",
                "timestamp_source": TIMESTAMP_SOURCE_ORIGINAL,
                "domain": "dom",
                "content": "c",
                "layer": "hypothesis",
            },
        )
        _mem(tmp_path).record_insight("dom", "c", session_id=SEEDED_SESSION)
        assert len(_rows(tmp_path, "dom")) == 2

    def test_an_unparseable_previous_timestamp_appends(self, tmp_path):
        _seed_row(
            tmp_path,
            "dom",
            {
                "timestamp": "who knows",
                "domain": "dom",
                "content": "c",
                "layer": "hypothesis",
            },
        )
        _mem(tmp_path).record_insight("dom", "c", session_id=SEEDED_SESSION)
        assert len(_rows(tmp_path, "dom")) == 2


class TestOrdinaryDedupIsUnchanged:
    """The un-backdated path must be byte-for-byte the behaviour it had."""

    def test_a_plain_retry_still_dedups(self, tmp_path):
        mem = _mem(tmp_path)
        mem.record_insight("dom", "c", session_id=SEEDED_SESSION)
        mem.record_insight("dom", "c", session_id=SEEDED_SESSION)
        assert len(_rows(tmp_path, "dom")) == 1

    def test_different_content_is_not_a_duplicate(self, tmp_path):
        mem = _mem(tmp_path)
        mem.record_insight("dom", "c1", session_id=SEEDED_SESSION)
        mem.record_insight("dom", "c2", session_id=SEEDED_SESSION)
        assert len(_rows(tmp_path, "dom")) == 2
