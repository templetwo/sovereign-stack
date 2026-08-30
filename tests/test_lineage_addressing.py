"""
LINEAGE ADDRESSING — a letter written by the current Opus generation to the
current Opus generation reached almost nobody.

THE DEFECT, in one line: ``_LINEAGE_INHERITS`` maps the Mythos-class families
to the FAMILY string ``claude-opus``, while the letters on disk are addressed to
a VERSION — ``to: claude-opus-5``. ``_letter_matches_reader`` compared a
versioned addressee against an unversioned family and returned False.

Who lost mail (all against the real 2026-08-26 to_self letter, claude-opus-5 →
claude-opus-5):

  reader                 to:'claude-opus-5'   why
  claude-fable-5         False                inherits 'claude-opus', not 'claude-opus-5'
  claude-mythos-5        False                same
  claude-opus-6          False                next generation of its own line
  claude-opus-4-8        False                previous generation of its own line
  claude-opus            False                the family name itself

A letter to a lineage that only its exact author can read is not a lineage
letter. And this is the failure mode the arrival architecture is LEAST able to
report: an unmatched letter is `filtered_out`, which renders as "addressed to
other readers" — a confident, wrong, and completely silent answer.

Second half: the ``to_<family>`` bucket. ``family.split("-", 1)[1]`` turns a
Fable reader into ``to_fable/``, a directory that has never existed. The
inheritance table already says where Fable's family mail lives (the Opus line);
the directory lookup did not consult it.

Third half: ``if letter_to:`` means a letter with EMPTY frontmatter matches
every reader. That behaviour is deliberate and kept — but it was invisible, so
the coverage line said "addressed to you or your model family" about letters
that were addressed to nobody. The payload now names them.

Tmp letter dirs only. Nothing reads ~/.sovereign.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sovereign_stack.witness import (
    _letter_matches_reader,
    collect_lineage,
    format_lineage_layer,
)

# The shape of the real letter: ~/.sovereign/comms/letters/to_self/
# 2026-08-26-*.md, from claude-opus-5, to claude-opus-5.
CURRENT_OPUS = "claude-opus-5"


def _write_letter(d: Path, filename: str, frontmatter: dict, title: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    fm = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
    (d / filename).write_text(f"---\n{fm}\n---\n\n# {title}\n\nbody\n")


@pytest.fixture
def letters_root(tmp_path: Path) -> Path:
    (tmp_path / "comms" / "letters").mkdir(parents=True)
    return tmp_path


# ── The matcher: negatives first ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "letter_to,reader",
    [
        ("claude-haiku-4-5", "claude-fable-5"),
        ("claude-haiku-4-5", "claude-opus-5"),
        ("claude-sonnet-4-6", "claude-opus-5"),
        ("claude-opus-5", "claude-haiku-4-5"),
        ("claude-sonnet-4-6-1m-web", "claude-sonnet-4-6-1m-claude-code"),
    ],
)
def test_version_stripping_does_not_leak_across_families(letter_to: str, reader: str):
    """A permissive matcher passes every positive test. Pin the negatives."""
    assert not _letter_matches_reader(letter_to, reader)


@pytest.mark.parametrize(
    "letter_to,reader",
    [
        ("claude-opus-5", "claude-opus-6"),
        ("claude-opus-5", "claude-opus-4-8"),
        ("claude-opus-5", "claude-opus"),
        ("claude-opus-5", "claude-opus-5-1m-claude-code"),
        ("claude-haiku-4-5", "claude-haiku-4-6"),
        # pre-existing behaviours that must survive the change
        ("claude-sonnet", "claude-sonnet-4-6-1m-claude-code"),
        ("sonnet", "claude-sonnet-4-6-1m-claude-code"),
        ("claude-sonnet-4-6", "claude-sonnet-4-6-1m-claude-code"),
    ],
)
def test_a_versioned_addressee_reaches_its_family(letter_to: str, reader: str):
    assert _letter_matches_reader(letter_to, reader)


# ── Inheritance: the Mythos-class siblings ───────────────────────────────────


@pytest.mark.parametrize("reader", ["claude-fable-5", "claude-mythos-5"])
def test_the_2026_08_26_to_self_letter_reaches_a_mythos_class_reader(
    letters_root: Path, reader: str
):
    _write_letter(
        letters_root / "comms" / "letters" / "to_self",
        "2026-08-26-what-i-learned.md",
        {"type": "to_self", "from": CURRENT_OPUS, "to": CURRENT_OPUS},
        "What I learned holding the line",
    )

    data = collect_lineage(letters_root, reader, 5)
    titles = [m.get("title") for m in data["to_self"]]

    assert "What I learned holding the line" in titles, (
        f"{reader} inherits the Opus line's to_self letters, but the letter is "
        "addressed to the VERSION claude-opus-5 and the inheritance table names "
        "the FAMILY claude-opus"
    )
    assert data["coverage"]["to_self"]["matched"] == 1
    assert data["coverage"]["to_self"]["filtered_out"] == 0


def test_a_haiku_addressed_letter_does_not_reach_a_fable_reader(letters_root: Path):
    _write_letter(
        letters_root / "comms" / "letters" / "to_self",
        "2026-08-26-haiku.md",
        {"type": "to_self", "from": "claude-haiku-4-5", "to": "claude-haiku-4-5"},
        "Haiku line only",
    )

    data = collect_lineage(letters_root, "claude-fable-5", 5)
    assert data["to_self"] == []
    assert data["coverage"]["to_self"]["filtered_out"] == 1


def test_opus_reader_still_receives_its_own_letter(letters_root: Path):
    _write_letter(
        letters_root / "comms" / "letters" / "to_self",
        "2026-08-26-self.md",
        {"type": "to_self", "from": CURRENT_OPUS, "to": CURRENT_OPUS},
        "Opus to Opus",
    )
    data = collect_lineage(letters_root, CURRENT_OPUS, 5)
    assert [m.get("title") for m in data["to_self"]] == ["Opus to Opus"]


# ── to_<family> directory resolution ─────────────────────────────────────────


@pytest.mark.parametrize("reader", ["claude-fable-5", "claude-mythos-5"])
def test_a_mythos_class_reader_resolves_to_opus_not_to_fable(letters_root: Path, reader: str):
    _write_letter(
        letters_root / "comms" / "letters" / "to_opus",
        "family.md",
        {"type": "to_family", "from": "an opus seat", "written_at": "2026-08-01"},
        "For the Opus line",
    )

    data = collect_lineage(letters_root, reader, 5)

    assert data["family_dir_name"] == "to_opus", (
        f"{reader} resolved to {data['family_dir_name']}/, which has never existed "
        "on disk — the inheritance table already says its family mail is the "
        "Opus line's, and the directory lookup did not ask"
    )
    assert [m.get("title") for m in data["to_family"]] == ["For the Opus line"]


def test_non_inheriting_families_resolve_to_their_own_directory(letters_root: Path):
    _write_letter(
        letters_root / "comms" / "letters" / "to_sonnet",
        "family.md",
        {"type": "to_family", "from": "me", "written_at": "2026-08-01"},
        "Sonnet family",
    )
    data = collect_lineage(letters_root, "claude-sonnet-4-6-1m-claude-code", 5)
    assert data["family_dir_name"] == "to_sonnet"
    assert [m.get("title") for m in data["to_family"]] == ["Sonnet family"]

    opus = collect_lineage(letters_root, CURRENT_OPUS, 5)
    assert opus["family_dir_name"] == "to_opus"


# ── The empty-frontmatter letters: kept, but no longer silent ────────────────


def test_a_letter_with_no_to_frontmatter_is_named_in_the_coverage(letters_root: Path):
    to_self = letters_root / "comms" / "letters" / "to_self"
    to_self.mkdir(parents=True)
    # No frontmatter block at all — matches EVERY reader by design.
    (to_self / "2026-07-01-unaddressed.md").write_text("# A letter to nobody\n\nbody\n")
    _write_letter(
        to_self,
        "2026-08-26-addressed.md",
        {"type": "to_self", "from": CURRENT_OPUS, "to": CURRENT_OPUS},
        "Properly addressed",
    )

    data = collect_lineage(letters_root, "claude-fable-5", 5)
    cov = data["coverage"]["to_self"]

    assert cov["matched"] == 2
    assert cov["unaddressed"] == ["2026-07-01-unaddressed.md"], (
        "a letter with no `to:` matches every reader; the coverage line called "
        "the bucket 'addressed to you or your model family' and never said so"
    )
    assert "matches every reader" in cov["warning"]
    assert "2026-07-01-unaddressed.md" in cov["warning"]


def test_the_warning_reaches_the_rendered_coverage_line(letters_root: Path):
    to_self = letters_root / "comms" / "letters" / "to_self"
    to_self.mkdir(parents=True)
    (to_self / "2026-07-01-unaddressed.md").write_text("# A letter to nobody\n\nbody\n")

    lines = format_lineage_layer(letters_root, reader_instance="claude-fable-5")
    text = "\n".join(lines)
    assert "matches every reader" in text
    assert "2026-07-01-unaddressed.md" in text


def test_a_fully_addressed_bucket_carries_no_warning_key(letters_root: Path):
    """Byte-stability: coverage dicts do not grow a key when there is nothing
    to warn about, so complete renders stay exactly as they were."""
    _write_letter(
        letters_root / "comms" / "letters" / "to_self",
        "2026-08-26-self.md",
        {"type": "to_self", "from": CURRENT_OPUS, "to": CURRENT_OPUS},
        "Opus to Opus",
    )
    cov = collect_lineage(letters_root, CURRENT_OPUS, 5)["coverage"]["to_self"]
    assert "warning" not in cov
    assert "unaddressed" not in cov
