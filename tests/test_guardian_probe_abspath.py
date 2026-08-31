"""The guardian probe must not depend on PATH for system binaries.

RED on 52f1f1b: `lsof` is /usr/sbin/lsof, and the dashboard daemon's launchd
plist PATH is venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin -- no
/usr/sbin. A bare ["lsof", ...] raises FileNotFoundError inside the daemon and
read_guardian() fails soft to None, so the panel renders "no data" while the
same code works from an interactive shell. Measured live 2026-08-30 22:09 EDT.
"""

import os
import shutil

import pytest

from sovereign_stack import dashboard_readers as R


def _launchd_like_path(monkeypatch):
    """The daemon's real PATH: everything except /usr/sbin."""
    monkeypatch.setenv(
        "PATH",
        "/Users/tony_studio/sovereign-stack/venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    )


@pytest.mark.skipif(not os.path.exists("/usr/sbin/lsof"), reason="no /usr/sbin/lsof here")
def test_lsof_resolves_when_usr_sbin_is_not_on_path(monkeypatch):
    _launchd_like_path(monkeypatch)
    assert shutil.which("lsof") is None, "precondition: lsof must be off PATH for this test"
    assert R._resolve_tool("lsof") == "/usr/sbin/lsof"


def test_resolve_tool_prefers_path_when_present():
    assert R._resolve_tool("pgrep") == shutil.which("pgrep") or R._resolve_tool("pgrep").endswith(
        "/pgrep"
    )


def test_resolve_tool_returns_bare_name_when_nothing_resolves(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(R, "_PROBE_SEARCH_DIRS", ())
    assert R._resolve_tool("definitely-not-a-real-binary") == "definitely-not-a-real-binary"


@pytest.mark.skipif(not os.path.exists("/usr/sbin/lsof"), reason="no /usr/sbin/lsof here")
def test_guardian_probe_invokes_lsof_by_absolute_path(monkeypatch):
    """The end-to-end shape, WITHOUT making a real probe.

    tests/conftest.py's autouse `_no_live_dashboard_probes` guard forbids real
    lsof/pgrep calls from the suite -- correctly, and an earlier draft of this
    test tried to make one and was refused by it. So: neutralise the guard for
    this one test, stub subprocess.run, and assert on the ARGV the probe builds.
    That is the actual defect -- a bare name vs a resolved path -- and it needs
    no external process at all.
    """
    _launchd_like_path(monkeypatch)
    monkeypatch.setattr(R, "_refuse_external_probe", lambda *_a, **_k: None)

    seen = []

    class _Result:
        stdout = "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"

    def _fake_run(argv, **_kw):
        seen.append(argv)
        return _Result()

    monkeypatch.setattr(R.subprocess, "run", _fake_run)
    R._guardian_probe()

    assert seen, "probe made no subprocess call"
    lsof_argv = seen[0]
    assert lsof_argv[0] == "/usr/sbin/lsof", (
        f"probe must resolve lsof to an absolute path under the daemon's PATH, got {lsof_argv[0]!r}"
    )
    for argv in seen[1:]:
        assert os.path.isabs(argv[0]), f"probe called {argv[0]!r} by bare name"
