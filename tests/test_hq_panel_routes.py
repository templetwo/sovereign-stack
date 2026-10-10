"""HQ cockpit Phase 2: the /hq/* routes, the vendor/ static dir, and the two
new snapshot keys. Server spun up on an ephemeral port like test_dashboard_web.py.
Every external reader is stubbed; nothing here touches the live ~/.sovereign tree,
the bridge, or a real claude."""

from __future__ import annotations

import hashlib
import json
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
