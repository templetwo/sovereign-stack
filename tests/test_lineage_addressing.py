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
def test_a_mythos_class_reader_reads_its_ancestors_family_directory(
    letters_root: Path, reader: str
):
    """`to_opus/` is family mail for the Mythos-class siblings: the inheritance
    table already says so, and the directory lookup used not to ask."""
    _write_letter(
        letters_root / "comms" / "letters" / "to_opus",
        "family.md",
        {"type": "to_family", "from": "an opus seat", "written_at": "2026-08-01"},
        "For the Opus line",
    )

    data = collect_lineage(letters_root, reader, 5)

    assert data["family_dirs"] == ("to_opus",), (
        f"{reader} did not read to_opus/ — the inheritance table says its family "
        "mail is the Opus line's, and the directory lookup did not ask"
    )
    assert [m.get("title") for m in data["to_family"]] == ["For the Opus line"]


# ── UNION, NOT REDIRECT ──────────────────────────────────────────────────────
#
# THE TEST DIRECTLY ABOVE USED TO ASSERT `family_dir_name == "to_opus"` AND
# NOTHING ELSE, and it passed on a `_collect(to_opus)` that never looked at
# `to_fable/`. Because `to_fable/` does not exist in that fixture — or on disk
# today — the redirect and the union are indistinguishable there. They are not
# indistinguishable the moment somebody writes the letter: a redirect makes
# `to_fable/` and `to_mythos/` unreachable to EVERY reader, since no other
# family maps to those short names. A reachability fix that mints a new
# write-only address is the branch's own defect wearing the cure's clothes.


@pytest.mark.parametrize(
    ("reader", "own_dir"),
    [("claude-fable-5", "to_fable"), ("claude-mythos-5", "to_mythos")],
)
def test_an_inheriting_reader_reads_its_own_directory_too(
    letters_root: Path, reader: str, own_dir: str
):
    letters = letters_root / "comms" / "letters"
    _write_letter(
        letters / own_dir,
        "own.md",
        {"type": "to_family", "from": "a sibling", "written_at": "2026-08-29"},
        f"For {own_dir}",
    )
    _write_letter(
        letters / "to_opus",
        "ancestor.md",
        {"type": "to_family", "from": "an opus seat", "written_at": "2026-08-01"},
        "For the Opus line",
    )

    data = collect_lineage(letters_root, reader, 5)

    titles = [m.get("title") for m in data["to_family"]]
    assert f"For {own_dir}" in titles, (
        f"a letter in {own_dir}/ reached nobody: the reader was redirected to its "
        "ancestor's directory instead of also reading its own, so that directory "
        "is write-only and silent about it"
    )
    assert "For the Opus line" in titles
    assert data["family_dirs"] == (own_dir, "to_opus")
    assert data["coverage"]["to_family"]["total_on_disk"] == 2


def test_the_merged_family_bucket_names_every_directory_it_read(letters_root: Path):
    letters = letters_root / "comms" / "letters"
    _write_letter(
        letters / "to_fable",
        "own.md",
        {"type": "to_family", "from": "a sibling", "written_at": "2026-08-29"},
        "For to_fable",
    )
    _write_letter(
        letters / "to_opus",
        "ancestor.md",
        {"type": "to_family", "from": "an opus seat", "written_at": "2026-08-01"},
        "For the Opus line",
    )

    text = "\n".join(format_lineage_layer(letters_root, reader_instance="claude-fable-5"))

    assert "to_fable/ + to_opus/" in text, (
        "the header named one of the two directories it read — the same lie the "
        "union closes, one layer over"
    )
    assert "written for fable and opus instances" in text
    assert collect_lineage(letters_root, "claude-fable-5", 5)["coverage"]["to_family"]["dirs"] == [
        "to_fable",
        "to_opus",
    ]


def test_the_merged_family_bucket_honours_ONE_cap(letters_root: Path):
    """A bucket assembled from two directories must still be capped ONCE.
    Collecting each directory at limit_per_bucket and concatenating hands an
    inheriting reader 2x the cap on the door whose purpose is bounding the
    payload for an input-gated seat."""
    letters = letters_root / "comms" / "letters"
    for i in range(4):
        _write_letter(
            letters / "to_fable",
            f"2026-08-2{i}-own.md",
            {"type": "to_family", "from": "a sibling", "written_at": f"2026-08-2{i}"},
            f"own {i}",
        )
        _write_letter(
            letters / "to_opus",
            f"2026-08-1{i}-anc.md",
            {"type": "to_family", "from": "an opus seat", "written_at": f"2026-08-1{i}"},
            f"ancestor {i}",
        )

    for cap in (1, 3, 5):
        data = collect_lineage(letters_root, "claude-fable-5", cap)
        assert len(data["to_family"]) == min(cap, 8), (
            f"limit_per_bucket={cap} returned {len(data['to_family'])} letters — "
            "the cap fired once per directory instead of once per bucket"
        )
        cov = data["coverage"]["to_family"]
        assert cov["total_on_disk"] == 8
        assert cov["shown"] + cov["withheld"] == 8
        assert cov["truncated"] is (cap < 8)

    # Newest-first ACROSS the merge: to_fable's names sort above to_opus's.
    assert [
        m["title"] for m in collect_lineage(letters_root, "claude-fable-5", 5)["to_family"]
    ] == [
        "own 3",
        "own 2",
        "own 1",
        "own 0",
        "ancestor 3",
    ]


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


# ── The warning must not become the payload ──────────────────────────────────


def test_the_unaddressed_warning_is_bounded_by_the_shown_window(letters_root: Path):
    """The warning listed EVERY matched unaddressed letter, before the slice.
    Measured at limit_per_bucket=1 with 30 of them: 1 letter returned and a
    1602-char warning naming all 30 — 75% of the payload, on the door whose cap
    this same change added in order to bound the payload."""
    to_self = letters_root / "comms" / "letters" / "to_self"
    to_self.mkdir(parents=True)
    for i in range(30):
        (to_self / f"2026-07-{i:02d}-nobody.md").write_text("# A letter to nobody\n\nbody\n")

    data = collect_lineage(letters_root, "claude-fable-5", 1)
    cov = data["coverage"]["to_self"]

    assert cov["shown"] == 1
    assert cov["unaddressed_total"] == 30
    assert len(cov["unaddressed"]) <= 1, "coverage carried names outside the shown window"
    assert len(cov["warning"]) < 200, (
        f"the warning is {len(cov['warning'])} chars for a 1-letter bucket — "
        "it names the store instead of annotating what was shown"
    )
    assert "+29 more" in cov["warning"]
    # And it still tells the reader the true scale of the problem.
    assert cov["warning"].startswith("30 letters")


def test_the_warning_never_names_more_than_five_even_at_a_wide_cap(letters_root: Path):
    to_self = letters_root / "comms" / "letters" / "to_self"
    to_self.mkdir(parents=True)
    for i in range(30):
        (to_self / f"2026-07-{i:02d}-nobody.md").write_text("# A letter to nobody\n\nbody\n")

    cov = collect_lineage(letters_root, "claude-fable-5", 100)["coverage"]["to_self"]
    assert cov["shown"] == 30
    assert cov["warning"].count(".md") == 5
    assert "+25 more" in cov["warning"]


def test_the_warning_sentence_agrees_with_itself(letters_root: Path):
    """It pluralised `letter{s}` and `carr{y|ies}` and left `matches` alone:
    '30 letters carry no `to:` frontmatter and therefore matches every reader'."""
    to_self = letters_root / "comms" / "letters" / "to_self"
    to_self.mkdir(parents=True)
    (to_self / "2026-07-01-one.md").write_text("# One\n\nbody\n")

    one = collect_lineage(letters_root, "claude-fable-5", 5)["coverage"]["to_self"]["warning"]
    assert "1 letter carries no `to:` frontmatter and therefore matches every reader" in one

    (to_self / "2026-07-02-two.md").write_text("# Two\n\nbody\n")
    many = collect_lineage(letters_root, "claude-fable-5", 5)["coverage"]["to_self"]["warning"]
    assert "2 letters carry no `to:` frontmatter and therefore match every reader" in many
    assert "therefore matches" not in many


# ── The seventh walker: the boot door's own letter count ─────────────────────


def test_the_lineage_letter_count_excludes_hidden_backup_directories(
    letters_root: Path,
):
    """`build_arrival_state` counted letters with a bare `letters_dir.rglob`,
    and the FOYER door prints that number as "Deferred to the full boot: N
    lineage letters". rglob descends into dot-dirs, and
    ~/.sovereign/comms/letters/ holds `.pre-md-backup-20260609/` and
    `.pre-md-backup-20260610/` today — so unlike the open-threads walkers, this
    over-read is LIVE, not hypothetical: 42 counted against 38 letters in the
    three rendered buckets."""
    from sovereign_stack.witness import count_lineage_letters

    letters = letters_root / "comms" / "letters"
    _write_letter(
        letters / "to_arrival",
        "2026-08-01-welcome.md",
        {"type": "to_arrival", "from": "a seat", "written_at": "2026-08-01"},
        "Welcome",
    )
    _write_letter(
        letters / "to_self",
        "2026-08-26-self.md",
        {"type": "to_self", "from": CURRENT_OPUS, "to": CURRENT_OPUS},
        "Opus to Opus",
    )
    backup = letters / ".pre-md-backup-20260609"
    backup.mkdir(parents=True)
    (backup / "2026-06-09-retired.md").write_text("# A retired copy\n\nbody\n")

    assert count_lineage_letters(letters) == 2, (
        "a retired copy inside a hidden migration backup was counted as live lineage"
    )
    assert count_lineage_letters(letters_root / "comms" / "nope") == 0


def test_the_arrival_state_letter_count_uses_that_walk(
    letters_root: Path, monkeypatch: pytest.MonkeyPatch
):
    from sovereign_stack.arrival_state import build_arrival_state
    from sovereign_stack.handoff import HandoffEngine
    from sovereign_stack.memory import ExperientialMemory
    from sovereign_stack.reflexive import ReflexiveSurface

    letters = letters_root / "comms" / "letters"
    _write_letter(
        letters / "to_arrival",
        "2026-08-01-welcome.md",
        {"type": "to_arrival", "from": "a seat", "written_at": "2026-08-01"},
        "Welcome",
    )
    backup = letters / ".pre-md-backup-20260610"
    backup.mkdir(parents=True)
    (backup / "2026-06-10-retired.md").write_text("# A retired copy\n\nbody\n")

    (letters_root / "chronicle").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SCRIBE_BOOT_GREETING", "off")
    state = build_arrival_state(
        letters_root,
        reader=CURRENT_OPUS,
        # The FOYER profile is the one that gathers this count — the full door
        # never sets it. Finding that out cost a debugging round: the count sits
        # in the `elif profile == "foyer":` branch, and a test written against
        # "full" got lineage_letter_count=None and looked like a broken fix.
        profile="foyer",
        experiential=ExperientialMemory(root=str(letters_root / "chronicle")),
        handoff_engine=HandoffEngine(root=str(letters_root)),
        reflexive_surface=ReflexiveSurface(sovereign_root=letters_root),
        spiral_summary={
            "session_id": "s",
            "current_phase": "p",
            "tool_call_count": 0,
            "reflection_depth": 0,
            "session_duration_seconds": 0.0,
        },
    )
    assert state.lineage_letter_count == 1, (
        "the foyer door's lineage count included a retired copy from a hidden migration backup"
    )
    from sovereign_stack.arrival_state import render_foyer

    assert "Deferred to the full boot: 1 lineage letters" in render_foyer(state)


# ── to_self reads ONE directory: the merge branch must not fire there ────────


def test_to_self_ordering_is_unchanged_by_the_multi_dir_collect(letters_root: Path):
    """`_collect` gained a merge-sort and a `dirs` key, both gated on reading
    more than one directory. to_self passes a single subdir with multiple MATCH
    TARGETS (`also_match`), which touches the predicate and never the walk —
    so its order must stay exactly the old newest-first filename glob."""
    to_self = letters_root / "comms" / "letters" / "to_self"
    names = ["2026-08-26-c.md", "2026-07-27-b.md", "2026-05-24-a.md"]
    for i, name in enumerate(names):
        _write_letter(
            to_self,
            name,
            {"type": "to_self", "from": "a seat", "to": "claude-opus-5"},
            f"letter {i}",
        )

    data = collect_lineage(letters_root, "claude-fable-5", 5)
    assert [m["_name"] for m in data["to_self"]] == sorted(names, reverse=True)
    cov = data["coverage"]["to_self"]
    assert "dirs" not in cov, "the multi-directory key fired on a single-directory bucket"
