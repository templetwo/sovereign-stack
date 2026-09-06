"""One test per finding in the 2026-09-06 adversarial review of the RC.

Astra (Codex seat 3/3) rejected tip 4632e93 with nine findings. This file is
the proof, one class per finding, each written so it FAILS on that tip and
PASSES on the fix — the bar HQ set for this round: "a test that FAILS on
4632e93 and PASSES on your commit, in the test file, not a comment."

The findings are reproduced here in the reviewer's own terms rather than
paraphrased into something easier to satisfy. Where a check would have passed
on the old tip for an accidental reason, the premise it depends on is pinned
in its own assertion, so a future refactor cannot quietly make one of these
pass for the wrong reason.

F6 (the suite's live-store isolation) is proven in
tests/test_live_store_containment.py instead, beside the three guards it joins.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from sovereign_stack import server
from sovereign_stack import signal_ledger as sl


def _guardian_ok(root: Path) -> None:
    (root / "guardian").mkdir(parents=True, exist_ok=True)
    (root / "guardian" / "status.json").write_text('{"issues": []}', encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _a_halt(root: Path, name: str = "old.md") -> None:
    (root / "daemons" / "halts").mkdir(parents=True, exist_ok=True)
    (root / "daemons" / "halts" / name).write_text("halt\n", encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
# F1 — the marker must carry cumulative integrity evidence and a freshness
#      bound; a zero-delta rescan must not disarm loss detection.
# ══════════════════════════════════════════════════════════════════════════


class TestF1TheMarkerCarriesCumulativeEvidence:
    def test_the_marker_records_the_whole_ledger_not_just_this_scan(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _a_halt(root)
        _guardian_ok(root)
        sl.scan_all(root)
        marker = json.loads(sl.scan_marker_path(root).read_text())
        # These four fields do not exist on 4632e93.
        assert marker["ledger_bytes"] == sl.ledger_path(root).stat().st_size
        assert marker["ledger_rows"] == 1
        assert len(marker["ledger_sha256"]) == 64
        assert marker["max_age_seconds"] > 0

    def test_truncation_after_a_zero_delta_rescan_is_an_error(self, tmp_sovereign_root):
        """THE REVIEWER'S REPRODUCTION, verbatim in shape.

        Open one halt, scan, scan again unchanged, truncate the ledger to zero
        bytes, read the heartbeat. On 4632e93 that answered
        `error:null, ingestion:"ok", total:0` for a ledger whose contents were
        gone — because the only reconciliation compared the LAST SCAN'S DELTA
        (zero, on a rescan) against the ledger, and `0 > n` is false for every
        n. The detector's sensitivity decayed to zero on the ordinary path.
        """
        root = tmp_sovereign_root
        _a_halt(root)
        _guardian_ok(root)
        sl.scan_all(root)
        sl.scan_all(root)  # the zero-delta rescan that used to disarm the check
        marker = json.loads(sl.scan_marker_path(root).read_text())
        assert sum(marker["counts"].values()) == 0, "premise: the rescan opened nothing"
        sl.ledger_path(root).write_text("", encoding="utf-8")
        field = sl.heartbeat_field(root)
        assert field["error"], "a truncated ledger read as clean"
        assert field["error"].startswith("ledger_truncated")
        assert field["ingestion"] == "error"
        assert field["total"] is None

    def test_rewriting_history_under_a_fresh_marker_is_caught(self, tmp_sovereign_root):
        """Same byte count, different bytes. The prefix hash is what sees it;
        a tail-only digest or a length check alone would not."""
        root = tmp_sovereign_root
        _a_halt(root)
        _guardian_ok(root)
        sl.scan_all(root)
        path = sl.ledger_path(root)
        data = path.read_bytes()
        rewritten = data.replace(b'"watch-2/3"', b'"watch-9/9"')
        assert len(rewritten) == len(data), "premise: this rewrite preserves the length"
        path.write_bytes(rewritten)
        field = sl.heartbeat_field(root)
        assert field["error"].startswith("ledger_rewritten")
        assert field["total"] is None

    def test_appending_an_ack_after_a_scan_is_not_loss(self, tmp_sovereign_root):
        """POSITIVE CONTROL (law #3). The ledger is append-only and a watch
        seat legitimately acks a second after a scan. A whole-file digest would
        cry loss on every honest acknowledgement, which would make the check
        useless and get it turned off."""
        root = tmp_sovereign_root
        _a_halt(root)
        _guardian_ok(root)
        sl.scan_all(root)
        sid = sl.signal_id_for("halt", "old.md")
        sl.ack_signal(sid, actor="seat:watch", state="acted", reason="handled", root=root)
        field = sl.heartbeat_field(root)
        assert field["error"] is None
        assert field["total"] == 0

    def test_a_stale_marker_is_not_healthy(self, tmp_sovereign_root):
        """A legitimate, fully valid marker dated 2020 returned ingestion:"ok"
        on 4632e93 — there was no freshness check at all."""
        root = tmp_sovereign_root
        _a_halt(root)
        _guardian_ok(root)
        sl.scan_all(root)
        marker = json.loads(sl.scan_marker_path(root).read_text())
        marker["scanned_at"] = "2020-01-01T00:00:00Z"
        sl.scan_marker_path(root).write_text(json.dumps(marker), encoding="utf-8")
        assert sl._validate_marker(marker) is None, "premise: this marker is otherwise valid"
        field = sl.heartbeat_field(root)
        assert field["ingestion"] == "stale"
        assert field["error"].startswith("scan_stale")
        assert field["total"] is None

    def test_empty_marker_maps_are_not_a_completed_scan(self, tmp_sovereign_root):
        """`_validate_marker` accepted `counts:{}` / `source_status:{}` on
        4632e93; with an empty ledger those produce zero counts and no error —
        a marker certifying a scan of nothing as a healthy scan of everything."""
        root = tmp_sovereign_root
        sl.ledger_path(root).parent.mkdir(parents=True, exist_ok=True)
        sl.ledger_path(root).touch()
        sl.scan_marker_path(root).write_text(
            json.dumps(
                {
                    "scanned_at": sl._now(),
                    "counts": {},
                    "source_status": {},
                    "ledger_bytes": 0,
                    "ledger_rows": 0,
                    "ledger_sha256": "0" * 64,
                    "max_age_seconds": 3600,
                }
            ),
            encoding="utf-8",
        )
        assert "not a completed scan" in (
            sl._validate_marker(json.loads(sl.scan_marker_path(root).read_text())) or ""
        )
        field = sl.heartbeat_field(root)
        assert field["ingestion"] == "marker_error"
        assert field["total"] is None

    def test_a_marker_missing_one_source_is_refused(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        sl.scan_all(root)
        marker = json.loads(sl.scan_marker_path(root).read_text())
        marker["source_status"].pop("thread")
        assert "missing ['thread']" in (sl._validate_marker(marker) or "")


# ══════════════════════════════════════════════════════════════════════════
# F2 — failed measurement must never produce zeros, all the way to the tool.
# ══════════════════════════════════════════════════════════════════════════


class TestF2NoZeroOnFailedMeasurement:
    def test_an_unreadable_thread_shard_is_not_a_healthy_zero(self, tmp_sovereign_root):
        """`signal_ledger.py:975` accumulated `read.bad_lines` and DROPPED
        `read.status`, so a chmod-000 shard scanned as `thread:"ok"` and the
        heartbeat answered `total:0, error:null, ingestion:"ok"`."""
        if os.geteuid() == 0:
            pytest.skip("root can read a 000 file; the premise does not hold")
        root = tmp_sovereign_root
        _guardian_ok(root)
        shard = root / "chronicle" / "open_threads" / "locked.jsonl"
        _write_jsonl(
            shard, [{"thread_id": "t1", "question": "q", "timestamp": "2026-09-01T00:00:00Z"}]
        )
        shard.chmod(0o000)
        try:
            assert not os.access(shard, os.R_OK), "premise: the shard is genuinely unreadable"
            sl.scan_all(root)
            marker = json.loads(sl.scan_marker_path(root).read_text())
            assert marker["source_status"]["thread"] != "ok", "the read failure was dropped"
            assert marker["source_status"]["thread"].startswith("unreadable")
            field = sl.heartbeat_field(root)
            assert field["total"] is None
            assert field["error"]
            assert field["by_source"]["thread"] is None
        finally:
            shard.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_corrupt_rows_never_report_a_zero_total(self, tmp_sovereign_root):
        """ "A ledger containing one list-shaped source returns
        error:'corrupt_rows:1 ...', total:0, directly violating never report
        zero on error." — the review, quoting its own probe."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        sl.scan_all(root)
        with sl.ledger_path(root).open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "signal_id": "s",
                        "source": ["halt"],
                        "produced_at": "2026-09-01T00:00:00Z",
                        "owner": "watch-2/3",
                        "state": "open",
                    }
                )
                + "\n"
            )
        field = sl.heartbeat_field(root)
        assert field["corrupt_rows"] == 1
        assert field["error"]
        assert field["total"] is None
        assert field["ingestion"] == "error"

    def test_the_tool_result_keeps_the_heartbeat_nulls(self, tmp_sovereign_root):
        """`signal_ledger.py:1160` rebuilt the tool result from `summarize`,
        discarding the heartbeat's per-source nulls: an unavailable guardian
        came back `ok:true` with guardian `open:0`."""
        root = tmp_sovereign_root
        _a_halt(root)
        sl.scan_all(root)  # no guardian fixture: the source is unavailable
        marker = json.loads(sl.scan_marker_path(root).read_text())
        assert marker["source_status"]["guardian"] == "unavailable", "premise"
        out = json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))
        assert out["by_source"]["guardian"]["open"] is None, "an unmeasured source rendered as 0"
        assert out["total"] is None
        assert out["error"]

    def test_a_malformed_honk_source_is_reported_not_counted(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        (root / "nape").mkdir(parents=True, exist_ok=True)
        (root / "nape" / "honks.jsonl").write_text("{not json}\n", encoding="utf-8")
        sl.scan_all(root)
        field = sl.heartbeat_field(root)
        assert field["source_status"]["honk"].startswith("degraded")
        assert field["by_source"]["honk"] is None
        assert field["total"] is None


# ══════════════════════════════════════════════════════════════════════════
# F3 — the honk fold implemented properly; the five broken folds reclassified.
# ══════════════════════════════════════════════════════════════════════════


class TestF3TheHonkFoldIsReal:
    def test_list_mode_returns_the_id_and_the_concern(self, tmp_sovereign_root):
        """ "old result contains the honk ID and concern; replacement exposes
        neither, only counts." The retired reader's replacement could not
        supply the identifier its own ack tool requires."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        _write_jsonl(
            root / "nape" / "honks.jsonl",
            [
                {
                    "honk_id": "h-1",
                    "pattern": "declare_before_verify",
                    "observation": "declared clean before reading the file",
                    "timestamp": "2026-09-01T00:00:00Z",
                }
            ],
        )
        out = json.loads(
            sl.handle_signal_tool("signals_summary", {"mode": "list", "source": "honk"}, root=root)
        )
        assert out["ok"] is True
        (row,) = out["signals"]
        assert row["signal_id"] == sl.signal_id_for("honk", "h-1")
        assert row["source"] == "honk"
        assert row["kind"] == "declare_before_verify"
        assert row["opened_at"] == "2026-09-01T00:00:00Z"
        assert row["owner"] == "watch-2/3"
        assert row["state"] == "open"
        assert row["concern"] == "declared clean before reading the file"

    def test_a_watch_seat_can_ack_what_the_list_showed(self, tmp_sovereign_root):
        """THE WHOLE POINT OF THE FOLD, end to end: read the id back out of
        the replacement and close the signal with it."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        _write_jsonl(
            root / "nape" / "honks.jsonl",
            [
                {
                    "honk_id": "h-2",
                    "pattern": "premature_summary",
                    "observation": "summarised early",
                    "timestamp": "2026-09-01T00:00:00Z",
                }
            ],
        )
        listed = json.loads(
            sl.handle_signal_tool("signals_summary", {"mode": "list", "source": "honk"}, root=root)
        )
        sid = listed["signals"][0]["signal_id"]
        from sovereign_stack.dispatch_context import reset_caller_seat, set_caller_seat

        token = set_caller_seat("seat:watch-2-3")
        try:
            acked = json.loads(
                sl.handle_signal_tool(
                    "signal_ack",
                    {"signal_id": sid, "state": "acknowledged", "reason": "seen"},
                    root=root,
                )
            )
        finally:
            reset_caller_seat(token)
        assert acked["ok"] is True
        assert acked["row"]["closed_by"] == "seat:watch-2-3"
        # and the concern survives the close, so the audit trail is not ids-only
        assert acked["row"]["concern"] == "summarised early"
        after = json.loads(
            sl.handle_signal_tool("signals_summary", {"mode": "list", "source": "honk"}, root=root)
        )
        assert after["count"] == 0, "the acked honk is gone from the open queue"

    def test_list_mode_is_published_in_the_schema(self):
        tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "signals_summary")
        modes = tool.inputSchema["properties"]["mode"]["enum"]
        assert modes == ["summary", "list"]


class TestF3TheBrokenFoldsAreNowOutrightRetirements:
    """ "do not advertise a fold that does not preserve the effect." Each of
    these five pointed a caller at a tool the review PROVED does not do the
    retired job."""

    LOST = {
        "mark_uncertainty": "no marker id, no confidence value",
        "resolve_uncertainty": "no marker-id resolution operation",
        "comms_acknowledge": "comms_get_acks(message_id) stays empty",
        "handoff_acted_on": "does not increment the acted-on record count",
        "handoff_archaeology": "no history or list mode",
    }

    @pytest.mark.parametrize("name", sorted(LOST))
    def test_replacement_is_none(self, name):
        assert server.RETIRED_TOOLS[name].replacement is None

    @pytest.mark.parametrize("name,phrase", sorted(LOST.items()))
    def test_the_refusal_names_what_was_lost(self, name, phrase):
        message = server.retired_tool_error(name)
        assert "It has no replacement." in message
        assert phrase in message, f"{name}'s refusal does not say what was lost"

    def test_signal_ack_no_longer_advertises_the_comms_fold(self):
        tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "signal_ack")
        assert "comms_acknowledge" not in tool.description

    def test_the_honk_fold_points_at_the_list_mode(self):
        for name in ("nape_honks", "nape_honks_with_history"):
            entry = server.RETIRED_TOOLS[name]
            assert entry.replacement == "signals_summary"
        assert "mode='list'" in server.RETIRED_TOOLS["nape_honks"].note


# ══════════════════════════════════════════════════════════════════════════
# F4 — ingestion runs on read; signal_ack is a write, not governance.
# ══════════════════════════════════════════════════════════════════════════


class TestF4TheWatchIsConnected:
    def test_signals_summary_ingests_before_it_answers(self, tmp_sovereign_root):
        """`scan_all` called itself an "Owned ingestion path" and had NO caller
        anywhere in src/, clients/, scripts/, the bridge or LaunchAgents. A
        halt sitting on disk was invisible to every read."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        _a_halt(root, "fresh.md")
        assert not sl.ledger_path(root).exists(), "premise: nothing has scanned yet"
        out = json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))
        assert out["ok"] is True
        assert out["total"] == 1, "the read did not ingest"
        assert sl.signal_id_for("halt", "fresh.md") in sl.load_latest(root)

    def test_a_second_read_does_not_rescan(self, tmp_sovereign_root):
        """Incremental by construction: inside the freshness bound the sweep is
        skipped entirely, so a burst of reads costs one scan, not N."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        _a_halt(root)
        sl.handle_signal_tool("signals_summary", {}, root=root)
        first = sl.scan_marker_path(root).read_text()
        sl.handle_signal_tool("signals_summary", {}, root=root)
        assert sl.scan_marker_path(root).read_text() == first

    def test_a_scan_failure_is_an_error_never_stale_but_ok(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _a_halt(root)
        sl.scan_all(root)

        def boom(*_a, **_k):
            raise RuntimeError("source sweep exploded")

        monkeypatch.setattr(sl, "scan_all", boom)
        # force the freshness bound to expire so ensure_scanned must scan
        monkeypatch.setenv(sl.MARKER_MAX_AGE_ENV, "1")
        marker = json.loads(sl.scan_marker_path(root).read_text())
        marker["max_age_seconds"] = 1
        marker["scanned_at"] = "2020-01-01T00:00:00Z"
        sl.scan_marker_path(root).write_text(json.dumps(marker), encoding="utf-8")
        field = sl.heartbeat_field(root, scan=True)
        assert field["ingestion"] == "error"
        assert field["error"].startswith("scan_failed:RuntimeError")
        assert field["total"] is None

    def test_signal_ack_is_a_write_not_governance(self):
        """HQ's ruling, 2026-09-06. Anthony's governance list is laws,
        policies, seat permissions, ring placement and deletes; a watch seat
        marking a honk read is none of those. Classifying it `govern` is what
        left the designated watch seat with no closure path: the canonical
        rings do not admit a govern-intent tool."""
        assert server.TOOL_INTENTS["signal_ack"] == "write"
        assert server.TOOL_INTENTS["signals_summary"] == "read"


# ══════════════════════════════════════════════════════════════════════════
# F5 — the actor comes from a trusted dispatch context, or the call is refused.
# ══════════════════════════════════════════════════════════════════════════


def _dispatch_ack(sid: str, **args):
    payload = {"signal_id": sid, "state": "acted", "reason": "closed", **args}
    result = asyncio.run(server._dispatch_tool("signal_ack", payload))
    return json.loads(result[0].text)


def _dispatch_ack_as(seat: str, sid: str, **args):
    """Ack with an identity established the way the BRIDGE establishes one:
    in-process, in the dispatch context, before the server is called.

    There is no argument that does this (review N3) — that is the point.
    """
    from sovereign_stack.dispatch_context import reset_caller_seat, set_caller_seat

    token = set_caller_seat(seat)
    try:
        return _dispatch_ack(sid, **args)
    finally:
        reset_caller_seat(token)


class TestF5TheActorIsResolvedNotComposed:
    """ "In actual in-process MCP requests, patched session values `daemon`,
    `None`, and empty string all successfully closed a halt as `seat:daemon`,
    `seat:None`, and `seat:` respectively (mcp_isError:false, body ok:true)."

    Every test here goes through the REAL dispatcher, which is where the
    review found the defect — `_signal_actor()` always returned a non-empty
    string, so the refusal in `handle_signal_tool` was unreachable in
    production and a unit-level proof would have said nothing.
    """

    @pytest.fixture
    def signal(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        monkeypatch.setattr(sl, "default_sovereign_root", lambda: root)
        _guardian_ok(root)
        _a_halt(root, "x.md")
        sl.scan_all(root)
        return sl.signal_id_for("halt", "x.md")

    def test_a_missing_session_is_refused_not_stamped_seat_none(self, signal, monkeypatch):
        monkeypatch.setattr(server.spiral_state, "session_id", None)
        out = _dispatch_ack(signal)
        assert out["ok"] is False
        assert "no caller identity" in out["error"]
        # AND NOTHING MOVED. A refusal that still writes the row is the fail-open
        # one layer in: the signal must still be open, unstamped.
        row = sl.load_latest(sl.default_sovereign_root())[signal]
        assert row["state"] == "open"
        assert row["closed_by"] is None

    def test_an_empty_session_is_refused_not_stamped_seat_colon(self, signal, monkeypatch):
        monkeypatch.setattr(server.spiral_state, "session_id", "")
        out = _dispatch_ack(signal)
        assert out["ok"] is False
        assert "no caller identity" in out["error"]

    def test_a_seat_cannot_close_as_the_producer(self, signal, monkeypatch):
        """`server.py:3155` produced `seat:<session>` while
        `signal_ledger.py:515` compared against the bare producer label
        `daemon`, so the two could never be equal and the check could not
        refuse the one case it exists for."""
        monkeypatch.setattr(server.spiral_state, "session_id", "daemon")
        out = _dispatch_ack(signal)
        assert out["ok"] is False
        assert "producer cannot close its own signal" in out["error"]

    def test_two_seats_stamp_two_different_closers(self, tmp_sovereign_root, monkeypatch):
        """ "It also cannot distinguish concurrent trusted seats sharing the
        server." The bridge establishes the identity it verified in the
        DISPATCH CONTEXT, in-process. Round 2 did this with an `actor_seat`
        argument; review N3 proved a caller could send that argument too, so
        the contract moved out of band (see TestN3...)."""
        root = tmp_sovereign_root
        monkeypatch.setattr(sl, "default_sovereign_root", lambda: root)
        _guardian_ok(root)
        _a_halt(root, "a.md")
        _a_halt(root, "b.md")
        sl.scan_all(root)
        monkeypatch.setattr(server.spiral_state, "session_id", "spiral_shared_session")
        first = _dispatch_ack_as("seat:hq-studio", sl.signal_id_for("halt", "a.md"))
        second = _dispatch_ack_as("seat:grok-build", sl.signal_id_for("halt", "b.md"))
        assert first["ok"] is True and second["ok"] is True
        assert first["row"]["closed_by"] == "seat:hq-studio"
        assert second["row"]["closed_by"] == "seat:grok-build"
        assert first["row"]["closed_by"] != second["row"]["closed_by"]

    def test_an_empty_injected_actor_is_a_refusal_not_a_default(self, signal, monkeypatch):
        """SUPERSEDED IN FORM BY N3, KEPT IN SUBSTANCE. Round 2 sent
        `actor_seat="   "`; that argument is now refused outright by name.
        The property the test exists for — a blank identity is a refusal and
        never a default — is asserted on the context, which is where identity
        lives now."""
        monkeypatch.setattr(server.spiral_state, "session_id", None)
        out = _dispatch_ack(signal, actor_seat="   ")
        assert out["ok"] is False
        assert "'actor_seat' is not an accepted argument" in out["error"]
        blank = _dispatch_ack(signal)
        assert blank["ok"] is False
        assert "no caller identity" in blank["error"]

    def test_a_placeholder_session_is_not_an_identity(self, signal, monkeypatch):
        monkeypatch.setattr(server.spiral_state, "session_id", "unknown")
        out = _dispatch_ack(signal)
        assert out["ok"] is False

    def test_the_ledger_refuses_a_namespaced_producer_directly(self):
        """Unit-level companion: the comparison now strips one namespace, so
        `seat:daemon` and `daemon` are comparable at all."""
        assert sl._actor_identity("seat:daemon") == "daemon"
        assert sl._actor_identity("nape-ack") == "nape-ack"
        with pytest.raises(ValueError):
            sl._normalise_actor("seat:")
        with pytest.raises(ValueError):
            sl._normalise_actor("seat:None")

    def test_no_identity_argument_is_published_at_all(self):
        """REVERSED BY REVIEW N3, DELIBERATELY. Round 2 published `actor_seat`
        "so the bridge can fill it in"; the reviewer then filled it in from an
        ordinary MCP call and closed a signal as an arbitrary seat. A
        parameter the server reads is reachable by every caller of the
        server. The bridge now uses the dispatch context instead."""
        from sovereign_stack.dispatch_context import REFUSED_IDENTITY_ARGUMENTS

        tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "signal_ack")
        for forbidden in REFUSED_IDENTITY_ARGUMENTS:
            assert forbidden not in tool.inputSchema["properties"]
        assert set(tool.inputSchema["required"]) == {"signal_id", "state"}


# ══════════════════════════════════════════════════════════════════════════
# F7 — one cross-shard latest-state policy, applied before mutating.
# ══════════════════════════════════════════════════════════════════════════


class TestF7CrossShardLatestStateWins:
    def test_a_newer_open_state_in_another_shard_beats_an_older_resolved_one(
        self, tmp_sovereign_root
    ):
        """THE REVIEWER'S FIXTURE. `signal_ledger.py:980` reset `latest_by_id`
        INSIDE the shard loop, so "latest record wins" held only within a
        shard; `:994` could only ever close, never re-open. Both old and HEAD
        left T `acted`, permanently — a rescan is idempotent, so nothing
        repaired it."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        d = root / "chronicle" / "open_threads"
        _write_jsonl(
            d / "a.jsonl",
            [
                {
                    "thread_id": "T",
                    "question": "q",
                    "timestamp": "2026-08-01T00:00:00Z",
                    "resolved": True,
                }
            ],
        )
        _write_jsonl(
            d / "b.jsonl",
            [
                {
                    "thread_id": "T",
                    "question": "q",
                    "timestamp": "2026-09-01T00:00:00Z",
                    "resolved": False,
                }
            ],
        )
        sl.scan_all(root)
        sid = sl.signal_id_for("thread", "T")
        assert sl.load_latest(root)[sid]["state"] == "open", (
            "the obsolete August terminal state won"
        )

    def test_an_older_open_state_does_not_reopen_a_newer_resolution(self, tmp_sovereign_root):
        """The mirror case, so the policy is 'latest wins' and not 'open always
        wins' — a rule that only ever moves one direction is not a policy."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        d = root / "chronicle" / "open_threads"
        _write_jsonl(
            d / "a.jsonl",
            [
                {
                    "thread_id": "T",
                    "question": "q",
                    "timestamp": "2026-09-01T00:00:00Z",
                    "resolved": True,
                }
            ],
        )
        _write_jsonl(
            d / "b.jsonl",
            [
                {
                    "thread_id": "T",
                    "question": "q",
                    "timestamp": "2026-08-01T00:00:00Z",
                    "resolved": False,
                }
            ],
        )
        sl.scan_all(root)
        assert sl.load_latest(root)[sl.signal_id_for("thread", "T")]["state"] == "acted"

    def test_a_rescan_after_reopening_is_still_idempotent(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        d = root / "chronicle" / "open_threads"
        _write_jsonl(
            d / "a.jsonl",
            [
                {
                    "thread_id": "T",
                    "question": "q",
                    "timestamp": "2026-08-01T00:00:00Z",
                    "resolved": True,
                }
            ],
        )
        _write_jsonl(
            d / "b.jsonl",
            [
                {
                    "thread_id": "T",
                    "question": "q",
                    "timestamp": "2026-09-01T00:00:00Z",
                    "resolved": False,
                }
            ],
        )
        sl.scan_all(root)
        rows_after_first = len(sl.ledger_path(root).read_text().splitlines())
        sl.scan_all(root)
        assert len(sl.ledger_path(root).read_text().splitlines()) == rows_after_first


# ══════════════════════════════════════════════════════════════════════════
# F8 — the native heartbeat carries the signal count.
# ══════════════════════════════════════════════════════════════════════════


class TestF8TheNativeHeartbeatCarriesUnackedSignals:
    def test_the_field_is_present_with_the_bridge_shape(self):
        """ "a seat calling the published Stack heartbeat cannot see it." The
        dashboard had the field and the HTTP bridge wired it; the call-first
        boot tool an arriving instance actually reaches did not."""
        result = asyncio.run(server._dispatch_tool("heartbeat", {}))
        payload = json.loads(result[0].text)
        assert "unacked_signals" in payload
        field = payload["unacked_signals"]
        for key in ("error", "ingestion", "total", "by_source", "source_status"):
            assert key in field, f"heartbeat's unacked_signals is missing {key}"

    def test_it_is_the_same_function_the_dashboard_uses(self):
        from sovereign_stack import dashboard_web

        assert dashboard_web.heartbeat_field is sl.heartbeat_field
        assert server.signal_heartbeat_field is sl.heartbeat_field


# ══════════════════════════════════════════════════════════════════════════
# F9 — the watch lifecycle survives the retirement, folded into post_fix_verify.
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def post_fix_root(tmp_path, monkeypatch):
    """A temporary post-fix root. The three live watches under
    ~/.sovereign/post_fix/watches are never touched."""
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    return tmp_path


def _post_fix(args: dict):
    from sovereign_stack.post_fix_tools import handle_post_fix_tool

    result = asyncio.run(handle_post_fix_tool("post_fix_verify", args, "spiral_test"))
    return result[0].text


def _make_watch(tmp_path) -> str:
    marker = tmp_path / "probe.txt"
    marker.write_text("baseline\n", encoding="utf-8")
    out = json.loads(
        _post_fix(
            {
                "fix_description": "folded watch lifecycle",
                "probes": [{"name": "p", "type": "file_hash", "path": str(marker)}],
                "schedule_offsets_min": [5],
            }
        )
    )
    return out["watch_id"]


class TestF9TheWatchLifecycleIsFolded:
    def test_the_three_names_map_to_post_fix_verify(self):
        """ "a seat can open a watch and cannot inspect or cancel it through the
        surviving tool surface." That is a workflow regression, not a
        retirement."""
        for name in ("watch_status", "watch_cancel", "watch_resample"):
            entry = server.RETIRED_TOOLS[name]
            assert entry.replacement == "post_fix_verify", name
            assert "mode=" in entry.note, f"{name}'s note does not name the mode"

    def test_status_lists_an_open_watch(self, post_fix_root):
        watch_id = _make_watch(post_fix_root)
        listing = json.loads(_post_fix({"mode": "status"}))
        assert listing["count"] == 1
        assert listing["watches"][0]["watch_id"] == watch_id

    def test_status_with_an_id_returns_the_full_record(self, post_fix_root):
        watch_id = _make_watch(post_fix_root)
        full = json.loads(_post_fix({"mode": "status", "watch_id": watch_id}))
        assert full["watch_id"] == watch_id
        assert "baseline" in full

    def test_resample_samples_now(self, post_fix_root):
        watch_id = _make_watch(post_fix_root)
        out = json.loads(_post_fix({"mode": "resample", "watch_id": watch_id}))
        assert out.get("sampled") or out.get("sample") or out.get("drift") is not None, out
        full = json.loads(_post_fix({"mode": "status", "watch_id": watch_id}))
        assert full["samples"], "the resample recorded nothing"

    def test_cancel_archives_the_watch(self, post_fix_root):
        watch_id = _make_watch(post_fix_root)
        json.loads(_post_fix({"mode": "cancel", "watch_id": watch_id, "reason": "done"}))
        assert json.loads(_post_fix({"mode": "status"}))["count"] == 0
        archived = json.loads(_post_fix({"mode": "status", "include_archived": True}))
        assert archived["count"] == 1

    def test_verify_still_refuses_a_call_with_no_probes(self, post_fix_root):
        """THE INVARIANT THAT MOVED FROM THE SCHEMA INTO THE HANDLER. Dropping
        `required: [fix_description, probes]` was necessary — the read modes do
        not carry them — but dropping it without re-asserting it in code would
        have traded a fold for a fail-open on the only write path here."""
        assert "requires at least one probe" in _post_fix({"fix_description": "x", "probes": []})
        assert "requires fix_description" in _post_fix({"probes": [{"name": "p"}]})

    def test_an_unknown_mode_is_refused(self, post_fix_root):
        assert "mode must be" in _post_fix({"mode": "banana"})

    def test_the_mode_is_published(self):
        tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "post_fix_verify")
        assert tool.inputSchema["properties"]["mode"]["enum"] == [
            "verify",
            "status",
            "resample",
            "cancel",
        ]
