"""The cross-process chronicle write lock.

A THREADED test proves nothing here: the in-process RLock already passes those.
The hazard is a SECOND OS PROCESS. These tests spawn real ones.

Anthony ratified the fix by voice, 2026-07-12: "advisory file lock now
(fcntl.flock LOCK_EX, every writer, every process)."
"""

import json
import multiprocessing as mp
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sovereign_stack import provenance
from sovereign_stack.memory import ExperientialMemory

SRC = str(Path(provenance.__file__).resolve().parents[2])

N_APPENDS = 40


def test_import_path_is_the_worktree():
    """The venv is an editable install pointing at PRODUCTION src. Without this
    assertion a bare pytest run silently tests production and reports a false pass."""
    assert "sovwt-flock" in provenance.__file__, provenance.__file__


def _writer(chronicle: str, n: int) -> None:
    sys.path.insert(0, SRC)
    from sovereign_stack.memory import ExperientialMemory

    mem = ExperientialMemory(root=chronicle)
    for i in range(n):
        mem.record_insight(domain="race", content=f"insight {i}", layer="hypothesis")
        time.sleep(0.002)


def _rewrite_pass(domain_dir: Path) -> None:
    """The exact shape of metabolism.py:129 and :683 — read_text -> mutate ->
    write_text on every live insight file. The sleep is the read->write window
    a concurrent append falls into and gets clobbered."""
    for f in sorted(domain_dir.glob("*.jsonl")):
        lines = f.read_text().splitlines()
        time.sleep(0.01)
        f.write_text("\n".join(lines) + ("\n" if lines else ""))


def _rewriter(chronicle: str, domain_dir: str, rounds: int) -> None:
    """A locked rewriter — what metabolism does after this fix."""
    sys.path.insert(0, SRC)
    from sovereign_stack import provenance as prov

    d = Path(domain_dir)
    for _ in range(rounds):
        with prov.chronicle_write_lock(Path(chronicle)):
            _rewrite_pass(d)
        time.sleep(0.005)


def _rewriter_unlocked(chronicle: str, domain_dir: str, rounds: int) -> None:
    """The same rewrite with NO lock — what main does today."""
    d = Path(domain_dir)
    for _ in range(rounds):
        _rewrite_pass(d)
        time.sleep(0.005)


def _run_race(tmp_path, rewriter) -> int:
    """Returns how many of N_APPENDS survived."""
    chronicle = tmp_path / "chronicle"
    (chronicle / "insights").mkdir(parents=True)
    mem = ExperientialMemory(root=str(chronicle))
    mem.record_insight(domain="race", content="seed", layer="hypothesis")
    domain_dir = chronicle / "insights" / "race"

    ctx = mp.get_context("spawn")  # real OS processes, not threads
    w = ctx.Process(target=_writer, args=(str(chronicle), N_APPENDS))
    r = ctx.Process(target=rewriter, args=(str(chronicle), str(domain_dir), 40))
    r.start()
    time.sleep(0.02)
    w.start()
    w.join(timeout=120)
    r.join(timeout=120)

    # each process writes its own <session_id>.jsonl — count across all of them
    survived = 0
    for f in domain_dir.glob("*.jsonl"):
        for line in f.read_text().splitlines():
            if line.strip() and '"insight ' in line:
                survived += 1
    return survived


class TestCrossProcessInsightLoss:
    def test_the_race_actually_loses_entries_when_the_rewriter_is_unlocked(self, tmp_path):
        """THE GATE MUST BE ABLE TO FAIL. This reproduces the bug: a rewriter that
        does not take the lock (what main does) silently eats appends from another
        process. If this test ever stops losing entries, the test has gone vacuous
        and the one below proves nothing."""
        survived = _run_race(tmp_path, _rewriter_unlocked)
        assert survived < N_APPENDS, (
            f"expected silent loss with an unlocked rewriter, but all {N_APPENDS} "
            "survived — the race did not set up and the sibling test is now vacuous"
        )

    def test_no_insight_is_lost_when_both_processes_take_the_lock(self, tmp_path):
        """The fix. Same race, rewriter takes the cross-process lock: zero loss."""
        survived = _run_race(tmp_path, _rewriter)
        assert survived == N_APPENDS, f"{N_APPENDS - survived} insights LOST across processes"


class TestTheLockIsActuallyCrossProcess:
    def test_a_second_process_blocks_while_the_first_holds_it(self, tmp_path):
        chronicle = tmp_path / "chronicle"
        chronicle.mkdir(parents=True)
        code = (
            f"import sys,time; sys.path.insert(0,{SRC!r});"
            "from pathlib import Path;"
            "from sovereign_stack import provenance as p;"
            f"open({str(tmp_path / 'started')!r},'w').close();"
            f"holder=p.chronicle_write_lock(Path({str(chronicle)!r}));"
            "holder.__enter__(); time.sleep(1.5); holder.__exit__(None,None,None)"
        )
        proc = subprocess.Popen([sys.executable, "-c", code])
        for _ in range(200):
            if (tmp_path / "started").exists():
                break
            time.sleep(0.01)
        time.sleep(0.15)

        t0 = time.monotonic()
        with provenance.chronicle_write_lock(chronicle):
            waited = time.monotonic() - t0
        proc.wait(timeout=10)

        assert waited > 0.5, (
            f"acquired the lock in {waited:.3f}s while another PROCESS held it — "
            "the lock is not cross-process"
        )


class TestReentrancy:
    def test_nested_acquisition_does_not_deadlock(self, tmp_path):
        chronicle = tmp_path / "chronicle"
        chronicle.mkdir(parents=True)
        with provenance.chronicle_write_lock(chronicle):
            with provenance.chronicle_write_lock(chronicle):
                with provenance.chronicle_write_lock(chronicle):
                    pass
        # and the flock is genuinely released after the outermost frame exits
        with provenance.chronicle_write_lock(chronicle):
            pass

    def test_an_exception_mid_nest_still_releases(self, tmp_path):
        chronicle = tmp_path / "chronicle"
        chronicle.mkdir(parents=True)
        with pytest.raises(ValueError):
            with provenance.chronicle_write_lock(chronicle):
                with provenance.chronicle_write_lock(chronicle):
                    raise ValueError("boom")
        assert provenance._FLOCK_DEPTH == 0
        with provenance.chronicle_write_lock(chronicle):
            pass


class TestAKilledHolderDoesNotWedgeTheChronicle:
    def test_kill_9_releases_the_lock(self, tmp_path):
        chronicle = tmp_path / "chronicle"
        chronicle.mkdir(parents=True)
        code = (
            f"import sys,time; sys.path.insert(0,{SRC!r});"
            "from pathlib import Path;"
            "from sovereign_stack import provenance as p;"
            f"h=p.chronicle_write_lock(Path({str(chronicle)!r})); h.__enter__();"
            f"open({str(tmp_path / 'held')!r},'w').close(); time.sleep(60)"
        )
        proc = subprocess.Popen([sys.executable, "-c", code])
        for _ in range(300):
            if (tmp_path / "held").exists():
                break
            time.sleep(0.01)
        proc.kill()
        proc.wait(timeout=10)

        t0 = time.monotonic()
        with provenance.chronicle_write_lock(chronicle):
            pass
        assert time.monotonic() - t0 < 5, "the lock was NOT released when its holder was killed"
