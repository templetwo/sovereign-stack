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
        # Phase 3: the push link's detail rides this Studio-only route.
        push = data["push"]
        assert push["listening"] is False and isinstance(push["reason"], str)
        assert set(push) == {
            "listening",
            "reason",
            "inbound",
            "batches_handed",
            "last_handed_at",
            "events_waiting",
            "min_interval_s",
            "last_error",
            "last_tick_at",
            "sources",
            "blind",
            "nonce_file_ok",
        }
        # Never ticked (the suite keeps the poller off): it says so (HQ review A6).
        assert push["reason"] == "push poller not started"

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
        # Only the boolean crosses into the CORS-wildcard snapshot (PR #11 lesson).
        assert "push" not in hp and "reason" not in hp and "last_handed_at" not in hp


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
            'id="hq-focus"',  # Focus replaced Collapse (cockpit layout, 2026-10-10)
            'id="stat-honks-alltime"',
            'id="stat-honks-ledger"',
            'id="stat-hq-main"',  # HQ beside BRIDGE UP in the header
            'id="stat-hq-sub"',
            'id="attention-strip"',  # NEEDS A LOOK, read-only
        )
        for needle in needles:
            assert needle in html, needle
        # 12 existing sections carry class="panel ..." plus the HQ panel.
        assert html.count('class="panel ') == 13
        # The HQ panel and ACTIVITY share the center column, HQ first; the
        # banner sits in its reserved slot above the terminal. The slice ends
        # at the column's OWN closing tag (div depth counter), not end of file.
        start = html.index('<div class="cockpit-col col-center"')
        depth, pos = 0, start
        for m in re.finditer(r"<div\b|</div>", html[start:]):
            depth += 1 if m.group(0) != "</div>" else -1
            if depth == 0:
                pos = start + m.end()
                break
        assert depth == 0, "col-center never closes"
        center = html[start:pos]
        assert 'class="cockpit-col col-c"' not in center
        assert 'id="hq-panel"' in center and 'class="panel panel-act"' in center
        assert center.index('id="hq-panel"') < center.index('class="panel panel-act"')
        assert (
            html.index('class="hq-banner-slot"')
            < html.index('id="hq-banner"')
            < html.index('id="hq-term"')
        )
        assert 'id="hq-collapse"' not in html
        # Rebuilt every 3 s: a live region would announce constantly.
        strip_tag = re.search(r'<div class="attention-strip"[^>]*>', html).group(0)
        assert 'aria-live="off"' in strip_tag

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
            # Cockpit layout, 2026-10-10: focus toggle, size ownership, plain words.
            "function hqSetFocus",
            "attachCustomKeyEventHandler",
            "function hqPostSize",
            "document.hasFocus()",
            "function renderAttentionStrip",
            "push link on",  # Phase 3 (2026-10-10): the link word from the push poller
            "push link off",
            "push link held, batches print in the panel and do not reach HQ",
            "blind to ",
            "last error ",
            "last handed",
            # Review 1 fixes (2026-10-10).
            "new ResizeObserver(",
            "lastPosted",
            "window.addEventListener('focus', () => hqPostSize(true))",
            "if (!force && same) return false;",  # dedupe is for unforced posts only
            "hq.forcedThisFrame",  # forced posts in one frame merge
            "hq.lastPosted = null; hq.resumedResize = false;",  # stream exit
            "ev.repeat",
            "ev.preventDefault()",
            "chat.db not probed",
            "chat.db blocked",
            "main.textContent = 'off';",  # was "HQ off"; the one-word state since 2026-10-10
            "HQ unreachable",
            "console unreachable",
            "signals, all sources",
            "honk signals (ledger)",
            "guardian unavailable",
            "watchman unavailable",
            "g.issue_count === 0",
            "renderHqHeader(null)",
            "renderAttentionStrip(null)",
            "const HQ_PROBE_STALE_S = 900;",
            "hqNoteEl.textContent = 'console unreachable'",
        )
        for needle in needles:
            assert needle in js, needle
        assert "not listening" not in js
        assert " open signals'" not in js  # the strip's old unlabelled total
        # The file's own comment names innerHTML to forbid it; assert no ASSIGNMENT.
        assert re.search(r"\.innerHTML\s*=", js) is None
        assert "new Terminal(" in js and "FitAddon" in js

    def test_style_has_panel_hq(self):
        css = (STATIC / "style.css").read_text()
        assert ".panel-hq" in css and "#hq-term" in css
        # Cockpit layout, 2026-10-10: the center column, its cap, focus, the slot.
        assert ".col-center { grid-area: act;" in css
        assert "minmax(460px, 1100px)" in css
        assert "body.hq-focus .cockpit {" in css
        assert ".hq-banner-slot" in css and "min-height: 34px" in css
        assert ".is-collapsed" not in css

    def test_style_review_1_fixes(self):
        css = (STATIC / "style.css").read_text()
        # #hq-term carries no padding (the fit addon measures the parent box).
        block = re.search(r"#hq-term\s*\{([^}]*)\}", css).group(1)
        assert "padding" not in block
        assert "#hq-term .xterm { padding: 4px; }" in css
        # The attention strip stays visible in focus mode.
        # (selector-local, so a rule nested in @media is caught too)
        hidden = re.search(r"hq-focus[^{}]*attention-strip[^{}]*\{[^}]*display:\s*none", css)
        assert hidden is None, hidden and hidden.group(0)
        # Locked focus fills the single row; the calc is only the fallback.
        assert "grid-template-rows: minmax(0, 1fr)" in css
        assert ".stat-hq-block .stat-value.is-stale {" in css
        assert "body.hq-focus .app.is-locked .col-center { height: auto; min-height: 0; }" in css
        # The fallback covers every unlocked width, outside any width query.
        fallback = (
            "body.hq-focus .app:not(.is-locked) .col-center "
            "{ height: calc(100vh - 190px); min-height: 420px; }"
        )
        assert fallback in css
        assert "@media (max-width: 1379px) {\n  body.hq-focus" not in css


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


# ── Fix 1 (web seat review of PR #9, 2026-10-10): Studio-only is a rule in code ──
#
# The web seat measured serve("0.0.0.0", 0) in a container: a non-loopback client
# minted a token from /session (no Host check there), spoofed Host and Origin,
# POSTed /hq/start and got a shell. Loopback was a default of the plist, not a
# rule of the code. Now the code refuses on its own: /hq/* and /session answer
# only when the server is bound to a loopback address AND the peer is loopback.


class TestStudioOnly:
    @pytest.mark.parametrize(
        "bound, peer, expect",
        [
            ("127.0.0.1", "127.0.0.1", None),
            ("127.0.0.1", "::1", None),
            ("::1", "127.0.0.1", None),
            ("0.0.0.0", "127.0.0.1", "server is not bound to loopback"),
            ("", "127.0.0.1", "server is not bound to loopback"),
            ("192.168.1.10", "127.0.0.1", "server is not bound to loopback"),
            ("127.0.0.1", "192.168.1.5", "peer is not loopback"),
            ("127.0.0.1", "::ffff:10.0.0.7", "peer is not loopback"),
            ("127.0.0.1", "garbage", "peer is not loopback"),
        ],
    )
    def test_gate_names_the_failing_side(self, bound, peer, expect):
        reason = web._studio_only_reason(bound, peer)
        if expect is None:
            assert reason is None
        else:
            assert reason is not None and expect in reason

    @pytest.fixture
    def wide_server(self, monkeypatch, tmp_path):
        """Bound beyond loopback for a few hundred milliseconds on an ephemeral port;
        every panel route must refuse even the loopback requester."""
        monkeypatch.setattr(conn, "_launchctl_print_text", lambda label: None)
        monkeypatch.setattr(
            conn,
            "_http_probe",
            lambda url, timeout=2.0: {"http_status": None, "body": "", "error": "m"},
        )
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        server = web.serve(host="0.0.0.0", port=0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(0.05)
        yield ("127.0.0.1", port)
        server.shutdown()

    def test_server_bound_beyond_loopback_refuses_the_panel_and_session(
        self, wide_server, panel_env
    ):
        host, port = wide_server
        pinned = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}"}
        status, body, _ = _req(f"http://{host}:{port}/session", headers=pinned)
        assert status == 403 and "Studio-only" in json.loads(body)["error"]
        status, body, _ = _req(f"http://{host}:{port}/hq/status", headers=pinned)
        assert status == 403 and "Studio-only" in json.loads(body)["error"]
        status, _, _ = _req(f"http://{host}:{port}/hq/stream?token=x", headers=pinned)
        assert status == 403
        status, body, _ = _req(
            f"http://{host}:{port}/hq/start",
            "POST",
            {"cols": 80, "rows": 24},
            {**pinned, "X-CSRF-Token": web._mint_csrf_token()},
        )
        assert status == 403 and "Studio-only" in json.loads(body)["error"]
        assert not panel_env.running
        # The read-only dashboard itself still answers: the rule is on the panel.
        status, _, _ = _req(f"http://{host}:{port}/health", headers=pinned)
        assert status == 200

    def test_loopback_server_still_serves_the_panel(self, running_server, panel_env):
        host, port = running_server
        status, _, _ = _req(f"http://{host}:{port}/session")
        assert status == 200
        status, _, _ = _req(f"http://{host}:{port}/hq/status")
        assert status == 200

    def test_live_plist_passes_no_host_flag(self):
        plist = (
            Path.home() / "Library" / "LaunchAgents" / "com.templetwo.sovereign-dashboard-web.plist"
        )
        if not plist.exists():
            pytest.skip("not the Studio")
        import plistlib

        args = plistlib.loads(plist.read_bytes()).get("ProgramArguments", [])
        assert "--host" not in args and not any(a.startswith("--host=") for a in args)
