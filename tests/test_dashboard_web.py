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
from sovereign_stack import dashboard_web as web


@pytest.fixture
def running_server(monkeypatch):
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
    def test_snapshot_shape(self, monkeypatch):
        monkeypatch.setattr(conn, "_launchctl_print_text", lambda label: None)
        monkeypatch.setattr(
            conn,
            "_http_probe",
            lambda url, timeout=2.0: {"http_status": None, "body": "", "error": "mocked"},
        )
        snapshot = web.build_snapshot()
        assert "timestamp" in snapshot
        assert "connectivity" in snapshot
        assert "endpoints" in snapshot["connectivity"]
        assert isinstance(snapshot["unacked_honks"], int)
        assert isinstance(snapshot["listener_stale"], bool)
