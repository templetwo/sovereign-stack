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
    days_old,
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
