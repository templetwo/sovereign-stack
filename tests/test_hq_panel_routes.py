"""HQ cockpit Phase 2: the /hq/* routes, the vendor/ static dir, and the two
new snapshot keys. Server spun up on an ephemeral port like test_dashboard_web.py.
Every external reader is stubbed; nothing here touches the live ~/.sovereign tree,
the bridge, or a real claude."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from sovereign_stack import connectivity as conn
from sovereign_stack import dashboard_readers as readers
from sovereign_stack import dashboard_web as web

VENDOR = Path(web.__file__).parent / "dashboard_web_static" / "vendor"
VENDOR_SHA = {
    "xterm.js": "14903579ff54664cd72f8e8699e6961a6272c21863ec1c3b118cdc8af5d4a972",
    "xterm.css": "854a7c0fb70e8b1a083c16797ab827299fb18744f5ad34f227b48337e33293c6",
    "addon-fit.js": "ba3ea256ce0620a0992a197d6c9baea64823fc93d8da07a9e366ca9943c18527",
}


@pytest.fixture(autouse=True)
def _no_external_probes(monkeypatch):
    readers.reset_caches()
    monkeypatch.setattr(readers, "fetch_bridge_heartbeat", lambda: None)
    monkeypatch.setattr(readers, "read_guardian", lambda: None)
    yield
    readers.reset_caches()


@pytest.fixture
def running_server(monkeypatch, tmp_path):
    monkeypatch.setattr(conn, "_launchctl_print_text", lambda label: None)
    monkeypatch.setattr(
        conn,
        "_http_probe",
        lambda url, timeout=2.0: {"http_status": None, "body": "", "error": "m"},
    )
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    server = web.serve(host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.05)
    yield (host, port)
    server.shutdown()


def _req(url, method="GET", body=None, headers=None, timeout=3.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


class TestVendor:
    @pytest.mark.parametrize("name", sorted(VENDOR_SHA))
    def test_vendor_hashes_match_manifest(self, name):
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == VENDOR_SHA[name]

    def test_vendor_files_served(self, running_server):
        host, port = running_server
        pairs = (
            ("xterm.js", "javascript"),
            ("xterm.css", "text/css"),
            ("addon-fit.js", "javascript"),
        )
        for name, ct in pairs:
            status, body, hdr = _req(f"http://{host}:{port}/static/vendor/{name}")
            assert status == 200, name
            assert ct in hdr.get("Content-Type", "").lower()
            assert hashlib.sha256(body).hexdigest() == VENDOR_SHA[name]

    def test_vendor_md_and_licenses_present(self):
        assert (VENDOR / "VENDOR.md").exists()
        assert (VENDOR / "LICENSE-xterm").exists() and (VENDOR / "LICENSE-addon-fit").exists()

    @pytest.mark.parametrize(
        "bad", ["vendor/../app.js", "vendor/.hidden", "other/app.js", "vendor//xterm.js"]
    )
    def test_static_guard_still_rejects_traversal_and_other_subdirs(self, running_server, bad):
        host, port = running_server
        status, _, _ = _req(f"http://{host}:{port}/static/{bad}")
        assert status == 404


# ── Task 3: the /hq/* routes on the substrate ───────────────────────────────

ECHO_CMD = json.dumps(["/bin/sh", "-c", "echo READY; cat"])


@pytest.fixture
def panel_env(monkeypatch, tmp_path):
    """A fresh session per test, /bin/sh child, no real guard."""
    from sovereign_stack import hq_panel

    monkeypatch.setenv("HQ_PANEL_COMMAND", ECHO_CMD)
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    monkeypatch.setenv("HQ_PANEL_STATUS_INTERVAL_S", "3600")
    fresh = hq_panel.HQSession(seat_finder=lambda: None)
    monkeypatch.setattr(hq_panel, "_SESSION", fresh)
    yield fresh
    fresh._terminate_for_tests()


def _token(host, port):
    status, body, _ = _req(f"http://{host}:{port}/session")
    assert status == 200
    return json.loads(body)["csrf_token"]


def _good_headers(host, port, token):
    return {"Origin": f"http://{host}:{port}", "X-CSRF-Token": token}


def _start(host, port, token, cols=80, rows=24):
    return _req(
        f"http://{host}:{port}/hq/start",
        "POST",
        {"cols": cols, "rows": rows},
        _good_headers(host, port, token),
    )


class TestHqRoutes:
    def test_status_shape_and_no_cors(self, running_server, panel_env):
        host, port = running_server
        status, body, hdr = _req(f"http://{host}:{port}/hq/status")
        assert status == 200
        data = json.loads(body)
        assert data["running"] is False and data["listening"] is False
        assert "Access-Control-Allow-Origin" not in hdr

    def test_status_never_carries_token(self, running_server, panel_env):
        host, port = running_server
        token = _token(host, port)
        _, body, _ = _req(f"http://{host}:{port}/hq/status")
        assert token not in body.decode()
        _, snap, _ = _req(f"http://{host}:{port}/snapshot.json")
        assert token not in snap.decode()

    @pytest.mark.parametrize("route", ["/hq/start", "/hq/input", "/hq/resize"])
    def test_get_on_post_routes_is_405(self, running_server, panel_env, route):
        host, port = running_server
        status, _, hdr = _req(f"http://{host}:{port}{route}")
        assert status == 405 and "Access-Control-Allow-Origin" not in hdr

    @pytest.mark.parametrize("route", ["/hq/start", "/hq/input", "/hq/resize"])
    def test_post_without_token_is_403_and_spawns_nothing(self, running_server, panel_env, route):
        host, port = running_server
        hdr = {"Origin": f"http://{host}:{port}"}
        status, body, _ = _req(
            f"http://{host}:{port}{route}", "POST", {"cols": 80, "rows": 24}, hdr
        )
        assert status == 403 and b"csrf" in body
        assert panel_env.running is False

    def test_post_wrong_origin_is_403(self, running_server, panel_env):
        host, port = running_server
        token = _token(host, port)
        hdr = {"Origin": "http://evil.example", "X-CSRF-Token": token}
        status, body, _ = _req(
            f"http://{host}:{port}/hq/start", "POST", {"cols": 80, "rows": 24}, hdr
        )
        assert status == 403 and b"origin" in body and panel_env.running is False

    def test_wrong_host_is_403_on_every_hq_route(self, running_server, panel_env):
        host, port = running_server
        token = _token(host, port)
        hdr = {
            "Host": "stack.templetwo.com",
            "Origin": f"http://{host}:{port}",
            "X-CSRF-Token": token,
        }
        cases = (
            ("/hq/status", "GET", None),
            ("/hq/stream?token=" + token, "GET", None),
            ("/hq/start", "POST", {"cols": 80, "rows": 24}),
        )
        for route, method, body in cases:
            status, _, _ = _req(f"http://{host}:{port}{route}", method, body, hdr)
            assert status == 403, route

    def test_stream_refuses_without_token(self, running_server, panel_env):
        host, port = running_server
        hdr = {"Origin": f"http://{host}:{port}"}
        status, body, rh = _req(f"http://{host}:{port}/hq/stream", headers=hdr)
        assert status == 403 and "Access-Control-Allow-Origin" not in rh
        status, _, _ = _req(f"http://{host}:{port}/hq/stream?token=deadbeef", headers=hdr)
        assert status == 403

    def test_start_then_stream_then_input(self, running_server, panel_env):
        import base64

        host, port = running_server
        token = _token(host, port)
        status, body, hdr = _start(host, port, token, 100, 30)
        assert status == 200, body
        assert json.loads(body)["status"] == "started"
        assert "Access-Control-Allow-Origin" not in hdr
        url = f"http://{host}:{port}/hq/stream?token={token}&after=0"
        opener_req = urllib.request.Request(url, headers={"Origin": f"http://{host}:{port}"})
        with urllib.request.urlopen(opener_req, timeout=5) as r:
            assert "text/event-stream" in r.headers.get("Content-Type", "")
            assert r.headers.get("Access-Control-Allow-Origin") is None
            seen, deadline, hello, buf = b"", time.time() + 3, None, b""
            while time.time() < deadline and b"READY" not in seen:
                line = r.readline()
                buf += line
                if line == b"\n":
                    block = buf.decode()
                    buf = b""
                    if "event: hello" in block:
                        data_lines = [ln for ln in block.splitlines() if ln.startswith("data:")]
                        hello = json.loads(data_lines[0][5:])
                    else:
                        for ln in block.splitlines():
                            if ln.startswith("data:"):
                                seen += base64.b64decode(ln[5:].strip())
            assert hello is not None and hello["running"] is True
            assert b"READY" in seen
        payload = {"data": base64.b64encode(b"pong\n").decode()}
        status, body, _ = _req(
            f"http://{host}:{port}/hq/input", "POST", payload, _good_headers(host, port, token)
        )
        assert status == 200 and json.loads(body)["bytes"] == 5
        deadline = time.time() + 3
        while time.time() < deadline:
            chunks, _ = panel_env.replay(0)
            if b"pong" in b"".join(c for _, c in chunks):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("input did not reach the child")
        status, body, _ = _req(
            f"http://{host}:{port}/hq/resize",
            "POST",
            {"cols": 120, "rows": 40},
            _good_headers(host, port, token),
        )
        assert status == 200

    def test_second_start_is_409_and_spawns_nothing(self, running_server, panel_env):
        host, port = running_server
        token = _token(host, port)
        _start(host, port, token)
        pid1 = panel_env.status()["pid"]
        status, body, _ = _start(host, port, token)
        assert status == 409 and b"already running" in body
        assert panel_env.status()["pid"] == pid1

    def test_start_refused_409_when_a_seat_is_live(self, running_server, panel_env):
        host, port = running_server
        seat = {"pid": 77, "comm": "claude", "cwd": "/h", "exe": "/e", "kind": "terminal"}
        panel_env._seat_finder = lambda: seat
        token = _token(host, port)
        status, body, _ = _start(host, port, token)
        assert status == 409 and b"77" in body and panel_env.running is False

    def test_input_before_start_is_409_and_bad_base64_is_400(self, running_server, panel_env):
        host, port = running_server
        token = _token(host, port)
        hdr = _good_headers(host, port, token)
        status, _, _ = _req(f"http://{host}:{port}/hq/input", "POST", {"data": "cGluZw=="}, hdr)
        assert status == 409
        _start(host, port, token)
        status, _, _ = _req(f"http://{host}:{port}/hq/input", "POST", {"data": "@@@not-b64"}, hdr)
        assert status == 400

    def test_status_writer_file_appears_under_sovereign_root(self, running_server, tmp_path):
        p = tmp_path / "hq" / "cockpit" / "console_status.json"
        deadline = time.time() + 3
        while not p.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert p.exists()
        rec = json.loads(p.read_text())
        assert isinstance(rec["ok"], bool) and rec["checked_at"]


# ── Task 4: two additive snapshot keys ──────────────────────────────────────

FIRST_19_KEYS = [
    "timestamp",
    "connectivity",
    "halts_count",
    "decisions_count",
    "unacked_honks",
    "unacked_signals",
    "listener_stale",
    "latest",
    "feed",
    "service_telemetry",
    "watchman",
    "spiral",
    "self_model",
    "guardian",
    "open_threads",
    "arrival_gate",
    "bridge_heartbeat",
    "lineage",
    "open_threads_index",
]


class TestSnapshotKeys:
    def test_two_new_keys_append_at_the_end(self, running_server, panel_env):
        host, port = running_server
        _, body, _ = _req(f"http://{host}:{port}/snapshot.json")
        keys = list(json.loads(body).keys())
        assert keys[-2:] == ["honk_counts", "hq_panel"]
        assert keys[:19] == FIRST_19_KEYS

    def test_honk_counts_are_two_labelled_numbers_never_folded(
        self, running_server, panel_env, monkeypatch
    ):
        host, port = running_server
        field = {"ingestion": "ok", "by_source": {"honk": 34, "watchman": 5}}
        monkeypatch.setattr(web, "heartbeat_field", lambda: field)
        _, body, _ = _req(f"http://{host}:{port}/snapshot.json")
        snap = json.loads(body)
        hc = snap["honk_counts"]
        assert hc["all_time_unacked"] == snap["unacked_honks"]
        assert hc["ledger_open"] == 34 and hc["ledger_since"] == "2026-09-07"
        assert "since the nape daemon began" in hc["all_time_label"]
        assert "signal ledger" in hc["ledger_label"]

    def test_honk_ledger_count_is_none_when_ledger_blind(
        self, running_server, panel_env, monkeypatch
    ):
        host, port = running_server
        monkeypatch.setattr(
            web, "heartbeat_field", lambda: {"ingestion": "never", "by_source": None}
        )
        _, body, _ = _req(f"http://{host}:{port}/snapshot.json")
        assert json.loads(body)["honk_counts"]["ledger_open"] is None

    def test_hq_panel_key_mirrors_status(self, running_server, panel_env):
        host, port = running_server
        _, body, _ = _req(f"http://{host}:{port}/snapshot.json")
        hp = json.loads(body)["hq_panel"]
        assert hp["running"] is False and hp["listening"] is False and "over_24h" in hp


# ── Task 5: the page assets ─────────────────────────────────────────────────

STATIC = Path(web.__file__).parent / "dashboard_web_static"


class TestPageAssets:
    def test_index_wires_vendor_and_panel(self):
        html = (STATIC / "index.html").read_text()
        assert "/static/vendor/xterm.css" in html
        assert "/static/vendor/xterm.js" in html and "/static/vendor/addon-fit.js" in html
        assert html.index("/static/vendor/xterm.js") < html.index("/static/app.js")
        needles = (
            'id="hq-panel"',
            'id="hq-term"',
            'id="hq-start"',
            'id="hq-note"',
            'id="hq-banner"',
            'id="hq-collapse"',
            'id="stat-honks-alltime"',
            'id="stat-honks-ledger"',
        )
        for needle in needles:
            assert needle in html, needle
        # 12 existing sections carry class="panel ..." plus the HQ panel.
        assert html.count('class="panel ') == 13

    def test_app_js_wires_routes_and_never_uses_innerhtml(self):
        js = (STATIC / "app.js").read_text()
        needles = (
            "/hq/status",
            "/hq/stream?token=",
            "/hq/start",
            "/hq/input",
            "/hq/resize",
            "function initHqPanel",
            "function renderHqPanel",
            "get('start') === 'hq'",
            "over_24h",
            "honk_counts",
        )
        for needle in needles:
            assert needle in js, needle
        # The file's own comment names innerHTML to forbid it; assert no ASSIGNMENT.
        assert re.search(r"\.innerHTML\s*=", js) is None
        assert "new Terminal(" in js and "FitAddon" in js

    def test_style_has_panel_hq(self):
        css = (STATIC / "style.css").read_text()
        assert ".panel-hq" in css and "#hq-term" in css


class TestPageFixPass:
    """Review fix pass: token refresh, stream reopen, resume, serial input, start param."""

    def test_app_js_refreshes_token_reopens_stream_and_queues_input(self):
        js = (STATIC / "app.js").read_text()
        needles = (
            "function refreshCsrf",
            "readyState === EventSource.CLOSED",
            "function hqPostWithRetry",
            "function hqFlushInput",
            "history.replaceState",
            "hp.running && !hq.es",
            "input refused",
        )
        for needle in needles:
            assert needle in js, needle


class TestSeat2of3Findings:
    def test_start_answers_the_spawned_pid_even_for_a_short_lived_child(
        self, running_server, panel_env, monkeypatch
    ):
        host, port = running_server
        monkeypatch.setenv("HQ_PANEL_COMMAND", json.dumps(["/bin/sh", "-c", "exit 0"]))
        token = _token(host, port)
        status, body, _ = _start(host, port, token)
        assert status == 200, body
        assert isinstance(json.loads(body)["pid"], int)
