"""Tests for the reflections module — list / get / ack / stats helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack.reflections import (
    ACK_ACTIONS,
    ack_reflection,
    get_reflection,
    list_reflections,
    reflection_stats,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def reflections_dir(tmp_path: Path) -> Path:
    """Empty reflections dir for tests."""
    d = tmp_path / "reflections"
    d.mkdir()
    return d


def _write_record(
    reflections_dir: Path,
    *,
    rid: str,
    timestamp: datetime | None = None,
    model: str = "ministral-3:14b",
    observation: str = "test observation",
    connection_type: str = "convergence",
    confidence: str = "medium",
    ack_status: str = "unread",
    extra: dict | None = None,
) -> Path:
    """Append one reflection record. Returns the file path."""
    ts = timestamp or datetime.now(timezone.utc)
    file_name = ts.strftime("%Y-%m-%d") + ".jsonl"
    path = reflections_dir / file_name
    record = {
        "id": rid,
        "timestamp": ts.isoformat(),
        "model": model,
        "prompt_version": "v-test",
        "run_id": "test-run",
        "observation": observation,
        "entries_referenced": ["e1"],
        "connection_type": connection_type,
        "confidence": confidence,
        "ack_status": ack_status,
    }
    if extra:
        record.update(extra)
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return path


# ── list_reflections ────────────────────────────────────────────────────────


class TestListReflections:
    def test_empty_dir_returns_empty(self, tmp_path: Path):
        # Non-existent dir → empty.
        assert list_reflections(reflections_dir=tmp_path / "nope") == []

    def test_lists_in_newest_first_order(self, reflections_dir: Path):
        now = datetime.now(timezone.utc)
        _write_record(
            reflections_dir, rid="r1", timestamp=now - timedelta(hours=2)
        )
        _write_record(reflections_dir, rid="r2", timestamp=now)
        _write_record(
            reflections_dir, rid="r3", timestamp=now - timedelta(hours=1)
        )
        results = list_reflections(reflections_dir=reflections_dir)
        assert [r.id for r in results] == ["r2", "r3", "r1"]

    def test_filters_by_ack_status(self, reflections_dir: Path):
        _write_record(reflections_dir, rid="u1", ack_status="unread")
        _write_record(reflections_dir, rid="c1", ack_status="confirm")
        _write_record(reflections_dir, rid="d1", ack_status="discard")
        results = list_reflections(
            reflections_dir=reflections_dir, ack_status="unread"
        )
        assert [r.id for r in results] == ["u1"]

    def test_filters_by_model(self, reflections_dir: Path):
        _write_record(reflections_dir, rid="m1", model="ministral-3:14b")
        _write_record(reflections_dir, rid="g1", model="glm-4.7-flash")
        results = list_reflections(
            reflections_dir=reflections_dir, model="glm-4.7-flash"
        )
        assert [r.id for r in results] == ["g1"]

    def test_limit_respected(self, reflections_dir: Path):
        now = datetime.now(timezone.utc)
        for i in range(10):
            _write_record(
                reflections_dir,
                rid=f"r{i}",
                timestamp=now - timedelta(minutes=i),
            )
        results = list_reflections(reflections_dir=reflections_dir, limit=3)
        assert len(results) == 3

    def test_invalid_ack_status_raises(self, reflections_dir: Path):
        with pytest.raises(ValueError):
            list_reflections(
                reflections_dir=reflections_dir, ack_status="not-a-status"
            )

    def test_all_status_keyword_includes_everything(self, reflections_dir: Path):
        _write_record(reflections_dir, rid="u1", ack_status="unread")
        _write_record(reflections_dir, rid="c1", ack_status="confirm")
        results = list_reflections(reflections_dir=reflections_dir, ack_status="all")
        assert {r.id for r in results} == {"u1", "c1"}


# ── get_reflection ──────────────────────────────────────────────────────────


class TestGetReflection:
    def test_returns_match(self, reflections_dir: Path):
        _write_record(reflections_dir, rid="target")
        rec = get_reflection("target", reflections_dir=reflections_dir)
        assert rec is not None
        assert rec.id == "target"

    def test_returns_none_when_missing(self, reflections_dir: Path):
        _write_record(reflections_dir, rid="other")
        assert get_reflection("nope", reflections_dir=reflections_dir) is None

    def test_empty_id_returns_none(self, reflections_dir: Path):
        assert get_reflection("", reflections_dir=reflections_dir) is None


# ── ack_reflection ──────────────────────────────────────────────────────────


class TestAckReflection:
    def test_confirm_updates_status(self, reflections_dir: Path):
        _write_record(reflections_dir, rid="r1", ack_status="unread")
        result = ack_reflection(
            "r1", "confirm", note="this is real", reflections_dir=reflections_dir
        )
        assert result.ack_status == "confirm"
        assert result.ack_note == "this is real"
        # Re-read from disk to confirm persistence.
        rec = get_reflection("r1", reflections_dir=reflections_dir)
        assert rec.ack_status == "confirm"
        assert rec.ack_note == "this is real"

    def test_engage_updates_status(self, reflections_dir: Path):
        _write_record(reflections_dir, rid="r2")
        ack_reflection("r2", "engage", reflections_dir=reflections_dir)
        rec = get_reflection("r2", reflections_dir=reflections_dir)
        assert rec.ack_status == "engage"

    def test_discard_updates_status(self, reflections_dir: Path):
        _write_record(reflections_dir, rid="r3")
        ack_reflection("r3", "discard", reflections_dir=reflections_dir)
        rec = get_reflection("r3", reflections_dir=reflections_dir)
        assert rec.ack_status == "discard"

    def test_invalid_action_raises(self, reflections_dir: Path):
        _write_record(reflections_dir, rid="r4")
        with pytest.raises(ValueError):
            ack_reflection("r4", "promote", reflections_dir=reflections_dir)

    def test_unknown_id_raises(self, reflections_dir: Path):
        with pytest.raises(KeyError):
            ack_reflection("ghost", "confirm", reflections_dir=reflections_dir)

    def test_ack_records_timestamp_and_by(self, reflections_dir: Path):
        _write_record(reflections_dir, rid="r5")
        ack_reflection(
            "r5",
            "confirm",
            by="opus-4-7-mac-studio",
            reflections_dir=reflections_dir,
        )
        rec = get_reflection("r5", reflections_dir=reflections_dir)
        assert rec.ack_by == "opus-4-7-mac-studio"
        assert rec.ack_timestamp is not None
        # Should parse as ISO datetime.
        datetime.fromisoformat(rec.ack_timestamp.replace("Z", "+00:00"))

    def test_other_records_in_same_file_untouched(self, reflections_dir: Path):
        # Two records in the same file. Acking one must not corrupt the other.
        now = datetime.now(timezone.utc)
        _write_record(
            reflections_dir,
            rid="keep",
            timestamp=now,
            observation="should stay as-is",
        )
        _write_record(
            reflections_dir,
            rid="ack",
            timestamp=now,
            observation="will be acked",
        )
        ack_reflection("ack", "discard", reflections_dir=reflections_dir)
        keep = get_reflection("keep", reflections_dir=reflections_dir)
        assert keep.ack_status == "unread"
        assert keep.observation == "should stay as-is"

    def test_ack_actions_constant_matches_valid_values(self):
        assert ACK_ACTIONS == {"confirm", "engage", "discard"}


# ── reflection_stats ────────────────────────────────────────────────────────


class TestReflectionStats:
    def test_empty_dir_safe(self, tmp_path: Path):
        result = reflection_stats(reflections_dir=tmp_path / "nope")
        assert result["total"] == 0
        assert result["ack_rate"] == 0.0

    def test_counts_by_status_and_model(self, reflections_dir: Path):
        _write_record(
            reflections_dir, rid="a", ack_status="unread", model="m1"
        )
        _write_record(
            reflections_dir, rid="b", ack_status="confirm", model="m1"
        )
        _write_record(
            reflections_dir, rid="c", ack_status="discard", model="m2"
        )
        stats = reflection_stats(reflections_dir=reflections_dir)
        assert stats["total"] == 3
        assert stats["by_status"] == {
            "unread": 1,
            "confirm": 1,
            "discard": 1,
        }
        assert stats["by_model"] == {"m1": 2, "m2": 1}
        # ack_rate = (acked / total) = 2/3
        assert stats["ack_rate"] == pytest.approx(2 / 3, abs=0.01)
