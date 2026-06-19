"""Tests for scribe/resident.py — resident lifecycle, dual-refresh, provenance.

Covers per the design spec (scribe_resident_design.md):
  (3) Resident lifecycle: idempotency, immortal TTL exemption, re-spawn after store drop.
  (4) Fork-C freshness: map mtime trigger; chronicle TTL trigger (spy on
      build_scribe_chronicle_context); no-change -> no rebuild.

Isolation strategy (per Lesson #582 + advisor):
  - Patch module-level constants directly via monkeypatch.setattr, not just env vars,
    because they are bound at import time.
  - Stub build_scribe_chronicle_context on context_builder to avoid hitting real chronicle.
  - Inject a fresh ScribeSessionStore and reset _resident_* singletons in setup.
  - Never allow real API calls — _client_cache guarded via bridge_integration module.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sovereign_stack.scribe import bridge_integration as bi_mod
from sovereign_stack.scribe import resident as resident_mod
from sovereign_stack.scribe.session import ScribeSession, ScribeSessionStore

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_fake_map(tmp_path: Path) -> Path:
    """Write a minimal primary_routes.md with known route bullet lines."""
    stack_map_dir = tmp_path / "stack_map"
    stack_map_dir.mkdir(parents=True, exist_ok=True)
    routes_md = stack_map_dir / "primary_routes.md"
    routes_md.write_text(
        "# Primary Routes\n\n"
        "## family-one\n\n"
        "- my_toolkit (tool): returns live toolkit\n"
        "- recall_insights (tool): query chronicle\n"
        "- where_did_i_leave_off (tool): boot ritual\n",
        encoding="utf-8",
    )
    return routes_md


@pytest.fixture()
def isolated_resident(tmp_path, monkeypatch):
    """Full isolation fixture: fresh store, patched module constants, stubbed context."""
    # Patch the SOVEREIGN_ROOT and derived paths directly on the module
    fake_root = tmp_path / ".sovereign"
    fake_root.mkdir(parents=True)
    map_file = _make_fake_map(fake_root)

    monkeypatch.setattr(resident_mod, "SOVEREIGN_ROOT", fake_root)
    monkeypatch.setattr(resident_mod, "STACK_MAP_PATH", map_file)
    state_dir = fake_root / "scribe_threads" / "_resident"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "state.json"
    monkeypatch.setattr(resident_mod, "RESIDENT_STATE_PATH", state_path)

    # Stub context builder so tests never touch the real chronicle
    monkeypatch.setattr(
        "sovereign_stack.scribe.context_builder.build_scribe_chronicle_context",
        MagicMock(return_value="=== stubbed chronicle context ==="),
    )

    # Give bridge_integration a fresh isolated store
    fresh_store = ScribeSessionStore(archive_root=tmp_path / "archive")
    monkeypatch.setattr(bi_mod, "_scribe_store", fresh_store)

    # Reset resident module-level singletons
    resident_mod._reset_resident_for_tests()

    yield {
        "root": fake_root,
        "map_file": map_file,
        "state_path": state_path,
        "store": fresh_store,
    }

    # Teardown: always reset so later tests don't inherit state
    resident_mod._reset_resident_for_tests()
    monkeypatch.setattr(bi_mod, "_scribe_store", ScribeSessionStore())


# ---------------------------------------------------------------------------
# (3a) Resident lifecycle: idempotency
# ---------------------------------------------------------------------------


class TestResidentLifecycleIdempotency:
    def test_two_calls_return_same_session_id(self, isolated_resident):
        """ensure_resident_scribe() is idempotent — same session_id on two calls."""
        r1 = resident_mod.ensure_resident_scribe()
        r2 = resident_mod.ensure_resident_scribe()
        assert r1 is not None
        assert r2 is not None
        assert r1.session_id == r2.session_id

    def test_resident_registered_in_store(self, isolated_resident):
        """After spawn, the resident lives in the session store."""
        r = resident_mod.ensure_resident_scribe()
        store = bi_mod.get_store()
        registered = store._sessions.get(r.session_id)
        assert registered is r

    def test_resident_is_immortal(self, isolated_resident):
        """Resident session must have immortal=True."""
        r = resident_mod.ensure_resident_scribe()
        assert r.immortal is True

    def test_provenance_marker_written(self, isolated_resident):
        """State JSON marker must be written after spawn."""
        env = isolated_resident
        resident_mod.ensure_resident_scribe()
        assert env["state_path"].exists(), "provenance marker not written"
        state = json.loads(env["state_path"].read_text())
        assert "session_id" in state
        assert "created_at" in state
        assert "model" in state

    def test_marker_has_correct_session_id(self, isolated_resident):
        """Marker session_id matches the returned session."""
        env = isolated_resident
        r = resident_mod.ensure_resident_scribe()
        state = json.loads(env["state_path"].read_text())
        assert state["session_id"] == r.session_id


# ---------------------------------------------------------------------------
# (3b) Immortal -> expired property
# ---------------------------------------------------------------------------


class TestImmortalExpiredProperty:
    def test_immortal_resident_never_expires_regardless_of_age(self, isolated_resident):
        """expired property returns False for immortal session even with old last_message_at."""
        r = resident_mod.ensure_resident_scribe()
        assert r.immortal is True
        # Backdate last_message_at to 10 years ago
        r.last_message_at = "2016-01-01T00:00:00+00:00"
        # TTL machinery: expires_at_unix would be 2016-01-01 + 240min — definitely past
        assert r.expired is False, "immortal resident must not expire even with ancient timestamp"

    def test_non_immortal_session_can_expire(self):
        """Control: normal sessions still expire when TTL elapses."""
        session = ScribeSession.create(ttl_minutes=1)
        # Backdate so it's definitely expired
        session.last_message_at = "2016-01-01T00:00:00+00:00"
        assert session.expired is True


# ---------------------------------------------------------------------------
# (3c) Re-spawn after store drop
# ---------------------------------------------------------------------------


class TestReSpawnAfterStoreDrop:
    def test_drop_store_yields_fresh_resident(self, tmp_path, monkeypatch):
        """Re-spawn after clearing the store yields a fresh resident with a new session_id
        and rewrites the provenance marker."""
        # Isolated setup
        fake_root = tmp_path / ".sovereign"
        fake_root.mkdir(parents=True)
        map_file = _make_fake_map(fake_root)
        monkeypatch.setattr(resident_mod, "SOVEREIGN_ROOT", fake_root)
        monkeypatch.setattr(resident_mod, "STACK_MAP_PATH", map_file)
        state_dir = fake_root / "scribe_threads" / "_resident"
        state_dir.mkdir(parents=True)
        state_path = state_dir / "state.json"
        monkeypatch.setattr(resident_mod, "RESIDENT_STATE_PATH", state_path)
        monkeypatch.setattr(
            "sovereign_stack.scribe.context_builder.build_scribe_chronicle_context",
            MagicMock(return_value="=== context ==="),
        )
        resident_mod._reset_resident_for_tests()

        fresh_store = ScribeSessionStore(archive_root=tmp_path / "archive")
        monkeypatch.setattr(bi_mod, "_scribe_store", fresh_store)

        # First spawn
        r1 = resident_mod.ensure_resident_scribe()
        first_id = r1.session_id
        first_marker = json.loads(state_path.read_text())

        # Simulate store being dropped (e.g., eviction)
        fresh_store._sessions.clear()
        # Reset the module-level singleton so it tries to re-spawn
        resident_mod._reset_resident_for_tests()

        # Second spawn
        r2 = resident_mod.ensure_resident_scribe()
        second_id = r2.session_id
        second_marker = json.loads(state_path.read_text())

        # Fresh resident must be different from the evicted one
        assert second_id != first_id, "re-spawn must produce a new session_id"
        assert r2.immortal is True
        assert second_marker["session_id"] == second_id
        assert second_marker["session_id"] != first_marker["session_id"]

        # Cleanup
        resident_mod._reset_resident_for_tests()
        monkeypatch.setattr(bi_mod, "_scribe_store", ScribeSessionStore())


# ---------------------------------------------------------------------------
# (4) Fork-C freshness: dual-refresh triggers
# ---------------------------------------------------------------------------


class TestForkCFreshnessMapMtime:
    def test_map_mtime_forward_triggers_reload(self, isolated_resident, tmp_path):
        """Touching map mtime forward causes refresh_if_stale to reload the map block."""
        env = isolated_resident
        map_file: Path = env["map_file"]

        # Spawn the resident
        r = resident_mod.ensure_resident_scribe()

        # Seed chronicle_context with a map section header so the swap branch activates
        r.chronicle_context = (
            "=== PRIMARY ROUTES MAP (always-loaded navigation) ===\nold content\n"
            "=== OTHER SECTION ===\nother\n"
        )

        # Record the map_built_at at spawn
        map_built_at_before = resident_mod._resident_map_built_at

        # Advance the mtime of map_file past map_built_at using os.utime
        future_ts = map_built_at_before + 60  # 60 seconds into the future
        import os

        os.utime(str(map_file), (future_ts, future_ts))

        # Update the map file content
        map_file.write_text(
            "# Primary Routes\n\n- new_route (tool): new description\n",
            encoding="utf-8",
        )
        # Set the mtime again after writing (write bumps mtime)
        future_ts2 = future_ts + 1
        os.utime(str(map_file), (future_ts2, future_ts2))

        # refresh_if_stale should detect the mtime change and reload
        refreshed = resident_mod.refresh_if_stale(r)
        assert refreshed is True, "refresh_if_stale should return True when map mtime advanced"
        assert resident_mod._resident_map_built_at >= future_ts2

    def test_no_change_no_rebuild(self, isolated_resident, monkeypatch):
        """When map mtime and TTL have not changed, refresh_if_stale does not rebuild."""

        # Use a real spy on build_scribe_chronicle_context
        spy = MagicMock(return_value="=== context ===")
        monkeypatch.setattr(
            "sovereign_stack.scribe.context_builder.build_scribe_chronicle_context",
            spy,
        )

        r = resident_mod.ensure_resident_scribe()
        # Reset mock after spawn (spawn calls it once)
        spy.reset_mock()

        # Ensure context_built_at is recent (well within TTL)
        resident_mod._resident_context_built_at = time.time()
        # Map mtime matches what the resident has
        resident_mod._resident_map_built_at = resident_mod._map_mtime()

        refreshed = resident_mod.refresh_if_stale(r)
        assert refreshed is False, "no refresh should happen when nothing changed"
        spy.assert_not_called()


class TestForkCFreshnessTTL:
    def test_ttl_expiry_rebuilds_chronicle_context(self, isolated_resident, monkeypatch):
        """Advancing clock past SCRIBE_CONTEXT_TTL_MINUTES triggers chronicle context rebuild."""

        spy = MagicMock(return_value="=== freshly rebuilt context ===")
        monkeypatch.setattr(
            "sovereign_stack.scribe.context_builder.build_scribe_chronicle_context",
            spy,
        )

        r = resident_mod.ensure_resident_scribe()
        spy.reset_mock()

        # Back-date context_built_at so TTL is exceeded
        # Default TTL is 45 minutes = 2700 seconds
        resident_mod._resident_context_built_at = time.time() - (
            resident_mod.SCRIBE_CONTEXT_TTL_MINUTES * 60 + 10
        )

        refreshed = resident_mod.refresh_if_stale(r)
        assert refreshed is True, "TTL expiry must trigger rebuild"
        spy.assert_called_once()
        assert "freshly rebuilt context" in r.chronicle_context
