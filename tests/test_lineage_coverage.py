"""
Lineage Door Coverage Tests — the cap must SAY what it withheld

The lineage door capped every bucket at limit_per_bucket=5 with zero
coverage signal: headers printed len() of the already-capped list, so the
oldest to_self letters vanished silently. Separately, a letter with no
frontmatter block rendered as `[] [?] (untitled)` — a silent blank header —
while its body surfaced fine. One defect, two symptoms (co-signed
ground_truth diagnosis, Stack chronicle sovereign-stack, 2026-08-02).

These tests pin the fix: per-bucket total-on-disk + truncated flag +
withheld count in the collect envelope (the aae7281 read-honesty shape),
shown-of-total headers in the render, an addressee-miss that says so, and
a `metadata missing` marker with body-derived identity for frontmatterless
letters. Plus byte-stability guards: complete, well-formed buckets render
exactly as before.
"""

import shutil
import tempfile
from pathlib import Path

from sovereign_stack.witness import (
    _parse_letter_frontmatter,
    collect_lineage,
    format_lineage_layer,
    render_lineage,
)


def _write_letter(d: Path, filename: str, frontmatter: dict, title: str = "Test letter") -> None:
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    body = f"\n# {title}\n\nContent here.\n"
    (d / filename).write_text("\n".join(fm_lines) + body)


def _write_bare_letter(d: Path, filename: str, title: str) -> None:
    """A letter with NO frontmatter block — starts straight at the H1."""
    (d / filename).write_text(f"# {title}\n\nBody that self-identifies its author.\n")


class _LineageCase:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.letters = self.tmp / "comms" / "letters"
        self.letters.mkdir(parents=True)

    def teardown_method(self):
        shutil.rmtree(self.tmp)

    def _fill(self, subdir: str, n: int, **fm_extra) -> Path:
        d = self.letters / subdir
        d.mkdir(exist_ok=True)
        for i in range(n):
            _write_letter(
                d,
                f"2026-01-{i + 1:02d}-letter-{i + 1}.md",
                {"from": "opus-test", "written_at": f"2026-01-{i + 1:02d}", **fm_extra},
                f"Letter number {i + 1}",
            )
        return d


# ── Coverage envelope on collect_lineage ─────────────────────────────────────


class TestCollectCoverageEnvelope(_LineageCase):
    def test_coverage_present_with_totals_and_truncated_flag(self):
        self._fill("to_arrival", 7)
        data = collect_lineage(self.tmp, None, 5)
        cov = data["coverage"]["arrivals"]
        assert cov["total_on_disk"] == 7
        assert cov["matched"] == 7
        assert cov["shown"] == 5
        assert cov["withheld"] == 2
        assert cov["truncated"] is True
        assert len(data["arrivals"]) == 5

    def test_complete_bucket_not_truncated(self):
        self._fill("to_arrival", 3)
        cov = collect_lineage(self.tmp, None, 5)["coverage"]["arrivals"]
        assert cov["total_on_disk"] == 3
        assert cov["withheld"] == 0
        assert cov["truncated"] is False

    def test_cap_drops_the_oldest_not_the_newest(self):
        # Glob is reverse-sorted (newest-first); the cap must eat the OLDEST.
        self._fill("to_arrival", 7)
        data = collect_lineage(self.tmp, None, 5)
        titles = [m["title"] for m in data["arrivals"]]
        assert "Letter number 7" in titles  # newest survives
        assert "Letter number 1" not in titles  # oldest withheld
        assert data["coverage"]["arrivals"]["withheld"] == 2

    def test_to_self_counts_addressee_filter_and_cap_separately(self):
        d = self.letters / "to_self"
        d.mkdir()
        for i in range(7):
            _write_letter(
                d,
                f"2026-02-{i + 1:02d}-mine-{i + 1}.md",
                {"from": "me", "to": "claude-sonnet", "written_at": f"2026-02-{i + 1:02d}"},
                f"Mine {i + 1}",
            )
        for i in range(2):
            _write_letter(
                d,
                f"2026-03-{i + 1:02d}-other-{i + 1}.md",
                {"from": "me", "to": "claude-opus", "written_at": f"2026-03-{i + 1:02d}"},
                f"Other {i + 1}",
            )
        cov = collect_lineage(self.tmp, "claude-sonnet-4-6-test", 5)["coverage"]["to_self"]
        assert cov["total_on_disk"] == 9
        assert cov["matched"] == 7
        assert cov["shown"] == 5
        assert cov["withheld"] == 2
        assert cov["filtered_out"] == 2

    def test_no_reader_still_counts_to_self_on_disk(self):
        self._fill("to_self", 3, to="claude-opus")
        cov = collect_lineage(self.tmp, None, 5)["coverage"]["to_self"]
        assert cov["total_on_disk"] == 3
        assert cov["shown"] == 0
        assert cov.get("no_reader") is True


# ── Rendered coverage — the header states shown-of-total ─────────────────────


class TestRenderStatesCoverage(_LineageCase):
    def test_truncated_bucket_header_states_total_and_withheld(self):
        self._fill("to_arrival", 7)
        lines = format_lineage_layer(self.tmp)
        header = next(ln for ln in lines if "to_arrival" in ln)
        assert "showing 5 of 7 letters on disk" in header
        assert "2 older withheld by limit_per_bucket" in header

    def test_complete_bucket_header_byte_identical_to_precoverage_form(self):
        self._fill("to_arrival", 3)
        lines = format_lineage_layer(self.tmp)
        assert "  to_arrival (3 letters — for whoever lands next):" in lines

    def test_single_letter_header_keeps_singular_form(self):
        self._fill("to_arrival", 1)
        lines = format_lineage_layer(self.tmp)
        assert "  to_arrival (1 letter — for whoever lands next):" in lines

    def test_to_self_header_states_withheld_and_filtered(self):
        d = self.letters / "to_self"
        d.mkdir()
        for i in range(7):
            _write_letter(
                d,
                f"2026-02-{i + 1:02d}-mine-{i + 1}.md",
                {"from": "me", "to": "claude-sonnet", "written_at": f"2026-02-{i + 1:02d}"},
                f"Mine {i + 1}",
            )
        for i in range(2):
            _write_letter(
                d,
                f"2026-03-{i + 1:02d}-other-{i + 1}.md",
                {"from": "me", "to": "claude-opus", "written_at": f"2026-03-{i + 1:02d}"},
                f"Other {i + 1}",
            )
        lines = format_lineage_layer(self.tmp, reader_instance="claude-sonnet-4-6-test")
        header = next(ln for ln in lines if ln.lstrip().startswith("to_self"))
        assert "showing 5 of 9 letters on disk" in header
        assert "2 older withheld by limit_per_bucket" in header
        assert "2 addressed to other readers" in header

    def test_addressee_miss_says_so_instead_of_vanishing(self):
        self._fill("to_arrival", 1)
        self._fill("to_self", 2, to="claude-opus")
        lines = format_lineage_layer(self.tmp, reader_instance="claude-sonnet-4-6-test")
        miss = [ln for ln in lines if "to_self: 0 of 2 letters shown" in ln]
        assert miss, f"addressee-miss stayed silent: {lines}"
        assert "none addressed to you" in miss[0]
        assert "2 addressed to other readers" in miss[0]

    def test_no_reader_miss_prescribes_source_instance(self):
        self._fill("to_arrival", 1)
        self._fill("to_self", 2, to="claude-opus")
        lines = format_lineage_layer(self.tmp)
        miss = [ln for ln in lines if "to_self: 0 of 2 letters shown" in ln]
        assert miss, f"no-reader miss stayed silent: {lines}"
        assert "pass source_instance" in miss[0]

    def test_empty_store_still_returns_empty(self):
        (self.letters / "to_arrival").mkdir()
        assert format_lineage_layer(self.tmp) == []

    def test_wrapper_identity_holds_on_truncated_and_malformed_store(self):
        self._fill("to_arrival", 7)
        d = self.letters / "to_self"
        d.mkdir()
        _write_bare_letter(d, "2026-07-04-bare.md", "Bare To Self")
        for fc in (False, True):
            assert format_lineage_layer(
                self.tmp, reader_instance="claude-sonnet-4-6-test", full_content=fc
            ) == render_lineage(
                collect_lineage(self.tmp, "claude-sonnet-4-6-test", 5), full_content=fc
            )


# ── Frontmatterless letters — marked, never a silent blank header ────────────


class TestMetadataMissingMarker(_LineageCase):
    def test_parser_marks_missing_frontmatter_and_recovers_identity(self):
        d = self.letters / "to_arrival"
        d.mkdir()
        _write_bare_letter(
            d, "2026-07-06-the-instrument-sings-in-tune.md", "The Instrument Sings in Tune"
        )
        meta = _parse_letter_frontmatter(d / "2026-07-06-the-instrument-sings-in-tune.md")
        assert meta.get("_frontmatter_missing") is True
        assert meta.get("title") == "The Instrument Sings in Tune"
        assert meta.get("written_at") == "2026-07-06"

    def test_unterminated_frontmatter_also_marked(self):
        d = self.letters / "to_arrival"
        d.mkdir()
        (d / "2026-07-07-unterminated.md").write_text("---\nfrom: someone\n\n# Cut Off\n")
        meta = _parse_letter_frontmatter(d / "2026-07-07-unterminated.md")
        assert meta.get("_frontmatter_missing") is True

    def test_render_shows_marker_and_recovered_title_not_blank(self):
        d = self.letters / "to_arrival"
        d.mkdir()
        _write_bare_letter(
            d, "2026-07-06-the-instrument-sings-in-tune.md", "The Instrument Sings in Tune"
        )
        lines = format_lineage_layer(self.tmp)
        entry = next(ln for ln in lines if ln.lstrip().startswith("•"))
        assert "[metadata missing]" in entry
        assert "The Instrument Sings in Tune" in entry
        assert "[2026-07-06]" in entry
        assert "(untitled)" not in entry
        assert "[?]" not in entry

    def test_to_self_bare_letter_marked_unaddressed(self):
        # A frontmatterless to_self letter has an empty `to`, which bypasses
        # the reader filter and surfaces to EVERY family (root cause shared
        # with the blank header). Routing is unchanged here — but the render
        # must now SHOW the miss instead of a blank `[?] → [?]`.
        d = self.letters / "to_self"
        d.mkdir()
        _write_bare_letter(
            d, "2026-07-04-the-day-four-minds-converged.md", "The Day Four Minds Converged"
        )
        lines = format_lineage_layer(self.tmp, reader_instance="claude-fable-5-test")
        entry = next(ln for ln in lines if ln.lstrip().startswith("•"))
        assert "[metadata missing]" in entry
        assert "→ [unaddressed]" in entry
        assert "The Day Four Minds Converged" in entry

    def test_bare_letter_body_still_renders_with_full_content(self):
        d = self.letters / "to_arrival"
        d.mkdir()
        _write_bare_letter(d, "2026-07-06-bare.md", "Bare Letter")
        lines = format_lineage_layer(self.tmp, full_content=True)
        assert any("Body that self-identifies" in ln for ln in lines)

    def test_wellformed_letter_render_unchanged(self):
        d = self.letters / "to_arrival"
        d.mkdir()
        _write_letter(
            d,
            "2026-01-01-fine.md",
            {"from": "opus-test", "written_at": "2026-01-01"},
            "Well Formed",
        )
        lines = format_lineage_layer(self.tmp)
        assert "    • [2026-01-01] [opus-test] Well Formed" in lines
