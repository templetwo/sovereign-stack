"""
Post-fix verification tests.

Exercises the watch lifecycle (create → sample → drift / clean → close),
each probe type (http / command / file_hash), the expected-matcher branches
in _diff_probe, and the tick_once driver.

Storage is redirected to a tmp_path via SOVEREIGN_ROOT so tests don't touch
real ~/.sovereign data. HTTP probes monkeypatch `urlopen` in the module to
avoid any network dependency.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sovereign_stack import post_fix_tools as pfx

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def sovereign_root_tmp(tmp_path, monkeypatch):
    """Redirect SOVEREIGN_ROOT so every test gets its own filesystem."""
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    yield tmp_path


@pytest.fixture
def fake_nape():
    """
    Stand-in for NapeDaemon with a spy on emit_external_honk so tests can
    assert a drift honk was emitted without depending on the real daemon.
    """
    nape = MagicMock()
    nape.emit_external_honk = MagicMock(return_value={"honk_id": "fake-honk-1"})
    return nape


def _make_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# =============================================================================
# CREATE WATCH
# =============================================================================

class TestCreateWatch:
    def test_captures_baseline_and_persists(self, tmp_path):
        target = _make_file(tmp_path / "cfg.txt", "alpha")
        watch = pfx.create_watch(
            fix_description="baseline-capture check",
            domain_tags=["test"],
            probes=[{"name": "cfg", "type": "file_hash", "path": str(target)}],
            session_id="sess-1",
        )
        assert watch["status"] == "active"
        assert watch["baseline"]["results"]["cfg"]["exists"] is True
        assert len(watch["baseline"]["results"]["cfg"]["sha256"]) == 64
        # Persisted to active watches dir.
        loaded = pfx.load_watch(watch["watch_id"])
        assert loaded is not None
        assert loaded["fix_description"] == "baseline-capture check"

    def test_schedule_offsets_default_and_sorted(self, tmp_path):
        target = _make_file(tmp_path / "x.txt", "x")
        watch = pfx.create_watch(
            fix_description="x",
            domain_tags=[],
            probes=[{"name": "cfg", "type": "file_hash", "path": str(target)}],
            schedule_offsets_min=[120, 5, 30, 5],  # duplicates + unsorted
        )
        assert watch["schedule_offsets_min"] == [5, 30, 120]

    def test_empty_probes_raises(self):
        with pytest.raises(ValueError):
            pfx.create_watch(fix_description="x", domain_tags=[], probes=[])


# =============================================================================
# TAKE SAMPLE — schedule / force / drift / clean
# =============================================================================

class TestTakeSample:
    def test_not_due_before_first_offset(self, tmp_path):
        target = _make_file(tmp_path / "a.txt", "a")
        watch = pfx.create_watch(
            fix_description="f",
            domain_tags=[],
            probes=[{"name": "p", "type": "file_hash", "path": str(target)}],
            schedule_offsets_min=[60],
        )
        result = pfx.take_sample(watch["watch_id"])
        assert result["status"] == "not_due"

    def test_force_sample_runs_immediately(self, tmp_path, fake_nape):
        target = _make_file(tmp_path / "a.txt", "a")
        watch = pfx.create_watch(
            fix_description="f",
            domain_tags=[],
            probes=[{"name": "p", "type": "file_hash", "path": str(target)}],
            schedule_offsets_min=[60],
        )
        result = pfx.take_sample(watch["watch_id"], force=True, nape_daemon=fake_nape)
        assert result["status"] == "force_sampled"
        assert result["drift"] == []
        # Clean forced sample does not close the watch — scheduled offsets still pending.
        assert result["watch_status"] == "active"
        fake_nape.emit_external_honk.assert_not_called()

    def test_drift_detected_emits_honk_and_closes(self, tmp_path, fake_nape):
        target = _make_file(tmp_path / "cfg.txt", "version-1")
        watch = pfx.create_watch(
            fix_description="config baseline",
            domain_tags=["config"],
            probes=[{"name": "cfg", "type": "file_hash", "path": str(target)}],
            schedule_offsets_min=[60],
            session_id="sess-drift",
        )
        # Mutate the file so sha256 diverges.
        target.write_text("version-2")
        result = pfx.take_sample(watch["watch_id"], force=True, nape_daemon=fake_nape)
        assert result["status"] == "force_sampled"
        assert len(result["drift"]) == 1
        assert result["drift"][0]["reason"] == "file_hash_changed"
        assert result["watch_status"] == "drift_detected"
        # Nape honk emitted with the right pattern and session.
        fake_nape.emit_external_honk.assert_called_once()
        kwargs = fake_nape.emit_external_honk.call_args.kwargs
        assert kwargs["pattern"] == pfx.NAPE_PATTERN_DRIFT
        assert kwargs["session_id"] == "sess-drift"
        assert "cfg:file_hash_changed" in kwargs["observation"]
        # Drift watch moves to archive.
        assert pfx.load_watch(watch["watch_id"])["status"] == "drift_detected"

    def test_all_scheduled_offsets_clean_closes_watch(self, tmp_path, fake_nape, monkeypatch):
        target = _make_file(tmp_path / "a.txt", "a")
        watch = pfx.create_watch(
            fix_description="f",
            domain_tags=[],
            probes=[{"name": "p", "type": "file_hash", "path": str(target)}],
            schedule_offsets_min=[5, 30],
        )
        # Fast-forward `now` so both scheduled offsets are due.
        pfx._iso(pfx._parse_iso(watch["created_at"]).replace(year=2099))
        # Simpler: monkeypatch _now to return a value well past both offsets.
        future = pfx._parse_iso(watch["created_at"]).replace(year=2099)
        monkeypatch.setattr(pfx, "_now", lambda: future)
        r1 = pfx.take_sample(watch["watch_id"], nape_daemon=fake_nape)
        assert r1["status"] == "sampled"
        assert r1["sample"]["offset_min"] == 5
        assert r1["watch_status"] == "active"
        r2 = pfx.take_sample(watch["watch_id"], nape_daemon=fake_nape)
        assert r2["status"] == "sampled"
        assert r2["sample"]["offset_min"] == 30
        assert r2["watch_status"] == "completed_clean"
        final = pfx.load_watch(watch["watch_id"])
        assert final["status"] == "completed_clean"
        assert final["closed_reason"] == "all_samples_clean"

    def test_honk_emission_failure_is_non_fatal(self, tmp_path):
        target = _make_file(tmp_path / "a.txt", "a")
        watch = pfx.create_watch(
            fix_description="f",
            domain_tags=[],
            probes=[{"name": "p", "type": "file_hash", "path": str(target)}],
            schedule_offsets_min=[60],
        )
        target.write_text("b")  # induce drift
        broken = MagicMock()
        broken.emit_external_honk = MagicMock(side_effect=RuntimeError("nape is down"))
        result = pfx.take_sample(watch["watch_id"], force=True, nape_daemon=broken)
        assert result["status"] == "force_sampled"
        assert "honk_emission_error" in result["sample"]
        assert result["watch_status"] == "drift_detected"


# =============================================================================
# PROBE RUNNERS
# =============================================================================

class TestCommandProbe:
    def test_exit_code_and_stdout_captured(self):
        probe = {"name": "p", "type": "command", "cmd": "echo hello"}
        result = pfx._run_command_probe(probe)
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert result["timed_out"] is False

    def test_nonzero_exit_captured(self):
        probe = {"name": "p", "type": "command", "cmd": "false"}
        result = pfx._run_command_probe(probe)
        assert result["exit_code"] != 0

    def test_shell_mode_supports_pipes(self):
        probe = {"name": "p", "type": "command", "cmd": "echo hi | tr i I", "shell": True}
        result = pfx._run_command_probe(probe)
        assert "hI" in result["stdout"]

    def test_diff_stdout_contains_matches(self):
        probe = {
            "name": "p", "type": "command", "cmd": "echo alpha",
            "expected": {"exit_code": 0, "stdout_contains": "alpha"},
        }
        baseline = pfx._run_command_probe(probe)
        current = pfx._run_command_probe(probe)
        assert pfx._diff_probe(probe, baseline, current) is None

    def test_diff_missing_required_substring(self):
        probe = {
            "name": "p", "type": "command", "cmd": "echo beta",
            "expected": {"stdout_contains": "alpha"},
        }
        baseline = {"exit_code": 0, "stdout": "beta\n"}
        current = {"exit_code": 0, "stdout": "beta\n"}
        drift = pfx._diff_probe(probe, baseline, current)
        assert drift is not None
        assert drift["reason"] == "stdout_missing_required_substring"

    def test_diff_baseline_fallback_detects_output_change(self):
        probe = {"name": "p", "type": "command", "cmd": "echo x"}  # no expected block
        baseline = {"exit_code": 0, "stdout": "one\n"}
        current = {"exit_code": 0, "stdout": "two\n"}
        drift = pfx._diff_probe(probe, baseline, current)
        assert drift is not None
        assert drift["reason"] == "output_differs_from_baseline"

    def test_diff_regex(self):
        probe = {
            "name": "p", "type": "command", "cmd": "echo x",
            "expected": {"stdout_regex": r"\bready\b"},
        }
        current = {"exit_code": 0, "stdout": "service ready to serve"}
        drift = pfx._diff_probe(probe, {"exit_code": 0, "stdout": "service ready to serve"}, current)
        assert drift is None
        current_bad = {"exit_code": 0, "stdout": "service starting"}
        drift_bad = pfx._diff_probe(probe, {"exit_code": 0, "stdout": "service ready"}, current_bad)
        assert drift_bad is not None
        assert drift_bad["reason"] == "stdout_regex_not_matched"


class TestFileHashProbe:
    def test_existing_file_hashed(self, tmp_path):
        target = _make_file(tmp_path / "f.txt", "hello")
        result = pfx._run_file_hash_probe({"name": "f", "type": "file_hash", "path": str(target)})
        assert result["exists"] is True
        assert len(result["sha256"]) == 64

    def test_missing_file_reports_nonexistence(self, tmp_path):
        result = pfx._run_file_hash_probe({"name": "f", "type": "file_hash", "path": str(tmp_path / "nope")})
        assert result["exists"] is False
        assert result["sha256"] is None

    def test_diff_existence_change(self):
        probe = {"name": "f", "type": "file_hash", "path": "/tmp/whatever"}
        baseline = {"exists": True, "sha256": "deadbeef" + "0" * 56}
        current = {"exists": False, "sha256": None}
        drift = pfx._diff_probe(probe, baseline, current)
        assert drift is not None
        assert drift["reason"] == "file_hash_changed"  # sha differs, so hash-change wins


class TestHttpProbe:
    def test_success_rate_computed(self, monkeypatch):
        # Fake urlopen: first 8 of 10 return 200, last 2 return 503.
        class FakeResp:
            def __init__(self, status): self.status = status
            def __enter__(self): return self
            def __exit__(self, *a): pass
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] <= 8:
                return FakeResp(200)
            return FakeResp(503)

        monkeypatch.setattr(pfx, "urlopen", fake_urlopen)
        result = pfx._run_http_probe({
            "name": "h", "type": "http", "url": "https://example.com",
            "samples": 10, "timeout_sec": 1,
            "expected": {"status": 200},
        })
        assert result["success_rate"] == 0.8
        assert result["ok_count"] == 8

    def test_diff_success_rate_below_min(self):
        probe = {
            "name": "h", "type": "http", "url": "https://example.com",
            "expected": {"status": 200, "success_rate_min": 0.95},
        }
        baseline = {"success_rate": 1.0, "status_codes": [200, 200]}
        current = {"success_rate": 0.5, "status_codes": [200, 503]}
        drift = pfx._diff_probe(probe, baseline, current)
        assert drift is not None
        assert drift["reason"] == "http_success_rate_drop"
        assert drift["current_rate"] == 0.5

    def test_diff_clean_when_rate_holds(self):
        probe = {
            "name": "h", "type": "http", "url": "https://example.com",
            "expected": {"status": 200, "success_rate_min": 0.9},
        }
        baseline = {"success_rate": 1.0}
        current = {"success_rate": 1.0}
        assert pfx._diff_probe(probe, baseline, current) is None


# =============================================================================
# WATCH CANCEL & LIST
# =============================================================================

class TestWatchManagement:
    def test_cancel_archives_watch(self, tmp_path):
        target = _make_file(tmp_path / "a.txt", "a")
        watch = pfx.create_watch(
            fix_description="f",
            domain_tags=[],
            probes=[{"name": "p", "type": "file_hash", "path": str(target)}],
        )
        result = pfx.cancel_watch(watch["watch_id"], reason="no longer relevant")
        assert result["status"] == "cancelled"
        loaded = pfx.load_watch(watch["watch_id"])
        assert loaded["status"] == "cancelled"
        assert loaded["closed_reason"] == "no longer relevant"

    def test_cancel_twice_is_safe(self, tmp_path):
        target = _make_file(tmp_path / "a.txt", "a")
        watch = pfx.create_watch(
            fix_description="f", domain_tags=[],
            probes=[{"name": "p", "type": "file_hash", "path": str(target)}],
        )
        pfx.cancel_watch(watch["watch_id"], reason="first")
        r2 = pfx.cancel_watch(watch["watch_id"], reason="second")
        assert r2["status"] == "watch_already_closed"

    def test_list_active_excludes_archived(self, tmp_path):
        t = _make_file(tmp_path / "a.txt", "a")
        w1 = pfx.create_watch(fix_description="keep", domain_tags=[], probes=[{"name": "p", "type": "file_hash", "path": str(t)}])
        w2 = pfx.create_watch(fix_description="archive", domain_tags=[], probes=[{"name": "p", "type": "file_hash", "path": str(t)}])
        pfx.cancel_watch(w2["watch_id"], reason="test")
        active = pfx.list_watches(status="active")
        assert len(active) == 1
        assert active[0]["watch_id"] == w1["watch_id"]
        all_watches = pfx.list_watches(status="all")
        assert len(all_watches) == 2

    def test_load_missing_watch_returns_none(self):
        assert pfx.load_watch("pfw_does_not_exist") is None


# =============================================================================
# TICK DRIVER
# =============================================================================

class TestTick:
    def test_tick_drains_all_due_samples(self, tmp_path, monkeypatch, fake_nape):
        t = _make_file(tmp_path / "a.txt", "a")
        watch = pfx.create_watch(
            fix_description="f", domain_tags=[],
            probes=[{"name": "p", "type": "file_hash", "path": str(t)}],
            schedule_offsets_min=[5, 30, 120],
        )
        # Fast-forward past the last offset so all three are due at once.
        future = pfx._parse_iso(watch["created_at"]).replace(year=2099)
        monkeypatch.setattr(pfx, "_now", lambda: future)
        result = pfx.tick_once(nape_daemon=fake_nape)
        assert result["active_watches"] == 1
        assert result["samples_taken"] == 3
        final = pfx.load_watch(watch["watch_id"])
        assert final["status"] == "completed_clean"

    def test_tick_with_no_watches_is_noop(self, fake_nape):
        result = pfx.tick_once(nape_daemon=fake_nape)
        assert result["active_watches"] == 0
        assert result["samples_taken"] == 0

    def test_tick_stops_on_drift(self, tmp_path, monkeypatch, fake_nape):
        t = _make_file(tmp_path / "a.txt", "a")
        watch = pfx.create_watch(
            fix_description="f", domain_tags=[],
            probes=[{"name": "p", "type": "file_hash", "path": str(t)}],
            schedule_offsets_min=[5, 30],
        )
        future = pfx._parse_iso(watch["created_at"]).replace(year=2099)
        monkeypatch.setattr(pfx, "_now", lambda: future)
        # Mutate file so the first sample drifts.
        t.write_text("b")
        result = pfx.tick_once(nape_daemon=fake_nape)
        assert result["samples_taken"] == 1  # drift closes the watch; second offset never runs
        final = pfx.load_watch(watch["watch_id"])
        assert final["status"] == "drift_detected"


# =============================================================================
# EVENTS / AUDIT
# =============================================================================

class TestEventLog:
    def test_events_logged_for_lifecycle(self, tmp_path, fake_nape):
        t = _make_file(tmp_path / "a.txt", "a")
        watch = pfx.create_watch(
            fix_description="f", domain_tags=[],
            probes=[{"name": "p", "type": "file_hash", "path": str(t)}],
        )
        pfx.take_sample(watch["watch_id"], force=True, nape_daemon=fake_nape)
        pfx.cancel_watch(watch["watch_id"], reason="done")
        events_path = pfx._events_path()
        assert events_path.exists()
        lines = [json.loads(ln) for ln in events_path.read_text().splitlines() if ln.strip()]
        event_types = [e["event"] for e in lines]
        assert "watch_created" in event_types
        assert "sample_taken" in event_types
        assert "watch_cancelled" in event_types
