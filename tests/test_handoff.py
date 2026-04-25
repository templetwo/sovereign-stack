"""
Handoff engine tests.

The handoff engine is the instance-to-instance continuity primitive. It is
read on every boot by where_did_i_leave_off. A subtle bug here silently
drops the memory of a previous session, so this suite locks down the core
invariants: write, unconsumed filter, mark_consumed lifecycle, size limit,
empty-note rejection, and attribution formatting.
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from sovereign_stack.handoff import (
    HANDOFF_MAX_BYTES,
    HandoffEngine,
    format_handoff_for_surface,
)


class TestHandoffEngine:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.engine = HandoffEngine(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── Write ──

    def test_write_persists_record_to_disk(self):
        record = self.engine.write(
            note="The next instance should check X before Y.",
            source_instance="test-instance",
            source_session_id="session_1",
            thread="general",
        )
        assert Path(record["_path"]).exists()
        data = json.loads(Path(record["_path"]).read_text())
        assert data["note"] == "The next instance should check X before Y."
        assert data["source_instance"] == "test-instance"
        assert data["source_session_id"] == "session_1"
        assert data["thread"] == "general"
        assert data["consumed_at"] is None
        assert data["consumed_by"] is None

    def test_write_empty_note_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            self.engine.write(
                note="",
                source_instance="i",
                source_session_id="s",
            )

    def test_write_whitespace_only_note_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            self.engine.write(
                note="   \n\t  ",
                source_instance="i",
                source_session_id="s",
            )

    def test_write_oversize_note_rejected(self):
        big_note = "x" * (HANDOFF_MAX_BYTES + 10)
        with pytest.raises(ValueError, match="bytes"):
            self.engine.write(
                note=big_note,
                source_instance="i",
                source_session_id="s",
            )

    def test_write_boundary_size_accepted(self):
        """Exactly HANDOFF_MAX_BYTES should still be accepted."""
        note = "x" * HANDOFF_MAX_BYTES
        record = self.engine.write(
            note=note,
            source_instance="i",
            source_session_id="s",
        )
        assert Path(record["_path"]).exists()

    def test_write_unicode_note_counts_bytes_not_chars(self):
        """A note whose char count is under the limit but byte count is over should reject."""
        # 1000 emoji characters = ~4000 bytes in UTF-8
        emoji_note = "🌀" * 1000
        with pytest.raises(ValueError, match="bytes"):
            self.engine.write(
                note=emoji_note,
                source_instance="i",
                source_session_id="s",
            )

    def test_write_default_thread_is_general(self):
        record = self.engine.write(
            note="test",
            source_instance="i",
            source_session_id="s",
        )
        assert record["thread"] == "general"

    def test_write_unknown_source_defaults(self):
        """Empty source_instance / session_id get replaced with 'unknown'."""
        record = self.engine.write(
            note="test",
            source_instance="",
            source_session_id="",
            thread="t",
        )
        assert record["source_instance"] == "unknown"
        assert record["source_session_id"] == "unknown"

    def test_write_with_special_chars_in_slug(self):
        """Weird instance names don't break the filename."""
        record = self.engine.write(
            note="test",
            source_instance="my/weird\\instance:name",
            source_session_id="s",
            thread="thread with spaces!",
        )
        path = Path(record["_path"])
        assert path.exists()
        assert path.suffix == ".json"

    # ── Unconsumed / Read ──

    def test_unconsumed_returns_only_unconsumed(self):
        r1 = self.engine.write("first", "i", "s", "general")
        self.engine.write("second", "i", "s", "general")
        self.engine.mark_consumed([r1["_path"]], consumed_by="reader")
        open_records = self.engine.unconsumed()
        assert len(open_records) == 1
        assert open_records[0]["note"] == "second"

    def test_unconsumed_thread_filter(self):
        self.engine.write("alpha note", "i", "s", "alpha")
        self.engine.write("beta note", "i", "s", "beta")
        alpha = self.engine.unconsumed(thread="alpha")
        assert len(alpha) == 1
        assert alpha[0]["thread"] == "alpha"

    def test_unconsumed_newest_first(self):
        import time

        self.engine.write("first", "i", "s1", "t")
        time.sleep(0.01)
        self.engine.write("second", "i", "s2", "t")
        records = self.engine.unconsumed()
        assert records[0]["note"] == "second"
        assert records[1]["note"] == "first"

    def test_unconsumed_respects_limit(self):
        for i in range(5):
            self.engine.write(f"note {i}", "i", f"s{i}", "t")
        records = self.engine.unconsumed(limit=3)
        assert len(records) == 3

    def test_unconsumed_empty_root_returns_empty(self):
        """Fresh engine with no handoffs returns []."""
        assert self.engine.unconsumed() == []

    # ── mark_consumed lifecycle ──

    def test_mark_consumed_flips_flag(self):
        r = self.engine.write("note", "i", "s", "t")
        count = self.engine.mark_consumed([r["_path"]], consumed_by="reader-instance")
        assert count == 1
        data = json.loads(Path(r["_path"]).read_text())
        assert data["consumed_at"] is not None
        assert data["consumed_by"] == "reader-instance"

    def test_mark_consumed_is_idempotent(self):
        """Calling mark_consumed twice on the same handoff only counts once."""
        r = self.engine.write("note", "i", "s", "t")
        first = self.engine.mark_consumed([r["_path"]], consumed_by="reader1")
        second = self.engine.mark_consumed([r["_path"]], consumed_by="reader2")
        assert first == 1
        assert second == 0
        # First consumer wins.
        data = json.loads(Path(r["_path"]).read_text())
        assert data["consumed_by"] == "reader1"

    def test_mark_consumed_missing_path_returns_zero(self):
        count = self.engine.mark_consumed(["/nonexistent/path.json"], consumed_by="r")
        assert count == 0

    def test_mark_consumed_empty_list_returns_zero(self):
        count = self.engine.mark_consumed([], consumed_by="r")
        assert count == 0

    def test_mark_consumed_partial_success(self):
        r = self.engine.write("note", "i", "s", "t")
        count = self.engine.mark_consumed(
            [r["_path"], "/nonexistent.json"],
            consumed_by="r",
        )
        assert count == 1

    def test_consumed_handoff_still_queryable_via_all(self):
        """Consumption archives but does not delete — the chronicle remembers."""
        r = self.engine.write("past intent", "i", "s", "t")
        self.engine.mark_consumed([r["_path"]], consumed_by="reader")
        records = self.engine.all(include_consumed=True)
        assert len(records) == 1
        assert records[0]["note"] == "past intent"
        assert records[0]["consumed_by"] == "reader"

    # ── all() and archive ──

    def test_all_include_consumed_flag(self):
        r1 = self.engine.write("first", "i", "s", "t")
        self.engine.write("second", "i", "s", "t")
        self.engine.mark_consumed([r1["_path"]], consumed_by="r")

        with_consumed = self.engine.all(include_consumed=True)
        without_consumed = self.engine.all(include_consumed=False)
        assert len(with_consumed) == 2
        assert len(without_consumed) == 1

    def test_all_thread_filter(self):
        self.engine.write("a", "i", "s", "alpha")
        self.engine.write("b", "i", "s", "beta")
        self.engine.write("c", "i", "s", "alpha")
        alpha = self.engine.all(thread="alpha")
        assert len(alpha) == 2
        assert all(r["thread"] == "alpha" for r in alpha)

    def test_all_respects_limit(self):
        for i in range(10):
            self.engine.write(f"n{i}", "i", f"s{i}", "t")
        records = self.engine.all(limit=4)
        assert len(records) == 4

    # ── Attribution formatting ──

    def test_format_handoff_attributes_source(self):
        """Formatted output includes source_instance + session + timestamp."""
        record = self.engine.write(
            note="I think X matters.",
            source_instance="claude-code-macbook",
            source_session_id="spiral_2026_0419",
            thread="general",
        )
        formatted = format_handoff_for_surface(record)
        assert "claude-code-macbook" in formatted
        assert "spiral_2026_0419" in formatted
        assert "I think X matters." in formatted

    def test_format_handoff_frames_as_claim_not_memory(self):
        """The wording marks this as someone else's note, for epistemic hygiene."""
        record = self.engine.write(
            note="note",
            source_instance="i",
            source_session_id="s",
            thread="t",
        )
        formatted = format_handoff_for_surface(record)
        # Match the attribution cue — "left this note" is load-bearing language.
        assert "left this note" in formatted or "previous instance" in formatted.lower()

    def test_format_handoff_includes_thread(self):
        record = self.engine.write(
            note="note",
            source_instance="i",
            source_session_id="s",
            thread="architecture",
        )
        formatted = format_handoff_for_surface(record)
        assert "architecture" in formatted

    # ── Robustness ──

    def test_corrupt_json_file_skipped(self):
        """A malformed json file in the handoffs dir shouldn't crash reads."""
        bad = Path(self.tmpdir) / "handoffs" / "bad.json"
        bad.write_text("{ not valid json")
        self.engine.write("good", "i", "s", "t")
        records = self.engine.unconsumed()
        assert len(records) == 1
        assert records[0]["note"] == "good"

    def test_multiple_writes_all_persist(self):
        for i in range(3):
            self.engine.write(f"note {i}", "i", f"s{i}", "t")
        files = list((Path(self.tmpdir) / "handoffs").glob("*.json"))
        assert len(files) == 3
