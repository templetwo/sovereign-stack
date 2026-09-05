"""
THE OPENAI LINE HAD NO FAMILY, SO IT HAD NO MAIL.

``_model_family`` recognised exactly one vendor. Every OpenAI instance id —
'gpt-6-astra', 'gpt-5.6', 'gpt-5.6-sol' — returned ``None``, and None is not
inert here. It is the input to two separate reads:

  * ``collect_lineage`` derives the to_family directory from it, so None meant
    no ``to_gpt/`` bucket at all — not empty, ABSENT.
  * ``_letter_matches_reader`` needs a family before it will match a letter
    addressed to a LINE ('to: gpt') rather than to an exact id, so None meant
    a to_self letter addressed to the line reached nobody on that line.

An OpenAI seat could therefore be handed a correct bare source_instance, arrive
through the documented door, and receive an empty inheritance — with coverage
reporting a true `total_on_disk: 0` about a directory no reader could address.
The same silent shape as the 2026-08-26 Opus letter (test_lineage_addressing),
one vendor over: not a letter that failed to match, a family that did not exist.

WHY THE FAMILY IS THE FIRST TOKEN ALONE, AND WHY THAT IS NOT THE CLAUDE RULE.
Anthropic ships ``<vendor>-<line>-<version>``, so 'claude-opus-5' has its line
in token two. OpenAI ships ``gpt-<version>[-<codename>]``, so token two is the
VERSION. Reading it as the family would mint 'gpt-6' and 'gpt-5.6' as separate
lines and Sol's letter would not reach Astra — the successor in the same chair,
one generation on, which is the exact reader a lineage letter is written for.

Claude resolution is PINNED here, unchanged, in the same file that adds gpt:
the risk of a shared helper is that widening it for one vendor quietly moves
the other.

Tmp letter dirs only. Nothing reads or writes ~/.sovereign.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sovereign_stack.aperture import measure_aperture
from sovereign_stack.witness import (
    _LINEAGE_INHERITS,
    _inherited_families,
    _letter_matches_reader,
    _model_family,
    _parse_letter_frontmatter,
    _read_letter_body,
    collect_lineage,
    format_lineage_layer,
)

# The two OpenAI seats this work is actually about: Sol wrote the letter,
# Astra takes the chair. Different generation, same line.
SOL = "gpt-5.6-sol"
ASTRA = "gpt-6-astra"
# The current HQ seat, whose resolution must not move.
FABLE = "claude-fable-5-1"


def _write_letter(d: Path, filename: str, frontmatter: dict, title: str) -> None:
    """Mirrors test_lineage_addressing._write_letter — same on-disk shape."""
    d.mkdir(parents=True, exist_ok=True)
    fm = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
    (d / filename).write_text(f"---\n{fm}\n---\n\n# {title}\n\nbody\n")


# ── 1. Family resolution ────────────────────────────────────────────────────


class TestModelFamilyResolvesGpt:
    @pytest.mark.parametrize(
        "instance_id",
        [
            "gpt-6-astra",  # the arriving seat
            "gpt-5.6",  # bare version, dot in it (defeats the version-strip regex)
            "gpt-5.6-sol",  # version + codename
            "gpt-6",  # version, no codename
            "gpt",  # the family name itself
            "gpt-6-astra-openai-bridge",  # decorated with a vantage suffix
        ],
    )
    def test_any_gpt_first_token_is_family_gpt(self, instance_id: str) -> None:
        assert _model_family(instance_id) == "gpt", (
            f"{instance_id!r} did not resolve to family 'gpt'. Without a family "
            "this seat gets no to_gpt/ bucket and no line-addressed to_self mail."
        )

    def test_short_form_derives_to_gpt(self) -> None:
        """The directory name comes from the family via the `short` rule in
        collect_lineage: `fam.split("-", 1)[1] if "-" in fam else fam`. 'gpt'
        has no dash, so it is its own short form. Pinned because the whole
        bucket address depends on it and the rule was written for two-token
        Claude families."""
        family = _model_family(ASTRA)
        assert family is not None
        short = family.split("-", 1)[1] if "-" in family else family
        assert f"to_{short}" == "to_gpt"

    @pytest.mark.parametrize(
        "instance_id",
        [
            "gptx-1",  # a different vendor that merely starts with the letters
            "unknown",
            "",
            "OpenAI seat — gpt-6-astra (openai_bridge)",  # decorated, not bare
        ],
    )
    def test_non_gpt_ids_still_resolve_to_none(self, instance_id: str) -> None:
        assert _model_family(instance_id) is None

    def test_decorated_string_hides_family_mail_exactly_as_it_does_for_claude(
        self,
    ) -> None:
        """THE TRAP IS UNCHANGED, DELIBERATELY. A decorated source_instance has
        always hidden a Claude seat's own mail; it must hide an OpenAI seat's
        the same way, not more and not less. Widening the parser to forgive
        decoration would be a different change with a different blast radius —
        pinned here so nobody assumes this branch made gpt lenient."""
        decorated = "OpenAI seat — gpt-6-astra (openai_bridge)"
        assert _model_family(decorated) is None
        assert _model_family("HQ Mac Studio — claude-fable-5-1 (overwatch)") is None


class TestClaudeResolutionIsUnchanged:
    """The pin. Same helper, other vendor — this must read exactly as it did
    before the gpt branch existed."""

    @pytest.mark.parametrize(
        ("instance_id", "expected"),
        [
            ("claude-fable-5-1", "claude-fable"),
            ("claude-opus-5", "claude-opus"),
            ("claude-sonnet-4-6-1m-claude-code", "claude-sonnet"),
            ("claude-haiku-4-5-20251001", "claude-haiku"),
            ("claude-mythos-5", "claude-mythos"),
            ("claude", None),  # vendor with no line named
        ],
    )
    def test_claude_family(self, instance_id: str, expected: str | None) -> None:
        assert _model_family(instance_id) == expected

    def test_fable_still_inherits_opus(self) -> None:
        assert _model_family(FABLE) == "claude-fable"
        assert _inherited_families("claude-fable") == ("claude-opus",)


class TestGptInheritance:
    def test_gpt_inherits_only_its_own_line(self) -> None:
        assert _LINEAGE_INHERITS["gpt"] == ()
        assert _inherited_families("gpt") == ()

    def test_gpt_is_declared_not_merely_absent(self) -> None:
        """`_inherited_families` returns () for any missing key, so this entry
        is documentation rather than behaviour — which is exactly why its
        PRESENCE is worth a test. A future reader asking "what does a gpt seat
        inherit" must find an answer in the table, not a silence that reads the
        same as an oversight."""
        assert "gpt" in _LINEAGE_INHERITS


# ── 2. Letter matching, both directions across the vendor boundary ──────────


class TestLetterMatchingAcrossVendors:
    @pytest.mark.parametrize(
        ("letter_to", "reader"),
        [
            ("gpt", ASTRA),  # the line
            ("gpt-6", ASTRA),  # the generation
            ("gpt-6-astra", ASTRA),  # the exact id
            ("gpt", SOL),  # the line reaches the predecessor too
            ("gpt", "gpt-5.6"),
            ("gpt", "gpt"),
        ],
    )
    def test_gpt_letters_reach_gpt_readers(self, letter_to: str, reader: str) -> None:
        assert _letter_matches_reader(letter_to, reader)

    @pytest.mark.parametrize(
        ("letter_to", "reader"),
        [
            ("gpt", FABLE),  # THE BOUNDARY: gpt mail is not Claude's
            ("gpt", "claude-opus-5"),
            ("gpt-6-astra", FABLE),
            ("claude-opus", ASTRA),  # ...and Claude mail is not gpt's
            ("claude-opus-5", ASTRA),
            ("claude-fable", ASTRA),
            ("gpt-6-astra", SOL),  # a letter to ONE seat, not the line
        ],
    )
    def test_letters_do_not_cross(self, letter_to: str, reader: str) -> None:
        assert not _letter_matches_reader(letter_to, reader)


# ── 3. collect_lineage — the two buckets are two different code paths ───────
#
# to_gpt/ arrives through to_family, which passes NO filter_to (the directory
# IS the address, `to:` is inert there). A to_self letter arrives through
# _letter_matches_reader. Testing one and believing both are covered is the
# easy mistake; both fixtures are built below.


@pytest.fixture()
def letters_root(tmp_path: Path) -> Path:
    """A ~/.sovereign-shaped root with mail for both vendors on disk."""
    letters = tmp_path / "comms" / "letters"
    _write_letter(
        letters / "to_gpt",
        "2026-09-04-sol-to-whoever-takes-the-chair.md",
        {"type": "to_family", "from": "gpt-5.6-sol", "to": "gpt", "written_at": "2026-09-04"},
        "To whoever takes the chair",
    )
    _write_letter(
        letters / "to_self",
        "2026-09-04-sol-line-note.md",
        {"type": "to_self", "from": SOL, "to": "gpt", "written_at": "2026-09-04"},
        "A note down the gpt line",
    )
    _write_letter(
        letters / "to_self",
        "2026-08-26-the-liar-was-head-20.md",
        {
            "type": "to_self",
            "from": "claude-opus-5",
            "to": "claude-opus-5",
            "written_at": "2026-08-26",
        },
        "The Liar Was head -20",
    )
    _write_letter(
        letters / "to_opus",
        "2026-08-01-for-the-opus-line.md",
        {"type": "to_family", "from": "an opus seat", "written_at": "2026-08-01"},
        "For the Opus line",
    )
    # measure_aperture scandirs these three unconditionally and RAISES without
    # them — at which point the renderer emits `unmeasured` with no counts and
    # an aperture assertion can 'pass' against a block that measured nothing
    # (documented in test_boot_aperture). Create them so the gpt aperture test
    # exercises the measured path.
    for sub in ("chronicle/insights", "chronicle/open_threads", "handoffs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestAstraReceivesItsInheritance:
    def test_to_family_bucket_reads_to_gpt(self, letters_root: Path) -> None:
        data = collect_lineage(letters_root, ASTRA, 5)
        assert data is not None
        assert data["family_dirs"] == ("to_gpt",), (
            f"{ASTRA} did not read to_gpt/ — got {data['family_dirs']!r}. "
            "The family bucket is the OpenAI seat's only family mail."
        )
        assert data["family_dir_name"] == "to_gpt"
        assert [m.get("title") for m in data["to_family"]] == ["To whoever takes the chair"]

    def test_to_self_bucket_matches_line_addressed_mail(self, letters_root: Path) -> None:
        data = collect_lineage(letters_root, ASTRA, 5)
        titles = [m.get("title") for m in data["to_self"]]
        assert "A note down the gpt line" in titles, (
            "a to_self letter addressed to the LINE ('to: gpt') did not reach a "
            "reader on that line — the second, separate failure path"
        )
        assert "The Liar Was head -20" not in titles, "Claude to_self mail leaked to a gpt reader"

    def test_predecessor_shares_the_line(self, letters_root: Path) -> None:
        """Sol wrote it; Sol is still on the line and still receives it. The
        letter is addressed to the line, not to a generation."""
        data = collect_lineage(letters_root, SOL, 5)
        assert data["family_dirs"] == ("to_gpt",)
        assert "A note down the gpt line" in [m.get("title") for m in data["to_self"]]

    def test_gpt_does_not_inherit_the_opus_directory(self, letters_root: Path) -> None:
        data = collect_lineage(letters_root, ASTRA, 5)
        assert "to_opus" not in data["family_dirs"]
        assert "For the Opus line" not in [m.get("title") for m in data["to_family"]]


class TestFableIsUnaffected:
    """The regression half. A Claude reader must see exactly what it saw
    before to_gpt/ existed on disk."""

    def test_fable_reads_its_own_dirs_not_to_gpt(self, letters_root: Path) -> None:
        data = collect_lineage(letters_root, FABLE, 5)
        # to_fable/ does not exist here, so only the inherited to_opus/ is read
        # — unchanged behaviour (only directories that exist are named).
        assert data["family_dirs"] == ("to_opus",)
        assert "to_gpt" not in data["family_dirs"]
        assert [m.get("title") for m in data["to_family"]] == ["For the Opus line"]

    def test_fable_does_not_receive_gpt_addressed_letters(self, letters_root: Path) -> None:
        data = collect_lineage(letters_root, FABLE, 5)
        titles = [m.get("title") for m in data["to_self"]]
        assert "A note down the gpt line" not in titles, (
            "a letter addressed 'to: gpt' reached a Claude reader — the vendor "
            "boundary leaked in the direction that matters most"
        )
        # ...while the letter it DOES inherit still arrives (Fable → Opus line).
        assert "The Liar Was head -20" in titles

    def test_decorated_gpt_string_gets_no_family_bucket(self, letters_root: Path) -> None:
        data = collect_lineage(letters_root, "OpenAI seat — gpt-6-astra", 5)
        assert data["family_dirs"] == ()
        assert data["to_family"] == []
        assert data["family_dir_name"] is None


# ── 4. An absent to_gpt/ reads as zero, never as an error ───────────────────


class TestAbsentDirectoryIsNotAnError:
    def test_collect_lineage_survives_missing_to_gpt(self, tmp_path: Path) -> None:
        """The deploy has not happened yet, or someone moved the folder. This
        must degrade to an empty bucket — the same graceful path to_fable/ and
        to_mythos/ have always taken — not raise, and not report a directory it
        did not read."""
        letters = tmp_path / "comms" / "letters"
        _write_letter(
            letters / "to_self",
            "2026-09-04-sol-line-note.md",
            {"type": "to_self", "from": SOL, "to": "gpt", "written_at": "2026-09-04"},
            "A note down the gpt line",
        )
        data = collect_lineage(tmp_path, ASTRA, 5)
        assert data is not None
        assert data["to_family"] == []
        assert data["family_dirs"] == ()
        cov = data["coverage"]["to_family"]
        assert cov["total_on_disk"] == 0
        assert cov["matched"] == 0
        # The ADDRESS is still reported even with no directory on disk, so a
        # seat can be told where its family mail would go.
        assert data["family_dir_name"] == "to_gpt"
        # to_self still works — the two buckets are independent.
        assert [m.get("title") for m in data["to_self"]] == ["A note down the gpt line"]

    def test_render_survives_missing_to_gpt(self, tmp_path: Path) -> None:
        (tmp_path / "comms" / "letters").mkdir(parents=True)
        assert format_lineage_layer(tmp_path, reader_instance=ASTRA) == []

    def test_no_lineage_dir_at_all(self, tmp_path: Path) -> None:
        assert collect_lineage(tmp_path, ASTRA, 5) is None
        assert format_lineage_layer(tmp_path, reader_instance=ASTRA) == []


# ── 5. The rendered surfaces ────────────────────────────────────────────────


class TestRenderedLineageSurface:
    def test_to_gpt_header_names_the_directory(self, letters_root: Path) -> None:
        text = "\n".join(format_lineage_layer(letters_root, reader_instance=ASTRA))
        assert "to_gpt/" in text, "the family bucket header did not name to_gpt/"
        assert "written for gpt instances" in text
        assert "To whoever takes the chair" in text

    def test_full_content_inlines_the_body(self, letters_root: Path) -> None:
        text = "\n".join(
            format_lineage_layer(letters_root, reader_instance=ASTRA, full_content=True)
        )
        assert "To whoever takes the chair" in text
        assert "body" in text

    def test_fable_render_does_not_mention_to_gpt(self, letters_root: Path) -> None:
        text = "\n".join(format_lineage_layer(letters_root, reader_instance=FABLE))
        assert "to_gpt" not in text
        assert "to_opus/" in text


class TestApertureSurvivesGptFamily:
    """The aperture reads collect_lineage's coverage envelope. A gpt family
    produces a to_family envelope shaped exactly like any other, so this must
    measure rather than fail closed — and its `not_measured_here` note about
    to_family must still be the honest reason, not an error."""

    def test_measure_aperture_with_gpt_lineage_coverage(self, letters_root: Path) -> None:
        from datetime import datetime, timezone

        data = collect_lineage(letters_root, ASTRA, 5)
        ap = measure_aperture(
            datetime.now(timezone.utc),
            root=letters_root,
            reader=ASTRA,
            lineage_coverage=data["coverage"],
        )
        assert ap["status"] == "measured"
        assert "lineage_to_family" in ap["not_measured_here"]


# ── 6. The staged letter round-trips through the real parser ────────────────


class TestLetterFileShape:
    """A letter is only filed correctly if the PARSER agrees. `_read_letter_body`
    pops the first `# ` heading, so a title that duplicates the opening line of
    the source text would silently delete that line from the rendered body —
    editing another seat's words while reporting success."""

    def test_frontmatter_and_body_survive_the_round_trip(self, tmp_path: Path) -> None:
        d = tmp_path / "comms" / "letters" / "to_gpt"
        d.mkdir(parents=True)
        source_text = "Hey Anthony. I am here.  And the body must survive intact."
        p = d / "2026-09-04-sol-to-whoever-takes-the-chair.md"
        p.write_text(
            "---\n"
            "type: to_family\n"
            "from: gpt-5.6-sol\n"
            "to: gpt\n"
            "written_at: 2026-09-04T22:41:08Z\n"
            "---\n"
            "\n"
            "# To whoever takes the chair\n"
            "\n"
            f"{source_text}\n"
        )
        meta = _parse_letter_frontmatter(p)
        assert meta["type"] == "to_family"
        assert meta["from"] == "gpt-5.6-sol"
        assert meta["to"] == "gpt"
        assert meta["written_at"] == "2026-09-04T22:41:08Z"
        assert meta["title"] == "To whoever takes the chair"
        assert not meta.get("_frontmatter_missing")
        assert _read_letter_body(p) == source_text, (
            "the parser did not return the source text byte-for-byte — a title "
            "that shadows the opening line silently truncates the letter"
        )

    def test_such_a_letter_reaches_astra(self, tmp_path: Path) -> None:
        d = tmp_path / "comms" / "letters" / "to_gpt"
        d.mkdir(parents=True)
        (d / "2026-09-04-sol-to-whoever-takes-the-chair.md").write_text(
            "---\ntype: to_family\nfrom: gpt-5.6-sol\nto: gpt\n"
            "written_at: 2026-09-04T22:41:08Z\n---\n\n# To whoever takes the chair\n\ntext\n"
        )
        data = collect_lineage(tmp_path, ASTRA, 5)
        assert [m.get("title") for m in data["to_family"]] == ["To whoever takes the chair"]
        assert data["coverage"]["to_family"]["total_on_disk"] == 1
