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

The shape is pinned whole (FIELD_KEYS, SUMMARY_KEYS), so no sum comes back
under a new name, `by_source_sum` included. The removal drops a headline and no
information: each dropped number is rebuilt here from the per-source values the
field still publishes. And the console names the thread count for what it is,
the open threads, one `open_thread` signal each.

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
# scope list that only ever described one of them. `by_source_sum` is the
# labelled sum the plan's Files line named (plan :205). It was never built: the
# spec's A3 line says "drop the headline total", and the Interfaces line (plan
# :209) gives the shape with no sum. It is pinned absent with the rest.
REMOVED_KEYS = (
    "total",
    "total_configured",
    "total_configured_scope",
    "stale_24h",
    "stale_7d",
    "by_source_sum",
)

# THE PUBLISHED SHAPE, WHOLE. Every branch of heartbeat_field (measured,
# degraded, config error, never scanned) carries exactly these keys, so a sum
# cannot return under any name without this set changing with it.
FIELD_KEYS = frozenset(
    {
        "error",
        "ingestion",
        "scanned_at",
        "refresh_started_at",
        "partial",
        "unreached",
        "measured",
        "not_configured",
        "by_source",
        "stale_24h_by_source",
        "note",
        "by_source_detail",
        "corrupt_rows",
        "source_status",
        "sources_degraded",
    }
)

# signals_summary's summary is the field itself, its nested detail published
# under `by_source`, so it carries the same envelope and no sum either.
SUMMARY_KEYS = frozenset(
    {
        "ok",
        "mode",
        "error",
        "ingestion",
        "scanned_at",
        "refresh_started_at",
        "partial",
        "unreached",
        "not_configured",
        "by_source",
        "note",
        "corrupt_rows",
        "source_status",
        "sources_degraded",
    }
)

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
    assert set(field) == FIELD_KEYS


def test_a_blind_field_carries_the_note_and_no_total(tmp_sovereign_root):
    field = sl.heartbeat_field(tmp_sovereign_root)  # nothing has scanned this root
    assert field["measured"] is False and field["error"] == "not_scanned"
    assert field["by_source"] is None and field["stale_24h_by_source"] is None
    assert field["note"] == NOTE
    for key in REMOVED_KEYS:
        assert key not in field, f"the blind shape still carries {key}"
    assert set(field) == FIELD_KEYS


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
    assert set(field) == FIELD_KEYS


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
    assert set(field) == FIELD_KEYS


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
    assert set(out) == SUMMARY_KEYS


def test_the_signals_summary_description_says_by_source_and_never_a_total():
    description = next(t.description for t in sl.SIGNAL_TOOLS if t.name == "signals_summary")
    assert "unacked total" not in description, "it still promises the total it no longer returns"
    assert "counts by source" in description and "never a total" in description


def _declare(root: Path, sources: dict[str, str]) -> None:
    path = sl.source_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sources": sources}), encoding="utf-8")


def test_each_dropped_number_is_rebuilt_from_what_the_field_still_publishes(tmp_sovereign_root):
    """A3 drops a headline, not information. summarize() is the unchanged
    internal fold the old sums came from (its count loop and its configured
    scope); on a fully measured root each of them is a sum of per-source values
    the field still publishes. On a blind or partial read those per-source
    values are null, which is where the old sums were null too."""
    root = tmp_sovereign_root
    _three_threads_one_honk(root)
    fold, field = sl.summarize(root), sl.heartbeat_field(root)
    assert field["error"] is None and field["not_configured"] == [], "premise: fully measured"
    assert fold["total"] == sum(field["by_source"].values()) == 4
    # Nothing is declared out of scope, so the configured-scope sum IS the
    # total: keeping it would have published the headline under another name.
    assert fold["total_configured"] == fold["total"]
    assert fold["total_configured_scope"] == list(sl.SOURCES)
    assert fold["stale_24h"] == sum(field["stale_24h_by_source"].values()) == 3
    assert fold["stale_7d"] == sum(d["stale_7d"] for d in field["by_source_detail"].values()) == 3


def test_a_declared_source_leaves_the_configured_sum_rebuildable_from_its_scope(
    tmp_sovereign_root,
):
    """With a source declared out of scope, the narrower number the field
    used to carry is the sum of `by_source` over every source not in
    `not_configured`, and the declared source itself is null, never 0."""
    root = tmp_sovereign_root
    _declare(root, {"guardian": "not_configured"})
    _three_threads_one_honk(root)
    fold, field = sl.summarize(root), sl.heartbeat_field(root)
    assert field["not_configured"] == ["guardian"], "premise: one source declared out"
    scope = [s for s in sl.SOURCES if s not in field["not_configured"]]
    assert fold["total_configured_scope"] == scope
    assert fold["total_configured"] == sum(field["by_source"][s] for s in scope) == 4
    # The all-sources sum stays unknown, as before A3: a declared source is out
    # of scope, not measured.
    assert fold["total"] is None and field["by_source"]["guardian"] is None


def test_every_thread_signal_is_an_open_thread_row(tmp_sovereign_root):
    """The row-level half of A3's label. The scanner opens one signal per
    open thread and stamps it kind "open_thread", so `by_source["thread"]`
    counts open-thread rows and nothing else."""
    root = tmp_sovereign_root
    _three_threads_one_honk(root)
    listed = json.loads(
        sl.handle_signal_tool("signals_summary", {"mode": "list", "source": "thread"}, root=root)
    )
    assert listed["ok"] is True
    assert [row["kind"] for row in listed["signals"]] == ["open_thread"] * 3
    assert sl.heartbeat_field(root)["by_source"]["thread"] == len(listed["signals"])


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
    assert set(signals) == FIELD_KEYS
    assert snapshot["honk_counts"]["ledger_open"] == 1


def test_the_attention_strip_renders_no_cross_source_sum():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "u.total" not in js, "the strip still reads a total"
    assert "u.stale_7d" not in js, "the strip still reads the cross-source week count"
    assert "signals, all sources" not in js
    # ...and it still renders each source, a null as a word rather than a 0.
    assert "u.by_source_detail" in js


def test_the_attention_strip_labels_the_thread_count_as_open_threads():
    """Spec A3: "the thread backlog is labelled as the open-thread count it
    is". The strip is where a person reads that count, so it says open
    threads, not threads."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "['thread', 'open threads']" in js
    assert "['thread', 'threads']" not in js
