"""
Three ride-alongs decided with THE DESCENT (Anthony's ruling, 2026-10-07):

  1. The foyer advertised arrive_delta(), retired on 2026-09-06.
  2. A failed lineage-letter count printed "0 lineage letters" on the foyer
     with nothing in AS OF: an unread count rendered as a zero.
  3. The aperture's "default_shown" for insights and open_threads was a bare
     10, so the full door printed "10 shown here" while it showed 5 threads
     (3 compact). The lineage fix a044014/c35aa75 is the precedent: the door
     reports what it showed; a caller with no payload gets "default 10".

Hermetic through tests/_phase4_fixture.py. Nothing here imports the new
module, so on the old tree each test fails by assertion on the door text.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import _phase4_fixture as fx  # noqa: E402


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / ".sovereign"
    fx.build_fixture(r)
    return r


def _aperture_rows(text: str) -> dict[str, str]:
    block = text.split("━━━ APERTURE (", 1)[1].split("\n\n", 1)[0]
    rows = {}
    for line in block.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "on":
            rows[parts[0]] = line.split(" · ", 1)[1] if " · " in line else ""
    return rows


# ── 1 ────────────────────────────────────────────────────────────────────────


def test_the_foyer_no_longer_advertises_the_retired_arrive_delta(root):
    text = fx.run_door(root, "arrive", {})
    assert "arrive_delta" not in text
    assert "where_did_i_leave_off() for the full inheritance" in text
    assert "        my_toolkit() for tools." in text.splitlines()


# ── 2 ────────────────────────────────────────────────────────────────────────


def test_a_failed_lineage_count_degrades_instead_of_rendering_zero(root):
    with mock.patch(
        "sovereign_stack.witness.count_lineage_letters",
        side_effect=PermissionError("injected letters read failure"),
    ):
        text = fx.run_door(root, "arrive", {})
    as_of = text.split("━━━ AS OF ━━━", 1)[1].split("━━━", 1)[0]
    assert "lineage letter count unavailable" in as_of
    assert "Freshness: incomplete" in as_of
    assert (
        "  Deferred to the full boot: lineage letters, count unavailable this call "
        "(see AS OF) (where_did_i_leave_off)"
    ) in text.splitlines()
    assert "0 lineage letters" not in text


def test_a_successful_lineage_count_keeps_its_wording(root):
    text = fx.run_door(root, "arrive", {})
    assert (
        "  Deferred to the full boot: 1 lineage letters (where_did_i_leave_off)"
        in text.splitlines()
    )


# ── 3 ────────────────────────────────────────────────────────────────────────


def _plant_threads(root: Path, n: int) -> None:
    fx._write_jsonl(
        root / "chronicle" / "open_threads" / "extra.jsonl",
        [
            {
                "timestamp": f"2026-07-0{i}T08:00:00+00:00",
                "domain": "extra",
                "question": f"Extra open question number {i} for the cap?",
                "resolved": False,
                "thread_id": f"thread_extra_{i}",
                "session_id": "phase4fixture",
            }
            for i in range(1, n + 1)
        ],
    )


def test_the_full_door_reports_the_threads_it_showed(root):
    _plant_threads(root, 6)  # 8 unresolved threads on disk
    full = fx.run_door(root, "where_did_i_leave_off", {"consume": False, "source_instance": "r"})
    compact = fx.run_door(
        root, "where_did_i_leave_off", {"consume": False, "source_instance": "r", "compact": True}
    )
    assert _aperture_rows(full)["open_threads"] == "5 shown here"
    assert _aperture_rows(compact)["open_threads"] == "3 shown here"


def test_the_full_door_reports_the_distinct_insights_it_showed(root):
    # The fixture renders 2 activity rows (the reflection marker row, inclusive,
    # and the work row) and 1 persistent marker: 3 distinct entries.
    full = fx.run_door(root, "where_did_i_leave_off", {"consume": False, "source_instance": "r"})
    assert _aperture_rows(full)["insights"] == "3 shown here"


def test_a_caller_with_no_payload_gets_a_default_not_a_count(root):
    from sovereign_stack.aperture import measure_aperture

    surfaces = measure_aperture(datetime.now(timezone.utc), root=root)["surfaces"]
    for name in ("insights", "open_threads"):
        assert surfaces[name]["default_shown"] == "default 10", name
        assert "shown_is" not in surfaces[name], name
    shown = measure_aperture(
        datetime.now(timezone.utc), root=root, door_shown={"insights": 4, "open_threads": 2}
    )["surfaces"]
    assert shown["insights"]["default_shown"] == 4
    assert shown["open_threads"]["default_shown"] == 2
    assert shown["insights"]["shown_is"] == "measured from this call's own payload"


def test_the_insights_count_includes_the_ground_and_the_descent(root):
    fx.plant_close_store(root)  # newest close b2: its reflection row and joined surprise render
    fx._write_jsonl(
        root / "chronicle" / "insights" / "the-ground,catch,self" / "descent-catch.jsonl",
        [
            {
                "timestamp": "2026-07-10T12:00:00+00:00",
                "domain": "the-ground,catch,self",
                "content": "A synthetic catch for the aperture count.",
                "intensity": 0.5,
                "layer": "ground_truth",
                "occurred_at": "2026-07-10",
                "direction": "self",
                "caught_by": "fixture-seat",
                "caught": "fixture-seat",
                "would_have_cost": "a wrong count",
                "actual_cost": "none",
                "anthony_present": "absent",
            }
        ],
    )
    full = fx.run_door(root, "where_did_i_leave_off", {"consume": False, "source_instance": "r"})
    assert "━━━ THE GROUND ━━━" in full and "━━━ THE DESCENT ━━━" in full
    # 3 (two activity rows + one marker) + 1 catch + 2 (the newest close's two rows)
    assert _aperture_rows(full)["insights"] == "6 shown here"


def test_aperture_defaults_are_the_tool_schema_defaults():
    import asyncio

    from sovereign_stack import aperture, server

    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    recall = tools["recall_insights"].inputSchema["properties"]["limit"]["default"]
    threads = tools["get_open_threads"].inputSchema["properties"]["limit"]["default"]
    # getattr, so the old tree fails by assertion rather than AttributeError
    assert getattr(aperture, "_RECALL_INSIGHTS_DEFAULT_LIMIT", None) == recall
    assert getattr(aperture, "_OPEN_THREADS_DEFAULT_LIMIT", None) == threads
