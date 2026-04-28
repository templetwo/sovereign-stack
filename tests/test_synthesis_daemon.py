"""
Tests for the synthesis daemon (the local-LLM reflector).

Coverage focus:
  * Chronicle reading (time window, max_entries cap, malformed lines tolerated)
  * JSON extraction from raw model output (handles ANSI escapes, thinking
    traces, markdown code fences, stray prose)
  * Reflection parsing + schema validation (drops malformed entries silently,
    coerces invalid connection_type / confidence to safe defaults)
  * Persistence to JSONL (ack_status field present, run_id propagated, append
    mode preserves prior content)
  * Prompt assembly (entries serialized, long bodies trimmed)

Network-touching paths (call_ollama) are NOT exercised here — they're a
subprocess/HTTP boundary, tested manually at the fireside on 2026-04-26.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack.daemons.synthesis_daemon import (
    Reflection,
    _recover_complete_reflections,
    build_prompt,
    extract_json_block,
    parse_reflections,
    read_recent_chronicle,
    write_reflections,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def chronicle_root(tmp_path: Path) -> Path:
    """An empty chronicle root."""
    root = tmp_path / "chronicle" / "insights"
    root.mkdir(parents=True)
    return root


def _write_entry(
    root: Path,
    domain: str,
    timestamp: datetime,
    content: str,
    layer: str = "hypothesis",
    session_id: str = "test-session",
) -> Path:
    """Helper: write one chronicle entry under root/<domain>/test.jsonl."""
    domain_dir = root / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    path = domain_dir / "test.jsonl"
    record = {
        "timestamp": timestamp.isoformat(),
        "domain": domain,
        "content": content,
        "layer": layer,
        "session_id": session_id,
    }
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return path


# ── read_recent_chronicle ───────────────────────────────────────────────────


class TestReadRecentChronicle:
    def test_empty_root_returns_empty(self, tmp_path: Path):
        # Non-existent chronicle root should be handled gracefully.
        missing = tmp_path / "nope"
        assert read_recent_chronicle(chronicle_root=missing) == []

    def test_recent_entry_surfaces(self, chronicle_root: Path):
        now = datetime.now(timezone.utc)
        _write_entry(chronicle_root, "test-domain", now, "fresh content")
        entries = read_recent_chronicle(chronicle_root=chronicle_root)
        assert len(entries) == 1
        assert entries[0]["content"] == "fresh content"
        assert entries[0]["domain"] == "test-domain"

    def test_old_entry_filtered(self, chronicle_root: Path):
        # Entries older than the recent_hours window must NOT surface.
        old = datetime.now(timezone.utc) - timedelta(hours=72)
        _write_entry(chronicle_root, "old-domain", old, "stale")
        entries = read_recent_chronicle(
            chronicle_root=chronicle_root, recent_hours=24
        )
        assert entries == []

    def test_max_entries_cap_respected(self, chronicle_root: Path):
        now = datetime.now(timezone.utc)
        for i in range(15):
            _write_entry(
                chronicle_root,
                f"d{i}",
                now - timedelta(minutes=i),
                f"content {i}",
            )
        entries = read_recent_chronicle(
            chronicle_root=chronicle_root, max_entries=5
        )
        assert len(entries) == 5

    def test_newest_first(self, chronicle_root: Path):
        now = datetime.now(timezone.utc)
        _write_entry(chronicle_root, "d-old", now - timedelta(hours=10), "older")
        _write_entry(chronicle_root, "d-new", now - timedelta(minutes=5), "newer")
        entries = read_recent_chronicle(chronicle_root=chronicle_root)
        assert entries[0]["content"] == "newer"
        assert entries[1]["content"] == "older"

    def test_malformed_jsonl_lines_skipped(self, chronicle_root: Path):
        domain_dir = chronicle_root / "d"
        domain_dir.mkdir()
        path = domain_dir / "test.jsonl"
        now = datetime.now(timezone.utc).isoformat()
        path.write_text(
            "not valid json\n"
            + json.dumps({"timestamp": now, "domain": "d", "content": "good"})
            + "\n"
            + "{garbage\n"
        )
        entries = read_recent_chronicle(chronicle_root=chronicle_root)
        assert len(entries) == 1
        assert entries[0]["content"] == "good"

    def test_missing_timestamp_skipped(self, chronicle_root: Path):
        domain_dir = chronicle_root / "d"
        domain_dir.mkdir()
        path = domain_dir / "test.jsonl"
        path.write_text(json.dumps({"domain": "d", "content": "no ts"}) + "\n")
        entries = read_recent_chronicle(chronicle_root=chronicle_root)
        assert entries == []


# ── extract_json_block ──────────────────────────────────────────────────────


class TestExtractJsonBlock:
    def test_plain_json(self):
        raw = '{"reflections": []}'
        assert extract_json_block(raw) == raw

    def test_strips_thinking_trailer(self):
        raw = "Some thinking here.\n...done thinking.\n\n{\"reflections\": []}"
        assert extract_json_block(raw) == '{"reflections": []}'

    def test_strips_markdown_fences(self):
        raw = '```json\n{"reflections": []}\n```'
        assert extract_json_block(raw) == '{"reflections": []}'

    def test_strips_ansi_escapes(self):
        # Real-world failure mode: Ollama CLI streaming leaks cursor codes.
        raw = '\x1b[6D\x1b[K{"reflections": []}\x1b[0m'
        assert extract_json_block(raw) == '{"reflections": []}'

    def test_returns_none_when_no_json(self):
        assert extract_json_block("just prose, no braces") is None

    def test_returns_none_when_empty(self):
        assert extract_json_block("") is None

    def test_finds_json_amid_prose(self):
        raw = "Here is what I think: {\"reflections\": [1]} that is all."
        assert extract_json_block(raw) == '{"reflections": [1]}'


# ── parse_reflections ───────────────────────────────────────────────────────


class TestParseReflections:
    def test_valid_payload(self):
        raw = json.dumps(
            {
                "reflections": [
                    {
                        "observation": "test obs",
                        "entries_referenced": ["entry_1"],
                        "connection_type": "convergence",
                        "confidence": "medium",
                    }
                ]
            }
        )
        result = parse_reflections(raw)
        assert len(result) == 1
        assert result[0].observation == "test obs"
        assert result[0].connection_type == "convergence"
        assert result[0].confidence == "medium"

    def test_invalid_connection_type_coerced_to_other(self):
        raw = json.dumps(
            {
                "reflections": [
                    {
                        "observation": "x",
                        "entries_referenced": [],
                        "connection_type": "totally_made_up",
                        "confidence": "low",
                    }
                ]
            }
        )
        result = parse_reflections(raw)
        assert result[0].connection_type == "other"

    def test_invalid_confidence_coerced_to_low(self):
        raw = json.dumps(
            {
                "reflections": [
                    {
                        "observation": "x",
                        "entries_referenced": [],
                        "connection_type": "other",
                        "confidence": "stratospheric",
                    }
                ]
            }
        )
        result = parse_reflections(raw)
        assert result[0].confidence == "low"

    def test_empty_observation_dropped(self):
        raw = json.dumps(
            {
                "reflections": [
                    {
                        "observation": "",
                        "entries_referenced": [],
                        "connection_type": "other",
                        "confidence": "low",
                    },
                    {
                        "observation": "real obs",
                        "entries_referenced": [],
                        "connection_type": "other",
                        "confidence": "low",
                    },
                ]
            }
        )
        result = parse_reflections(raw)
        assert len(result) == 1
        assert result[0].observation == "real obs"

    def test_non_dict_items_dropped(self):
        raw = json.dumps({"reflections": ["not a dict", 42, None]})
        assert parse_reflections(raw) == []

    def test_unparseable_returns_empty(self):
        assert parse_reflections("garbage {{{") == []

    def test_entries_referenced_truncated_per_item(self):
        long_ref = "x" * 200
        raw = json.dumps(
            {
                "reflections": [
                    {
                        "observation": "x",
                        "entries_referenced": [long_ref],
                        "connection_type": "other",
                        "confidence": "low",
                    }
                ]
            }
        )
        result = parse_reflections(raw)
        assert len(result[0].entries_referenced[0]) == 80

    def test_caps_entries_referenced_count(self):
        many = [f"e{i}" for i in range(20)]
        raw = json.dumps(
            {
                "reflections": [
                    {
                        "observation": "x",
                        "entries_referenced": many,
                        "connection_type": "other",
                        "confidence": "low",
                    }
                ]
            }
        )
        result = parse_reflections(raw)
        assert len(result[0].entries_referenced) == 10


# ── write_reflections ───────────────────────────────────────────────────────


class TestWriteReflections:
    def test_writes_with_metadata(self, tmp_path: Path):
        refs = [
            Reflection(
                observation="test obs",
                entries_referenced=["e1"],
                connection_type="convergence",
                confidence="medium",
            )
        ]
        path = write_reflections(
            refs,
            run_id="test-run",
            model="test-model",
            prompt_version="v-test",
            entries_window_hours=24,
            entries_count=3,
            out_dir=tmp_path,
        )
        assert path.exists()
        line = path.read_text().strip()
        record = json.loads(line)
        # Required metadata fields.
        assert record["model"] == "test-model"
        assert record["prompt_version"] == "v-test"
        assert record["run_id"] == "test-run"
        assert record["entries_window_hours"] == 24
        assert record["entries_count"] == 3
        # Reflection fields.
        assert record["observation"] == "test obs"
        assert record["connection_type"] == "convergence"
        # Ack-loop default state.
        assert record["ack_status"] == "unread"
        # Generated id present.
        assert record["id"].startswith("reflection_test-run_")

    def test_append_mode_preserves_prior(self, tmp_path: Path):
        r1 = [
            Reflection(
                observation="first",
                entries_referenced=[],
                connection_type="other",
                confidence="low",
            )
        ]
        r2 = [
            Reflection(
                observation="second",
                entries_referenced=[],
                connection_type="other",
                confidence="low",
            )
        ]
        write_reflections(
            r1,
            run_id="a",
            model="m",
            prompt_version="v",
            entries_window_hours=1,
            entries_count=1,
            out_dir=tmp_path,
        )
        write_reflections(
            r2,
            run_id="b",
            model="m",
            prompt_version="v",
            entries_window_hours=1,
            entries_count=1,
            out_dir=tmp_path,
        )
        # Find the date-named file. Both writes hit the same date.
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["observation"] == "first"
        assert json.loads(lines[1])["observation"] == "second"


# ── build_prompt ────────────────────────────────────────────────────────────


class TestRecoverCompleteReflections:
    """Tests for the truncation-tolerant fallback parser. The function
    walks JSON character-by-character with a string-literal-aware brace
    counter, capturing each complete `{...}` object inside the top-level
    `"reflections":[...]` array and dropping incomplete tails. It exists
    because ministral-3:14b sometimes hits a token ceiling mid-third
    reflection — without this salvage, all 3 reflections would be lost
    on JSONDecodeError. With it, the first 2 (or 1) survive."""

    def test_three_complete_reflections_recovered(self):
        # Synthetically valid block — 3 complete objects, then closed array.
        block = (
            '{"reflections": ['
            '{"observation": "first", "connection_type": "convergence"},'
            '{"observation": "second", "connection_type": "structural_echo"},'
            '{"observation": "third", "connection_type": "untouched_question"}'
            "]}"
        )
        out = _recover_complete_reflections(block)
        assert len(out) == 3
        assert out[0]["observation"] == "first"
        assert out[2]["observation"] == "third"

    def test_truncated_third_drops_incomplete(self):
        # Two complete objects, then truncation mid-third. Third must drop;
        # first two must survive — this is the production failure mode.
        block = (
            '{"reflections": ['
            '{"observation": "first", "connection_type": "convergence"},'
            '{"observation": "second", "connection_type": "contradiction"},'
            '{"observation": "third is cut'
        )
        out = _recover_complete_reflections(block)
        assert len(out) == 2
        assert out[0]["observation"] == "first"
        assert out[1]["observation"] == "second"

    def test_braces_inside_string_dont_throw_depth(self):
        # Observation contains literal `{` and `}` inside the string —
        # the brace counter must be string-literal-aware. If it isn't,
        # depth tracking is wrong and the recovery returns garbage.
        block = (
            '{"reflections": ['
            '{"observation": "talks about {curly} braces { and } more",'
            ' "connection_type": "other"}'
            "]}"
        )
        out = _recover_complete_reflections(block)
        assert len(out) == 1
        assert "{curly}" in out[0]["observation"]

    def test_escaped_quotes_inside_string_handled(self):
        # Escaped quote inside an observation must NOT toggle the in_str
        # state. If it does, the parser thinks the string ended early and
        # mis-counts braces.
        block = (
            '{"reflections": ['
            '{"observation": "she said \\"hello\\" to the chronicle",'
            ' "connection_type": "convergence"}'
            "]}"
        )
        out = _recover_complete_reflections(block)
        assert len(out) == 1
        assert 'said "hello"' in out[0]["observation"]

    def test_no_reflections_array_returns_empty(self):
        # No "reflections" key at all — recovery returns empty list, doesn't crash.
        assert _recover_complete_reflections('{"unrelated": "data"}') == []
        assert _recover_complete_reflections("just prose, no JSON") == []
        assert _recover_complete_reflections("") == []

    def test_empty_array_returns_empty(self):
        assert _recover_complete_reflections('{"reflections": []}') == []

    def test_individual_object_invalid_json_skipped(self):
        # An object that is brace-balanced but invalid JSON (missing comma,
        # bad escape, etc.) gets skipped silently — the brace counter
        # advances past it and tries the next one.
        block = (
            '{"reflections": ['
            '{"observation": "valid"}'
            "]}"
        )
        out = _recover_complete_reflections(block)
        assert len(out) == 1


class TestBuildPrompt:
    def test_includes_entries(self):
        entries = [
            {
                "timestamp": "2026-04-26T12:00:00+00:00",
                "domain": "d1",
                "layer": "hypothesis",
                "content": "first",
            },
            {
                "timestamp": "2026-04-26T11:00:00+00:00",
                "domain": "d2",
                "layer": "ground_truth",
                "content": "second",
            },
        ]
        prompt = build_prompt(entries)
        assert "[ENTRY 1]" in prompt
        assert "[ENTRY 2]" in prompt
        assert "first" in prompt
        assert "second" in prompt
        assert "d1" in prompt
        assert "d2" in prompt

    def test_long_content_trimmed(self):
        entries = [
            {
                "timestamp": "2026-04-26T12:00:00+00:00",
                "domain": "d",
                "layer": "h",
                "content": "x" * 5000,
            }
        ]
        prompt = build_prompt(entries)
        # 1800 char cap + truncation marker should appear.
        assert "[…truncated for prompt budget]" in prompt
        # Body capped before marker.
        assert prompt.count("x") < 5000

    def test_includes_preamble_and_closing(self):
        prompt = build_prompt([])
        assert "quiet observer" in prompt
        assert "gesture, don't declare" in prompt
