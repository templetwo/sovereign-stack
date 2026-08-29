"""
Tests for Sovereign Console v2 — the additive reskin over the v4 cockpit.

Three things these tests defend, in order of how expensive the failure is:

  1. THE SNAPSHOT CONTRACT IS EXACT, NOT A MEMBERSHIP CHECK.
     The pre-existing `test_snapshot_carries_watchman_key_additively` asserts
     membership only, so an 11th key lands silently. `build_snapshot()`'s
     docstring is the written contract and it names an ORDER; a set
     comparison structurally cannot check order. Every assertion here uses
     `list(snapshot.keys())`.

  2. EVERY READER FAILS SOFT AND SAYS SO.
     Missing file, malformed JSON, missing directory, absent sqlite db —
     each returns None (panel renders "source missing"), never a plausible
     zero. A reader that returns `{"count": 0}` for "the file isn't there"
     is the fail-open shape this house forbids.

  3. NO DEMO DATA, NO innerHTML, NO metabolize.
     Static assertions over the shipped assets and the module source.

External-probe discipline: `fetch_bridge_heartbeat()` and `read_guardian()`
reach outside the process (HTTP to :8100, `lsof`). Every test here that
calls `build_snapshot()` monkeypatches BOTH through the module object and
clears the reader caches first — a module-level TTL cache plus pytest's
arbitrary ordering is exactly how a green suite hides a live-system read.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack import connectivity as conn
from sovereign_stack import dashboard_readers as readers
from sovereign_stack import dashboard_web as web

STATIC_DIR = Path(web.__file__).parent / "dashboard_web_static"

# Captured at import, BEFORE the autouse stub below can replace them. The
# two external-reader tests restore these so they exercise the real
# function (with its inner probe stubbed) rather than the module-wide
# lambda that keeps every other test off the live bridge.
_REAL_READ_GUARDIAN = readers.read_guardian
_REAL_FETCH_HEARTBEAT = readers.fetch_bridge_heartbeat


# ── The exact snapshot contract ──────────────────────────────────────────────

LEGACY_KEYS = [
    "timestamp",
    "connectivity",
    "halts_count",
    "decisions_count",
    "unacked_honks",
    "listener_stale",
    "latest",
    "feed",
    "service_telemetry",
    "watchman",
]

V2_KEYS = [
    "spiral",
    "self_model",
    "guardian",
    "open_threads",
    "arrival_gate",
    "bridge_heartbeat",
    "lineage",
]


@pytest.fixture(autouse=True)
def _no_external_probes(monkeypatch):
    """Neutralize the two readers that leave the process, for EVERY test in
    this module, and drop the caches on both sides of the test.

    Patched on the module object (`monkeypatch.setattr(readers, ...)`) —
    which only works because build_snapshot() calls them as
    `dashboard_readers.fetch_bridge_heartbeat()`, never via a
    `from ... import` binding captured at import time.
    """
    readers.reset_caches()
    monkeypatch.setattr(readers, "fetch_bridge_heartbeat", lambda: None)
    monkeypatch.setattr(readers, "read_guardian", lambda: None)
    yield
    readers.reset_caches()


@pytest.fixture
def isolated_snapshot(monkeypatch, tmp_path):
    """build_snapshot() against a tmp SOVEREIGN_ROOT with launchctl/http
    probes stubbed — same isolation discipline as test_dashboard_web.py."""
    monkeypatch.setattr(conn, "_launchctl_print_text", lambda label: None)
    monkeypatch.setattr(
        conn,
        "_http_probe",
        lambda url, timeout=2.0: {"http_status": None, "body": "", "error": "mocked"},
    )
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    return tmp_path


class TestSnapshotKeyContract:
    def test_snapshot_key_order_is_exact(self, isolated_snapshot):
        """The 10 legacy keys keep their exact names AND order; the v2 keys
        are appended as 11..16. Fails on unfixed code (only 10 keys)."""
        snapshot = web.build_snapshot()
        assert list(snapshot.keys()) == LEGACY_KEYS + V2_KEYS

    def test_every_v2_key_is_individually_nullable(self, isolated_snapshot):
        """A bare tmp root has no spiral_state.json, no self_model.json, no
        open_threads dir, no session_tokens.db — each key must be present
        and None, never an invented empty structure."""
        snapshot = web.build_snapshot()
        for key in V2_KEYS:
            assert key in snapshot, key
            assert snapshot[key] is None, f"{key} should be None on a bare root"

    def test_legacy_key_structure_untouched(self, isolated_snapshot):
        snapshot = web.build_snapshot()
        assert isinstance(snapshot["unacked_honks"], int)
        assert isinstance(snapshot["listener_stale"], bool)
        assert isinstance(snapshot["feed"], list)
        assert "endpoints" in snapshot["connectivity"]
        assert snapshot["watchman"]["sweeps"] == []


# ── read_spiral_state ────────────────────────────────────────────────────────


class TestSpiralReader:
    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        assert readers.read_spiral_state() is None

    def test_malformed_json_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        (tmp_path / "spiral_state.json").write_text("{not json at all")
        assert readers.read_spiral_state() is None

    def test_reads_phase_depth_and_tool_calls(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        started = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        (tmp_path / "spiral_state.json").write_text(
            json.dumps(
                {
                    "session_id": "spiral_test",
                    "current_phase": "Counter-Perspectives",
                    "reflection_depth": 2,
                    "tool_call_count": 3250,
                    "started": started,
                    "phase_history": [{"to_phase": "A"}, {"to_phase": "B"}],
                }
            )
        )
        out = readers.read_spiral_state()
        assert out["current_phase"] == "Counter-Perspectives"
        assert out["reflection_depth"] == 2
        assert out["tool_call_count"] == 3250
        assert out["phase_history_count"] == 2
        # Provenance is mandatory on every reader's output.
        assert out["age_seconds"] is not None
        assert out["age_seconds"] >= 0
        assert 10000 < out["session_age_seconds"] < 11000

    def test_partial_file_does_not_invent_values(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        (tmp_path / "spiral_state.json").write_text(json.dumps({"session_id": "x"}))
        out = readers.read_spiral_state()
        assert out["current_phase"] is None
        assert out["tool_call_count"] is None
        assert out["reflection_depth"] is None


# ── read_self_model ──────────────────────────────────────────────────────────


class TestSelfModelReader:
    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        assert readers.read_self_model() is None

    def test_malformed_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        (tmp_path / "self_model.json").write_text("[[[")
        assert readers.read_self_model() is None

    def test_age_comes_from_newest_entry_not_file_mtime(self, monkeypatch, tmp_path):
        """A backup or an rsync touches mtime; the record's own timestamp is
        the only honest age. Live self_model.json was last WRITTEN
        2026-05-25 while its mtime moves around."""
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        newer = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        (tmp_path / "self_model.json").write_text(
            json.dumps(
                {
                    "strength": [{"observation": "synthesis", "timestamp": old}],
                    "drift": [{"observation": "poetic", "timestamp": newer}],
                }
            )
        )
        out = readers.read_self_model()
        age_days = out["age_seconds"] / 86400
        assert 29 < age_days < 31, age_days
        assert out["stale"] is True  # 30d > the 14d degrade threshold

    def test_fresh_model_is_not_stale(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        now = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        (tmp_path / "self_model.json").write_text(
            json.dumps({"tendency": [{"observation": "t", "timestamp": now}]})
        )
        out = readers.read_self_model()
        assert out["stale"] is False
        assert out["stale_after_days"] == 14

    def test_degraded_model_still_carries_its_content(self, monkeypatch, tmp_path):
        """Past the staleness threshold the panel degrades VISUALLY — the
        reader must not blank the text, or the page shows an empty card
        where a 3-month-old truth belongs."""
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        old = (datetime.now(timezone.utc) - timedelta(days=95)).isoformat()
        (tmp_path / "self_model.json").write_text(
            json.dumps({"blind_spot": [{"observation": "declares early", "timestamp": old}]})
        )
        out = readers.read_self_model()
        assert out["stale"] is True
        cats = {e["category"]: e for e in out["entries"]}
        assert cats["blind_spot"]["observation"] == "declares early"

    def test_newest_entry_per_category_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        a = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        b = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        (tmp_path / "self_model.json").write_text(
            json.dumps(
                {
                    "drift": [
                        {"observation": "older", "timestamp": a},
                        {"observation": "newer", "timestamp": b},
                    ]
                }
            )
        )
        out = readers.read_self_model()
        assert out["entries"][0]["observation"] == "newer"


# ── read_open_threads ────────────────────────────────────────────────────────


def _write_thread(root: Path, name: str, *, resolved, question="q?", sub=None):
    d = root / "chronicle" / "open_threads"
    if sub:
        d = d / sub
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "thread_id": name,
        "question": question,
        "context": "",
        "domain": name,
        "layer": "open_thread",
        "resolved": resolved,
    }
    (d / f"{name}.jsonl").write_text(json.dumps(rec) + "\n")


class TestOpenThreadsReader:
    def test_missing_directory_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        assert readers.read_open_threads() is None

    def test_string_false_counts_as_unresolved(self, monkeypatch, tmp_path):
        """THE TRAP: live records carry `"resolved": "False"` — the STRING.
        A naive truthiness test (`if rec["resolved"]`) reads every live
        unresolved thread as RESOLVED and reports 0 open threads on a box
        with ~180."""
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        _write_thread(tmp_path, "a", resolved="False")
        _write_thread(tmp_path, "b", resolved=False)
        _write_thread(tmp_path, "c", resolved="false")
        _write_thread(tmp_path, "d", resolved=None)
        out = readers.read_open_threads()
        assert out["unresolved_count"] == 4

    def test_string_true_counts_as_resolved(self, monkeypatch, tmp_path):
        """The other direction of the same trap — `"resolved": "True"` is a
        non-empty string and must NOT be counted open."""
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        _write_thread(tmp_path, "open-one", resolved="False")
        _write_thread(tmp_path, "closed-str", resolved="True")
        _write_thread(tmp_path, "closed-bool", resolved=True)
        out = readers.read_open_threads()
        assert out["unresolved_count"] == 1
        assert out["threads"][0]["domain"] == "open-one"

    def test_recursive_scan(self, monkeypatch, tmp_path):
        """`_list_paths(..., recursive=True)` is what dashboard.py uses for
        this tree; a flat glob misses nested shards."""
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        _write_thread(tmp_path, "flat", resolved="False")
        _write_thread(tmp_path, "nested", resolved="False", sub="deep/deeper")
        out = readers.read_open_threads()
        assert out["unresolved_count"] == 2

    def test_malformed_lines_skipped_not_fatal(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        _write_thread(tmp_path, "good", resolved="False")
        d = tmp_path / "chronicle" / "open_threads"
        (d / "bad.jsonl").write_text('{oops\n{"also": bad}\n')
        out = readers.read_open_threads()
        assert out["unresolved_count"] == 1
        assert out["malformed_skipped"] == 2

    def test_limit_caps_rendered_rows_but_not_the_count(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        for i in range(9):
            _write_thread(tmp_path, f"t{i}", resolved="False")
        out = readers.read_open_threads(limit=4)
        assert out["unresolved_count"] == 9
        assert len(out["threads"]) == 4


# ── read_arrival_gate ────────────────────────────────────────────────────────


ARRIVAL_DDL = """
CREATE TABLE arrival_requests (
  rid TEXT PRIMARY KEY, code TEXT, source_instance TEXT, seat_description TEXT,
  requested_scope TEXT, granted_scope TEXT, ttl_hours INTEGER, status TEXT,
  created_at TEXT, decided_at TEXT, decided_via TEXT, requester_ip TEXT,
  token_id TEXT, last_poll_at TEXT, poll_violations INTEGER
);
"""


def _make_arrival_db(root: Path, rows):
    d = root / "bridge"
    d.mkdir(parents=True, exist_ok=True)
    db = d / "session_tokens.db"
    c = sqlite3.connect(db)
    c.executescript(ARRIVAL_DDL)
    for rid, code, status, created_at in rows:
        c.execute(
            "INSERT INTO arrival_requests (rid, code, source_instance, "
            "seat_description, requested_scope, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, code, "seat", "desc", '["read"]', status, created_at),
        )
    c.commit()
    c.close()
    return db


class TestArrivalGateReader:
    def test_missing_db_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        assert readers.read_arrival_gate() is None

    def test_read_only_open_does_not_create_the_db(self, monkeypatch, tmp_path):
        """mode=ro must never bring the file into existence — a dashboard
        that creates the bridge's token db is a write we did not authorize."""
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        readers.read_arrival_gate()
        assert not (tmp_path / "bridge" / "session_tokens.db").exists()

    def test_fresh_pending_surfaces(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        fresh = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        _make_arrival_db(tmp_path, [("r1", "kestrel-birch", "pending", fresh)])
        out = readers.read_arrival_gate()
        assert out["pending_count"] == 1
        assert out["pending"][0]["code"] == "kestrel-birch"
        assert out["status"] == "asked"

    def test_pending_older_than_900s_is_expired_by_us(self, monkeypatch, tmp_path):
        """_expire_stale only runs on the BRIDGE's own connection, so a naive
        read shows a long-dead request as live. We apply the 900s cutoff
        ourselves — the half the live box (0 pending rows) cannot prove."""
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        stale = (datetime.now(timezone.utc) - timedelta(seconds=901)).isoformat()
        _make_arrival_db(tmp_path, [("r1", "old-code", "pending", stale)])
        out = readers.read_arrival_gate()
        assert out["pending_count"] == 0
        assert out["status"] == "quiet"
        assert out["expired_by_cutoff"] == 1

    def test_cutoff_boundary_and_mixed_statuses(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        now = datetime.now(timezone.utc)
        _make_arrival_db(
            tmp_path,
            [
                ("r1", "fresh", "pending", (now - timedelta(seconds=10)).isoformat()),
                ("r2", "stale", "pending", (now - timedelta(seconds=901)).isoformat()),
                ("r3", "done", "consumed", (now - timedelta(seconds=10)).isoformat()),
                ("r4", "gone", "expired", (now - timedelta(seconds=10)).isoformat()),
            ],
        )
        out = readers.read_arrival_gate()
        assert out["pending_count"] == 1
        assert out["pending"][0]["code"] == "fresh"
        assert out["pending_window_seconds"] == 900

    def test_naive_created_at_does_not_expire_everything(self, monkeypatch, tmp_path):
        """A tz-naive ISO string compared against an aware `now` raises, or
        (worse) silently reads 4 hours off and expires every request."""
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        _make_arrival_db(tmp_path, [("r1", "naive", "pending", naive)])
        out = readers.read_arrival_gate()
        assert out["pending_count"] == 1

    def test_tokens_are_declared_unavailable_never_empty(self, monkeypatch, tmp_path):
        """GET /api/admin/tokens is MASTER-token-only. An empty token list
        reads as 'no tokens exist' — the fail-open shape. The reader states
        unavailability instead."""
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        fresh = datetime.now(timezone.utc).isoformat()
        _make_arrival_db(tmp_path, [("r1", "c", "pending", fresh)])
        out = readers.read_arrival_gate()
        assert out["tokens_available"] is False
        assert "master token" in out["tokens_note"].lower()
        assert "tokens" not in out or out.get("tokens") is None

    def test_missing_table_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        d = tmp_path / "bridge"
        d.mkdir(parents=True)
        sqlite3.connect(d / "session_tokens.db").close()
        assert readers.read_arrival_gate() is None


# ── read_lineage_letters ─────────────────────────────────────────────────────


LETTER = """---
type: to_self
from: claude-opus-5
written_at: 2026-08-26T01:27:04Z
title: The Liar Was head -20
---

THE BODY IS A SECRET AND MUST NEVER REACH THE PAGE.
More body text.
"""


class TestLineageReader:
    def test_missing_dir_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        assert readers.read_lineage_letters() is None

    def test_title_and_date_only_never_body(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        d = tmp_path / "comms" / "letters" / "to_self"
        d.mkdir(parents=True)
        (d / "2026-08-26-the-liar-was-head-20.md").write_text(LETTER)
        out = readers.read_lineage_letters()
        assert out["letters"][0]["title"] == "The Liar Was head -20"
        assert out["letters"][0]["bucket"] == "to_self"
        assert out["letters"][0]["date"].startswith("2026-08-26")
        blob = json.dumps(out)
        assert "SECRET" not in blob
        assert "body" not in out["letters"][0]

    def test_filename_fallbacks_when_no_frontmatter(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        d = tmp_path / "comms" / "letters" / "breakthroughs"
        d.mkdir(parents=True)
        (d / "2026-06-30-two-seats-one-correction.md").write_text("no frontmatter here")
        out = readers.read_lineage_letters()
        letter = out["letters"][0]
        assert letter["title"] == "Two Seats One Correction"
        assert letter["date"] == "2026-06-30"

    def test_event_date_frontmatter_is_honored(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        d = tmp_path / "comms" / "letters" / "breakthroughs"
        d.mkdir(parents=True)
        (d / "x.md").write_text(
            "---\ntype: breakthrough\nevent_date: 2026-06-30\ntitle: Two Seats\n---\nbody\n"
        )
        out = readers.read_lineage_letters()
        assert out["letters"][0]["date"] == "2026-06-30"

    def test_counts_all_buckets_and_caps_rows(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        base = tmp_path / "comms" / "letters"
        for bucket in ("to_arrival", "breakthroughs", "to_self"):
            d = base / bucket
            d.mkdir(parents=True)
            for i in range(4):
                (d / f"2026-01-0{i + 1}-letter-{i}.md").write_text("---\ntitle: T\n---\nb")
        out = readers.read_lineage_letters(limit=5)
        assert out["counts"]["to_self"] == 4
        assert out["total"] == 12
        assert len(out["letters"]) == 5


# ── Guardian + heartbeat: cache + fail-soft ──────────────────────────────────


class TestExternalReaders:
    def test_guardian_cache_is_keyed_and_resettable(self, monkeypatch):
        """A TTL cache that survives reset_caches() makes test order
        load-bearing — a live `lsof` result would leak into a later test
        asserting None."""
        calls = []

        def fake_probe():
            calls.append(1)
            return (["a listener line"], {"ollama": True})

        monkeypatch.setattr(readers, "read_guardian", _REAL_READ_GUARDIAN)
        monkeypatch.setattr(readers, "_guardian_probe", fake_probe)
        readers.reset_caches()
        first = readers.read_guardian()
        second = readers.read_guardian()
        assert len(calls) == 1  # second call served from cache
        assert first["health_score"] == second["health_score"]
        assert first["cache_ttl_seconds"] >= 30
        readers.reset_caches()
        readers.read_guardian()
        assert len(calls) == 2  # reset actually dropped it

    def test_guardian_fails_soft_when_probe_raises(self, monkeypatch):
        def boom():
            raise OSError("lsof missing")

        monkeypatch.setattr(readers, "read_guardian", _REAL_READ_GUARDIAN)
        monkeypatch.setattr(readers, "_guardian_probe", boom)
        readers.reset_caches()
        assert readers.read_guardian() is None

    def test_heartbeat_fails_soft_and_caches(self, monkeypatch):
        calls = []

        def fake_fetch(url, timeout):
            calls.append(url)
            return {
                "version": "1.21.0",
                "tools": 98,
                "source_commit": "abc1234",
                "bridge_commit": "def5678",
                "service_start_time": "2026-08-28T00:00:00Z",
                "aperture": {"surfaces": {"insights": {"on_disk": 3416}}},
                "gate": {"total_pending_all_substrates": 124},
                "arrival_gate": True,
            }

        monkeypatch.setattr(readers, "fetch_bridge_heartbeat", _REAL_FETCH_HEARTBEAT)
        monkeypatch.setattr(readers, "_http_get_json", fake_fetch)
        readers.reset_caches()
        out = readers.fetch_bridge_heartbeat()
        readers.fetch_bridge_heartbeat()
        assert len(calls) == 1
        assert out["version"] == "1.21.0"
        assert out["tools"] == 98
        assert out["aperture_surfaces"]["insights"]["on_disk"] == 3416
        assert out["gate_total_pending_all_substrates"] == 124
        assert out["cache_ttl_seconds"] >= 10
        assert out["age_seconds"] is not None

    def test_heartbeat_none_when_unreachable(self, monkeypatch):
        def boom(url, timeout):
            raise OSError("connection refused")

        monkeypatch.setattr(readers, "fetch_bridge_heartbeat", _REAL_FETCH_HEARTBEAT)
        monkeypatch.setattr(readers, "_http_get_json", boom)
        readers.reset_caches()
        assert readers.fetch_bridge_heartbeat() is None

    def test_heartbeat_uses_no_auth_header(self, monkeypatch):
        """No bridge token in server memory, ever — GET /api/heartbeat is
        the no-auth door and that is the whole reason this console needs
        no credential."""
        src = Path(readers.__file__).read_text()
        assert "Authorization" not in src
        assert "BRIDGE_TOKEN" not in src


# ── Static asset discipline ──────────────────────────────────────────────────


_UNSAFE_HTML_SINK = re.compile(
    r"\.(innerHTML|outerHTML)\s*(=|\+=)|\.insertAdjacentHTML\s*\(|"
    r"document\.write\s*\(|\bnew\s+Function\s*\("
)
_JS_LINE_COMMENT = re.compile(r"(?<!:)//.*$")


def _strip_js_comments(src: str) -> str:
    """Drop // line comments and /* */ blocks before scanning.

    Load-bearing: app.js's own header prose says "never innerHTML,
    anywhere" and the word appears in two comments. A naive substring grep
    therefore FAILS on today's clean code and would be debugged as a real
    finding. We match on the SINK SYNTAX (assignment / call), and strip
    comments first so a future comment can't hide a real sink either.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(_JS_LINE_COMMENT.sub("", ln) for ln in src.splitlines())


class TestStaticAssets:
    def test_no_html_injection_sinks_in_any_js(self):
        offenders = []
        for js in sorted(STATIC_DIR.glob("*.js")):
            code = _strip_js_comments(js.read_text())
            for i, line in enumerate(code.splitlines(), 1):
                if _UNSAFE_HTML_SINK.search(line):
                    offenders.append(f"{js.name}:{i}: {line.strip()}")
        assert not offenders, "HTML-injection sink in a textContent-only console:\n" + "\n".join(
            offenders
        )

    @pytest.mark.parametrize(
        "element_id",
        [
            # v4 must-survive affordances, by the id the code binds to.
            "search-input",
            "incident-strip",
            "incident-segs",
            "ecg",
            "ecg-group",
            "ecg-path-1",
            "ecg-path-2",
            "timeline",
            "timeline-dots",
            "timeline-brush",
            "theme-toggle",
            "svc-list",
            "topology",
            "chip-row",
            "feed",
            "chronicle-list",
            "watchman-list",
            "wm-summary",
            "latency-now",
            "latency-p95",
            "latency-rps",
            "latency-chart",
            "throughput-chart",
            "status-pill",
            "status-dot",
            "status-label",
            "status-uptext",
            "clock-time",
            "clock-date",
            "stat-version",
            "stat-tools",
            "poll-status",
            "svc-counts",
            "app",
            "cockpit",
        ],
    )
    def test_must_survive_element_id_present(self, element_id):
        html = (STATIC_DIR / "index.html").read_text()
        assert f'id="{element_id}"' in html, f"must-survive id lost: {element_id}"

    @pytest.mark.parametrize(
        "panel_id",
        [
            "guardian-body",
            "mirror-body",
            "spiral-body",
            "threads-list",
            "arrival-body",
            "lineage-list",
        ],
    )
    def test_v2_panel_shell_present(self, panel_id):
        html = (STATIC_DIR / "index.html").read_text()
        assert f'id="{panel_id}"' in html

    def test_must_survive_functions_still_defined(self):
        js = (STATIC_DIR / "app.js").read_text()
        for fn in (
            "function initSearch",
            "function renderIncident",
            "function handleAck",
            "function initBrush",
            "function initTheme",
            "function handleRestart",
            "function handleTail",
            "function svcHistoryPush",
            "function buildSparkline",
            "function toggleServiceExpanded",
        ):
            assert fn in js, f"must-survive function lost: {fn}"

    def test_ack_stays_signature_scoped(self):
        """The session-global boolean fail-opened once (one inert click hid
        every future incident). ackedSignatures is the fix; it must remain
        a Set keyed per honk."""
        js = (STATIC_DIR / "app.js").read_text()
        assert "ackedSignatures: new Set()" in js
        assert "function honkSignature" in js

    def test_restart_is_two_step_guarded(self):
        js = (STATIC_DIR / "app.js").read_text()
        assert "/actions/restart" in js
        assert "X-CSRF-Token" in js

    def test_timeline_window_is_still_24h(self):
        """The README specifies a 60-min timeline; the shipped brush is 24h
        and 24h is must-survive. The window constant stays a day."""
        js = (STATIC_DIR / "app.js").read_text()
        assert "24 * 3600000" in js

    def test_no_demo_or_seed_data_in_the_console(self):
        """The prototype's seedDemo() is never cleared on going live and its
        numbers persist under a green LIVE badge. No such path ships."""
        js = (STATIC_DIR / "app.js").read_text()
        lowered = _strip_js_comments(js).lower()
        for banned in ("seeddemo", "demodata", "demo_data", "fakedata", "mockdata"):
            assert banned not in lowered, banned

    def test_no_bridge_token_anywhere_in_the_frontend(self):
        """The README's localStorage {url, token} would persist the MASTER
        bridge token to browser disk. Nothing in this console has a token."""
        for name in ("app.js", "index.html"):
            text = (STATIC_DIR / name).read_text().lower()
            assert "bearer" not in text
            assert "bridge_token" not in text
            assert "ss-console-v2" not in text

    def test_google_fonts_link_with_fallbacks(self):
        html = (STATIC_DIR / "index.html").read_text()
        assert "fonts.googleapis.com" in html
        css = (STATIC_DIR / "style.css").read_text()
        assert "JetBrains Mono" in css
        assert "Space Grotesk" in css

    def test_aurora_honors_reduced_motion(self):
        css = (STATIC_DIR / "style.css").read_text()
        assert "prefers-reduced-motion" in css


# ── metabolize is disqualified as a polled source ────────────────────────────


class TestMetabolizeIsNeverCalled:
    def test_no_metabolize_identifier_in_dashboard_sources(self):
        """metabolize WRITES to metabolism_log.jsonl on every detect call —
        polling it every few seconds would add ~3,600 records/day to a log
        that holds 236 total. Two of the six cells it feeds don't even
        exist in its output."""
        for mod in (web, readers):
            src = Path(mod.__file__).read_text()
            # Allow the word inside an explanatory comment; ban any import
            # or call form.
            assert "import metabolism" not in src
            assert "from .metabolism" not in src
            assert "metabolize(" not in src
            assert "handle_metabolism_tool" not in src

    def test_build_snapshot_does_not_touch_metabolism_at_runtime(
        self, monkeypatch, isolated_snapshot
    ):
        """Source-grep alone is weak. Make the module explode if reached."""
        from sovereign_stack import metabolism

        def tripwire(*a, **k):
            raise AssertionError("build_snapshot() reached metabolism")

        monkeypatch.setattr(metabolism, "_load_all_insights", tripwire)
        monkeypatch.setattr(metabolism, "_load_all_threads", tripwire)
        monkeypatch.setattr(metabolism, "handle_metabolism_tool", tripwire)
        snapshot = web.build_snapshot()
        assert list(snapshot.keys()) == LEGACY_KEYS + V2_KEYS

    def test_metabolism_log_is_not_written_by_a_snapshot(self, isolated_snapshot):
        web.build_snapshot()
        assert not (isolated_snapshot / "metabolism_log.jsonl").exists()


# ── Snapshot integration: the v2 keys carry real reader output ───────────────


class TestSnapshotCarriesReaderOutput:
    def test_populated_root_fills_the_v2_keys(self, monkeypatch, isolated_snapshot):
        root = isolated_snapshot
        (root / "spiral_state.json").write_text(
            json.dumps({"current_phase": "Recursive Integration", "tool_call_count": 12})
        )
        (root / "self_model.json").write_text(
            json.dumps(
                {
                    "strength": [
                        {
                            "observation": "synthesis",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    ]
                }
            )
        )
        _write_thread(root, "domain-a", resolved="False")
        fresh = datetime.now(timezone.utc).isoformat()
        _make_arrival_db(root, [("r1", "code-word", "pending", fresh)])

        snapshot = web.build_snapshot()
        assert snapshot["spiral"]["current_phase"] == "Recursive Integration"
        assert snapshot["self_model"]["entries"][0]["category"] == "strength"
        assert snapshot["open_threads"]["unresolved_count"] == 1
        assert snapshot["arrival_gate"]["pending_count"] == 1
        # Still stubbed off by the autouse fixture — proves nullability.
        assert snapshot["guardian"] is None
        assert snapshot["bridge_heartbeat"] is None

    def test_one_broken_reader_does_not_kill_the_snapshot(self, monkeypatch, isolated_snapshot):
        """Every v2 section is individually fail-soft: a reader that raises
        yields None for its key, not a 500 on /snapshot.json."""

        def boom():
            raise RuntimeError("reader exploded")

        monkeypatch.setattr(readers, "read_spiral_state", boom)
        snapshot = web.build_snapshot()
        assert snapshot["spiral"] is None
        assert list(snapshot.keys()) == LEGACY_KEYS + V2_KEYS

    def test_every_v2_key_carries_provenance(self, monkeypatch, isolated_snapshot):
        """Per-panel provenance: a panel cannot render staleness the server
        never told it about."""
        root = isolated_snapshot
        (root / "spiral_state.json").write_text(json.dumps({"current_phase": "P"}))
        (root / "self_model.json").write_text(
            json.dumps(
                {
                    "drift": [
                        {"observation": "d", "timestamp": datetime.now(timezone.utc).isoformat()}
                    ]
                }
            )
        )
        _write_thread(root, "d", resolved="False")
        _make_arrival_db(root, [("r", "c", "pending", datetime.now(timezone.utc).isoformat())])
        snapshot = web.build_snapshot()
        for key in ("spiral", "self_model", "open_threads", "arrival_gate"):
            section = snapshot[key]
            assert section is not None, key
            assert "age_seconds" in section, key
            assert "source" in section, key
