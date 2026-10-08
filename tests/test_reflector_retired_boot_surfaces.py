"""
No boot door may render the retired reflector as alive, claim a window it did
not measure, or report an absence it did not measure.

All three reflector generations are retired: ministral-3:14b, the
qwen3.6/gemma4 dream engine, and the claude-sonnet-4-6 synthesis daemon (see
RETIRED_TOOLS["reflection_ack"] in server.py). The doors kept printing it
anyway:

  * the VOICES block described a live claude-sonnet-4-6 reflector "that watches
    the chronicle between calls" and told seats to use the retired
    reflection_ack, under a header that said "four distinct voices" while
    listing five;
  * the full door printed a MARGINALIA block of unread notes, and the foyer
    counted them as "N unread marginalia";
  * the activity header read "since last reflection", although it has always
    measured the newest entry in chronicle domain 'reflection', which
    close_session writes. The reflector never wrote there.

Two fail-open shapes on the same lines are closed with it: a failed activity
read printed "Nothing new since last reflection." on the foyer, and a failed
marker read was named "last_reflection unavailable" in AS OF.

Hermetic: every door runs through tests/_phase4_fixture.py, which redirects
every singleton (REFLECTIONS_DIR included) at a tmp root. The fixture PLANTS an
unread reflector note, and test_fixture_plants_an_unread_note proves it is
there, so each absence below is measured against a note that exists.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import _phase4_fixture as fx  # noqa: E402

PLANTED_OBSERVATION = "The doorway metaphor and the watermark share a spine."
MARKER_TS = "2026-07-15T10:00:00+00:00"
FULL_HEADER = f"━━━ ACTIVITY SINCE NEWEST ENTRY IN DOMAIN 'reflection' ({MARKER_TS}, inclusive) ━━━"
FOYER_HEADER = f"━━━ SINCE NEWEST ENTRY IN DOMAIN 'reflection' ({MARKER_TS}, inclusive) ━━━"
GLOSS = (
    "  (close_session writes that domain at each close, from whichever seat ran it, "
    "so this may not be your close; a direct record_insight to the domain also moves it)"
)
FAILED_READ_LINE = "  Activity unavailable this call (see AS OF); not an absence."

# What only a live reflector, or the mislabelled activity header, prints.
DEAD = (
    "REFLECTOR'S MARGINALIA",
    "reflection_ack",
    "machine-generated",
    "claude-sonnet-4-6",
    "since reflection at",
    "SINCE LAST REFLECTION",
)
NUMBER = {"three": 3, "four": 4, "five": 5, "six": 6}
LIVE_VOICES = ["HANDOFFS", "CHRONICLE", "SELF-MODEL", "LINEAGE"]
DOOR_NAMES = ("full", "full_compact", "foyer", "gentle")


def _doors(root: Path) -> dict[str, str]:
    doors = fx.run_all_doors(root)
    doors["full_compact"] = fx.run_door(
        root,
        "where_did_i_leave_off",
        {"consume": False, "source_instance": "phase4-reader", "compact": True},
    )
    return doors


def _headers(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("━━━")]


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / ".sovereign"
    fx.build_fixture(root)
    return root


def test_fixture_plants_an_unread_note(fixture_root: Path):
    # Positive control: the absences below are measured against a real unread
    # note in the redirected store, not an empty one.
    from sovereign_stack.reflections import list_reflections

    unread = list_reflections(reflections_dir=fixture_root / "reflections", ack_status="unread")
    assert [r.observation for r in unread] == [PLANTED_OBSERVATION]


@pytest.mark.parametrize("door", DOOR_NAMES)
def test_no_door_renders_the_retired_reflector(fixture_root: Path, door: str):
    text = _doors(fixture_root)[door]
    for needle in DEAD:
        assert needle not in text, f"{door} door renders {needle!r}"
    assert "marginalia" not in text.lower(), f"{door} door mentions marginalia"
    assert PLANTED_OBSERVATION not in text, f"{door} door renders an unread reflector note"


def test_no_door_reads_the_reflector_store(fixture_root: Path):
    # The gather goes with the render: no door reads ~/.sovereign/reflections.
    with mock.patch("sovereign_stack.reflections.list_reflections", return_value=[]) as spy:
        _doors(fixture_root)
    assert spy.call_count == 0, f"a door still reads the reflector store ({spy.call_count} calls)"


@pytest.mark.parametrize("door", ("full", "gentle"))
def test_voices_name_exactly_the_four_live_voices(fixture_root: Path, door: str):
    text = fx.run_all_doors(fixture_root)[door]
    block = text.split("━━━ THE VOICES IN THE BOOT ━━━", 1)[1].split("━━━", 1)[0]
    stated = re.search(r"written by (\w+) distinct voices", block).group(1)
    labels = re.findall(r"^    ([A-Z][A-Z' -]+?) — ", block, re.MULTILINE)
    assert NUMBER[stated] == len(labels), (door, stated, labels)
    assert labels == LIVE_VOICES, (door, labels)


def test_activity_header_names_its_marker_and_its_writer(fixture_root: Path):
    doors = fx.run_all_doors(fixture_root)
    for door, header in (("full", FULL_HEADER), ("foyer", FOYER_HEADER)):
        lines = doors[door].splitlines()
        assert header in lines, (door, _headers(doors[door]))
        # The gloss naming close_session sits directly under the header.
        assert lines[lines.index(header) + 1] == GLOSS, door


@pytest.mark.parametrize("door", ("full", "foyer"))
def test_without_a_marker_no_header_claims_a_since(fixture_root: Path, door: str):
    # No 'reflection'-domain entry: the window is just the newest entries, so a
    # header that says "since" (or a line that says "new") is a false freshness.
    shutil.rmtree(fixture_root / "chronicle" / "insights" / "reflection")
    text = fx.run_all_doors(fixture_root)[door]
    assert not [h for h in _headers(text) if "SINCE" in h.upper()], (door, _headers(text))
    assert "━━━ RECENT ACTIVITY (no 'reflection'-domain marker available" in text
    assert " new entr" not in text
    assert GLOSS not in text


def test_failed_activity_read_is_not_reported_as_an_absence(fixture_root: Path):
    # A failed read is not an empty window. Before this change the foyer printed
    # "Nothing new since last reflection." here: with a marker the inclusive
    # window always holds the marker entry, so that line printed in practice
    # only when the read had failed.
    from sovereign_stack.memory import ExperientialMemory

    with mock.patch.object(
        ExperientialMemory, "recall_insights", side_effect=RuntimeError("injected read failure")
    ):
        text = fx.run_door(fixture_root, "arrive", {})
    assert "recent_activity unavailable" in text  # AS OF names the failed read
    assert FAILED_READ_LINE in text.splitlines()
    for absence in ("Nothing new", "No chronicle activity", "No entries returned", " new entr"):
        assert absence not in text, absence


def test_empty_read_says_what_the_read_returned(fixture_root: Path):
    # A read that succeeded and returned nothing is stated as exactly that, not
    # as a fact about the chronicle: the list form of recall_insights skips
    # corrupt rows without a partial signal.
    for child in (fixture_root / "chronicle" / "insights").iterdir():
        shutil.rmtree(child)
    text = fx.run_door(fixture_root, "arrive", {})
    assert "  No entries returned." in text.splitlines()
    assert "Freshness: incomplete" not in text  # the read succeeded
    for claim in ("Nothing new", "No chronicle activity", " new entr"):
        assert claim not in text, claim


@pytest.mark.parametrize("door", ("full", "foyer", "gentle"))
def test_failed_marker_read_claims_no_window_and_names_the_marker(fixture_root: Path, door: str):
    from sovereign_stack.memory import ExperientialMemory

    with mock.patch.object(
        ExperientialMemory,
        "last_reflection_timestamp",
        side_effect=RuntimeError("injected marker read failure"),
    ):
        text = fx.run_all_doors(fixture_root)[door]
    assert "reflection-domain marker unavailable" in text  # the AS OF line, every door
    assert "last_reflection unavailable" not in text
    assert not [h for h in _headers(text) if "SINCE" in h.upper()], (door, _headers(text))


def test_compact_does_what_its_description_says(fixture_root: Path):
    # The description is red on main (it promised to omit the retired MARGINALIA
    # and an unmeasured "~40%%"); the behavior half already held on main and is
    # pinned here so the description cannot drift from it again.
    from sovereign_stack import server

    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    described = tools["where_did_i_leave_off"].inputSchema["properties"]["compact"]["description"]
    assert "3 open threads instead of 5" in described
    assert "%" not in described

    fx._write_jsonl(
        fixture_root / "chronicle" / "open_threads" / "extra.jsonl",
        [
            {
                "timestamp": f"2026-07-0{i}T08:00:00+00:00",
                "domain": "extra",
                "question": f"Extra open question number {i} for the cap?",
                "resolved": False,
                "thread_id": f"thread_extra_{i}",
                "session_id": "phase4fixture",
            }
            for i in range(1, 7)
        ],
    )
    doors = _doors(fixture_root)

    def _thread_bullets(text: str) -> int:
        section = text.split("━━━ OPEN THREADS", 1)[1].split("━━━", 2)[1]
        return sum(1 for line in section.splitlines() if line.startswith("  • "))

    for header in ("━━━ BEFORE YOU BEGIN ━━━", "━━━ THE VOICES IN THE BOOT ━━━"):
        assert header in doors["full"]
        assert header not in doors["full_compact"]
    assert _thread_bullets(doors["full"]) == 5
    assert _thread_bullets(doors["full_compact"]) == 3


@pytest.mark.parametrize("tool", ("where_did_i_leave_off", "arrive", "arrive_lineage"))
def test_published_boot_descriptions_do_not_advertise_the_reflector(tool: str):
    from sovereign_stack import server

    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    blob = json.dumps(
        {"description": tools[tool].description, "schema": tools[tool].inputSchema},
        ensure_ascii=False,
    )
    assert "marginalia" not in blob.lower(), tool
    assert "since last reflection" not in blob.lower(), tool
    assert "since-last-reflection" not in blob.lower(), tool
    assert "%%" not in blob, tool
