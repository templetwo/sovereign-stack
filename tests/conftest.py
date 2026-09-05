"""
Shared pytest fixtures for the sovereign-stack test suite.

Available fixtures
------------------

tmp_sovereign_root(monkeypatch, tmp_path)
    Yields a Path that mirrors the ~/.sovereign/ directory layout.
    Sets the SOVEREIGN_ROOT environment variable to that path so any
    code that reads ``os.environ["SOVEREIGN_ROOT"]`` picks up the sandbox
    automatically.  The fixture name is deliberately distinct from the
    per-file ``tmp_sovereign`` and ``sovereign_root`` fixtures already
    defined in individual test modules — pytest's local scope always wins,
    so there is no shadowing.

    Subdirs created:
        chronicle/insights, chronicle/open_threads,
        daemons/halts, decisions, nape, comms, handoffs, reflexive,
        consciousness

    Usage::

        def test_something(tmp_sovereign_root):
            assert (tmp_sovereign_root / "chronicle").is_dir()

silent_subprocess(monkeypatch)
    Patches ``subprocess.run`` (looked up via the ``subprocess`` module)
    to return a ``CompletedProcess`` with returncode=0, stdout=b"",
    stderr=b"" — no real processes are spawned.  Pass keyword overrides
    as attributes on the returned mock when you need a specific returncode
    or output::

        def test_non_zero(silent_subprocess):
            silent_subprocess.returncode = 1
            ...

frozen_now(monkeypatch)
    Monkeypatches ``datetime.datetime`` in the ``datetime`` module so that
    ``datetime.now()`` always returns the same stable instant
    (2026-04-24 12:00:00 UTC).  Useful for tests that write timestamps
    and compare them to expected strings.

    Usage::

        def test_stamped(frozen_now):
            from datetime import datetime, timezone
            assert datetime.now(timezone.utc).year == 2026
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── AUTOUSE GUARD 1 of 4: the live-audit tripwire ───────────────────────────
#
# COUNT UPDATED 2026-09-05 (was "1 of 2"). Two more guards were added in the
# middle of this file, each in its own labelled block exactly as the note below
# asks: the scribe containment and the nape autohook containment. Four autouse
# guards now, all protecting the same thing from different write paths.
#
# DELIBERATELY PLACED HERE, ABOVE THE FIXTURES, NOT APPENDED AT THE END.
# feat/console-v2-reskin adds its own autouse guard (probe containment) at the
# bottom of this file, and two branches appending different blocks to the same
# last line is an add/add conflict every time. Keeping this one up here means
# both guards land, both stay autouse, and the merge is clean. If you add a
# third, give it its own labelled block somewhere neither of these two ends.
#
# WHY THIS EXISTS, in one incident: on 2026-08-30 a fixture in
# test_ring2_post_v121_regressions.py rebound openai_bridge.pending_writes
# .PENDING_DIR and hash_chain.AUDIT_DIR, and believed itself sandboxed. It was
# not. audit.py does `from .hash_chain import AUDIT_DIR, AUDIT_LOG`, and a
# from-import copies the VALUE at import time — so append_audit_event kept
# writing through audit.AUDIT_LOG to Anthony's real hash chain. 383 rows across
# 114 synthetic proposal ids landed in
# ~/.sovereign/openai_bridge/audit/audit.jsonl, and the whole suite reported
# green throughout. The test could not see its own blast radius.
#
# Every existing defence was aimed one layer too high: tmp_sovereign_root sets
# an env var these modules never read, and the fixture patched the names it
# could see rather than the names the writer actually resolves.
#
# So this asserts on the ONE thing that cannot be faked — the bytes on disk.
# Function-scoped deliberately: a session-scoped check would say "something in
# 2445 tests wrote to the live chain" and leave the bisect to a human, while
# this names the test in its own failure.
#
# Scope note: bridge audit logs are written only by a bridge drain, so a daemon
# is not expected to move them mid-run. If this ever fires without a test being
# at fault, that is still worth knowing — something wrote to Anthony's audit
# chain while the suite was running.
_LIVE_AUDIT_GLOB = ".sovereign/*/audit/audit.jsonl"


def _live_audit_sizes() -> dict[str, int]:
    home = Path.home()
    if not (home / ".sovereign").is_dir():
        return {}
    sizes: dict[str, int] = {}
    for p in home.glob(_LIVE_AUDIT_GLOB):
        try:
            sizes[str(p)] = p.stat().st_size
        except OSError:
            continue
    return sizes


@pytest.fixture(autouse=True)
def no_live_audit_writes():
    """Fail any test that changes a live bridge audit log on disk.

    Fail-closed and content-based: it compares real byte sizes, so it holds
    regardless of which module global a future test forgets to rebind. Skips
    cleanly when ~/.sovereign does not exist (CI, a fresh clone).
    """
    before = _live_audit_sizes()
    yield
    after = _live_audit_sizes()
    grew = {
        path: (before.get(path), size) for path, size in after.items() if before.get(path) != size
    }
    assert not grew, (
        "THIS TEST WROTE TO A LIVE AUDIT CHAIN under ~/.sovereign — "
        f"{grew}. Isolate EVERY write path the code can reach, not just the "
        "obvious one: openai_bridge needs all five of "
        "pending_writes.PENDING_DIR, hash_chain.AUDIT_DIR, hash_chain.AUDIT_LOG, "
        "audit.AUDIT_DIR and audit.AUDIT_LOG rebound, because audit.py "
        "from-imports the last two by value."
    )


# ── Stable reference time used by frozen_now ────────────────────────────────
_FROZEN_UTC = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)

# ── Subdirectories that mirror a populated ~/.sovereign/ tree ────────────────
_SOVEREIGN_SUBDIRS = [
    "chronicle/insights",
    "chronicle/open_threads",
    "daemons/halts",
    "decisions",
    "nape",
    "comms",
    "handoffs",
    "reflexive",
    "consciousness",
]


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_sovereign_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Generator[Path, None, None]:
    """Sandbox ~/.sovereign/ layout; sets SOVEREIGN_ROOT env var."""
    root = tmp_path / ".sovereign"
    for subdir in _SOVEREIGN_SUBDIRS:
        (root / subdir).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SOVEREIGN_ROOT", str(root))
    yield root


@pytest.fixture
def silent_subprocess(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace subprocess.run with a no-op stub (returncode=0)."""
    fake = MagicMock(
        spec=subprocess.CompletedProcess,
        returncode=0,
        stdout=b"",
        stderr=b"",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    return fake


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """
    Monkeypatch datetime.datetime so .now() / .utcnow() return a stable
    instant (2026-04-24 12:00:00 UTC).

    Only patches the ``datetime`` class inside the ``datetime`` module.
    Code that already holds a reference to the real ``datetime`` class
    (imported at module load time) will not see the patch — standard
    monkeypatch limitation.
    """
    import datetime as _dt_module

    class _FrozenDatetime(_dt_module.datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return _FROZEN_UTC.astimezone(tz) if tz else _FROZEN_UTC.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):  # type: ignore[override]
            return _FROZEN_UTC.replace(tzinfo=None)

    monkeypatch.setattr(_dt_module, "datetime", _FrozenDatetime)
    return _FROZEN_UTC


# ── AUTOUSE GUARD (own labelled block, per the note at the top of this file) ─
#   THE SCRIBE NEVER BILLS, AND NEVER WRITES INTO THE LIVE STORE, FROM A TEST
#
# WHY THIS EXISTS, measured: on 2026-09-05 a review of this branch snapshotted
# ~/.sovereign around one full-suite run and found 61 files changed, 55 of them
# under ~/.sovereign/scribe_threads/ — 53 brand-new scribe session logs carrying
# "parent_instance": "test" / "test-instance" / "test-refactor" / "test-compact",
# real generated greeting prose, and a per-session cost_usd. Every boot-path test
# was making a LIVE Sonnet call on Anthony's account and writing the result into
# his live store. Across one day of runs: 385 sessions, $18.17.
#
# The receipt that missed it globbed `*.jsonl`. The writes are `.log` and
# `.json`. A filter narrower than the write path CANNOT FAIL, and a check that
# cannot fail is not a receipt — the same fail-open shape this branch exists to
# close on record_learning.
#
# MECHANISM, and note that neither half is a bug in this branch's diff:
#   1. scribe/bridge_integration.py:61 defaults SCRIBE_BOOT_GREETING to "on";
#      server.py:3428 then spawns a session and awaits a live model call on
#      EVERY where_did_i_leave_off. (HQ doctrine calls this flag "a valve
#      connected to nothing" — it is connected; the DEFAULT is what is wrong.)
#   2. server.py:3405 calls ensure_resident_scribe() BEFORE that flag is read,
#      and it writes ~/.sovereign/scribe_threads/_resident/state.json. The flag
#      does not gate it. Closing only limb 1 would have been a second false
#      receipt.
#
# WHY IT IS AN IN-PROCESS SEAM GUARD AND NOT A DISK DIFF, unlike the audit
# tripwire above: scribe_threads is a CONCURRENTLY WRITTEN directory. The live
# bridge serves real boots, and a sibling worktree running this same suite
# writes there too (observed live, 361 -> 385 files in ~15 minutes while this
# fixture was being written). A per-test before/after stat of that tree would
# fail tests for another process's writes. Everything below is process-local,
# so it says something true regardless of what else is running on the machine.
#
# THREE LIMBS, because the spend and the write happen at different moments:
#   (a) SCRIBE_BOOT_GREETING=off — read per call at bridge_integration.py:61,
#       so setenv works; skips spawn, model call and log entirely.
#   (b) HaikuClient cannot be CONSTRUCTED. get_client() imports it inside the
#       function body, so the patch lands; it already swallows the failure and
#       returns None, which is the same degraded path as "no API key". This is
#       the limb that stops MONEY: the log write happens after the API call
#       returns, so a write-only guard would report a charge already made.
#       Tests that inject their own fake client (test_scribe_navigational uses
#       HaikuClient.__new__) are unaffected — they never construct one.
#   (c) The three scribe writers refuse a destination inside the real
#       ~/.sovereign, resolved AT CALL TIME so a test that redirects the module
#       constants (test_scribe_resident, test_scribe_navigational) still passes
#       and an unisolated caller fails BY TEST NAME.
#
# This guard is demonstrated able to fail in tests/test_scribe_containment.py —
# an unfalsifiable containment fixture is the artifact class it replaces.
_LIVE_SOVEREIGN = Path(os.path.expanduser("~/.sovereign")).resolve()


def _is_under_live_sovereign(path: Path) -> bool:
    """True if `path` lands inside the operator's real ~/.sovereign."""
    try:
        candidate = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return candidate == _LIVE_SOVEREIGN or _LIVE_SOVEREIGN in candidate.parents


class _LiveScribeWriteRefused(AssertionError):
    """Raised in-process when a test aims a scribe writer at the live store."""


_IMPORT_TIME_BINDING_HINT = (
    "these are bound from SOVEREIGN_ROOT AT MODULE IMPORT, so setting that env "
    "var inside a test cannot redirect them."
)


def _refuse_live_root(kind: str, dest: Path, hint: str) -> None:
    if _is_under_live_sovereign(dest):
        raise _LiveScribeWriteRefused(f"THIS TEST AIMED {kind} AT THE LIVE STORE: {dest}. {hint}")


@pytest.fixture(autouse=True)
def _scribe_never_bills_or_writes_live(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    from sovereign_stack.scribe import bridge_integration as _bi
    from sovereign_stack.scribe import haiku_client as _hc
    from sovereign_stack.scribe import resident as _res
    from sovereign_stack.scribe import session as _sess

    # (a) the real cost kill switch, suite-wide.
    monkeypatch.setenv("SCRIBE_BOOT_GREETING", "off")

    # (b) no test may construct a live model client.
    class _RefusedHaikuClient:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError(
                "the test suite may not construct a live HaikuClient "
                "(tests/conftest.py::_scribe_never_bills_or_writes_live)"
            )

    monkeypatch.setattr(_hc, "HaikuClient", _RefusedHaikuClient)

    # (c) the three writers refuse the live root, resolved at call time.
    scribe_hint = (
        "Redirect the module constant the writer actually resolves "
        "(scribe.bridge_integration.PHASE1_LOG_ROOT, "
        "scribe.resident.RESIDENT_STATE_PATH, scribe.session.ARCHIVE_ROOT / "
        f"ScribeSessionStore(archive_root=...)) — {_IMPORT_TIME_BINDING_HINT}"
    )

    _orig_log = _bi._log_phase1_greeting

    def _guarded_log(session, greeting_text, result_meta):
        _refuse_live_root("THE SCRIBE'S greeting log", _bi.PHASE1_LOG_ROOT, scribe_hint)
        return _orig_log(session, greeting_text, result_meta)

    monkeypatch.setattr(_bi, "_log_phase1_greeting", _guarded_log)

    _orig_marker = _res._write_resident_marker

    def _guarded_marker(*args, **kwargs):
        _refuse_live_root("THE SCRIBE'S resident marker", _res.RESIDENT_STATE_PATH, scribe_hint)
        return _orig_marker(*args, **kwargs)

    monkeypatch.setattr(_res, "_write_resident_marker", _guarded_marker)

    _orig_archive = _sess.archive_session
    _archive_sig = inspect.signature(_orig_archive)

    def _guarded_archive(*args, **kwargs):
        # Bind against the real signature so the archive_root DEFAULT we check
        # is the one the function will actually use, not a re-read of the
        # module constant (which a test may have patched without the default
        # following it).
        bound = _archive_sig.bind(*args, **kwargs)
        bound.apply_defaults()
        _refuse_live_root(
            "THE SCRIBE'S session archive", bound.arguments["archive_root"], scribe_hint
        )
        return _orig_archive(*args, **kwargs)

    monkeypatch.setattr(_sess, "archive_session", _guarded_archive)


# ── AUTOUSE GUARD (own labelled block) ──────────────────────────────────────
#     THE NAPE AUTOHOOK NEVER OBSERVES INTO THE LIVE STORE
#
# The scribe was the loud half of the 2026-09-05 finding (53 of 61 changed
# files). This is one of the quiet ones, and it is the SAME SHAPE ONE MODULE
# OVER: `server.py:182` builds `nape_daemon = NapeDaemon(root=DEFAULT_ROOT)` as
# a MODULE-LEVEL SINGLETON, so the live ~/.sovereign/nape path is captured at
# import. Every tool dispatch then calls `nape_daemon.observe(...)`
# (server.py:3923, 4355, 4365) and appends a row carrying the tool name, the
# arguments and a slice of the result.
#
# MEASURED, not inferred: a full-suite run on 2026-09-05 left rows in
# ~/.sovereign/nape/observations.jsonl stamped
# "source_instance": "test", tool_name "where_did_i_leave_off", inside the run
# window. Anthony's live drift-detection telemetry was carrying the test
# suite's synthetic tool calls.
#
# WHY THE EXISTING DEFENCES MISS IT, which is the instructive part: the
# phase-4 fixture patches `server.DEFAULT_ROOT`, and `tmp_sovereign_root` sets
# `SOVEREIGN_ROOT` — but the singleton read that value ONCE, at import, and
# holds a Path built from it. Patching the name a writer was built FROM does
# not move the writer. That is verbatim the lesson the audit-chain guard at the
# top of this file was written for.
#
# TWO LIMBS, redirect and refuse, because either alone is weaker than it looks:
#   - REDIRECT the singleton to a session-scoped tmp root, so dispatch-driven
#     tests keep exercising the real autohook and simply land elsewhere. No
#     test needs changing, and none is silently skipped.
#   - REFUSE at `_append_jsonl`, the one function all three nape stores
#     (observations, honks, acks) go through, so ANY daemon instance still
#     pointed at the live root fails BY TEST NAME rather than appending. This
#     is what makes the redirect falsifiable instead of assumed.


@pytest.fixture(scope="session")
def _nape_tmp_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One tmp nape root for the whole run — the autohook's rows are telemetry
    no test asserts on, so a per-test directory would be 3,000 mkdirs for
    nothing."""
    return tmp_path_factory.mktemp("nape-autohook-sink")


@pytest.fixture(autouse=True)
def _nape_never_writes_live(monkeypatch: pytest.MonkeyPatch, _nape_tmp_root: Path) -> None:
    # IMPORTED, NOT LOOKED UP IN sys.modules. The first draft did the latter to
    # avoid paying for the import, and it made the redirect ORDER-DEPENDENT: it
    # applied in the full suite (where some other module had already imported
    # the server at collection) and silently did NOT apply when this file was
    # run on its own, because pytest.importorskip runs inside the test, after
    # the fixture. Its own containment test caught that on the first run — a
    # guard whose coverage depends on which files you selected is the fail-open
    # shape again. Measured: importing sovereign_stack.server writes nothing to
    # the live store (0 files added or changed beyond the bridge's own logs),
    # and it is paid once per process, not per test.
    import sovereign_stack.server as _server
    from sovereign_stack import nape_daemon as _nape

    _orig_append = _nape._append_jsonl
    nape_hint = (
        "The nape daemon that wrote this is rooted at the live store. "
        "server.nape_daemon is a module-level singleton built from DEFAULT_ROOT "
        f"at import ({_IMPORT_TIME_BINDING_HINT}) — rebind "
        "sovereign_stack.server.nape_daemon itself, or construct "
        "NapeDaemon(root=<tmp>)."
    )

    def _guarded_append(path, record):
        _refuse_live_root("THE NAPE STORE", path, nape_hint)
        return _orig_append(path, record)

    monkeypatch.setattr(_nape, "_append_jsonl", _guarded_append)
    monkeypatch.setattr(_server, "nape_daemon", _nape.NapeDaemon(root=str(_nape_tmp_root)))


# ── The dashboard's two external probes never leave the test process ────────
#
# `SOVEREIGN_ROOT` redirects neither of them: `dashboard_readers.read_guardian`
# shells out to `lsof -iTCP` + two `pgrep`s, and `fetch_bridge_heartbeat` GETs
# the operator's live bridge on :8100. Containment used to live in ONE test
# file's autouse fixture, which meant any other test file that touched
# `build_snapshot()` silently probed the real machine. This puts the guard on
# the seam instead, for the whole suite.
#
# A test that stubs `_guardian_probe` / `_http_get_json` with an in-process
# fake REPLACES the guarded function and keeps exercising the real reader —
# that is why the guard sits at those two functions and not at the readers.
@pytest.fixture(autouse=True)
def _no_live_dashboard_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOVEREIGN_DASHBOARD_NO_EXTERNAL_PROBES", "1")
