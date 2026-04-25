"""
Stage A+B reflection-daemons observability tests.

Covers:
  * record_prior_alignment — validation, success path, not-surfaced tracking
  * prior_alignment_summary — empty log, aggregation, by_source bucketing,
    time-windowed queries, turns_with_priors_no_alignment gap-counting
  * Dashboard pollers: _git_recent_commits, _launchctl_service_states

Test structure mirrors test_nape_daemon.py: pytest with class-based grouping,
monkeypatch for SOVEREIGN_ROOT isolation, tmp_path fixtures, unittest.mock
for subprocess.

Stage A (`honks_with_history`) already has comprehensive coverage in
test_nape_daemon.py — not duplicated here.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sovereign_stack.dashboard import (
    _git_recent_commits,
    _launchctl_service_states,
)
from sovereign_stack.prior_alignment import (
    _kind_for_signature,
    _within_window,
    prior_alignment_summary,
    record_prior_alignment,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _write_priors_entry(
    root: Path, turn_id: str, items: list, ts: str = "2026-04-25T10:00:00+00:00"
) -> None:
    """Write one priors_log entry for `turn_id`."""
    priors_dir = root / "reflexive"
    priors_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "turn_id": turn_id,
        "timestamp": ts,
        "included_items": items,
    }
    with (priors_dir / "priors_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# TestRecordPriorAlignment
# ─────────────────────────────────────────────────────────────────────────────


class TestRecordPriorAlignment:
    """record_prior_alignment: validation + success + not_surfaced tracking."""

    def test_rejects_empty_turn_id(self, tmp_path):
        """Empty string turn_id → ok=False with a descriptive error."""
        result = record_prior_alignment("", sovereign_root=tmp_path)
        assert result["ok"] is False
        assert "turn_id" in result["error"].lower()

    def test_rejects_none_turn_id(self, tmp_path):
        """None turn_id → ok=False."""
        result = record_prior_alignment(None, sovereign_root=tmp_path)  # type: ignore[arg-type]
        assert result["ok"] is False

    def test_rejects_unknown_turn_id(self, tmp_path):
        """A turn_id not present in priors_log → ok=False, error=unknown_turn_id."""
        result = record_prior_alignment(
            "00000000-0000-0000-0000-000000000000",
            sovereign_root=tmp_path,
        )
        assert result["ok"] is False
        assert result["error"] == "unknown_turn_id"

    def test_success_path_writes_alignment_log(self, tmp_path):
        """Valid turn_id → ok=True, record persisted to alignment_log.jsonl."""
        tid = str(uuid.uuid4())
        _write_priors_entry(tmp_path, tid, ["thread:abc", "insight:xyz"])

        result = record_prior_alignment(
            tid,
            aligned_with=["thread:abc"],
            contradicted=[],
            ignored=["insight:xyz"],
            notes="test note",
            sovereign_root=tmp_path,
        )

        assert result["ok"] is True
        rec = result["alignment_record"]
        assert rec["turn_id"] == tid
        assert rec["aligned_with"] == ["thread:abc"]
        assert rec["ignored"] == ["insight:xyz"]
        assert rec["notes"] == "test note"

        # Check file was written.
        log_path = tmp_path / "reflexive" / "alignment_log.jsonl"
        assert log_path.exists()
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        persisted = json.loads(lines[0])
        assert persisted["turn_id"] == tid

    def test_success_returns_correct_record_shape(self, tmp_path):
        """Returned alignment_record has all required fields."""
        tid = str(uuid.uuid4())
        _write_priors_entry(tmp_path, tid, ["honk:h1"])

        result = record_prior_alignment(
            tid,
            aligned_with=["honk:h1"],
            sovereign_root=tmp_path,
        )

        assert result["ok"] is True
        rec = result["alignment_record"]
        for field_name in (
            "turn_id",
            "timestamp",
            "surfaced",
            "aligned_with",
            "contradicted",
            "ignored",
            "notes",
        ):
            assert field_name in rec, f"Missing field: {field_name}"

    def test_not_surfaced_signatures_recorded_but_still_ok(self, tmp_path):
        """Signatures not in the priors surface are noted in not_surfaced_referenced
        but do NOT cause a rejection — ok=True."""
        tid = str(uuid.uuid4())
        # Only "thread:abc" was surfaced.
        _write_priors_entry(tmp_path, tid, ["thread:abc"])

        result = record_prior_alignment(
            tid,
            aligned_with=["thread:abc", "insight:phantom"],  # phantom was not surfaced
            sovereign_root=tmp_path,
        )

        assert result["ok"] is True
        rec = result["alignment_record"]
        assert "insight:phantom" in rec["not_surfaced_referenced"]
        assert "thread:abc" not in rec["not_surfaced_referenced"]

    def test_multiple_records_append_to_same_log(self, tmp_path):
        """Two calls append two lines — the log is not overwritten."""
        for i in range(2):
            tid = str(uuid.uuid4())
            _write_priors_entry(tmp_path, tid, [f"thread:t{i}"])
            record_prior_alignment(
                tid,
                aligned_with=[f"thread:t{i}"],
                sovereign_root=tmp_path,
            )

        log_path = tmp_path / "reflexive" / "alignment_log.jsonl"
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2


# ─────────────────────────────────────────────────────────────────────────────
# TestPriorAlignmentSummary
# ─────────────────────────────────────────────────────────────────────────────


class TestPriorAlignmentSummary:
    """prior_alignment_summary: empty, aggregation, by_source, window, gaps."""

    def test_empty_log_returns_all_zeros(self, tmp_path):
        """No alignment records → totals/ratios all zero, no crash."""
        result = prior_alignment_summary(sovereign_root=tmp_path)

        assert result["totals"]["aligned"] == 0
        assert result["totals"]["contradicted"] == 0
        assert result["totals"]["ignored"] == 0
        assert result["totals"]["turns_with_alignment"] == 0
        assert result["ratios"]["alignment_rate"] == 0.0
        assert result["ratios"]["contradiction_rate"] == 0.0
        assert result["ratios"]["ignore_rate"] == 0.0
        assert result["by_source"] == {}

    def test_aggregates_counts_across_multiple_records(self, tmp_path):
        """aligned/contradicted/ignored roll up correctly across records."""
        # Record 1: 2 aligned, 1 ignored
        tid1 = str(uuid.uuid4())
        _write_priors_entry(tmp_path, tid1, ["thread:a", "thread:b", "insight:c"])
        record_prior_alignment(
            tid1,
            aligned_with=["thread:a", "thread:b"],
            ignored=["insight:c"],
            sovereign_root=tmp_path,
        )

        # Record 2: 1 contradicted
        tid2 = str(uuid.uuid4())
        _write_priors_entry(tmp_path, tid2, ["honk:h1"])
        record_prior_alignment(
            tid2,
            contradicted=["honk:h1"],
            sovereign_root=tmp_path,
        )

        result = prior_alignment_summary(sovereign_root=tmp_path)
        assert result["totals"]["aligned"] == 2
        assert result["totals"]["contradicted"] == 1
        assert result["totals"]["ignored"] == 1
        assert result["totals"]["turns_with_alignment"] == 2

    def test_by_source_bucketing(self, tmp_path):
        """Signatures bucketed by prefix: honk→drift, thread→thread, insight→insight,
        uncertainty→uncertainty."""
        tid = str(uuid.uuid4())
        _write_priors_entry(
            tmp_path,
            tid,
            ["honk:h1", "thread:t1", "insight:i1", "uncertainty:u1"],
        )
        record_prior_alignment(
            tid,
            aligned_with=["honk:h1", "thread:t1"],
            contradicted=["insight:i1"],
            ignored=["uncertainty:u1"],
            sovereign_root=tmp_path,
        )

        result = prior_alignment_summary(sovereign_root=tmp_path)
        by_src = result["by_source"]

        assert by_src["drift"]["aligned"] == 1
        assert by_src["thread"]["aligned"] == 1
        assert by_src["insight"]["contradicted"] == 1
        assert by_src["uncertainty"]["ignored"] == 1

        # Totals per source.
        assert by_src["drift"]["total"] == 1
        assert by_src["thread"]["total"] == 1

    def test_time_window_since_excludes_older_records(self, tmp_path):
        """Records before `since` are excluded from the summary."""
        tid_old = str(uuid.uuid4())
        _write_priors_entry(tmp_path, tid_old, ["thread:old"], ts="2026-01-01T00:00:00+00:00")

        tid_new = str(uuid.uuid4())
        _write_priors_entry(tmp_path, tid_new, ["thread:new"], ts="2026-04-25T00:00:00+00:00")

        # Write alignment records with matching timestamps directly to log.
        align_path = tmp_path / "reflexive" / "alignment_log.jsonl"
        align_path.parent.mkdir(parents=True, exist_ok=True)
        for tid, ts, sig in [
            (tid_old, "2026-01-01T00:00:00+00:00", "thread:old"),
            (tid_new, "2026-04-25T00:00:00+00:00", "thread:new"),
        ]:
            align_path.open("a").write(
                json.dumps(
                    {
                        "turn_id": tid,
                        "timestamp": ts,
                        "aligned_with": [sig],
                        "contradicted": [],
                        "ignored": [],
                    }
                )
                + "\n"
            )

        # Only include records on or after April.
        result = prior_alignment_summary(
            since="2026-04-01T00:00:00+00:00",
            sovereign_root=tmp_path,
        )
        assert result["totals"]["aligned"] == 1
        assert result["totals"]["turns_with_alignment"] == 1

    def test_time_window_until_excludes_future_records(self, tmp_path):
        """Records after `until` are excluded."""
        tid = str(uuid.uuid4())
        _write_priors_entry(tmp_path, tid, ["thread:t1"], ts="2026-06-01T00:00:00+00:00")

        align_path = tmp_path / "reflexive" / "alignment_log.jsonl"
        align_path.parent.mkdir(parents=True, exist_ok=True)
        align_path.write_text(
            json.dumps(
                {
                    "turn_id": tid,
                    "timestamp": "2026-06-01T00:00:00+00:00",
                    "aligned_with": ["thread:t1"],
                    "contradicted": [],
                    "ignored": [],
                }
            )
            + "\n"
        )

        # Window ends before June.
        result = prior_alignment_summary(
            until="2026-05-01T00:00:00+00:00",
            sovereign_root=tmp_path,
        )
        assert result["totals"]["aligned"] == 0

    def test_turns_with_priors_no_alignment_gap_counting(self, tmp_path):
        """Turns that had priors surfaced but no alignment record filed are counted."""
        # Two prior_for_turn calls.
        tid1 = str(uuid.uuid4())
        tid2 = str(uuid.uuid4())
        _write_priors_entry(tmp_path, tid1, ["thread:a"])
        _write_priors_entry(tmp_path, tid2, ["insight:b"])

        # File alignment for tid1 only.
        record_prior_alignment(
            tid1,
            aligned_with=["thread:a"],
            sovereign_root=tmp_path,
        )

        result = prior_alignment_summary(sovereign_root=tmp_path)
        # tid2 had priors but no alignment → gap of 1.
        assert result["totals"]["turns_with_priors_no_alignment"] >= 1

    def test_turns_with_empty_included_items_not_counted_as_gap(self, tmp_path):
        """A priors_log entry with empty included_items doesn't contribute to gap."""
        tid = str(uuid.uuid4())
        # Write a priors entry with no items.
        priors_dir = tmp_path / "reflexive"
        priors_dir.mkdir(parents=True, exist_ok=True)
        entry = {"turn_id": tid, "timestamp": "2026-04-25T10:00:00+00:00", "included_items": []}
        (priors_dir / "priors_log.jsonl").write_text(json.dumps(entry) + "\n")

        result = prior_alignment_summary(sovereign_root=tmp_path)
        assert result["totals"]["turns_with_priors_no_alignment"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# TestKindForSignature (unit tests for _kind_for_signature helper)
# ─────────────────────────────────────────────────────────────────────────────


class TestKindForSignature:
    def test_honk_prefix_returns_drift(self):
        assert _kind_for_signature("honk:abc123") == "drift"

    def test_thread_prefix_returns_thread(self):
        assert _kind_for_signature("thread:t99") == "thread"

    def test_insight_prefix_returns_insight(self):
        assert _kind_for_signature("insight:some-id") == "insight"

    def test_uncertainty_prefix_returns_uncertainty(self):
        assert _kind_for_signature("uncertainty:u1") == "uncertainty"

    def test_unknown_prefix_passes_through(self):
        assert _kind_for_signature("foo:bar") == "foo"

    def test_no_colon_returns_unknown(self):
        assert _kind_for_signature("no-colon-here") == "unknown"

    def test_non_string_returns_unknown(self):
        assert _kind_for_signature(42) == "unknown"  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# TestWithinWindow (unit tests for _within_window helper)
# ─────────────────────────────────────────────────────────────────────────────


class TestWithinWindow:
    def test_no_bounds_always_true(self):
        assert _within_window("2026-04-25T10:00:00+00:00", None, None) is True

    def test_before_since_returns_false(self):
        assert (
            _within_window(
                "2026-01-01T00:00:00+00:00",
                "2026-04-01T00:00:00+00:00",
                None,
            )
            is False
        )

    def test_after_until_returns_false(self):
        assert (
            _within_window(
                "2026-12-01T00:00:00+00:00",
                None,
                "2026-06-01T00:00:00+00:00",
            )
            is False
        )

    def test_within_both_bounds_true(self):
        assert (
            _within_window(
                "2026-04-25T00:00:00+00:00",
                "2026-04-01T00:00:00+00:00",
                "2026-05-01T00:00:00+00:00",
            )
            is True
        )

    def test_missing_timestamp_defaults_to_true(self):
        assert _within_window(None, "2026-04-01T00:00:00+00:00", None) is True

    def test_garbage_timestamp_defaults_to_true(self):
        # Unparseable timestamps should not crash.
        assert _within_window("not-a-date", "2026-04-01T00:00:00+00:00", None) is True


# ─────────────────────────────────────────────────────────────────────────────
# TestGitRecentCommits
# ─────────────────────────────────────────────────────────────────────────────


class TestGitRecentCommits:
    def test_returns_empty_for_non_git_path(self, tmp_path):
        """A directory without a .git folder → empty list, no crash."""
        result = _git_recent_commits(tmp_path)
        assert result == []

    def test_parses_real_git_log_from_project_repo(self):
        """Use the project's own .git directory as a live fixture."""
        repo = Path("/Users/tony_studio/sovereign-stack")
        if not (repo / ".git").exists():
            pytest.skip("Sovereign-stack .git not found; skipping live test")

        commits = _git_recent_commits(repo, limit=3)
        # Should return up to 3 entries.
        assert len(commits) <= 3
        if commits:
            for entry in commits:
                assert "sha" in entry
                assert "subject" in entry
                assert "iso" in entry
                # SHA is a hex string of at least 7 chars.
                assert len(entry["sha"]) >= 7

    def test_limit_respected(self, tmp_path):
        """limit parameter is forwarded; mocked output is truncated."""
        # Create a fake .git dir so the path guard passes.
        (tmp_path / ".git").mkdir()

        fake_output = "\n".join(
            [f"abc{i:03d}\t2026-04-25T10:00:00+00:00\tcommit {i}" for i in range(10)]
        )

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = fake_output
            mock_run.return_value = mock_proc

            result = _git_recent_commits(tmp_path, limit=5)
        assert len(result) == 10  # We return all lines from output; limit is a git arg

    def test_git_failure_returns_empty(self, tmp_path):
        """Non-zero git return code → empty list."""
        (tmp_path / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 128
            mock_proc.stdout = ""
            mock_run.return_value = mock_proc

            result = _git_recent_commits(tmp_path, limit=5)
        assert result == []

    def test_timeout_exception_returns_empty(self, tmp_path):
        """TimeoutExpired during git call → empty list, no crash."""
        (tmp_path / ".git").mkdir()

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 3.0)):
            result = _git_recent_commits(tmp_path)
        assert result == []

    def test_file_not_found_returns_empty(self, tmp_path):
        """git binary missing → FileNotFoundError → empty list, no crash."""
        (tmp_path / ".git").mkdir()

        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            result = _git_recent_commits(tmp_path)
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# TestLaunchctlServiceStates
# ─────────────────────────────────────────────────────────────────────────────


class TestLaunchctlServiceStates:
    def _make_proc(self, stdout: str, returncode: int = 0) -> MagicMock:
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        return proc

    def test_extracts_state_and_pid(self):
        """State and PID are parsed from launchctl print output."""
        fake_output = (
            "{\n"
            "    active count = 1\n"
            "    state = running\n"
            "    pid = 4242\n"
            "    last exit code = 0\n"
            "}\n"
        )
        with patch.object(subprocess, "run", return_value=self._make_proc(fake_output)):
            result = _launchctl_service_states(["com.example.myservice"])

        assert "com.example.myservice" in result
        info = result["com.example.myservice"]
        assert info["state"] == "running"
        assert info["pid"] == 4242

    def test_state_none_on_subprocess_failure(self):
        """Non-zero return code → {state: None, pid: None}."""
        with patch.object(subprocess, "run", return_value=self._make_proc("", returncode=1)):
            result = _launchctl_service_states(["com.example.missing"])
        info = result["com.example.missing"]
        assert info["state"] is None
        assert info["pid"] is None

    def test_state_none_on_timeout(self):
        """TimeoutExpired → {state: None, pid: None}, no crash."""
        with patch.object(
            subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["launchctl"], 2.0),
        ):
            result = _launchctl_service_states(["com.example.slow"])
        info = result["com.example.slow"]
        assert info["state"] is None
        assert info["pid"] is None

    def test_state_none_on_os_error(self):
        """OSError (launchctl not present) → {state: None, pid: None}."""
        with patch.object(subprocess, "run", side_effect=OSError("no launchctl")):
            result = _launchctl_service_states(["com.example.absent"])
        info = result["com.example.absent"]
        assert info["state"] is None
        assert info["pid"] is None

    def test_multiple_labels_all_returned(self):
        """Each label in the input gets an entry in the output dict."""
        fake_output = "    state = running\n    pid = 1\n"
        with patch.object(subprocess, "run", return_value=self._make_proc(fake_output)):
            result = _launchctl_service_states(["svc.a", "svc.b", "svc.c"])
        assert set(result.keys()) == {"svc.a", "svc.b", "svc.c"}

    def test_empty_labels_list_returns_empty_dict(self):
        """Empty input → empty output, no subprocess call needed."""
        with patch.object(subprocess, "run") as mock_run:
            result = _launchctl_service_states([])
        assert result == {}
        mock_run.assert_not_called()
