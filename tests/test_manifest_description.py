"""pyproject's description carries no count, and the manifest check holds it there (design E2).

R1 section 4c, measured 2026-10-21: the package description, the line
``pip show`` and PyPI print, said "MCP server with 97 tools" while
``list_tools()`` published 53. It also named ``arrive_delta``, retired
2026-09-06, and a Haiku 4.5 scribe, whose one tool (``ask_scribe``) retired
the same day. ``manifest --check`` passed anyway: it read pyproject's version
and nothing else.

THE RULE, design E2 (docs/superpowers/specs/2026-10-21-next-phase-design.md)
as HQ ruled it for this task: the description carries no tool count, and the
check compares the manifest's and the README's stated counts to
``len(list_tools())``. So a count in the description fails the check whether
or not it equals the live count today. A correct count there is a hand-typed
claim that the next retirement turns false. The live count already has two
homes, ``stack_manifest.json`` and the README's generated block, and the
check compares both to the manifest it builds from ``list_tools()``.

Readings, stated so a reviewer can dispute each one by name:

  * No ``as of`` exemption. The README allows a dated count as history; a
    package description is one current sentence with no history to hold.
  * The description's count reader is wider than the README rule: it also
    sees up to two words before the noun ("53 MCP tools", "12 Consciousness
    Tools"), in either case. The README rule is unchanged by this task.
  * A pyproject with no description makes no claim and passes. A description
    the reader cannot read raises; it never passes unread.

Every rule here ships with a control that shows it can fail and one that shows
it can pass.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from sovereign_stack import manifest as mf
from sovereign_stack import server

ROOT = Path(__file__).resolve().parents[1]

COUNT_FREE = (
    'description = "MCP server for AI memory. The live tool count is in stack_manifest.json."'
)


def _published_count() -> int:
    return len(asyncio.run(server.list_tools()))


def _synthetic_manifest() -> dict:
    return {
        "manifest_schema_version": mf.MANIFEST_SCHEMA_VERSION,
        "version": "1.21.0",
        "source_commit": "deadbeef",
        "working_tree_dirty": False,
        "tools_count": 2,
        "tools": ["alpha", "beta"],
        "tests_collected": None,
    }


def _pyproject(description_line: str, version: str = "1.21.0") -> str:
    return f'[project]\nname = "sovereign-stack"\nversion = "{version}"\n{description_line}\n'


def _readme(manifest: dict) -> str:
    return f"# Stack\n\n{mf.render_block(manifest)}\n\nsome prose\n"


def _description_problems(manifest: dict, pyproject: str) -> list[str]:
    """The check's findings about the description, on a README that is otherwise current."""
    problems = mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject)
    return [p for p in problems if "pyproject description" in p]


def _retired_names_in(text: str) -> list[str]:
    """Retired tools named in ``text``.

    A snake_case name counts where it stands as a whole identifier. A one-word
    name (route, derive, govern, metabolize) is also an English word, so only a
    call, ``name(``, names that tool.
    """
    identifiers = set(re.findall(r"(?<![A-Za-z0-9_])[a-z][a-z0-9_]*(?![A-Za-z0-9_])", text))
    calls = set(re.findall(r"(?<![A-Za-z0-9_.])([a-z][a-z0-9_]*)\(", text))
    return sorted(
        name
        for name in server.RETIRED_TOOLS
        if ("_" in name and name in identifiers) or name in calls
    )


# ── The check can fail ──────────────────────────────────────────────────────


def test_a_description_stating_a_stale_tool_count_fails_the_check():
    """The brief's case, against the live registry: the stated count differs from list_tools()."""
    manifest = mf.build_manifest(repo_root=ROOT)
    live = _published_count()
    stale = live + 44  # 97 against 53 when R1 measured it
    pyproject = _pyproject(
        f'description = "MCP server with {stale} tools for AI memory"', manifest["version"]
    )
    problems = _description_problems(manifest, pyproject)
    assert len(problems) == 1, problems
    assert f"states {stale} tools (list_tools() publishes {live})" in problems[0]


def test_a_description_stating_the_live_count_still_fails_the_check():
    """Count-free, not count-correct: right today, wrong at the next retirement."""
    manifest = mf.build_manifest(repo_root=ROOT)
    live = _published_count()
    pyproject = _pyproject(f'description = "MCP server with {live} tools"', manifest["version"])
    problems = _description_problems(manifest, pyproject)
    assert len(problems) == 1, problems
    assert f"states {live} tools" in problems[0]


@pytest.mark.parametrize(
    ("fragment", "claim"),
    [
        ("97 tools", "97 tools"),
        ("53 MCP tools", "53 MCP tools"),
        ("12 Consciousness Tools", "12 Consciousness Tools"),
        ("1 tool", "1 tool"),
        ("3,856 tests", "3,856 tests"),
        ("1,918+ tests", "1,918+ tests"),
        pytest.param("8 tools (as of Feb 2026)", "8 tools", id="a dated count is still a count"),
    ],
)
def test_every_count_shape_in_the_description_fails_the_check(fragment, claim):
    manifest = _synthetic_manifest()
    problems = _description_problems(
        manifest, _pyproject(f'description = "A stack with {fragment}."')
    )
    assert len(problems) == 1, problems
    assert f"states {claim} (" in problems[0]


@pytest.mark.parametrize(
    "description_line",
    [
        pytest.param('description = """MCP server with 97 tools"""', id="multi-line basic string"),
        pytest.param(
            "description = '''MCP server with 97 tools'''", id="multi-line literal string"
        ),
        pytest.param("description = 'MCP server with 97 tools'", id="literal string"),
        pytest.param('description = "MCP server with 97 tools', id="unterminated"),
        pytest.param(
            'description = """97 tools"""\n[tool.x]\ndescription = "fine"',
            id="a later table's description cannot stand in",
        ),
    ],
)
def test_a_description_the_check_cannot_read_raises_rather_than_passing_unread(
    description_line,
):
    manifest = _synthetic_manifest()
    with pytest.raises(mf.ManifestError, match="description"):
        mf.check_docs_against_manifest(manifest, _readme(manifest), _pyproject(description_line))


# ── The check can pass ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "MCP server for AI memory. Runtime-reflexive. 100% local.",
        "Python 3.10 to 3.12, OAuth 2.1, and a Haiku 4.5 scribe in its history.",
    ],
)
def test_numbers_that_are_not_counts_pass_the_check(text):
    manifest = _synthetic_manifest()
    pyproject = _pyproject(f'description = "{text}"')
    assert mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject) == []


def test_a_pyproject_with_no_description_makes_no_claim():
    manifest = _synthetic_manifest()
    pyproject = 'name = "x"\nversion = "1.21.0"\n'
    assert mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject) == []


# ── manifest --check itself, on a scratch tree ──────────────────────────────


def _scratch_tree(root: Path, description_line: str) -> None:
    """A tree that --check passes in every respect but, perhaps, the description."""
    (root / "pyproject.toml").write_text(_pyproject(description_line), encoding="utf-8")
    manifest = mf.build_manifest(repo_root=root)
    (root / "README.md").write_text(_readme(manifest), encoding="utf-8")
    (root / "stack_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def test_manifest_check_fails_on_a_count_in_the_description(tmp_path, capsys):
    live = _published_count()
    _scratch_tree(tmp_path, f'description = "MCP server with {live + 44} tools"')
    assert mf._main(["--check"], root=tmp_path) == 1
    out = capsys.readouterr().out
    assert "MANIFEST CHECK FAILED" in out
    assert f"states {live + 44} tools (list_tools() publishes {live})" in out


def test_manifest_check_passes_the_same_tree_with_a_count_free_description(tmp_path, capsys):
    """The control for the test above: the same tree, differing only in its description."""
    _scratch_tree(tmp_path, COUNT_FREE)
    assert mf._main(["--check"], root=tmp_path) == 0
    assert "manifest check OK" in capsys.readouterr().out


# ── This repository ─────────────────────────────────────────────────────────


def _live_description() -> str | None:
    return mf._read_description((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_live_description_carries_no_count_and_points_at_the_manifest():
    manifest = mf.build_manifest(repo_root=ROOT)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert _description_problems(manifest, pyproject) == []
    description = _live_description()
    assert description, "pyproject.toml has no description, so this test would pass by silence"
    assert "stack_manifest.json" in description, (
        "the description no longer says where the live tool count lives"
    )


def test_the_live_description_names_no_retired_tool():
    description = _live_description() or ""
    assert _retired_names_in(description) == [], (
        f"pyproject's description names retired tools: {_retired_names_in(description)}"
    )


def test_the_counts_the_check_compares_are_the_published_surface():
    """The ruling's second half.

    --check compares the committed manifest's count and the README block's count
    to the manifest it builds, so that manifest must count list_tools(), the
    published surface, and not the registry, which still holds every retired
    schema.
    """
    manifest = mf.build_manifest(repo_root=ROOT)
    published = asyncio.run(server.list_tools())
    assert manifest["tools_count"] == len(published)
    assert manifest["tools"] == sorted(tool.name for tool in published)
    assert len(server._registered_tools()) > len(published), (
        "with nothing retired, this test could not tell the two surfaces apart"
    )
