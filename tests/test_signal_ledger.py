"""Signal ledger — tmp roots only. Never writes live ~/.sovereign."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack import signal_ledger as sl


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _guardian_ok(root: Path) -> None:
    """Give a tmp root a readable guardian so a scan can be FULLY measured.

    Without it every synthetic scan reports guardian "unavailable" — correct,
    since guardian posture is a property of the real machine and a test must
    never probe it — which under the 2026-09-06 contract nulls the total. Tests
    that want to assert a healthy number have to supply the source, and that
    is the contract working, not a workaround.
    """
    (root / "guardian").mkdir(parents=True, exist_ok=True)
    (root / "guardian" / "status.json").write_text('{"issues": []}', encoding="utf-8")


def _scan_measured(root: Path) -> dict:
    """scan_all with every source readable, then the field."""
    _guardian_ok(root)
    sl.scan_all(root)
    return sl.heartbeat_field(root)


def test_idempotent_open_and_ack_reason(tmp_sovereign_root):
    root = tmp_sovereign_root
    a = sl.open_signal(
        source="halt", native_id="h1.md", produced_at="2026-09-01T00:00:00Z", root=root
    )
    b = sl.open_signal(
        source="halt", native_id="h1.md", produced_at="2026-09-01T00:00:00Z", root=root
    )
    assert a is not None and b is None
    sid = sl.signal_id_for("halt", "h1.md")
    with pytest.raises(ValueError, match="reason"):
        sl.ack_signal(sid, actor="watch-2/3", state="dismissed", reason="", root=root)
    with pytest.raises(PermissionError):
        sl.ack_signal(sid, actor="daemon", state="dismissed", reason="nope", root=root)
    row = sl.ack_signal(sid, actor="watch-2/3", state="dismissed", reason="stale halt", root=root)
    assert row["state"] == "dismissed"
    assert sl.load_latest(root)[sid]["state"] == "dismissed"


def test_honk_adapter_acks_and_idempotent_scan(tmp_sovereign_root):
    root = tmp_sovereign_root
    _write_jsonl(
        root / "nape" / "honks.jsonl",
        [
            {"honk_id": "h-open", "timestamp": "2026-09-01T00:00:00Z", "level": "sharp"},
            {"honk_id": "h-ack", "timestamp": "2026-09-01T01:00:00Z", "level": "low"},
        ],
    )
    _write_jsonl(
        root / "nape" / "acks.jsonl", [{"honk_id": "h-ack", "acked_at": "2026-09-01T02:00:00Z"}]
    )
    assert sl.scan_honks(root).opened == 2
    assert sl.scan_honks(root).opened == 0
    latest = sl.load_latest(root)
    assert latest[sl.signal_id_for("honk", "h-open")]["state"] == "open"
    assert latest[sl.signal_id_for("honk", "h-ack")]["state"] == "acknowledged"


def test_watchman_and_proposals_and_threads(tmp_sovereign_root):
    root = tmp_sovereign_root
    _write_jsonl(
        root / "watchman" / "spool.jsonl",
        [{"kind": "watchman-sweep", "sweep_id": "S1", "started_at": "2026-09-05T00:00:00Z"}],
    )
    pending = root / "grok_bridge" / "pending_writes"
    pending.mkdir(parents=True)
    (pending / "p1.json").write_text(
        json.dumps(
            {"proposal_id": "abc", "status": "pending", "timestamp": "2026-09-05T00:00:00Z"}
        ),
        encoding="utf-8",
    )
    (pending / "p2.json").write_text(
        json.dumps(
            {"proposal_id": "def", "status": "committed", "timestamp": "2026-09-05T00:00:00Z"}
        ),
        encoding="utf-8",
    )
    threads = root / "chronicle" / "open_threads"
    threads.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        threads / "t1.jsonl",
        [{"thread_id": "t1", "resolved": False, "timestamp": "2026-09-01T00:00:00Z"}],
    )
    _write_jsonl(
        threads / "t2.jsonl",
        [{"thread_id": "t2", "resolved": True, "timestamp": "2026-09-01T00:00:00Z"}],
    )
    sl.scan_all(root)
    latest = sl.load_latest(root)
    assert latest[sl.signal_id_for("watchman", "S1")]["state"] == "open"
    assert latest[sl.signal_id_for("proposal", "grok_bridge:abc")]["state"] == "open"
    assert latest[sl.signal_id_for("proposal", "grok_bridge:def")]["state"] == "acted"
    assert latest[sl.signal_id_for("thread", "t1")]["state"] == "open"
    assert latest[sl.signal_id_for("thread", "t2")]["state"] == "acted"


def test_summary_stale_windows(tmp_sovereign_root):
    root = tmp_sovereign_root
    now = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)
    sl.open_signal(
        source="halt",
        native_id="old.md",
        produced_at=(now - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        root=root,
    )
    sl.open_signal(
        source="halt",
        native_id="mid.md",
        produced_at=(now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        root=root,
    )
    sl.open_signal(
        source="halt", native_id="new.md", produced_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), root=root
    )
    s = sl.summarize(root, now=now)
    # No marker on this root, so nothing certifies the SOURCES were read and
    # `total` is null by contract. The stale-window arithmetic is still a real
    # measurement of the rows present, and it says so in its own key name.
    assert s["total"] is None
    assert s["open_measured"] == 3
    assert s["stale_24h_measured"] == 2
    assert s["stale_7d_measured"] == 1
    _guardian_ok(root)
    sl.scan_all(root)
    scanned = sl.summarize(root, now=now)
    assert scanned["total"] == 3
    assert scanned["stale_24h"] == 2
    assert scanned["stale_7d"] == 1
    assert scanned["by_source"]["halt"]["open"] == 3


def test_heartbeat_error_not_zero(tmp_sovereign_root, monkeypatch):
    root = tmp_sovereign_root
    sl.open_signal(source="halt", native_id="x.md", produced_at="2026-09-01T00:00:00Z", root=root)
    unscanned = sl.heartbeat_field(root)
    assert unscanned["error"] == "not_scanned", "a ledger with no scan behind it is not measured"
    assert unscanned["total"] is None
    good = _scan_measured(root)
    assert good["error"] is None
    assert good["total"] == 1

    def boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(Path, "read_text", boom)
    bad = sl.heartbeat_field(root)
    assert bad["error"]
    assert bad["total"] is None


def test_escalation_default_off(monkeypatch):
    sent = []
    monkeypatch.delenv(sl.NTFY_ENV, raising=False)
    out = sl.send_escalation("new_halt", "file 20260804T131703.md", publish=sent.append)
    assert out["sent"] is False
    assert out["enabled"] is False
    assert sent == []
    assert "NEW HALT" in out["text"]
    assert "One line" in out["text"]
    monkeypatch.setenv(sl.NTFY_ENV, "1")
    out2 = sl.send_escalation("new_halt", "file 20260804T131703.md", publish=sent.append)
    assert out2["sent"] is True
    assert sent == [out2["text"]]


def test_halt_decision_guardian_adapters(tmp_sovereign_root):
    root = tmp_sovereign_root
    (root / "daemons" / "halts").mkdir(parents=True, exist_ok=True)
    (root / "daemons" / "halts" / "20260804T131703.md").write_text("halt\n", encoding="utf-8")
    (root / "decisions").mkdir(parents=True, exist_ok=True)
    (root / "decisions" / "metabolize_20260520.md").write_text("decision\n", encoding="utf-8")
    (root / "guardian").mkdir(parents=True, exist_ok=True)
    (root / "guardian" / "issues.json").write_text(
        json.dumps(["Ollama bound on all interfaces", "18 ports listening"]),
        encoding="utf-8",
    )
    assert sl.scan_halts(root).opened == 1
    assert sl.scan_halts(root).opened == 0
    assert sl.scan_decisions(root).opened == 1
    assert sl.scan_guardian(root).opened == 2
    assert sl.scan_guardian(root).opened == 0
    latest = sl.load_latest(root)
    assert latest[sl.signal_id_for("halt", "20260804T131703.md")]["state"] == "open"
    assert latest[sl.signal_id_for("decision", "metabolize_20260520.md")]["state"] == "open"
    assert latest[sl.signal_id_for("guardian", "Ollama bound on all interfaces")]["state"] == "open"


def test_thread_shard_folds_each_id_and_skips_hidden(tmp_sovereign_root):
    """3/3 P1: one domain shard holds many thread_ids; recs[-1] undercounted."""
    root = tmp_sovereign_root
    shard = root / "chronicle" / "open_threads" / "golden-lattice.jsonl"
    shard.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        shard,
        [
            {"thread_id": "A", "resolved": False, "timestamp": "2026-09-01T00:00:00Z"},
            {"thread_id": "B", "resolved": True, "timestamp": "2026-09-01T01:00:00Z"},
        ],
    )
    nested = (
        root / "chronicle" / "open_threads" / "tech-debt,compaction,auto-detection" / "log.jsonl"
    )
    nested.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        nested,
        [{"thread_id": "nested-1", "resolved": False, "timestamp": "2026-09-01T02:00:00Z"}],
    )
    hidden = root / "chronicle" / "open_threads" / ".bak-20260502" / "gone.jsonl"
    hidden.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        hidden,
        [{"thread_id": "hidden", "resolved": False, "timestamp": "2026-09-01T03:00:00Z"}],
    )
    assert sl.scan_threads(root).opened == 3
    latest = sl.load_latest(root)
    assert latest[sl.signal_id_for("thread", "A")]["state"] == "open"
    assert latest[sl.signal_id_for("thread", "B")]["state"] == "acted"
    assert latest[sl.signal_id_for("thread", "nested-1")]["state"] == "open"
    assert sl.signal_id_for("thread", "hidden") not in latest


def test_malformed_ledger_is_error_not_zero(tmp_sovereign_root):
    root = tmp_sovereign_root
    path = sl.ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken json}\n", encoding="utf-8")
    bad = sl.heartbeat_field(root)
    assert bad["error"]
    assert bad["total"] is None
    assert bad["ingestion"] == "error"

    path.write_bytes(b"\xff\xfe\x00not-utf8")
    enc = sl.heartbeat_field(root)
    assert enc["error"]
    assert enc["total"] is None

    path.unlink()
    sl.open_signal(source="halt", native_id="ok.md", produced_at="2026-09-01T00:00:00Z", root=root)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"signal_id":"truncated"\n')
    tail = sl.heartbeat_field(root)
    assert tail["error"]
    assert tail["total"] is None


def test_never_scanned_is_not_healthy_zero(tmp_sovereign_root):
    root = tmp_sovereign_root
    field = sl.heartbeat_field(root)
    assert field["error"] == "not_scanned"
    assert field["total"] is None
    assert field["ingestion"] == "never"
    sl.scan_all(root)
    after = sl.heartbeat_field(root)
    # guardian has no fixture and this is not the live root, so it reports
    # unavailable, not zero — which degrades ingestion AND nulls the total.
    # That is the point: an unmeasured source cannot contribute a measured 0.
    assert after["ingestion"] == "degraded"
    assert after["sources_degraded"] == ["guardian"]
    assert after["error"] == "unmeasured_sources:guardian"
    assert after["total"] is None
    assert after["by_source"]["guardian"] is None
    _guardian_ok(root)
    sl.scan_all(root)
    measured = sl.heartbeat_field(root)
    assert measured["error"] is None
    assert measured["total"] == 0
    assert measured["ingestion"] == "ok"
    assert after["by_source"]["guardian"] is None
    assert after["scanned_at"]


def test_guardian_status_json_shape(tmp_sovereign_root):
    root = tmp_sovereign_root
    (root / "guardian").mkdir(parents=True, exist_ok=True)
    (root / "guardian" / "status.json").write_text(
        json.dumps(
            {
                "source": "guardian_tools._evaluate_status",
                "health_score": 60,
                "issues": ["Ollama bound on all interfaces", "No issues detected"],
                "issue_count": 1,
            }
        ),
        encoding="utf-8",
    )
    assert sl.scan_guardian(root).opened == 1
    latest = sl.load_latest(root)
    assert latest[sl.signal_id_for("guardian", "Ollama bound on all interfaces")]["state"] == "open"
    assert sl.signal_id_for("guardian", "No issues detected") not in latest


def test_handle_tools(tmp_sovereign_root):
    root = tmp_sovereign_root
    _guardian_ok(root)
    sl.open_signal(source="halt", native_id="z.md", produced_at="2026-09-01T00:00:00Z", root=root)
    summary = json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))
    assert summary["ok"] is True
    assert summary["total"] == 1
    sid = sl.signal_id_for("halt", "z.md")
    from sovereign_stack.dispatch_context import reset_caller_seat, set_caller_seat

    token = set_caller_seat("seat:spiral_test")
    try:
        closed = json.loads(
            sl.handle_signal_tool(
                "signal_ack",
                {"signal_id": sid, "state": "acted", "reason": "checked"},
                root=root,
            )
        )
    finally:
        reset_caller_seat(token)
    assert closed["ok"] is True
    token = set_caller_seat("daemon")
    try:
        refused = json.loads(
            sl.handle_signal_tool(
                "signal_ack",
                {"signal_id": sid, "state": "dismissed", "reason": "x"},
                root=root,
            )
        )
    finally:
        reset_caller_seat(token)
    assert refused["ok"] is False


# ═══════════════════════════════════════════════════════════════════════════
# Cross-substrate review 2026-09-06 (Astra, Codex seat 3/3) — verdict reject.
# One test per finding, named after it. Each reproduces the reviewer's own
# scenario before asserting the fix.
# ═══════════════════════════════════════════════════════════════════════════


def _append_raw(root: Path, line: str) -> None:
    path = sl.ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


class TestP1RowSchemaValidatedAtLoad:
    """P1 1 — row schema validated at load; corruption cannot erase an open signal."""

    def test_partial_row_does_not_overwrite_valid_prior_row(self, tmp_sovereign_root):
        # The reviewer's exact reproduction: a valid open row for id x, then
        # `{"signal_id": "x"}` appended. Both heartbeat and signals_summary
        # reported a healthy zero.
        root = tmp_sovereign_root
        sl.open_signal(
            source="halt", native_id="x.md", produced_at="2026-09-01T00:00:00Z", root=root
        )
        sid = sl.signal_id_for("halt", "x.md")
        _append_raw(root, json.dumps({"signal_id": sid}))
        state = sl.load_state(root)
        assert state.latest[sid]["state"] == "open", "corrupt row displaced the valid row"
        assert state.corrupt_count == 1
        _guardian_ok(root)
        sl.scan_all(root)
        field = sl.heartbeat_field(root)
        assert sl.summarize(root)["open_measured"] == 1, "the open signal survived the corrupt row"
        # ...but it is a FLOOR, not a total. A corrupt row is an unknown number
        # of lost signals, so the total is null — "never report zero on error"
        # generalises to "never report a number you cannot stand behind".
        assert field["total"] is None
        assert field["error"], "a corrupt row must never read as clean"
        assert field["corrupt_rows"] == 1
        summary = json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))
        assert summary["error"]
        assert summary["total"] is None

    def test_bad_state_bad_source_and_missing_reason_are_corrupt(self):
        base = {
            "signal_id": "s1",
            "source": "halt",
            "produced_at": "2026-09-01T00:00:00Z",
            "owner": "watch-2/3",
            "state": "open",
        }
        assert sl._validate_row(base) is None
        assert "state" in sl._validate_row({**base, "state": "banana"})
        assert "source" in sl._validate_row({**base, "source": "not-a-source"})
        assert "source" in sl._validate_row({**base, "source": ["halt"]})
        assert "owner" in sl._validate_row({**base, "owner": ""})
        assert "produced_at" in sl._validate_row({**base, "produced_at": "not-a-date"})
        closed = {**base, "state": "acted", "closed_by": "seat:x", "closed_at": base["produced_at"]}
        assert sl._validate_row({**closed, "reason": "did it"}) is None
        assert "reason" in sl._validate_row({**closed, "reason": None})
        assert "reason" in sl._validate_row({**closed, "reason": "   "})
        assert "closed_by" in sl._validate_row({**closed, "reason": "x", "closed_by": ""})

    def test_list_shaped_source_cannot_abort_the_dashboard_snapshot(self, tmp_sovereign_root):
        # dashboard_web.build_snapshot calls heartbeat_field with no section
        # guard, so an uncaught TypeError here took the whole console down.
        root = tmp_sovereign_root
        _append_raw(
            root,
            json.dumps(
                {
                    "signal_id": "s",
                    "source": ["halt"],
                    "produced_at": "2026-09-01T00:00:00Z",
                    "owner": "watch-2/3",
                    "state": "open",
                }
            ),
        )
        field = sl.heartbeat_field(root)  # must not raise
        assert field["error"]
        # The reviewer's exact complaint: this returned total 0 beside a
        # non-null error, "directly violating never report zero on error".
        assert field["total"] is None
        assert field["corrupt_rows"] == 1

    def test_unparseable_produced_at_is_corrupt_not_invisible(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _append_raw(
            root,
            json.dumps(
                {
                    "signal_id": "s",
                    "source": "halt",
                    "produced_at": "whenever",
                    "owner": "watch-2/3",
                    "state": "open",
                }
            ),
        )
        field = sl.heartbeat_field(root)
        assert field["error"] and field["corrupt_rows"] == 1


class TestP1ScanMarkerCannotMaskLedgerLoss:
    """P1 2 — a marker can never turn ledger loss or failed ingestion into ok."""

    def test_deleted_ledger_after_a_scan_is_an_error_not_a_zero(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        (root / "daemons" / "halts").mkdir(parents=True, exist_ok=True)
        (root / "daemons" / "halts" / "old.md").write_text("halt\n", encoding="utf-8")
        _guardian_ok(root)
        sl.scan_all(root)
        assert sl.heartbeat_field(root)["total"] == 1
        sl.ledger_path(root).unlink()
        field = sl.heartbeat_field(root)
        assert field["error"] == "ledger_missing"
        assert field["total"] is None
        assert field["ingestion"] == "error"

    def test_a_marker_that_carries_no_counts_certifies_nothing(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        path = sl.scan_marker_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"bogus": True}), encoding="utf-8")
        field = sl.heartbeat_field(root)
        assert field["error"].startswith("marker_invalid")
        assert field["total"] is None

    def test_marker_claiming_more_than_the_ledger_holds_is_loss(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        sl.scan_all(root)
        marker = json.loads(sl.scan_marker_path(root).read_text(encoding="utf-8"))
        marker["counts"]["halt"] = 5
        sl.scan_marker_path(root).write_text(json.dumps(marker), encoding="utf-8")
        field = sl.heartbeat_field(root)
        assert field["error"].startswith("ledger_shrank")
        assert field["total"] is None or field["ingestion"] == "error"

    def test_a_scan_materialises_the_ledger_so_empty_is_not_absent(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        sl.scan_all(root)
        assert sl.ledger_path(root).exists(), (
            "scanned-and-empty must leave a ledger, or it is indistinguishable "
            "from scanned-then-lost"
        )

    def test_a_source_that_raises_does_not_blind_the_others(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        (root / "daemons" / "halts").mkdir(parents=True, exist_ok=True)
        (root / "daemons" / "halts" / "h.md").write_text("halt\n", encoding="utf-8")

        def boom(*_a, **_k):
            raise RuntimeError("source exploded")

        monkeypatch.setattr(sl, "scan_watchman", boom)
        out = sl.scan_all(root)
        assert out["source_status"]["watchman"].startswith("failed:")
        assert out["counts"]["halt"] == 1, "a later source still ran"
        field = sl.heartbeat_field(root)
        assert "watchman" in field["sources_degraded"]
        assert field["by_source"]["watchman"] is None


class TestP1GuardianWiredToTheRealReader:
    """P1 3 — guardian ingestion uses the reader the code has; unavailable is not zero."""

    def test_default_provider_is_the_live_dashboard_reader(self):
        from sovereign_stack import dashboard_readers

        src = sl.default_guardian_provider.__code__.co_names
        assert "read_guardian" in src, "the guardian seam must call the real reader"
        assert callable(dashboard_readers.read_guardian)

    def test_unavailable_guardian_reports_unavailable_never_zero(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        result = sl.scan_guardian(root, provider=lambda: None)
        assert result.opened == 0
        assert result.status == "unavailable"
        sl.scan_all(root, guardian_provider=lambda: None)
        field = sl.heartbeat_field(root)
        assert field["source_status"]["guardian"] == "unavailable"
        assert field["by_source"]["guardian"] is None, "unavailable must not render as 0"
        assert "guardian" in field["sources_degraded"]

    def test_supplied_provider_in_reader_shape_opens_signals(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        payload = {
            "source": "guardian_tools._evaluate_status",
            "issues": ["Ollama bound on all interfaces", "18 ports listening"],
            "issue_count": 2,
        }
        assert sl.scan_guardian(root, provider=lambda: payload).opened == 2
        assert sl.scan_guardian(root, provider=lambda: payload).opened == 0

    def test_unreadable_source_file_is_degraded_not_a_healthy_zero(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        honks = root / "nape" / "honks.jsonl"
        honks.parent.mkdir(parents=True, exist_ok=True)
        honks.write_text("{broken}\n", encoding="utf-8")
        result = sl.scan_honks(root)
        assert result.opened == 0
        assert result.status.startswith("degraded"), result.status
        sl.scan_all(root)
        field = sl.heartbeat_field(root)
        assert "honk" in field["sources_degraded"]
        assert field["by_source"]["honk"] is None


class TestP1ProducerSeparationIsAnActorCheck:
    """P1 4 — closed_by comes from the server-known identity, never the call."""

    def test_the_tool_schema_has_no_closer_parameter(self):
        tool = next(t for t in sl.SIGNAL_TOOLS if t.name == "signal_ack")
        props = tool.inputSchema["properties"]
        assert "owner" not in props, "a closer you can type is a closer you can forge"
        assert set(tool.inputSchema["required"]) == {"signal_id", "state"}

    def test_a_caller_cannot_choose_who_closed_it(self, tmp_sovereign_root):
        """UPDATED BY REVIEW N3. `owner` used to be silently ignored here; an
        ignored identity argument is indistinguishable from an honoured one,
        so it is now refused by name. The identity comes from the dispatch
        context and from nowhere else."""
        from sovereign_stack.dispatch_context import reset_caller_seat, set_caller_seat

        root = tmp_sovereign_root
        sl.open_signal(
            source="halt", native_id="a.md", produced_at="2026-09-01T00:00:00Z", root=root
        )
        sid = sl.signal_id_for("halt", "a.md")
        token = set_caller_seat("seat:spiral_20260906_000000")
        try:
            refused = json.loads(
                sl.handle_signal_tool(
                    "signal_ack",
                    {
                        "signal_id": sid,
                        "state": "acted",
                        "reason": "done",
                        "owner": "somebody-else",
                    },
                    root=root,
                )
            )
            out = json.loads(
                sl.handle_signal_tool(
                    "signal_ack",
                    {"signal_id": sid, "state": "acted", "reason": "done"},
                    root=root,
                )
            )
        finally:
            reset_caller_seat(token)
        assert refused["ok"] is False
        assert "'owner' is not an accepted argument" in refused["error"]
        assert out["ok"] is True
        assert out["row"]["closed_by"] == "seat:spiral_20260906_000000"
        assert out["row"]["owner"] == "watch-2/3", "assignment is not the actor"

    def test_producer_self_close_is_refused_on_the_server_known_identity(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        sl.open_signal(
            source="halt", native_id="b.md", produced_at="2026-09-01T00:00:00Z", root=root
        )
        sid = sl.signal_id_for("halt", "b.md")
        # halt's producer is "daemon". The old check was an exact string
        # compare on a caller-supplied label, so "Daemon" and whitespace
        # aliases sailed past it.
        for spelling in ("daemon", "Daemon", "  DAEMON  "):
            with pytest.raises(PermissionError):
                sl.ack_signal(sid, actor=spelling, state="dismissed", reason="nope", root=root)

    def test_a_call_with_no_resolvable_actor_is_refused(self, tmp_sovereign_root):
        """An UNSET dispatch context is a refusal. There is no longer an
        `actor` parameter to pass a blank into (review N3) — the absence of
        an established identity is the case, and it is the default case."""
        root = tmp_sovereign_root
        sl.open_signal(
            source="halt", native_id="c.md", produced_at="2026-09-01T00:00:00Z", root=root
        )
        sid = sl.signal_id_for("halt", "c.md")
        out = json.loads(
            sl.handle_signal_tool(
                "signal_ack",
                {"signal_id": sid, "state": "acted", "reason": "x"},
                root=root,
            )
        )
        assert out["ok"] is False
        assert "caller identity" in out["error"]

    def test_the_dispatch_layer_supplies_a_server_generated_actor(self):
        from sovereign_stack import server

        actor = server._signal_actor()
        assert actor.startswith("seat:")
        assert server.spiral_state.session_id in actor
        assert actor not in sl.SOURCE_PRODUCER.values()


class TestP1LedgerToolsNotInTheRemoteBaseTier:
    """P1 5 — the outside Claude surface does not widen in this release."""

    def test_neither_ledger_tool_is_base_tier(self):
        from claude_bridge.tiers import BASE_TOOLS, DESTRUCTIVE_TOOLS, classify

        for name in ("signals_summary", "signal_ack"):
            assert name not in BASE_TOOLS
            assert name not in DESTRUCTIVE_TOOLS
            assert classify(name) == "step_up", "unclassified must fail closed to step-up"

    def test_the_hold_is_declared_not_merely_absent(self):
        from claude_bridge.tiers import HELD_UNCLASSIFIED

        # descend (THE DESCENT) was held here 2026-10-08 → 2026-10-10 for the
        # same reason, then classified BASE on Anthony's word (2026-10-10), so
        # it leaves the hold. Still an equality, so an unreviewed addition to
        # (or removal from) the hold fails here rather than passing.
        assert frozenset({"signals_summary", "signal_ack"}) == HELD_UNCLASSIFIED


class TestP2HonkIdTypeNormalisedOnBothSides:
    """P2 — an acknowledged honk with a non-string id stayed open forever."""

    def test_integer_honk_id_matches_its_integer_ack(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _write_jsonl(
            root / "nape" / "honks.jsonl", [{"honk_id": 7, "timestamp": "2026-09-01T00:00:00Z"}]
        )
        _write_jsonl(root / "nape" / "acks.jsonl", [{"honk_id": 7}])
        assert sl.scan_honks(root).opened == 1
        latest = sl.load_latest(root)
        assert latest[sl.signal_id_for("honk", "7")]["state"] == "acknowledged"

    def test_list_shaped_id_is_skipped_and_counted_not_raised(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _write_jsonl(
            root / "nape" / "honks.jsonl",
            [
                {"honk_id": ["bad"], "timestamp": "2026-09-01T00:00:00Z"},
                {"honk_id": "good", "timestamp": "2026-09-01T00:00:00Z"},
            ],
        )
        result = sl.scan_honks(root)
        assert result.opened == 1
        assert result.skipped == 1
        assert result.status.startswith("degraded")

    def test_non_object_proposal_json_does_not_abort_the_scan(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        pending = root / "grok_bridge" / "pending_writes"
        pending.mkdir(parents=True)
        (pending / "a.json").write_text("[]", encoding="utf-8")
        (pending / "b.json").write_text("null", encoding="utf-8")
        (pending / "c.json").write_text(json.dumps({"proposal_id": "ok"}), encoding="utf-8")
        result = sl.scan_proposals(root)
        assert result.opened == 1
        assert result.skipped == 2


class TestP2AnonymousThreadIdsHashTheRelativePath:
    """P2 — two shards with identical rows collapsed into one signal."""

    def test_same_row_in_two_domains_makes_two_signals(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        d = root / "chronicle" / "open_threads"
        row = {"question": "what now", "timestamp": "2026-09-01T00:00:00Z"}
        _write_jsonl(d / "domain_a" / "log.jsonl", [row])
        _write_jsonl(d / "domain_b" / "log.jsonl", [row])
        assert sl.scan_threads(root).opened == 2, "basename hashing collapsed these into one"
        _guardian_ok(root)
        sl.scan_all(root)
        assert sl.heartbeat_field(root)["total"] == 2


class TestP2OpenIsIdempotentUnderTheAppendLock:
    """P2 — check-and-append must happen inside one lock hold."""

    def test_concurrent_opens_of_one_id_append_one_row(self, tmp_sovereign_root):
        import threading

        root = tmp_sovereign_root
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def go():
            try:
                barrier.wait(timeout=5)
                sl.open_signal(
                    source="halt",
                    native_id="race.md",
                    produced_at="2026-09-01T00:00:00Z",
                    root=root,
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors, errors
        rows = [
            line
            for line in sl.ledger_path(root).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(rows) == 1, f"one signal, one open event; got {len(rows)}"


class TestP2ClosureReasonsAreNonBlankStrings:
    """P2 — str()-coercion put Python reprs in the audit trail."""

    def test_non_string_reasons_are_refused_not_stringified(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        sl.open_signal(
            source="halt", native_id="r.md", produced_at="2026-09-01T00:00:00Z", root=root
        )
        sid = sl.signal_id_for("halt", "r.md")
        for bad in ({"reason": ""}, [None], True, 1):
            with pytest.raises(ValueError, match="reason must be a string"):
                sl.ack_signal(sid, actor="watch-2/3", state="acted", reason=bad, root=root)
        for blank in (None, "", "   "):
            with pytest.raises(ValueError, match="reason"):
                sl.ack_signal(sid, actor="watch-2/3", state="acted", reason=blank, root=root)
        row = sl.ack_signal(sid, actor="watch-2/3", state="acted", reason="  real  ", root=root)
        assert row["reason"] == "real"


class TestSignalsSummarySourceFilter:
    """Fold — nape_honks / nape_honks_with_history become signals_summary(source='honk')."""

    def test_source_filter_narrows_to_one_source(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _write_jsonl(
            root / "nape" / "honks.jsonl", [{"honk_id": "h1", "timestamp": "2026-09-01T00:00:00Z"}]
        )
        (root / "daemons" / "halts").mkdir(parents=True, exist_ok=True)
        (root / "daemons" / "halts" / "h.md").write_text("halt\n", encoding="utf-8")
        _guardian_ok(root)
        sl.scan_all(root)
        out = json.loads(sl.handle_signal_tool("signals_summary", {"source": "honk"}, root=root))
        assert out["ok"] is True
        assert set(out["by_source"]) == {"honk"}
        assert out["by_source"]["honk"]["open"] == 1
        bad = json.loads(sl.handle_signal_tool("signals_summary", {"source": "nope"}, root=root))
        assert bad["ok"] is False


# ── ONE COUNTER ACROSS TWO STORES (2026-10-21) ──────────────────────────────
#
# The console counts nape/honks.jsonl against nape/acks.jsonl; the ledger
# counts open honk-source signals. The sync was one-way: scan_honks turns a
# nape ack into a ledger close, but a signal_ack close of a honk signal never
# wrote a nape ack, so every ledger close left the console counting a honk the
# ledger called closed (2026-10-14: console 1064, ledger 59). These tests read
# both counters the way their production callers do: the console through
# dashboard.count_unacked_honks, the ledger through the latest-row fold.

from sovereign_stack.dashboard import count_unacked_honks  # noqa: E402
from sovereign_stack.dispatch_context import reset_caller_seat, set_caller_seat  # noqa: E402

ONE_COUNTER_SEAT = "seat:hq-test-seat"


def _honk(hid: str, level: str = "sharp", pattern: str = "declare_before_verify") -> dict:
    return {
        "honk_id": hid,
        "session_id": "one-counter-session",
        "timestamp": "2026-10-21T12:00:00Z",
        "level": level,
        "pattern": pattern,
        "trigger_tool": "record_insight",
        "observation": f"{hid} observation",
    }


def _console(root: Path) -> int:
    return count_unacked_honks(root / "nape" / "honks.jsonl")


def _ledger_open_honks(root: Path) -> int:
    return sum(
        1
        for row in sl.load_latest(root).values()
        if row.get("source") == "honk" and row.get("state") == "open"
    )


def _seat_ack(root: Path, sid: str, state: str = "acted", reason: str = "checked by hand") -> dict:
    token = set_caller_seat(ONE_COUNTER_SEAT)
    try:
        return json.loads(
            sl.handle_signal_tool(
                "signal_ack", {"signal_id": sid, "state": state, "reason": reason}, root=root
            )
        )
    finally:
        reset_caller_seat(token)


def _ack_rows(root: Path) -> list[dict]:
    path = root / "nape" / "acks.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_signal_ack_of_a_honk_signal_writes_the_nape_ack(tmp_sovereign_root):
    """The regression: a ledger close must close the console's honk too."""
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-sharp")])
    assert sl.scan_honks(root).opened == 1
    sid = sl.signal_id_for("honk", "h-sharp")
    assert _console(root) == 1
    out = _seat_ack(root, sid)
    assert out["ok"] is True and out["row"]["state"] == "acted"
    assert _console(root) == 0
    acks = _ack_rows(root)
    assert [a["honk_id"] for a in acks] == ["h-sharp"]
    assert sid in acks[0]["note"]


def test_console_and_ledger_agree_through_scan_and_close(tmp_sovereign_root):
    """Two actionable honks and one informational: both counters read 2, then 1."""
    root = tmp_sovereign_root
    _write_jsonl(
        root / "nape" / "honks.jsonl",
        [
            _honk("h-a"),
            _honk("h-b", level="uneasy", pattern="repeated_mistake"),
            _honk("h-s", level="satisfied", pattern="clean_verify_declare"),
        ],
    )
    sl.scan_honks(root)
    assert _console(root) == _ledger_open_honks(root) == 2
    out = _seat_ack(root, sl.signal_id_for("honk", "h-a"))
    assert out["ok"] is True
    assert _console(root) == _ledger_open_honks(root) == 1


def test_re_ack_writes_no_second_nape_ack(tmp_sovereign_root):
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sl.scan_honks(root)
    sid = sl.signal_id_for("honk", "h-1")
    first = _seat_ack(root, sid)
    assert first["nape_ack"]["status"] == "written"
    assert first["nape_ack"]["honk_id"] == "h-1"
    second = _seat_ack(root, sid, state="dismissed", reason="second look, still fine")
    assert second["ok"] is True
    assert second["nape_ack"] == {"status": "already_acked", "honk_id": "h-1"}
    assert [a["honk_id"] for a in _ack_rows(root)] == ["h-1"]


def test_a_honk_nape_acked_before_the_close_is_not_acked_twice(tmp_sovereign_root):
    """nape_ack lands first (no sweep since), then the seat closes the signal."""
    from sovereign_stack.nape_daemon import NapeDaemon

    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sl.scan_honks(root)
    NapeDaemon(root=str(root)).ack("h-1", "acked through the nape_ack tool")
    out = _seat_ack(root, sl.signal_id_for("honk", "h-1"))
    assert out["ok"] is True
    assert out["nape_ack"]["status"] == "already_acked"
    assert len(_ack_rows(root)) == 1


def test_the_nape_ack_note_names_the_signal_state_and_closer(tmp_sovereign_root):
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sl.scan_honks(root)
    sid = sl.signal_id_for("honk", "h-1")
    out = _seat_ack(root, sid, state="dismissed", reason="false positive")
    (ack,) = _ack_rows(root)
    assert ack["ack_id"] == out["nape_ack"]["ack_id"]
    assert set(ack) == {"ack_id", "honk_id", "note", "acked_at"}  # nape's own shape
    assert f"signal_id={sid}" in ack["note"]
    assert "state=dismissed" in ack["note"]
    assert f"closed_by={ONE_COUNTER_SEAT}" in ack["note"]


def test_missing_nape_store_never_raises_and_is_never_created(tmp_sovereign_root):
    root = tmp_sovereign_root
    (root / "nape").rmdir()  # the sandbox pre-creates it empty; take it away
    sl.open_signal(
        source="honk",
        native_id="h-gone",
        produced_at="2026-10-21T12:00:00Z",
        root=root,
        origin_path="nape/honks.jsonl",
    )
    sid = sl.signal_id_for("honk", "h-gone")
    out = _seat_ack(root, sid)
    assert out["ok"] is True and out["row"]["state"] == "acted"
    assert out["nape_ack"]["status"] == "skipped"
    assert "nape store absent" in out["nape_ack"]["reason"]
    assert not (root / "nape").exists(), "skipping must not create the nape store"
    assert sl.load_latest(root)[sid]["closed_by"] == ONE_COUNTER_SEAT
    assert "nape ack mirror skipped" in sl.diagnostics_path(root).read_text(encoding="utf-8")


def test_a_honk_nape_does_not_know_is_skipped_not_invented(tmp_sovereign_root):
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-real")])
    sl.open_signal(
        source="honk",
        native_id="h-not-on-disk",
        produced_at="2026-10-21T12:00:00Z",
        root=root,
        origin_path="nape/honks.jsonl",
    )
    out = _seat_ack(root, sl.signal_id_for("honk", "h-not-on-disk"))
    assert out["ok"] is True
    assert out["nape_ack"]["status"] == "skipped"
    assert "No honk found" in out["nape_ack"]["reason"]
    assert _ack_rows(root) == []


def test_a_row_with_no_origin_is_skipped_with_a_reason(tmp_sovereign_root):
    """Rows written before `origin` existed (2026-09-06) cannot name their honk."""
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sid = sl.signal_id_for("honk", "h-1")
    sl._append(
        sl._row(
            signal_id=sid,
            source="honk",
            produced_at="2026-09-01T00:00:00Z",
            owner="watch-2/3",
            state="open",
            reason=None,
            closed_by=None,
            closed_at=None,
            updated_at="2026-09-01T00:00:00Z",
        ),
        root,
    )
    out = _seat_ack(root, sid)
    assert out["ok"] is True
    assert out["nape_ack"]["status"] == "skipped"
    assert "origin.native_id" in out["nape_ack"]["reason"]
    assert _ack_rows(root) == []


def test_a_non_honk_close_is_not_applicable_and_touches_no_nape_file(tmp_sovereign_root):
    root = tmp_sovereign_root
    sl.open_signal(source="halt", native_id="h1.md", produced_at="2026-10-21T00:00:00Z", root=root)
    out = _seat_ack(root, sl.signal_id_for("halt", "h1.md"), state="dismissed", reason="stale")
    assert out["ok"] is True
    assert out["nape_ack"]["status"] == "not_applicable"
    assert list((root / "nape").iterdir()) == []


def test_a_close_derived_from_a_nape_ack_is_not_mirrored_back(tmp_sovereign_root):
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    _write_jsonl(root / "nape" / "acks.jsonl", [{"honk_id": "h-1", "note": "nape side"}])
    sl.scan_honks(root)  # opens, then closes as the nape-ack closer
    latest = sl.load_latest(root)[sl.signal_id_for("honk", "h-1")]
    assert latest["closed_by"] == sl.NAPE_ACK_CLOSER
    assert len(_ack_rows(root)) == 1
    mirror: dict = {}
    sl.ack_signal(
        sl.signal_id_for("honk", "h-1"),
        actor=sl.NAPE_ACK_CLOSER,
        state="acknowledged",
        reason="nape acks.jsonl",
        root=root,
        nape_mirror=mirror,
    )
    assert mirror["status"] == "skipped"
    assert len(_ack_rows(root)) == 1


def test_rescan_after_the_mirror_leaves_the_seat_close_standing(tmp_sovereign_root):
    """The nape ack the mirror wrote must not let scan_honks re-close as nape-ack."""
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sl.scan_honks(root)
    sid = sl.signal_id_for("honk", "h-1")
    _seat_ack(root, sid, state="dismissed", reason="known false positive")
    sl.scan_honks(root)
    latest = sl.load_latest(root)[sid]
    assert latest["closed_by"] == ONE_COUNTER_SEAT
    assert latest["state"] == "dismissed" and latest["reason"] == "known false positive"
    rows = [
        json.loads(x)
        for x in sl.ledger_path(root).read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    assert [r["state"] for r in rows if r["signal_id"] == sid] == ["open", "dismissed"]


def test_a_successful_mirrored_ack_is_not_observed_and_honks_nothing(tmp_sovereign_root):
    from sovereign_stack.nape_daemon import NapeDaemon

    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sl.scan_honks(root)
    sid = sl.signal_id_for("honk", "h-1")
    args = {"signal_id": sid, "state": "acted", "reason": "error fixed; the failed write is done"}
    token = set_caller_seat(ONE_COUNTER_SEAT)
    try:
        result = sl.handle_signal_tool("signal_ack", args, root=root)
    finally:
        reset_caller_seat(token)
    assert json.loads(result)["nape_ack"]["status"] == "written"
    honks_before = (root / "nape" / "honks.jsonl").read_text(encoding="utf-8")
    NapeDaemon(root=str(root)).observe("signal_ack", args, result, "one-counter-session")
    assert (root / "nape" / "honks.jsonl").read_text(encoding="utf-8") == honks_before
    obs = root / "nape" / "observations.jsonl"
    assert not obs.exists() or "signal_ack" not in obs.read_text(encoding="utf-8")
    assert sl.scan_honks(root).opened == 0
    assert _console(root) == _ledger_open_honks(root) == 0


def test_a_mirror_failure_is_reported_and_the_close_stands(tmp_sovereign_root, monkeypatch):
    from sovereign_stack.nape_daemon import NapeDaemon

    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sl.scan_honks(root)
    sid = sl.signal_id_for("honk", "h-1")

    def boom(self, honk_id, note):
        raise OSError("disk said no")

    monkeypatch.setattr(NapeDaemon, "ack_once", boom)
    out = _seat_ack(root, sid)
    assert out["ok"] is True and out["row"]["state"] == "acted"
    assert out["nape_ack"]["status"] == "failed"
    assert "OSError" in out["nape_ack"]["error"]
    assert sl.load_latest(root)[sid]["state"] == "acted"
    # Honest about the divergence it could not close: the console still counts it.
    assert _console(root) == 1
    assert "nape ack mirror failed" in sl.diagnostics_path(root).read_text(encoding="utf-8")


def test_a_direct_library_close_mirrors_too(tmp_sovereign_root):
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sl.scan_honks(root)
    sl.ack_signal(
        sl.signal_id_for("honk", "h-1"),
        actor="seat:a-cleanup-script",
        state="acknowledged",
        reason=None,
        root=root,
    )
    assert [a["honk_id"] for a in _ack_rows(root)] == ["h-1"]
    assert _console(root) == 0


def test_an_informational_honk_opens_no_signal(tmp_sovereign_root):
    root = tmp_sovereign_root
    _write_jsonl(
        root / "nape" / "honks.jsonl",
        [_honk("h-s", level="satisfied", pattern="clean_verify_declare")],
    )
    assert sl.scan_honks(root).opened == 0
    assert sl.signal_id_for("honk", "h-s") not in sl.load_latest(root)


def test_an_informational_signal_opened_before_the_rule_still_closes(tmp_sovereign_root):
    """Only the OPEN is skipped: a nape ack still closes one already open."""
    root = tmp_sovereign_root
    _write_jsonl(
        root / "nape" / "honks.jsonl",
        [_honk("h-s", level="satisfied", pattern="clean_verify_declare")],
    )
    sl.open_signal(
        source="honk",
        native_id="h-s",
        produced_at="2026-10-01T00:00:00Z",
        root=root,
        kind="clean_verify_declare",
        origin_path="nape/honks.jsonl",
    )
    sid = sl.signal_id_for("honk", "h-s")
    assert sl.load_latest(root)[sid]["state"] == "open"
    _write_jsonl(root / "nape" / "acks.jsonl", [{"honk_id": "h-s"}])
    sl.scan_honks(root)
    assert sl.load_latest(root)[sid]["state"] == "acknowledged"


def test_heartbeat_ledger_count_and_console_agree_after_a_close(tmp_sovereign_root):
    """The production pair: honk_counts.all_time_unacked reads
    count_unacked_honks; honk_counts.ledger_open reads heartbeat_field's
    by_source["honk"]. Both move on one signal_ack."""
    root = tmp_sovereign_root
    _write_jsonl(
        root / "nape" / "honks.jsonl",
        [
            _honk("h-a"),
            _honk("h-b"),
            _honk("h-s", level="satisfied", pattern="clean_verify_declare"),
        ],
    )
    field = _scan_measured(root)
    assert field["by_source"]["honk"] == _console(root) == 2
    _seat_ack(root, sl.signal_id_for("honk", "h-b"))
    assert sl.heartbeat_field(root)["by_source"]["honk"] == _console(root) == 1


def test_an_unreadable_nape_store_never_turns_a_landed_close_into_ok_false(tmp_sovereign_root):
    """NEVER RAISES, structurally. Path.exists() re-raises EACCES rather than
    answering False, so a nape dir the process cannot search must land as a
    `failed` outcome beside ok:true, never as an exception after the ledger
    close was already appended (which the tool would report as ok:false)."""
    import os
    import stat

    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions, so EACCES cannot be produced")
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sl.scan_honks(root)
    sid = sl.signal_id_for("honk", "h-1")
    nape_dir = root / "nape"
    mode = stat.S_IMODE(nape_dir.stat().st_mode)
    os.chmod(nape_dir, 0)
    try:
        out = _seat_ack(root, sid)
    finally:
        os.chmod(nape_dir, mode)
    assert out["ok"] is True, out
    assert out["nape_ack"]["status"] == "failed"
    assert "PermissionError" in out["nape_ack"]["error"]
    assert sl.load_latest(root)[sid]["closed_by"] == ONE_COUNTER_SEAT


# ── Review of bfc5507: R1, R2, F4 ───────────────────────────────────────────


def test_a_mirrored_ack_is_never_glued_onto_a_line_missing_its_newline(tmp_sovereign_root):
    """R1. The last ack lacks its newline; the mirror's ack must start a new line.
    On bfc5507 the two acks fused into one unparseable line, hiding BOTH, and the
    console count ROSE (1 -> 2) on a close."""
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1"), _honk("h-2")])
    (root / "nape" / "acks.jsonl").write_text(
        '{"honk_id": "h-1", "note": "nape side"}', encoding="utf-8"
    )
    sl.scan_honks(root)
    assert _console(root) == 1
    out = _seat_ack(root, sl.signal_id_for("honk", "h-2"))
    assert out["ok"] is True
    assert _console(root) == 0
    assert sorted(a["honk_id"] for a in _ack_rows(root)) == ["h-1", "h-2"]


def test_a_mirrored_ack_that_does_not_read_back_is_failed_not_written(
    tmp_sovereign_root, monkeypatch
):
    """R1. A write that did not land must not be reported as `written`."""
    from sovereign_stack import nape_daemon as nd

    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    _write_jsonl(root / "nape" / "acks.jsonl", [{"honk_id": "unrelated"}])
    sl.scan_honks(root)
    monkeypatch.setattr(nd, "_append_jsonl", lambda path, record: None)  # a lost write
    out = _seat_ack(root, sl.signal_id_for("honk", "h-1"))
    assert out["ok"] is True
    assert out["nape_ack"]["status"] == "failed"
    assert _console(root) == 1


def _append_bytes(path: Path, data: bytes) -> None:
    with path.open("ab") as fh:
        fh.write(data)


def test_an_invalid_byte_in_acks_fails_the_mirror_and_is_not_skipped(tmp_sovereign_root):
    """R2. UnicodeDecodeError is a ValueError; it is a store-read failure, not
    nape refusing the id, so the outcome is `failed`."""
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    _write_jsonl(root / "nape" / "acks.jsonl", [{"honk_id": "unrelated"}])
    sl.scan_honks(root)
    _append_bytes(root / "nape" / "acks.jsonl", b"\xff\xfe\n")
    out = _seat_ack(root, sl.signal_id_for("honk", "h-1"))
    assert out["ok"] is True
    assert out["nape_ack"]["status"] == "failed"
    assert "UnicodeDecodeError" in out["nape_ack"]["error"]


def test_an_invalid_byte_in_honks_fails_the_mirror_and_is_not_skipped(tmp_sovereign_root):
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sl.scan_honks(root)
    _append_bytes(root / "nape" / "honks.jsonl", b"\xff\xfe\n")
    out = _seat_ack(root, sl.signal_id_for("honk", "h-1"))
    assert out["ok"] is True
    assert out["nape_ack"]["status"] == "failed"
    assert "UnicodeDecodeError" in out["nape_ack"]["error"]


def test_the_nape_ack_is_written_after_the_ledger_close(tmp_sovereign_root, monkeypatch):
    """F4. Ledger first, nape second: the order the rescan safety rests on. The
    review's order-swap mutation survived the suite; this kills it."""
    root = tmp_sovereign_root
    _write_jsonl(root / "nape" / "honks.jsonl", [_honk("h-1")])
    sl.scan_honks(root)
    calls: list[str] = []
    orig_append = sl._append
    orig_mirror = sl._mirror_close_to_nape

    def recording_append(row, root=None):
        calls.append(f"ledger:{row.get('state')}")
        return orig_append(row, root)

    def recording_mirror(*args, **kwargs):
        calls.append("nape")
        return orig_mirror(*args, **kwargs)

    monkeypatch.setattr(sl, "_append", recording_append)
    monkeypatch.setattr(sl, "_mirror_close_to_nape", recording_mirror)
    out = _seat_ack(root, sl.signal_id_for("honk", "h-1"))
    assert out["nape_ack"]["status"] == "written"
    assert calls == ["ledger:acted", "nape"]
