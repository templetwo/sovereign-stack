"""Tests for the Claude connector's step-up elevation module (feat/claude-connector).

Covers the Door That Asks client in clients/claude_bridge/elevation.py:
fail-closed unavailability, the request -> pending -> poll -> active/denied
lifecycle, poll throttling, TTL expiry, and the plaintext-once invariant
(the svs_ session token minted at approval is a receipt and must never be
persisted anywhere by this module).

Hermetic: all module-level storage Paths are monkeypatched into tmp_path,
and elevation's httpx usage is replaced by a fake AsyncClient — no network,
nothing under ~/.sovereign is touched.
"""

import asyncio
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

_CLIENTS = Path(__file__).parent.parent / "clients"
if _CLIENTS.exists() and str(_CLIENTS) not in sys.path:
    sys.path.insert(0, str(_CLIENTS))

from claude_bridge import elevation, oauth  # noqa: E402

TOOL = "set_policy"
FAMILY = "fam0123456789abcdef0123"
CLIENT = "client_test_abc"

HAPPY_REQUEST_BODY = {
    "arrival_request_id": "arq_x",
    "code": "harbor-juniper",
    "status": "pending",
    "notification_sent": True,
}

APPROVED_BODY = {
    "status": "approved",
    "session_token": "svs_SECRET_PLAINTEXT",
    "token_id": "tok123",
    "scope": ["read"],
    "expires_at": "2026-07-04T23:59:59+00:00",
    "grant": {
        "code": "harbor-juniper",
        "decided_at": "2026-07-04T12:00:00+00:00",
        "decided_via": "ntfy_tap",
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def hermetic_storage(monkeypatch, tmp_path):
    """Redirect every module-level storage Path into tmp_path."""
    elev_dir = tmp_path / "elevations"
    audit_dir = tmp_path / "audit"
    tokens_dir = tmp_path / "oauth" / "tokens"
    refresh_dir = tmp_path / "oauth" / "refresh"
    codes_dir = tmp_path / "oauth" / "codes"
    for d in (elev_dir, audit_dir, tokens_dir, refresh_dir, codes_dir):
        d.mkdir(parents=True)
    monkeypatch.setattr(elevation, "_ELEV_DIR", elev_dir)
    monkeypatch.setattr(elevation, "_AUDIT_DIR", audit_dir)
    monkeypatch.setattr(elevation, "_AUDIT_LOG", audit_dir / "destructive_calls.jsonl")
    monkeypatch.setattr(oauth, "_TOKENS_DIR", tokens_dir)
    monkeypatch.setattr(oauth, "_REFRESH_DIR", refresh_dir)
    monkeypatch.setattr(oauth, "_CODES_DIR", codes_dir)
    monkeypatch.setattr(oauth, "_CLIENTS_FILE", tmp_path / "oauth_clients.json")
    return tmp_path


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = {} if body is None else body

    def json(self):
        return self._body


class FakeDoor:
    """Call recorder + canned-response queue standing in for the Door."""

    def __init__(self):
        self.post_calls = []
        self.get_calls = []
        self.post_responses = []
        self.get_responses = []
        self.post_exc = None
        self.get_exc = None


@pytest.fixture
def door(monkeypatch):
    door = FakeDoor()

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, json=None):
            door.post_calls.append((url, json))
            if door.post_exc is not None:
                raise door.post_exc
            return door.post_responses.pop(0)

        async def get(self, url):
            door.get_calls.append(url)
            if door.get_exc is not None:
                raise door.get_exc
            return door.get_responses.pop(0)

    fake_httpx = types.SimpleNamespace(
        AsyncClient=FakeAsyncClient,
        HTTPError=httpx.HTTPError,
        ConnectError=httpx.ConnectError,
    )
    monkeypatch.setattr(elevation, "httpx", fake_httpx)
    return door


def _ensure():
    return asyncio.run(elevation.ensure_elevation(TOOL, FAMILY, CLIENT))


def _write_record(**overrides):
    """Write an elevation record directly (bypassing the Door)."""
    rec = {
        "elevation_id": "abcd1234",
        "tool": TOOL,
        "family_id": FAMILY,
        "client_id": CLIENT,
        "rid": "arq_x",
        "code": "harbor-juniper",
        "status": "pending",
        "requested_at": _now().isoformat(),
        "last_poll_at": None,
        "notification_sent": True,
    }
    rec.update(overrides)
    elevation._save_elevation(TOOL, FAMILY, rec)
    return rec


def _read_record():
    return json.loads(elevation._elev_path(TOOL, FAMILY).read_text())


def _audit_events():
    log = elevation._AUDIT_LOG
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


class TestDoorUnavailable:
    """Fail closed: no reachable/enabled Door means no destructive tier."""

    def test_unreachable_door_fails_closed(self, door):
        door.post_exc = httpx.ConnectError("boom")
        status = _ensure()
        assert status.state == "unavailable"

    def test_gate_disabled_404_is_unavailable(self, door):
        door.post_responses.append(FakeResponse(404))
        status = _ensure()
        assert status.state == "unavailable"

    def test_rate_limited_429_is_unavailable(self, door):
        door.post_responses.append(FakeResponse(429))
        status = _ensure()
        assert status.state == "unavailable"


class TestHappyRequest:
    def test_request_returns_pending_with_code(self, door):
        door.post_responses.append(FakeResponse(201, dict(HAPPY_REQUEST_BODY)))
        status = _ensure()
        assert status.state == "pending"
        assert status.code == "harbor-juniper"

    def test_request_persists_pending_record(self, door):
        door.post_responses.append(FakeResponse(201, dict(HAPPY_REQUEST_BODY)))
        _ensure()
        rec = _read_record()
        assert rec["status"] == "pending"
        assert rec["rid"] == "arq_x"
        assert rec["code"] == "harbor-juniper"

    def test_request_is_audited(self, door):
        door.post_responses.append(FakeResponse(201, dict(HAPPY_REQUEST_BODY)))
        _ensure()
        events = [e["event"] for e in _audit_events()]
        assert "step_up_requested" in events


class TestPollThrottle:
    def test_recent_poll_suppresses_door_get(self, door):
        _write_record(last_poll_at=_now().isoformat())
        status = _ensure()
        assert status.state == "pending"
        assert door.get_calls == []


class TestPollApproved:
    def test_approval_activates_and_keeps_only_the_receipt(self, door, hermetic_storage):
        _write_record(last_poll_at=None)
        door.get_responses.append(FakeResponse(200, dict(APPROVED_BODY)))
        status = _ensure()
        assert status.state == "active"

        rec = _read_record()
        assert rec["status"] == "active"
        assert rec["receipt_token_id"] == "tok123"

        # Plaintext-once invariant: the svs_ session token is a receipt the
        # Door minted, not a credential — it must appear NOWHERE on disk.
        for path in hermetic_storage.rglob("*"):
            if path.is_file():
                assert "svs_SECRET_PLAINTEXT" not in path.read_text(), path


class TestPollDenied:
    def test_denied_then_fresh_request(self, door):
        _write_record(last_poll_at=None)
        door.get_responses.append(FakeResponse(200, {"status": "denied"}))
        status = _ensure()
        assert status.state == "denied"

        # A denied record does not block: the next ensure re-asks the Door.
        door.post_responses.append(FakeResponse(201, dict(HAPPY_REQUEST_BODY)))
        status = _ensure()
        assert status.state == "pending"
        assert len(door.post_calls) == 1


class TestActiveLifecycle:
    def test_expired_active_elevation_re_requests(self, door):
        stale = _now() - timedelta(seconds=elevation.ELEVATION_TTL_SECONDS + 60)
        _write_record(status="active", approved_at=stale.isoformat(), receipt_token_id="tok123")
        door.post_responses.append(FakeResponse(201, dict(HAPPY_REQUEST_BODY)))
        status = _ensure()
        assert status.state == "pending"
        assert len(door.post_calls) == 1

    def test_fresh_active_elevation_needs_no_door_call(self, door):
        _write_record(status="active", approved_at=_now().isoformat(), receipt_token_id="tok123")
        status = _ensure()
        assert status.state == "active"
        assert door.post_calls == []
        assert door.get_calls == []
