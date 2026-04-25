"""
Tests for sovereign_stack.monitor — the auto-recovery loop.

Focus on the decision logic (when to attempt restart, backoff, max cap)
and the run_once tick. The async run_loop is just `while True: run_once`,
so we don't unit-test the loop itself.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from sovereign_stack import connectivity as conn
from sovereign_stack import monitor as mon

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    """Redirect monitor.log to a tempdir so tests don't pollute home."""
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    yield tmp_path / "monitor.log"


def _make_status(name: str, status: str) -> conn.EndpointStatus:
    return conn.EndpointStatus(
        name=name, label=f"com.test.{name}",
        kind=conn.KIND_ALWAYS_ON, status=status,
    )


def _make_ok_action(name: str) -> conn.ActionResult:
    return conn.ActionResult(name=name, action="restart", ok=True, returncode=0)


# ── RestartTracker ──────────────────────────────────────────────────────────


class TestRestartTracker:
    def test_first_attempt_always_allowed(self):
        cfg = mon.MonitorConfig()
        t = mon.RestartTracker(cfg)
        assert t.should_attempt("svc", now=1000.0) is True

    def test_streak_caps_at_max(self):
        cfg = mon.MonitorConfig(max_restarts=3,
                                backoff_base=1.0, backoff_cap=1.0)
        t = mon.RestartTracker(cfg)
        # Three failed restarts in a row.
        for i in range(3):
            t.record_attempt("svc", success=False, now=1000.0 + i * 10)
        # 4th attempt blocked by max_restarts.
        assert t.should_attempt("svc", now=1100.0) is False

    def test_backoff_blocks_immediate_retry(self):
        cfg = mon.MonitorConfig(backoff_base=2.0, backoff_cap=60.0)
        t = mon.RestartTracker(cfg)
        t.record_attempt("svc", success=False, now=1000.0)
        # Immediate retry blocked.
        assert t.should_attempt("svc", now=1000.5) is False
        # After 2s backoff (2^1 = 2s), allowed.
        assert t.should_attempt("svc", now=1003.0) is True

    def test_backoff_capped(self):
        cfg = mon.MonitorConfig(backoff_base=10.0, backoff_cap=15.0,
                                max_restarts=10)
        t = mon.RestartTracker(cfg)
        # Force count = 5 → 10^5 = 100000s but cap is 15s.
        for i in range(5):
            t.record_attempt("svc", success=False, now=1000.0 + i)
        # Last attempt at t=1004; 16s later should be allowed.
        assert t.should_attempt("svc", now=1020.0) is True

    def test_success_resets_streak(self):
        cfg = mon.MonitorConfig(backoff_base=2.0, max_restarts=3)
        t = mon.RestartTracker(cfg)
        t.record_attempt("svc", success=False, now=1000.0)
        t.record_attempt("svc", success=False, now=1010.0)
        t.record_attempt("svc", success=True, now=1020.0)
        # After success, streak counter back to zero — backoff doesn't apply.
        assert t.should_attempt("svc", now=1021.0) is True

    def test_baseline_reset_clears_streak(self):
        cfg = mon.MonitorConfig(backoff_base=10.0, max_restarts=2,
                                baseline_reset_seconds=100)
        t = mon.RestartTracker(cfg)
        t.record_attempt("svc", success=False, now=1000.0)
        t.record_attempt("svc", success=False, now=1001.0)
        # Maxed out → blocked.
        assert t.should_attempt("svc", now=1002.0) is False
        # 200s later, baseline reset → allowed again.
        assert t.should_attempt("svc", now=1202.0) is True


# ── run_once ────────────────────────────────────────────────────────────────


class TestRunOnce:
    def test_no_action_when_all_ok(self, tmp_log):
        cfg = mon.MonitorConfig(log_path=tmp_log)
        statuses = [_make_status("a", conn.STATUS_OK),
                    _make_status("b", conn.STATUS_OK)]
        summary = mon.run_once(
            cfg,
            check_fn=lambda: statuses,
            restart_fn=lambda ep: pytest.fail("should not restart"),
        )
        assert summary["down"] == []
        assert summary["actions"] == []

    def test_restart_only_for_down(self, tmp_log):
        cfg = mon.MonitorConfig(log_path=tmp_log)
        # Use a real ENDPOINTS entry name so the lookup succeeds.
        target_name = conn.ENDPOINTS[0].name
        statuses = [_make_status(target_name, conn.STATUS_DOWN)]
        called = []
        def fake_restart(ep):
            called.append(ep.name)
            return _make_ok_action(ep.name)
        summary = mon.run_once(
            cfg,
            check_fn=lambda: statuses,
            restart_fn=fake_restart,
        )
        assert called == [target_name]
        assert summary["actions"][0]["action"] == "restart"
        assert summary["actions"][0]["ok"] is True

    def test_degraded_does_not_trigger_restart(self, tmp_log):
        """Degraded means service is UP but health probe failed.
        Restarting on degraded amplifies flakiness."""
        cfg = mon.MonitorConfig(log_path=tmp_log)
        target_name = conn.ENDPOINTS[0].name
        statuses = [_make_status(target_name, conn.STATUS_DEGRADED)]
        summary = mon.run_once(
            cfg,
            check_fn=lambda: statuses,
            restart_fn=lambda ep: pytest.fail("should not restart on degraded"),
        )
        assert summary["actions"] == []
        assert target_name in summary["degraded"]

    def test_dry_run_does_not_invoke_restart(self, tmp_log):
        cfg = mon.MonitorConfig(log_path=tmp_log, dry_run=True)
        target_name = conn.ENDPOINTS[0].name
        statuses = [_make_status(target_name, conn.STATUS_DOWN)]
        summary = mon.run_once(
            cfg,
            check_fn=lambda: statuses,
            restart_fn=lambda ep: pytest.fail("dry_run must not restart"),
        )
        assert summary["actions"][0]["action"] == "would_restart"
        assert summary["dry_run"] is True

    def test_exclude_skips_endpoint(self, tmp_log):
        target_name = conn.ENDPOINTS[0].name
        cfg = mon.MonitorConfig(log_path=tmp_log, exclude=[target_name])
        statuses = [_make_status(target_name, conn.STATUS_DOWN)]
        summary = mon.run_once(
            cfg,
            check_fn=lambda: statuses,
            restart_fn=lambda ep: pytest.fail("excluded must not restart"),
        )
        assert summary["actions"] == []

    def test_log_event_written_to_file(self, tmp_log):
        cfg = mon.MonitorConfig(log_path=tmp_log)
        statuses = [_make_status("nope", conn.STATUS_OK)]
        mon.run_once(cfg, check_fn=lambda: statuses,
                     restart_fn=lambda ep: _make_ok_action(ep.name))
        assert tmp_log.exists()
        line = tmp_log.read_text().strip().splitlines()[-1]
        rec = json.loads(line)
        assert "timestamp" in rec
        assert rec["checked"] == ["nope"]

    def test_failed_restart_increments_streak(self, tmp_log):
        cfg = mon.MonitorConfig(
            log_path=tmp_log, max_restarts=2, backoff_base=1.0,
        )
        target_name = conn.ENDPOINTS[0].name
        statuses = [_make_status(target_name, conn.STATUS_DOWN)]
        tracker = mon.RestartTracker(cfg)
        def fail_restart(ep):
            return conn.ActionResult(
                name=ep.name, action="restart", ok=False,
                returncode=1, stderr="boom",
            )
        # First tick — restart attempted, fails.
        s1 = mon.run_once(cfg, tracker,
                          check_fn=lambda: statuses,
                          restart_fn=fail_restart,
                          now_fn=lambda: 1000.0)
        assert s1["actions"][0]["ok"] is False
        # Second tick at t=1001 — streak count=1, backoff=1s → allowed.
        s2 = mon.run_once(cfg, tracker,
                          check_fn=lambda: statuses,
                          restart_fn=fail_restart,
                          now_fn=lambda: 1002.0)
        assert s2["actions"][0]["ok"] is False
        # Third tick — count=2 == max → deferred.
        s3 = mon.run_once(cfg, tracker,
                          check_fn=lambda: statuses,
                          restart_fn=fail_restart,
                          now_fn=lambda: 1010.0)
        assert s3["actions"][0]["action"] == "deferred"


# ── monitor_cli ─────────────────────────────────────────────────────────────


class TestMonitorCli:
    def test_once_dry_run_json(self, tmp_log, capsys):
        from sovereign_stack import monitor_cli as cli
        with patch.object(conn, "_launchctl_print_text", return_value=None), \
             patch.object(
                 conn, "_http_probe",
                 return_value={"http_status": None, "body": "",
                               "error": "mocked"},
             ):
            rc = cli.main(["--once", "--dry-run", "--json"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert "checked" in data
        assert "actions" in data
        assert data["dry_run"] is True
