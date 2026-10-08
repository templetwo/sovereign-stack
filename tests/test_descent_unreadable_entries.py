"""
THE DESCENT — an entry the reader cannot use is UNREADABLE, never absent, and
one odd record never takes the voice down (fix round 1, 2026-10-08).

Origin. The adversarial fail-closed review of 21eb7e8 proposed
test_descent_unreadable_entries.py (sha256 66da42b3439143e8…): the shard and
handoff entry kinds and the sidecar the parser cannot finish are its tests,
adopted here. The builder's probes of 21eb7e8 widened them: a handoff symlink
LOOP made DirEntry.is_file() raise and took the whole voice down instead of
being counted; a JSONL line or a handoff file nested past the parser's
recursion limit raised RecursionError (not a ValueError) out of the gather and
out of descend() raw; one oddly typed field (a list session id, a number for
text) raised TypeError or AttributeError out of the whole read; one
undecodable byte made the whole shard unreadable, and an integer past the
conversion limit raised a plain ValueError; a stamp at the edge of the
calendar raised OverflowError, and a sidecar cursor of 0001-01-01T00:00Z
validated and then crashed the whole full door at render time; a boot door
read "nothing" over a store whose every close line was unreadable; and the
foyer printed its count without the rows it skipped.

Never a door in a FIFO case: the HANDOFFS section's reader and the memory
readers open a FIFO and block, a hazard that predates THE DESCENT. Those cases
run the gather, the renderers and descend() directly.

Synthetic only (tests/_phase4_fixture.py); the module is imported inside each
test so a tree without the fix fails per test, not at collection.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import _phase4_fixture as fx  # noqa: E402

HEADER = "━━━ THE DESCENT ━━━"
DISTILLATE = "2026-07-02_fixture-distill"
GEN = "2026-07-20T12:00:00Z"
BOMB = "[" * 200_000  # nesting the C scanner cannot finish: RecursionError, not a ValueError


def _d():
    from sovereign_stack import descent

    return descent


def _block(text: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index(HEADER)
    out = [HEADER]
    for line in lines[start + 1 :]:
        if line.startswith("━━━"):
            break
        out.append(line)
    return out


def _as_of(text: str) -> str:
    return text.split("━━━ AS OF ━━━", 1)[1].split("━━━", 1)[0]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / ".sovereign"
    fx.build_fixture(r)
    ids = fx.plant_close_store(r)
    fx.plant_distillate(r, [ids["a1"], ids["a2"], ids["b1"]])
    return r


def _make(path: Path, kind: str, root: Path) -> None:
    if path.exists() or path.is_symlink():
        path.unlink()
    if kind == "dangling-symlink":
        os.symlink(root / "nowhere" / path.name, path)
    elif kind == "symlink-loop":
        os.symlink(path.name, path)  # points at itself: DirEntry.is_file() raises ELOOP
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)


KINDS = ("dangling-symlink", "directory", "fifo", "symlink-loop")


# ── an entry that is not a regular file ──────────────────────────────────────


@pytest.mark.parametrize("kind", KINDS)
def test_a_shard_that_is_not_a_regular_file_is_unreadable_not_absent(root, kind):
    # symlink-loop is green on 21eb7e8 by design: is_file() raised ELOOP there
    # and the shard reader raised with it. The other three were read as absent.
    _make(root / "chronicle" / "insights" / "reflection" / "zz-entry.jsonl", kind, root)
    with pytest.raises(OSError):
        _d().gather_descent(root, depth="full")
    with pytest.raises(OSError):
        _d().gather_descent(root, depth="foyer")
    with pytest.raises(ValueError, match="the close store could not be read"):
        _d().descend(sovereign_root=root, month="2026-07")
    if kind != "fifo":
        foyer = fx.run_door(root, "arrive", {})
        assert "The Descent:" not in foyer
        assert "the_descent unavailable" in _as_of(foyer)


@pytest.mark.parametrize("kind", KINDS)
def test_a_handoff_entry_that_is_not_a_regular_file_is_counted_unreadable(root, kind):
    _make(root / "handoffs" / "descent_fixture_b2.json", kind, root)
    state = _d().gather_descent(root, depth="full")
    assert len(state.closes) == 4
    assert state.handoffs_unreadable == 1
    block = _d().render_descent(state, generated_at=GEN)
    assert block[6] == "    handed forward: (none joined; 1 handoff file could not be read)"
    july = _d().descend(sovereign_root=root, month="2026-07").splitlines()
    i = july.index("  HANDED FORWARD")  # b2, the newest, renders first
    assert july[i + 1] == "    (none joined; 1 handoff file could not be read)"
    assert july[-1] == (
        "COVERAGE: 2 of 2 shown (offset 0, limit 10). 0 unreadable rows skipped; "
        "1 handoff file unreadable."
    )


# ── what the parser cannot finish ────────────────────────────────────────────


def test_a_sidecar_the_parser_cannot_finish_makes_the_index_unknown_not_the_voice(root):
    side = root / "chronicle" / "distillates" / f"{DISTILLATE}.index.json"
    side.write_text(BOMB)  # under the 1 MiB cap
    state = _d().gather_descent(root, depth="full")
    assert state.index_error is not None and "RecursionError" in state.index_error
    assert len(state.closes) == 4
    assert "distillation unknown" in _d().descend(sovereign_root=root, month="2026-07")
    by_seat = _d().descend(sovereign_root=root, seat="beta")
    assert by_seat.startswith("THE DESCENT — 1 close in this selection")
    with pytest.raises(ValueError, match="the distillate index could not be trusted"):
        _d().descend(sovereign_root=root, shape="R1")
    doors = fx.run_all_doors(root)
    block = _block(doors["full"])
    assert block[1].startswith("  4 closes on record")
    assert any(
        x.startswith("  Distillation: unknown this call; the distillate index could not be trusted")
        for x in block
    ), block
    assert "the_descent distillate index unavailable" in _as_of(doors["full"])
    assert "the_descent unavailable" not in _as_of(doors["full"])
    assert "distillation unknown this call (see AS OF)" in doors["foyer"]


LINES = {
    "nesting-past-the-recursion-limit": BOMB.encode() + b"\n",
    "undecodable-utf8": (
        b'{"timestamp": "2026-07-04T10:00:00+00:00", "domain": "reflection", '
        b'"content": "a bad \xff byte", "close_session": true}\n'
    ),
    "integer-past-the-conversion-limit": b"1" * 5000 + b"\n",
}


@pytest.mark.parametrize("kind", sorted(LINES))
def test_a_line_that_will_not_decode_or_parse_is_a_skipped_row_not_a_fatal_one(root, kind):
    shard = root / "chronicle" / "insights" / "reflection" / "descent-fixture.jsonl"
    with open(shard, "ab") as fh:
        fh.write(LINES[kind])
    state = _d().gather_descent(root, depth="full")
    assert (len(state.closes), state.skipped_rows) == (4, 1)
    block = _d().render_descent(state, generated_at=GEN)
    assert block[1].endswith("a close does not record its machine. 1 unreadable row skipped.")
    foyer = _d().render_descent_foyer_line(
        _d().gather_descent(root, depth="foyer"), generated_at=GEN
    )
    assert foyer.startswith(
        "  The Descent: 4 closes on record (1 unreadable row skipped), newest 2026-07-03;"
    )
    july = _d().descend(sovereign_root=root, month="2026-07")
    assert july.splitlines()[-1].endswith("1 unreadable row skipped; 0 handoff files unreadable.")


@pytest.mark.parametrize(
    "cursor",
    ("0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59-05:00"),
    ids=("year-zero-in-eastern", "past-the-calendar-in-utc"),
)
def test_a_sidecar_time_the_house_clock_cannot_show_makes_the_index_unknown(tmp_path, cursor):
    r = tmp_path / ".sovereign"
    fx.build_fixture(r)
    ids = fx.plant_close_store(r)
    path = fx.plant_distillate(r, [ids["a1"], ids["a2"], ids["b1"]])
    doc = json.loads(path.read_text())
    doc["cursor"] = cursor  # only the cursor: "created" stays a valid time
    path.write_text(json.dumps(doc))
    reason = f"{DISTILLATE}.index.json: cursor is outside the range the house clock can show"
    assert _d().gather_descent(r, depth="full").index_error == reason
    full = fx.run_door(r, "where_did_i_leave_off", {"consume": False, "source_instance": "r"})
    block = _block(full)
    assert block[1].startswith("  4 closes on record")
    assert (
        f"  Distillation: unknown this call; the distillate index could not be trusted "
        f"({reason}). The counts above are live and stand."
    ) in block
    assert "the_descent distillate index unavailable" in _as_of(full)
    assert _d().descend(sovereign_root=r).startswith("THE DESCENT — index")


def test_a_handoff_file_the_parser_cannot_finish_is_counted_not_fatal(root):
    (root / "handoffs" / "zz-bomb.json").write_text(BOMB)
    state = _d().gather_descent(root, depth="full")
    assert (len(state.closes), state.handoffs_unreadable) == (4, 1)
    block = _d().render_descent(state, generated_at=GEN)
    assert block[6] == "    handed forward: Beta hands on: ask for the second seat."
    july = _d().descend(sovereign_root=root, month="2026-07")
    assert july.splitlines()[-1].endswith("0 unreadable rows skipped; 1 handoff file unreadable.")


# ── one odd record ───────────────────────────────────────────────────────────

_SESSION_B = fx.DESCENT_SESSION_B
# label -> (path under the root, the record, which count it lands in). The
# surprise and handoff records sit NEARER to close b2 than b2's own legs, so a
# reader that joined them would show them; set aside, b2 joins its own.
ODD = {
    "close-content-not-text": (
        "chronicle/insights/reflection/zz-odd.jsonl",
        {
            "timestamp": "2026-07-03T11:00:00+00:00",
            "domain": "reflection",
            "content": 7,
            "session_id": "descentfixture_odd",
            "source_instance": "fixture-seat-odd",
            "close_session": True,
        },
        "skipped_rows",
    ),
    "close-session-id-not-text": (
        "chronicle/insights/reflection/zz-odd.jsonl",
        {
            "timestamp": "2026-07-03T11:00:00+00:00",
            "domain": "reflection",
            "content": "An odd close whose session id is a list.",
            "session_id": [_SESSION_B],
            "source_instance": "fixture-seat-odd",
            "close_session": True,
        },
        "skipped_rows",
    ),
    "surprise-content-not-text": (
        "chronicle/insights/surprise/zz-odd.jsonl",
        {
            "timestamp": "2026-07-03T12:00:00.100000+00:00",
            "domain": "surprise",
            "content": ["not", "text"],
            "session_id": _SESSION_B,
        },
        "skipped_rows",
    ),
    "surprise-session-id-not-text": (
        "chronicle/insights/surprise/zz-odd.jsonl",
        {
            "timestamp": "2026-07-03T12:00:00.100000+00:00",
            "domain": "surprise",
            "content": "An odd surprise whose session id is a list.",
            "session_id": [_SESSION_B],
        },
        "skipped_rows",
    ),
    "handoff-note-not-text": (
        "handoffs/zz_odd.json",
        {"timestamp": "2026-07-03T08:00:00.050000", "source_session_id": _SESSION_B, "note": 123},
        "handoffs_unreadable",
    ),
    "handoff-session-id-not-text": (
        "handoffs/zz_odd.json",
        {
            "timestamp": "2026-07-03T08:00:00.050000",
            "source_session_id": [_SESSION_B],
            "note": "An odd pick-up whose session id is a list.",
        },
        "handoffs_unreadable",
    ),
    # Stamps the house clock cannot show. A naive 9999-12-31T23:30 is Eastern
    # and crosses into year 10000 in UTC; 0001-01-01T00:00Z is year 0 in Eastern.
    "close-stamp-past-the-calendar": (
        "chronicle/insights/reflection/zz-odd.jsonl",
        {
            "timestamp": "9999-12-31T23:30:00",
            "domain": "reflection",
            "content": "An odd close stamped at the end of the calendar.",
            "session_id": "descentfixture_odd",
            "source_instance": "fixture-seat-odd",
            "close_session": True,
        },
        "skipped_rows",
    ),
    "close-stamp-off-the-eastern-clock": (
        "chronicle/insights/reflection/zz-odd.jsonl",
        {
            "timestamp": "0001-01-01T00:00:00+00:00",
            "domain": "reflection",
            "content": "An odd close stamped at the start of the calendar.",
            "session_id": "descentfixture_odd",
            "source_instance": "fixture-seat-odd",
            "close_session": True,
        },
        "skipped_rows",
    ),
    "surprise-stamp-past-the-calendar": (
        "chronicle/insights/surprise/zz-odd.jsonl",
        {
            "timestamp": "9999-12-31T23:30:00",
            "domain": "surprise",
            "content": "An odd surprise stamped at the end of the calendar.",
            "session_id": _SESSION_B,
        },
        "skipped_rows",
    ),
    "handoff-stamp-past-the-calendar": (
        "handoffs/zz_odd.json",
        {
            "timestamp": "9999-12-31T23:30:00",
            "source_session_id": _SESSION_B,
            "note": "An odd pick-up stamped at the end of the calendar.",
        },
        "handoffs_unreadable",
    ),
}


@pytest.mark.parametrize("label", sorted(ODD))
def test_one_odd_typed_record_is_counted_and_never_takes_the_voice_down(root, label):
    rel, record, counter = ODD[label]
    path = root / rel
    if path.suffix == ".jsonl":
        fx._write_jsonl(path, [record])
    else:
        path.write_text(json.dumps(record))
    state = _d().gather_descent(root, depth="full")
    assert len(state.closes) == 4
    expected = (1, 0) if counter == "skipped_rows" else (0, 1)
    assert (state.skipped_rows, state.handoffs_unreadable) == expected
    block = _d().render_descent(state, generated_at=GEN)
    assert block[5] == "    surprised: Beta was surprised the check caught it first."
    assert block[6] == "    handed forward: Beta hands on: ask for the second seat."
    july = _d().descend(sovereign_root=root, month="2026-07")
    assert july.startswith("THE DESCENT — 2 closes in this selection")
    assert _d().descend(sovereign_root=root).startswith("THE DESCENT — index")
    full = fx.run_door(root, "where_did_i_leave_off", {"consume": False, "source_instance": "r"})
    assert "the_descent unavailable" not in _as_of(full)
    assert _block(full)[1].startswith("  4 closes on record")


# ── partiality is stated, never read as absence ──────────────────────────────


def test_no_readable_close_beside_skipped_rows_degrades_instead_of_reading_empty(tmp_path):
    r = tmp_path / ".sovereign"
    fx.build_fixture(r)
    (r / "chronicle" / "insights" / "reflection" / "zz-corrupt.jsonl").write_text(
        '{"timestamp": "2026-07-04T10:00:00+00:00", "domain": "reflection", "close_session": true\n'
        '{"timestamp": "not a time", "domain": "reflection", "close_session": true, '
        '"content": "A close whose stamp nobody can read."}\n'
    )
    state = _d().gather_descent(r, depth="full")
    assert (state.closes, state.skipped_rows) == ([], 2)
    doors = fx.run_all_doors(r)
    doors["full_compact"] = fx.run_door(
        r,
        "where_did_i_leave_off",
        {"consume": False, "source_instance": "phase4-reader", "compact": True},
    )
    for name, text in doors.items():
        assert HEADER not in text, name
        assert "The Descent:" not in text, name
        assert "the_descent unavailable" in _as_of(text), name
        assert "Freshness: incomplete" in _as_of(text), name
        assert "0 closes" not in text, name
    # descend() states the same reading, with its count
    assert _d().descend(sovereign_root=r).splitlines()[1].endswith("2 unreadable rows skipped.")


def test_the_foyer_line_says_when_rows_were_skipped(root):
    shard = root / "chronicle" / "insights" / "reflection" / "descent-fixture.jsonl"
    with open(shard, "a", encoding="utf-8") as fh:
        fh.write('{"timestamp": "2026-07-04T10:00:00+00:00", "close_session": true\n')
    foyer = fx.run_door(root, "arrive", {})
    assert (
        "  The Descent: 4 closes on record (1 unreadable row skipped), newest 2026-07-03; "
        "1 undistilled, due (descend())"
    ) in foyer.splitlines()
    assert _block(fx.run_all_doors(root)["full"])[1].endswith(" 1 unreadable row skipped.")


def test_a_surprise_that_did_not_join_is_not_claimed_unwritten(root):
    s = root / "chronicle" / "insights" / "surprise" / "descent-fixture.jsonl"
    rows = [json.loads(x) for x in s.read_text().splitlines() if x.strip()]
    for row in rows:
        if row["content"].startswith("Beta was surprised"):
            row["timestamp"] = "2026-07-03T12:00:11.000000+00:00"  # 11 s after its close
    s.write_text("".join(json.dumps(x) + "\n" for x in rows))
    said = "(no surprise joined: none from this session within 10 seconds)"
    block = _block(fx.run_all_doors(root)["full"])
    assert block[5] == f"    surprised: {said}"
    july = fx.run_door(root, "descend", {"month": "2026-07"}).splitlines()
    i = july.index("  SURPRISED")  # b2, the newest, renders first
    assert july[i + 1] == f"    {said}"
    assert "(none written)" not in "\n".join(block + july)
    assert any("Beta was surprised" in x for x in s.read_text().splitlines())  # still on disk


# ── a count of one is singular (build spec 2(e)) ─────────────────────────────


@pytest.mark.parametrize(
    "months,fold",
    ((13, " (+1 earlier month, 1 close)"), (14, " (+2 earlier months, 2 closes)")),
    ids=("one-folded", "two-folded"),
)
def test_the_arc_fold_counts_one_in_the_singular(tmp_path, months, fold):
    # two-folded is the control: plural on 21eb7e8 and here alike.
    r = tmp_path / ".sovereign"
    fx.build_fixture(r)
    rows = []
    for k in range(months):
        year, month0 = divmod(2025 * 12 + 7 + k, 12)  # 2025-08 onward
        rows.append(
            {
                "timestamp": f"{year:04d}-{month0 + 1:02d}-15T12:00:00+00:00",
                "domain": "reflection",
                "content": f"Arc close {k}.",
                "session_id": f"descentfixture_arc_{k}",
                "source_instance": "fixture-seat-arc",
                "close_session": True,
            }
        )
    fx._write_jsonl(r / "chronicle" / "insights" / "reflection" / "descent-arc.jsonl", rows)
    block = _d().render_descent(_d().gather_descent(r, depth="full"), generated_at=GEN)
    assert block[2].startswith("  By month: ")
    assert block[2].endswith(fold)


def test_descend_counts_one_in_the_singular(root):
    shard = root / "chronicle" / "insights" / "reflection" / "descent-fixture.jsonl"
    with open(shard, "a", encoding="utf-8") as fh:
        fh.write('{"truncated": \n')
    (root / "handoffs" / "zz_corrupt.json").write_text("{not json")
    july = _d().descend(sovereign_root=root, month="2026-07").splitlines()
    assert july[-1] == (
        "COVERAGE: 2 of 2 shown (offset 0, limit 10). 1 unreadable row skipped; "
        "1 handoff file unreadable."
    )
    assert "    (none joined; 1 handoff file could not be read)" in july  # b1 has no pick-up
    index = _d().descend(sovereign_root=root).splitlines()
    assert index[1].endswith("(Eastern). 1 unreadable row skipped.")
