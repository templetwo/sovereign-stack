"""
The handoff tool nudges toward `supersedes` (lane 2026-10-17_descent-strengthen,
item D).

The pick-up correction marker THE DESCENT shows fires only through
`supersedes`, and 10 of 418 handoffs on disk ever used it. When `supersedes`
was NOT passed and the same source_instance already has an earlier handoff on
the same thread, the tool's result names that file in one added line. Nothing
else changes: the write, the record, and every other line are as before.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import _phase4_fixture as fx  # noqa: E402

NUDGE = "Earlier handoff by this seat on this thread: "


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / ".sovereign"
    fx.build_fixture(r)
    return r


def _hand(root: Path, **args) -> str:
    return fx.run_door(root, "handoff", args)


def _newest_name(root: Path, seat_slug: str) -> str:
    return sorted(p.name for p in (root / "handoffs").glob(f"*_{seat_slug}_*.json"))[-1]


def test_a_second_handoff_on_the_same_thread_names_the_first(root):
    _hand(root, note="first pick-up", source_instance="nudge-seat", thread="nudge-thread")
    first = _newest_name(root, "nudge-seat")
    text = _hand(root, note="second pick-up", source_instance="nudge-seat", thread="nudge-thread")
    line = next(x for x in text.splitlines() if x.startswith(NUDGE))
    assert line == (f"{NUDGE}{first}. If this one corrects its pick-up, pass supersedes={first}.")


def test_the_nudge_names_the_newest_earlier_handoff_not_the_one_just_written(root):
    for n in (1, 2):
        _hand(root, note=f"pick-up {n}", source_instance="nudge-seat", thread="nudge-thread")
    second = _newest_name(root, "nudge-seat")
    text = _hand(root, note="pick-up 3", source_instance="nudge-seat", thread="nudge-thread")
    third = _newest_name(root, "nudge-seat")
    line = next(x for x in text.splitlines() if x.startswith(NUDGE))
    assert second in line and third not in line


def test_no_nudge_on_a_first_handoff(root):
    text = _hand(root, note="only one", source_instance="nudge-seat", thread="nudge-thread")
    assert NUDGE not in text


def test_no_nudge_for_another_seat_or_another_thread(root):
    _hand(root, note="a", source_instance="nudge-seat", thread="nudge-thread")
    other_seat = _hand(root, note="b", source_instance="other-seat", thread="nudge-thread")
    other_thread = _hand(root, note="c", source_instance="nudge-seat", thread="other-thread")
    assert NUDGE not in other_seat
    assert NUDGE not in other_thread


def test_no_nudge_when_supersedes_was_passed(root):
    _hand(root, note="a", source_instance="nudge-seat", thread="nudge-thread")
    first = _newest_name(root, "nudge-seat")
    text = _hand(
        root, note="b", source_instance="nudge-seat", thread="nudge-thread", supersedes=first
    )
    assert NUDGE not in text
    assert f"supersedes: {first}" in text


def test_the_nudge_carries_no_declare_or_error_word(root):
    from sovereign_stack.nape_daemon import _result_has_error_word, contains_declare_word

    _hand(root, note="a", source_instance="nudge-seat", thread="nudge-thread")
    text = _hand(root, note="b", source_instance="nudge-seat", thread="nudge-thread")
    line = next(x for x in text.splitlines() if x.startswith(NUDGE))
    assert not contains_declare_word(line)
    assert not _result_has_error_word(line)
