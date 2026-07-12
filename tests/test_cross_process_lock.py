"""The cross-process chronicle write lock.

A THREADED test proves nothing here: the in-process RLock already passes those.
The hazard is a SECOND OS PROCESS. These tests spawn real ones.

Anthony ratified the fix by voice, 2026-07-12: "advisory file lock now
(fcntl.flock LOCK_EX, every writer, every process)."
"""

import multiprocessing as mp
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sovereign_stack import provenance
from sovereign_stack.memory import ExperientialMemory

SRC = str(Path(provenance.__file__).resolve().parents[1])  # .../src

N_APPENDS = 40


def test_src_resolves_to_a_real_package_dir():
    """SRC is handed to spawned children. If it points at the repo root instead
    of src/, their sys.path.insert is a no-op and they silently import whatever
    the editable install points at — which is PRODUCTION. Assert it is src/."""
    assert (Path(SRC) / "sovereign_stack" / "provenance.py").exists(), SRC


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
        with pytest.raises(ValueError), provenance.chronicle_write_lock(chronicle):
            with provenance.chronicle_write_lock(chronicle):
                raise ValueError("boom")
        assert not provenance._FLOCK_DEPTH, provenance._FLOCK_DEPTH
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


# ── The reflections tree: a SECOND cross-process race, on a SIBLING of the
# chronicle. synthesis_daemon (a separate launchd process, 04:17 daily) appends
# to reflections/<date>.jsonl; reflection_ack rewrites that whole file from the
# server process. An adversarial review reproduced 9 of 120 daemon reflections
# silently lost. Both sides must take the SAME lockfile — a chronicle-rooted
# lock would serialize against nothing, because reflections are not under it.


def _refl_appender(refl_dir: str, n: int, locked: bool) -> None:
    sys.path.insert(0, SRC)
    import json as _json
    from pathlib import Path as _P

    from sovereign_stack import provenance as prov

    d = _P(refl_dir)
    path = d / "2026-07-12.jsonl"
    for i in range(n):
        rec = _json.dumps({"id": f"r{i}", "observation": f"reflection {i}", "ack_status": "unread"})
        if locked:
            with prov.scoped_write_lock(prov.reflections_lock_path(d)):
                with path.open("a") as fh:
                    fh.write(rec + "\n")
        else:
            with path.open("a") as fh:
                fh.write(rec + "\n")
        time.sleep(0.002)


def _refl_rewriter(refl_dir: str, rounds: int, locked: bool) -> None:
    """ack_reflection's shape: read whole file -> rebuild -> tmp -> replace."""
    sys.path.insert(0, SRC)
    from pathlib import Path as _P

    from sovereign_stack import provenance as prov

    d = _P(refl_dir)
    path = d / "2026-07-12.jsonl"

    def _pass():
        if not path.exists():
            return
        lines = path.read_text().splitlines()
        time.sleep(0.01)
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""))
        tmp.replace(path)

    for _ in range(rounds):
        if locked:
            with prov.scoped_write_lock(prov.reflections_lock_path(d)):
                _pass()
        else:
            _pass()
        time.sleep(0.005)


def _run_reflections_race(tmp_path, locked: bool) -> int:
    d = tmp_path / "reflections"
    d.mkdir(parents=True)
    (d / "2026-07-12.jsonl").write_text("")
    ctx = mp.get_context("spawn")
    a = ctx.Process(target=_refl_appender, args=(str(d), N_APPENDS, locked))
    r = ctx.Process(target=_refl_rewriter, args=(str(d), 40, locked))
    r.start()
    time.sleep(0.02)
    a.start()
    a.join(timeout=120)
    r.join(timeout=120)
    return sum(
        1 for line in (d / "2026-07-12.jsonl").read_text().splitlines() if '"reflection ' in line
    )


class TestReflectionsCrossProcessLoss:
    def test_unlocked_reflections_actually_lose(self, tmp_path):
        """The gate must be able to fail. This is the bug an adversarial review
        reproduced on Anthony's live topology: 9 of 120 daemon reflections gone."""
        assert _run_reflections_race(tmp_path, locked=False) < N_APPENDS, (
            "no reflections were lost unlocked — the race did not set up, "
            "and the sibling test below is therefore vacuous"
        )

    def test_locked_reflections_lose_nothing(self, tmp_path):
        survived = _run_reflections_race(tmp_path, locked=True)
        assert survived == N_APPENDS, f"{N_APPENDS - survived} reflections LOST across processes"

    def test_both_sides_resolve_to_the_same_lockfile(self, tmp_path):
        """The trap: reflections live at ~/.sovereign/reflections, a SIBLING of
        chronicle/. If ack_reflection locked the chronicle root and the daemon
        locked the reflections dir, they would take different files and serialize
        against nothing — a fix that passes a naive test and still loses."""
        d = tmp_path / "reflections"
        assert provenance.reflections_lock_path(d) == d / ".write.lock"
        assert provenance.reflections_lock_path(d) != provenance.chronicle_lock_path(
            tmp_path / "chronicle"
        )


class TestTheLockNeverBlocksTheEventLoop:
    """THE FREEZE, REINTRODUCED AND CAUGHT. _acquire_flock spin-waits up to 30s.
    Six mutators took it INLINE in the async dispatcher, so any OTHER process
    holding the lock froze every seat on the stack — the 2026-07-10 wedge, made
    worse because a foreign process could now trigger it. Measured on this tree:
    the inline shape produces ZERO heartbeat ticks. Every async caller offloads.
    """

    def _foreign_holder(self, tmp_path, chronicle, secs=2.0):
        code = (
            f"import sys,time; sys.path.insert(0,{SRC!r});"
            "from pathlib import Path;"
            "from sovereign_stack import provenance as p;"
            f"h=p.chronicle_write_lock(Path({str(chronicle)!r})); h.__enter__();"
            f"open({str(tmp_path / 'held')!r},'w').close(); time.sleep({secs});"
            "h.__exit__(None,None,None)"
        )
        proc = subprocess.Popen([sys.executable, "-c", code])
        for _ in range(400):
            if (tmp_path / "held").exists():
                break
            time.sleep(0.01)
        return proc

    def _ticks_while_blocked(self, chronicle, offload: bool) -> int:
        import asyncio

        mem = ExperientialMemory(root=str(chronicle))
        ticks = 0

        async def drive():
            nonlocal ticks

            async def heartbeat():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.02)
                    ticks += 1

            hb = asyncio.create_task(heartbeat())
            if offload:
                await asyncio.to_thread(mem.record_insight, "loop", "offloaded", 0.5)
            else:
                mem.record_insight("loop", "inline", 0.5)
            hb.cancel()

        asyncio.run(drive())
        return ticks

    def test_the_inline_shape_freezes_the_loop(self, tmp_path):
        """THE GATE MUST BE ABLE TO FAIL. Calling a chronicle mutator inline on
        the loop, while another PROCESS holds the lock, stalls everything."""
        chronicle = tmp_path / "chronicle"
        (chronicle / "insights").mkdir(parents=True)
        proc = self._foreign_holder(tmp_path, chronicle)
        ticks = self._ticks_while_blocked(chronicle, offload=False)
        proc.wait(timeout=15)
        assert ticks == 0, (
            f"expected a total freeze on the inline shape but got {ticks} ticks — "
            "the race did not set up and the sibling test is vacuous"
        )

    def test_offloaded_the_loop_keeps_ticking(self, tmp_path):
        """The fix: server.py offloads every mutator, so a foreign holder can
        never stall the loop."""
        chronicle = tmp_path / "chronicle"
        (chronicle / "insights").mkdir(parents=True)
        proc = self._foreign_holder(tmp_path, chronicle)
        ticks = self._ticks_while_blocked(chronicle, offload=True)
        proc.wait(timeout=15)
        assert ticks > 20, (
            f"only {ticks} ticks while blocked on another PROCESS's lock — the freeze is back"
        )
