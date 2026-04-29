"""
Witness Module Tests — Boot-Surface Invariants

The boot surface is how every Claude instance lands into continuity.
These helpers turn stored self-knowledge into readable lines. If they
break silently, the arrival goes empty and the instance loses context.
"""

import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sovereign_stack.witness import (
    _letter_matches_reader,
    _model_family,
    days_old,
    format_lineage_layer,
    format_self_model,
    format_threads_with_age,
    format_unresolved_uncertainties,
)


class TestDaysOld:
    def test_none_returns_zero(self):
        assert days_old(None) == 0

    def test_empty_string_returns_zero(self):
        assert days_old("") == 0

    def test_unparseable_returns_zero(self):
        assert days_old("not a date") == 0
        assert days_old("2026-99-99") == 0

    def test_future_returns_zero(self):
        future = (datetime.now() + timedelta(days=5)).isoformat()
        assert days_old(future) == 0

    def test_naive_iso_parses_correctly(self):
        past = (datetime.now() - timedelta(days=7)).isoformat()
        assert days_old(past) == 7

    def test_tz_aware_iso_parses_correctly(self):
        past = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        assert days_old(past) == 14

    def test_today_returns_zero(self):
        today = datetime.now().isoformat()
        assert days_old(today) == 0


class TestFormatSelfModel:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_file_returns_empty(self):
        assert format_self_model(self.tmpdir) == []

    def test_corrupt_file_returns_empty(self):
        (self.tmpdir / "self_model.json").write_text("{ not valid")
        assert format_self_model(self.tmpdir) == []

    def test_empty_model_returns_empty(self):
        (self.tmpdir / "self_model.json").write_text(
            json.dumps({"strength": [], "tendency": [], "blind_spot": [], "drift": []})
        )
        assert format_self_model(self.tmpdir) == []

    def test_single_category_surfaces(self):
        (self.tmpdir / "self_model.json").write_text(
            json.dumps(
                {
                    "strength": [
                        {"observation": "Strong at synthesis.", "timestamp": "2026-04-06T00:00:00"}
                    ]
                }
            )
        )
        lines = format_self_model(self.tmpdir)
        assert any("WHO YOU'VE BEEN OBSERVED TO BE" in ln for ln in lines)
        assert any("strength" in ln and "synthesis" in ln for ln in lines)

    def test_shows_latest_per_category(self):
        (self.tmpdir / "self_model.json").write_text(
            json.dumps(
                {
                    "strength": [
                        {"observation": "old strength", "timestamp": "2026-01-01"},
                        {"observation": "latest strength", "timestamp": "2026-04-01"},
                    ]
                }
            )
        )
        lines = format_self_model(self.tmpdir)
        joined = "\n".join(lines)
        assert "latest strength" in joined
        assert "old strength" not in joined

    def test_category_order(self):
        """Strength first (affirm), drift last (shadow)."""
        (self.tmpdir / "self_model.json").write_text(
            json.dumps(
                {
                    "strength": [{"observation": "S obs", "timestamp": "t"}],
                    "tendency": [{"observation": "T obs", "timestamp": "t"}],
                    "blind_spot": [{"observation": "B obs", "timestamp": "t"}],
                    "drift": [{"observation": "D obs", "timestamp": "t"}],
                }
            )
        )
        lines = format_self_model(self.tmpdir)
        joined = "\n".join(lines)
        pos_s = joined.find("S obs")
        pos_t = joined.find("T obs")
        pos_b = joined.find("B obs")
        pos_d = joined.find("D obs")
        assert pos_s < pos_t < pos_b < pos_d

    def test_long_observation_truncated(self):
        long_obs = "x" * 500
        (self.tmpdir / "self_model.json").write_text(
            json.dumps({"strength": [{"observation": long_obs, "timestamp": "t"}]})
        )
        lines = format_self_model(self.tmpdir, max_obs_len=60)
        body_lines = [ln for ln in lines if "strength" in ln]
        assert len(body_lines) == 1
        # Line is "  strength: xxxx...". The observation text itself must be <= max_obs_len.
        assert "…" in body_lines[0]

    def test_missing_observation_field_skipped(self):
        (self.tmpdir / "self_model.json").write_text(
            json.dumps(
                {
                    "strength": [{"timestamp": "t"}]  # no observation field
                }
            )
        )
        assert format_self_model(self.tmpdir) == []


class TestFormatUnresolvedUncertainties:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.consciousness_dir = self.tmpdir / "consciousness"
        self.consciousness_dir.mkdir(parents=True)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_log(self, markers):
        path = self.consciousness_dir / "uncertainty_log.json"
        path.write_text(
            json.dumps(
                {
                    "created": "2026-01-01",
                    "purpose": "test",
                    "markers": markers,
                }
            )
        )

    def test_missing_file_returns_empty(self):
        assert format_unresolved_uncertainties(self.tmpdir) == []

    def test_empty_markers_returns_empty(self):
        self._write_log([])
        assert format_unresolved_uncertainties(self.tmpdir) == []

    def test_all_resolved_returns_empty(self):
        self._write_log(
            [
                {"question": "Q1", "resolved": True, "timestamp": "2026-01-01"},
                {"question": "Q2", "resolution": "answer", "timestamp": "2026-01-02"},
            ]
        )
        assert format_unresolved_uncertainties(self.tmpdir) == []

    def test_single_unresolved_surfaces(self):
        self._write_log([{"question": "Is X safe?", "timestamp": "2026-04-01"}])
        lines = format_unresolved_uncertainties(self.tmpdir)
        joined = "\n".join(lines)
        assert "UNRESOLVED UNCERTAINTIES" in joined
        assert "Is X safe?" in joined

    def test_mixed_returns_only_unresolved(self):
        self._write_log(
            [
                {"question": "Q resolved", "resolved": True, "timestamp": "2026-01-01"},
                {"question": "Q open", "timestamp": "2026-04-01"},
            ]
        )
        lines = format_unresolved_uncertainties(self.tmpdir)
        joined = "\n".join(lines)
        assert "Q open" in joined
        assert "Q resolved" not in joined

    def test_supports_content_field(self):
        """Some historical markers use 'content' instead of 'question'."""
        self._write_log([{"content": "Flagged via content field", "timestamp": "2026-04-01"}])
        lines = format_unresolved_uncertainties(self.tmpdir)
        assert any("Flagged via content field" in ln for ln in lines)

    def test_respects_limit(self):
        markers = [{"question": f"Q{i}", "timestamp": f"2026-04-{i:02d}"} for i in range(1, 11)]
        self._write_log(markers)
        lines = format_unresolved_uncertainties(self.tmpdir, limit=3)
        # Header + blurb + blank + 3 bullets + trailing blank = 6 lines
        bullet_count = sum(1 for ln in lines if ln.startswith("  • "))
        assert bullet_count == 3

    def test_newest_first(self):
        self._write_log(
            [
                {"question": "oldest", "timestamp": "2026-01-01"},
                {"question": "newest", "timestamp": "2026-04-15"},
            ]
        )
        lines = format_unresolved_uncertainties(self.tmpdir)
        joined = "\n".join(lines)
        assert joined.find("newest") < joined.find("oldest")

    def test_age_tag_appears_for_old(self):
        past = (datetime.now() - timedelta(days=10)).isoformat()
        self._write_log([{"question": "old Q", "timestamp": past}])
        lines = format_unresolved_uncertainties(self.tmpdir)
        joined = "\n".join(lines)
        assert "10d old" in joined

    def test_corrupt_log_returns_empty(self):
        (self.consciousness_dir / "uncertainty_log.json").write_text("{ not json")
        assert format_unresolved_uncertainties(self.tmpdir) == []


class TestFormatThreadsWithAge:
    def test_empty_returns_empty(self):
        assert format_threads_with_age([]) == []

    def test_single_thread_formatted(self):
        threads = [
            {
                "question": "What is X?",
                "domain": "test",
                "timestamp": datetime.now().isoformat(),
            }
        ]
        lines = format_threads_with_age(threads)
        assert any("OPEN THREADS" in ln for ln in lines)
        assert any("What is X?" in ln for ln in lines)

    def test_recent_thread_no_age_tag(self):
        """Today's thread should not carry an age tag."""
        threads = [
            {
                "question": "fresh",
                "domain": "d",
                "timestamp": datetime.now().isoformat(),
            }
        ]
        lines = format_threads_with_age(threads)
        bullet = next(ln for ln in lines if ln.startswith("  •"))
        assert "(0d)" not in bullet  # 0-day-old gets no tag

    def test_recent_nontrivial_age_tag(self):
        past = (datetime.now() - timedelta(days=5)).isoformat()
        threads = [{"question": "q", "domain": "d", "timestamp": past}]
        lines = format_threads_with_age(threads)
        bullet = next(ln for ln in lines if ln.startswith("  •"))
        assert "(5d)" in bullet
        assert "stale" not in bullet.lower()

    def test_stale_thread_marked(self):
        """Threads older than 30 days get a 'stale?' flag."""
        past = (datetime.now() - timedelta(days=60)).isoformat()
        threads = [{"question": "q", "domain": "d", "timestamp": past}]
        lines = format_threads_with_age(threads)
        bullet = next(ln for ln in lines if ln.startswith("  •"))
        assert "stale" in bullet.lower()
        assert "60d" in bullet

    def test_preserves_domain(self):
        threads = [{"question": "q", "domain": "architecture", "timestamp": "2026-01-01"}]
        lines = format_threads_with_age(threads)
        bullet = next(ln for ln in lines if ln.startswith("  •"))
        assert "[architecture]" in bullet

    def test_question_truncation(self):
        long_q = "x" * 300
        threads = [{"question": long_q, "domain": "d", "timestamp": "2026-01-01"}]
        lines = format_threads_with_age(threads, truncate_question=50)
        bullet = next(ln for ln in lines if ln.startswith("  •"))
        assert bullet.count("x") <= 50


# ── _model_family ────────────────────────────────────────────────────────────


class TestModelFamily:
    def test_extracts_sonnet_family(self):
        assert _model_family("claude-sonnet-4-6-1m-claude-code") == "claude-sonnet"

    def test_extracts_opus_family(self):
        assert _model_family("claude-opus-4-7-1m-claude-code") == "claude-opus"

    def test_extracts_haiku_family(self):
        assert _model_family("claude-haiku-4-5-20251001") == "claude-haiku"

    def test_unknown_returns_none(self):
        assert _model_family("unknown") is None

    def test_empty_returns_none(self):
        assert _model_family("") is None

    def test_non_claude_returns_none(self):
        assert _model_family("gpt-4-turbo") is None


# ── _letter_matches_reader ───────────────────────────────────────────────────


class TestLetterMatchesReader:
    def test_exact_match(self):
        assert _letter_matches_reader(
            "claude-sonnet-4-6-1m-claude-code",
            "claude-sonnet-4-6-1m-claude-code",
        )

    def test_family_match(self):
        assert _letter_matches_reader("claude-sonnet", "claude-sonnet-4-6-1m-claude-code")

    def test_short_family_match(self):
        assert _letter_matches_reader("sonnet", "claude-sonnet-4-6-1m-claude-code")

    def test_prefix_match(self):
        assert _letter_matches_reader("claude-sonnet-4-6", "claude-sonnet-4-6-1m-claude-code")

    def test_wrong_family_no_match(self):
        assert not _letter_matches_reader("claude-opus", "claude-sonnet-4-6-1m-claude-code")

    def test_different_exact_id_no_match(self):
        assert not _letter_matches_reader(
            "claude-sonnet-4-6-1m-web", "claude-sonnet-4-6-1m-claude-code"
        )

    def test_empty_letter_to_no_match(self):
        assert not _letter_matches_reader("", "claude-sonnet-4-6-1m-claude-code")

    def test_empty_reader_no_match(self):
        assert not _letter_matches_reader("claude-sonnet", "")


# ── format_lineage_layer ─────────────────────────────────────────────────────


def _write_letter(d: Path, filename: str, frontmatter: dict, title: str = "Test letter") -> None:
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    body = f"\n# {title}\n\nContent here.\n"
    (d / filename).write_text("\n".join(fm_lines) + body)


class TestFormatLineageLayer:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.letters = self.tmp / "comms" / "letters"
        self.letters.mkdir(parents=True)

    def teardown_method(self):
        shutil.rmtree(self.tmp)

    def test_missing_base_dir_returns_empty(self):
        assert format_lineage_layer(Path("/does/not/exist")) == []

    def test_all_empty_subdirs_returns_empty(self):
        (self.letters / "to_arrival").mkdir()
        assert format_lineage_layer(self.tmp) == []

    def test_to_arrival_surfaces(self):
        d = self.letters / "to_arrival"
        d.mkdir()
        _write_letter(d, "2026-01-01-test.md", {"type": "to_arrival", "from": "opus-test", "written_at": "2026-01-01"}, "Hello arrival")
        lines = format_lineage_layer(self.tmp)
        assert any("to_arrival" in ln for ln in lines)
        assert any("Hello arrival" in ln for ln in lines)

    def test_to_self_exact_match_surfaces(self):
        d = self.letters / "to_self"
        d.mkdir()
        _write_letter(d, "letter.md", {"type": "to_self", "to": "claude-sonnet-4-6-1m-test", "from": "me"}, "Exact")
        lines = format_lineage_layer(self.tmp, reader_instance="claude-sonnet-4-6-1m-test")
        assert any("Exact" in ln for ln in lines)

    def test_to_self_family_match_surfaces(self):
        d = self.letters / "to_self"
        d.mkdir()
        _write_letter(d, "letter.md", {"type": "to_self", "to": "claude-sonnet", "from": "me"}, "Family letter")
        lines = format_lineage_layer(self.tmp, reader_instance="claude-sonnet-4-6-1m-test")
        assert any("Family letter" in ln for ln in lines)

    def test_to_self_wrong_family_hidden(self):
        d = self.letters / "to_self"
        d.mkdir()
        _write_letter(d, "letter.md", {"type": "to_self", "to": "claude-opus", "from": "me"}, "Opus only")
        lines = format_lineage_layer(self.tmp, reader_instance="claude-sonnet-4-6-1m-test")
        assert not any("Opus only" in ln for ln in lines)

    def test_to_family_dir_surfaces_for_matching_tier(self):
        d = self.letters / "to_sonnet"
        d.mkdir()
        _write_letter(d, "letter.md", {"type": "to_family", "from": "me", "written_at": "2026-01-01"}, "Sonnet family")
        lines = format_lineage_layer(self.tmp, reader_instance="claude-sonnet-4-6-1m-test")
        assert any("Sonnet family" in ln for ln in lines)
        assert any("to_sonnet" in ln for ln in lines)

    def test_to_family_dir_hidden_for_wrong_tier(self):
        d = self.letters / "to_opus"
        d.mkdir()
        _write_letter(d, "letter.md", {"type": "to_family", "from": "me", "written_at": "2026-01-01"}, "Opus family")
        lines = format_lineage_layer(self.tmp, reader_instance="claude-sonnet-4-6-1m-test")
        assert not any("Opus family" in ln for ln in lines)
