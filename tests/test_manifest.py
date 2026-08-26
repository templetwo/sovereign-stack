"""
Tests for executable self-description.

Every test here answers one question: can this gate FAIL? A drift check
that has never been shown to reject a drifting document is decoration.
The load-bearing case is `test_readme_without_block_raises` — a document
that asserts nothing must not pass by having nothing to say.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sovereign_stack import manifest as mf

ROOT = Path(__file__).resolve().parents[1]


def _manifest():
    return {
        "manifest_schema_version": 1,
        "version": "1.15.0",
        "source_commit": "deadbeef",
        "working_tree_dirty": False,
        "tools_count": 2,
        "tools": ["alpha", "beta"],
        "tests_collected": 10,
    }


def _readme(extra: str = "", block: str | None = None) -> str:
    body = block if block is not None else mf.render_block(_manifest())
    return f"# Stack\n\n{body}\n\nsome prose\n{extra}\n"


PYPROJECT = 'name = "x"\nversion = "1.15.0"\n'


class TestBuildManifest:
    def test_reflects_the_live_registry(self):
        m = mf.build_manifest(repo_root=ROOT)
        assert m["tools_count"] == len(m["tools"]) > 0
        assert m["tools"] == sorted(m["tools"])
        assert m["manifest_schema_version"] == mf.MANIFEST_SCHEMA_VERSION

    def test_empty_registry_raises_rather_than_emitting_zero(self, monkeypatch):
        """An empty manifest would read as 'zero tools, verified'."""
        monkeypatch.setattr(mf, "live_tool_names", list)
        with pytest.raises(mf.ManifestError):
            mf.build_manifest(repo_root=ROOT)

    def test_live_tool_names_refuses_empty(self, monkeypatch):
        import sovereign_stack.server as srv

        async def _none():
            return []

        monkeypatch.setattr(srv, "list_tools", _none)
        with pytest.raises(mf.ManifestError):
            mf.live_tool_names()


class TestDriftCheckCanFail:
    def test_agreeing_docs_pass(self):
        assert mf.check_docs_against_manifest(_manifest(), _readme(), PYPROJECT) == []

    def test_undated_count_outside_block_fails(self):
        problems = mf.check_docs_against_manifest(
            _manifest(), _readme(extra="the server registers all 82 tools"), PYPROJECT
        )
        assert any("undated count" in p for p in problems)

    def test_dated_count_outside_block_is_allowed(self):
        """History is not drift. The phrase is what makes the difference."""
        problems = mf.check_docs_against_manifest(
            _manifest(),
            _readme(extra="82 tools live, as of v1.5.1 / May 2026"),
            PYPROJECT,
        )
        assert problems == []

    def test_stale_block_fails(self):
        stale = f"{mf.BEGIN_MARK}\n\n**v1.0.0 · 1 tools · 1 tests**\n\n{mf.END_MARK}"
        problems = mf.check_docs_against_manifest(_manifest(), _readme(block=stale), PYPROJECT)
        assert any("stale" in p for p in problems)

    def test_version_disagreement_fails(self):
        problems = mf.check_docs_against_manifest(_manifest(), _readme(), 'version = "9.9.9"\n')
        assert any("pyproject version" in p for p in problems)

    def test_readme_without_block_raises(self):
        """
        THE LOAD-BEARING CONTROL. A README asserting no current count would
        satisfy every other check in this file by saying nothing at all.
        Silence must not read as agreement.
        """
        with pytest.raises(mf.ManifestError):
            mf.check_docs_against_manifest(_manifest(), "# Stack\n\njust prose\n", PYPROJECT)

    def test_unparseable_pyproject_raises(self):
        with pytest.raises(mf.ManifestError):
            mf.check_docs_against_manifest(_manifest(), _readme(), "no version here")


class TestRepositoryIsCurrent:
    """The regression that will catch the next drift, on this actual repo."""

    def test_live_repo_docs_agree_with_the_registry(self):
        m = mf.build_manifest(repo_root=ROOT)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert mf.check_docs_against_manifest(m, readme, pyproject) == []

    def test_committed_manifest_matches_the_registry(self):
        import json

        m = mf.build_manifest(repo_root=ROOT)
        committed = json.loads((ROOT / "stack_manifest.json").read_text(encoding="utf-8"))
        assert committed["tools"] == m["tools"]
        assert committed["tools_count"] == m["tools_count"]
        assert committed["version"] == m["version"]
