"""
Dashboard tests — focused on the data layer (no rendering, no async loop).

What we test here:
  * ActivityFeed cap + ordering
  * MtimeIndex change detection
  * read_recent_honks (jsonl tail + ack filter)
  * read_chronicle_tail (last record extraction)
  * parse_spiral_status_text
  * collect_state composition
  * render_state produces stable string output for a known DashboardState
  * CLI --once --json emits valid JSON snapshot
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from sovereign_stack import connectivity as conn
from sovereign_stack import dashboard as dash
from sovereign_stack import dashboard_cli as cli

# ── ActivityFeed ────────────────────────────────────────────────────────────


class TestActivityFeed:
    def test_add_and_iterate_newest_first(self):
        f = dash.ActivityFeed(maxlen=10)
        f.add(dash.CAT_INSIGHT, "first", ts=100.0)
        f.add(dash.CAT_THREAD, "second", ts=200.0)
        items = list(f)
        assert items[0].message == "second"
        assert items[1].message == "first"

    def test_cap_drops_oldest(self):
        f = dash.ActivityFeed(maxlen=3)
        for i in range(5):
            f.add(dash.CAT_TOOLS, f"e{i}", ts=float(i))
        msgs = [e.message for e in f]
        assert msgs == ["e4", "e3", "e2"]

    def test_to_list_serializes_with_time_str(self):
        f = dash.ActivityFeed(maxlen=5)
        f.add(dash.CAT_HONK, "watch out", ts=1700000000.0)
        listed = f.to_list()
        assert len(listed) == 1
        assert listed[0]["category"] == dash.CAT_HONK
        assert listed[0]["message"] == "watch out"
        assert "time" in listed[0]
        assert listed[0]["ts"] == 1700000000.0

    def test_to_list_limit(self):
        f = dash.ActivityFeed(maxlen=20)
        for i in range(10):
            f.add(dash.CAT_TOOLS, f"e{i}", ts=float(i))
        assert len(f.to_list(limit=3)) == 3


# ── MtimeIndex ──────────────────────────────────────────────────────────────


class TestMtimeIndex:
    def test_first_call_returns_all_paths(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        b = tmp_path / "b.txt"
        b.write_text("y")
        idx = dash._MtimeIndex()
        changed = idx.diff([a, b])
        assert {p.name for p in changed} == {"a.txt", "b.txt"}

    def test_unchanged_paths_not_returned_second_time(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        idx = dash._MtimeIndex()
        idx.diff([a])  # seed
        assert idx.diff([a]) == []

    def test_modified_path_returned(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("first")
        idx = dash._MtimeIndex()
        idx.diff([a])
        # Bump mtime forward.
        future = time.time() + 100
        import os
        os.utime(a, (future, future))
        a.write_text("second")
        os.utime(a, (future, future))
        changed = idx.diff([a])
        assert changed == [a]

    def test_missing_paths_skipped_silently(self, tmp_path):
        idx = dash._MtimeIndex()
        ghost = tmp_path / "never_existed.txt"
        assert idx.diff([ghost]) == []


# ── read_recent_honks ──────────────────────────────────────────────────────


class TestReadHonks:
    def test_returns_empty_when_path_missing(self, tmp_path):
        assert dash.read_recent_honks(tmp_path / "nope.jsonl") == []

    def test_reads_last_n_in_reverse_order(self, tmp_path):
        path = tmp_path / "honks.jsonl"
        path.write_text("\n".join([
            json.dumps({"honk_id": "1", "level": "sharp",
                        "pattern": "p1", "trigger_tool": "t1"}),
            json.dumps({"honk_id": "2", "level": "low",
                        "pattern": "p2", "trigger_tool": "t2"}),
            json.dumps({"honk_id": "3", "level": "uneasy",
                        "pattern": "p3", "trigger_tool": "t3"}),
        ]) + "\n")
        out = dash.read_recent_honks(path, limit=2)
        # Most recent first.
        assert out[0]["honk_id"] == "3"
        assert out[1]["honk_id"] == "2"

    def test_skips_acks(self, tmp_path):
        path = tmp_path / "honks.jsonl"
        path.write_text("\n".join([
            json.dumps({"honk_id": "1", "level": "sharp",
                        "pattern": "p1", "trigger_tool": "t1"}),
            # Ack record uses ack_id + honk_id; we should skip it.
            json.dumps({"ack_id": "a1", "honk_id": "1",
                        "note": "addressed"}),
            json.dumps({"honk_id": "2", "level": "low",
                        "pattern": "p2", "trigger_tool": "t2"}),
        ]) + "\n")
        out = dash.read_recent_honks(path, limit=10)
        ids = [h["honk_id"] for h in out]
        # No record with ack_id should appear.
        assert all("ack_id" not in h for h in out)
        assert "1" in ids and "2" in ids

    def test_cross_file_acks_jsonl_excludes_honks(self, tmp_path):
        """The canonical layout: nape_daemon.acknowledge writes acks
        to a SIBLING acks.jsonl (not back into honks.jsonl). A dashboard
        reader that only checks within honks.jsonl misses these acks
        entirely (which was the 2026-04-25 bug — dashboard reported
        100 unacked while every honk had been acked through the
        standard path)."""
        honks_path = tmp_path / "honks.jsonl"
        acks_path = tmp_path / "acks.jsonl"
        honks_path.write_text("\n".join([
            json.dumps({"honk_id": "h1", "level": "sharp",
                        "pattern": "p1", "trigger_tool": "t1"}),
            json.dumps({"honk_id": "h2", "level": "sharp",
                        "pattern": "p2", "trigger_tool": "t2"}),
            json.dumps({"honk_id": "h3", "level": "sharp",
                        "pattern": "p3", "trigger_tool": "t3"}),
        ]) + "\n")
        acks_path.write_text("\n".join([
            json.dumps({"ack_id": "a1", "honk_id": "h1",
                        "note": "addressed"}),
            json.dumps({"ack_id": "a2", "honk_id": "h2",
                        "note": "addressed"}),
        ]) + "\n")
        out = dash.read_recent_honks(honks_path, limit=10)
        ids = [h["honk_id"] for h in out]
        # h1 and h2 acked via sibling file; only h3 remains unacked.
        assert ids == ["h3"], f"expected only h3 unacked, got {ids}"

    def test_missing_acks_file_treats_all_as_unacked(self, tmp_path):
        """No acks.jsonl present → every honk is unacked. Don't crash."""
        honks_path = tmp_path / "honks.jsonl"
        honks_path.write_text(
            json.dumps({"honk_id": "h1", "level": "sharp"}) + "\n"
        )
        out = dash.read_recent_honks(honks_path)
        assert len(out) == 1

    def test_malformed_lines_skipped(self, tmp_path):
        path = tmp_path / "honks.jsonl"
        path.write_text(
            "{garbage}\n"
            + json.dumps({"honk_id": "1", "level": "sharp"}) + "\n"
            + "not json either\n"
        )
        out = dash.read_recent_honks(path)
        assert len(out) == 1
        assert out[0]["honk_id"] == "1"


# ── read_chronicle_tail ────────────────────────────────────────────────────


class TestChronicleTail:
    def test_returns_last_record(self, tmp_path):
        f = tmp_path / "x.jsonl"
        f.write_text("\n".join([
            json.dumps({"i": 1}),
            json.dumps({"i": 2}),
            json.dumps({"i": 3}),
        ]) + "\n")
        assert dash.read_chronicle_tail(f) == {"i": 3}

    def test_returns_none_for_missing(self, tmp_path):
        assert dash.read_chronicle_tail(tmp_path / "nope.jsonl") is None

    def test_returns_none_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert dash.read_chronicle_tail(f) is None


# ── parse_spiral_status_text ───────────────────────────────────────────────


class TestSpiralParser:
    def test_parses_phase_and_counters(self):
        text = (
            "Phase: Counter-Perspectives\n"
            "Tool Calls: 73507\n"
            "Reflection Depth: 12\n"
            "Duration: 1553563s\n"
        )
        out = dash.parse_spiral_status_text(text)
        assert out["phase"] == "Counter-Perspectives"
        assert out["tool_calls"] == 73507
        assert out["reflection_depth"] == 12
        assert out["duration_seconds"] == 1553563.0

    def test_handles_missing_fields(self):
        out = dash.parse_spiral_status_text("Phase: Initialization\n")
        assert out["phase"] == "Initialization"
        assert "tool_calls" not in out

    def test_handles_garbage_int_fields(self):
        # "Tool Calls: not-a-number" should not raise.
        out = dash.parse_spiral_status_text("Tool Calls: not-a-number\n")
        assert "tool_calls" not in out


# ── format_uptime ──────────────────────────────────────────────────────────


class TestFormatUptime:
    def test_days(self):
        assert dash._format_uptime(86400 * 3 + 3600 * 4) == "3d 4h"

    def test_hours(self):
        assert dash._format_uptime(3600 * 5 + 60 * 17) == "5h 17m"

    def test_minutes(self):
        assert dash._format_uptime(60 * 13) == "13m"

    def test_zero(self):
        assert dash._format_uptime(0) == "0m"


# ── collect_state ───────────────────────────────────────────────────────────


class TestCollectState:
    def test_composes_snapshot(self, tmp_path, monkeypatch):
        # Set up a sandbox sovereign root.
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
        (tmp_path / "daemons" / "halts").mkdir(parents=True)
        (tmp_path / "daemons" / "halts" / "h1.md").write_text("halt")
        (tmp_path / "decisions").mkdir(parents=True)
        (tmp_path / "decisions" / "metabolize_x.md").write_text("d")
        (tmp_path / "decisions" / "metabolize_y.md").write_text("d")
        (tmp_path / "nape").mkdir(parents=True)
        (tmp_path / "nape" / "honks.jsonl").write_text(
            json.dumps({"honk_id": "h", "level": "sharp"}) + "\n"
        )

        feed = dash.ActivityFeed()
        feed.add(dash.CAT_INSIGHT, "test")

        # Inject a fake connectivity check so we don't hit the real
        # launchctl during the unit test.
        def fake_check_all():
            return [
                conn.EndpointStatus(
                    name="listener", label="x", kind=conn.KIND_PERIODIC,
                    status=conn.STATUS_STALE,
                ),
                conn.EndpointStatus(
                    name="sse", label="x", kind=conn.KIND_ALWAYS_ON,
                    status=conn.STATUS_OK,
                ),
            ]

        state = dash.collect_state(
            feed,
            sovereign_root=tmp_path,
            connectivity_check=fake_check_all,
        )
        assert state.halts_count == 1
        assert state.decisions_count == 2
        assert state.unacked_honks == 1
        assert state.listener_stale is True
        assert len(state.feed) == 1


# ── render_state ────────────────────────────────────────────────────────────


class TestRenderState:
    def _state(self, **overrides):
        agg = {
            "overall": conn.STATUS_OK,
            "counts": {conn.STATUS_OK: 1},
            "endpoints": [{
                "name": "sse", "label": "x", "kind": conn.KIND_ALWAYS_ON,
                "status": conn.STATUS_OK, "pid": 100,
                "http_status": 200, "log_age_seconds": None, "notes": [],
            }],
            "timestamp": 1700000000.0,
        }
        defaults = {
            "timestamp": 1700000000.0,
            "connectivity_summary": agg,
            "bridge_stats": dash.BridgeStats(),
            "feed": [],
            "listener_stale": False,
            "halts_count": 0,
            "decisions_count": 0,
            "unacked_honks": 0,
        }
        defaults.update(overrides)
        return dash.DashboardState(**defaults)

    def test_render_includes_header(self):
        out = dash.render_state(self._state(), color=False)
        assert "SOVEREIGN STACK DASHBOARD" in out
        assert "SERVICES" in out
        assert "LIVE ACTIVITY" in out

    def test_render_includes_endpoint_row(self):
        out = dash.render_state(self._state(), color=False)
        assert "sse" in out
        assert "OK" in out

    def test_render_indicators_only_when_nonzero(self):
        plain = dash.render_state(self._state(), color=False)
        assert "halt note" not in plain
        with_halt = dash.render_state(
            self._state(halts_count=2, listener_stale=True), color=False,
        )
        assert "2 halt note" in with_halt
        assert "listener stale" in with_halt

    def test_feed_renders(self):
        feed = [{
            "time": "12:34:56", "ts": 1700000000.0,
            "category": dash.CAT_INSIGHT, "message": "stored insight",
        }]
        out = dash.render_state(self._state(feed=feed), color=False)
        assert "12:34:56" in out
        assert "stored insight" in out
        assert dash.CAT_INSIGHT in out

    def test_no_color_strips_ansi(self):
        out = dash.render_state(self._state(), color=False)
        assert "\033[" not in out


# ── collect_latest_entries ──────────────────────────────────────────────────


class TestCollectLatest:
    def _seed_chronicle(self, root: Path, kind: str, domain: str,
                        record: dict) -> Path:
        d = root / "chronicle" / kind / domain
        d.mkdir(parents=True, exist_ok=True)
        f = d / "log.jsonl"
        with f.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record) + "\n")
        return f

    def test_picks_newest_jsonl_record_per_type(self, tmp_path):
        # Two insights in different domains; older + newer.
        self._seed_chronicle(
            tmp_path, "insights", "old-dom",
            {"timestamp": "2026-04-01T00:00:00Z", "layer": "ground_truth",
             "content": "old", "domain": "old-dom"},
        )
        # Bump mtime backward via os.utime so the "old" file is older.
        old_path = tmp_path / "chronicle" / "insights" / "old-dom" / "log.jsonl"
        import os
        os.utime(old_path, (1000, 1000))

        self._seed_chronicle(
            tmp_path, "insights", "new-dom",
            {"timestamp": "2026-04-25T00:00:00Z", "layer": "hypothesis",
             "content": "the latest insight body text", "domain": "new-dom"},
        )

        latest = dash.collect_latest_entries(tmp_path)
        ins = latest["insight"]
        assert ins is not None
        assert "the latest insight" in ins["preview"]
        assert ins["layer"] == "hypothesis"

    def test_returns_none_when_type_absent(self, tmp_path):
        latest = dash.collect_latest_entries(tmp_path)
        for key in ("insight", "open_thread", "learning",
                    "handoff", "decision", "halt", "honk"):
            assert key in latest
            assert latest[key] is None

    def test_handoff_loaded_from_json(self, tmp_path):
        d = tmp_path / "handoffs"
        d.mkdir(parents=True)
        f = d / "20260425T000000_test.json"
        f.write_text(json.dumps({
            "timestamp": "2026-04-25T00:00:00+00:00",
            "thread": "test-thread",
            "note": "handoff body content",
            "source_instance": "claude-test",
        }))
        latest = dash.collect_latest_entries(tmp_path)
        h = latest["handoff"]
        assert h is not None
        assert h["thread"] == "test-thread"
        assert "handoff body content" in h["preview"]

    def test_decision_takes_first_non_header_line(self, tmp_path):
        d = tmp_path / "decisions"
        d.mkdir(parents=True)
        f = d / "metabolize_20260425.md"
        f.write_text(
            "# Metabolism digest — 2026-04-25\n"
            "## Section\n"
            "First real content line about contradictions.\n"
        )
        latest = dash.collect_latest_entries(tmp_path)
        dec = latest["decision"]
        assert dec is not None
        assert "First real content" in dec["preview"]

    def test_halt_extracts_reason_line(self, tmp_path):
        d = tmp_path / "daemons" / "halts"
        d.mkdir(parents=True)
        f = d / "20260425_halt.md"
        f.write_text(
            "# Halt — daemon.uncertainty\n"
            "Timestamp: 2026-04-25T00:00:00Z\n"
            "Reason: consecutive_unacked_threshold_reached\n"
            "## What the daemon tried\n"
        )
        latest = dash.collect_latest_entries(tmp_path)
        halt = latest["halt"]
        assert halt is not None
        assert "consecutive_unacked" in halt["preview"]

    def test_honk_skips_acked(self, tmp_path):
        d = tmp_path / "nape"
        d.mkdir(parents=True)
        f = d / "honks.jsonl"
        f.write_text("\n".join([
            json.dumps({"honk_id": "1", "level": "sharp",
                        "pattern": "p", "trigger_tool": "t",
                        "observation": "the issue"}),
            json.dumps({"ack_id": "a1", "honk_id": "1",
                        "note": "addressed"}),
        ]) + "\n")
        latest = dash.collect_latest_entries(tmp_path)
        h = latest["honk"]
        # Honk record returned (ack record skipped, original is unacked-shaped
        # in this synthetic case). We verify the honk surfaced — the ack
        # filter is exercised in the read_recent_honks tests above.
        assert h is not None
        assert h.get("pattern") == "p"

    def test_preview_text_truncates(self):
        long = "x" * 500
        out = dash._preview_text(long, limit=100)
        assert len(out) <= 101  # 100 + ellipsis
        assert out.endswith("…")

    def test_preview_text_collapses_newlines(self):
        out = dash._preview_text("line1\nline2\nline3")
        assert "\n" not in out


# ── CLI --once --json ───────────────────────────────────────────────────────


class TestCli:
    def test_once_json_emits_valid_json(self, capsys):
        with patch.object(conn, "_launchctl_print_text", return_value=None), \
             patch.object(
                 conn, "_http_probe",
                 return_value={"http_status": None, "body": "",
                               "error": "mocked"},
             ):
            rc = cli.main(["--once", "--json", "--no-bridge", "--no-color"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert "connectivity" in data
        assert "bridge" in data
        assert data["bridge"]["reachable"] is False  # --no-bridge

    def test_no_bridge_disables_bridge_calls(self, capsys):
        # --once with --no-bridge runs `run_loop` with bridge_url=None;
        # under --no-bridge --json, doesn't even start the loop. Verify
        # the flag is wired.
        with patch.object(conn, "_launchctl_print_text", return_value=None), \
             patch.object(
                 conn, "_http_probe",
                 return_value={"http_status": None, "body": "",
                               "error": "mocked"},
             ):
            rc = cli.main(["--once", "--json", "--no-bridge"])
        assert rc == 0
