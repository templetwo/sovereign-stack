"""The 2026-09-06 incremental-sweep release. One class per build item.

Every test here FAILS on `0ed12da` (the deployed release) and PASSES on the
fix. `refresh_in_flight`, `scan_budget_seconds` and the watermark helpers DO
NOT EXIST on `0ed12da`, so the tests that need them reach for them INSIDE the
test body: a module-scope import of a missing name turns the whole file into a
collection error, and a collection error proves the file does not load, not
that the behaviour is absent — the convention `test_rc_review_round3.py`
already sets.

WHAT THE LANE MEASURED, so a reader knows what these tests are defending. The
deployed sweep over the live corpus (8,765 ledger rows / 6.09 MB, 3,975 honks,
240 spool rows, 295 proposal files, 161 thread shards) took 265 s, at full CPU,
in the process that answers every stack tool call, and a no-change rescan took
the same 268 s. On this branch the same corpus sweeps in 0.34 s and rescans in
0.04 s. The dominant cost was never the source files — it was `open_signal`
re-folding the WHOLE ledger once per source record.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from sovereign_stack import signal_ledger as sl


def _guardian_ok(root: Path) -> None:
    (root / "guardian").mkdir(parents=True, exist_ok=True)
    (root / "guardian" / "status.json").write_text('{"issues": []}', encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _honks(root: Path, n: int, start: int = 0) -> None:
    _write_jsonl(
        root / "nape" / "honks.jsonl",
        [
            {
                "honk_id": f"h{i}",
                "timestamp": "2026-09-01T00:00:00Z",
                "pattern": "drift",
                "observation": f"observation {i}",
            }
            for i in range(start, start + n)
        ],
    )


def _thread_shard(root: Path, domain: str, rows: list[dict]) -> Path:
    path = root / "chronicle" / "open_threads" / domain / "log.jsonl"
    _write_jsonl(path, rows)
    return path


def _marker(root: Path) -> dict:
    return json.loads(sl.scan_marker_path(root).read_text())


def _sweep(root: Path, **kw):
    return sl.scan_all(root, guardian_provider=lambda: {"issues": []}, **kw)


# ══════════════════════════════════════════════════════════════════════════
# 1 — PER-SHARD WATERMARKS
# ══════════════════════════════════════════════════════════════════════════


class TestTheCertificateRecordsWhatItRead:
    def test_the_certificate_carries_a_watermark_per_shard(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 3)
        _thread_shard(root, "alpha", [{"thread_id": "t1", "question": "q"}])
        _sweep(root)
        marks = _marker(root)["watermarks"]
        honk = {s["path"]: s for s in marks["honk"]["shards"]}
        assert "nape/honks.jsonl" in honk
        entry = honk["nape/honks.jsonl"]
        st = (root / "nape" / "honks.jsonl").stat()
        assert entry["size"] == st.st_size
        assert entry["mtime_ns"] == st.st_mtime_ns
        assert entry["rows"] == 3
        assert len(marks["honk"]["digest"]) == 64
        thread_paths = {s["path"] for s in marks["thread"]["shards"]}
        assert thread_paths == {"chronicle/open_threads/alpha/log.jsonl"}

    def test_an_unchanged_shard_is_not_read_again(self, tmp_sovereign_root, monkeypatch):
        """THE WHOLE POINT, stated as behaviour rather than as a timing.

        A duration is not a receipt — it varies with the box. What a test can
        assert is that the bytes were never opened.
        """
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 3)
        _write_jsonl(root / "watchman" / "spool.jsonl", [{"sweep_id": "s1"}])
        _sweep(root)

        real = sl._read_source_jsonl
        opened: list[str] = []

        def counting(path):
            opened.append(str(path))
            return real(path)

        monkeypatch.setattr(sl, "_read_source_jsonl", counting)
        _sweep(root)
        assert not any("honks.jsonl" in p for p in opened), opened
        assert not any("spool.jsonl" in p for p in opened), opened

    def test_a_changed_shard_is_read_and_its_new_rows_fold(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 2)
        first = _sweep(root)
        assert first["counts"]["honk"] == 2
        _honks(root, 4)
        second = _sweep(root)
        assert second["counts"]["honk"] == 2, "the two new honks did not fold"
        entry = {s["path"]: s for s in _marker(root)["watermarks"]["honk"]["shards"]}
        assert entry["nape/honks.jsonl"]["rows"] == 4

    def test_a_new_shard_is_read_even_though_the_others_did_not_move(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        (root / "daemons" / "halts").mkdir(parents=True, exist_ok=True)
        (root / "daemons" / "halts" / "a.md").write_text("a\n", encoding="utf-8")
        assert _sweep(root)["counts"]["halt"] == 1
        (root / "daemons" / "halts" / "b.md").write_text("b\n", encoding="utf-8")
        assert _sweep(root)["counts"]["halt"] == 1
        paths = {s["path"] for s in _marker(root)["watermarks"]["halt"]["shards"]}
        assert paths == {"daemons/halts/a.md", "daemons/halts/b.md"}

    def test_a_shard_that_lost_read_permission_is_not_carried_forward_as_ok(
        self, tmp_sovereign_root
    ):
        """`chmod 000` MOVES NEITHER SIZE NOR MTIME.

        A stat-only comparison would carry the source's old "ok" forward while
        the file cannot be opened at all — a healthy status for a source that
        is now unreadable, which is the exact fail-open shape this module
        exists to close. The readability probe is what makes the skip safe.
        """
        root = tmp_sovereign_root
        _guardian_ok(root)
        _write_jsonl(root / "watchman" / "spool.jsonl", [{"sweep_id": "s1"}])
        _sweep(root)
        assert _marker(root)["source_status"]["watchman"] == "ok"
        spool = root / "watchman" / "spool.jsonl"
        before = spool.stat()
        os.chmod(spool, 0o000)
        try:
            if os.access(spool, os.R_OK):  # pragma: no cover - root can always read
                pytest.skip("this user can read a 000 file; the probe cannot bite")
            _sweep(root)
            after = spool.stat()
            assert (after.st_size, after.st_mtime_ns) == (
                before.st_size,
                before.st_mtime_ns,
            ), "the fixture changed the stat, so this proves nothing"
            assert _marker(root)["source_status"]["watchman"] != "ok"
        finally:
            os.chmod(spool, 0o644)

    def test_a_vanished_shard_forces_the_source_to_be_read(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _write_jsonl(root / "watchman" / "spool.jsonl", [{"sweep_id": "s1"}])
        _sweep(root)
        (root / "watchman" / "spool.jsonl").unlink()
        _sweep(root)
        assert _marker(root)["watermarks"]["watchman"]["shards"] == []

    def test_the_watermark_list_is_bounded_and_says_so(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        _guardian_ok(root)
        monkeypatch.setenv(sl.WATERMARK_MAX_SHARDS_ENV, "2")
        for i in range(5):
            _thread_shard(root, f"d{i}", [{"thread_id": f"t{i}", "question": "q"}])
        _sweep(root)
        block = _marker(root)["watermarks"]["thread"]
        assert block["shard_count"] == 5
        assert len(block["shards"]) <= 2
        assert block["elided"] == 5 - len(block["shards"])
        assert block["window_days"] == sl.watermark_window_days()
        assert "elided" in block["note"] and "digest" in block["note"]
        assert len(block["digest"]) == 64

    def test_an_elided_shard_is_re_read_rather_than_assumed_unchanged(
        self, tmp_sovereign_root, monkeypatch
    ):
        """The bound costs TIME, never correctness."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        monkeypatch.setenv(sl.WATERMARK_MAX_SHARDS_ENV, "1")
        for i in range(3):
            _thread_shard(root, f"d{i}", [{"thread_id": f"t{i}", "question": "q"}])
        _sweep(root)
        real = sl._read_source_jsonl
        opened: list[str] = []

        def counting(path):
            opened.append(str(path))
            return real(path)

        monkeypatch.setattr(sl, "_read_source_jsonl", counting)
        _sweep(root)
        assert sum("open_threads" in p for p in opened) == 3


class TestTheThreadSourceSkipsAllOrNothing:
    """PER-SHARD SKIPPING IS UNSOUND FOR A CROSS-SHARD FOLD.

    `scan_threads` decides a thread's state from the latest record ACROSS every
    shard (F7). Reading a subset therefore decides from a subset of the
    evidence. The guard that looks sufficient — "ignore a candidate older than
    the ledger row's produced_at" — is a no-op, because `open_signal` stamps
    produced_at at FIRST OPEN and returns early on every rescan without
    updating it, so the stored value is first-seen, not latest-across-shards.
    """

    def test_one_touched_shard_makes_every_thread_shard_be_read(
        self, tmp_sovereign_root, monkeypatch
    ):
        root = tmp_sovereign_root
        _guardian_ok(root)
        for i in range(3):
            _thread_shard(root, f"d{i}", [{"thread_id": f"t{i}", "question": "q"}])
        _sweep(root)
        _thread_shard(root, "d0", [{"thread_id": "t0", "question": "q", "resolved": True}])
        real = sl._read_source_jsonl
        opened: list[str] = []

        def counting(path):
            opened.append(str(path))
            return real(path)

        monkeypatch.setattr(sl, "_read_source_jsonl", counting)
        _sweep(root)
        assert sum("open_threads" in p for p in opened) == 3

    def test_a_wholly_unchanged_thread_store_is_skipped(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        _guardian_ok(root)
        for i in range(3):
            _thread_shard(root, f"d{i}", [{"thread_id": f"t{i}", "question": "q"}])
        _sweep(root)
        real = sl._read_source_jsonl
        opened: list[str] = []

        def counting(path):
            opened.append(str(path))
            return real(path)

        monkeypatch.setattr(sl, "_read_source_jsonl", counting)
        _sweep(root)
        assert not any("open_threads" in p for p in opened)

    def test_the_cross_shard_resolution_still_wins_after_an_incremental_sweep(
        self, tmp_sovereign_root
    ):
        """F7's own fixture, run through the incremental path.

        `a` resolves the thread on 2026-09-01; `b` carries an OLDER open record
        and is the shard that gets touched. The latest record across both
        shards is still the resolution, so the signal must end up acted — which
        is only true if touching `b` caused `a` to be read as well.
        """
        root = tmp_sovereign_root
        _guardian_ok(root)
        _thread_shard(
            root,
            "a",
            [
                {
                    "thread_id": "shared",
                    "question": "q",
                    "timestamp": "2026-09-01T00:00:00Z",
                    "resolved": True,
                }
            ],
        )
        _thread_shard(
            root,
            "b",
            [{"thread_id": "shared", "question": "q", "timestamp": "2026-08-01T00:00:00Z"}],
        )
        _sweep(root)
        sid = sl.signal_id_for("thread", "shared")
        assert sl.load_latest(root)[sid]["state"] == "acted"
        _thread_shard(
            root,
            "b",
            [
                {"thread_id": "shared", "question": "q", "timestamp": "2026-08-01T00:00:00Z"},
                {"thread_id": "other", "question": "q2", "timestamp": "2026-08-02T00:00:00Z"},
            ],
        )
        _sweep(root)
        assert sl.load_latest(root)[sid]["state"] == "acted", (
            "an incremental sweep reopened a thread from the older shard alone"
        )


class TestAPreviousReleaseCertificateStaysValid:
    """DEPLOY GUARD, and it is deliberately not a red/green item.

    The certificate on the live box carries exactly the eight keys `0ed12da`
    wrote. Putting `watermarks` or `partial` in MARKER_REQUIRED would make that
    file `marker_invalid` the moment this branch deploys, and
    `_refresh_decision` answers marker_invalid with REFUSE plus a quarantine
    instruction — the release would hand the operator a recovery procedure for
    an undamaged ledger. This test passes on `0ed12da` too, trivially; it is
    here to fail loudly if anyone ever promotes a new field to required.
    """

    def test_a_certificate_with_no_watermarks_is_still_valid(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 2)
        _sweep(root)
        marker = _marker(root)
        for field in ("watermarks", "partial", "unreached"):
            marker.pop(field, None)
        sl.scan_marker_path(root).write_text(json.dumps(marker, sort_keys=True) + "\n")
        parsed, error = sl._read_scan_marker(root)
        assert error is None and parsed is not None
        field = sl.heartbeat_field(root)
        assert field["ingestion"] == "ok"

    def test_a_certificate_with_no_watermarks_produces_a_full_sweep(
        self, tmp_sovereign_root, monkeypatch
    ):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 2)
        _sweep(root)
        marker = _marker(root)
        marker.pop("watermarks", None)
        sl.scan_marker_path(root).write_text(json.dumps(marker, sort_keys=True) + "\n")
        real = sl._read_source_jsonl
        opened: list[str] = []

        def counting(path):
            opened.append(str(path))
            return real(path)

        monkeypatch.setattr(sl, "_read_source_jsonl", counting)
        _sweep(root)
        assert any("honks.jsonl" in p for p in opened)

    def test_a_present_but_broken_watermark_block_invalidates_the_certificate(
        self, tmp_sovereign_root
    ):
        """Absent means "nothing known". Present and unparseable is a claim
        about what was read that we cannot check, and is refused."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 1)
        _sweep(root)
        marker = _marker(root)
        marker["watermarks"] = {"honk": {"digest": "short", "shards": []}}
        sl.scan_marker_path(root).write_text(json.dumps(marker, sort_keys=True) + "\n")
        parsed, error = sl._read_scan_marker(root)
        assert parsed is None
        assert error and "marker_invalid" in error

    def test_a_rewritten_ledger_disqualifies_the_watermarks(self, tmp_sovereign_root):
        """A watermark says "those rows are already in THIS ledger".

        Strip `origin` from every row — the R1 repair case — and the sources
        have not moved, so a stat-only skip would skip them and the provenance
        backfill would never run. The certificate no longer describes the
        ledger, so its watermarks do not apply.
        """
        root = tmp_sovereign_root
        _guardian_ok(root)
        # THE BACKFILL ONLY FIRES WHERE THE SOURCE CAN SUPPLY A CLAIM ID, so
        # the honk carries one — otherwise this would pass for the wrong reason.
        _write_jsonl(
            root / "nape" / "honks.jsonl",
            [
                {
                    "honk_id": "h0",
                    "timestamp": "2026-09-01T00:00:00Z",
                    "pattern": "drift",
                    "observation": "body",
                    "claim_id": "d" * 64,
                }
            ],
        )
        _sweep(root)
        sid = sl.signal_id_for("honk", "h0")
        assert sl.load_latest(root)[sid]["origin"]["claim_id"] == "d" * 64
        path = sl.ledger_path(root)
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        for row in rows:
            row.pop("origin", None)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        _sweep(root)
        assert sl.load_latest(root)[sid].get("origin", {}).get("claim_id") == "d" * 64, (
            "the rescan skipped the sources and never re-provenanced the row"
        )


# ══════════════════════════════════════════════════════════════════════════
# 2 — OFF THE LOOP
# ══════════════════════════════════════════════════════════════════════════


class TestTheSweepRunsOffTheCallersThread:
    def test_the_sweep_does_not_run_on_the_calling_thread(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        seen: list[int] = []
        real = sl.scan_all

        def watched(*a, **kw):
            seen.append(threading.get_ident())
            return real(*a, **kw)

        sl.scan_all = watched  # noqa: SLF001 - restored below
        try:
            assert sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []}) is None
        finally:
            sl.scan_all = real
        assert seen and all(t != threading.get_ident() for t in seen)

    def test_a_read_during_a_refresh_serves_the_last_certificate(self, tmp_sovereign_root):
        """NEVER A ZERO, NEVER STALE-BUT-OK.

        The certificate has aged out, so the pre-release answer was
        ingestion:"stale" with every count nulled. With the replacement already
        being computed, the previous numbers are the best honest answer there
        is — published under `refreshing`, with the old `scanned_at` still
        attached so nobody can read them as fresh.
        """
        root = tmp_sovereign_root
        _guardian_ok(root)
        sl.open_signal(
            source="halt", native_id="fixture", produced_at="2026-08-01T00:00:00Z", root=root
        )
        _sweep(root)
        marker = _marker(root)
        marker["scanned_at"] = "2020-01-01T00:00:00.000Z"
        sl.scan_marker_path(root).write_text(json.dumps(marker, sort_keys=True) + "\n")

        release = threading.Event()
        real = sl.scan_all

        def slow(*a, **kw):
            release.wait(10)
            return real(*a, **kw)

        sl.scan_all = slow
        try:
            assert (
                sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []}, wait=0.05) is None
            )
            inflight = sl.refresh_in_flight(root)
            assert inflight and inflight["started_at"]
            field = sl.heartbeat_field(root)
            assert field["ingestion"] == "refreshing"
            assert field["measured"] is True
            assert field["total"] == 1, "a refresh blanked a perfectly good count"
            assert field["scanned_at"] == "2020-01-01T00:00:00.000Z"
            assert field["refresh_started_at"] == inflight["started_at"]
            assert "scan_stale" in (field["error"] or "")
        finally:
            release.set()
            sl.scan_all = real
            for _ in range(200):
                if sl.refresh_in_flight(root) is None:
                    break
                time.sleep(0.02)

    def test_a_second_reader_does_not_wait_on_a_running_sweep(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        release = threading.Event()
        real = sl.scan_all

        def slow(*a, **kw):
            release.wait(10)
            return real(*a, **kw)

        sl.scan_all = slow
        try:
            sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []}, wait=0.05)
            assert sl.refresh_in_flight(root) is not None
            started = time.monotonic()
            assert sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []}) is None
            assert time.monotonic() - started < 1.0, "the second reader blocked"
        finally:
            release.set()
            sl.scan_all = real
            for _ in range(200):
                if sl.refresh_in_flight(root) is None:
                    break
                time.sleep(0.02)

    def test_a_refresh_that_raises_is_an_error_with_a_null_total(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        real = sl.scan_all

        def boom(*a, **kw):
            raise RuntimeError("fixture sweep explosion")

        sl.scan_all = boom
        try:
            field = sl.heartbeat_field(root, scan=True)
        finally:
            sl.scan_all = real
        assert field["total"] is None
        assert "fixture sweep explosion" in field["error"]
        assert field["ingestion"] == "error"

    def test_integrity_is_judged_before_any_worker_is_started(self, tmp_sovereign_root):
        """INTEGRITY BEFORE REFRESH (review N2) SURVIVES THE MOVE.

        A refusal reachable only by waiting on a future would be a refusal the
        caller can time out of. The decision is made on the caller's thread,
        and no sweep is queued at all.
        """
        root = tmp_sovereign_root
        _guardian_ok(root)
        sl.open_signal(
            source="halt", native_id="fixture", produced_at="2026-08-01T00:00:00Z", root=root
        )
        _sweep(root)
        sl.ledger_path(root).write_text("")
        real = sl.scan_all
        calls: list[int] = []

        def counted(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        sl.scan_all = counted
        try:
            error = sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []})
        finally:
            sl.scan_all = real
        assert error and "ledger_truncated" in error and "quarantine" in error
        assert calls == [], "a sweep was queued over a damaged ledger"
        assert sl.refresh_in_flight(root) is None


# ══════════════════════════════════════════════════════════════════════════
# 3 — TIME BUDGET
# ══════════════════════════════════════════════════════════════════════════


class TestAnOverrunSweepSaysSoInsteadOfShortening:
    def test_a_zero_budget_writes_a_partial_certificate(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 2)
        out = _sweep(root, budget_seconds=0.000001)
        assert out["partial"] is True
        assert out["unreached"], "a partial sweep that names nothing"
        marker = _marker(root)
        assert marker["partial"] is True
        assert set(marker["unreached"]) <= set(sl.SOURCES)
        for name in marker["unreached"]:
            assert marker["source_status"][name].startswith("partial:")

    def test_a_partial_certificate_reads_as_partial_with_a_null_total(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 2)
        _sweep(root, budget_seconds=0.000001)
        field = sl.heartbeat_field(root)
        assert field["ingestion"] == "partial"
        assert field["total"] is None
        assert field["stale_24h"] is None
        assert field["partial"] is True
        assert field["unreached"]
        assert "partial_scan:" in field["error"]
        assert set(field["by_source"].values()) == {None}

    def test_the_tool_surface_carries_the_partial_state(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 2)
        _sweep(root, budget_seconds=0.000001)
        out = json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))
        assert out["ok"] is True
        assert out["ingestion"] == "partial"
        assert out["total"] is None
        assert out["unreached"]

    def test_a_partial_sweep_keeps_the_progress_it_made(self, tmp_sovereign_root):
        """The next sweep RESUMES. A budget that made a partial certificate and
        then threw away the watermarks would restart from nothing every time
        and never converge."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 2)
        _write_jsonl(root / "watchman" / "spool.jsonl", [{"sweep_id": "s1"}])
        _sweep(root)
        before = _marker(root)["watermarks"]
        _sweep(root, budget_seconds=0.000001)
        after = _marker(root)["watermarks"]
        assert after.get("honk") == before.get("honk")
        assert after.get("watchman") == before.get("watchman")

    def test_a_later_complete_sweep_clears_partial(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 2)
        _sweep(root, budget_seconds=0.000001)
        assert sl.heartbeat_field(root)["ingestion"] == "partial"
        _sweep(root)
        field = sl.heartbeat_field(root)
        assert field["partial"] is False
        assert field["ingestion"] == "ok"
        assert field["total"] == 2

    def test_a_partial_certificate_that_names_nothing_is_invalid(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 1)
        _sweep(root)
        marker = _marker(root)
        marker["partial"] = True
        marker["unreached"] = []
        sl.scan_marker_path(root).write_text(json.dumps(marker, sort_keys=True) + "\n")
        parsed, error = sl._read_scan_marker(root)
        assert parsed is None and error and "marker_invalid" in error

    def test_the_budget_is_configurable(self, tmp_sovereign_root, monkeypatch):
        assert sl.scan_budget_seconds() == sl.DEFAULT_SCAN_BUDGET_SECONDS
        monkeypatch.setenv(sl.SCAN_BUDGET_ENV, "7")
        assert sl.scan_budget_seconds() == 7.0
        monkeypatch.setenv(sl.SCAN_BUDGET_ENV, "not a number")
        assert sl.scan_budget_seconds() == sl.DEFAULT_SCAN_BUDGET_SECONDS


# ══════════════════════════════════════════════════════════════════════════
# THE COST THAT WAS ACTUALLY THERE
# ══════════════════════════════════════════════════════════════════════════


class TestTheSweepFoldsTheLedgerOnceNotPerRecord:
    def test_a_sweep_never_refolds_the_whole_ledger_per_record(
        self, tmp_sovereign_root, monkeypatch
    ):
        """265 s, and this is where every one of them went.

        `open_signal` called `load_latest` — a full parse and validate of the
        entire ledger — once per source record, inside its append lock, to
        answer "have I seen this id?". `scan_honks`, `scan_proposals` and
        `scan_threads` each called it a second time per record on top. On the
        live corpus that is 8,765 rows re-folded ~8,000 times.
        """
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honks(root, 25)
        calls: list[int] = []
        real = sl.load_latest

        def counted(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        monkeypatch.setattr(sl, "load_latest", counted)
        _sweep(root)
        assert calls == [], f"{len(calls)} whole-ledger re-folds during one sweep"

    def test_a_foreign_append_during_a_sweep_is_still_seen(self, tmp_sovereign_root):
        """THE INDEX IS NOT ALLOWED TO BE A SECOND MODEL OF THE LEDGER.

        It is refreshed by TAILING the file under the append lock, so a row
        written by anyone else lands in the fold on the next lookup — which is
        what keeps `open_signal` idempotent (reviewer finding 8) rather than
        minting a duplicate open from a stale cache.
        """
        root = tmp_sovereign_root
        _guardian_ok(root)
        with sl._scan_index(root):
            sl.open_signal(
                source="halt", native_id="a.md", produced_at="2026-09-01T00:00:00Z", root=root
            )
            assert sl._latest_view(root)
            # A DIFFERENT WRITER, not going through the index at all.
            other = sl._row(
                signal_id=sl.signal_id_for("halt", "b.md"),
                source="halt",
                produced_at="2026-09-01T00:00:00Z",
                owner="watch-2/3",
                state="open",
                reason=None,
                closed_by=None,
                closed_at=None,
                updated_at="2026-09-01T00:00:00Z",
            )
            with sl.ledger_path(root).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(other, sort_keys=True) + "\n")
            assert sl.signal_id_for("halt", "b.md") in sl._latest_view(root)
            assert (
                sl.open_signal(
                    source="halt",
                    native_id="b.md",
                    produced_at="2026-09-01T00:00:00Z",
                    root=root,
                )
                is None
            ), "the sweep minted a duplicate open over a foreign append"

    def test_the_index_does_not_leak_out_of_the_sweep(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        with sl._scan_index(root):
            assert sl._SCAN_INDEX.get() is not None
        assert sl._SCAN_INDEX.get() is None
