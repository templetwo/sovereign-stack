"""Cockpit integration (2026-10-10): file drop to path, working/waiting fields,
the comms line, and the polish pass. Same isolation as test_hq_panel_routes.py:
an ephemeral-port server, SOVEREIGN_ROOT in tmp_path, a /bin/sh child, every
external reader stubbed. Nothing here writes under the real ~/.sovereign."""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
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
    return root / "hq" / "cockpit" / "drops"


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

    def test_missing_content_length_is_refused(self, running_server, panel, tmp_path):
        host, port = running_server
        panel.start(80, 24)
        status, _, _ = _drop(
            host, port, _token(host, port), "a.txt", b"", extra={"Transfer-Encoding": "chunked"}
        )
        assert status == 411
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
            "hp.seq_idle_seconds",
            "'unknown'",
            "'working'",
            "'waiting'",
            "DOWN · exit ${exit}",
        ):
            assert needle in js, needle
        assert "LIVE · " not in js
