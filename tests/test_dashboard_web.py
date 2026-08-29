"""
Tests for sovereign_stack.dashboard_web — the HTTP dashboard server.

Strategy: spin up the server on an ephemeral port in a thread, hit the
endpoints with stdlib urllib, validate the responses. No browser needed.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest

from sovereign_stack import connectivity as conn
from sovereign_stack import dashboard_readers as readers
from sovereign_stack import dashboard_web as web


@pytest.fixture(autouse=True)
def _no_external_probes(monkeypatch):
    """Keep build_snapshot()'s Console-v2 readers off the live machine.

    Two of the six v2 sections leave the process — fetch_bridge_heartbeat()
    GETs :8100/api/heartbeat and read_guardian() shells out to lsof/pgrep.
    SOVEREIGN_ROOT cannot redirect either, so without this every test in
    this file would probe the operator's real bridge and enumerate their
    real listening sockets. Both are patched on the MODULE object, which is
    what build_snapshot() looks up, and the TTL caches are dropped on both
    sides so no result can leak across tests in either direction.
    """
    readers.reset_caches()
    monkeypatch.setattr(readers, "fetch_bridge_heartbeat", lambda: None)
    monkeypatch.setattr(readers, "read_guardian", lambda: None)
    yield
    readers.reset_caches()


@pytest.fixture
def running_server(monkeypatch, tmp_path):
    """Start the web server on an ephemeral port; tear down after."""
    # Patch connectivity calls so the server doesn't actually shell out
    # to launchctl during tests.
    monkeypatch.setattr(
        conn,
        "_launchctl_print_text",
        lambda label: None,
    )
    monkeypatch.setattr(
        conn,
        "_http_probe",
        lambda url, timeout=2.0: {"http_status": None, "body": "", "error": "mocked"},
    )
    # build_snapshot() now reads watchman's spool.jsonl + watchman.log (via
    # dashboard.build_watchman_summary) in addition to the pre-existing
    # chronicle/honks/halts/decisions reads — isolate the whole snapshot
    # data layer to a tmp root so every request this fixture serves stays
    # off the live ~/.sovereign tree, watchman spool included.
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))

    server = web.serve(host="127.0.0.1", port=0)  # 0 = ephemeral
    host, port = server.server_address[:2]
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()
    # Give the server a tick to come up.
    time.sleep(0.05)
    yield (host, port)
    server.shutdown()


def _get(url: str, timeout: float = 2.0) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read(), r.headers.get("Content-Type", "")


# ── Endpoints ───────────────────────────────────────────────────────────────


class TestEndpoints:
    def test_root_serves_html(self, running_server):
        host, port = running_server
        status, body, ct = _get(f"http://{host}:{port}/")
        assert status == 200
        assert "text/html" in ct
        assert b"Sovereign Stack" in body

    def test_snapshot_returns_json(self, running_server):
        host, port = running_server
        status, body, ct = _get(f"http://{host}:{port}/snapshot.json")
        assert status == 200
        assert "application/json" in ct
        data = json.loads(body)
        assert "connectivity" in data
        assert "halts_count" in data
        assert "decisions_count" in data
        assert "unacked_honks" in data

    def test_health_endpoint(self, running_server):
        host, port = running_server
        status, body, _ = _get(f"http://{host}:{port}/health")
        assert status == 200
        data = json.loads(body)
        assert data["status"] == "healthy"

    def test_static_css(self, running_server):
        host, port = running_server
        status, body, ct = _get(f"http://{host}:{port}/static/style.css")
        assert status == 200
        assert "text/css" in ct
        # Sentinel content from style.css
        assert b"--bg" in body or b":root" in body

    def test_static_js(self, running_server):
        host, port = running_server
        status, body, ct = _get(f"http://{host}:{port}/static/app.js")
        assert status == 200
        assert "javascript" in ct.lower()

    def test_unknown_path_404(self, running_server):
        host, port = running_server
        try:
            urllib.request.urlopen(
                f"http://{host}:{port}/nonexistent",
                timeout=2.0,
            )
            pytest.fail("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404

    def test_path_traversal_blocked(self, running_server):
        host, port = running_server
        try:
            urllib.request.urlopen(
                f"http://{host}:{port}/static/../../etc/passwd",
                timeout=2.0,
            )
            pytest.fail("expected 404 for path traversal")
        except urllib.error.HTTPError as e:
            assert e.code == 404


# ── Snapshot builder ────────────────────────────────────────────────────────


class TestBuildSnapshot:
    def test_snapshot_shape(self, monkeypatch, tmp_path):
        monkeypatch.setattr(conn, "_launchctl_print_text", lambda label: None)
        monkeypatch.setattr(
            conn,
            "_http_probe",
            lambda url, timeout=2.0: {"http_status": None, "body": "", "error": "mocked"},
        )
        # build_snapshot() reads the filesystem directly (chronicle, honks,
        # halts, decisions, and now watchman's spool.jsonl/watchman.log) —
        # isolate to a tmp root so this test never touches the live tree.
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        snapshot = web.build_snapshot()
        assert "timestamp" in snapshot
        assert "connectivity" in snapshot
        assert "endpoints" in snapshot["connectivity"]
        assert isinstance(snapshot["unacked_honks"], int)
        assert isinstance(snapshot["listener_stale"], bool)

    def test_snapshot_carries_watchman_key_additively(self, tmp_path, monkeypatch):
        """`watchman` is appended, not substituted — every pre-existing key
        (the 9 keys through service_telemetry) must still be present and
        typed as before, per build_snapshot's additive-only discipline."""
        monkeypatch.setattr(conn, "_launchctl_print_text", lambda label: None)
        monkeypatch.setattr(
            conn,
            "_http_probe",
            lambda url, timeout=2.0: {"http_status": None, "body": "", "error": "mocked"},
        )
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        snapshot = web.build_snapshot()

        for key in (
            "timestamp",
            "connectivity",
            "halts_count",
            "decisions_count",
            "unacked_honks",
            "listener_stale",
            "latest",
            "feed",
            "service_telemetry",
        ):
            assert key in snapshot, key

        assert "watchman" in snapshot
        wm = snapshot["watchman"]
        assert wm["sweeps"] == []  # tmp_path has no watchman/ dir at all
        assert wm["malformed_skipped"] == 0
        assert wm["summary"]["status"] == "unknown"
