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
