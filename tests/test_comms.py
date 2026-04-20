"""
Comms module tests — locks down the read invariants that opus-4-7-web
flagged as missing from the iPhone-app side of the door.

Covers: ordering (asc/desc), time-bounded recall (since/until), offset+limit
paging (the thing that was silently ignored in the bridge), unread_for
filtering, body retrieval vs count, empty channel, corrupt-line tolerance.
"""
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sovereign_stack import comms


@pytest.fixture
def fake_comms(monkeypatch):
    """Redirect COMMS_DIR to a temp dir for each test."""
    tmp = Path(tempfile.mkdtemp())
    tmp.mkdir(exist_ok=True)
    monkeypatch.setattr(comms, "COMMS_DIR", tmp)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _write_messages(tmpdir: Path, channel: str, messages: list):
    path = tmpdir / f"{channel}.jsonl"
    with open(path, "w") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")


def _make_msg(ts: float, sender: str = "test", content: str = "",
              read_by: list = None, iso: str = None, mid: str = None):
    from datetime import datetime, timezone
    return {
        "id": mid or f"msg-{ts}",
        "timestamp": ts,
        "iso": iso or datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "sender": sender,
        "content": content,
        "channel": "general",
        "read_by": read_by or [],
    }


# ── Path + parsing helpers ──

class TestChannelPath:
    def test_sanitizes_filename(self, fake_comms):
        p = comms._channel_path("general")
        assert p.name == "general.jsonl"

    def test_strips_unsafe_chars(self, fake_comms):
        p = comms._channel_path("weird/name!")
        assert "/" not in p.name
        assert "!" not in p.name


class TestParseTimestamp:
    def test_none_returns_none(self):
        assert comms._parse_timestamp(None) is None
        assert comms._parse_timestamp("") is None

    def test_epoch_float(self):
        assert comms._parse_timestamp(1775183055.488628) == 1775183055.488628

    def test_epoch_int(self):
        assert comms._parse_timestamp(1775183055) == 1775183055.0

    def test_epoch_string(self):
        assert comms._parse_timestamp("1775183055.0") == 1775183055.0

    def test_iso_with_z(self):
        ts = comms._parse_timestamp("2026-04-03T02:24:15Z")
        assert ts is not None
        assert 1775100000 < ts < 1775200000

    def test_iso_with_offset(self):
        ts = comms._parse_timestamp("2026-04-03T02:24:15+00:00")
        assert ts is not None

    def test_garbage_returns_none(self):
        assert comms._parse_timestamp("not a timestamp") is None


# ── read_channel — the core fix ──

class TestReadChannel:
    def test_empty_channel_returns_empty(self, fake_comms):
        assert comms.read_channel("nonexistent") == []

    def test_newest_first_by_default(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(100, content="oldest"),
            _make_msg(200, content="middle"),
            _make_msg(300, content="newest"),
        ])
        result = comms.read_channel()
        assert result[0]["content"] == "newest"
        assert result[-1]["content"] == "oldest"

    def test_asc_order(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(100, content="oldest"),
            _make_msg(200, content="middle"),
            _make_msg(300, content="newest"),
        ])
        result = comms.read_channel(order="asc")
        assert result[0]["content"] == "oldest"

    def test_since_filter(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(100, content="before"),
            _make_msg(200, content="at boundary"),
            _make_msg(300, content="after"),
        ])
        result = comms.read_channel(since=200)
        # since is EXCLUSIVE — message exactly at 200 excluded
        contents = {m["content"] for m in result}
        assert "after" in contents
        assert "before" not in contents
        assert "at boundary" not in contents

    def test_until_filter(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(100, content="early"),
            _make_msg(200, content="middle"),
            _make_msg(300, content="late"),
        ])
        result = comms.read_channel(until=250)
        contents = {m["content"] for m in result}
        assert "early" in contents
        assert "middle" in contents
        assert "late" not in contents

    def test_since_iso(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(1775000000, content="before"),
            _make_msg(1775300000, content="after"),
        ])
        result = comms.read_channel(since="2026-04-01T00:00:00Z")
        # 2026-04-01 epoch ~ 1775260800, so 1775300000 > that
        assert any(m["content"] == "after" for m in result)

    def test_offset_actually_works(self, fake_comms):
        """THE iPhone fix — offset was silently ignored before."""
        _write_messages(fake_comms, "general", [
            _make_msg(i, content=f"msg{i}") for i in range(10)
        ])
        # desc order → msg9 first. offset=2 skips msg9 and msg8.
        result = comms.read_channel(order="desc", limit=3, offset=2)
        assert len(result) == 3
        assert result[0]["content"] == "msg7"
        assert result[1]["content"] == "msg6"
        assert result[2]["content"] == "msg5"

    def test_limit_caps_at_max(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(i, content=f"m{i}") for i in range(10)
        ])
        result = comms.read_channel(limit=99999)
        assert len(result) == 10

    def test_limit_respected_when_smaller_than_data(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(i) for i in range(100)
        ])
        result = comms.read_channel(limit=5)
        assert len(result) == 5

    def test_pagination_covers_everything(self, fake_comms):
        """Paging through with offset+limit should return each message exactly once."""
        _write_messages(fake_comms, "general", [
            _make_msg(i, content=f"m{i}") for i in range(20)
        ])
        seen = set()
        offset = 0
        while True:
            page = comms.read_channel(order="asc", limit=7, offset=offset)
            if not page:
                break
            for m in page:
                assert m["content"] not in seen  # no duplicates
                seen.add(m["content"])
            offset += len(page)
        assert len(seen) == 20

    def test_unread_for_filter(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(100, content="iphone saw this", read_by=["claude-iphone"]),
            _make_msg(200, content="iphone missed this", read_by=["claude-code"]),
            _make_msg(300, content="iphone missed this too", read_by=[]),
        ])
        result = comms.read_channel(unread_for="claude-iphone")
        contents = {m["content"] for m in result}
        assert "iphone saw this" not in contents
        assert "iphone missed this" in contents
        assert "iphone missed this too" in contents

    def test_corrupt_line_skipped(self, fake_comms):
        path = fake_comms / "general.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps(_make_msg(100, content="good")) + "\n")
            f.write("{ not valid json\n")
            f.write(json.dumps(_make_msg(200, content="also good")) + "\n")
        result = comms.read_channel(order="asc")
        assert len(result) == 2
        assert result[0]["content"] == "good"


# ── count_unread ──

class TestCountUnread:
    def test_empty_channel(self, fake_comms):
        assert comms.count_unread("general", "claude-iphone") == 0

    def test_counts_messages_missing_instance(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(100, read_by=["claude-iphone"]),
            _make_msg(200, read_by=[]),
            _make_msg(300, read_by=["other"]),
        ])
        assert comms.count_unread("general", "claude-iphone") == 2

    def test_all_read_returns_zero(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(100, read_by=["claude-iphone"]),
            _make_msg(200, read_by=["claude-iphone", "other"]),
        ])
        assert comms.count_unread("general", "claude-iphone") == 0


# ── unread_messages — the missing body endpoint ──

class TestUnreadMessages:
    def test_returns_bodies_not_just_count(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(100, content="caught", read_by=["claude-iphone"]),
            _make_msg(200, content="missed A", read_by=[]),
            _make_msg(300, content="missed B", read_by=[]),
        ])
        msgs = comms.unread_messages("claude-iphone")
        contents = [m["content"] for m in msgs]
        assert "caught" not in contents
        assert "missed A" in contents
        assert "missed B" in contents

    def test_asc_order_default(self, fake_comms):
        """Oldest-first is the default — caller catches up in the order things were said."""
        _write_messages(fake_comms, "general", [
            _make_msg(100, content="first unread", read_by=[]),
            _make_msg(200, content="second unread", read_by=[]),
        ])
        msgs = comms.unread_messages("claude-iphone")
        assert msgs[0]["content"] == "first unread"
        assert msgs[1]["content"] == "second unread"

    def test_limit_respected(self, fake_comms):
        _write_messages(fake_comms, "general", [
            _make_msg(i, content=f"m{i}", read_by=[]) for i in range(10)
        ])
        msgs = comms.unread_messages("claude-iphone", limit=3)
        assert len(msgs) == 3


# ── list_channels ──

class TestListChannels:
    def test_empty_comms_dir(self, fake_comms):
        # fake_comms already exists but is empty
        channels = comms.list_channels()
        assert channels == []

    def test_lists_channels_with_counts(self, fake_comms):
        _write_messages(fake_comms, "general", [_make_msg(100), _make_msg(200)])
        _write_messages(fake_comms, "alerts", [_make_msg(300)])
        channels = comms.list_channels()
        by_name = {c["name"]: c for c in channels}
        assert by_name["general"]["messages"] == 2
        assert by_name["alerts"]["messages"] == 1
        assert by_name["general"]["latest"] is not None
