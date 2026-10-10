"""Cockpit integration (2026-10-10): file drop to path, working/waiting fields,
the comms line, and the polish pass. Same isolation as test_hq_panel_routes.py:
an ephemeral-port server, SOVEREIGN_ROOT in tmp_path, a /bin/sh child, every
external reader stubbed. Nothing here writes under the real ~/.sovereign."""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import re
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

from sovereign_stack import connectivity as conn
from sovereign_stack import dashboard_readers as readers
from sovereign_stack import dashboard_web as web
from sovereign_stack import hq_panel

STATIC = Path(web.__file__).parent / "dashboard_web_static"
ECHO_CMD = json.dumps(["/bin/sh", "-c", "echo READY; cat"])


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
    monkeypatch.setenv("HQ_DROP_ROOT", str(tmp_path / "drops"))
    monkeypatch.setenv("HQ_PANEL_STATUS_INTERVAL_S", "3600")
    server = web.serve(host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.05)
    yield (host, port)
    server.shutdown()


@pytest.fixture
def panel(monkeypatch, tmp_path):
    monkeypatch.setenv("HQ_PANEL_COMMAND", ECHO_CMD)
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    fresh = hq_panel.HQSession(seat_finder=lambda: None)
    monkeypatch.setattr(hq_panel, "_SESSION", fresh)
    yield fresh
    fresh._terminate_for_tests()


def _raw(host, port, method, path, body=b"", headers=None):
    """A raw request, so the body is bytes and Content-Length is ours to set."""
    c = http.client.HTTPConnection(host, port, timeout=5)
    try:
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        hdrs = {"Host": f"{host}:{port}", **(headers or {})}
        if "Content-Length" not in hdrs and "Transfer-Encoding" not in hdrs:
            hdrs["Content-Length"] = str(len(body))
        for k, v in hdrs.items():
            if v is not None:  # None = send no such header at all
                c.putheader(k, v)
        c.endheaders()
        if body:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                c.send(body)
        r = c.getresponse()
        return r.status, r.read(), dict(r.getheaders())
    finally:
        c.close()


def _token(host, port):
    status, body, _ = _raw(host, port, "GET", "/session")
    assert status == 200
    return json.loads(body)["csrf_token"]


def _drop(host, port, token, name, data=b"hello\n", extra=None):
    hdr = {
        "Origin": f"http://{host}:{port}",
        "X-CSRF-Token": token,
        "X-Filename": urllib.parse.quote(name, safe=""),
        "Content-Type": "application/octet-stream",
        **(extra or {}),
    }
    return _raw(host, port, "POST", "/hq/drop", data, hdr)


def _drops_dir(root: Path) -> Path:
    return root / "drops"  # HQ_DROP_ROOT in the running_server fixture


def _all_drops(root: Path) -> list[Path]:
    d = _drops_dir(root)
    return sorted(p for p in d.rglob("*") if p.is_file()) if d.exists() else []


# ── Item 1: /hq/drop ────────────────────────────────────────────────────────


class TestDropRoute:
    def test_get_is_405(self, running_server, panel):
        host, port = running_server
        status, _, hdr = _raw(host, port, "GET", "/hq/drop")
        assert status == 405 and "Access-Control-Allow-Origin" not in hdr

    def test_wrong_host_is_refused_and_writes_nothing(self, running_server, panel, tmp_path):
        host, port = running_server
        panel.start(80, 24)
        token = _token(host, port)
        status, body, _ = _drop(host, port, token, "a.txt", extra={"Host": "evil.example:80"})
        assert status == 403 and b"host" in body
        assert _all_drops(tmp_path) == []

    def test_missing_csrf_is_refused_and_writes_nothing(self, running_server, panel, tmp_path):
        host, port = running_server
        panel.start(80, 24)
        status, body, _ = _drop(host, port, "", "a.txt")
        assert status == 403 and b"csrf" in body
        assert _all_drops(tmp_path) == []

    def test_wrong_origin_is_refused(self, running_server, panel, tmp_path):
        host, port = running_server
        panel.start(80, 24)
        token = _token(host, port)
        status, body, _ = _drop(host, port, token, "a.txt", extra={"Origin": "http://evil.example"})
        assert status == 403 and b"origin" in body
        assert _all_drops(tmp_path) == []

    @pytest.mark.parametrize("name", ["evil.sh", "noext", "a.exe", "archive.tar.gz", "x.PNG.app"])
    def test_bad_extension_is_400(self, running_server, panel, tmp_path, name):
        host, port = running_server
        panel.start(80, 24)
        status, body, _ = _drop(host, port, _token(host, port), name)
        assert status == 400 and b"not allowed" in body
        assert _all_drops(tmp_path) == []

    @pytest.mark.parametrize(
        "name",
        [
            "../escape.txt",
            "a/b.txt",
            "..\\x.txt",
            ".env.txt",
            ".bashrc",
            "a\x00.txt",
            "a\nb.txt",
            "",
        ],
    )
    def test_traversal_dotfile_and_control_names_are_400(
        self, running_server, panel, tmp_path, name
    ):
        host, port = running_server
        panel.start(80, 24)
        status, _, _ = _drop(host, port, _token(host, port), name)
        assert status == 400
        assert _all_drops(tmp_path) == []
        assert not (tmp_path / "hq" / "cockpit" / "escape.txt").exists()

    def test_oversized_body_is_413_before_reading(
        self, running_server, panel, tmp_path, monkeypatch
    ):
        host, port = running_server
        panel.start(80, 24)
        monkeypatch.setattr(web, "HQ_DROP_MAX_BYTES", 16)
        status, body, _ = _drop(host, port, _token(host, port), "big.txt", b"x" * 17)
        assert status == 413
        assert _all_drops(tmp_path) == []

    def test_the_real_cap_is_25_mb(self):
        assert web.HQ_DROP_MAX_BYTES == 25 * 1024 * 1024

    def test_chunked_transfer_encoding_is_refused(self, running_server, panel, tmp_path):
        host, port = running_server
        panel.start(80, 24)
        status, _, _ = _drop(
            host, port, _token(host, port), "a.txt", b"", extra={"Transfer-Encoding": "chunked"}
        )
        assert status == 411
        assert _all_drops(tmp_path) == []

    def test_absent_content_length_is_refused(self, running_server, panel, tmp_path):
        host, port = running_server
        panel.start(80, 24)
        status, body, _ = _drop(
            host, port, _token(host, port), "a.txt", b"", extra={"Content-Length": None}
        )
        assert status == 411 and b"Content-Length is required" in body
        assert _all_drops(tmp_path) == []

    def test_not_running_is_409_and_writes_nothing(self, running_server, panel, tmp_path):
        host, port = running_server
        status, body, _ = _drop(host, port, _token(host, port), "a.txt")
        assert status == 409 and b"no panel session" in body
        assert _all_drops(tmp_path) == []

    def test_good_drop_writes_the_file_and_returns_its_sha256(
        self, running_server, panel, tmp_path
    ):
        host, port = running_server
        assert panel.start(80, 24)[0]
        data = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 10
        status, body, hdr = _drop(host, port, _token(host, port), "shot.png", data)
        assert status == 200, body
        assert "Access-Control-Allow-Origin" not in hdr
        out = json.loads(body)
        p = Path(out["path"])
        assert p.is_absolute() and p.read_bytes() == data
        assert out["bytes"] == len(data)
        assert out["sha256"] == hashlib.sha256(data).hexdigest()
        assert p.parent.parent == _drops_dir(tmp_path)
        assert p.parent.name == time.strftime("%Y%m%d") and p.name == "shot.png"
        # Nothing under the mirrored tree.
        assert not (tmp_path / "hq" / "cockpit" / "drops").exists()

    def test_second_drop_of_the_same_name_never_overwrites(self, running_server, panel, tmp_path):
        host, port = running_server
        assert panel.start(80, 24)[0]
        token = _token(host, port)
        paths = []
        for payload in (b"one", b"two", b"three"):
            status, body, _ = _drop(host, port, token, "notes.md", payload)
            assert status == 200, body
            paths.append(Path(json.loads(body)["path"]))
        assert [p.name for p in paths] == ["notes.md", "notes-2.md", "notes-3.md"]
        assert [p.read_bytes() for p in paths] == [b"one", b"two", b"three"]

    def test_unsafe_characters_are_replaced_so_the_typed_path_needs_no_quoting(
        self, running_server, panel, tmp_path
    ):
        host, port = running_server
        assert panel.start(80, 24)[0]
        status, body, _ = _drop(host, port, _token(host, port), "Screen Shot $(x) é;rm.PNG")
        assert status == 200, body
        name = json.loads(body)["name"]
        assert name.endswith(".png")
        assert all(c.isalnum() or c in "._-" for c in name), name


# ── Item 2: working / waiting, from the server's clock ─────────────────────


@pytest.fixture
def session(monkeypatch, tmp_path):
    monkeypatch.setenv("HQ_PANEL_COMMAND", ECHO_CMD)
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    s = hq_panel.HQSession(seat_finder=lambda: None)
    yield s
    s._terminate_for_tests()


def _wait(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


class TestWorkingWaitingFields:
    def test_fields_are_present_and_none_before_any_output(self, session):
        st = session.status()
        assert {"seq_changed_at", "seq_idle_seconds", "last_input_at"} <= set(st)
        assert st["seq_changed_at"] is None and st["seq_idle_seconds"] is None
        assert st["last_input_at"] is None

    def test_output_stamps_seq_changed_at_and_idle_grows_from_it(self, session):
        before = time.time()
        assert session.start(80, 24)[0]
        assert _wait(lambda: session.status()["seq_changed_at"] is not None)
        st = session.status()
        from datetime import datetime

        stamped = datetime.fromisoformat(st["seq_changed_at"]).timestamp()
        assert before - 1 <= stamped <= time.time() + 1
        assert isinstance(st["seq_idle_seconds"], float) and st["seq_idle_seconds"] >= 0
        # Pretend the last output was 30 s ago: idle reads >= 20 (the "waiting" side).
        session._seq_changed_at -= 30
        assert session.status()["seq_idle_seconds"] >= 30

    def test_input_stamps_last_input_at_only_when_written(self, session):
        session.write(b"ignored\n")  # not running: nothing written, no stamp
        assert session.status()["last_input_at"] is None
        assert session.start(80, 24)[0]
        assert _wait(lambda: session.status()["seq"] >= 1)
        session.write(b"ping\n")
        assert session.status()["last_input_at"] is not None

    def test_input_route_stamps_last_input_at(self, running_server, panel):
        import base64

        host, port = running_server
        assert panel.start(80, 24)[0]
        token = _token(host, port)
        payload = json.dumps({"data": base64.b64encode(b"x").decode()}).encode()
        hdr = {
            "Origin": f"http://{host}:{port}",
            "X-CSRF-Token": token,
            "Content-Type": "application/json",
        }
        status, _, _ = _raw(host, port, "POST", "/hq/input", payload, hdr)
        assert status == 200
        status, body, _ = _raw(host, port, "GET", "/hq/status")
        assert json.loads(body)["last_input_at"] is not None

    def test_a_new_start_clears_the_previous_sessions_stamps(self, session, monkeypatch):
        assert session.start(80, 24)[0]
        assert _wait(lambda: session.status()["seq_changed_at"] is not None)
        session.write(b"x\n")
        session._terminate_for_tests()
        assert _wait(lambda: not session.running)
        monkeypatch.setenv("HQ_PANEL_COMMAND", json.dumps(["/bin/sh", "-c", "sleep 5"]))
        assert session.start(80, 24)[0]
        st = session.status()
        assert st["seq_changed_at"] is None and st["last_input_at"] is None

    def test_page_derives_one_word_from_the_server_fields(self):
        js = (STATIC / "app.js").read_text()
        for needle in (
            "function hqActivityWord",
            "const HQ_STILL_S = 20;",
            "st.seq_idle_seconds",
            "'unknown'",
            "'working'",
            "'waiting'",
            "DOWN · exit ${exit}",
        ):
            assert needle in js, needle
        assert "LIVE · " not in js


# ── Item 3: the comms line, from the launchd spool files only ──────────────

SECRET_TEXT = "the body of a text that must never leave the disk"


def _spool(root: Path, status: dict | None, rows: list[dict] | None):
    d = root / "imessage"
    d.mkdir(parents=True, exist_ok=True)
    if status is not None:
        (d / "daemon_status.json").write_text(json.dumps(status))
    if rows is not None:
        (d / "inbound.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def _row(rowid, utc, eastern, from_me=0):
    return {
        "rowid": rowid,
        "date_iso_eastern": eastern,
        "date_utc": utc,
        "chat_guid": "any;-;+10000000000",
        "handle": "+10000000000",
        "is_from_me": from_me,
        "text": SECRET_TEXT,
    }


class TestCommsReader:
    def test_none_when_the_status_file_is_absent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        assert readers.read_comms() is None
        _spool(tmp_path, None, [])
        assert readers.read_comms() is None

    def test_none_when_the_status_file_is_malformed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        d = _spool(tmp_path, None, None)
        (d / "daemon_status.json").write_text("{ not json")
        assert readers.read_comms() is None

    def test_healthy_spool_and_newest_inbound_row(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        now = time.time()
        from datetime import datetime, timezone

        utc = datetime.fromtimestamp(now - 600, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            _row(10, "2026-10-01 10:00:00", "2026-10-01 06:00:00"),
            _row(11, utc, "2026-10-10 16:29:33"),
            _row(12, utc, "2026-10-10 16:30:00", from_me=1),  # not his: skipped
        ]
        _spool(tmp_path, {"ok": True, "error": None, "last_poll": now - 5}, rows)
        c = readers.read_comms()
        assert c["spool_ok"] is True and c["spool_stale"] is False and c["spool_error"] is False
        assert 4 <= c["spool_age_s"] <= 30
        assert c["last_inbound_rowid"] == 11
        assert c["last_inbound_at"] == "2026-10-10 16:29:33"
        # Age from date_utc, not the Eastern stamp (which would be 4 h off).
        assert 590 <= c["last_inbound_age_s"] <= 640
        assert c["unanswered_for_s"] is None
        blob = json.dumps(c)
        assert SECRET_TEXT not in blob and "+10000000000" not in blob and "any;-;" not in blob

    def test_stale_and_not_ok_are_reported_not_hidden(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        _spool(tmp_path, {"ok": False, "error": "x", "last_poll": time.time() - 3600}, None)
        c = readers.read_comms()
        assert c["spool_ok"] is False and c["spool_error"] is True and c["spool_stale"] is True
        assert c["last_inbound_at"] is None and c["last_inbound_rowid"] is None

    def test_no_last_poll_falls_back_to_the_file_mtime(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        _spool(tmp_path, {"ok": True}, None)
        c = readers.read_comms()
        assert c["spool_age_s"] is not None and c["spool_age_s"] < 30

    def test_torn_and_malformed_lines_are_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        d = _spool(tmp_path, {"ok": True, "last_poll": time.time()}, None)
        good = json.dumps(_row(7, "2026-10-10 20:00:00", "2026-10-10 16:00:00"))
        (d / "inbound.jsonl").write_text('{"rowid": 1, "torn\n' + good + "\nnot json\n")
        assert readers.read_comms()["last_inbound_rowid"] == 7

    def test_missing_inbound_file_is_its_own_unreadable_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        _spool(tmp_path, {"ok": True, "last_poll": time.time()}, None)
        c = readers.read_comms()
        assert c["inbound_readable"] is False and c["last_inbound_at"] is None
        _spool(tmp_path, None, [])
        assert readers.read_comms()["inbound_readable"] is True

    def test_a_last_poll_in_the_future_reads_stale_not_fresh(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        _spool(tmp_path, {"ok": True, "last_poll": time.time() + 3600}, [])
        c = readers.read_comms()
        assert c["spool_stale"] is True and c["spool_age_s"] is None


# ── Review fix 1: the timing stamps and comms are Studio-only ──────────────

TIMING_FIELDS = ("seq_changed_at", "seq_idle_seconds", "last_input_at")


class TestStudioOnlyActivityAndComms:
    def _spool_with_row(self, root):
        _spool(
            root,
            {"ok": True, "error": "boom", "last_poll": time.time()},
            [_row(5, "2026-10-10 20:00:00", "2026-10-10 16:00:00")],
        )

    def test_snapshot_has_no_comms_and_no_timing_fields(self, running_server, panel, tmp_path):
        self._spool_with_row(tmp_path)
        assert panel.start(80, 24)[0]
        assert _wait(lambda: panel.status()["seq_changed_at"] is not None)
        host, port = running_server
        _, body, hdr = _raw(host, port, "GET", "/snapshot.json")
        snap = json.loads(body)
        assert "comms" not in snap and len(snap) == 21
        assert list(snap)[-2:] == ["honk_counts", "hq_panel"]
        for f in TIMING_FIELDS:
            assert f not in snap["hq_panel"], f
        text = body.decode()
        assert SECRET_TEXT not in text and "imessage" not in text
        assert "2026-10-10 16:00:00" not in text  # the inbound stamp

    def test_hq_status_carries_them_under_the_right_host(self, running_server, panel, tmp_path):
        self._spool_with_row(tmp_path)
        assert panel.start(80, 24)[0]
        assert _wait(lambda: panel.status()["seq_changed_at"] is not None)
        host, port = running_server
        status, body, hdr = _raw(host, port, "GET", "/hq/status")
        assert status == 200 and "Access-Control-Allow-Origin" not in hdr
        st = json.loads(body)
        for f in TIMING_FIELDS:
            assert f in st, f
        assert isinstance(st["seq_idle_seconds"], float)
        c = st["comms"]
        assert c["last_inbound_at"] == "2026-10-10 16:00:00" and c["spool_ok"] is True
        assert c["inbound_readable"] is True
        for gone in ("source", "last_inbound_rowid", "unanswered_for_s", "spool_error"):
            assert gone not in c, gone
        assert SECRET_TEXT not in body.decode() and "+10000000000" not in body.decode()

    def test_hq_status_under_a_wrong_host_is_403(self, running_server, panel, tmp_path):
        self._spool_with_row(tmp_path)
        host, port = running_server
        status, body, _ = _raw(host, port, "GET", "/hq/status", headers={"Host": "evil.example"})
        assert status == 403 and b"2026-10-10 16:00:00" not in body

    def test_hq_status_comms_is_null_when_the_reader_raises(
        self, running_server, panel, monkeypatch
    ):
        def boom():
            raise RuntimeError("spool reader exploded")

        monkeypatch.setattr(readers, "read_comms", boom)
        host, port = running_server
        status, body, _ = _raw(host, port, "GET", "/hq/status")
        assert status == 200 and json.loads(body)["comms"] is None

    def test_page_reads_activity_and_comms_from_hq_status(self):
        js = (STATIC / "app.js").read_text()
        for needle in (
            "async function hqFetchStatus",
            "hq.status = await hqFetchStatus();",
            "hqActivityWord(hq.status)",
            "renderHqComms(hq.status)",
            "hq.status = null;",
        ):
            assert needle in js, needle
        assert "snapshot.comms" not in js and "snapshot && snapshot.comms" not in js


class TestCommsPage:
    def test_page_renders_one_line_and_no_answered_claim(self):
        js = (STATIC / "app.js").read_text()
        html = (STATIC / "index.html").read_text()
        assert 'id="hq-comms"' in html
        assert html.index('class="hq-banner-slot"') < html.index('id="hq-comms"')
        assert html.index('id="hq-comms"') < html.index('id="hq-term"')
        for needle in (
            "function renderHqComms",
            "last text from Anthony: ",
            "'comms unavailable'",
            "spool ok",
            "renderHqComms(null)",
        ):
            assert needle in js, needle
        assert "'comms unavailable (inbound spool file unreadable)'" in js
        body = js[js.index("function renderHqComms") : js.index("function renderHqPanel")]
        assert "answered" not in body
        assert re.search(r"\bc\.text\b", body) is None  # never the message body


# ── Item 4: polish (static checks; no browser in this suite) ───────────────


class TestPolish:
    def test_font_size_buttons_keys_and_persistence(self):
        js = (STATIC / "app.js").read_text()
        html = (STATIC / "index.html").read_text()
        assert 'id="hq-font-down"' in html and 'id="hq-font-up"' in html
        for needle in (
            "function hqSetFontSize",
            "hq.term.options.fontSize = v;",
            "hqRefit(true);",
            "const HQ_FONT_KEY = 'ss-hq-font';",
            "fontSize: hqSavedFontSize()",
        ):
            assert needle in js, needle
        # localStorage is touched only inside try blocks in the font helpers.
        body = js[js.index("function hqSavedFontSize") : js.index("function hqSetFontSize")]
        assert body.count("localStorage.") == body.count("try { ") == 1
        setter = js[js.index("function hqSetFontSize") : js.index("// ── Copy on select")]
        assert setter.count("localStorage.") == setter.count("try { ") == 1
        # Buttons only: no font chord (Ctrl+Shift+- is Ctrl+_, undo).
        assert "hqFontKeyStep" not in js
        assert "'Equal'" not in js and "'Minus'" not in js and "NumpadSubtract" not in js
        assert "Ctrl+Shift+-" not in html and "Ctrl+Shift+=" not in html
        # The focus key is unchanged.
        assert "ev.code === 'KeyF'" in js

    def test_copy_on_select_writes_and_never_reads_the_clipboard(self):
        js = (STATIC / "app.js").read_text()
        assert "hq.term.onSelectionChange(hqCopySelection)" in js
        assert "navigator.clipboard.writeText(sel)" in js
        assert "clipboard.readText" not in js and "clipboard.read(" not in js

    def test_waiting_state_is_quiet_and_respects_reduced_motion(self):
        js = (STATIC / "app.js").read_text()
        css = (STATIC / "style.css").read_text()
        assert "function hqSetActivityClass" in js
        assert ".panel-hq.is-working {" in css and ".panel-hq.is-waiting {" in css
        assert "@media (prefers-reduced-motion: reduce) { .panel-hq { transition: none; } }" in css

    def test_drop_handler_is_wired_without_innerhtml(self):
        js = (STATIC / "app.js").read_text()
        for needle in (
            "function hqInitDrop",
            "'/hq/drop'",
            "'X-Filename': encodeURIComponent(file.name)",
            "hq.pending += `${res.data.path} `;",
            "dropped ${res.data.name}, ${res.data.bytes} B",
            "ev.preventDefault();  // else the browser navigates to the dropped file",
        ):
            assert needle in js, needle
        assert re.search(r"\.innerHTML\s*=", js) is None

    def test_hq_buttons_have_a_style(self):
        css = (STATIC / "style.css").read_text()
        assert ".panel-hq .panel-head .btn {" in css


# ── Review fixes 3 and 7: drop root off ~/.sovereign, retention, read timeout ──


class TestDropRootAndRetention:
    def test_default_root_is_local_share_not_sovereign(self, monkeypatch):
        monkeypatch.delenv("HQ_DROP_ROOT", raising=False)
        root = web._hq_drop_root()
        assert root == Path.home() / ".local" / "share" / "hq-cockpit" / "drops"
        assert ".sovereign" not in root.parts
        assert web._hq_drop_dir().parent == root

    def test_env_override_is_honoured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HQ_DROP_ROOT", str(tmp_path / "x"))
        assert web._hq_drop_root() == tmp_path / "x"

    def test_sweep_removes_old_files_only_and_never_follows_symlinks(self, tmp_path):
        import os

        root = tmp_path / "drops"
        old_dir, new_dir = root / "20260801", root / time.strftime("%Y%m%d")
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        aged = time.time() - 31 * 86400
        old_f, new_f = old_dir / "old.txt", new_dir / "new.txt"
        old_f.write_text("old")
        new_f.write_text("new")
        os.utime(old_f, (aged, aged))
        # Outside the root: an old file reached by a file symlink and by a dir symlink.
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "keep.txt"
        target.write_text("keep")
        os.utime(target, (aged, aged))
        (new_dir / "link.txt").symlink_to(target)
        os.utime(new_dir / "link.txt", (aged, aged), follow_symlinks=False)
        (root / "dirlink").symlink_to(outside, target_is_directory=True)
        assert web._hq_drop_sweep(root) == 1
        assert not old_f.exists() and not old_dir.exists()  # emptied date dir goes
        assert new_f.exists() and target.exists()
        assert (new_dir / "link.txt").is_symlink() and (root / "dirlink").is_symlink()

    def test_a_symlinked_root_is_not_swept(self, tmp_path):
        import os

        real = tmp_path / "real"
        (real / "20260801").mkdir(parents=True)
        f = real / "20260801" / "old.txt"
        f.write_text("x")
        aged = time.time() - 31 * 86400
        os.utime(f, (aged, aged))
        link = tmp_path / "drops"
        link.symlink_to(real, target_is_directory=True)
        assert web._hq_drop_sweep(link) == 0 and f.exists()

    def test_a_successful_drop_sweeps_old_files(self, running_server, panel, tmp_path):
        import os

        old = _drops_dir(tmp_path) / "20260801" / "stale.md"
        old.parent.mkdir(parents=True)
        old.write_text("stale")
        aged = time.time() - 40 * 86400
        os.utime(old, (aged, aged))
        host, port = running_server
        assert panel.start(80, 24)[0]
        status, body, _ = _drop(host, port, _token(host, port), "fresh.md", b"fresh")
        assert status == 200, body
        assert not old.exists() and Path(json.loads(body)["path"]).exists()

    def test_a_stalled_body_times_out_and_leaves_no_partial_file(
        self, running_server, panel, tmp_path, monkeypatch
    ):
        import socket

        monkeypatch.setattr(web, "HQ_DROP_READ_TIMEOUT_S", 0.5)
        host, port = running_server
        assert panel.start(80, 24)[0]
        token = _token(host, port)
        s = socket.create_connection((host, port), timeout=5)
        try:
            head = (
                f"POST /hq/drop HTTP/1.1\r\nHost: {host}:{port}\r\n"
                f"Origin: http://{host}:{port}\r\nX-CSRF-Token: {token}\r\n"
                "X-Filename: slow.txt\r\nContent-Length: 1000\r\n\r\n"
            )
            s.sendall(head.encode() + b"only ten b")
            resp = s.recv(4096)
        finally:
            s.close()
        assert b" 408 " in resp.split(b"\r\n", 1)[0]
        assert _all_drops(tmp_path) == []
