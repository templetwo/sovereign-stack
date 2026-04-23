"""
Acknowledgment split tests — Enhancement 3.

Verifies:
1. acknowledge() writes an ack record without mutating read_by on the source message.
2. read_channel(mark_seen=False) tags returned messages with _mark_seen=False.
3. touch_thread() records a touch; get_open_threads() still includes the thread.
4. resolve_thread_by_id() works correctly after touch_thread().
5. mark_acted_on() writes a record; mark_consumed() remains independent.
6. Credit string for opus-4-7-web is present in comms.py module docstring.
"""

import inspect
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from sovereign_stack import comms
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.handoff import HandoffEngine


# ── Fixtures ──

@pytest.fixture
def fake_comms_dir(monkeypatch):
    """Redirect COMMS_DIR to a fresh temp directory."""
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(comms, "COMMS_DIR", tmp)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def memory_root():
    """Fresh chronicle root for each test."""
    tmp = tempfile.mkdtemp()
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def handoff_root():
    """Fresh handoff root for each test."""
    tmp = tempfile.mkdtemp()
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ── Helper ──

def _write_message(tmpdir: Path, channel: str, msg: dict):
    path = tmpdir / f"{channel}.jsonl"
    with open(path, "a") as fh:
        fh.write(json.dumps(msg) + "\n")


def _make_msg(ts: float, mid: str = "msg-1", content: str = "", read_by=None):
    return {
        "id": mid,
        "timestamp": ts,
        "sender": "test",
        "content": content,
        "channel": "general",
        "read_by": read_by or [],
    }


# ── Case 1: acknowledge writes ack without mutating read_by ──

class TestAcknowledge:
    def test_acknowledge_writes_ack_record(self, fake_comms_dir):
        """acknowledge() must append to acks.jsonl."""
        ack = comms.acknowledge("msg-abc", "claude-code", note="integrated", channel="general")

        acks_path = fake_comms_dir / "acks.jsonl"
        assert acks_path.exists(), "acks.jsonl should be created on first acknowledge"

        written = json.loads(acks_path.read_text().strip().splitlines()[-1])
        assert written["message_id"] == "msg-abc"
        assert written["instance_id"] == "claude-code"
        assert written["note"] == "integrated"
        assert written["channel"] == "general"
        assert "timestamp" in written

    def test_acknowledge_does_not_mutate_read_by(self, fake_comms_dir):
        """The source message's read_by field must be unchanged after an ack."""
        original_msg = _make_msg(100.0, mid="msg-xyz", read_by=[])
        _write_message(fake_comms_dir, "general", original_msg)

        comms.acknowledge("msg-xyz", "claude-code", note="noted")

        # Re-read the message from disk and confirm read_by is untouched.
        messages = comms.read_channel(channel="general", order="asc")
        assert len(messages) == 1
        assert messages[0]["read_by"] == [], "read_by must not be mutated by acknowledge()"

    def test_get_acknowledgments_filters_by_message_id(self, fake_comms_dir):
        comms.acknowledge("msg-1", "inst-A", note="note1")
        comms.acknowledge("msg-2", "inst-B", note="note2")
        comms.acknowledge("msg-1", "inst-C", note="note3")

        acks = comms.get_acknowledgments(message_id="msg-1")
        assert len(acks) == 2
        assert all(a["message_id"] == "msg-1" for a in acks)

    def test_get_acknowledgments_filters_by_instance_id(self, fake_comms_dir):
        comms.acknowledge("msg-1", "inst-A")
        comms.acknowledge("msg-2", "inst-A")
        comms.acknowledge("msg-3", "inst-B")

        acks = comms.get_acknowledgments(instance_id="inst-A")
        assert len(acks) == 2
        assert all(a["instance_id"] == "inst-A" for a in acks)

    def test_empty_message_id_raises(self, fake_comms_dir):
        with pytest.raises(ValueError, match="message_id"):
            comms.acknowledge("", "inst-A")

    def test_empty_instance_id_raises(self, fake_comms_dir):
        with pytest.raises(ValueError, match="instance_id"):
            comms.acknowledge("msg-1", "")


# ── Case 2: read_channel(mark_seen=False) does not write read_by ──

class TestReadChannelMarkSeen:
    def test_mark_seen_false_tags_messages(self, fake_comms_dir):
        """When mark_seen=False, returned messages get _mark_seen=False tag."""
        msg = _make_msg(100.0, mid="m1", content="hello")
        _write_message(fake_comms_dir, "general", msg)

        results = comms.read_channel(channel="general", mark_seen=False)
        assert len(results) == 1
        assert results[0].get("_mark_seen") is False, (
            "_mark_seen=False sentinel must be set on returned messages"
        )

    def test_mark_seen_true_does_not_tag_messages(self, fake_comms_dir):
        """When mark_seen=True (default), _mark_seen sentinel is absent."""
        msg = _make_msg(100.0, mid="m1", content="hello")
        _write_message(fake_comms_dir, "general", msg)

        results = comms.read_channel(channel="general", mark_seen=True)
        assert len(results) == 1
        # _mark_seen=False should not appear when mark_seen is True
        assert results[0].get("_mark_seen") is not False, (
            "_mark_seen=False sentinel must not appear when mark_seen=True"
        )

    def test_mark_seen_default_preserves_existing_behavior(self, fake_comms_dir):
        """Calling read_channel() without mark_seen kwarg still works."""
        _write_message(fake_comms_dir, "general", _make_msg(100.0, content="x"))
        results = comms.read_channel(channel="general")
        assert len(results) == 1


# ── Case 3: touch_thread records a touch; get_open_threads still includes thread ──

class TestTouchThread:
    def test_touch_thread_records_touch(self, memory_root):
        mem = ExperientialMemory(root=memory_root)
        mem.record_open_thread("What is the optimal K?", domain="entropy")

        threads = mem.get_open_threads()
        thread_id = threads[0]["thread_id"]

        touch = mem.touch_thread(thread_id, note="Considered K=2.0", instance_id="inst-1")
        assert touch["thread_id"] == thread_id
        assert touch["note"] == "Considered K=2.0"
        assert touch["instance_id"] == "inst-1"
        assert "timestamp" in touch

    def test_touch_thread_does_not_hide_from_open_threads(self, memory_root):
        """Touching must not remove a thread from get_open_threads."""
        mem = ExperientialMemory(root=memory_root)
        mem.record_open_thread("Is witness necessary?", domain="governance")

        threads = mem.get_open_threads()
        assert len(threads) == 1
        thread_id = threads[0]["thread_id"]

        mem.touch_thread(thread_id, note="Still open")

        # Thread must still appear after a touch.
        threads_after = mem.get_open_threads()
        assert len(threads_after) == 1
        assert threads_after[0]["thread_id"] == thread_id

    def test_get_thread_touches_filters_by_thread_id(self, memory_root):
        mem = ExperientialMemory(root=memory_root)
        mem.record_open_thread("Question A", domain="alpha")
        mem.record_open_thread("Question B", domain="beta")

        threads = mem.get_open_threads()
        t_id_a = next(t["thread_id"] for t in threads if "Question A" in t["question"])
        t_id_b = next(t["thread_id"] for t in threads if "Question B" in t["question"])

        mem.touch_thread(t_id_a, note="touched A")
        mem.touch_thread(t_id_b, note="touched B")
        mem.touch_thread(t_id_a, note="touched A again")

        touches_a = mem.get_thread_touches(thread_id=t_id_a)
        assert len(touches_a) == 2
        assert all(r["thread_id"] == t_id_a for r in touches_a)

    def test_touch_empty_note_raises(self, memory_root):
        mem = ExperientialMemory(root=memory_root)
        mem.record_open_thread("Q", domain="test")
        threads = mem.get_open_threads()
        tid = threads[0]["thread_id"]
        with pytest.raises(ValueError, match="note"):
            mem.touch_thread(tid, note="")


# ── Case 4: resolve_thread_by_id after touch_thread works correctly ──

class TestResolveAfterTouch:
    def test_resolve_thread_by_id_after_touch(self, memory_root):
        """Touching a thread must not interfere with subsequent resolution by id."""
        mem = ExperientialMemory(root=memory_root)
        mem.record_open_thread("Unresolved question", domain="test")

        threads = mem.get_open_threads()
        assert len(threads) == 1
        tid = threads[0]["thread_id"]

        # Touch first.
        mem.touch_thread(tid, note="Thought about it")

        # Thread still open.
        assert len(mem.get_open_threads()) == 1

        # Now resolve.
        insight_path = mem.resolve_thread_by_id(tid, resolution="The answer is 42.")
        assert insight_path != "", "resolve_thread_by_id should return an insight path"

        # Thread must now be closed.
        open_after = mem.get_open_threads()
        assert len(open_after) == 0, "Thread must be resolved after resolve_thread_by_id"


# ── Case 5: mark_acted_on writes record, mark_consumed independent ──

class TestMarkActedOn:
    def test_mark_acted_on_writes_record(self, handoff_root):
        engine = HandoffEngine(root=handoff_root)
        rec = engine.write("Check the entropy logs", "inst-A", "sess-1")
        path = rec["_path"]

        acted = engine.mark_acted_on(
            handoff_path=path,
            consumed_by="inst-B",
            what_was_done="Reviewed entropy logs, found K=2.0 optimal.",
        )

        assert acted["handoff_path"] == path
        assert acted["consumed_by"] == "inst-B"
        assert "timestamp" in acted

        acted_on_log = Path(handoff_root) / "handoffs" / "acted_on.jsonl"
        assert acted_on_log.exists()
        written = json.loads(acted_on_log.read_text().strip().splitlines()[-1])
        assert written["what_was_done"] == "Reviewed entropy logs, found K=2.0 optimal."

    def test_mark_acted_on_does_not_affect_consumed(self, handoff_root):
        """mark_acted_on must not flip consumed_at on the original handoff."""
        engine = HandoffEngine(root=handoff_root)
        rec = engine.write("Note", "inst-A", "sess-1")
        path = rec["_path"]

        engine.mark_acted_on(path, "inst-B", "Did something")

        # consumed_at must still be None on the original record.
        data = json.loads(Path(path).read_text())
        assert data["consumed_at"] is None, (
            "mark_acted_on must not mutate consumed_at on the handoff file"
        )

    def test_mark_consumed_independent_of_acted_on(self, handoff_root):
        """mark_consumed and mark_acted_on are independent — both can coexist."""
        engine = HandoffEngine(root=handoff_root)
        rec = engine.write("Note", "inst-A", "sess-1")
        path = rec["_path"]

        # Consume first.
        engine.mark_consumed([path], consumed_by="inst-B")
        # Then record what was done.
        engine.mark_acted_on(path, "inst-B", "Followed up on the note.")

        # Both records should exist independently.
        data = json.loads(Path(path).read_text())
        assert data["consumed_at"] is not None, "consumed_at must be set by mark_consumed"

        records = engine.acted_on_records(handoff_path=path)
        assert len(records) == 1
        assert records[0]["what_was_done"] == "Followed up on the note."

    def test_acted_on_records_filters_by_path(self, handoff_root):
        engine = HandoffEngine(root=handoff_root)
        r1 = engine.write("Note 1", "inst-A", "sess-1")
        r2 = engine.write("Note 2", "inst-A", "sess-2")

        engine.mark_acted_on(r1["_path"], "inst-B", "Action A")
        engine.mark_acted_on(r2["_path"], "inst-B", "Action B")

        records = engine.acted_on_records(handoff_path=r1["_path"])
        assert len(records) == 1
        assert records[0]["what_was_done"] == "Action A"

    def test_mark_acted_on_empty_what_was_done_raises(self, handoff_root):
        engine = HandoffEngine(root=handoff_root)
        rec = engine.write("Note", "inst-A", "sess-1")
        with pytest.raises(ValueError, match="what_was_done"):
            engine.mark_acted_on(rec["_path"], "inst-B", what_was_done="")


# ── Case 6: Credit string for opus-4-7-web present in comms.py ──

class TestCreditString:
    def test_opus_credit_in_comms_module_docstring(self):
        """The module docstring must credit opus-4-7-web for the ack split proposal."""
        module_source = inspect.getsource(comms)
        assert "opus-4-7-web" in module_source, (
            "comms.py must contain a credit for opus-4-7-web per the spec"
        )
        assert "2026-04-20" in module_source, (
            "comms.py must contain the date 2026-04-20 in the credit line"
        )


# ── Edit 5: get_open_threads touch annotations ──

class TestOpenThreadsTouchAnnotations:
    """get_open_threads annotates each thread with touch_count and last_touched_at."""

    def test_never_touched_thread_has_zero_count_and_none(self, memory_root):
        """A thread with no touches gets touch_count=0 and last_touched_at=None."""
        mem = ExperientialMemory(root=memory_root)
        mem.record_open_thread("Untouched question", domain="test")

        threads = mem.get_open_threads()
        assert len(threads) == 1
        thread = threads[0]
        assert thread["touch_count"] == 0, (
            f"Never-touched thread must have touch_count=0, got {thread['touch_count']}"
        )
        assert thread["last_touched_at"] is None, (
            f"Never-touched thread must have last_touched_at=None, got {thread['last_touched_at']}"
        )

    def test_three_touches_annotated_correctly(self, memory_root):
        """After 3 touches, get_open_threads returns touch_count=3 and correct last_touched_at."""
        mem = ExperientialMemory(root=memory_root)
        mem.record_open_thread("Actively considered question", domain="entropy")

        threads = mem.get_open_threads()
        assert len(threads) == 1
        tid = threads[0]["thread_id"]

        touch1 = mem.touch_thread(tid, note="First look", instance_id="inst-A")
        touch2 = mem.touch_thread(tid, note="Second look", instance_id="inst-A")
        touch3 = mem.touch_thread(tid, note="Third look", instance_id="inst-B")

        threads_after = mem.get_open_threads()
        assert len(threads_after) == 1
        annotated = threads_after[0]

        assert annotated["touch_count"] == 3, (
            f"Expected touch_count=3, got {annotated['touch_count']}"
        )
        assert annotated["last_touched_at"] is not None
        # The most recent touch timestamp should match the third touch
        assert annotated["last_touched_at"] == touch3["timestamp"], (
            f"last_touched_at should match the most recent touch timestamp. "
            f"Expected {touch3['timestamp']}, got {annotated['last_touched_at']}"
        )

    def test_multiple_threads_annotated_independently(self, memory_root):
        """Two threads get independent touch_count annotations."""
        mem = ExperientialMemory(root=memory_root)
        mem.record_open_thread("Question A", domain="alpha")
        mem.record_open_thread("Question B", domain="beta")

        threads = mem.get_open_threads()
        tid_a = next(t["thread_id"] for t in threads if "Question A" in t["question"])
        tid_b = next(t["thread_id"] for t in threads if "Question B" in t["question"])

        # Touch A twice, B once
        mem.touch_thread(tid_a, note="A touch 1")
        mem.touch_thread(tid_a, note="A touch 2")
        mem.touch_thread(tid_b, note="B touch 1")

        threads_after = mem.get_open_threads()
        by_id = {t["thread_id"]: t for t in threads_after}

        assert by_id[tid_a]["touch_count"] == 2
        assert by_id[tid_b]["touch_count"] == 1
        assert by_id[tid_a]["last_touched_at"] is not None
        assert by_id[tid_b]["last_touched_at"] is not None

    def test_annotations_do_not_alter_thread_resolution(self, memory_root):
        """Touch annotations must not interfere with thread resolution."""
        mem = ExperientialMemory(root=memory_root)
        mem.record_open_thread("Resolvable question", domain="test")
        threads = mem.get_open_threads()
        tid = threads[0]["thread_id"]

        mem.touch_thread(tid, note="Considering it")

        # Verify annotations are present
        open_threads = mem.get_open_threads()
        assert open_threads[0]["touch_count"] == 1

        # Resolution must still work
        mem.resolve_thread_by_id(tid, resolution="Resolved with evidence.")
        open_after = mem.get_open_threads()
        assert len(open_after) == 0
