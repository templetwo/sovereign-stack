"""
BaseDaemon tests — guard the lifted scaffolding directly.

UncertaintyResurfacer and MetabolizeDaemon already exercise BaseDaemon
through their own test suites. This file adds direct-on-base tests that
use a minimal concrete subclass — so a future refactor of base.py shows
up here as a focused failure rather than buried in two daemon-specific
suites that share the bug.

Lifted invariants this file protects:
  * State schema_version persistence + future-version refusal.
  * Halt note four-field contract (reason, what tried, evidence, blocked).
  * Halt-alert post under SENDER_HALT_ALERT.
  * Ack-counting respects the threshold (zero before threshold-many posts;
    correct count after).
  * _record_post appends + trims to retention cap + saves.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pytest

from sovereign_stack.daemons.base import (
    BaseDaemon,
    CONSECUTIVE_UNACKED_THRESHOLD,
    DaemonState,
    POSTED_DIGESTS_RETAINED,
    STATE_SCHEMA_VERSION,
)
from sovereign_stack.daemons.senders import SENDER_HALT_ALERT


# ── Fixtures + minimal concrete subclass ────────────────────────────────────


@pytest.fixture
def tmp_root():
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    (root / "daemons").mkdir()
    (root / "daemons" / "halts").mkdir()
    yield root
    shutil.rmtree(tmp, ignore_errors=True)


class CommsStore:
    def __init__(self):
        self.posts: List[Dict] = []
        self.acks: Dict[str, List[Dict]] = {}

    def post(self, *, sender, content, channel, message_id, extra_fields=None):
        rec = {
            "id": message_id,
            "sender": sender,
            "content": content,
            "channel": channel,
            **(extra_fields or {}),
        }
        self.posts.append(rec)
        return rec

    def acknowledge(self, message_id, instance_id, note=""):
        self.acks.setdefault(message_id, []).append({
            "message_id": message_id,
            "instance_id": instance_id,
            "note": note,
        })

    def get_acks(self, message_id):
        return list(self.acks.get(message_id, []))


class StubDaemon(BaseDaemon):
    """Minimal concrete subclass — only what BaseDaemon requires."""

    SENDER = "daemon.stub"
    HALT_FILENAME_TAG = "stub"
    HALT_SOURCE = "stub"
    DAEMON_LABEL = "daemon.stub"

    def run(self, *, dry_run: bool = False):
        # Not exercised — direct tests call helpers, not run().
        raise NotImplementedError

    def _halt_what_tried(self) -> List[str]:
        return ["Stub daemon — exercises BaseDaemon helpers in tests."]

    def _halt_blocked_downstream(self) -> List[str]:
        return ["- Nothing real; this is a test scaffold."]


def make_stub(root: Path, *, comms: CommsStore | None = None,
              now: datetime | None = None,
              unacked_threshold: int = CONSECUTIVE_UNACKED_THRESHOLD):
    comms = comms or CommsStore()
    now = now or datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    counter = {"n": 0}

    def id_fn():
        counter["n"] += 1
        return f"stub-{counter['n']:03d}"

    return StubDaemon(
        state_path=root / "daemons" / "stub_state.json",
        halt_dir=root / "daemons" / "halts",
        compass_fn=lambda action, stakes: {"decision": "PROCEED"},
        comms_post_fn=comms.post,
        comms_get_acks_fn=comms.get_acks,
        now_fn=lambda: now,
        id_fn=id_fn,
        unacked_threshold=unacked_threshold,
    ), comms


# ── State schema ────────────────────────────────────────────────────────────


class TestStateSchema:
    def test_load_returns_default_when_missing(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        state = daemon._load_state()
        assert state.schema_version == STATE_SCHEMA_VERSION
        assert state.posted_digests == []
        assert state.halted_at is None

    def test_save_then_load_roundtrip(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        state = DaemonState(
            posted_digests=[{"message_id": "m1", "posted_at": "t1",
                             "content_snippet": "x"}],
            halted_at="2026-04-25T00:00:00+00:00",
            halt_reason="test",
        )
        daemon._save_state(state)

        loaded = daemon._load_state()
        assert loaded.halted_at == "2026-04-25T00:00:00+00:00"
        assert loaded.halt_reason == "test"
        assert len(loaded.posted_digests) == 1

    def test_unversioned_legacy_loads_as_v1(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        daemon.state_path.parent.mkdir(parents=True, exist_ok=True)
        daemon.state_path.write_text(json.dumps({
            "posted_digests": [],
            "halted_at": None,
            "halt_reason": None,
        }))
        state = daemon._load_state()
        assert state.schema_version == STATE_SCHEMA_VERSION

    def test_future_version_refuses_to_load(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        daemon.state_path.parent.mkdir(parents=True, exist_ok=True)
        daemon.state_path.write_text(json.dumps({
            "schema_version": STATE_SCHEMA_VERSION + 1,
            "posted_digests": [],
            "halted_at": None,
            "halt_reason": None,
        }))
        with pytest.raises(ValueError):
            daemon._load_state()

    def test_corrupt_json_returns_default(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        daemon.state_path.parent.mkdir(parents=True, exist_ok=True)
        daemon.state_path.write_text("not valid json {{{")
        state = daemon._load_state()
        # Default — no crash.
        assert state.posted_digests == []


# ── Ack counting ────────────────────────────────────────────────────────────


class TestAckCounting:
    def test_zero_before_threshold_posts(self, tmp_root):
        """If fewer than threshold-many posts have been made, the circuit
        breaker cannot fire — _count_recent_unacked returns 0."""
        daemon, _ = make_stub(tmp_root)
        state = DaemonState(posted_digests=[
            {"message_id": "m1", "posted_at": "t1", "content_snippet": "x"},
            {"message_id": "m2", "posted_at": "t2", "content_snippet": "x"},
        ])
        # Only 2 posts; threshold is 3.
        assert daemon._count_recent_unacked(state) == 0

    def test_full_count_when_all_unacked(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        state = DaemonState(posted_digests=[
            {"message_id": f"m{i}", "posted_at": "t", "content_snippet": "x"}
            for i in range(CONSECUTIVE_UNACKED_THRESHOLD)
        ])
        assert daemon._count_recent_unacked(state) == CONSECUTIVE_UNACKED_THRESHOLD

    def test_acked_post_reduces_count(self, tmp_root):
        comms = CommsStore()
        daemon, _ = make_stub(tmp_root, comms=comms)
        state = DaemonState(posted_digests=[
            {"message_id": f"m{i}", "posted_at": "t", "content_snippet": "x"}
            for i in range(CONSECUTIVE_UNACKED_THRESHOLD)
        ])
        comms.acknowledge("m1", "claude-test", "integrated")
        assert daemon._count_recent_unacked(state) == CONSECUTIVE_UNACKED_THRESHOLD - 1

    def test_only_last_n_counted(self, tmp_root):
        """Earlier posts beyond the threshold window don't count even if unacked."""
        comms = CommsStore()
        daemon, _ = make_stub(tmp_root, comms=comms)
        # 5 posts; ack only the last threshold-many.
        state = DaemonState(posted_digests=[
            {"message_id": f"m{i}", "posted_at": "t", "content_snippet": "x"}
            for i in range(5)
        ])
        for i in range(5 - CONSECUTIVE_UNACKED_THRESHOLD, 5):
            comms.acknowledge(f"m{i}", "claude-test", "ack")
        # All in the window are acked → 0 unacked.
        assert daemon._count_recent_unacked(state) == 0


# ── Posting bookkeeping ─────────────────────────────────────────────────────


class TestRecordPost:
    def test_appends_and_persists(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        state = DaemonState()
        now = datetime(2026, 4, 25, tzinfo=timezone.utc)
        daemon._record_post(
            state, message_id="msg-1", content="hello world", now=now,
        )
        assert len(state.posted_digests) == 1
        assert state.posted_digests[0]["message_id"] == "msg-1"
        assert state.posted_digests[0]["content_snippet"] == "hello world"

        # Persisted to disk.
        loaded = daemon._load_state()
        assert loaded.posted_digests[0]["message_id"] == "msg-1"

    def test_extra_fields_ride_along(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        state = DaemonState()
        daemon._record_post(
            state,
            message_id="msg-1",
            content="x",
            now=datetime(2026, 4, 25, tzinfo=timezone.utc),
            extra={"fingerprints": ["abc", "def"], "decision_path": "/x"},
        )
        assert state.posted_digests[0]["fingerprints"] == ["abc", "def"]
        assert state.posted_digests[0]["decision_path"] == "/x"

    def test_trims_to_retention_cap(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        state = DaemonState()
        for i in range(POSTED_DIGESTS_RETAINED * 2):
            daemon._record_post(
                state,
                message_id=f"msg-{i}",
                content=f"c{i}",
                now=datetime(2026, 4, 25, tzinfo=timezone.utc),
            )
        assert len(state.posted_digests) == POSTED_DIGESTS_RETAINED
        # Oldest dropped, newest retained.
        last_ids = [e["message_id"] for e in state.posted_digests]
        assert last_ids[-1] == f"msg-{POSTED_DIGESTS_RETAINED * 2 - 1}"


# ── Halt write-path ─────────────────────────────────────────────────────────


class TestPerformHalt:
    def test_writes_halt_note_with_four_fields(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        state = DaemonState(posted_digests=[
            {"message_id": "m1", "posted_at": "t1", "content_snippet": "snip 1"},
            {"message_id": "m2", "posted_at": "t2", "content_snippet": "snip 2"},
            {"message_id": "m3", "posted_at": "t3", "content_snippet": "snip 3"},
        ])
        halt_path = daemon._perform_halt(
            state,
            reason="test_halt_reason",
            evidence_note="three test posts went unacked.",
        )
        body = halt_path.read_text()

        # (a) reason
        assert "Reason: test_halt_reason" in body
        # (b) what daemon tried
        assert "## What the daemon tried to do" in body
        assert "Stub daemon" in body
        # (c) evidence
        assert "## Evidence that triggered the halt" in body
        assert "three test posts went unacked." in body
        for mid in ("m1", "m2", "m3"):
            assert mid in body
        # (d) blocked downstream
        assert "## What's blocked downstream" in body
        assert "this is a test scaffold" in body
        # Bonus — to-resolve section
        assert "## To resolve" in body

    def test_marks_state_halted_and_persists(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        state = DaemonState(posted_digests=[
            {"message_id": f"m{i}", "posted_at": "t", "content_snippet": "x"}
            for i in range(3)
        ])
        daemon._perform_halt(state, reason="r", evidence_note="e")
        assert state.halted_at is not None
        assert state.halt_reason == "r"
        # Persisted.
        loaded = daemon._load_state()
        assert loaded.halted_at == state.halted_at

    def test_posts_halt_alert_to_comms(self, tmp_root):
        comms = CommsStore()
        daemon, _ = make_stub(tmp_root, comms=comms)
        state = DaemonState(posted_digests=[
            {"message_id": f"m{i}", "posted_at": "t", "content_snippet": "x"}
            for i in range(3)
        ])
        daemon._perform_halt(state, reason="r", evidence_note="e")

        alerts = [p for p in comms.posts if p["sender"] == SENDER_HALT_ALERT]
        assert len(alerts) == 1
        # DAEMON_LABEL appears in the alert content.
        assert "daemon.stub halted" in alerts[0]["content"]
        # halt_source carries the subclass tag.
        assert alerts[0]["halt_source"] == "stub"

    def test_halt_filename_uses_subclass_tag(self, tmp_root):
        daemon, _ = make_stub(tmp_root)
        state = DaemonState(posted_digests=[
            {"message_id": f"m{i}", "posted_at": "t", "content_snippet": "x"}
            for i in range(3)
        ])
        halt_path = daemon._perform_halt(
            state, reason="myreason", evidence_note="e",
        )
        assert "stub_myreason" in halt_path.name


# ── Halt-alert is best-effort ───────────────────────────────────────────────


class TestHaltAlertBestEffort:
    def test_comms_failure_does_not_propagate(self, tmp_root):
        """If comms post fails, _post_halt_alert swallows — the durable
        record is the halt note on disk."""
        def failing_post(**kwargs):
            raise RuntimeError("comms is down")

        daemon = StubDaemon(
            state_path=tmp_root / "daemons" / "stub_state.json",
            halt_dir=tmp_root / "daemons" / "halts",
            compass_fn=lambda a, s: {"decision": "PROCEED"},
            comms_post_fn=failing_post,
            comms_get_acks_fn=lambda mid: [],
            now_fn=lambda: datetime(2026, 4, 25, tzinfo=timezone.utc),
        )
        state = DaemonState(posted_digests=[
            {"message_id": f"m{i}", "posted_at": "t", "content_snippet": "x"}
            for i in range(3)
        ])
        # Should NOT raise; halt note still on disk.
        halt_path = daemon._perform_halt(state, reason="r", evidence_note="e")
        assert halt_path.exists()
