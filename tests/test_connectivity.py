"""
Connectivity manager tests.

Built 2026-04-25 alongside the manager itself. Verifies the registry
shape, launchctl parsing, status decision tree (always_on / periodic /
http-degrade), action subprocess invocations, aggregation rollup, and
the CLI argparse routing.

Subprocess and HTTP are mocked at the module-level helper boundary
(`_run`, `_http_probe`) so tests never shell out or hit the network.

Reason-receipts on disabled services (2026-07-12): the stored-vs-inferred
reconciliation tests below never read the live services.jsonl — every
service_fold is built by hand or via sovereign_stack.service_ledger's own
pure functions against tmp_path fixtures.
"""

from __future__ import annotations

import json
import subprocess
import time
from unittest.mock import patch

import pytest

from sovereign_stack import connectivity as conn
from sovereign_stack import connectivity_cli as cli
from sovereign_stack import service_ledger as sl
from sovereign_stack.connectivity import (
    ENDPOINTS,
    KIND_ALWAYS_ON,
    KIND_PERIODIC,
    KIND_UNREGISTERED,
    RECONCILE_EXPECTED_DISABLED,
    RECONCILE_INCIDENT,
    RECONCILE_OK,
    RECONCILE_UNEXPECTED_RUNNING,
    STATUS_DEGRADED,
    STATUS_DOWN,
    STATUS_OK,
    STATUS_STALE,
    STATUS_UNKNOWN,
    Endpoint,
    EndpointStatus,
    aggregate,
    aggregate_reconciled,
    check_all,
    check_all_reconciled,
    check_status,
    check_unregistered_service,
    get_endpoint,
    parse_launchctl_print,
    reconcile_service,
    restart,
    start,
    stop,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a fake CompletedProcess matching subprocess.run's return shape."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ── Registry shape ──────────────────────────────────────────────────────────


class TestRegistry:
    def test_registry_non_empty(self):
        assert len(ENDPOINTS) > 0

    def test_all_endpoints_have_required_fields(self):
        for e in ENDPOINTS:
            assert e.name, f"endpoint missing name: {e}"
            assert e.kind in (KIND_ALWAYS_ON, KIND_PERIODIC), f"unknown kind {e.kind} on {e.name}"
            assert e.description, f"endpoint missing description: {e.name}"

    def test_periodic_endpoints_have_cadence(self):
        for e in ENDPOINTS:
            if e.kind == KIND_PERIODIC:
                assert e.cadence_seconds and e.cadence_seconds > 0, (
                    f"periodic endpoint {e.name} missing cadence_seconds"
                )

    def test_unique_names(self):
        names = [e.name for e in ENDPOINTS]
        assert len(names) == len(set(names)), f"duplicate names: {names}"

    def test_unique_labels(self):
        labels = [e.label for e in ENDPOINTS if e.label]
        assert len(labels) == len(set(labels)), f"duplicate labels: {labels}"

    def test_get_endpoint_by_name(self):
        e = get_endpoint("sse")
        assert e.name == "sse"

    def test_get_endpoint_unknown_raises(self):
        with pytest.raises(KeyError):
            get_endpoint("definitely-not-a-real-name")


# ── launchctl parsing ───────────────────────────────────────────────────────


class TestLaunchctlParse:
    def test_parse_running_service(self):
        text = """com.templetwo.sovereign-bridge = {
            active count = 1
            state = running
            pid = 1456
            last exit code = 0
            program = /usr/bin/python3
        }"""
        parsed = parse_launchctl_print(text)
        assert parsed["state"] == "running"
        assert parsed["pid"] == 1456
        assert parsed["last_exit_code"] == 0

    def test_parse_not_running_service(self):
        text = """com.templetwo.comms-listener = {
            active count = 0
            state = not running
            program = /bin/bash
        }"""
        parsed = parse_launchctl_print(text)
        assert parsed["state"] == "not"  # only first token after =
        # The full state "not running" gets cut on whitespace; we accept
        # the conservative parse and check via the status decision tree.

    def test_parse_negative_exit_code(self):
        text = """com.example = {
            state = running
            pid = 9999
            last exit code = -15
        }"""
        parsed = parse_launchctl_print(text)
        assert parsed["last_exit_code"] == -15

    def test_parse_missing_fields_returns_none(self):
        parsed = parse_launchctl_print("nothing useful here")
        assert parsed["state"] is None
        assert parsed["pid"] is None
        assert parsed["last_exit_code"] is None


# ── HTTP probe ──────────────────────────────────────────────────────────────


class TestHttpProbe:
    def test_probe_success(self):
        # Real urllib here would fail; we test the helper structure.
        with patch.object(conn.urllib.request, "urlopen") as mock_open:

            class FakeResp:
                status = 200

                def read(self, n):
                    return b'{"ok": true}'

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

            mock_open.return_value = FakeResp()
            result = conn._http_probe("http://x")
        assert result["http_status"] == 200
        assert "ok" in result["body"]
        assert result["error"] is None

    def test_probe_connection_refused(self):
        import urllib.error

        with patch.object(
            conn.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            result = conn._http_probe("http://x")
        assert result["http_status"] is None
        assert "url_error" in result["error"]

    def test_probe_http_404(self):
        import urllib.error

        # HTTPError carries a status code AND is treated as "got a response"
        # not as an error from the probe's perspective.
        err = urllib.error.HTTPError(
            url="http://x",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )
        with patch.object(conn.urllib.request, "urlopen", side_effect=err):
            result = conn._http_probe("http://x")
        assert result["http_status"] == 404
        assert result["error"] is None


# ── check_status: always_on logic ───────────────────────────────────────────


class TestCheckStatusAlwaysOn:
    def _ep(self, **kw):
        defaults = {
            "name": "t",
            "label": "com.templetwo.test",
            "kind": KIND_ALWAYS_ON,
            "description": "test",
            "health_url": None,
        }
        defaults.update(kw)
        return Endpoint(**defaults)

    def test_running_no_http_probe_is_ok(self):
        ep = self._ep()
        with patch.object(
            conn,
            "_launchctl_print_text",
            return_value="state = running\npid = 100\n",
        ):
            s = check_status(ep)
        assert s.status == STATUS_OK
        assert s.pid == 100

    def test_not_loaded_is_down(self):
        ep = self._ep()
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_status(ep)
        assert s.status == STATUS_DOWN

    def test_running_with_failed_http_is_degraded(self):
        ep = self._ep(health_url="http://127.0.0.1:99/health")
        with (
            patch.object(
                conn,
                "_launchctl_print_text",
                return_value="state = running\npid = 100\n",
            ),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "url_error: refused"},
            ),
        ):
            s = check_status(ep)
        assert s.status == STATUS_DEGRADED
        assert s.http_ok is False

    def test_running_with_ok_http_is_ok(self):
        ep = self._ep(health_url="http://x")
        with (
            patch.object(
                conn,
                "_launchctl_print_text",
                return_value="state = running\npid = 100\n",
            ),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": 200, "body": "{}", "error": None},
            ),
        ):
            s = check_status(ep)
        assert s.status == STATUS_OK
        assert s.http_ok is True

    def test_health_match_substring_required(self):
        ep = self._ep(health_url="http://x", health_match="healthy")
        with (
            patch.object(
                conn,
                "_launchctl_print_text",
                return_value="state = running\npid = 100\n",
            ),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": 200, "body": '{"status":"DEGRADED"}', "error": None},
            ),
        ):
            s = check_status(ep)
        # Body 200 OK but missing the required match → degraded.
        assert s.status == STATUS_DEGRADED
        assert s.http_ok is False

    def test_self_probe_skipped_when_pid_matches_own_process(self):
        """When the service PID == our PID, skip the HTTP probe entirely.

        Probing our own port via blocking urllib inside an async event loop
        deadlocks. The fact that this tool call is executing proves the
        service is alive — no HTTP round-trip needed.
        """
        import os

        my_pid = os.getpid()
        ep = self._ep(health_url="http://127.0.0.1:3434/health", health_match="healthy")
        with (
            patch.object(
                conn,
                "_launchctl_print_text",
                return_value=f"state = running\npid = {my_pid}\n",
            ),
            patch.object(conn, "_http_probe") as mock_probe,
        ):
            s = check_status(ep)
        mock_probe.assert_not_called()
        assert s.status == STATUS_OK
        assert s.http_ok is True
        assert any("self-probe skipped" in n for n in s.notes)

    def test_non_self_probe_still_runs_http_check(self):
        """When the service PID != our PID, the HTTP probe runs normally."""
        ep = self._ep(health_url="http://127.0.0.1:3434/health")
        with (
            patch.object(
                conn,
                "_launchctl_print_text",
                return_value="state = running\npid = 99999\n",
            ),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "url_error: refused"},
            ) as mock_probe,
        ):
            s = check_status(ep)
        mock_probe.assert_called_once()
        assert s.status == STATUS_DEGRADED

    def test_http_healthy_with_launchctl_not_loaded_is_ok(self):
        """Probe is the source of truth: an HTTP-healthy always-on service
        is UP even when its launchctl label fails to load.

        Regression for the HQ-pulse false-DEGRADED loop — launchctl
        enumeration missed the label every 15 minutes while the service
        answered its health URL the whole time.
        """
        ep = self._ep(health_url="http://x")
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": 200, "body": "{}", "error": None},
            ),
        ):
            s = check_status(ep)
        assert s.status == STATUS_OK
        assert s.http_ok is True
        # launchctl detail stays in the report as supplementary info.
        assert any("not loaded" in n for n in s.notes)
        assert any("overrides launchctl" in n for n in s.notes)

    def test_http_healthy_overrides_unrecognized_launchctl_state(self):
        """Probe success wins regardless of what launchctl enumerated."""
        ep = self._ep(health_url="http://x")
        with (
            patch.object(
                conn,
                "_launchctl_print_text",
                return_value="state = waiting\n",
            ),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": 200, "body": "{}", "error": None},
            ),
        ):
            s = check_status(ep)
        assert s.status == STATUS_OK
        assert s.http_ok is True

    def test_http_failed_with_launchctl_not_loaded_stays_down(self):
        """No probe success, no launchctl — still DOWN, not upgraded."""
        ep = self._ep(health_url="http://x")
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "url_error: refused"},
            ),
        ):
            s = check_status(ep)
        assert s.status == STATUS_DOWN
        assert s.http_ok is False


# ── check_status: periodic logic ────────────────────────────────────────────


class TestCheckStatusPeriodic:
    def _ep(self, log_path, cadence=300):
        return Endpoint(
            name="lst",
            label="com.templetwo.test",
            kind=KIND_PERIODIC,
            description="periodic test",
            cadence_seconds=cadence,
            log_path=log_path,
        )

    def test_recent_log_is_ok(self, tmp_path):
        log = tmp_path / "log.txt"
        log.write_text("recent")
        ep = self._ep(str(log), cadence=300)
        # Log just touched; well within 2x cadence (600s).
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_status(ep, now=time.time())
        assert s.status == STATUS_OK
        assert s.log_age_seconds is not None and s.log_age_seconds < 60

    def test_stale_log_is_stale(self, tmp_path):
        log = tmp_path / "log.txt"
        log.write_text("ancient")
        ep = self._ep(str(log), cadence=60)
        # Now is 1000s after mtime → way beyond 2x60=120 tolerance.
        future_now = log.stat().st_mtime + 1000
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_status(ep, now=future_now)
        assert s.status == STATUS_STALE

    def test_missing_log_is_stale(self, tmp_path):
        ep = self._ep(str(tmp_path / "never_existed.log"))
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_status(ep)
        assert s.status == STATUS_STALE
        assert any("missing" in n for n in s.notes)

    def test_stale_periodic_not_upgraded_by_http_probe(self, tmp_path):
        """Probe authority is scoped to always-on: a stale periodic job
        stays STALE — its health signal is the log cadence, not HTTP."""
        log = tmp_path / "log.txt"
        log.write_text("ancient")
        ep = Endpoint(
            name="lst",
            label="com.templetwo.test",
            kind=KIND_PERIODIC,
            description="periodic test",
            cadence_seconds=60,
            log_path=str(log),
            health_url="http://x",
        )
        future_now = log.stat().st_mtime + 1000
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": 200, "body": "{}", "error": None},
            ),
        ):
            s = check_status(ep, now=future_now)
        assert s.status == STATUS_STALE


# ── check_status: no label (e.g., external service we just probe HTTP) ──────


class TestCheckStatusHttpOnly:
    def test_probe_succeeds_without_label(self):
        ep = Endpoint(
            name="ext",
            label=None,
            kind=KIND_ALWAYS_ON,
            description="external service",
            health_url="http://x",
        )
        with patch.object(
            conn,
            "_http_probe",
            return_value={"http_status": 200, "body": "ok", "error": None},
        ):
            s = check_status(ep)
        # No launchctl label → launchctl can say nothing; the successful
        # health probe is authoritative for an always-on service.
        assert s.http_ok is True
        assert s.status == STATUS_OK


# ── Action helpers (subprocess args) ────────────────────────────────────────


class TestActions:
    def _ep(self):
        return Endpoint(
            name="t",
            label="com.templetwo.test",
            kind=KIND_ALWAYS_ON,
            description="t",
        )

    def test_restart_invokes_kickstart_with_k(self):
        ep = self._ep()
        captured = {}

        def fake_run(cmd, timeout=5.0):
            captured["cmd"] = cmd
            return _make_completed(returncode=0)

        with patch.object(conn, "_run", side_effect=fake_run):
            r = restart(ep)
        assert r.ok is True
        assert "kickstart" in captured["cmd"]
        assert "-k" in captured["cmd"]
        assert any("com.templetwo.test" in c for c in captured["cmd"])

    def test_start_invokes_kickstart_no_k(self):
        ep = self._ep()
        captured = {}

        def fake_run(cmd, timeout=5.0):
            captured["cmd"] = cmd
            return _make_completed(returncode=0)

        with patch.object(conn, "_run", side_effect=fake_run):
            r = start(ep)
        assert r.ok is True
        assert "kickstart" in captured["cmd"]
        assert "-k" not in captured["cmd"]

    def test_stop_invokes_kill_sigterm(self):
        ep = self._ep()
        captured = {}

        def fake_run(cmd, timeout=5.0):
            captured["cmd"] = cmd
            return _make_completed(returncode=0)

        with patch.object(conn, "_run", side_effect=fake_run):
            r = stop(ep)
        assert r.ok is True
        assert "kill" in captured["cmd"]
        assert "SIGTERM" in captured["cmd"]

    def test_action_returns_failure_on_nonzero(self):
        ep = self._ep()
        with patch.object(
            conn,
            "_run",
            return_value=_make_completed(returncode=1, stderr="failed"),
        ):
            r = restart(ep)
        assert r.ok is False
        assert r.returncode == 1
        assert "failed" in r.stderr

    def test_action_on_no_label_endpoint(self):
        ep = Endpoint(
            name="x",
            label=None,
            kind=KIND_ALWAYS_ON,
            description="",
        )
        r = restart(ep)
        assert r.ok is False
        assert "no launchctl label" in r.stderr


# ── Aggregation ─────────────────────────────────────────────────────────────


class TestAggregate:
    def _s(self, status):
        return EndpointStatus(name="x", label="x", kind=KIND_ALWAYS_ON, status=status)

    def test_all_ok_overall_ok(self):
        agg = aggregate([self._s(STATUS_OK), self._s(STATUS_OK)])
        assert agg["overall"] == STATUS_OK
        assert agg["counts"][STATUS_OK] == 2

    def test_any_down_overall_down(self):
        agg = aggregate(
            [
                self._s(STATUS_OK),
                self._s(STATUS_OK),
                self._s(STATUS_DOWN),
            ]
        )
        assert agg["overall"] == STATUS_DOWN

    def test_degraded_without_down_overall_degraded(self):
        agg = aggregate([self._s(STATUS_OK), self._s(STATUS_DEGRADED)])
        assert agg["overall"] == STATUS_DEGRADED

    def test_stale_counts_as_degraded(self):
        agg = aggregate([self._s(STATUS_OK), self._s(STATUS_STALE)])
        assert agg["overall"] == STATUS_DEGRADED

    def test_unknown_only_counts_as_degraded(self):
        agg = aggregate([self._s(STATUS_UNKNOWN)])
        assert agg["overall"] == STATUS_DEGRADED


# ── check_all integration ──────────────────────────────────────────────────


class TestCheckAll:
    def test_check_all_returns_one_per_endpoint(self):
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "mocked"},
            ),
        ):
            results = check_all()
        assert len(results) == len(ENDPOINTS)
        names = {r.name for r in results}
        assert names == {e.name for e in ENDPOINTS}


# ── CLI ─────────────────────────────────────────────────────────────────────


class TestCli:
    def test_status_default_returns_2_when_degraded(self, capsys):
        # All endpoints unknown/down because we mock everything to fail.
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "mocked"},
            ),
        ):
            rc = cli.main(["status"])
        assert rc == 2

    def test_status_json_outputs_aggregate(self, capsys):
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "mocked"},
            ),
        ):
            cli.main(["status", "--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "overall" in data
        assert "endpoints" in data
        assert len(data["endpoints"]) == len(ENDPOINTS)

    def test_list_command(self, capsys):
        rc = cli.main(["list"])
        assert rc == 0
        out = capsys.readouterr().out
        # Each registered endpoint name appears in the listing.
        for e in ENDPOINTS:
            assert e.name in out

    def test_unknown_endpoint_exits(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["restart", "definitely-not-real"])

    def test_restart_all_loops_endpoints(self):
        calls = []

        def fake_run(cmd, timeout=5.0):
            calls.append(cmd)
            return _make_completed(returncode=0)

        with patch.object(conn, "_run", side_effect=fake_run):
            rc = cli.main(["restart", "all"])
        assert rc == 0
        # One subprocess invocation per labeled endpoint.
        labeled = [e for e in ENDPOINTS if e.label]
        assert len(calls) == len(labeled)


# ── reconcile_service: stored ledger state vs inferred launchctl/HTTP read ──


class TestReconcileService:
    def _status(self, status, label="com.templetwo.test"):
        return EndpointStatus(name="t", label=label, kind=KIND_ALWAYS_ON, status=status)

    def test_no_fold_leaves_both_none(self):
        """Default behavior for every existing caller (check_all(), the CLI,
        monitor.py) that never passes a service_fold — zero regression."""
        stored, reconciliation = reconcile_service(self._status(STATUS_DOWN), None)
        assert stored is None
        assert reconciliation is None

    def test_endpoint_with_no_label_cannot_reconcile(self):
        status = self._status(STATUS_DOWN, label=None)
        stored, reconciliation = reconcile_service(status, {"x": {"state": "disabled"}})
        assert stored is None
        assert reconciliation is None

    def test_stored_disabled_inferred_down_is_expected(self):
        """PROVE IT (1/2): a stored-disabled + inferred-down service reads
        as FINE, not as an incident."""
        fold = {"com.templetwo.test": {"state": "disabled"}}
        stored, reconciliation = reconcile_service(self._status(STATUS_DOWN), fold)
        assert stored == "disabled"
        assert reconciliation == RECONCILE_EXPECTED_DISABLED

    def test_stored_enabled_inferred_down_is_incident(self):
        """PROVE IT (2/2): a stored-enabled + inferred-down service reads
        as an INCIDENT — today those two cases are indistinguishable."""
        fold = {"com.templetwo.test": {"state": "enabled"}}
        stored, reconciliation = reconcile_service(self._status(STATUS_DOWN), fold)
        assert stored == "enabled"
        assert reconciliation == RECONCILE_INCIDENT

    def test_no_record_inferred_down_defaults_to_incident(self):
        """No ledger record at all is treated the same as "enabled" — the
        ledger only records departures from the default-on assumption."""
        stored, reconciliation = reconcile_service(self._status(STATUS_DOWN), {})
        assert stored is None
        assert reconciliation == RECONCILE_INCIDENT

    def test_stored_disabled_inferred_running_is_unexpected(self):
        """The fourth cell: the ledger says disabled but the service answers
        anyway — the ledger is stale, or someone re-enabled it without
        recording why. Never silently OK."""
        fold = {"com.templetwo.test": {"state": "disabled"}}
        stored, reconciliation = reconcile_service(self._status(STATUS_OK), fold)
        assert stored == "disabled"
        assert reconciliation == RECONCILE_UNEXPECTED_RUNNING

    def test_stored_enabled_inferred_running_is_ok(self):
        fold = {"com.templetwo.test": {"state": "enabled"}}
        stored, reconciliation = reconcile_service(self._status(STATUS_OK), fold)
        assert stored == "enabled"
        assert reconciliation == RECONCILE_OK

    def test_degraded_counts_as_running_for_reconciliation(self):
        fold = {"com.templetwo.test": {"state": "enabled"}}
        stored, reconciliation = reconcile_service(self._status(STATUS_DEGRADED), fold)
        assert reconciliation == RECONCILE_OK

    @pytest.mark.parametrize("ambiguous_status", [STATUS_STALE, STATUS_UNKNOWN])
    def test_ambiguous_inferred_status_refuses_to_reconcile(self, ambiguous_status):
        """STALE/UNKNOWN are not confident enough to bucket into ok/incident
        — forcing a guess would be false confidence, not a fix. stored_state
        is still surfaced even though reconciliation abstains."""
        fold = {"com.templetwo.test": {"state": "disabled"}}
        stored, reconciliation = reconcile_service(self._status(ambiguous_status), fold)
        assert stored == "disabled"
        assert reconciliation is None


# ── check_status: reconciliation wired end-to-end via service_fold ─────────


class TestCheckStatusReconciliation:
    def _ep(self, **kw):
        defaults = {
            "name": "t",
            "label": "com.templetwo.test",
            "kind": KIND_ALWAYS_ON,
            "description": "test",
        }
        defaults.update(kw)
        return Endpoint(**defaults)

    def test_default_omits_reconciliation(self):
        """No service_fold passed -> identical to pre-ledger behavior."""
        ep = self._ep()
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_status(ep)
        assert s.status == STATUS_DOWN
        assert s.stored_state is None
        assert s.reconciliation is None

    def test_stored_disabled_plus_inferred_down_reads_fine(self):
        ep = self._ep()
        fold = {"com.templetwo.test": {"state": "disabled"}}
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_status(ep, service_fold=fold)
        assert s.status == STATUS_DOWN
        assert s.stored_state == "disabled"
        assert s.reconciliation == RECONCILE_EXPECTED_DISABLED

    def test_stored_enabled_plus_inferred_down_reads_as_incident(self):
        ep = self._ep()
        fold = {"com.templetwo.test": {"state": "enabled"}}
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_status(ep, service_fold=fold)
        assert s.status == STATUS_DOWN
        assert s.stored_state == "enabled"
        assert s.reconciliation == RECONCILE_INCIDENT

    def test_check_all_passes_fold_through(self):
        fold = {"com.templetwo.sovereign-sse": {"state": "disabled"}}
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "mocked"},
            ),
        ):
            results = check_all(service_fold=fold)
        sse = next(r for r in results if r.name == "sse")
        assert sse.stored_state == "disabled"
        assert sse.reconciliation == RECONCILE_EXPECTED_DISABLED


# ── check_unregistered_service: ledger knows it, ENDPOINTS does not ────────


class TestCheckUnregisteredService:
    def test_not_loaded_and_stored_disabled_reads_fine(self):
        fold = {"com.templetwo.sovereign.dream": {"state": "disabled"}}
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_unregistered_service("com.templetwo.sovereign.dream", service_fold=fold)
        assert s.kind == KIND_UNREGISTERED
        assert s.status == STATUS_DOWN
        assert s.reconciliation == RECONCILE_EXPECTED_DISABLED

    def test_loaded_and_running_but_stored_disabled_is_unexpected(self):
        fold = {"com.templetwo.sovereign.dream": {"state": "disabled"}}
        with patch.object(
            conn, "_launchctl_print_text", return_value="state = running\npid = 42\n"
        ):
            s = check_unregistered_service("com.templetwo.sovereign.dream", service_fold=fold)
        assert s.status == STATUS_OK
        assert s.reconciliation == RECONCILE_UNEXPECTED_RUNNING

    def test_no_fold_record_defaults_to_incident_when_down(self):
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_unregistered_service("com.templetwo.ghost", service_fold={})
        assert s.reconciliation == RECONCILE_INCIDENT

    def test_surfaces_unrunnable_module_from_plist(self, tmp_path):
        """Record this, don't fix it: the dream plist invokes a module that
        does not exist on this checkout. The service is not merely paused —
        it's unrunnable — and that must show up in the notes even though
        the reconciliation itself reads EXPECTED_DISABLED."""
        plist = tmp_path / "dream.plist"
        plist.write_text(
            "<plist><dict><key>ProgramArguments</key><array>"
            "<string>python</string><string>-m</string>"
            "<string>sovereign_stack.daemons.dream_daemon</string>"
            "</array></dict></plist>"
        )
        fold = sl.fold_service_state(
            [
                sl.build_service_record(
                    action="disable",
                    service="com.templetwo.sovereign.dream",
                    reason="manual eval phase",
                    plist_path=str(plist),
                )
            ]
        )
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_unregistered_service("com.templetwo.sovereign.dream", service_fold=fold)
        assert s.reconciliation == RECONCILE_EXPECTED_DISABLED
        assert any("unrunnable" in n for n in s.notes)
        assert any("dream_daemon" in n for n in s.notes)

    def test_no_unrunnable_note_when_module_is_real(self, tmp_path):
        plist = tmp_path / "synthesis.plist"
        plist.write_text(
            "<plist><dict><key>ProgramArguments</key><array>"
            "<string>python</string><string>-m</string>"
            "<string>sovereign_stack.daemons.synthesis_daemon</string>"
            "</array></dict></plist>"
        )
        fold = sl.fold_service_state(
            [
                sl.build_service_record(
                    action="disable",
                    service="com.templetwo.sovereign.synthesis",
                    reason="testing",
                    plist_path=str(plist),
                )
            ]
        )
        with patch.object(conn, "_launchctl_print_text", return_value=None):
            s = check_unregistered_service("com.templetwo.sovereign.synthesis", service_fold=fold)
        assert not any("unrunnable" in n for n in s.notes)


# ── check_all_reconciled: union of ENDPOINTS and ledger-only labels ────────


class TestCheckAllReconciled:
    def test_empty_fold_matches_check_all(self):
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "mocked"},
            ),
        ):
            statuses = check_all_reconciled(service_fold={})
        assert len(statuses) == len(ENDPOINTS)

    def test_ledger_only_label_is_appended(self):
        fold = {
            "com.templetwo.sovereign.dream": {
                "state": "disabled",
                "reason": "manual eval phase",
            }
        }
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "mocked"},
            ),
        ):
            statuses = check_all_reconciled(service_fold=fold)
        assert len(statuses) == len(ENDPOINTS) + 1
        dream = next(s for s in statuses if s.label == "com.templetwo.sovereign.dream")
        assert dream.kind == KIND_UNREGISTERED
        assert dream.reconciliation == RECONCILE_EXPECTED_DISABLED

    def test_registered_endpoint_in_fold_not_duplicated(self):
        """A ledger entry keyed on a label ENDPOINTS already knows about
        (e.g. a future registered-but-disabled service) must reconcile
        in place, not spawn a second synthetic entry."""
        fold = {"com.templetwo.sovereign-sse": {"state": "enabled"}}
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "mocked"},
            ),
        ):
            statuses = check_all_reconciled(service_fold=fold)
        assert len(statuses) == len(ENDPOINTS)

    def test_default_service_fold_loads_from_default_path(self, tmp_path, monkeypatch):
        ledger = tmp_path / "services.jsonl"
        sl.append_service_record(
            ledger,
            sl.build_service_record(action="disable", service="com.templetwo.ghost", reason="test"),
        )
        monkeypatch.setattr(sl, "default_services_path", lambda: ledger)
        with (
            patch.object(conn, "_launchctl_print_text", return_value=None),
            patch.object(
                conn,
                "_http_probe",
                return_value={"http_status": None, "body": "", "error": "mocked"},
            ),
        ):
            statuses = check_all_reconciled()
        ghost = next(s for s in statuses if s.label == "com.templetwo.ghost")
        assert ghost.reconciliation == RECONCILE_EXPECTED_DISABLED


# ── aggregate_reconciled: the rollup that makes reconciliation matter ──────


class TestAggregateReconciled:
    def _s(self, status, reconciliation=None, name="x"):
        return EndpointStatus(
            name=name,
            label=name,
            kind=KIND_ALWAYS_ON,
            status=status,
            reconciliation=reconciliation,
        )

    def test_stored_disabled_down_service_reads_overall_fine(self):
        """PROVE IT, end-to-end (1/2): a disabled-down service must leave
        overall health green — not just the per-service label, the actual
        rollup a dashboard or alert would read."""
        statuses = [
            self._s(STATUS_OK, name="bridge"),
            self._s(STATUS_DOWN, reconciliation=RECONCILE_EXPECTED_DISABLED, name="dream"),
        ]
        agg = aggregate_reconciled(statuses)
        assert agg["overall"] == STATUS_OK
        assert agg["counts"]["expected_disabled"] == 1
        assert "down" not in agg["counts"]

    def test_stored_enabled_down_service_reads_overall_incident(self):
        """PROVE IT, end-to-end (2/2): an enabled-down service must turn the
        rollup red, exactly as it does today."""
        statuses = [
            self._s(STATUS_OK, name="bridge"),
            self._s(STATUS_DOWN, reconciliation=RECONCILE_INCIDENT, name="sse"),
        ]
        agg = aggregate_reconciled(statuses)
        assert agg["overall"] == STATUS_DOWN

    def test_down_with_no_reconciliation_still_counts_as_incident(self):
        """A DOWN status with reconciliation never computed (no fold passed
        for that endpoint) must not silently read as fine."""
        statuses = [self._s(STATUS_DOWN, reconciliation=None, name="sse")]
        agg = aggregate_reconciled(statuses)
        assert agg["overall"] == STATUS_DOWN

    def test_unexpected_running_flags_at_least_degraded(self):
        """The fourth cell must never read as plain OK — a stale or
        bypassed ledger is itself worth surfacing."""
        statuses = [
            self._s(STATUS_OK, name="bridge"),
            self._s(STATUS_OK, reconciliation=RECONCILE_UNEXPECTED_RUNNING, name="dream"),
        ]
        agg = aggregate_reconciled(statuses)
        assert agg["overall"] == STATUS_DEGRADED

    def test_all_ok_or_expected_disabled_is_overall_ok(self):
        statuses = [
            self._s(STATUS_OK, reconciliation=RECONCILE_OK, name="bridge"),
            self._s(STATUS_DOWN, reconciliation=RECONCILE_EXPECTED_DISABLED, name="dream"),
            self._s(STATUS_DOWN, reconciliation=RECONCILE_EXPECTED_DISABLED, name="tunnel-legacy"),
        ]
        agg = aggregate_reconciled(statuses)
        assert agg["overall"] == STATUS_OK

    def test_plain_aggregate_is_unaffected_by_reconciliation_field(self):
        """aggregate() itself must be byte-for-byte the same function it was
        before this ledger existed — existing callers (CLI, monitor.py) see
        no behavior change."""
        statuses = [
            self._s(STATUS_OK, name="bridge"),
            self._s(STATUS_DOWN, reconciliation=RECONCILE_EXPECTED_DISABLED, name="dream"),
        ]
        agg = aggregate(statuses)
        # The old function has no idea reconciliation exists: a down service
        # still reads down, even though it's actually fine.
        assert agg["overall"] == STATUS_DOWN
        assert agg["counts"][STATUS_DOWN] == 1


# ── End-to-end with the real seed data ──────────────────────────────────────


class TestSeedDataEndToEnd:
    def test_both_seeded_services_read_expected_disabled(self, tmp_path):
        """The actual seed_known_disabled_services() records, run through
        the real reconciliation path with launchctl mocked to "not loaded"
        for both — the honest state of this machine today. Uses the real
        find_spec check against this checkout's actual package layout, not
        a mock: dream_daemon.py genuinely doesn't exist here."""
        ledger = tmp_path / "services.jsonl"
        for record in sl.seed_known_disabled_services(timestamp="2026-07-12T00:00:00+00:00"):
            sl.append_service_record(ledger, record)
        fold = sl.fold_service_state(sl.load_service_records(ledger))

        with patch.object(conn, "_launchctl_print_text", return_value=None):
            dream = check_unregistered_service("com.templetwo.sovereign.dream", service_fold=fold)
            tunnel = check_unregistered_service("com.templetwo.sovereign-tunnel", service_fold=fold)

        assert dream.reconciliation == RECONCILE_EXPECTED_DISABLED
        assert tunnel.reconciliation == RECONCILE_EXPECTED_DISABLED
        assert any("unrunnable" in n and "dream_daemon" in n for n in dream.notes)
        # The tunnel record carries no plist_path — nothing to check, nothing
        # spurious in its notes.
        assert not any("unrunnable" in n for n in tunnel.notes)

    def test_both_seeded_services_invisible_to_bare_check_all(self):
        """Neither seeded service is a registered Endpoint — check_all()
        (the pre-ledger surface) still can't see them at all, which is the
        exact invisibility this item exists to close via check_all_reconciled."""
        names = {e.name for e in ENDPOINTS}
        labels = {e.label for e in ENDPOINTS if e.label}
        assert "com.templetwo.sovereign.dream" not in names | labels
        assert "com.templetwo.sovereign-tunnel" not in names | labels
