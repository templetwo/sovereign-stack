"""The 2026-07-10 freeze: a file receipt whose bytes never arrive.

On 2026-07-10 a record_insight carrying a file receipt that pointed at an
iCloud dataless stub took the whole stack down. stat() called it an ordinary
regular file; the read blocked forever on materialization; record_insight is
called synchronously from the async dispatch; sse_server mounts that same
Server object on one uvicorn worker. One event loop, one blocking read, and
every seat wedged — heartbeat degraded, tools=-1.

Reproducing it needs a path that stat() calls a regular file and open() never
answers for. A fifo blocks on open() but stat() names it a fifo, so the old
code rejected it before it could hurt anyone. So we lie to stat() — one patch,
at the syscall both the old code (Path.is_file) and the new code
(classify_file_target) go through — and leave the block itself real: a real
fifo with no writer, opened for real, blocking in the kernel for real.

The dispatch runs on its own event loop in its own thread, and the test thread
waits on a wall clock. That is deliberate. On the unfixed code the loop blocks
INSIDE the coroutine, where asyncio.wait_for can never fire — a test that
awaited it would hang forever instead of failing. A deploy gate has to be able
to fail.
"""

import asyncio
import errno
import hashlib
import os
import stat as stat_module
import threading
import time
from pathlib import Path

import pytest

from sovereign_stack import provenance
from tests.test_nape_autohook import _isolated_server

# Enough loop ticks to prove the loop kept running while the receipt was being
# hashed, short enough that a blocked loop can't reach it by accident.
_TICK_SECONDS = 0.01
_MIN_TICKS = 5
# The dispatch must come back in about the hash budget. Anything past this and
# the loop is wedged, which is the bug.
_DISPATCH_DEADLINE_SECONDS = 10.0


@pytest.fixture
def wedged_file(tmp_path, monkeypatch):
    """A path stat() calls a regular file and open() blocks on forever.

    The fifo is real and has no writer, so open(O_RDONLY) blocks in the
    kernel — the same shape as an iCloud placeholder waiting on a download
    that never lands. os.stat is patched ONLY for this one path.
    """
    fifo = tmp_path / "dataless.bin"
    os.mkfifo(fifo)
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if str(path) == str(fifo):
            fields = list(st)
            fields[stat_module.ST_MODE] = (st.st_mode & ~stat_module.S_IFMT(st.st_mode)) | (
                stat_module.S_IFREG
            )
            fields[stat_module.ST_SIZE] = 1024
            return os.stat_result(fields)
        return st

    monkeypatch.setattr(os, "stat", fake_stat)
    return fifo


def _drive_dispatch(srv, name, arguments, timeout):
    """Run one _dispatch_tool on a private event loop in a private thread.

    Returns (text, ticks, wedged). `ticks` counts how many times a concurrent
    task got to run while the dispatch was in flight — loop liveness. `wedged`
    is True when the loop never came back, which is the freeze.
    """
    box = {}

    async def _main():
        ticks = 0
        stop = asyncio.Event()

        async def _ticker():
            nonlocal ticks
            while not stop.is_set():
                await asyncio.sleep(_TICK_SECONDS)
                ticks += 1

        beat = asyncio.create_task(_ticker())
        try:
            result = await srv._dispatch_tool(name, arguments)
        finally:
            stop.set()
            beat.cancel()
        box["text"] = result[0].text
        box["ticks"] = ticks

    runner = threading.Thread(target=lambda: asyncio.run(_main()), daemon=True)
    runner.start()
    runner.join(timeout)
    if runner.is_alive():
        return None, 0, True
    return box.get("text"), box.get("ticks", 0), False


class TestReceiptFreeze:
    def test_wedged_file_receipt_does_not_freeze_the_event_loop(self, wedged_file, monkeypatch):
        """The gate. Fails on main: the loop never comes back.

        Asserts three things, and all three have to hold:
          1. the dispatch returns at all (not wedged),
          2. a concurrent task kept running while it was in flight
             (the loop was never blocked — proves the to_thread offload),
          3. the receipt landed ATTESTED with a reason, never "verified"
             (proves the degradation is honest — see the third assertion,
             which is the one that matters most).
        """
        # raising=False on purpose: this test has to be able to RUN against the
        # unfixed code, where the constant does not exist yet. Against main it
        # then reaches the unbounded read_bytes(), the loop wedges, the join
        # below times out, and the first assertion fires. That is the gate.
        monkeypatch.setattr(provenance, "READ_TIMEOUT_SECONDS", 0.3, raising=False)

        with _isolated_server("freeze-test") as (srv, tmp_root):
            text, ticks, wedged = _drive_dispatch(
                srv,
                "record_insight",
                {
                    "domain": "test",
                    "content": "receipt pointing at bytes that never arrive",
                    "verified_by": [{"kind": "file", "ref": str(wedged_file), "sha256": "a" * 64}],
                },
                timeout=_DISPATCH_DEADLINE_SECONDS,
            )

            assert not wedged, (
                "THE FREEZE: record_insight never returned. A blocking receipt "
                "re-hash is running on the event loop."
            )
            assert ticks >= _MIN_TICKS, (
                f"event loop was blocked during the write (only {ticks} ticks in "
                f"~{_DISPATCH_DEADLINE_SECONDS}s) — the hash is bounded but still "
                "running inline on the loop"
            )
            assert "recorded" in text.lower(), f"the write should degrade, not die: {text}"

            entry = _last_entry(tmp_root)
            receipt = entry["verified_by"][0]
            assert receipt["checked_at_write"] == "attested", (
                "a receipt whose bytes were never read must not wear a stamp it "
                f"did not earn (got {receipt['checked_at_write']!r})"
            )
            assert receipt["checked_at_write"] != "verified"
            assert "hash" in receipt["unverified_reason"].lower()

    def test_fifo_receipt_is_refused_before_anything_opens_it(self, tmp_path):
        """A fifo is not a file whose bytes we can hash. Refuse it, cheaply."""
        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)
        started = time.monotonic()
        with pytest.raises(provenance.ReceiptError, match="not a regular file"):
            provenance.preflight_file_receipt(
                {"kind": "file", "ref": str(fifo), "sha256": "b" * 64}, 1
            )
        assert time.monotonic() - started < 1.0, "the refusal opened the fifo"

    def test_a_300mb_file_that_hashes_fast_still_verifies(self, tmp_path):
        """
        The entropy program ships raw NDJSON as its primary record (house law
        #7). A 300 MB run file hashes in a fraction of the read budget — it must
        stamp "verified", not be refused. The wall clock already bounds the read;
        a size cap adds no hang protection, it only breaks a real workflow.
        """
        big = tmp_path / "run.ndjson"
        chunk = b"x" * (1024 * 1024)
        digest = hashlib.sha256()
        with open(big, "wb") as f:
            for _ in range(300):
                f.write(chunk)
                digest.update(chunk)
        assert big.stat().st_size == 300 * 1024 * 1024

        receipt = {"kind": "file", "ref": str(big), "sha256": digest.hexdigest()}
        provenance.preflight_file_receipt(receipt, 1)  # must not raise

        started = time.monotonic()
        stamped = provenance.verify_receipt_at_write(receipt, tmp_path / "chronicle", 1)
        elapsed = time.monotonic() - started

        assert stamped["checked_at_write"] == "verified", (
            "a big file that hashes inside the budget earned its verification; "
            "rejecting it would have broken the entropy program's raw NDJSON"
        )
        assert "unverified_reason" not in stamped
        assert elapsed < provenance.READ_TIMEOUT_SECONDS

    @pytest.mark.skipif(
        not hasattr(os.stat(__file__), "st_flags"),
        reason="SF_DATALESS detection relies on st_flags, a macOS/BSD stat field absent on "
        "Linux; the test injects it via os.stat_result(fields, {'st_flags': ...}), which "
        "only takes effect where the platform recognizes st_flags (macOS). On Linux the key "
        "is dropped, classify_file_target returns None, and 'dataless' in None raises.",
    )
    def test_dataless_placeholder_is_refused_without_opening_it(self, tmp_path, monkeypatch):
        """The real 2026-07-10 shape: SF_DATALESS set, bytes not on this machine."""
        stub = tmp_path / "in-icloud.pdf"
        stub.write_bytes(b"placeholder")
        real_stat = os.stat

        def fake_stat(path, *args, **kwargs):
            st = real_stat(path, *args, **kwargs)
            if str(path) == str(stub):
                fields = list(st)
                return os.stat_result(fields, {"st_flags": provenance._SF_DATALESS})
            return st

        monkeypatch.setattr(os, "stat", fake_stat)
        assert "dataless" in provenance.classify_file_target(stub)

    def test_symlink_loop_is_refused(self, tmp_path):
        loop = tmp_path / "a"
        other = tmp_path / "b"
        loop.symlink_to(other)
        other.symlink_to(loop)
        assert provenance.classify_file_target(loop) == "dangling — symlink loop"

    def test_a_good_file_receipt_still_verifies(self, tmp_path):
        """The control: the gate must still let a true receipt PASS."""
        import hashlib

        artifact = tmp_path / "real.txt"
        artifact.write_bytes(b"the bytes that were actually there")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        receipt = {"kind": "file", "ref": str(artifact), "sha256": digest}

        provenance.preflight_file_receipt(receipt, 1)
        stamped = provenance.verify_receipt_at_write(receipt, tmp_path)
        assert stamped["checked_at_write"] == "verified"
        assert "unverified_reason" not in stamped

    def test_changed_bytes_still_stamp_mismatch(self, tmp_path):
        """Degradation must not swallow tamper-evidence."""
        artifact = tmp_path / "changed.txt"
        artifact.write_bytes(b"different bytes")
        receipt = {"kind": "file", "ref": str(artifact), "sha256": "d" * 64}
        stamped = provenance.verify_receipt_at_write(receipt, tmp_path)
        assert stamped["checked_at_write"] == "mismatch"


class TestBoundedIO:
    def test_run_bounded_returns_the_caller_even_when_the_read_never_does(self, tmp_path):
        fifo = tmp_path / "never"
        os.mkfifo(fifo)

        def _blocks_forever():
            with open(fifo, "rb") as handle:
                return handle.read()

        started = time.monotonic()
        with pytest.raises(provenance.BoundedIOError):
            provenance._run_bounded(_blocks_forever, 0.2)
        assert time.monotonic() - started < 2.0

    def test_run_bounded_reraises_the_underlying_oserror(self, tmp_path):
        missing = tmp_path / "nope"

        def _boom():
            raise OSError(errno.EACCES, "denied")

        with pytest.raises(OSError, match="denied"):
            provenance._run_bounded(_boom, 1.0)
        assert not missing.exists()

    def test_hash_file_bounded_matches_hashlib(self, tmp_path):
        import hashlib

        blob = tmp_path / "blob.bin"
        blob.write_bytes(os.urandom(3 * 1024 * 1024))
        assert provenance.hash_file_bounded(blob) == hashlib.sha256(blob.read_bytes()).hexdigest()

    def test_read_blob_bounded_reports_unreadable_not_verified(self, tmp_path, monkeypatch):
        fifo = tmp_path / "blob.txt"
        os.mkfifo(fifo)
        real_stat = os.stat

        def fake_stat(path, *args, **kwargs):
            st = real_stat(path, *args, **kwargs)
            if str(path) == str(fifo):
                fields = list(st)
                fields[stat_module.ST_MODE] = (
                    st.st_mode & ~stat_module.S_IFMT(st.st_mode)
                ) | stat_module.S_IFREG
                fields[stat_module.ST_SIZE] = 10
                return os.stat_result(fields)
            return st

        monkeypatch.setattr(os, "stat", fake_stat)
        monkeypatch.setattr(provenance, "READ_TIMEOUT_SECONDS", 0.2)
        content, verdict = provenance.read_blob_bounded(fifo)
        assert content is None
        assert verdict == "unreadable"


def _last_entry(tmp_root: Path) -> dict:
    import json

    files = sorted((tmp_root / "chronicle" / "insights").rglob("*.jsonl"))
    assert files, "no insight file was written"
    lines = [line for line in files[-1].read_text().splitlines() if line.strip()]
    return json.loads(lines[-1])
