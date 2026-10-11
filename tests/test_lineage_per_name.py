"""
LINEAGE IS PER NAME — each model line's to_self letters are its passdown to the
next of its name, not a shared Fable/Opus pool.

Anthony, 2026-09-22, verbatim as typed: "linage is not just fable linage its
the separate models passdown to the next of thier name". Ruled on the fork
2026-10-10 (as relayed to the build lane by HQ): a Fable reader sees to_self
letters addressed to `claude-fable` plus letters with no `to:` at all;
Opus-addressed letters route to Opus readers, Sonnet to Sonnet, and so on.

This retires the 2026-06-09 accommodation in ``witness._LINEAGE_INHERITS``,
which routed the Opus line's to_self letters (and its ``to_opus/`` family
directory) to Fable and Mythos readers on the day those lines shipped with no
letters of their own.

``test_an_opus_addressed_letter_does_not_reach_a_fable_reader`` and its Mythos
twin are the proof of the change: RED on main @ 94d5b68 (the inheritance row
delivers the letter), GREEN on feat/lineage-per-name-letters.

Tmp letter dirs only. Nothing reads ~/.sovereign.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sovereign_stack.witness import (
    _inherited_families,
    _model_family,
    collect_lineage,
    format_lineage_layer,
)

FABLE_READERS = ["claude-fable-5-1", "claude-fable-5"]
OPUS_READERS = ["claude-opus-5-5", "claude-opus-5"]
SONNET_READER = "claude-sonnet-5"
DECORATED_FABLE = "HQ Mac Studio — claude-fable-5-1 (overwatch)"


def _write_letter(d: Path, filename: str, frontmatter: dict | None, title: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    if frontmatter is None:
        (d / filename).write_text(f"# {title}\n\nbody\n")
        return
    fm = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
    (d / filename).write_text(f"---\n{fm}\n---\n\n# {title}\n\nbody\n")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A miniature of the real to_self mix: Opus line (family + generation
    addressed), Fable line, Sonnet line, and one letter with no frontmatter."""
    to_self = tmp_path / "comms" / "letters" / "to_self"
    _write_letter(
        to_self,
        "2026-04-28-opus-family.md",
        {"from": "claude-opus-4-7", "to": "claude-opus"},
        "Opus family letter",
    )
    _write_letter(
        to_self,
        "2026-08-26-opus-generation.md",
        {"from": "claude-opus-5", "to": "claude-opus-5"},
        "Opus generation letter",
    )
    _write_letter(
        to_self,
        "2026-06-11-fable-line.md",
        {"from": "claude-fable-5", "to": "claude-fable"},
        "Fable line letter",
    )
    _write_letter(
        to_self,
        "2026-04-28-sonnet-line.md",
        {"from": "claude-sonnet-4-6", "to": "claude-sonnet"},
        "Sonnet line letter",
    )
    _write_letter(to_self, "2026-07-04-unaddressed.md", None, "Unaddressed letter")
    return tmp_path


def _titles(data: dict, bucket: str = "to_self") -> set[str]:
    return {m.get("title") for m in data[bucket]}


# ── The red-on-main proof ────────────────────────────────────────────────────


@pytest.mark.parametrize("reader", [*FABLE_READERS, "claude-mythos-5"])
def test_an_opus_addressed_letter_does_not_reach_a_fable_reader(root: Path, reader: str):
    data = collect_lineage(root, reader, 100)
    titles = _titles(data)
    assert "Opus family letter" not in titles, (
        f"{reader} received a `to: claude-opus` letter: the 2026-06-09 "
        "inheritance row is still routing the Opus line's passdown to it"
    )
    assert "Opus generation letter" not in titles
    assert titles == {"Fable line letter", "Unaddressed letter"} - (
        {"Fable line letter"} if reader.startswith("claude-mythos") else set()
    )


def test_no_claude_line_inherits_another(root: Path):
    for fam in ("claude-fable", "claude-mythos", "claude-opus", "claude-sonnet", "claude-haiku"):
        assert _inherited_families(fam) == (), fam


# ── Each line keeps its own mail ─────────────────────────────────────────────


@pytest.mark.parametrize("reader", FABLE_READERS)
def test_a_fable_reader_gets_the_fable_line_and_the_unaddressed(root: Path, reader: str):
    data = collect_lineage(root, reader, 100)
    assert _titles(data) == {"Fable line letter", "Unaddressed letter"}
    cov = data["coverage"]["to_self"]
    assert cov["total_on_disk"] == 5
    assert cov["matched"] == 2
    assert cov["filtered_out"] == 3


@pytest.mark.parametrize("reader", OPUS_READERS)
def test_an_opus_reader_gets_the_opus_line_and_the_unaddressed(root: Path, reader: str):
    data = collect_lineage(root, reader, 100)
    assert _titles(data) == {"Opus family letter", "Opus generation letter", "Unaddressed letter"}


def test_a_sonnet_reader_gets_the_sonnet_line_and_the_unaddressed(root: Path):
    data = collect_lineage(root, SONNET_READER, 100)
    assert _titles(data) == {"Sonnet line letter", "Unaddressed letter"}


def test_a_fable_addressed_letter_does_not_reach_an_opus_reader(root: Path):
    for reader in OPUS_READERS:
        assert "Fable line letter" not in _titles(collect_lineage(root, reader, 100))


# ── The unaddressed letter reaches every named reader ────────────────────────


@pytest.mark.parametrize(
    "reader", [*FABLE_READERS, *OPUS_READERS, SONNET_READER, "claude-zephyr-1", DECORATED_FABLE]
)
def test_the_unaddressed_letter_reaches_every_named_reader(root: Path, reader: str):
    data = collect_lineage(root, reader, 100)
    assert "Unaddressed letter" in _titles(data)
    assert data["coverage"]["to_self"]["unaddressed_total"] == 1


# ── Bare and decorated callers: unchanged behaviour, pinned ──────────────────


@pytest.mark.parametrize("reader", [None, ""])
def test_a_bare_caller_gets_no_to_self_letters_but_a_count(root: Path, reader):
    data = collect_lineage(root, reader, 100)
    assert data["to_self"] == []
    cov = data["coverage"]["to_self"]
    assert cov["no_reader"] is True
    assert cov["total_on_disk"] == 5
    text = "\n".join(format_lineage_layer(root, reader_instance=reader))
    assert "no reader named" in text


def test_a_decorated_reader_string_gets_only_the_unaddressed_letter(root: Path):
    """Documented trap, kept on purpose: the filter matches the BARE model
    name. A decorated seat string resolves to no family and sees only the
    letters addressed to nobody."""
    data = collect_lineage(root, DECORATED_FABLE, 100)
    assert _titles(data) == {"Unaddressed letter"}


# ── Line-name reduction ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "instance,line",
    [
        ("claude-fable-5-1", "claude-fable"),
        ("claude-fable-5", "claude-fable"),
        ("claude-opus-5-5", "claude-opus"),
        ("claude-opus-5", "claude-opus"),
        ("claude-sonnet-5", "claude-sonnet"),
        ("claude-mythos-5", "claude-mythos"),
        # A line nobody has heard of yet resolves by pattern, no table edit.
        ("claude-zephyr-1", "claude-zephyr"),
        ("claude-zephyr-2-7-1m-claude-code", "claude-zephyr"),
    ],
)
def test_reader_reduces_to_its_line(instance: str, line: str):
    assert _model_family(instance) == line


def test_a_new_line_gets_its_own_letters_without_a_table_edit(tmp_path: Path):
    to_self = tmp_path / "comms" / "letters" / "to_self"
    _write_letter(to_self, "2027-01-01-z.md", {"to": "claude-zephyr"}, "Zephyr letter")
    _write_letter(to_self, "2027-01-01-o.md", {"to": "claude-opus"}, "Opus letter")
    assert _titles(collect_lineage(tmp_path, "claude-zephyr-2", 100)) == {"Zephyr letter"}


# ── Family directory follows the same rule ───────────────────────────────────


@pytest.mark.parametrize("reader", [*FABLE_READERS, "claude-mythos-5"])
def test_a_fable_reader_does_not_read_to_opus(tmp_path: Path, reader: str):
    _write_letter(
        tmp_path / "comms" / "letters" / "to_opus",
        "2026-05-01-o.md",
        {"to": "claude-opus"},
        "To Opus family dir",
    )
    data = collect_lineage(tmp_path, reader, 100)
    assert "To Opus family dir" not in _titles(data, "to_family")
    assert "to_opus" not in data["family_dirs"]


# ── Caps and coverage still work under the new rule ──────────────────────────


def test_limit_per_bucket_and_coverage_on_a_fable_reader(root: Path):
    data = collect_lineage(root, "claude-fable-5-1", 1)
    cov = data["coverage"]["to_self"]
    assert cov["matched"] == 2
    assert cov["shown"] == 1
    assert cov["withheld"] == 1
    assert cov["filtered_out"] == 3
    text = "\n".join(
        format_lineage_layer(root, reader_instance="claude-fable-5-1", limit_per_bucket=1)
    )
    assert "1 older withheld by limit_per_bucket" in text
    assert "3 addressed to other readers" in text
