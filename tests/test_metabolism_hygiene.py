"""
Metabolism Hygiene Tests

The hygiene actions give metabolism hands, not just eyes. These tests lock
down the invariants: test-pollution patterns are caught, archives preserve
original content, genuine observations are untouched, self-model dedup keeps
the first occurrence, backups are written.
"""

import json
import shutil
import tempfile
from pathlib import Path

from sovereign_stack.metabolism import (
    _archive_test_artifacts_impl,
    _dedup_self_model_impl,
    _is_test_artifact,
)

# ══════════ Pattern detection ══════════


class TestIsTestArtifact:
    def test_stress_test_prefix(self):
        assert _is_test_artifact("STRESS TEST HYPOTHESIS: should be retired")

    def test_stress_test_lowercase_prefix(self):
        assert _is_test_artifact("Stress test: system correctly ran all tool validations")

    def test_unicode_test_prefix(self):
        assert _is_test_artifact("Unicode test: †⟡† 🦆 ñ ü 中文")

    def test_monomorphic_filler(self):
        """50KB of the same character — classic stress-test filler."""
        assert _is_test_artifact("x" * 50000)

    def test_monomorphic_threshold_just_over(self):
        """Just over 1000 chars and ≤3 unique chars qualifies."""
        assert _is_test_artifact("a" * 1001)
        assert _is_test_artifact(("abc" * 400)[:1001])  # 3 unique chars

    def test_short_repeating_content_not_flagged(self):
        """Short 'xxx' isn't test pollution — might be legitimate placeholder."""
        assert not _is_test_artifact("xxx")

    def test_legitimate_observation_not_flagged(self):
        assert not _is_test_artifact("The system revealed a continuity bug in the handoff engine.")

    def test_empty_not_flagged(self):
        assert not _is_test_artifact("")
        assert not _is_test_artifact(None)

    def test_leading_whitespace_tolerated(self):
        assert _is_test_artifact("   STRESS TEST something")


# ══════════ Archive test artifacts from chronicle ══════════


class TestArchiveTestArtifacts:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.chronicle = self.tmpdir / "chronicle"
        self.insights = self.chronicle / "insights"
        self.insights.mkdir(parents=True)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_insights(self, domain: str, entries: list):
        domain_dir = self.insights / domain
        domain_dir.mkdir(exist_ok=True)
        path = domain_dir / "session.jsonl"
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return path

    def test_missing_insights_dir_returns_zero(self):
        result = _archive_test_artifacts_impl(self.tmpdir / "nonexistent")
        assert result["archived"] == 0

    def test_clean_chronicle_untouched(self):
        self._write_insights(
            "real",
            [
                {"content": "A legitimate observation.", "domain": "real"},
                {"content": "Another real insight.", "domain": "real"},
            ],
        )
        result = _archive_test_artifacts_impl(self.chronicle)
        assert result["archived"] == 0
        assert (self.insights / "real" / "session.jsonl").exists()

    def test_test_artifacts_moved_to_archive(self):
        self._write_insights(
            "stress_test",
            [
                {"content": "STRESS TEST HYPOTHESIS: placeholder", "domain": "stress_test"},
                {"content": "x" * 50000, "domain": "stress_test"},
                {"content": "Unicode test: 🦆", "domain": "stress_test"},
            ],
        )
        result = _archive_test_artifacts_impl(self.chronicle)
        assert result["archived"] == 3
        # Domain dir should be gone (all entries archived, became empty)
        assert not (self.insights / "stress_test").exists()
        # Archive dir should exist with the content
        archive_dir = self.chronicle / ".archive_test_artifacts"
        assert archive_dir.exists()
        archive_files = list(archive_dir.glob("*.jsonl"))
        assert len(archive_files) == 1

    def test_mixed_file_partially_cleaned(self):
        """A file with mixed real + test entries keeps only the real ones."""
        path = self._write_insights(
            "mixed",
            [
                {"content": "Real observation 1.", "domain": "mixed"},
                {"content": "STRESS TEST: noise", "domain": "mixed"},
                {"content": "Real observation 2.", "domain": "mixed"},
            ],
        )
        result = _archive_test_artifacts_impl(self.chronicle)
        assert result["archived"] == 1
        # File should still exist with the 2 real entries
        assert path.exists()
        kept = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        assert len(kept) == 2
        assert all("Real observation" in k["content"] for k in kept)

    def test_archived_entry_preserves_provenance(self):
        self._write_insights(
            "stress_test",
            [
                {
                    "content": "STRESS TEST: x",
                    "domain": "stress_test",
                    "timestamp": "2026-04-06T00:00:00",
                },
            ],
        )
        _archive_test_artifacts_impl(self.chronicle)
        archive_files = list((self.chronicle / ".archive_test_artifacts").glob("*.jsonl"))
        assert len(archive_files) == 1
        archived = [json.loads(line) for line in archive_files[0].read_text().splitlines()]
        assert len(archived) == 1
        a = archived[0]
        assert a["content"] == "STRESS TEST: x"
        assert a["timestamp"] == "2026-04-06T00:00:00"
        assert "_archived_at" in a
        assert a["_archived_reason"] == "test_artifact_pattern"
        assert "_original_file" in a

    def test_hidden_dirs_skipped(self):
        """Directories starting with '.' (like the archive itself) are never cleaned."""
        (self.insights / ".migrated_seeds").mkdir()
        (self.insights / ".migrated_seeds" / "session.jsonl").write_text(
            json.dumps({"content": "STRESS TEST: in hidden dir"}) + "\n"
        )
        result = _archive_test_artifacts_impl(self.chronicle)
        assert result["archived"] == 0
        # The file is left alone.
        assert (self.insights / ".migrated_seeds" / "session.jsonl").exists()

    def test_corrupt_jsonl_line_preserved(self):
        """A malformed line shouldn't crash and shouldn't be classified as artifact."""
        path = self._write_insights(
            "mixed",
            [
                {"content": "Real entry.", "domain": "mixed"},
            ],
        )
        # Append corrupt line
        with open(path, "a") as f:
            f.write("{ this is not valid\n")
        _archive_test_artifacts_impl(self.chronicle)
        # File should still have both lines.
        lines = path.read_text().splitlines()
        assert len(lines) == 2

    def test_multiple_calls_idempotent(self):
        """Running twice on the same chronicle does not duplicate archive entries."""
        self._write_insights(
            "stress_test",
            [
                {"content": "STRESS TEST: x", "domain": "stress_test"},
            ],
        )
        _archive_test_artifacts_impl(self.chronicle)
        _archive_test_artifacts_impl(self.chronicle)  # Second call — chronicle is clean now.
        archive_files = list((self.chronicle / ".archive_test_artifacts").glob("*.jsonl"))
        archived = [
            json.loads(line)
            for af in archive_files
            for line in af.read_text().splitlines()
            if line.strip()
        ]
        assert len(archived) == 1  # Not 2 — second call found nothing to archive.


# ══════════ Dedup self model ══════════


class TestDedupSelfModel:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_model(self, model: dict):
        (self.tmpdir / "self_model.json").write_text(json.dumps(model, indent=2))

    def test_missing_file_returns_zero(self):
        result = _dedup_self_model_impl(self.tmpdir)
        assert result["removed"] == 0

    def test_corrupt_file_returns_error(self):
        (self.tmpdir / "self_model.json").write_text("{ not json")
        result = _dedup_self_model_impl(self.tmpdir)
        assert "error" in result

    def test_clean_model_untouched(self):
        self._write_model(
            {
                "strength": [
                    {"observation": "Strong at synthesis.", "timestamp": "2026-04-01"},
                ],
                "tendency": [
                    {"observation": "Moves fast.", "timestamp": "2026-04-01"},
                ],
            }
        )
        result = _dedup_self_model_impl(self.tmpdir)
        assert result["removed"] == 0

    def test_duplicate_observations_removed(self):
        """The same observation text in the same category is a duplicate."""
        self._write_model(
            {
                "strength": [
                    {"observation": "Synthesis.", "timestamp": "t1"},
                    {"observation": "Synthesis.", "timestamp": "t2"},
                    {"observation": "Synthesis.", "timestamp": "t3"},
                ]
            }
        )
        result = _dedup_self_model_impl(self.tmpdir)
        assert result["removed"] == 2
        model = json.loads((self.tmpdir / "self_model.json").read_text())
        assert len(model["strength"]) == 1

    def test_dedup_keeps_first_occurrence(self):
        """Order matters: first observation stays, subsequent duplicates drop."""
        self._write_model(
            {
                "strength": [
                    {"observation": "Original.", "timestamp": "2026-01-01"},
                    {"observation": "Original.", "timestamp": "2026-02-01"},
                ]
            }
        )
        _dedup_self_model_impl(self.tmpdir)
        model = json.loads((self.tmpdir / "self_model.json").read_text())
        assert model["strength"][0]["timestamp"] == "2026-01-01"

    def test_test_pollution_always_removed(self):
        """Even if unique, test-pollution observations go away."""
        self._write_model(
            {
                "strength": [
                    {"observation": "Real.", "timestamp": "t1"},
                    {
                        "observation": "Stress test: system correctly ran all tool validations",
                        "timestamp": "t2",
                    },
                ]
            }
        )
        result = _dedup_self_model_impl(self.tmpdir)
        assert result["removed"] == 1
        model = json.loads((self.tmpdir / "self_model.json").read_text())
        assert len(model["strength"]) == 1
        assert model["strength"][0]["observation"] == "Real."

    def test_backup_written(self):
        self._write_model(
            {
                "strength": [
                    {"observation": "A.", "timestamp": "t1"},
                    {"observation": "A.", "timestamp": "t2"},
                ]
            }
        )
        _dedup_self_model_impl(self.tmpdir)
        backup = self.tmpdir / "self_model.json.pre_dedup.bak"
        assert backup.exists()
        backup_data = json.loads(backup.read_text())
        # Backup contains the ORIGINAL — pre-dedup
        assert len(backup_data["strength"]) == 2

    def test_archive_preserves_removed_entries(self):
        self._write_model(
            {
                "strength": [
                    {"observation": "Real.", "timestamp": "t1"},
                    {"observation": "Stress test: noise", "timestamp": "t2"},
                    {"observation": "Real.", "timestamp": "t3"},
                ]
            }
        )
        _dedup_self_model_impl(self.tmpdir)
        archive = self.tmpdir / "self_model_archive.jsonl"
        assert archive.exists()
        archived = [json.loads(line) for line in archive.read_text().splitlines() if line.strip()]
        assert len(archived) == 2
        reasons = {a["_archived_reason"] for a in archived}
        assert "test_artifact_pattern" in reasons
        assert "duplicate" in reasons

    def test_categories_touched_reported(self):
        self._write_model(
            {
                "strength": [
                    {"observation": "A.", "timestamp": "t1"},
                    {"observation": "A.", "timestamp": "t2"},
                ],
                "tendency": [
                    {"observation": "B.", "timestamp": "t1"},  # unique
                ],
            }
        )
        result = _dedup_self_model_impl(self.tmpdir)
        assert "strength" in result["categories_touched"]
        assert "tendency" not in result["categories_touched"]
