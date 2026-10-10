"""Tests for scripts/hq-cockpit.command (Phase 1 of the HQ cockpit plan).

The launcher only orchestrates other programs, so every program it calls that could
change the machine (curl, launchctl, ps, lsof, osascript, open, nohup, sudo, sleep) is
replaced by a fake on PATH. Each fake records its argv (and stdin) to a call log and
reads its behaviour from a small scenario directory. HOME points at a tmp dir. Nothing
here touches launchd, the network, Terminal, or a browser.

The script is run under /bin/bash (bash 3.2 on macOS, bash 5 on ubuntu), matching its
shebang, so a bash-4-only construct fails here rather than on the Studio.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "hq-cockpit.command"
BASH = "/bin/bash"
FAKED = ("curl", "launchctl", "ps", "lsof", "osascript", "open", "nohup", "sudo", "sleep")
CODE_TAB_REL = (
    "Library/Application Support/Claude/claude-code/2.1.286/0a1b2c/claude.app/Contents/MacOS/claude"
)

pytestmark = pytest.mark.skipif(not Path(BASH).exists(), reason="needs /bin/bash")

# One dispatcher, written to disk, driven by argv[1] = the faked program's name.
FAKE_PY = r"""
import json, os, sys
from pathlib import Path

state = Path(os.environ["HQ_FAKE_STATE"])
name, argv = sys.argv[1], sys.argv[2:]
stdin = ""
if name == "osascript" and "-e" not in argv:
    stdin = sys.stdin.read()
with open(state / "calls.jsonl", "a") as f:
    f.write(json.dumps({"name": name, "argv": argv, "stdin": stdin}) + "\n")

def procs():
    p = state / "procs.json"
    return json.loads(p.read_text()) if p.exists() else []

def arg_after(flag):
    return argv[argv.index(flag) + 1] if flag in argv else None

if name == "curl":
    if "/hq/status" in argv[-1]:
        st = state / "hq_status"
        c_file = state / "hq_status_count"
        c = int(c_file.read_text()) + 1 if c_file.exists() else 1
        c_file.write_text(str(c))
        if (state / "panel_starts_late").exists() and c > 20:
            print('{"running": true, "pid": 9002}')
            sys.exit(0)
        print(st.read_text() if st.exists() else '{"running": false}')
        sys.exit(0)
    n_file = state / "curl_count"
    n = int(n_file.read_text()) + 1 if n_file.exists() else 1
    n_file.write_text(str(n))
    fails = int((state / "curl_fails").read_text())
    healthy = (fails >= 0 and n > fails) or (state / "nohup_started").exists()
    if healthy:
        body = state / "curl_body"
        print(body.read_text() if body.exists() else '{"status":"healthy"}')
        sys.exit(0)
    sys.exit(7)
elif name == "launchctl":
    if argv and argv[0] == "print":
        sys.exit(0 if (state / "launchctl_loaded").read_text() == "1" else 113)
    sys.exit(0)
elif name == "ps":
    if "-axo" in argv:
        for p in procs():
            print("%5d %s" % (p["pid"], p["comm"]))
    elif "-p" in argv:
        pid = int(arg_after("-p"))
        for p in procs():
            if p["pid"] == pid:
                print(p["args"])
                sys.exit(0)
        sys.exit(1)
    sys.exit(0)
elif name == "lsof":
    pid, kind = int(arg_after("-p")), arg_after("-d")
    for p in procs():
        if p["pid"] == pid:
            print("p%d" % pid)
            if kind == "txt":
                print("n/usr/lib/dyld")
                print("n" + p["exe"])
            else:
                print("n" + p["cwd"])
            sys.exit(0)
    sys.exit(1)
elif name == "osascript":
    text = " ".join(argv) + "\n" + stdin
    if "do script" in text:
        ps = procs()
        ps.append({
            "pid": 4000 + len(ps),
            "comm": "claude",
            "args": "claude -n HQ /hq-boot",
            "cwd": os.environ["HOME"],
            "exe": "/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
        })
        (state / "procs.json").write_text(json.dumps(ps))
    sys.exit(0)
elif name == "nohup":
    (state / "nohup_started").write_text("1")
    sys.exit(0)
elif name == "open":
    if argv and "start=hq" in argv[-1] and (state / "panel_starts").exists():
        (state / "hq_status").write_text('{"running": true, "pid": 9001}')
    sys.exit(0)
elif name == "sudo":
    sys.exit(1)
sys.exit(0)
"""


class Harness:
    """A scenario directory, a shim directory, and a fake HOME."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.state = root / "state"
        self.shims = root / "shims"
        for d in (self.home, self.state, self.shims):
            d.mkdir(parents=True, exist_ok=True)
        fake = root / "fake.py"
        fake.write_text(FAKE_PY)
        for name in FAKED:
            shim = self.shims / name
            shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" {name} "$@"\n')
            shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.plist = root / "LaunchAgents" / "com.templetwo.sovereign-dashboard-web.plist"
        self.log = self.home / ".cache" / "sovereign-dashboard" / "web.log"
        self.scenario()

    def scenario(self, *, curl_fails=0, loaded=True, plist=True, procs=()):
        """curl_fails: number of failing /health calls before healthy (-1 = never)."""
        (self.state / "curl_fails").write_text(str(curl_fails))
        (self.state / "launchctl_loaded").write_text("1" if loaded else "0")
        (self.state / "procs.json").write_text(json.dumps(list(procs)))
        if plist:
            self.plist.parent.mkdir(parents=True, exist_ok=True)
            self.plist.write_text("<plist/>")
        elif self.plist.exists():
            self.plist.unlink()

    def proc(self, pid, *, comm="claude", args="claude", cwd=None, exe="/opt/homebrew/bin/claude"):
        return {"pid": pid, "comm": comm, "args": args, "cwd": cwd or str(self.home), "exe": exe}

    def run(self, *args: str, env_extra=None) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.shims}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(self.home),
            "HQ_FAKE_STATE": str(self.state),
            "HQ_COCKPIT_CONSOLE_URL": "http://127.0.0.1:3435",
            "HQ_COCKPIT_CONSOLE_PLIST": str(self.plist),
            "HQ_COCKPIT_CONSOLE_BIN": str(self.home / "fake-console-bin"),
            "HQ_COCKPIT_CONSOLE_LOG": str(self.log),
            # Phase 1 cases pin the Terminal path; panel-mode tests override this.
            "HQ_COCKPIT_MODE": "terminal",
        }
        env.update(env_extra or {})
        return subprocess.run(
            [BASH, str(SCRIPT), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )

    def calls(self, name=None):
        path = self.state / "calls.jsonl"
        if not path.exists():
            return []
        out = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return [c for c in out if name is None or c["name"] == name]

    def launchctl_verbs(self):
        return [c["argv"][0] for c in self.calls("launchctl") if c["argv"]]

    def do_script_count(self):
        return sum("do script" in c["stdin"] + " ".join(c["argv"]) for c in self.calls("osascript"))

    def activate_only_count(self):
        return sum(
            "do script" not in c["stdin"] and "activate" in " ".join(c["argv"]) + c["stdin"]
            for c in self.calls("osascript")
        )

    def reset_calls(self):
        for f in ("calls.jsonl", "curl_count", "nohup_started"):
            (self.state / f).unlink(missing_ok=True)


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def dry(h: Harness, env_extra=None) -> str:
    """Run --dry-run, assert it acted on nothing, return stdout. Resets the call log."""
    h.reset_calls()
    r = h.run("--dry-run", env_extra=env_extra)
    assert r.returncode == 0, r.stdout + r.stderr
    assert_no_action(h)
    h.reset_calls()
    return r.stdout


def assert_no_action(h: Harness):
    assert not [v for v in h.launchctl_verbs() if v in ("kickstart", "bootstrap")]
    for name in ("nohup", "osascript", "open"):
        assert h.calls(name) == [], name


# ── the seven cases ──────────────────────────────────────────────────────────


def test_case1_console_up_no_hq(h):
    h.scenario(curl_fails=0)
    out = dry(h)
    assert "[dry-run] console: healthy, no action" in out
    assert "[dry-run] no process named claude found" in out
    assert "[dry-run] hq: none found; would start HQ in Terminal" in out
    assert "[dry-run] browser: would open http://127.0.0.1:3435" in out

    r = h.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert h.launchctl_verbs() == []
    assert h.calls("nohup") == []
    assert h.do_script_count() == 1
    assert len(h.calls("osascript")) == 1
    assert [c["argv"] for c in h.calls("open")] == [["http://127.0.0.1:3435"]]


def test_case2_console_loaded_not_answering_kickstart(h):
    h.scenario(curl_fails=3, loaded=True)
    out = dry(h)
    assert "would run launchctl kickstart -k gui/" in out
    assert "and wait for healthy" in out

    h.scenario(curl_fails=3, loaded=True)
    r = h.run()
    assert r.returncode == 0, r.stdout + r.stderr
    verbs = h.launchctl_verbs()
    assert verbs == ["print", "kickstart"]
    kick = [c["argv"] for c in h.calls("launchctl") if c["argv"][0] == "kickstart"][0]
    assert kick[1] == "-k" and kick[2].endswith("/com.templetwo.sovereign-dashboard-web")
    assert len(h.calls("curl")) == 4  # 1 initial probe + 3 waits, healthy on the 4th
    assert h.calls("nohup") == []
    assert len(h.calls("open")) == 1


def test_case2b_kickstart_never_healthy_exits_1_without_hq_or_browser(h):
    h.scenario(curl_fails=-1, loaded=True)
    h.log.parent.mkdir(parents=True, exist_ok=True)
    h.log.write_text("boom\n")
    r = h.run()
    assert r.returncode == 1
    assert "Still not answering" in r.stdout and "boom" in r.stdout
    assert h.calls("osascript") == [] and h.calls("open") == []


def test_case3_console_not_loaded_plist_present_bootstrap(h):
    h.scenario(curl_fails=2, loaded=False, plist=True)
    out = dry(h)
    assert "job not loaded, plist present; would run launchctl bootstrap gui/" in out

    h.scenario(curl_fails=2, loaded=False, plist=True)
    r = h.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert h.launchctl_verbs() == ["print", "bootstrap"]
    boot = [c["argv"] for c in h.calls("launchctl") if c["argv"][0] == "bootstrap"][0]
    assert boot[1].startswith("gui/") and boot[2] == str(h.plist)
    assert len(h.calls("curl")) == 3
    assert h.calls("nohup") == []


def test_console_no_job_no_plist_nohup_last_resort(h):
    h.scenario(curl_fails=-1, loaded=False, plist=False)
    out = dry(h)
    assert "no job and no plist; would nohup" in out
    assert not h.log.parent.exists()  # dry-run created nothing

    h.scenario(curl_fails=-1, loaded=False, plist=False)
    r = h.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert h.log.parent.is_dir()
    nohup = h.calls("nohup")
    assert len(nohup) == 1
    assert nohup[0]["argv"] == [str(h.home / "fake-console-bin"), "--port", "3435"]
    assert h.launchctl_verbs() == ["print"]
    assert "not supervised by launchd" in r.stdout


def test_case4_hq_already_running_in_terminal(h):
    h.scenario(procs=[h.proc(501, args="claude -n HQ /hq-boot")])
    out = dry(h)
    assert "[dry-run] pid 501 comm=claude" in out and "counts as HQ" in out
    assert "[dry-run] hq: found pid 501 in Terminal; would bring Terminal forward" in out

    h.scenario(procs=[h.proc(501, args="claude -n HQ /hq-boot")])
    r = h.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert h.do_script_count() == 0
    assert h.activate_only_count() == 1
    assert len(h.calls("open")) == 1


@pytest.mark.parametrize("variant", ["full_path_comm", "lsof_exe"])
def test_case5_hq_already_running_in_code_tab(h, variant):
    code_tab = str(h.home / CODE_TAB_REL)  # contains the space in "Application Support"
    scratch = str(h.root / "scratch-cwd")
    if variant == "full_path_comm":
        p = h.proc(777, comm=code_tab, args=code_tab, cwd=scratch, exe="/usr/lib/dyld")
    else:
        p = h.proc(777, comm="claude", args="claude", cwd=scratch, exe=code_tab)
    # A non-HQ claude is listed first, so the guard must examine and reject it, then find 777.
    other = h.proc(778, cwd=str(h.root / "elsewhere"), exe="/opt/homebrew/bin/claude")
    h.scenario(procs=[other, p])
    out = dry(h)
    lines = [ln for ln in out.splitlines() if ln.startswith("[dry-run] pid ")]
    assert len(lines) == 2, out
    assert lines[0].startswith("[dry-run] pid 778") and lines[0].endswith(
        "not HQ (cwd is not $HOME, binary not in a Claude app)"
    )
    assert lines[1].startswith("[dry-run] pid 777") and lines[1].endswith("counts as HQ")

    assert "would open -a Claude, would NOT start a seat" in out

    h.scenario(procs=[other, p])
    r = h.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert h.do_script_count() == 0
    # HQ review: a Code-tab seat lives in the Claude app, so that app comes forward (open -a,
    # no Apple event), Terminal is not activated, and the browser still opens.
    assert h.activate_only_count() == 0
    assert [c["argv"] for c in h.calls("open")] == [["-a", "Claude"], ["http://127.0.0.1:3435"]]
    assert "counts as HQ; not starting a second seat" in r.stdout


def test_health_body_that_mentions_healthy_but_is_not_up_is_not_up(h):
    # HQ review: {"status": "healthy", ...} is the console's answer; a body carrying the word
    # "healthy" with another status must not read as up.
    h.scenario(loaded=True)
    (h.state / "curl_body").write_text('{"status": "degraded", "healthy": false}')
    out = dry(h)
    assert "console: healthy, no action" not in out
    assert "job loaded but not answering; would run launchctl kickstart -k" in out


def test_case5_negative_control_scratch_cwd_plain_claude_is_not_hq(h):
    p = h.proc(778, cwd=str(h.root / "elsewhere"), exe="/opt/homebrew/bin/claude")
    h.scenario(procs=[p])
    out = dry(h)
    assert "[dry-run] pid 778" in out and "not HQ" in out
    assert "hq: none found" in out

    h.scenario(procs=[p])
    r = h.run()
    assert r.returncode == 0
    assert h.do_script_count() == 1


def test_case6_double_click_starts_exactly_one_hq(h):
    h.scenario(procs=[])
    first = h.run()
    second = h.run()
    assert first.returncode == 0 and second.returncode == 0, first.stdout + second.stdout
    assert h.do_script_count() == 1
    assert h.activate_only_count() == 1
    assert "HQ is already running" in second.stdout
    assert len(h.calls("open")) == 2


def test_case7_opened_from_finder_never_prompts(h):
    # Exercise every act path that reaches osascript, curl, and open.
    runs = [
        {"procs": []},
        {"procs": [h.proc(501)]},
        {"procs": [], "curl_fails": 1, "loaded": True},
        {"procs": [], "curl_fails": 1, "loaded": False, "plist": True},
    ]
    for sc in runs:
        h.scenario(**sc)
        r = h.run()
        assert r.returncode == 0, r.stdout + r.stderr
    osa = h.calls("osascript")
    assert osa, "expected osascript calls"
    for c in osa:
        text = " ".join(c["argv"]) + "\n" + c["stdin"]
        apps = re.findall(r'application\s+"([^"]+)"', text)
        assert apps and set(apps) == {"Terminal"}, text
        assert "System Events" not in text
    assert h.calls("sudo") == []
    for c in h.calls("curl"):
        urls = [a for a in c["argv"] if "://" in a]
        assert urls and all(u.startswith("http://127.0.0.1:") for u in urls), c
    for c in h.calls("open"):
        assert c["argv"] == ["http://127.0.0.1:3435"]
    assert "sudo" not in SCRIPT.read_text()


# ── the guard, the syntax, and dry-run itself ───────────────────────────────


def test_print_child_is_not_counted_as_hq(h):
    # cwd is $HOME, so the -p exclusion is the only thing keeping it out.
    kids = [
        h.proc(601, args="claude -p summarize this"),
        h.proc(602, args="claude --print hello"),
    ]
    h.scenario(procs=kids)
    out = dry(h)
    assert "pid 601 comm=claude: non-interactive" in out
    assert "pid 602 comm=claude: non-interactive" in out
    assert "hq: none found" in out

    h.scenario(procs=kids)
    r = h.run()
    assert r.returncode == 0
    assert h.do_script_count() == 1


def test_non_claude_process_names_are_ignored(h):
    others = [
        h.proc(701, comm="claudette", args="claudette"),
        h.proc(702, comm="/usr/bin/notclaude", args="notclaude"),
    ]
    h.scenario(procs=others)
    out = dry(h)
    assert "no process named claude found" in out
    assert "pid 701" not in out and "pid 702" not in out


def test_bash_syntax():
    r = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_script_shape():
    text = SCRIPT.read_text()
    assert text.startswith("#!/bin/bash\n")
    assert "\nset -u\n" in text
    assert os.access(SCRIPT, os.X_OK)


@pytest.mark.parametrize(
    "sc",
    [
        {"curl_fails": 0},
        {"curl_fails": -1, "loaded": True},
        {"curl_fails": -1, "loaded": False, "plist": True},
        {"curl_fails": -1, "loaded": False, "plist": False},
    ],
    ids=["up", "loaded-down", "not-loaded-plist", "no-plist"],
)
@pytest.mark.parametrize("how", ["flag", "env"])
def test_dry_run_acts_on_nothing(h, sc, how):
    h.scenario(**sc)
    args, extra = (("--dry-run",), None) if how == "flag" else ((), {"HQ_COCKPIT_DRY_RUN": "1"})
    r = h.run(*args, env_extra=extra)
    assert r.returncode == 0, r.stdout + r.stderr
    assert_no_action(h)
    assert "[dry-run] browser: would open" in r.stdout
    assert not h.log.parent.exists()


# ── Phase 2: the launcher starts HQ through the console panel ───────────────


def test_panel_mode_opens_start_url_and_waits_then_no_terminal(h):
    h.scenario()
    (h.state / "panel_starts").write_text("1")
    r = h.run(env_extra={"HQ_COCKPIT_MODE": "panel"})
    assert r.returncode == 0, r.stdout + r.stderr
    opens = [c["argv"] for c in h.calls("open")]
    assert opens == [["http://127.0.0.1:3435/?start=hq"]]
    assert h.do_script_count() == 0
    assert any("/hq/status" in c["argv"][-1] for c in h.calls("curl"))


def test_panel_mode_falls_back_to_terminal_when_panel_never_starts(h):
    h.scenario()
    r = h.run(env_extra={"HQ_COCKPIT_MODE": "panel"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert h.do_script_count() == 1
    assert "falling back to Terminal" in r.stdout
    opens = [c["argv"] for c in h.calls("open")]
    assert opens == [["http://127.0.0.1:3435/?start=hq"]]


def test_panel_mode_with_hq_live_brings_it_forward_and_opens_console(h):
    h.scenario(procs=[h.proc(501, args="claude -n HQ /hq-boot")])
    r = h.run(env_extra={"HQ_COCKPIT_MODE": "panel"})
    assert r.returncode == 0
    assert h.do_script_count() == 0 and h.activate_only_count() == 1
    assert [c["argv"] for c in h.calls("open")] == [["http://127.0.0.1:3435"]]


def test_panel_mode_dry_run_names_both_paths(h):
    h.scenario()
    out = dry(h, env_extra={"HQ_COCKPIT_MODE": "panel"})
    assert "would open http://127.0.0.1:3435/?start=hq" in out
    assert "fall back to Terminal" in out
    assert h.calls("open") == [] and h.do_script_count() == 0


def test_default_mode_is_panel(h):
    h.scenario()
    (h.state / "panel_starts").write_text("1")
    r = h.run(env_extra={"HQ_COCKPIT_MODE": ""})
    assert r.returncode == 0, r.stdout + r.stderr
    assert h.do_script_count() == 0
    assert [c["argv"] for c in h.calls("open")] == [["http://127.0.0.1:3435/?start=hq"]]


def test_panel_mode_rechecks_before_falling_back_when_the_panel_started_late(h):
    """Review finding (Important 2): the page's start may land after the 10 s window;
    the launcher must look again before it starts a Terminal seat."""
    h.scenario()
    (h.state / "panel_starts_late").write_text("1")
    r = h.run(env_extra={"HQ_COCKPIT_MODE": "panel"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert h.do_script_count() == 0, r.stdout
    assert "started late" in r.stdout
