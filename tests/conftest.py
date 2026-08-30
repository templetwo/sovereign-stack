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

import subprocess
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── AUTOUSE GUARD 1 of 2: the live-audit tripwire ───────────────────────────
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
