"""Unit tests for scribe.session — lifecycle, TTL, archive."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sovereign_stack.scribe.session import (
    DEFAULT_TTL_MINUTES,
    SCRIBE_ATTRIBUTION,
    ScribeSession,
    ScribeSessionStore,
    archive_session,
)


class TestSessionCreate:
    def test_create_sets_basic_fields(self):
        session = ScribeSession.create()
        assert session.session_id.startswith("scribe_")
        assert session.attribution == SCRIBE_ATTRIBUTION
        assert session.ttl_minutes == DEFAULT_TTL_MINUTES
        assert session.turn_count == 0
        assert not session.closed
        assert not session.expired

    def test_session_id_format(self):
        session = ScribeSession.create()
        parts = session.session_id.split("_")
        # scribe, YYYYMMDD, HHMMSS, hex8
        assert parts[0] == "scribe"
        assert len(parts) == 4
        assert len(parts[1]) == 8
        assert len(parts[2]) == 6
        assert len(parts[3]) == 8

    def test_session_id_uniqueness(self):
        ids = {ScribeSession.create().session_id for _ in range(50)}
        assert len(ids) == 50

    def test_parent_instance_recorded(self):
        session = ScribeSession.create(parent_instance="iphone-claude")
        assert session.parent_instance == "iphone-claude"

    def test_custom_ttl(self):
        session = ScribeSession.create(ttl_minutes=10)
        assert session.ttl_minutes == 10


class TestTurnAppend:
    def test_user_turn_appended(self):
        session = ScribeSession.create()
        session.append_user_turn("hello")
        assert session.turn_count == 1
        assert session.turns[0].role == "user"
        assert session.turns[0].message == "hello"

    def test_assistant_turn_with_metrics(self):
        session = ScribeSession.create()
        session.append_assistant_turn("hi there", tokens_in=100, tokens_out=20, cost_usd=0.0012)
        assert session.turn_count == 1
        assert session.turns[0].role == "assistant"
        assert session.turns[0].tokens_in == 100
        assert session.turns[0].cost_usd == 0.0012

    def test_user_turn_carries_redaction_counts(self):
        session = ScribeSession.create()
        session.append_user_turn("text", redaction_counts={"bearer_token": 1})
        assert session.turns[0].redaction_counts == {"bearer_token": 1}

    def test_last_message_at_updates(self):
        session = ScribeSession.create()
        initial = session.last_message_at
        time.sleep(0.01)
        session.append_user_turn("after")
        assert session.last_message_at > initial

    def test_total_cost_aggregates(self):
        session = ScribeSession.create()
        session.append_assistant_turn("a", cost_usd=0.001)
        session.append_assistant_turn("b", cost_usd=0.002)
        assert pytest.approx(session.total_cost_usd) == 0.003

    def test_total_tokens_aggregate(self):
        session = ScribeSession.create()
        session.append_assistant_turn("a", tokens_in=100, tokens_out=20)
        session.append_assistant_turn("b", tokens_in=200, tokens_out=40)
        assert session.total_tokens_in == 300
        assert session.total_tokens_out == 60


class TestTTL:
    def test_not_expired_immediately(self):
        session = ScribeSession.create(ttl_minutes=60)
        assert not session.expired

    def test_expired_with_zero_ttl_after_delay(self):
        session = ScribeSession.create(ttl_minutes=0)
        # With ttl=0, expires_at == last_message_at exactly. After any
        # delay it is expired.
        time.sleep(0.05)
        assert session.expired


class TestHandlePayload:
    def test_handle_shape(self):
        session = ScribeSession.create(ttl_minutes=240)
        handle = session.handle_payload()
        assert handle["session_id"] == session.session_id
        assert handle["endpoint"] == "/api/call ask_scribe"
        assert handle["ttl_minutes"] == 240


class TestArchive:
    def test_archive_writes_header_and_turns(self, tmp_path):
        session = ScribeSession.create(parent_instance="claude-code")
        session.append_user_turn("hi")
        session.append_assistant_turn("hello, what do you need?", cost_usd=0.001)

        path = archive_session(session, archive_root=tmp_path, eviction_reason="test")
        assert path.exists()

        lines = path.read_text().splitlines()
        assert len(lines) == 3  # header + 2 turns
        header = json.loads(lines[0])
        assert header["type"] == "header"
        assert header["session_id"] == session.session_id
        assert header["turn_count"] == 2
        assert header["eviction_reason"] == "test"

        turn1 = json.loads(lines[1])
        assert turn1["role"] == "user"
        assert turn1["message"] == "hi"

        turn2 = json.loads(lines[2])
        assert turn2["role"] == "assistant"
        assert turn2["cost_usd"] == 0.001

    def test_archive_creates_date_dir(self, tmp_path):
        session = ScribeSession.create()
        path = archive_session(session, archive_root=tmp_path)
        # Parent dir should be a date string
        parent = path.parent.name
        assert len(parent) == 10  # YYYY-MM-DD
        assert parent[4] == "-" and parent[7] == "-"


class TestSessionStore:
    def test_register_and_get(self):
        store = ScribeSessionStore(archive_root=Path("/tmp/scribe-test-store"))
        session = ScribeSession.create()
        store.register(session)
        retrieved = store.get(session.session_id)
        assert retrieved is session

    def test_get_missing_returns_none(self):
        store = ScribeSessionStore(archive_root=Path("/tmp/scribe-test-store"))
        assert store.get("not-a-session") is None

    def test_close_evicts_and_archives(self, tmp_path):
        store = ScribeSessionStore(archive_root=tmp_path)
        session = ScribeSession.create()
        session.append_user_turn("hi")
        store.register(session)
        assert store.close(session.session_id) is True
        # No longer in store
        assert store.get(session.session_id) is None
        # Archived to disk
        archive_files = list(tmp_path.rglob("*.jsonl"))
        assert len(archive_files) == 1

    def test_close_unknown_returns_false(self):
        store = ScribeSessionStore(archive_root=Path("/tmp/scribe-test-store"))
        assert store.close("not-a-session") is False

    def test_active_count(self):
        store = ScribeSessionStore(archive_root=Path("/tmp/scribe-test-store"))
        assert store.active_count() == 0
        store.register(ScribeSession.create())
        store.register(ScribeSession.create())
        assert store.active_count() == 2

    def test_expired_session_evicted_on_get(self, tmp_path):
        store = ScribeSessionStore(archive_root=tmp_path)
        session = ScribeSession.create(ttl_minutes=0)
        store.register(session)
        time.sleep(0.05)
        # get should evict the expired session and return None
        assert store.get(session.session_id) is None
        # Archive should be written
        archive_files = list(tmp_path.rglob("*.jsonl"))
        assert len(archive_files) == 1
        # Eviction reason recorded
        header = json.loads(archive_files[0].read_text().splitlines()[0])
        assert header["eviction_reason"] == "ttl_expired"

    def test_sweep_evicts_expired(self, tmp_path):
        store = ScribeSessionStore(archive_root=tmp_path)
        store.register(ScribeSession.create(ttl_minutes=0))
        store.register(ScribeSession.create(ttl_minutes=60))
        time.sleep(0.05)
        evicted = store.sweep()
        assert evicted == 1
        assert store.active_count() == 1
