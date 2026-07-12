"""The serialization the offload took away — held in the sync layer this time.

cb4de40 moved record_insight onto a worker thread so a slow receipt could never
freeze the loop again. That was right, and it is kept. But it made two chronicle
writers genuinely overlap for the first time, and it defended them with an
asyncio.Lock in the DISPATCHER — a lock that cannot do this job:

  * it unwinds on CancelledError, releasing while the worker thread is still
    mid read-modify-write (a client disconnect is enough);
  * every inline caller — close_session, resolve_thread, a daemon — never
    passes through the dispatcher at all, so it never takes the lock;
  * and metabolism rewrites insight files WHOLE (read_text -> mutate ->
    write_text), so an append landing inside that window is not a race we lose
    gracefully. It is Anthony's insight, gone.

The lock belongs in the sync layer, inside the mutators, where it is held by
every writer no matter how it was dispatched and cannot be released by
cancellation. These are the gates for that.

Interleavings are FORCED, never raced: the rewrite is paused between its read
and its write, and the appender is released into that window. On the unlocked
tree the append lands there and is clobbered — deterministically.
"""

import asyncio
import itertools
import json
import os
import threading
import time
from pathlib import Path

import pytest

from sovereign_stack import memory as memory_module
from sovereign_stack import metabolism, provenance, provenance_tools
from sovereign_stack.memory import ExperientialMemory

# Long enough that an unlocked appender lands inside the window every time,
# short enough to keep the suite quick.
_WINDOW_SECONDS = 0.75


def _chronicle(tmp_path: Path) -> Path:
    root = tmp_path / "chronicle"
    (root / "insights").mkdir(parents=True)
    (root / "open_threads").mkdir(parents=True)
    return root


def _entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_import_path_is_the_worktree():
    """The venv is an editable install pointing at PRODUCTION src. If this test
    imported production, every result below would be about code we did not write."""
    here = Path(__file__).resolve().parent.parent
    assert Path(provenance.__file__).resolve().is_relative_to(here), (
        f"provenance imported from {provenance.__file__} — NOT the worktree"
    )


class TestInsightRecordedDuringARewriteSurvives:
    """CRITICAL 1: metabolism's whole-file rewrite must not eat a live append."""

    def test_insight_appended_inside_the_rewrite_window_is_not_lost(self, tmp_path, monkeypatch):
        chronicle = _chronicle(tmp_path)
        mem = ExperientialMemory(root=str(chronicle))
        session = "session_lock_gate"
        target = chronicle / "insights" / "entropy" / f"{session}.jsonl"
        target.parent.mkdir(parents=True)
        target.write_text(
            "\n".join(
                json.dumps(e)
                for e in (
                    {
                        "timestamp": "2026-07-01T00:00:00+00:00",
                        "domain": "entropy",
                        "content": "STRESS TEST filler that metabolism will archive",
                        "layer": "hypothesis",
                        "session_id": session,
                    },
                    {
                        "timestamp": "2026-07-01T00:00:01+00:00",
                        "domain": "entropy",
                        "content": "a real finding that must survive the rewrite",
                        "layer": "ground_truth",
                        "session_id": session,
                    },
                )
            )
            + "\n"
        )

        read_done = threading.Event()
        real_write_text = Path.write_text

        def paused_write_text(self, *args, **kwargs):
            # Fires between metabolism's read_text and its write_text: the exact
            # window an append disappears into. Hold it open, let the appender in.
            if self == target:
                read_done.set()
                time.sleep(_WINDOW_SECONDS)
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", paused_write_text)

        appended: list[str] = []

        def appender():
            read_done.wait(timeout=10)
            appended.append(
                str(
                    mem.record_insight(
                        domain="entropy",
                        content="recorded WHILE metabolism was rewriting the file",
                        intensity=0.9,
                        session_id=session,
                        layer="ground_truth",
                    )
                )
            )

        writer = threading.Thread(target=appender)
        writer.start()
        metabolism._archive_test_artifacts_impl(chronicle)
        writer.join(timeout=30)
        assert not writer.is_alive(), "the appender never returned — deadlock"
        assert appended, "record_insight did not run"

        contents = [e["content"] for e in _entries(target)]
        assert "recorded WHILE metabolism was rewriting the file" in contents, (
            "SILENT DATA LOSS: the insight was appended inside metabolism's "
            "read->rewrite window and the rewrite clobbered it. This is the "
            "regression the offload introduced, and it is worse than the freeze."
        )
        assert "a real finding that must survive the rewrite" in contents
        assert not any("STRESS TEST" in c for c in contents), "metabolism still archived"


class TestInlineWritersTakeTheLockToo:
    """CRITICAL 2b: the dispatcher's lock never covered the inline callers."""

    def test_inline_thread_rewrite_cannot_clobber_an_offloaded_thread_append(
        self, tmp_path, monkeypatch
    ):
        """resolve_thread rewrites threads/<domain>.jsonl WHOLE and runs INLINE on
        the loop (close_session's shape). record_open_thread appends to that same
        file from a worker thread. The dispatcher lock covered neither."""
        chronicle = _chronicle(tmp_path)
        mem = ExperientialMemory(root=str(chronicle))
        mem.record_open_thread(question="the first question", domain="lockdom", session_id="s1")
        target = mem.threads_dir / "lockdom.jsonl"
        assert target.exists()

        read_done = threading.Event()
        real_open = open

        def paused_open(file, mode="r", *args, **kwargs):
            handle = real_open(file, mode, *args, **kwargs)
            if str(file) == str(target) and "w" in mode:
                read_done.set()
                time.sleep(_WINDOW_SECONDS)
            return handle

        appended = threading.Event()

        def appender():
            read_done.wait(timeout=10)
            mem.record_open_thread(
                question="opened WHILE resolve_thread was rewriting",
                domain="lockdom",
                session_id="s2",
            )
            appended.set()

        writer = threading.Thread(target=appender)
        writer.start()
        monkeypatch.setattr("builtins.open", paused_open)
        mem.resolve_thread("lockdom", "first question", "answered", session_id="s1")
        monkeypatch.undo()
        writer.join(timeout=30)
        assert appended.is_set(), "record_open_thread never returned — deadlock"

        questions = [e.get("question") for e in _entries(target)]
        assert "opened WHILE resolve_thread was rewriting" in questions, (
            "the inline rewrite clobbered the offloaded append — an inline writer "
            "bypasses any lock held in the async dispatcher"
        )
        first = [e for e in _entries(target) if e.get("question") == "the first question"]
        assert first and first[0]["resolved"] is True

    def test_two_writers_never_hold_the_critical_section_at_once(self, tmp_path, monkeypatch):
        """One inline (as close_session calls it), one offloaded onto a worker
        thread. Whatever the dispatch path, the appends must not overlap.

        Instrumented at the append itself — not at the lock — so a writer that
        skipped the lock entirely would show up as an overlap, not as a pass.
        """
        chronicle = _chronicle(tmp_path)
        mem = ExperientialMemory(root=str(chronicle))
        target = chronicle / "insights" / "d" / "s.jsonl"

        spans: list[tuple[float, float]] = []
        guard = threading.Lock()
        real_open = open

        def slow_append(file, mode="r", *args, **kwargs):
            if str(file) == str(target) and "a" in mode:
                started_at = time.monotonic()
                handle = real_open(file, mode, *args, **kwargs)
                time.sleep(0.3)  # hold the append open, inside the mutator
                with guard:
                    spans.append((started_at, time.monotonic()))
                return handle
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", slow_append)
        worker = threading.Thread(
            target=mem.record_insight,
            kwargs={"domain": "d", "content": "from the worker thread", "session_id": "s"},
        )
        worker.start()
        mem.record_insight(domain="d", content="from the inline caller", session_id="s")
        worker.join(timeout=30)
        monkeypatch.undo()
        assert not worker.is_alive(), "the worker never returned — deadlock"

        assert len(spans) == 2
        first, second = sorted(spans)
        assert first[1] <= second[0], (
            "two writers were appending to the chronicle at the same time — "
            "the serialization does not cover every dispatch path"
        )
        contents = [e["content"] for e in _entries(target)]
        assert "from the worker thread" in contents
        assert "from the inline caller" in contents


class TestCancellationCannotReleaseTheLock:
    """CRITICAL 2a: `async with lock: await to_thread(...)` unwinds on cancel and
    drops the lock while the thread is still writing. A sync lock cannot."""

    def test_lock_is_still_held_after_the_awaiting_task_is_cancelled(self, tmp_path, monkeypatch):
        chronicle = _chronicle(tmp_path)
        mem = ExperientialMemory(root=str(chronicle))

        in_section = threading.Event()
        calls = itertools.count()
        real_dedup = memory_module._dedup_hit

        def slow_dedup(path, content, domain, layer, timestamp):
            # Call 0 is the fast bail OUTSIDE the lock; call 1 is the first read
            # of the critical section, with the lock already held.
            if next(calls) == 1:
                in_section.set()
                time.sleep(1.0)
            return real_dedup(path, content, domain, layer, timestamp)

        monkeypatch.setattr(memory_module, "_dedup_hit", slow_dedup)

        async def drive():
            task = asyncio.create_task(
                asyncio.to_thread(
                    mem.record_insight,
                    "d",
                    "written by a caller who disconnects mid-write",
                    0.5,
                    "s",
                )
            )
            await asyncio.get_running_loop().run_in_executor(None, in_section.wait, 5)
            task.cancel()  # the client hung up / MCP timed out
            with pytest.raises(asyncio.CancelledError):
                await task
            # The worker thread is STILL inside the read-modify-write. The lock
            # it holds is a sync lock: the cancellation could not touch it.
            acquired = provenance._rlock_for(str(provenance.chronicle_lock_path(chronicle))).acquire(
                blocking=False
            )
            if acquired:  # pragma: no cover — only on a broken tree
                provenance._rlock_for(str(provenance.chronicle_lock_path(chronicle))).release()
            return acquired

        acquired = asyncio.run(drive())
        # The worker thread outlives the cancelled awaiter — that IS the finding.
        # But it still holds the lock, so it must be drained before the next test
        # runs, or it leaks lock state across the suite.
        time.sleep(1.2)
        assert not acquired, (
            "the write lock was released while a write was in flight — a cancelled "
            "awaiter must not be able to open the critical section to a second writer"
        )

        # And the abandoned write still completes and lands, intact.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _entries(chronicle / "insights" / "d" / "s.jsonl"):
                break
            time.sleep(0.05)
        contents = [e["content"] for e in _entries(chronicle / "insights" / "d" / "s.jsonl")]
        assert contents == ["written by a caller who disconnects mid-write"]


class TestReentrancy:
    """The nesting the lock introduces: resolve_thread -> record_insight, and
    record_insight -> append_supersession. A plain Lock deadlocks on both."""

    def test_nested_mutators_do_not_deadlock(self, tmp_path):
        chronicle = _chronicle(tmp_path)
        mem = ExperientialMemory(root=str(chronicle))
        mem.record_open_thread(question="does the lock deadlock", domain="d", session_id="s")

        done: list[str] = []

        def nest():
            # resolve_thread takes the lock, then calls record_insight, which
            # takes it again — and record_insight's ledger write takes it a third
            # time through append_supersession.
            mem.resolve_thread("d", "deadlock", "it does not", session_id="s")
            first = _entries(chronicle / "insights" / "d" / "s.jsonl")[0]
            mem.record_insight(
                domain="d",
                content="a successor, which appends to the ledger under the lock",
                session_id="s",
                supersedes=[provenance.derive_claim_id(first)],
                carry_forward_summary="the predecessor still teaches this",
            )
            mem.resolve_thread_by_id("nonexistent-thread-id", "no-op", session_id="s")
            done.append("ok")

        runner = threading.Thread(target=nest, daemon=True)
        runner.start()
        runner.join(timeout=20)
        assert not runner.is_alive(), "DEADLOCK: a nested mutator never returned"
        assert done == ["ok"]
        ledger = _entries(chronicle / "supersessions.jsonl")
        assert len(ledger) == 1 and ledger[0]["action"] == "supersede"


class TestForensicSurfaceTellsTheTruth:
    """MAJOR 4: inspect_claim is the tool whose whole job is 'was this checked?'"""

    def _record(self, mem: ExperientialMemory, receipt: dict) -> str:
        session = "s"
        path = mem.insights_dir / "d" / f"{session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": "2026-07-11T00:00:00+00:00",
            "domain": "d",
            "content": "a claim carrying a receipt nobody could re-check",
            "layer": "ground_truth",
            "session_id": session,
            "verified_by": [receipt],
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return provenance.derive_claim_id(entry)

    def test_inspect_claim_surfaces_unverified_reason(self, tmp_path):
        chronicle = _chronicle(tmp_path)
        mem = ExperientialMemory(root=str(chronicle))
        claim_id = self._record(
            mem,
            {
                "kind": "file",
                "ref": "/Volumes/wedged/run.ndjson",
                "sha256": "d" * 64,
                "checked_at_write": "attested",
                "unverified_reason": "file could not be hashed (did not complete within 5s)",
            },
        )
        result = provenance_tools.inspect_claim(claim_id, chronicle_root=chronicle)
        view = result["receipts"][0]
        assert view["checked_at_write"] == "attested"
        assert "could not be hashed" in view["unverified_reason"], (
            "a bare 'attested' cannot distinguish 'we tried to hash it and gave up' "
            "from 'a url receipt nobody ever re-checks' — surface the reason"
        )

    def test_a_transiently_unreadable_archive_is_not_reported_as_missing(self, tmp_path):
        """A blob intact on a slow mount must never read as 'the bytes are gone'.
        That is a false loss/tamper signal, inside the provenance system."""
        chronicle = _chronicle(tmp_path)
        mem = ExperientialMemory(root=str(chronicle))
        archive_id = "e" * 64

        blob = tmp_path / "unreadable.blob"
        os.mkfifo(blob)  # real: stats fine, open() would block — never opened
        (chronicle / "archives").mkdir(parents=True, exist_ok=True)
        with open(chronicle / "archives" / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"archive_id": archive_id, "path": str(blob), "sha256": "f" * 64}))
            f.write("\n")

        assert provenance.verify_archive_ref(archive_id, chronicle) == "unreadable"

        claim_id = self._record(
            mem, {"kind": "archive", "ref": archive_id, "checked_at_write": "verified"}
        )
        result = provenance_tools.inspect_claim(
            claim_id, verify_receipts=True, chronicle_root=chronicle
        )
        assert result["receipts"][0]["checked_now"] == "unreadable", (
            "the archive branch collapsed 'unreadable' to 'missing' — telling a "
            "forensic reader the archived bytes are GONE when they are intact"
        )

    def test_a_genuinely_gone_archive_blob_still_reads_missing(self, tmp_path):
        chronicle = _chronicle(tmp_path)
        archive_id = "a" * 64
        (chronicle / "archives").mkdir(parents=True, exist_ok=True)
        with open(chronicle / "archives" / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "archive_id": archive_id,
                        "path": str(tmp_path / "deleted.blob"),
                        "sha256": "f" * 64,
                    }
                )
            )
            f.write("\n")
        assert provenance.verify_archive_ref(archive_id, chronicle) == "missing"

    def test_stakes_verdict_vocabulary_covers_what_load_stakes_can_return(self):
        from sovereign_stack import protected

        assert "unreadable" in protected.STAKES_VERDICTS


class TestChronicleRootIsolation:
    """The welfare boundary: a test must never touch Anthony's lived records."""

    def test_default_chronicle_root_honors_sovereign_root(self, tmp_sovereign_root):
        resolved = provenance.default_chronicle_root()
        assert resolved == tmp_sovereign_root / "chronicle"
        assert not resolved.is_relative_to(Path.home() / ".sovereign")
        # The live default of every unparameterized provenance tool call.
        root, ledger = provenance_tools._live_paths(None, None)
        assert root.is_relative_to(tmp_sovereign_root)
        assert ledger.is_relative_to(tmp_sovereign_root)

    def test_production_still_resolves_to_the_live_chronicle(self, monkeypatch):
        """The LIVE server sets no SOVEREIGN_ROOT. Unset, nothing may move."""
        monkeypatch.delenv("SOVEREIGN_ROOT", raising=False)
        monkeypatch.delenv("SOVEREIGN_CHRONICLE", raising=False)
        assert provenance.default_chronicle_root() == Path.home() / ".sovereign" / "chronicle"
        assert (
            provenance.default_supersessions_path()
            == Path.home() / ".sovereign" / "chronicle" / "supersessions.jsonl"
        )


class TestDomainDirCannotVanishUnderAParkedWriter:
    """record_insight mkdirs the domain dir BEFORE taking the lock. Metabolism's
    archive pass rmdirs an emptied domain WHILE holding it. A writer already past
    the mkdir and parked on the lock comes back to a vanished parent."""

    def test_append_survives_the_domain_dir_being_removed_while_parked(self, tmp_path):
        chronicle = _chronicle(tmp_path)
        mem = ExperientialMemory(root=str(chronicle))
        domain_dir = chronicle / "insights" / "doomed"
        domain_dir.mkdir(parents=True)

        holder_has_lock = threading.Event()
        rmdir_done = threading.Event()
        failure: list[BaseException] = []

        def rmdir_holder():
            with provenance.chronicle_write_lock():
                holder_has_lock.set()
                # give the writer time to clear the pre-lock mkdir and park on
                # the lock, which is the only window where this can bite
                time.sleep(0.4)
                for child in domain_dir.iterdir():
                    child.unlink()
                domain_dir.rmdir()
                rmdir_done.set()

        def writer():
            holder_has_lock.wait(timeout=5)
            try:
                mem.record_insight(domain="doomed", content="must survive", layer="hypothesis")
            except BaseException as exc:  # noqa: BLE001 - the regression is any raise
                failure.append(exc)

        holder = threading.Thread(target=rmdir_holder)
        w = threading.Thread(target=writer)
        holder.start()
        w.start()
        holder.join(timeout=10)
        w.join(timeout=10)

        assert rmdir_done.is_set(), "the race never set up — holder did not rmdir"
        assert not failure, f"the append died on a vanished domain dir: {failure[0]!r}"
        landed = list((chronicle / "insights" / "doomed").glob("*.jsonl"))
        assert landed, "insight was lost — no file under the recreated domain dir"
        assert any("must survive" in f.read_text() for f in landed)
