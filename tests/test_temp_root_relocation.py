"""pytest's temp root never sits under a scratch root (design C3; plan 1 Task 4 fix round 1).

provenance.validate_file_ref refuses a file receipt under /tmp or /private/tmp
(provenance.SCRATCH_ROOTS). On Linux CI (ubuntu-latest) tempfile.gettempdir() is
/tmp, so tmp_path, and every receipt a test builds in it, would be refused by the
rule under test. tests/conftest.py's pytest_configure moves the process temp dir
to <rootdir>/.pytest-tmp when the platform temp dir resolves under a scratch root.

No macOS run shows the problem (its per-user temp dir is /private/var/folders/...,
outside both roots), so the hook is pinned two ways that hold on any machine:

  the canary    the real tmp_path of whatever machine runs the suite is not under
                a scratch root; on Linux CI it fails, naming the hook, if the hook
                did not fire
  the hook      pytest_configure called directly against a stand-in scratch root
                under tmp_path: it moves a temp dir under the root into the
                checkout, and leaves alone an explicit --basetemp,
                PYTEST_DEBUG_TEMPROOT, and a temp dir outside every root

Hermetic: the stand-in root, the temp dir and the checkout all live under tmp_path,
and tempfile.tempdir and TMPDIR are monkeypatched before the hook runs, so whatever
the hook sets is undone for the rest of the suite.
"""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sovereign_stack import provenance
from tests import conftest


def test_tmp_path_is_not_under_a_scratch_root(tmp_path):
    resolved = tmp_path.resolve()
    under = [root for root in provenance.SCRATCH_ROOTS if resolved.is_relative_to(root)]
    assert not under, (
        f"tmp_path {resolved} is under the scratch root {under[0]}. validate_file_ref "
        "refuses file receipts there (design C3), so every receipt test that builds its "
        "evidence in tmp_path fails for the wrong reason. tests/conftest.py's "
        "pytest_configure moves the temp root to <rootdir>/.pytest-tmp when the platform "
        "temp dir resolves under a scratch root (Linux CI's /tmp); it did not fire here, "
        "or an explicit --basetemp or PYTEST_DEBUG_TEMPROOT put the temp root under one."
    )


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """A stand-in scratch root with the process temp dir inside it, and a
    checkout (the rootdir) to move into."""
    scratch = tmp_path / "scratch"
    platform_tmp = scratch / "T"
    platform_tmp.mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    scratch = scratch.resolve()
    monkeypatch.setattr(provenance, "SCRATCH_ROOTS", (scratch,))
    monkeypatch.setattr(tempfile, "tempdir", str(platform_tmp))
    monkeypatch.setenv("TMPDIR", str(platform_tmp))
    monkeypatch.delenv("PYTEST_DEBUG_TEMPROOT", raising=False)
    return SimpleNamespace(scratch=scratch, platform_tmp=platform_tmp, checkout=checkout)


def _config(rootpath: Path, basetemp: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(rootpath=rootpath, option=SimpleNamespace(basetemp=basetemp))


def _left_alone(stand_in) -> None:
    assert tempfile.gettempdir() == str(stand_in.platform_tmp)
    assert os.environ["TMPDIR"] == str(stand_in.platform_tmp)
    assert not (stand_in.checkout / ".pytest-tmp").exists()


def test_a_temp_dir_under_a_scratch_root_moves_into_the_checkout(stand_in):
    conftest.pytest_configure(_config(stand_in.checkout))

    relocated = stand_in.checkout / ".pytest-tmp"
    assert relocated.is_dir()
    assert tempfile.gettempdir() == str(relocated)
    assert os.environ["TMPDIR"] == str(relocated)
    assert not any(relocated.resolve().is_relative_to(r) for r in provenance.SCRATCH_ROOTS)


def test_an_explicit_basetemp_is_left_alone(stand_in):
    conftest.pytest_configure(_config(stand_in.checkout, basetemp=str(stand_in.scratch / "bt")))
    _left_alone(stand_in)


def test_pytest_debug_temproot_is_left_alone(stand_in, monkeypatch):
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(stand_in.platform_tmp))
    conftest.pytest_configure(_config(stand_in.checkout))
    _left_alone(stand_in)


def test_a_temp_dir_outside_every_scratch_root_is_left_alone(stand_in, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(elsewhere))

    conftest.pytest_configure(_config(stand_in.checkout))

    assert tempfile.gettempdir() == str(elsewhere)
    assert not (stand_in.checkout / ".pytest-tmp").exists()
