"""HQ cockpit Phase 2: one pty-backed `claude` session for the console's terminal panel.

Stdlib only. One session at most; the duplicate-seat guard runs before every start
(the same rules as scripts/hq-cockpit.command). There is no stop: HQ ends its own
session. Output is kept in a bounded replay buffer with sequence numbers so a page
reload picks up where it left off, and the newest output wakes waiting SSE streams.

Permission parity (Anthony, 2026-10-09): the child is started with NO permission
flag; `claude` inherits ~/.claude/settings.json exactly as the Terminal seat does.

Phase 3 (push link, 2026-10-10) adds ONE flag that is not a permission: the
default command carries `--settings {"crossSessionInbound":"hold"}`, the inbound
setting made explicit as the plan asks. A bypass-mode session holds an unattested
peer message for review (measured on 2.1.296), so console batches wait in the
panel's review queue. `accept` is never the default: 2.1.296 has no sender
allowlist, so it would admit every same-user peer unheld. HQ_PANEL_INBOUND=accept
is Anthony's switch (gate-shape:channel-trust); see hq_push. Flag settings for
this one process: ~/.claude/settings.json and the Terminal seat are untouched.
"""

from __future__ import annotations

import collections
import contextlib
import fcntl
import json
import os
import pty
import signal
import sqlite3
import struct
import subprocess
import sys
import termios
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import hq_push

#: The command before the Phase 3 inbound flag; default_command() inserts it.
DEFAULT_COMMAND = ["claude", "-n", "HQ", "/hq-boot"]
DEFAULT_BUFFER_BYTES = 256 * 1024
OVER_24H_SECONDS = 24 * 3600
CODE_TAB_MARKER = "/Application Support/Claude/claude-code/"
CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
PANEL_LANG = "en_US.UTF-8"
_PANEL_PATH_TAIL = ("/usr/sbin", "/sbin")
_PYTHON_ENV_KEYS = ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")


def panel_env(base: dict, venv_bin: str | None = None) -> dict:
    """The panel child's environment, built from the console's (`base`) and
    corrected for the panel ALONE (web seat review of PR #9, 2026-10-10; the
    plist and the launcher stay as they are).

    Measured on the Studio 2026-10-10: the console's launchd PATH leads with the
    live stack's venv/bin and carries no LANG. The login profile prepends its own
    dirs but never removes venv/bin, and a bare `pip` in such a shell resolved to
    the live venv's pip, so a routine install from panel HQ would land in the
    running stack. Here: every PATH entry equal to the console interpreter's own
    bin dir (`venv_bin`, default sys.prefix/bin) is dropped, along with the
    virtualenv markers; ~/.local/bin leads and /usr/sbin:/sbin close, as a
    Terminal seat sees them; LANG is set only when the console has none."""
    env = dict(base)
    venv_bin = venv_bin or os.path.join(sys.prefix, "bin")
    kept = [d for d in env.get("PATH", "").split(":") if d and d != venv_bin]
    head = [os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin"]
    path = [d for d in head if d not in kept] + [d for d in kept if d not in _PANEL_PATH_TAIL]
    env["PATH"] = ":".join(path + list(_PANEL_PATH_TAIL))
    for key in _PYTHON_ENV_KEYS:
        env.pop(key, None)
    env.setdefault("LANG", PANEL_LANG)
    return env


def _run(argv: list[str]) -> str:
    """Run a read-only process-listing command; empty string on any failure."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _lsof_field(pid: int, kind: str) -> str:
    out = _run(["lsof", "-a", "-p", str(pid), "-d", kind, "-Fn"])
    names = [ln[1:] for ln in out.splitlines() if ln.startswith("n")]
    if kind == "txt":
        for n in names:
            if n.endswith(("/claude", "/claude.exe")):
                return n
        return ""
    return names[0] if names else ""


def find_live_hq_seat(home: str | None = None, exclude_pids=frozenset()) -> dict | None:
    """The duplicate-seat guard. Mirrors the launcher: a process named `claude`
    (basename match over the whole comm, which may be a full path containing a
    space), not a `-p`/`--print` child, counts as HQ if its cwd is $HOME or its
    binary lives in a Claude app (Code tab). Returns the first match or None."""
    home = home or os.path.expanduser("~")
    for line in _run(["ps", "-axo", "pid=,comm="]).splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, comm = int(parts[0]), parts[1]
        if pid in exclude_pids:
            continue
        if not (comm == "claude" or comm.endswith("/claude")):
            continue
        args = _run(["ps", "-o", "args=", "-p", str(pid)])
        padded = f" {args.strip()} "
        if " -p " in padded or " --print " in padded:
            continue
        exe = _lsof_field(pid, "txt")
        cwd = _lsof_field(pid, "cwd")
        in_app = any(
            p.startswith("/Applications/Claude.app/") or CODE_TAB_MARKER in p for p in (exe, comm)
        )
        if cwd == home or in_app:
            kind = "app" if in_app else "terminal"
            return {"pid": pid, "comm": comm, "cwd": cwd, "exe": exe, "kind": kind}
    return None


def chat_db_readable(path: Path | None = None) -> bool:
    """True only if one metadata row of chat.db reads. False on ANY failure: a TCC
    denial, a missing file, a locked db. Never None-as-ok (fail closed)."""
    target = path or CHAT_DB
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=2)
        try:
            conn.execute("SELECT MAX(ROWID) FROM message").fetchone()
        finally:
            conn.close()
        return True
    except Exception:
        return False


def _acquire_controlling_tty() -> None:
    """Runs in the child after setsid (start_new_session) and before exec: make the pty
    slave on fd 0 the controlling terminal. Seat 3/3 measured (mesh-20261009) that a
    shell child acquires the terminal by itself but a raw non-shell child, which is
    what `claude` is, does not: /dev/tty failed with ENXIO and a closed console left
    the child alive with no SIGHUP. This is what the stdlib's own pty.fork does."""
    with contextlib.suppress(OSError):
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _utc(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class HQSession:
    """One pty session. Thread-safe. `seat_finder` is injectable for tests."""

    def __init__(self, seat_finder=None):
        self._seat_finder = seat_finder or find_live_hq_seat
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._buf: collections.deque[tuple[int, bytes]] = collections.deque()
        self._buf_bytes = 0
        self._seq = 0
        self._truncated_before: int | None = None
        self._proc: subprocess.Popen | None = None
        self._fd: int | None = None
        self._reader: threading.Thread | None = None
        self._started_at: float | None = None
        self._last_exit: int | None = None
        self._transcript_bytes = 0
        self._starting = False
        self._last_started_pid: int | None = None
        # Working / waiting (cockpit integration, 2026-10-10): the server's own
        # clock values, stamped where they happen, so the page never infers
        # "HQ is idle" from a client timer alone.
        self._seq_changed_at: float | None = None
        self._last_input_at: float | None = None
        # Phase 3: what was actually spawned and when, so the push link binds to
        # this child alone and reads its inbound setting from the real argv.
        self._last_command: list[str] | None = None
        self._spawned_at: float | None = None

    # ── configuration ──
    @staticmethod
    def default_command() -> list[str]:
        """`claude -n HQ --settings {"crossSessionInbound":...} /hq-boot`."""
        return [*DEFAULT_COMMAND[:-1], *hq_push.inbound_settings_arg(), DEFAULT_COMMAND[-1]]

    @classmethod
    def _command(cls) -> list[str]:
        raw = os.environ.get("HQ_PANEL_COMMAND")
        if raw:
            cmd = json.loads(raw)
            if isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd):
                return cmd
        return cls.default_command()

    @staticmethod
    def _cwd() -> str:
        return os.environ.get("HQ_PANEL_CWD") or os.path.expanduser("~")

    @staticmethod
    def _cap() -> int:
        try:
            return max(4096, int(os.environ.get("HQ_PANEL_BUFFER_BYTES", DEFAULT_BUFFER_BYTES)))
        except ValueError:
            return DEFAULT_BUFFER_BYTES

    # ── state ──
    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def last_started_pid(self) -> int | None:
        """The pid start() most recently spawned, kept even after that child exits,
        so a caller that reads it after a short-lived child still gets the pid."""
        with self._lock:
            return self._last_started_pid

    @property
    def last_command(self) -> list[str] | None:
        """The argv start() most recently spawned (None before the first start)."""
        with self._lock:
            return list(self._last_command) if self._last_command else None

    @property
    def spawned_at(self) -> float | None:
        """The console's clock when start() spawned the current child."""
        with self._lock:
            return self._spawned_at

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def start(self, cols: int, rows: int) -> tuple[bool, str]:
        # Two tabs starting at once must get one claude (review 2026-10-09). The
        # lock covers the running check and the _starting flag only; the flag is
        # what serializes the guard and the spawn, which run after the lock is
        # released because the guard shells out to ps and lsof (seat 2/3,
        # mesh-20261009). A second start is refused, never queued.
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return False, f"a panel session is already running (pid {self._proc.pid})"
            if self._starting:
                return False, "a panel session is already starting"
            self._starting = True
        try:
            return self._start_locked_out(cols, rows)
        finally:
            with self._lock:
                self._starting = False

    def _start_locked_out(self, cols: int, rows: int) -> tuple[bool, str]:
        seat = self._seat_finder()
        if seat:
            reason = (
                f"an HQ seat is already live: pid {seat['pid']} ({seat['kind']}, "
                f"cwd {seat['cwd']}); not starting a second seat"
            )
            if seat.get("kind") == "app":
                # Any interactive Code-tab session counts, on any project (its cwd
                # cannot tell HQ from another session). The refusal carries the
                # launcher's own escape so the console button is not just dead
                # (seat 2/3, mesh-20261009).
                reason += (
                    "; if that Claude app Code-tab session is not HQ, close it, or start HQ "
                    "by hand with: cd ~ && claude -n HQ '/hq-boot'"
                )
            return False, reason
        cols = max(20, min(500, int(cols)))
        rows = max(5, min(200, int(rows)))
        master, slave = pty.openpty()
        env = panel_env(os.environ)
        env.update({"TERM": "xterm-256color", "COLUMNS": str(cols), "LINES": str(rows)})
        command = self._command()
        spawned_at = time.time()
        try:
            # preexec_fn runs between fork and exec in a threaded server; CPython
            # warns it can deadlock if it touches a lock another thread holds. It
            # is one ioctl on fd 0 and takes no lock (web seat review, 2026-10-10).
            proc = subprocess.Popen(
                command,
                cwd=self._cwd(),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                start_new_session=True,
                close_fds=True,
                preexec_fn=_acquire_controlling_tty,
            )
        except OSError as e:
            os.close(master)
            os.close(slave)
            return False, f"could not start the panel command: {e}"
        os.close(slave)
        with self._lock:
            self._proc, self._fd = proc, master
            self._last_started_pid = proc.pid
            self._last_command = list(command)
            self._spawned_at = spawned_at
            self._started_at = time.time()
            self._last_exit = None
            self._buf.clear()
            self._buf_bytes = 0
            self._truncated_before = None
            self._transcript_bytes = 0
            self._seq_changed_at = None
            self._last_input_at = None
        self._set_winsize(cols, rows)
        self._reader = threading.Thread(target=self._read_loop, args=(master, proc), daemon=True)
        self._reader.start()
        return True, f"started pid {proc.pid}"

    def _set_winsize(self, cols: int, rows: int) -> None:
        if self._fd is None:
            return
        with contextlib.suppress(OSError):
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def resize(self, cols: int, rows: int) -> None:
        self._set_winsize(max(20, min(500, int(cols))), max(5, min(200, int(rows))))
        proc = self._proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGWINCH)

    def write(self, data: bytes) -> None:
        fd = self._fd
        if fd is None or not self.running:
            return
        try:
            os.write(fd, data)
        except OSError:
            return
        with self._lock:
            self._last_input_at = time.time()

    def _read_loop(self, fd: int, proc: subprocess.Popen) -> None:
        cap = self._cap()
        try:
            while True:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                with self._cond:
                    self._seq += 1
                    self._seq_changed_at = time.time()
                    self._buf.append((self._seq, chunk))
                    self._buf_bytes += len(chunk)
                    self._transcript_bytes += len(chunk)
                    while self._buf_bytes > cap and len(self._buf) > 1:
                        old_seq, old = self._buf.popleft()
                        self._buf_bytes -= len(old)
                        self._truncated_before = old_seq + 1
                    self._cond.notify_all()
        finally:
            code = proc.wait()
            with self._cond:
                with contextlib.suppress(OSError):
                    os.close(fd)
                # Only the reader of the LIVE process clears the session (review
                # 2026-10-09): a reader for any other child must not flip it.
                if self._proc is proc:
                    self._last_exit = code
                    self._proc = None
                    self._fd = None
                    # A final tick so streams waiting on wait_for wake and see running=False.
                    self._seq += 1
                    self._seq_changed_at = time.time()
                    self._cond.notify_all()

    def replay(self, after: int) -> tuple[list[tuple[int, bytes]], int | None]:
        with self._lock:
            return [(s, c) for s, c in self._buf if s > after], self._truncated_before

    def wait_for(self, seq: int, timeout: float) -> bool:
        with self._cond:
            if self._seq > seq:
                return True
            self._cond.wait(timeout)
            return self._seq > seq

    def status(self) -> dict:
        with self._lock:
            now = time.time()
            proc = self._proc
            running = proc is not None and proc.poll() is None
            started = self._started_at if running else None
            age = (now - started) if started else None
            changed = self._seq_changed_at
            # How long the output has been still, on THIS clock. None when no
            # output has arrived yet (unknown, not "idle since forever").
            idle = round(max(0.0, now - changed), 1) if changed is not None else None
            return {
                "running": running,
                "pid": proc.pid if running else None,
                "started_at": _utc(started),
                "age_seconds": round(age) if age is not None else None,
                "transcript_bytes": self._transcript_bytes,
                "seq": self._seq,
                "over_24h": bool(age is not None and age >= OVER_24H_SECONDS),
                # Phase 3: the push link's own answer is folded in by the console
                # (dashboard_web), which owns the poller; this module alone cannot know.
                "listening": False,
                "chat_db_readable": _LAST_PROBE.get("ok"),
                "checked_at": _LAST_PROBE.get("checked_at"),
                "truncated_before": self._truncated_before,
                "last_exit": self._last_exit,
                "seq_changed_at": _utc(changed),
                "seq_idle_seconds": idle,
                "last_input_at": _utc(self._last_input_at),
            }

    def _terminate_for_tests(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
        if self._reader is not None:
            self._reader.join(timeout=3)


_SESSION: HQSession | None = None
_SESSION_LOCK = threading.Lock()
_LAST_PROBE: dict = {}


def get_session() -> HQSession:
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = HQSession()
        return _SESSION


def write_status_file(path: Path) -> dict:
    """Probe chat.db from THIS process (the console's own python, the one whose
    Full Disk Access grant is version-pinned) and write the result for the HQ
    module audit's json_key probe. `ok` is the probe's answer, never a default."""
    ok = chat_db_readable()
    rec = {
        "ok": ok,
        "checked_at": _utc(time.time()),
        "pid": os.getpid(),
        "probe": "chat.db MAX(ROWID) mode=ro",
    }
    _LAST_PROBE.update(ok=ok, checked_at=rec["checked_at"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2))
    os.replace(tmp, path)
    return rec


def start_status_writer(path: Path, interval_s: int = 300) -> threading.Thread:
    def loop():
        while True:
            with contextlib.suppress(Exception):
                write_status_file(path)
            time.sleep(interval_s)

    t = threading.Thread(target=loop, name="hq-panel-status-writer", daemon=True)
    t.start()
    return t
