"""Descriptions match the code: the census that keeps them honest (design B4).

R4's D15, measured 2026-10-21: tool descriptions, docstrings, the aperture and
the repo CLAUDE.md are edited by hand after behaviour changes, and nothing
checked them against the code. The connector served the boot door's schema
that night still saying handoffs "flip to consumed=true", 51 days after the
signature ledger (2026-08-31) made reading additive: reading signs a handoff
for you alone and hides it from nobody; only retire() clears it for everyone.
A stale description is a read-side fail-open. The caller is told about a side
effect that no longer exists, or is not told about one that does.

THE RULE, applied to every published tool's description and to every
description string inside its input schema:

  * none narrates consumption: the words consume, consumes, consumed,
    consuming and unconsumed, with snake_case split into words, so
    ``consumed_by`` and ``consumed_at`` count; and
  * none names a retired tool (``server.RETIRED_TOOLS``);

  EXCEPT inside a sentence that says ``legacy`` or ``retired``. That is how a
  live description points a caller from a dead name or field to the live one
  ("replaces nape_honks and nape_honks_with_history (retired 2026-09-06)").

Readings of the brief, stated so a reviewer can dispute each one by name:

  * "Every registered tool" is the PUBLISHED surface, ``list_tools()``. A
    retired tool's own description is served to no one: the registry minus
    the published set is exactly ``RETIRED_TOOLS`` (pinned by
    test_tool_retirement.py's test_the_surface_is_52), and un-retiring a tool
    puts its text back under this census (pinned here).
  * The legacy/retired exception covers both halves of the rule. Under the
    other reading the migration pointers that say "(retired 2026-09-06)"
    would have to lose the name a seat still carries in memory.
  * Four retired names are also English words (derive, govern, metabolize,
    route), and two of them are live ``my_toolkit`` intents. Only a
    call-shaped mention (the name followed by "(") names one of those tools.
  * "consumes" and "consuming" join the brief's three words. They are the
    same verb, and a census that a change of tense slips past is not a check.
  * A sentence ends at ".", "!" or "?" followed by whitespace, or at a line
    break. A semicolon does not end one.

The same scanner also pins two surfaces the brief names that are not tool
descriptions, the aperture's ``widen_with`` calls and the handoff module's
docstring, and the refusal ``handoff(supersedes=...)`` raises, which pointed a
caller at the same retired tool the aperture did.

AND THE REPO CLAUDE.md: its first ``**Version:**`` line names pyproject's
version, once and with no other version beside it, and the live published-tool
count. It said 1.12.0 and 94 tools while pyproject said 1.21.0 and the server
published 53, so both numbers are held.

Every rule here ships with a demonstrated-fail control (law 10), and the
coverage tests fail if the walk reads nothing.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sovereign_stack import manifest as mf
from sovereign_stack import server

ROOT = Path(__file__).resolve().parents[1]

CONSUMPTION_WORDS = frozenset({"consume", "consumes", "consumed", "consuming", "unconsumed"})
EXCEPTION_WORDS = frozenset({"legacy", "retired"})

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
# Lower-case letter runs: "consumed_by" is the words "consumed" and "by", and
# "legacy_unconsumed" is "legacy" and "unconsumed".
_WORD = re.compile(r"[a-z]+")
_CALL = re.compile(r"(?<![A-Za-z0-9_.])([a-z][a-z0-9_]*)\(")


def _names_tool(sentence: str, name: str) -> bool:
    """True when ``sentence`` mentions the tool ``name``.

    A snake_case name is matched where it stands as a whole identifier, so
    ``resolve_thread`` is not found inside ``resolve_thread_by_id``. A one-word
    name is an English word too ("route through a seat", "re-derive the id"),
    so only a call, ``name(``, counts as naming the tool.
    """
    if "_" in name:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
    else:
        pattern = rf"(?<![A-Za-z0-9_.]){re.escape(name)}\("
    return re.search(pattern, sentence) is not None


def stale_sentences(text: str, retired: set[str] | frozenset[str]) -> list[tuple[str, list[str]]]:
    """Each sentence of ``text`` that breaks the rule, with what it hit."""
    found: list[tuple[str, list[str]]] = []
    for sentence in _SENTENCE_BREAK.split(text or ""):
        if not sentence.strip():
            continue
        words = set(_WORD.findall(sentence.lower()))
        if words & EXCEPTION_WORDS:
            continue
        hits = sorted(words & CONSUMPTION_WORDS)
        hits += sorted(name for name in retired if _names_tool(sentence, name))
        if hits:
            found.append((sentence.strip(), hits))
    return found


def _published_tools() -> list:
    return asyncio.run(server.list_tools())


def _description_texts(tool) -> list[tuple[str, str]]:
    """(path, text) for the tool's description and every description in its schema."""
    found = [("description", tool.description or "")]

    def walk(node, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    found.append((".".join((*path, key)), value))
                else:
                    walk(value, (*path, str(key)))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, (*path, str(index)))

    walk(tool.inputSchema or {}, ("inputSchema",))
    return found


def _census() -> list[tuple[str, str, str]]:
    """(tool, path, text) for every description string the server publishes."""
    return [
        (tool.name, path, text)
        for tool in _published_tools()
        for path, text in _description_texts(tool)
    ]


RETIRED = frozenset(server.RETIRED_TOOLS)


def _format_offenders(offenders: list[tuple[str, str, str, list[str]]]) -> str:
    return "\n".join(
        f"  {tool} {path}: {hits}\n      {sentence!r}" for tool, path, sentence, hits in offenders
    )


# ── The census ──────────────────────────────────────────────────────────────


def test_no_published_description_narrates_consumption_or_names_a_retired_tool():
    offenders = [
        (tool, path, sentence, hits)
        for tool, path, text in _census()
        for sentence, hits in stale_sentences(text, RETIRED)
    ]
    assert offenders == [], (
        "these published descriptions say what the code no longer does (the ledger: "
        "reading signs it for you alone; only retire() clears it for everyone), or point "
        "a caller at a retired tool outside a sentence that says legacy or retired:\n"
        + _format_offenders(offenders)
    )


def test_the_census_reads_every_published_tool_and_the_door_parameters():
    """Coverage. An empty or shallow walk would pass the census by reading nothing."""
    texts = _census()
    published = {tool.name for tool in _published_tools()}
    assert len(published) > 0
    assert {tool for tool, _, _ in texts} == published
    paths = {(tool, path) for tool, path, _ in texts}
    for expected in (
        ("where_did_i_leave_off", "description"),
        ("where_did_i_leave_off", "inputSchema.properties.consume.description"),
        ("where_did_i_leave_off", "inputSchema.properties.source_instance.description"),
        ("nape_ack", "description"),
        ("nape_ack", "inputSchema.properties.honk_id.description"),
        ("handoff", "inputSchema.properties.supersedes.description"),
    ):
        assert expected in paths, f"the census did not read {expected}"


def test_the_retired_names_are_real_and_off_the_menu():
    """The retired-name half is vacuous if RETIRED_TOOLS is empty or still published."""
    published = {tool.name for tool in _published_tools()}
    assert len(RETIRED) > 0
    assert RETIRED.isdisjoint(published)


def test_un_retiring_a_tool_puts_its_text_under_the_census(monkeypatch):
    """Reading 1 as a test: the census reads list_tools(), the published surface.

    _registered_tools() also holds the 48 retired schemas, and no surface serves
    their text. Un-retiring is deleting one RETIRED_TOOLS entry, and the census
    then reads the tool's text. Shown on handoff_archaeology, whose description
    still says boot "consumed" the handoffs. A patched copy of the map, not
    delitem, so the real dict keeps its order for every later test.
    """
    name = "handoff_archaeology"
    assert name not in {tool.name for tool in _published_tools()}
    unretired = {key: entry for key, entry in server.RETIRED_TOOLS.items() if key != name}
    monkeypatch.setattr(server, "RETIRED_TOOLS", unretired)

    read = [text for tool, _, text in _census() if tool == name]
    assert read, f"un-retired, {name} is published, so the census must read its text"
    flagged = [hit for text in read for hit in stale_sentences(text, frozenset(unretired))]
    assert flagged, f"{name}'s text narrates consumption, so the census must flag it"


# ── The scanner can fail (law 10) ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "hits"),
    [
        ("Handoffs flip to consumed=true after this call.", ["consumed"]),
        ("Which instance is reading (recorded as consumed_by for audit).", ["consumed"]),
        ("Returns unconsumed handoffs from previous instances.", ["unconsumed"]),
        ("Does NOT consume handoffs.", ["consume"]),
        ("The boot consumes each handoff it shows.", ["consumes"]),
        ("Its filename, with or without .json (see handoff_archaeology).", ["handoff_archaeology"]),
        ("honk_id from nape_honks output.", ["nape_honks"]),
        ("Call govern(target) before acting.", ["govern"]),
    ],
)
def test_the_scanner_catches_a_stale_sentence(text, hits):
    assert stale_sentences(text, RETIRED) == [(text, hits)]


@pytest.mark.parametrize(
    "text",
    [
        "Every correction had to route through a seat with filesystem access to re-derive the id.",
        "A route telling you which arrival door to call next.",
        "The governance circuit computes a derived claim_id.",
        "legacy_unconsumed counts the pre-ledger field; it is nobody's queue.",
        "signals_summary(mode='list', source='honk') replaces nape_honks and "
        "nape_honks_with_history (retired 2026-09-06).",
        "Resolve it with resolve_thread_by_id.",
        "Reading signs it for you alone; only an explicit retire() clears it for everyone.",
    ],
)
def test_the_scanner_passes_a_live_sentence(text):
    assert stale_sentences(text, RETIRED) == []


def test_the_exception_is_per_sentence_not_per_description():
    """One sentence saying 'legacy' must not excuse its neighbour."""
    text = (
        "The parameter keeps its legacy name, consume. "
        "Handoffs flip to consumed=true after this call."
    )
    assert stale_sentences(text, RETIRED) == [
        ("Handoffs flip to consumed=true after this call.", ["consumed"])
    ]


# ── Surfaces the brief names that are not tool descriptions ─────────────────


def test_every_aperture_widen_with_calls_a_published_tool(tmp_sovereign_root):
    """The aperture tells every arriving seat which call widens each surface.

    It named handoff_archaeology (retired 2026-09-06), so a seat that followed
    it was refused. Read both reader shapes: the handoffs surface differs.
    """
    from sovereign_stack.aperture import measure_aperture

    published = {tool.name for tool in _published_tools()}
    for reader in (None, "census-reader-seat"):
        aperture = measure_aperture(
            datetime.now(timezone.utc), root=tmp_sovereign_root, reader=reader
        )
        widens = {
            name: surface["widen_with"]
            for name, surface in aperture["surfaces"].items()
            if "widen_with" in surface
        }
        assert "handoffs" in widens, "the census did not reach the handoffs surface"
        for surface, text in widens.items():
            assert stale_sentences(text, RETIRED) == [], (surface, text)
            unpublished = [call for call in _CALL.findall(text) if call not in published]
            assert unpublished == [], (surface, text)


def test_the_handoff_module_docstring_describes_the_ledger():
    from sovereign_stack import handoff

    assert stale_sentences(handoff.__doc__ or "", RETIRED) == []


def test_the_supersedes_refusal_points_at_no_retired_tool(tmp_path):
    from sovereign_stack.handoff import HandoffEngine

    engine = HandoffEngine(root=str(tmp_path))
    with pytest.raises(ValueError, match="does not name a handoff in this store") as refused:
        engine.resolve_handoff_id("20990101T000000_nobody_general")
    # The store path is the test's own tmp directory, whose name could carry
    # either exception word; take it out before scanning.
    message = str(refused.value).replace(str(engine.root), "<store>")
    assert stale_sentences(message, RETIRED) == [], message


# ── The repo CLAUDE.md header ───────────────────────────────────────────────

_HEADER_VERSION = re.compile(r"\*\*Version:\*\*\s*v?(\d+(?:\.\d+)+)")
_HEADER_COUNT = re.compile(r"(?<![\d,.])(\d+) published tools\b")


def _header_line(claude_md: str) -> str | None:
    """The first line that opens with **Version:**, or None."""
    for line in claude_md.splitlines():
        if line.startswith("**Version:**"):
            return line
    return None


def header_version(claude_md: str) -> str | None:
    line = _header_line(claude_md)
    match = _HEADER_VERSION.match(line) if line else None
    return match.group(1) if match else None


_VERSION_TOKEN = re.compile(r"(?<![\d.])\d+(?:\.\d+)+")


def header_versions(claude_md: str) -> list[str]:
    """Every version-shaped token (dotted numbers) on the header line, in order."""
    line = _header_line(claude_md)
    return _VERSION_TOKEN.findall(line) if line else []


def header_published_count(claude_md: str) -> int | None:
    line = _header_line(claude_md)
    match = _HEADER_COUNT.search(line) if line else None
    return int(match.group(1)) if match else None


def _pyproject_version() -> str:
    match = mf._VERSION_RE.search((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert match, "pyproject.toml names no version"
    return match.group(1)


def _repo_claude_md() -> str:
    return (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_the_repo_claude_md_header_names_the_pyproject_version():
    stated = header_version(_repo_claude_md())
    assert stated == _pyproject_version(), (
        f"CLAUDE.md's first **Version:** line says {stated!r}; "
        f"pyproject.toml says {_pyproject_version()!r}"
    )


def test_the_repo_claude_md_header_names_no_other_version():
    """The header carries pyproject's version once and no other version.

    The line once also said "1.21.0 was released 2026-08-28, and this tree
    carries unreleased work since". A release bump that moved only the first
    number would have left that prose naming the old version, unchecked. With
    one token on the line the bump is one edit, and this refuses a leftover.
    """
    assert header_versions(_repo_claude_md()) == [_pyproject_version()]


def test_the_repo_claude_md_header_states_the_live_published_tool_count():
    stated = header_published_count(_repo_claude_md())
    live = len(_published_tools())
    assert stated == live, (
        f"CLAUDE.md's first **Version:** line states {stated!r} published tools; "
        f"list_tools() publishes {live}"
    )


@pytest.mark.parametrize(
    ("text", "version", "count"),
    [
        ("# Title\n\nno header here\n", None, None),
        ("**Version:** 9.9.9 (somewhen) · 7 published tools\n", "9.9.9", 7),
        ("**Version:** 1.2.3 (somewhen) with 94 tools\n", "1.2.3", None),
        ("Version: 1.2.3 · 7 published tools\n", None, None),
        (
            "**Version:** 1.2.3 · 7 published tools\n**Version:** 4.5.6 · 8 published tools\n",
            "1.2.3",
            7,
        ),
        ("**History:** **Version:** 1.0.0\n**Version:** 2.0.0 · 3 published tools\n", "2.0.0", 3),
    ],
)
def test_the_header_reader_can_fail(text, version, count):
    """A missing or reworded header reads as None, never as agreement."""
    assert header_version(text) == version
    assert header_published_count(text) == count


@pytest.mark.parametrize(
    ("text", "versions"),
    [
        ("**Version:** 1.22.0 · 53 published tools\n", ["1.22.0"]),
        (
            "**Version:** 1.22.0 (1.21.0 was released 2026-08-28) · 53 published tools\n",
            ["1.22.0", "1.21.0"],
        ),
        ("**Version:** v1.2.3 (since 1.2.3.) · 7 published tools\n", ["1.2.3", "1.2.3"]),
        ("**Version:** 1.2.3 (Python 3.10) · 7 published tools\n", ["1.2.3", "3.10"]),
        ("# Title\n\nno header here\n", []),
    ],
)
def test_header_versions_sees_a_leftover_version(text, versions):
    """A second version on the line is seen, a repeat included; a date is not one."""
    assert header_versions(text) == versions
