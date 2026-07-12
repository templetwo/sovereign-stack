"""
Thread lifecycle status tests — GPT-5.6 hardening item 3.

The open-thread surface was sediment: a bare `resolved` boolean meant a
deliberately parked thread was indistinguishable from one actively waiting, and
a merge could only be faked by resolving with prose.

Verifies:
1. A held thread does not read as active (and is excluded from triage).
2. A pre-migration record carrying only `resolved` still reads correctly —
   the 128 live domain files are never rewritten by this change.
3. The whole-file thread rewrite is crash-safe: a failure at the swap leaves the
   original domain intact rather than truncated.
4. status <-> resolved stays consistent across all five statuses (the mirror
   invariant — it is what keeps every un-migrated legacy reader correct).
5. A closed thread points at the insight that closed it, by derived claim_id.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from sovereign_stack import provenance
from sovereign_stack.memory import (
    CLOSED_THREAD_STATUSES,
    THREAD_STATUS_ACTIVE,
    THREAD_STATUS_ANSWERED,
    THREAD_STATUS_HELD,
    THREAD_STATUS_MERGED,
    THREAD_STATUS_SUPERSEDED,
    THREAD_STATUSES,
    ExperientialMemory,
    apply_thread_status,
    thread_is_open,
    thread_status,
)
from sovereign_stack.witness import format_threads_with_age


@pytest.fixture
def memory_root():
    tmp = tempfile.mkdtemp()
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def mem(memory_root):
    return ExperientialMemory(root=memory_root)


def _records(mem, domain):
    path = mem.threads_dir / f"{domain}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _only_thread_id(mem, domain):
    return _records(mem, domain)[0]["thread_id"]


def _write_legacy(mem, domain, records):
    """Write raw records exactly as the pre-status files on disk carry them."""
    mem.threads_dir.mkdir(parents=True, exist_ok=True)
    path = mem.threads_dir / f"{domain}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


class TestHeldIsNotActive:
    """A held thread is visible but no longer indistinguishable."""

    def test_held_thread_does_not_read_as_active(self, mem):
        mem.record_open_thread("Does the anisotropic horizon engine transduce?", domain="entropy")
        tid = _only_thread_id(mem, "entropy")

        result = mem.set_thread_status(tid, THREAD_STATUS_HELD, note="blocked on ChatGPT re-pass")
        assert result["previous_status"] == THREAD_STATUS_ACTIVE
        assert result["status"] == THREAD_STATUS_HELD

        active = mem.get_open_threads(status=THREAD_STATUS_ACTIVE)
        assert [t["thread_id"] for t in active] == [], "a held thread must not read as active"

        held = mem.get_open_threads(status=THREAD_STATUS_HELD)
        assert [t["thread_id"] for t in held] == [tid]

    def test_held_thread_stays_visible_on_the_open_map(self, mem):
        """Held is not hidden — losing a parked thread would be worse than sediment."""
        mem.record_open_thread("Should threads move to an append-only ledger?", domain="stack")
        tid = _only_thread_id(mem, "stack")
        mem.set_thread_status(tid, THREAD_STATUS_HELD)

        threads = mem.get_open_threads()
        assert [t["thread_id"] for t in threads] == [tid]
        assert threads[0]["status"] == THREAD_STATUS_HELD

    def test_held_thread_still_reads_as_open_to_legacy_readers(self, mem):
        """
        The mirror is what makes this safe: every reader that predates the enum
        checks `resolved`, and a held thread is NOT resolved.
        """
        mem.record_open_thread("Parked but not answered", domain="stack")
        tid = _only_thread_id(mem, "stack")
        mem.set_thread_status(tid, THREAD_STATUS_HELD)

        raw = _records(mem, "stack")[0]
        assert raw["status"] == THREAD_STATUS_HELD
        assert raw["resolved"] is False, "held must never read as resolved to a legacy reader"

    def test_held_thread_is_excluded_from_triage(self, mem):
        """Triage ranks what to act on; a parked thread must not accrue age pressure."""
        mem.record_open_thread("Active question", domain="stack")
        mem.record_open_thread("Parked question", domain="stack")
        records = _records(mem, "stack")
        parked = next(r for r in records if r["question"] == "Parked question")["thread_id"]
        mem.set_thread_status(parked, THREAD_STATUS_HELD)

        triaged = mem.triage_threads()
        assert parked not in [t["thread_id"] for t in triaged]

        with_held = mem.triage_threads(include_held=True)
        assert parked in [t["thread_id"] for t in with_held]

    def test_held_renders_with_a_visible_marker_at_boot(self, mem):
        """The boot ritual is where 'visible lifecycle' actually pays off."""
        mem.record_open_thread("Waiting on the arbiter recompute", domain="entropy")
        tid = _only_thread_id(mem, "entropy")
        mem.set_thread_status(tid, THREAD_STATUS_HELD, note="blocked on registration")

        rendered = "\n".join(format_threads_with_age(mem.get_open_threads()))
        assert "[HELD]" in rendered
        assert "blocked on registration" in rendered

    def test_a_held_thread_can_still_be_answered(self, mem):
        """Holding parks a thread; it does not put it beyond resolution."""
        mem.record_open_thread("Held then answered", domain="stack")
        tid = _only_thread_id(mem, "stack")
        mem.set_thread_status(tid, THREAD_STATUS_HELD)

        assert mem.resolve_thread_by_id(tid, "The block cleared.") != ""
        assert mem.get_open_threads() == []
        assert _records(mem, "stack")[0]["status"] == THREAD_STATUS_ANSWERED


class TestPreMigrationRecordsStillRead:
    """
    Back-compat is mandatory: 128 live domain files carry only `resolved`, and
    this change does NOT rewrite them.
    """

    def test_legacy_unresolved_record_reads_as_active(self, mem):
        _write_legacy(
            mem,
            "legacy",
            [
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "thread_id": "thread_20260101_000000_deadbeef",
                    "question": "A question from before the enum",
                    "domain": "legacy",
                    "resolved": False,
                }
            ],
        )
        threads = mem.get_open_threads(domain="legacy")
        assert len(threads) == 1
        assert threads[0]["status"] == THREAD_STATUS_ACTIVE

    def test_legacy_resolved_record_reads_as_answered_and_stays_closed(self, mem):
        _write_legacy(
            mem,
            "legacy",
            [
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "thread_id": "thread_20260101_000000_cafebabe",
                    "question": "An answered question from before the enum",
                    "domain": "legacy",
                    "resolved": True,
                }
            ],
        )
        assert mem.get_open_threads(domain="legacy") == []

        record = _records(mem, "legacy")[0]
        assert thread_status(record) == THREAD_STATUS_ANSWERED
        assert thread_is_open(record) is False

    def test_record_with_no_status_and_no_resolved_reads_as_active(self, mem):
        """Tolerance, not assumption: a missing field must not close a thread."""
        _write_legacy(
            mem,
            "legacy",
            [
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "question": "Neither field present",
                    "domain": "legacy",
                }
            ],
        )
        threads = mem.get_open_threads(domain="legacy")
        assert len(threads) == 1
        assert threads[0]["status"] == THREAD_STATUS_ACTIVE
        assert threads[0]["thread_id"], "legacy records still get a backfilled thread_id"

    def test_corrupt_status_never_reopens_a_closed_thread(self, mem):
        """A garbage status falls back to the resolved mirror — fail closed."""
        record = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "question": "Corrupt status",
            "domain": "legacy",
            "status": "banana",
            "resolved": True,
        }
        assert thread_status(record) == THREAD_STATUS_ANSWERED
        assert thread_is_open(record) is False

    def test_reading_legacy_files_does_not_rewrite_them(self, mem):
        """The read path is tolerant; the live files are left alone."""
        path = _write_legacy(
            mem,
            "legacy",
            [
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "question": "Untouched on read",
                    "domain": "legacy",
                    "resolved": False,
                }
            ],
        )
        before = path.read_bytes()
        mem.get_open_threads(domain="legacy")
        mem.triage_threads()
        assert path.read_bytes() == before


class TestMirrorInvariant:
    """
    `resolved` is a maintained mirror of `status`, never hand-assigned. This is
    the single property that keeps every un-migrated legacy reader correct.
    """

    def test_resolved_mirrors_status_for_every_status(self):
        for status in THREAD_STATUSES:
            thread = apply_thread_status({}, status)
            assert thread["status"] == status
            assert thread["resolved"] is (status in CLOSED_THREAD_STATUSES)
            assert thread_is_open(thread) is (status not in CLOSED_THREAD_STATUSES)

    def test_unknown_status_is_refused_at_the_write(self):
        with pytest.raises(ValueError, match="unknown thread status"):
            apply_thread_status({}, "banana")

    def test_resolve_paths_refuse_a_non_closing_status(self, mem):
        mem.record_open_thread("Cannot resolve into held", domain="stack")
        tid = _only_thread_id(mem, "stack")
        with pytest.raises(ValueError):
            mem.resolve_thread_by_id(tid, "x", status=THREAD_STATUS_HELD)
        with pytest.raises(ValueError):
            mem.resolve_thread("stack", "Cannot", "x", status=THREAD_STATUS_ACTIVE)


class TestResolvingReceipt:
    """A closed thread names its answer instead of merely asserting one."""

    def test_resolution_ref_points_at_the_persisted_insight(self, mem):
        mem.record_open_thread("What closed this?", domain="stack")
        tid = _only_thread_id(mem, "stack")

        insight_path = mem.resolve_thread_by_id(tid, "This finding closed it.")

        record = _records(mem, "stack")[0]
        receipt = record["resolution_ref"]
        provenance.validate_receipt_shape(receipt)
        assert receipt["kind"] == "claim"

        # The id must match what a future reader derives from the PERSISTED
        # entry — derive_claim_id hashes timestamp+domain+content and
        # record_insight stamps the timestamp itself, so an id derived from a
        # pre-write dict would be a dangling pointer that still passes shape
        # validation.
        entries = [
            json.loads(line) for line in Path(insight_path).read_text().splitlines() if line.strip()
        ]
        persisted = next(e for e in entries if e.get("resolved_thread_id") == tid)
        assert receipt["ref"] == provenance.derive_claim_id(persisted)

    def test_resolve_thread_by_fragment_carries_the_same_receipt(self, mem):
        """Both write paths share one mutation — neither may drift."""
        mem.record_open_thread("Fragment-matched question", domain="stack")
        insight_path = mem.resolve_thread("stack", "Fragment-matched", "Answered by fragment.")

        record = _records(mem, "stack")[0]
        assert record["status"] == THREAD_STATUS_ANSWERED
        assert record["resolved"] is True
        receipt = record["resolution_ref"]
        provenance.validate_receipt_shape(receipt)

        entries = [
            json.loads(line) for line in Path(insight_path).read_text().splitlines() if line.strip()
        ]
        persisted = next(e for e in entries if e.get("resolved_thread_id"))
        assert receipt["ref"] == provenance.derive_claim_id(persisted)


class TestReachableThroughMcp:
    """
    A lifecycle nobody can drive is decoration. These exercise the MCP layer the
    seats actually use — patched to a temp root so the live chronicle is never
    written.
    """

    def _dispatch(self, mem, name, arguments):
        import asyncio

        from sovereign_stack import server

        with mock.patch.object(server, "experiential", mem):
            return asyncio.run(server._dispatch_tool(name, arguments))[0].text

    def test_merged_is_settable_through_the_resolve_tool(self, mem):
        mem.record_open_thread("The surviving question", domain="stack")
        mem.record_open_thread("The duplicate question", domain="stack")
        records = _records(mem, "stack")
        survivor = next(r for r in records if r["question"] == "The surviving question")
        duplicate = next(r for r in records if r["question"] == "The duplicate question")

        self._dispatch(
            mem,
            "resolve_thread_by_id",
            {
                "thread_id": duplicate["thread_id"],
                "resolution": "Folded into the survivor.",
                "status": "merged",
                "merged_into": survivor["thread_id"],
            },
        )

        after = {r["thread_id"]: r for r in _records(mem, "stack")}
        merged = after[duplicate["thread_id"]]
        assert merged["status"] == THREAD_STATUS_MERGED
        assert merged["merged_into"] == survivor["thread_id"]

    def test_superseded_is_settable_through_the_resolve_tool(self, mem):
        mem.record_open_thread("Overtaken by events", domain="stack")
        self._dispatch(
            mem,
            "resolve_thread",
            {
                "domain": "stack",
                "question_fragment": "Overtaken",
                "resolution": "Stopped applying.",
                "status": "superseded",
            },
        )
        assert _records(mem, "stack")[0]["status"] == THREAD_STATUS_SUPERSEDED

    def test_bad_status_is_a_clean_tool_error_not_a_stack_trace(self, mem):
        mem.record_open_thread("A question", domain="stack")
        tid = _only_thread_id(mem, "stack")
        text = self._dispatch(
            mem,
            "resolve_thread_by_id",
            {"thread_id": tid, "resolution": "x", "status": "held"},
        )
        assert "Thread not resolved" in text
        assert _records(mem, "stack")[0]["status"] == THREAD_STATUS_ACTIVE

    def test_get_open_threads_status_filter_flows_through_the_tool(self, mem):
        mem.record_open_thread("Active question here", domain="stack")
        mem.record_open_thread("Parked question here", domain="stack")
        parked = next(r for r in _records(mem, "stack") if r["question"] == "Parked question here")[
            "thread_id"
        ]
        mem.set_thread_status(parked, THREAD_STATUS_HELD)

        active_text = self._dispatch(mem, "get_open_threads", {"status": "active"})
        assert "Active question here" in active_text
        assert "Parked question here" not in active_text

        all_text = self._dispatch(mem, "get_open_threads", {})
        assert "Parked question here" in all_text
        assert '"status": "held"' in all_text

    def test_merged_thread_points_at_the_thread_it_folded_into(self, mem):
        """A merge is recorded structurally, not faked with prose."""
        mem.record_open_thread("The surviving question", domain="stack")
        mem.record_open_thread("The duplicate question", domain="stack")
        records = _records(mem, "stack")
        survivor = next(r for r in records if r["question"] == "The surviving question")
        duplicate = next(r for r in records if r["question"] == "The duplicate question")

        mem.resolve_thread_by_id(
            duplicate["thread_id"],
            "Folded into the surviving thread.",
            status=THREAD_STATUS_MERGED,
            merged_into=survivor["thread_id"],
        )

        after = {r["thread_id"]: r for r in _records(mem, "stack")}
        merged = after[duplicate["thread_id"]]
        assert merged["status"] == THREAD_STATUS_MERGED
        assert merged["resolved"] is True
        assert merged["merged_into"] == survivor["thread_id"]
        assert [t["thread_id"] for t in mem.get_open_threads()] == [survivor["thread_id"]]

    def test_superseded_thread_closes_without_pretending_to_be_answered(self, mem):
        mem.record_open_thread("Overtaken by events", domain="stack")
        tid = _only_thread_id(mem, "stack")
        mem.resolve_thread_by_id(
            tid, "The question stopped applying.", status=THREAD_STATUS_SUPERSEDED
        )

        record = _records(mem, "stack")[0]
        assert record["status"] == THREAD_STATUS_SUPERSEDED
        assert record["resolved"] is True
        assert mem.get_open_threads() == []


class TestCrashSafeRewrite:
    """
    The thread write is a FULL-FILE REWRITE, not an append. The old path
    open(path, "w") truncated the domain before rewriting it, so a crash
    mid-write destroyed every thread in that domain. Status changes make that
    path run far more often.
    """

    def test_a_crash_at_the_swap_leaves_the_domain_intact(self, mem, monkeypatch):
        for i in range(5):
            mem.record_open_thread(f"Question number {i}", domain="stack")
        before = (mem.threads_dir / "stack.jsonl").read_bytes()
        tid = _records(mem, "stack")[2]["thread_id"]

        def boom(src, dst):
            raise OSError("simulated crash at the rename")

        monkeypatch.setattr("sovereign_stack.memory.os.replace", boom)

        with pytest.raises(OSError, match="simulated crash"):
            mem.resolve_thread_by_id(tid, "This resolution never lands.")

        after = mem.threads_dir / "stack.jsonl"
        assert after.read_bytes() == before, "the domain file must survive the crash byte-for-byte"
        assert len(mem.get_open_threads(domain="stack")) == 5, "no thread may be lost"

    def test_a_crash_leaves_no_temp_file_behind(self, mem, monkeypatch):
        """A stray temp file must not linger — and must never look like a domain."""
        mem.record_open_thread("Question", domain="stack")
        tid = _only_thread_id(mem, "stack")

        def boom(fd):
            raise OSError("simulated crash mid-write")

        monkeypatch.setattr("sovereign_stack.memory.os.fsync", boom)

        with pytest.raises(OSError):
            mem.resolve_thread_by_id(tid, "Never lands.")

        assert list(mem.threads_dir.glob("*.tmp")) == []
        assert len(mem.get_open_threads(domain="stack")) == 1

    def test_the_temp_file_is_never_read_as_a_domain(self, mem):
        """
        The *.jsonl globs that enumerate threads must not pick up the temp file,
        or a half-written swap would surface as phantom threads.
        """
        mem.record_open_thread("Real question", domain="stack")
        stray = mem.threads_dir / "stack.jsonl.tmp"
        stray.write_text(json.dumps({"question": "phantom", "domain": "stack"}) + "\n")

        questions = [t["question"] for t in mem.get_open_threads()]
        assert questions == ["Real question"]
        assert "phantom" not in questions

    def test_a_successful_rewrite_leaves_no_temp_file(self, mem):
        mem.record_open_thread("Question", domain="stack")
        tid = _only_thread_id(mem, "stack")
        mem.resolve_thread_by_id(tid, "Resolved cleanly.")

        assert list(mem.threads_dir.glob("*.tmp")) == []
        assert os.path.exists(mem.threads_dir / "stack.jsonl")

    def test_unparseable_lines_survive_a_rewrite(self, mem):
        """A corrupt line is preserved verbatim, never silently dropped."""
        mem.record_open_thread("Good question", domain="stack")
        tid = _only_thread_id(mem, "stack")
        path = mem.threads_dir / "stack.jsonl"
        with open(path, "a") as f:
            f.write("{ this is not json\n")

        mem.resolve_thread_by_id(tid, "Resolved.")

        lines = [line for line in path.read_text().splitlines() if line.strip()]
        assert "{ this is not json" in lines
