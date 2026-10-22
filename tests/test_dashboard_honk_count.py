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


# ── 2026-10-21: an informational honk is not a backlog ─────────────────────
#
# Nape writes `level: "satisfied"` (pattern `clean_verify_declare`,
# nape_daemon.PATTERN_LEVELS) when a declaration WAS preceded by a verify call:
# a positive signal, nothing to acknowledge. Nape's own summary() has never
# counted those as unacknowledged. The console did: every one raised
# `unacked_honks`, and the push link handed HQ's session a batch for it
# (measured 2026-10-21: one satisfied honk, one batch). 710 of 4906 honks on
# disk that night were level satisfied.

SATISFIED = {"level": "satisfied", "pattern": "clean_verify_declare"}


def test_informational_honk_is_not_an_unacked_honk(tmp_path):
    """The regression: one satisfied honk, nothing to ack, so nothing counted."""
    p = _write(tmp_path, [{"honk_id": "s1", **SATISFIED}])
    assert count_unacked_honks(p) == 0
    assert read_recent_honks(p, limit=5) == []


def test_informational_excluded_from_count_and_preview_alike(tmp_path):
    """Count and preview drop the same records, so they still agree."""
    honks = [
        {"honk_id": "a", "level": "sharp", "pattern": "declare_before_verify"},
        {"honk_id": "s1", **SATISFIED},
        {"honk_id": "b", "level": "uneasy", "pattern": "repeated_mistake"},
        {"honk_id": "s2", **SATISFIED},
    ]
    p = _write(tmp_path, honks)
    preview = read_recent_honks(p, limit=999)
    assert [h["honk_id"] for h in preview] == ["b", "a"]
    assert count_unacked_honks(p) == len(preview) == 2


def test_the_informational_count_is_exposed_not_dropped(tmp_path):
    """What the count leaves out is its own number; an acked one is in neither."""
    from sovereign_stack.dashboard import count_informational_unacked_honks

    honks = [
        {"honk_id": "a", "level": "sharp"},
        {"honk_id": "s1", **SATISFIED},
        {"honk_id": "s2", **SATISFIED},
        {"honk_id": "s3", **SATISFIED},
    ]
    p = _write(tmp_path, honks, acks=[{"honk_id": "s3"}])
    assert count_unacked_honks(p) == 1
    assert count_informational_unacked_honks(p) == 2


def test_a_honk_with_no_level_still_wants_an_ack(tmp_path):
    """Fail closed: a shape the predicate cannot read stays counted."""
    p = _write(
        tmp_path,
        [
            {"honk_id": "a"},
            {"honk_id": "b", "level": None},
            {"honk_id": "c", "level": ["satisfied"]},
        ],
    )
    assert count_unacked_honks(p) == 3
    assert len(read_recent_honks(p, limit=9)) == 3


def test_a_json_value_that_is_not_an_object_is_skipped_not_raised(tmp_path):
    d = tmp_path / "nape"
    d.mkdir()
    (d / "honks.jsonl").write_text('5\n"x"\n[1]\n{"honk_id": "a"}\n', encoding="utf-8")
    (d / "acks.jsonl").write_text("", encoding="utf-8")
    assert count_unacked_honks(d / "honks.jsonl") == 1
    assert [h["honk_id"] for h in read_recent_honks(d / "honks.jsonl")] == ["a"]


def test_the_console_and_nape_summary_agree_on_real_honk_records(tmp_path):
    """Honks built by nape's own builder: the console's count equals summary()'s
    `unacknowledged`, and its informational count equals the satisfied tally."""
    from sovereign_stack.dashboard import count_informational_unacked_honks
    from sovereign_stack.nape_daemon import NapeDaemon

    daemon = NapeDaemon(root=str(tmp_path))
    for pattern in ("declare_before_verify", "clean_verify_declare", "repeated_mistake"):
        daemon.emit_external_honk("s", pattern, "record_insight", f"{pattern} seen")
    daemon.emit_external_honk("s", "clean_verify_declare", "record_insight", "again")
    p = tmp_path / "nape" / "honks.jsonl"
    summary = daemon.summary(None)
    assert count_unacked_honks(p) == summary["unacknowledged"] == 2
    assert count_informational_unacked_honks(p) == summary["satisfied"] == 2
    assert len(daemon.current_honks(None, include_satisfied=False)) == 2


def test_collect_state_carries_both_numbers(tmp_path):
    from sovereign_stack import dashboard as dash

    _write(tmp_path, [{"honk_id": "a", "level": "low"}, {"honk_id": "s1", **SATISFIED}])
    state = dash.collect_state(
        dash.ActivityFeed(), sovereign_root=tmp_path, connectivity_check=list
    )
    assert state.unacked_honks == 1
    assert state.informational_honks_unacked == 1
    assert state.latest["honk"]["honk_id"] == "a"


# ── F6 (review of bfc5507): the readers never raise, and unreadable is not zero ──
#
# A non-object line in acks.jsonl, an unhashable honk_id in either file, or an
# invalid UTF-8 byte in either file used to raise straight out of the counter
# (and /snapshot.json answered 500). An unreadable honks.jsonl or acks.jsonl
# read as a healthy number. Each test below failed on bfc5507.


def _write_bytes(tmp_path, honks: bytes, acks: bytes = b""):
    d = tmp_path / "nape"
    d.mkdir(exist_ok=True)
    (d / "honks.jsonl").write_bytes(honks)
    (d / "acks.jsonl").write_bytes(acks)
    return d / "honks.jsonl"


def test_a_non_object_line_in_acks_is_skipped_not_raised(tmp_path):
    p = _write(tmp_path, [{"honk_id": "h0"}, {"honk_id": "h1"}])
    (tmp_path / "nape" / "acks.jsonl").write_text('5\n"x"\n[1]\n{"honk_id": "h0"}\n')
    assert count_unacked_honks(p) == 1
    assert [h["honk_id"] for h in read_recent_honks(p)] == ["h1"]


def test_an_unhashable_honk_id_in_acks_is_skipped_not_raised(tmp_path):
    p = _write(
        tmp_path,
        [{"honk_id": "h0"}, {"honk_id": "h1"}, {"honk_id": "h2"}],
        acks=[{"honk_id": ["h0"]}, {"honk_id": {"k": 1}}, {"honk_id": "h1"}],
    )
    assert count_unacked_honks(p) == 2
    assert len(read_recent_honks(p, limit=9)) == 2


def test_an_unhashable_honk_id_in_honks_is_counted_not_raised(tmp_path):
    """No ack can name it, so it stays counted, like a honk with no id at all."""
    p = _write(tmp_path, [{"honk_id": ["x"]}, {"honk_id": "a"}])
    assert count_unacked_honks(p) == 2
    assert len(read_recent_honks(p, limit=9)) == 2


def test_invalid_utf8_in_honks_is_a_bad_line_not_a_crash(tmp_path):
    p = _write_bytes(tmp_path, b'{"honk_id": "a"}\n\xff\xfe\n{"honk_id": "b"}\n')
    assert count_unacked_honks(p) == 2
    assert [h["honk_id"] for h in read_recent_honks(p, limit=9)] == ["b", "a"]


def test_invalid_utf8_in_acks_is_a_bad_line_not_a_crash(tmp_path):
    p = _write_bytes(
        tmp_path,
        b'{"honk_id": "a"}\n{"honk_id": "b"}\n',
        acks=b'{"honk_id": "a"}\n\xff\n',
    )
    assert count_unacked_honks(p) == 1


def _unreadable(path):
    """chmod 000 for the test body; restored after. Root ignores modes."""
    import contextlib
    import os
    import stat

    import pytest

    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions, so EACCES cannot be produced")

    @contextlib.contextmanager
    def _cm():
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, 0)
        try:
            yield
        finally:
            os.chmod(path, mode)

    return _cm()


def test_an_unreadable_honks_file_is_unknown_not_zero(tmp_path):
    from sovereign_stack import dashboard as dash

    p = _write(tmp_path, [{"honk_id": "a"}, {"honk_id": "s1", **SATISFIED}])
    with _unreadable(p):
        assert count_unacked_honks(p) is None
        assert dash.count_informational_unacked_honks(p) is None
        assert read_recent_honks(p) == []
        state = dash.collect_state(
            dash.ActivityFeed(), sovereign_root=tmp_path, connectivity_check=list
        )
        assert state.unacked_honks is None
        assert state.informational_honks_unacked is None
        assert "honks unreadable" in dash.render_state(state, color=False)


def test_an_unreadable_acks_file_is_unknown_not_every_honk(tmp_path):
    """Without the acks the count cannot be known; it must not read as all of them."""
    p = _write(tmp_path, [{"honk_id": "a"}, {"honk_id": "b"}], acks=[{"honk_id": "a"}])
    with _unreadable(tmp_path / "nape" / "acks.jsonl"):
        assert count_unacked_honks(p) is None
        assert read_recent_honks(p) == []
