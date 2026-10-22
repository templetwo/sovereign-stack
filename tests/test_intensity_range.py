"""record_insight bounds intensity to [0.0, 1.0] (design C4, plan 1 Task 4).

intensity is the field that drives surfacing: the boot's persistent markers are
the entries at or above 0.9, and recall filters on min_intensity. Nothing
checked its range, and five April rows above 1 sit in the record (left there as
history; nothing here rewrites a stored row).

The check sits beside the emotional_intensity check. It differs in two ways:

  * the value must already be a number. "0.5" is refused, not coerced: every
    reader compares intensity as a number, and no caller in the record has ever
    sent a string (live census 2026-10-22: 4,767 floats, 8 ints, 0 strings).
  * a bool is refused. True would otherwise pass as 1.0, the strongest marker
    the record has, from a type slip.

An in-range value is stored exactly as given, so an int keeps the bytes it has
today. emotional_intensity is untouched.

Hermetic: every chronicle lives under tmp_path or the isolated server's tmp root.
"""

import asyncio
import json
import math

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.provenance import iter_chronicle_entries
from tests.test_nape_autohook import _isolated_server

MESSAGE = "intensity must be between 0.0 and 1.0"


@pytest.fixture
def mem(tmp_path):
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def _written(mem) -> list[dict]:
    return [
        entry for entry, _file, where in iter_chronicle_entries(mem.root) if where == "insights"
    ]


@pytest.mark.parametrize(
    "value",
    [1.5, -0.1, "3", "0.5", True, False, None, math.nan, math.inf, -math.inf, 2],
    ids=repr,
)
def test_out_of_range_or_non_numeric_intensity_is_refused_and_nothing_is_written(value, mem):
    with pytest.raises(ValueError) as refused:
        mem.record_insight("calibration", "an insight", intensity=value)
    assert str(refused.value) == MESSAGE
    assert _written(mem) == []


@pytest.mark.parametrize("value", [0.0, 1.0, 0.9, 0, 1, 0.5], ids=repr)
def test_in_range_intensity_is_accepted_and_stored_as_given(value, mem):
    mem.record_insight("calibration", "an insight", intensity=value)
    [entry] = _written(mem)
    assert entry["intensity"] == value
    assert type(entry["intensity"]) is type(value), "an in-range value is stored unconverted"


def test_the_default_intensity_still_records(mem):
    mem.record_insight("calibration", "an insight with the default")
    [entry] = _written(mem)
    assert entry["intensity"] == 0.5


class TestEmotionalIntensityUnchanged:
    """The emotional check keeps every behaviour it had, coercion included."""

    def test_out_of_range_still_refused_with_its_own_message(self, mem):
        with pytest.raises(ValueError, match=r"emotional_intensity must be in \[0\.0, 1\.0\]"):
            mem.record_insight("memory", "x", emotional_intensity=1.5)
        assert _written(mem) == []

    def test_a_non_number_still_refused_with_its_own_message(self, mem):
        with pytest.raises(ValueError, match="emotional_intensity must be a number 0.0-1.0"):
            mem.record_insight("memory", "x", emotional_intensity="heavy")

    def test_a_numeric_string_is_still_coerced_and_stored_as_a_float(self, mem):
        mem.record_insight("memory", "x", emotional_intensity="0.9")
        [entry] = _written(mem)
        assert entry["emotional_intensity"] == 0.9
        assert isinstance(entry["emotional_intensity"], float)

    def test_an_emotional_weight_above_the_operational_one_is_still_fine(self, mem):
        mem.record_insight("memory", "x", intensity=0.2, emotional_intensity=1.0)
        [entry] = _written(mem)
        assert (entry["intensity"], entry["emotional_intensity"]) == (0.2, 1.0)


def test_the_wire_refuses_out_of_range_intensity_as_an_error():
    """Through the MCP dispatch: a raise (isError on the wire), never ok-shaped text."""
    with _isolated_server("intensity-range-test") as (srv, tmp_root):
        args = {"domain": "calibration", "content": "an insight", "intensity": 1.5}
        with pytest.raises(ValueError, match=f"record_insight rejected: {MESSAGE}"):
            asyncio.run(srv._dispatch_tool("record_insight", args))
        assert not list((tmp_root / "chronicle" / "insights").rglob("*.jsonl"))

        args["intensity"] = 0.9
        result = asyncio.run(srv._dispatch_tool("record_insight", args))
        assert "Insight recorded" in result[0].text
        [path] = (tmp_root / "chronicle" / "insights").rglob("*.jsonl")
        assert json.loads(path.read_text().splitlines()[-1])["intensity"] == 0.9
