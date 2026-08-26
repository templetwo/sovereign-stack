"""
The unacked-honk counter must not saturate.

2026-08-26: the dashboard reported `len(read_recent_honks(..., limit=100))`
as its unacked count. That saturates at 100. The real backlog was 1,884 —
a 19x understatement rendered as a plain integer with no truncation
signal, sitting inside the health surface that was supposed to catch
exactly this class of lie.

Every test here asserts the counter can exceed the preview cap.
"""

from __future__ import annotations

import json

from sovereign_stack.dashboard import count_unacked_honks, read_recent_honks


def _write(tmp_path, honks, acks=()):
    d = tmp_path / "nape"
    d.mkdir(exist_ok=True)
    (d / "honks.jsonl").write_text("".join(json.dumps(h) + "\n" for h in honks), encoding="utf-8")
    (d / "acks.jsonl").write_text("".join(json.dumps(a) + "\n" for a in acks), encoding="utf-8")
    return d / "honks.jsonl"


def test_counter_exceeds_the_preview_cap(tmp_path):
    """The regression. 250 unacked must report 250, not 100."""
    honks = [{"honk_id": f"h{i}", "pattern": "x"} for i in range(250)]
    p = _write(tmp_path, honks)
    assert count_unacked_honks(p) == 250
    assert len(read_recent_honks(p, limit=100)) == 100


def test_counter_honours_cross_file_acks(tmp_path):
    honks = [{"honk_id": f"h{i}"} for i in range(120)]
    acks = [{"honk_id": f"h{i}"} for i in range(20)]
    p = _write(tmp_path, honks, acks)
    assert count_unacked_honks(p) == 100


def test_counter_honours_inline_acks(tmp_path):
    honks = [{"honk_id": "a", "ack_id": "done"}, {"honk_id": "b"}]
    p = _write(tmp_path, honks)
    assert count_unacked_honks(p) == 1


def test_missing_file_is_zero_not_an_error(tmp_path):
    assert count_unacked_honks(tmp_path / "nope" / "honks.jsonl") == 0


def test_malformed_lines_are_skipped_not_counted(tmp_path):
    d = tmp_path / "nape"
    d.mkdir()
    (d / "honks.jsonl").write_text(
        '{"honk_id": "a"}\nnot json\n\n{"honk_id": "b"}\n', encoding="utf-8"
    )
    (d / "acks.jsonl").write_text("", encoding="utf-8")
    assert count_unacked_honks(d / "honks.jsonl") == 2


def test_preview_and_counter_agree_on_what_acked_means(tmp_path):
    """Two implementations of 'is this acked?' is how a count and a list drift."""
    honks = [{"honk_id": f"h{i}"} for i in range(10)]
    acks = [{"honk_id": "h0"}, {"honk_id": "h1"}]
    p = _write(tmp_path, honks, acks)
    assert count_unacked_honks(p) == len(read_recent_honks(p, limit=999)) == 8
