"""hq_panel: one pty-backed session. Tests use /bin/sh as the child so no real
claude is spawned; the guard is injected so no real ps/lsof runs."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from sovereign_stack import hq_panel

ECHO = json.dumps(["/bin/sh", "-c", "echo READY; cat"])


@pytest.fixture
def session(monkeypatch, tmp_path):
    monkeypatch.setenv("HQ_PANEL_COMMAND", ECHO)
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    monkeypatch.setenv("HQ_PANEL_BUFFER_BYTES", "4096")
    s = hq_panel.HQSession(seat_finder=lambda: None)
    yield s
    s._terminate_for_tests()


def _collect(s, timeout=3.0, needle=b"READY"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunks, _ = s.replay(0)
        data = b"".join(c for _, c in chunks)
        if needle in data:
            return data
        time.sleep(0.05)
    raise AssertionError("needle not seen")


def test_start_runs_command_and_buffers_output(session):
    ok, reason = session.start(80, 24)
    assert ok, reason
    assert session.running and session.status()["pid"] > 0
    data = _collect(session)
    assert b"READY" in data
    assert session.last_seq >= 1


def test_write_reaches_child_and_echo_comes_back(session):
    assert session.start(80, 24)[0]
    _collect(session)
    session.write(b"ping\n")
    data = _collect(session, needle=b"ping")
    assert data.count(b"ping") >= 1


def test_second_start_refused_while_running(session):
    assert session.start(80, 24)[0]
    pid = session.status()["pid"]
    ok, reason = session.start(80, 24)
    assert not ok and "already running" in reason
    assert session.status()["pid"] == pid


def test_guard_refuses_when_a_seat_is_live(monkeypatch, tmp_path):
    monkeypatch.setenv("HQ_PANEL_COMMAND", ECHO)
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    seat = {"pid": 4242, "comm": "claude", "cwd": "/x", "exe": "/y", "kind": "terminal"}
    s = hq_panel.HQSession(seat_finder=lambda: seat)
    ok, reason = s.start(80, 24)
    assert not ok
    assert "4242" in reason and "terminal" in reason
    assert not s.running


def test_resize_sets_winsize(session):
    import fcntl
    import struct
    import termios

    assert session.start(80, 24)[0]
    session.resize(132, 40)
    packed = fcntl.ioctl(session._fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
    rows, cols, _, _ = struct.unpack("HHHH", packed)
    assert (cols, rows) == (132, 40)


def test_session_exit_flips_running_and_allows_restart(session):
    assert session.start(80, 24)[0]
    _collect(session)
    session.write(b"\x04")  # EOF to cat: child exits
    deadline = time.time() + 3
    while session.running and time.time() < deadline:
        time.sleep(0.05)
    assert not session.running
    st = session.status()
    assert st["last_exit"] is not None and st["pid"] is None
    ok, reason = session.start(80, 24)
    assert ok, reason


def test_buffer_cap_drops_oldest_and_reports_truncation(monkeypatch, tmp_path):
    cmd = json.dumps(["/bin/sh", "-c", "yes | head -c 20000; echo END; cat"])
    monkeypatch.setenv("HQ_PANEL_COMMAND", cmd)
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    monkeypatch.setenv("HQ_PANEL_BUFFER_BYTES", "4096")
    s = hq_panel.HQSession(seat_finder=lambda: None)
    try:
        assert s.start(80, 24)[0]
        _collect(s, needle=b"END")
        chunks, truncated_before = s.replay(0)
        total = sum(len(c) for _, c in chunks)
        assert total <= 4096
        assert truncated_before is not None and truncated_before > 1
        assert chunks[0][0] == truncated_before
        assert s.status()["truncated_before"] == truncated_before
    finally:
        s._terminate_for_tests()


def test_replay_after_seq_returns_only_newer(session):
    assert session.start(80, 24)[0]
    _collect(session)
    chunks, _ = session.replay(0)
    last = chunks[-1][0]
    session.write(b"later\n")
    _collect(session, needle=b"later")
    newer, _ = session.replay(last)
    assert newer and all(seq > last for seq, _ in newer)


def test_wait_for_returns_true_on_new_output_and_false_on_timeout(session):
    assert session.start(80, 24)[0]
    _collect(session)
    seq = session.last_seq
    assert session.wait_for(seq, timeout=0.2) is False
    session.write(b"x\n")
    assert session.wait_for(seq, timeout=2.0) is True


def test_status_shape_and_over_24h(session):
    st = session.status()
    assert st["running"] is False and st["pid"] is None and st["listening"] is False
    expected = {
        "running",
        "pid",
        "started_at",
        "age_seconds",
        "transcript_bytes",
        "seq",
        "over_24h",
        "listening",
        "chat_db_readable",
        "checked_at",
        "truncated_before",
        "last_exit",
    }
    assert set(st) >= expected
    assert session.start(80, 24)[0]
    session._started_at -= 25 * 3600
    assert session.status()["over_24h"] is True


def test_chat_db_probe_false_on_unreadable_path(tmp_path):
    assert hq_panel.chat_db_readable(tmp_path / "nope.db") is False


def test_chat_db_probe_true_on_a_real_message_table(tmp_path):
    import sqlite3

    db = tmp_path / "chat.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT)")
    c.execute("INSERT INTO message (text) VALUES ('x')")
    c.commit()
    c.close()
    assert hq_panel.chat_db_readable(db) is True


def test_status_file_written_and_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(hq_panel, "chat_db_readable", lambda path=None: False)
    out = tmp_path / "cockpit" / "console_status.json"
    rec = hq_panel.write_status_file(out)
    assert rec["ok"] is False and out.exists()
    on_disk = json.loads(out.read_text())
    assert on_disk["ok"] is False and on_disk["checked_at"] and on_disk["pid"] == os.getpid()


def test_find_live_hq_seat_rules(monkeypatch):
    home = "/Users/x"
    code_tab = (
        "/Users/x/Library/Application Support/Claude/claude-code/2.1.0/ab/"
        "claude.app/Contents/MacOS/claude"
    )
    table = {
        "ps_list": "  11 claude\n  12 claude\n  13 " + code_tab + "\n  14 claudette\n",
        "args": {11: "claude -p hi", 12: "claude -n HQ /hq-boot", 13: code_tab},
        "cwd": {11: home, 12: home, 13: "/private/tmp/scratch"},
        "exe": {
            11: "/opt/homebrew/bin/claude",
            12: "/opt/homebrew/bin/claude",
            13: "/usr/lib/dyld",
        },
    }

    def fake_run(argv):
        if argv[:2] == ["ps", "-axo"]:
            return table["ps_list"]
        if argv[0] == "ps" and "-p" in argv:
            return table["args"].get(int(argv[-1]), "")
        if argv[0] == "lsof":
            pid = int(argv[argv.index("-p") + 1])
            kind = argv[argv.index("-d") + 1]
            val = table["cwd" if kind == "cwd" else "exe"].get(pid, "")
            return f"p{pid}\nn{val}\n"
        raise AssertionError(argv)

    monkeypatch.setattr(hq_panel, "_run", fake_run)
    seat = hq_panel.find_live_hq_seat(home=home)
    assert seat["pid"] == 12 and seat["kind"] == "terminal"
    seat2 = hq_panel.find_live_hq_seat(home=home, exclude_pids={12})
    assert seat2["pid"] == 13 and seat2["kind"] == "app"
    assert hq_panel.find_live_hq_seat(home=home, exclude_pids={12, 13}) is None


def test_two_concurrent_starts_spawn_exactly_one_child(monkeypatch, tmp_path):
    """Review finding (Important 1): the running check, the guard and the spawn must be
    one critical section, or two tabs starting at once get two claudes."""
    import threading

    monkeypatch.setenv("HQ_PANEL_COMMAND", ECHO)
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))

    def slow_guard():
        time.sleep(0.3)
        return

    s = hq_panel.HQSession(seat_finder=slow_guard)
    results = []

    def go():
        results.append(s.start(80, 24))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    try:
        oks = [ok for ok, _ in results]
        assert oks.count(True) == 1, results
        assert any("already" in reason for ok, reason in results if not ok), results
        assert s.running
    finally:
        s._terminate_for_tests()


def test_reader_of_an_orphaned_child_never_clears_the_live_session(monkeypatch, tmp_path):
    """The reader's cleanup must only clear state for ITS process."""
    monkeypatch.setenv("HQ_PANEL_COMMAND", ECHO)
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    s = hq_panel.HQSession(seat_finder=lambda: None)
    try:
        assert s.start(80, 24)[0]
        live = s._proc
        import subprocess

        other = subprocess.Popen(["/bin/sh", "-c", "exit 3"])
        fd = os.open(os.devnull, os.O_RDONLY)
        s._read_loop(fd, other)  # a reader for a process that is NOT the session's
        assert s._proc is live and s.running
        assert s.status()["last_exit"] is None
    finally:
        s._terminate_for_tests()


def test_start_records_the_spawned_pid_even_if_the_child_exits_at_once(monkeypatch, tmp_path):
    """Seat 2/3's finding (mesh-20261009): a child that exits before the route reads
    status() made /hq/start answer pid null. The pid start() spawned must be kept."""
    monkeypatch.setenv("HQ_PANEL_COMMAND", json.dumps(["/bin/sh", "-c", "exit 0"]))
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    s = hq_panel.HQSession(seat_finder=lambda: None)
    try:
        ok, reason = s.start(80, 24)
        assert ok, reason
        deadline = time.time() + 3
        while s.running and time.time() < deadline:
            time.sleep(0.05)
        assert not s.running
        pid = s.last_started_pid
        assert isinstance(pid, int) and pid > 0
        assert str(pid) in reason
    finally:
        s._terminate_for_tests()


def test_refusal_for_a_code_tab_seat_names_the_escape(monkeypatch, tmp_path):
    """Seat 2/3's shrink: keep the wide Code-tab rule, but a 409 for kind=app must
    tell the operator how to get past it, as the launcher already does."""
    monkeypatch.setenv("HQ_PANEL_COMMAND", ECHO)
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    seat = {
        "pid": 9,
        "comm": "claude",
        "cwd": "/scratch",
        "exe": "/x/claude.app/Contents/MacOS/claude",
        "kind": "app",
    }
    s = hq_panel.HQSession(seat_finder=lambda: seat)
    ok, reason = s.start(80, 24)
    assert not ok
    assert "close it" in reason and "claude -n HQ" in reason


PROBE = (
    "import os, signal, sys, time\n"
    "out = open(sys.argv[1], 'w')\n"
    "try:\n"
    "    os.close(os.open('/dev/tty', os.O_RDWR))\n"
    "    out.write('devtty=OK\\n')\n"
    "except OSError as e:\n"
    "    out.write('devtty=FAIL %s\\n' % e.errno)\n"
    "out.flush()\n"
    "signal.signal(signal.SIGHUP, lambda *_: (out.write('HUP\\n'), out.flush(), os._exit(129)))\n"
    "out.write('READY\\n'); out.flush()\n"
    "time.sleep(10)\n"
    "out.write('TIMEOUT\\n'); out.flush()\n"
)


def _wait_for(path, needle, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and needle in path.read_text():
            return path.read_text()
        time.sleep(0.05)
    return path.read_text() if path.exists() else ""


def test_raw_child_has_a_controlling_terminal(monkeypatch, tmp_path):
    """Seat 3/3's G2 measurement (mesh-20261009, helix 53718): under the original spawn a
    /bin/sh child acquired the terminal by itself but a raw non-shell child did not
    (/dev/tty ENXIO). The panel's `claude` is the raw case, so the spawn must make the
    pty the child's controlling terminal (TIOCSCTTY)."""
    import sys

    probe = tmp_path / "probe.py"
    probe.write_text(PROBE)
    receipt = tmp_path / "receipt.txt"
    monkeypatch.setenv("HQ_PANEL_COMMAND", json.dumps([sys.executable, str(probe), str(receipt)]))
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    s = hq_panel.HQSession(seat_finder=lambda: None)
    try:
        assert s.start(80, 24)[0]
        text = _wait_for(receipt, "READY")
        assert "devtty=OK" in text, text
    finally:
        s._terminate_for_tests()


def test_raw_child_gets_hup_when_the_console_process_dies(tmp_path):
    """Seat 3/3's second finding: with no controlling terminal the raw child outlived a
    closed master. The console is a separate process, so its death is simulated by a
    driver process that starts the session and exits; the kernel must HUP the child."""
    import subprocess
    import sys

    probe = tmp_path / "probe.py"
    probe.write_text(PROBE)
    receipt = tmp_path / "receipt.txt"
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import json, os, sys, time\n"
        "from sovereign_stack import hq_panel\n"
        "s = hq_panel.HQSession(seat_finder=lambda: None)\n"
        "ok, reason = s.start(80, 24)\n"
        "assert ok, reason\n"
        "deadline = time.time() + 4\n"
        "while time.time() < deadline:\n"
        "    p = sys.argv[1]\n"
        "    if os.path.exists(p) and 'READY' in open(p).read():\n"
        "        break\n"
        "    time.sleep(0.05)\n"
        "os._exit(0)  # the console dies: every fd closes, the master included\n"
    )
    env = dict(os.environ)
    env["HQ_PANEL_COMMAND"] = json.dumps([sys.executable, str(probe), str(receipt)])
    env["HQ_PANEL_CWD"] = str(tmp_path)
    src = str(Path(hq_panel.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run([sys.executable, str(driver), str(receipt)], env=env, timeout=15, check=True)
    text = _wait_for(receipt, "HUP")
    assert "devtty=OK" in text and "HUP" in text and "TIMEOUT" not in text, text


# ── Fix 2 (web seat review of PR #9, 2026-10-10): the panel's own environment ──
#
# Measured on the Studio 2026-10-10: the console's launchd PATH puts the live
# stack's venv/bin FIRST and sets no LANG; the login profile prepends its own
# dirs but never removes venv/bin, and a bare `pip` still resolved to the live
# venv's pip. So a routine `pip install` from panel HQ would land in the running
# stack. The overlay below is the panel child's alone: not the plist, not the launcher.


def test_panel_env_scrubs_the_console_venv_and_sets_lang():
    base = {
        "PATH": "/x/venv/bin:/opt/homebrew/bin:/usr/bin:/bin",
        "VIRTUAL_ENV": "/x/venv",
        "PYTHONPATH": "/x/src",
        "HOME": "/h",
    }
    env = hq_panel.panel_env(base, venv_bin="/x/venv/bin")
    assert "/x/venv/bin" not in env["PATH"].split(":")
    assert env["PATH"].split(":")[:1] == [os.path.expanduser("~/.local/bin")]
    assert "/opt/homebrew/bin" in env["PATH"].split(":")
    assert env["PATH"].split(":")[-2:] == ["/usr/sbin", "/sbin"]
    assert "VIRTUAL_ENV" not in env and "PYTHONPATH" not in env
    assert env["LANG"] == "en_US.UTF-8"
    assert env["HOME"] == "/h"
    assert base == {  # the caller's mapping is untouched
        "PATH": "/x/venv/bin:/opt/homebrew/bin:/usr/bin:/bin",
        "VIRTUAL_ENV": "/x/venv",
        "PYTHONPATH": "/x/src",
        "HOME": "/h",
    }


def test_panel_env_keeps_a_lang_the_console_already_has():
    env = hq_panel.panel_env({"PATH": "/usr/bin:/bin", "LANG": "de_DE.UTF-8"}, venv_bin="/nowhere")
    assert env["LANG"] == "de_DE.UTF-8"


def test_child_does_not_see_the_console_venv_on_path(monkeypatch, tmp_path):
    """The real child, started the real way, reports its own PATH, LANG and VIRTUAL_ENV."""
    own_bin = os.path.join(sys.prefix, "bin")
    monkeypatch.setenv("PATH", f"{own_bin}:/usr/bin:/bin")
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.setenv(
        "HQ_PANEL_COMMAND",
        json.dumps(
            [
                "/bin/sh",
                "-c",
                'echo "PATH=$PATH"; echo "LANG=$LANG"; echo "VENV=${VIRTUAL_ENV:-none}"; echo READY; cat',
            ]
        ),
    )
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    s = hq_panel.HQSession(seat_finder=lambda: None)
    try:
        ok, reason = s.start(80, 24)
        assert ok, reason
        text = _collect(s).decode(errors="replace")
    finally:
        s._terminate_for_tests()
    path_line = next(ln for ln in text.splitlines() if ln.startswith("PATH="))
    assert own_bin not in path_line[len("PATH=") :].split(":")
    assert "LANG=en_US.UTF-8" in text
    assert "VENV=none" in text
