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
  * The description is ``[project]``'s own key, read as TOML reads it. No
    other table's description stands in for it, and a pyproject with none
    makes no claim and passes. Python 3.11+ reads it with tomllib. Python
    3.10, which CI's test matrix still runs and which has no tomllib, reads
    it with a plain reader that reads one shape (a double-quoted line with no
    escape, under ``[project]``) and raises on every other. A description
    neither reader can read raises; it never passes unread.

The ``reader`` fixture runs a test once per reader. The plain run sets the
module's tomllib handle to None, so it runs on every interpreter, and a 3.12
run exercises the path that 3.10 CI takes.

Fix round 1, from the review of 531e35b: the first reader took the first
column-0 ``description =`` line in any table. An earlier ``[tool.x]``
description stood in for the project's, and an indented key, a quoted key and
a root-level dotted ``project.description`` each read as no description, so
the check passed all four. A root inline table, digits spelled as escapes and
a key spelled with one passed the same way. Each shape has a case below.

Every rule here ships with a control that shows it can fail and one that shows
it can pass.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from pathlib import Path

import pytest

from sovereign_stack import manifest as mf
from sovereign_stack import server

ROOT = Path(__file__).resolve().parents[1]

# Asked of the interpreter, not of the module under test, so a module that
# failed to bind tomllib where the interpreter has it is caught.
HAS_TOMLLIB = importlib.util.find_spec("tomllib") is not None

STATED = "MCP server with 97 tools"
# One backslash, spelled so that no layer between an editor and this file can
# decode a TOML escape written after it: BS + "u0039" is TOML's escape for 9.
BS = chr(92)

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


@pytest.fixture(params=["tomllib", "plain"])
def reader(request, monkeypatch):
    """Run the test once per reader: tomllib (Python 3.11+) and the plain reader (3.10's)."""
    if request.param == "tomllib" and not HAS_TOMLLIB:
        pytest.skip("tomllib is Python 3.11+; this interpreter reads with the plain reader only")
    if request.param == "plain":
        # raising=False: the binding test below, not this fixture, pins that the handle exists.
        monkeypatch.setattr(mf, "_tomllib", None, raising=False)
    return request.param


# ── The check can fail ──────────────────────────────────────────────────────


def test_a_description_stating_a_stale_tool_count_fails_the_check(reader):
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


def test_a_description_stating_the_live_count_still_fails_the_check(reader):
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
def test_every_count_shape_in_the_description_fails_the_check(fragment, claim, reader):
    manifest = _synthetic_manifest()
    problems = _description_problems(
        manifest, _pyproject(f'description = "A stack with {fragment}."')
    )
    assert len(problems) == 1, problems
    assert f"states {claim} (" in problems[0]


# Each pyproject below has a [project] description that TOML reads as STATED.
# The second value is what the plain reader does with it: read it, or raise.
COUNTED_SHAPES = [
    pytest.param(
        '[tool.x]\ndescription = "fine"\n\n' + _pyproject(f'description = "{STATED}"'),
        "raises",
        id="(a) a tool.x description before the project table",
    ),
    pytest.param(_pyproject(f'  description = "{STATED}"'), "reads", id="(b) an indented key"),
    pytest.param(_pyproject(f'"description" = "{STATED}"'), "raises", id="(c) a quoted key"),
    pytest.param(
        _pyproject(f"'description' = \"{STATED}\""), "raises", id="(c) a literal-quoted key"
    ),
    pytest.param(
        f'version = "1.21.0"\nproject.name = "sovereign-stack"\nproject.description = "{STATED}"\n',
        "raises",
        id="(d) a root-level dotted project.description",
    ),
    pytest.param(
        f'version = "1.21.0"\nproject = {{name = "sovereign-stack", description = "{STATED}"}}\n',
        "raises",
        id="a root-level inline project table",
    ),
    pytest.param(
        _pyproject(f'description = "MCP server with {BS}u0039{BS}u0037 tools"'),
        "raises",
        id="digits spelled as escapes",
    ),
    pytest.param(
        _pyproject(f'"descr{BS}u0069ption" = "{STATED}"'),
        "raises",
        id="a key spelled with an escape",
    ),
    pytest.param(
        _pyproject(f'description = """{STATED}"""'), "raises", id="a multi-line basic string"
    ),
    pytest.param(
        _pyproject(f"description = '''{STATED}'''"), "raises", id="a multi-line literal string"
    ),
    pytest.param(_pyproject(f"description = '{STATED}'"), "raises", id="a literal string"),
    pytest.param(
        _pyproject(f'description = "{STATED}"') + '\n[tool.x]\ndescription = "fine"\n',
        "raises",
        id="a tool.x description after the project table",
    ),
    pytest.param(
        f'[ project ]\nname = "sovereign-stack"\nversion = "1.21.0"\ndescription = "{STATED}"\n',
        "reads",
        id="a spaced project header",
    ),
    pytest.param(
        _pyproject(f'description = "{STATED}"  # what pip show prints'),
        "reads",
        id="a trailing comment",
    ),
]


@pytest.mark.parametrize(("pyproject", "plain_reader"), COUNTED_SHAPES)
def test_every_toml_shape_of_a_counted_description_fails_the_check(pyproject, plain_reader, reader):
    """531e35b's check returned [] for (a) to (d), the inline table and the escapes."""
    manifest = _synthetic_manifest()
    if reader == "plain" and plain_reader == "raises":
        with pytest.raises(mf.ManifestError, match="description"):
            mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject)
        return
    if reader == "tomllib":
        import tomllib

        # The case is the shape its id names: TOML itself reads STATED from it.
        assert tomllib.loads(pyproject)["project"]["description"] == STATED
    assert mf._read_description(pyproject) == STATED
    problems = _description_problems(manifest, pyproject)
    assert len(problems) == 1, problems
    assert "states 97 tools (" in problems[0]


@pytest.mark.parametrize(
    "description_line",
    [
        pytest.param(f'description = "{STATED}', id="an unterminated string"),
        pytest.param("description = 97", id="a number, not a string"),
        pytest.param('dynamic = ["description"]', id="a description the build fills in"),
        pytest.param('dynamic = "description"', id="a dynamic that is not a list"),
        pytest.param(
            f'description = "{STATED}"\ndescription = "fine"', id="a description given twice"
        ),
    ],
)
def test_a_description_neither_reader_can_read_raises_rather_than_passing_unread(
    description_line, reader
):
    manifest = _synthetic_manifest()
    with pytest.raises(mf.ManifestError, match="description"):
        mf.check_docs_against_manifest(manifest, _readme(manifest), _pyproject(description_line))


def test_a_project_that_is_not_a_table_raises_where_tomllib_reads_it(reader):
    """tomllib cannot find the package's description in a project that is not a table.

    The plain reader reads no description here, because no line names one, and
    so makes no claim: there is no count for it to miss.
    """
    manifest = _synthetic_manifest()
    pyproject = 'version = "1.21.0"\nproject = "sovereign-stack"\n'
    if reader == "plain":
        assert mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject) == []
        return
    with pytest.raises(mf.ManifestError, match="description"):
        mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject)


# ── The check can pass ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "MCP server for AI memory. Runtime-reflexive. 100% local.",
        "Python 3.10 to 3.12, OAuth 2.1, and a Haiku 4.5 scribe in its history.",
    ],
)
def test_numbers_that_are_not_counts_pass_the_check(text, reader):
    manifest = _synthetic_manifest()
    pyproject = _pyproject(f'description = "{text}"')
    assert mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject) == []


def test_a_pyproject_with_no_description_makes_no_claim(reader):
    manifest = _synthetic_manifest()
    pyproject = 'name = "x"\nversion = "1.21.0"\n'
    assert mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject) == []


def test_another_tables_description_is_not_the_packages(reader):
    """(a) turned around: a description outside [project] is not what pip show prints.

    tomllib reads [project], finds no description there, and so no claim. The
    plain reader cannot place a description outside [project] and raises rather
    than guess.
    """
    manifest = _synthetic_manifest()
    pyproject = _pyproject('readme = "README.md"') + f'\n[tool.x]\ndescription = "{STATED}"\n'
    if reader == "plain":
        with pytest.raises(mf.ManifestError, match="description"):
            mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject)
        return
    assert mf._read_description(pyproject) is None
    assert mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject) == []


def test_a_line_inside_a_multi_line_string_is_not_a_key(reader):
    """The readme string holds a line shaped like the key; TOML reads no description.

    tomllib reads none, so no claim. A line reader cannot tell that line from
    the key, so the plain reader raises on the triple quote rather than read it.
    """
    manifest = _synthetic_manifest()
    pyproject = _pyproject(f'readme = """\ndescription = "{STATED}"\n"""')
    if reader == "plain":
        with pytest.raises(mf.ManifestError, match="triple quote"):
            mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject)
        return
    assert mf._read_description(pyproject) is None
    assert mf.check_docs_against_manifest(manifest, _readme(manifest), pyproject) == []


# ── The two readers ─────────────────────────────────────────────────────────


def test_the_module_reads_with_tomllib_where_the_interpreter_has_it():
    """The fixture's plain runs set this handle to None; here it is as the module bound it."""
    expected = importlib.import_module("tomllib") if HAS_TOMLLIB else None
    assert getattr(mf, "_tomllib", "no handle") is expected


@pytest.mark.skipif(not HAS_TOMLLIB, reason="tomllib is Python 3.11+")
def test_both_readers_read_the_live_description_identically():
    """3.10 CI reads this file with the plain reader; this run shows that reading is TOML's."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    via_tomllib = mf._read_description_tomllib(text)
    assert via_tomllib, "pyproject.toml has no description, so this test would pass by silence"
    assert mf._read_description_plain(text) == via_tomllib


# ── manifest --check itself, on a scratch tree ──────────────────────────────


def _scratch_tree(root: Path, pyproject: str) -> None:
    """A tree that --check passes in every respect but, perhaps, the description."""
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    manifest = mf.build_manifest(repo_root=root)
    (root / "README.md").write_text(_readme(manifest), encoding="utf-8")
    (root / "stack_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def test_manifest_check_fails_on_a_count_in_the_description(tmp_path, capsys, reader):
    live = _published_count()
    _scratch_tree(tmp_path, _pyproject(f'description = "MCP server with {live + 44} tools"'))
    assert mf._main(["--check"], root=tmp_path) == 1
    out = capsys.readouterr().out
    assert "MANIFEST CHECK FAILED" in out
    assert f"states {live + 44} tools (list_tools() publishes {live})" in out


def test_manifest_check_passes_the_same_tree_with_a_count_free_description(
    tmp_path, capsys, reader
):
    """The control for the test above: the same tree, differing only in its description."""
    _scratch_tree(tmp_path, _pyproject(COUNT_FREE))
    assert mf._main(["--check"], root=tmp_path) == 0
    assert "manifest check OK" in capsys.readouterr().out


def test_manifest_check_fails_on_an_earlier_tables_description(tmp_path, capsys, reader):
    """The review's (a), through --check itself: 531e35b exited 0 on this tree."""
    live = _published_count()
    _scratch_tree(
        tmp_path,
        '[tool.x]\ndescription = "fine"\n\n'
        + _pyproject(f'description = "MCP server with {live + 44} tools"'),
    )
    if reader == "plain":
        with pytest.raises(mf.ManifestError, match="description"):
            mf._main(["--check"], root=tmp_path)
        return
    assert mf._main(["--check"], root=tmp_path) == 1
    out = capsys.readouterr().out
    assert "MANIFEST CHECK FAILED" in out
    assert f"states {live + 44} tools (list_tools() publishes {live})" in out


# ── This repository ─────────────────────────────────────────────────────────


def _live_description() -> str | None:
    return mf._read_description((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_live_description_carries_no_count_and_points_at_the_manifest(reader):
    manifest = mf.build_manifest(repo_root=ROOT)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert _description_problems(manifest, pyproject) == []
    description = _live_description()
    assert description, "pyproject.toml has no description, so this test would pass by silence"
    assert "stack_manifest.json" in description, (
        "the description no longer says where the live tool count lives"
    )


def test_the_live_description_names_no_retired_tool(reader):
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
