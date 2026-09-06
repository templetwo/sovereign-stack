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
        sl.ack_signal(sid, owner="watch-2/3", state="dismissed", reason="", root=root)
    with pytest.raises(PermissionError):
        sl.ack_signal(sid, owner="daemon", state="dismissed", reason="nope", root=root)
    row = sl.ack_signal(sid, owner="watch-2/3", state="dismissed", reason="stale halt", root=root)
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
    assert sl.scan_honks(root) == 2
    assert sl.scan_honks(root) == 0
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
    assert s["total"] == 3
    assert s["stale_24h"] == 2
    assert s["stale_7d"] == 1
    assert s["by_source"]["halt"]["open"] == 3


def test_heartbeat_error_not_zero(tmp_sovereign_root, monkeypatch):
    root = tmp_sovereign_root
    sl.open_signal(source="halt", native_id="x.md", produced_at="2026-09-01T00:00:00Z", root=root)
    good = sl.heartbeat_field(root)
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
    assert sl.scan_halts(root) == 1
    assert sl.scan_halts(root) == 0
    assert sl.scan_decisions(root) == 1
    assert sl.scan_guardian(root) == 2
    assert sl.scan_guardian(root) == 0
    latest = sl.load_latest(root)
    assert latest[sl.signal_id_for("halt", "20260804T131703.md")]["state"] == "open"
    assert latest[sl.signal_id_for("decision", "metabolize_20260520.md")]["state"] == "open"
    assert latest[sl.signal_id_for("guardian", "Ollama bound on all interfaces")]["state"] == "open"


def test_handle_tools(tmp_sovereign_root):
    root = tmp_sovereign_root
    sl.open_signal(source="halt", native_id="z.md", produced_at="2026-09-01T00:00:00Z", root=root)
    summary = json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))
    assert summary["ok"] is True
    assert summary["total"] == 1
    sid = sl.signal_id_for("halt", "z.md")
    closed = json.loads(
        sl.handle_signal_tool(
            "signal_ack",
            {"signal_id": sid, "owner": "watch-2/3", "state": "acted", "reason": "checked"},
            root=root,
        )
    )
    assert closed["ok"] is True
    refused = json.loads(
        sl.handle_signal_tool(
            "signal_ack",
            {"signal_id": sid, "owner": "daemon", "state": "dismissed", "reason": "x"},
            root=root,
        )
    )
    assert refused["ok"] is False
