"""Signals by source, never a total (design A3, plan 1 Task 9).

`unacked_signals` led with `total`. On the night of 2026-10-20/21 that was 322
open signals, 196 of them open threads by construction, and hq-pulse printed
the same number on 87 of 88 runs while the house was dark: a count dominated by
a backlog that moves only when a thread is resolved cannot warn about anything
else. So the field reports every count by source and publishes no cross-source
sum at all. Not `total`, and not the sums that rebuild it under another name:
`total_configured` equals `total` whenever every source is configured, and the
top-level stale windows are mostly the same thread backlog. Per source the
stale windows still stand, in `by_source_detail` and in `stale_24h_by_source`.

Tmp roots only. Nothing here reads, counts or compares the live store.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sovereign_stack import connectivity as conn
from sovereign_stack import dashboard_readers as readers
from sovereign_stack import dashboard_web as web
from sovereign_stack import signal_ledger as sl

NOTE = "a total would be mostly the open-thread backlog by construction; read by source"

# What design A3 took out of the field: the four cross-source sums, and the
# scope list that only ever described one of them.
REMOVED_KEYS = ("total", "total_configured", "total_configured_scope", "stale_24h", "stale_7d")

STATIC = Path(web.__file__).parent / "dashboard_web_static"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _three_threads_one_honk(root: Path, *, guardian_readable: bool = True) -> None:
    """Three open threads weeks old and one honk raised now, ingested by the
    real sweep. With `guardian_readable` the guardian reads from a fixture file
    (a test never probes the box), so every source is measured; without it the
    guardian is unavailable, which is a measurement failure, not a zero."""
    for n in (1, 2, 3):
        _write_jsonl(
            root / "chronicle" / "open_threads" / f"t{n}.jsonl",
            [{"thread_id": f"t{n}", "resolved": False, "timestamp": "2026-09-01T00:00:00Z"}],
        )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_jsonl(
        root / "nape" / "honks.jsonl",
        [{"honk_id": "h1", "timestamp": now, "level": "sharp", "pattern": "declare_before_verify"}],
    )
    if guardian_readable:
        (root / "guardian").mkdir(parents=True, exist_ok=True)
        (root / "guardian" / "status.json").write_text('{"issues": []}', encoding="utf-8")
    sl.scan_all(root)


def test_a_measured_ledger_reports_each_source_and_no_total(tmp_sovereign_root):
    _three_threads_one_honk(tmp_sovereign_root)
    field = sl.heartbeat_field(tmp_sovereign_root)
    assert field["error"] is None and field["ingestion"] == "ok", "premise: fully measured"
    assert field["by_source"] == dict.fromkeys(sl.SOURCES, 0) | {"thread": 3, "honk": 1}
    # The 24-hour window, by source: the threads are weeks old, the honk is not.
    assert field["stale_24h_by_source"] == dict.fromkeys(sl.SOURCES, 0) | {"thread": 3}
    assert field["note"] == NOTE
    for key in REMOVED_KEYS:
        assert key not in field, f"the field still publishes {key}"


def test_a_blind_field_carries_the_note_and_no_total(tmp_sovereign_root):
    field = sl.heartbeat_field(tmp_sovereign_root)  # nothing has scanned this root
    assert field["measured"] is False and field["error"] == "not_scanned"
    assert field["by_source"] is None and field["stale_24h_by_source"] is None
    assert field["note"] == NOTE
    for key in REMOVED_KEYS:
        assert key not in field, f"the blind shape still carries {key}"


def test_an_unmeasured_source_is_null_in_both_maps_and_the_rest_still_count(tmp_sovereign_root):
    _three_threads_one_honk(tmp_sovereign_root, guardian_readable=False)
    field = sl.heartbeat_field(tmp_sovereign_root)
    assert field["ingestion"] == "degraded" and field["sources_degraded"] == ["guardian"]
    assert field["by_source"]["guardian"] is None
    assert field["stale_24h_by_source"]["guardian"] is None
    assert field["by_source"]["thread"] == 3 and field["by_source"]["honk"] == 1
    assert field["stale_24h_by_source"]["thread"] == 3
    for key in REMOVED_KEYS:
        assert key not in field, f"the degraded shape still carries {key}"


def test_a_config_error_nulls_the_stale_map_with_the_open_map(tmp_sovereign_root):
    """One fold, one null rule: the stale map is read off `by_source_detail`
    after the nulling, so it cannot keep a number the open map gave up."""
    root = tmp_sovereign_root
    sl.source_config_path(root).parent.mkdir(parents=True, exist_ok=True)
    sl.source_config_path(root).write_text("{not json", encoding="utf-8")
    _three_threads_one_honk(root)
    field = sl.heartbeat_field(root)
    assert field["ingestion"] == "config_error"
    assert set(field["by_source"].values()) == {None}
    assert set(field["stale_24h_by_source"].values()) == {None}


def test_signals_summary_reports_by_source_with_the_note_and_no_total(tmp_sovereign_root):
    root = tmp_sovereign_root
    _three_threads_one_honk(root)
    out = json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))
    assert out["ok"] is True and out["error"] is None
    assert out["by_source"]["thread"]["open"] == 3 and out["by_source"]["thread"]["stale_24h"] == 3
    assert out["by_source"]["honk"]["open"] == 1 and out["by_source"]["honk"]["stale_24h"] == 0
    assert out["note"] == NOTE
    for key in REMOVED_KEYS:
        assert key not in out, f"signals_summary still publishes {key}"


def test_the_signals_summary_description_says_by_source_and_never_a_total():
    description = next(t.description for t in sl.SIGNAL_TOOLS if t.name == "signals_summary")
    assert "unacked total" not in description, "it still promises the total it no longer returns"
    assert "counts by source" in description and "never a total" in description


@pytest.fixture
def _console_offline(monkeypatch):
    """build_snapshot()'s connectivity probes and Console-v2 readers kept off
    the machine, the same containment test_dashboard_web.py applies."""
    monkeypatch.setattr(conn, "_launchctl_print_text", lambda label: None)
    monkeypatch.setattr(
        conn,
        "_http_probe",
        lambda url, timeout=2.0: {"http_status": None, "body": "", "error": "mocked"},
    )
    readers.reset_caches()
    monkeypatch.setattr(readers, "fetch_bridge_heartbeat", lambda: None)
    monkeypatch.setattr(readers, "read_guardian", lambda: None)
    yield
    readers.reset_caches()


def test_the_console_builder_takes_the_new_shape_and_shows_the_honk_count(
    tmp_sovereign_root, monkeypatch, _console_offline
):
    root = tmp_sovereign_root
    _three_threads_one_honk(root)
    # build_snapshot() calls heartbeat_field() with no root. Aim that default at
    # this test's root, not the suite's shared sink, so the count it reads is
    # the ledger written above.
    monkeypatch.setattr(sl, "default_sovereign_root", lambda: root)
    snapshot = web.build_snapshot()
    signals = snapshot["unacked_signals"]
    assert signals["by_source"]["honk"] == 1 and signals["by_source"]["thread"] == 3
    assert signals["note"] == NOTE
    for key in REMOVED_KEYS:
        assert key not in signals, f"the snapshot still carries {key}"
    assert snapshot["honk_counts"]["ledger_open"] == 1


def test_the_attention_strip_renders_no_cross_source_sum():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "u.total" not in js, "the strip still reads a total"
    assert "u.stale_7d" not in js, "the strip still reads the cross-source week count"
    assert "signals, all sources" not in js
    # ...and it still renders each source, a null as a word rather than a 0.
    assert "u.by_source_detail" in js
