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

from sovereign_stack.daemons import synthesis_daemon
from sovereign_stack.daemons.synthesis_daemon import (
    Reflection,
    SynthesisDaemon,
    _recover_complete_reflections,
    build_prompt,
    extract_json_block,
    is_explicit_abstain,
    parse_reflections,
    read_ack_history,
    read_recent_chronicle,
    read_recent_handoffs,
    read_spanning_chronicle,
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
        entries = read_recent_chronicle(chronicle_root=chronicle_root, recent_hours=24)
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
        entries = read_recent_chronicle(chronicle_root=chronicle_root, max_entries=5)
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
        raw = 'Some thinking here.\n...done thinking.\n\n{"reflections": []}'
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
        raw = 'Here is what I think: {"reflections": [1]} that is all.'
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

    def test_bare_object_without_wrapper_tolerated(self):
        # ministral sometimes drops the {"reflections": [...]} wrapper and
        # emits a single bare reflection object. format=json doesn't enforce
        # the wrapper, so the parser must accept it rather than lose a good
        # reflection to parse_failed (observed live 2026-06-19).
        raw = json.dumps(
            {
                "observation": "d1 and d2 converge on the same correction",
                "entries_referenced": ["ENTRY_2", "ENTRY_5"],
                "connection_type": "convergence",
                "confidence": "medium",
            }
        )
        result = parse_reflections(raw)
        assert len(result) == 1
        assert result[0].connection_type == "convergence"
        assert result[0].observation.startswith("d1 and d2")

    def test_bare_array_without_wrapper_tolerated(self):
        # A top-level array of one object (no wrapper) is also accepted.
        raw = '[{"observation": "lone", "connection_type": "other", "confidence": "low"}]'
        result = parse_reflections(raw)
        assert len(result) == 1
        assert result[0].observation == "lone"

    def test_dict_without_reflections_or_observation_is_empty(self):
        # A structurally valid dict that is neither a wrapper nor a bare
        # reflection (e.g. {"reflections": []} abstain, or unrelated keys)
        # yields no reflections — abstain/parse_failed is decided upstream.
        assert parse_reflections('{"reflections": []}') == []
        assert parse_reflections('{"unrelated": "data"}') == []

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
        block = '{"reflections": [{"observation": "valid"}]}'
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

    def test_v3_abstain_permission_present(self):
        # v3: standard-mode prompt must explicitly offer the empty-array
        # abstain in both the preamble and the closing instruction, so a 14B
        # stops manufacturing one dialectical tension per firing.
        prompt = build_prompt([])
        assert '{"reflections": []}' in prompt
        assert "Abstaining is a respected, correct answer." in prompt
        assert "abstaining is correct" in prompt  # closing instruction
        # The forcing line from v2 must be gone.
        assert "make it count, you only get one" not in prompt

    def test_v3_abstain_absent_from_goose(self):
        # Goose mode is untouched — it has its own no-gaps path and must not
        # inherit the standard-mode abstain prose.
        prompt = build_prompt([], focus="goose")
        assert "Abstaining is a respected, correct answer." not in prompt

    def test_confirmed_patterns_injected(self):
        prompt = build_prompt([], confirmed_patterns=["the operational holism pattern"])
        assert "ALREADY CONFIRMED" in prompt
        assert "operational holism" in prompt

    def test_discarded_patterns_injected(self):
        prompt = build_prompt([], discarded_patterns=["a noisy angle"])
        assert "PREVIOUSLY DISCARDED" in prompt
        assert "noisy angle" in prompt

    def test_goose_mode_uses_goose_preamble(self):
        prompt = build_prompt([], focus="goose")
        assert "gap-finder" in prompt
        assert "GAPS" in prompt
        # Standard preamble must NOT appear.
        assert "quiet observer" not in prompt

    def test_goose_mode_includes_handoffs(self):
        handoffs = [
            {
                "timestamp": "2026-04-29T10:00:00+00:00",
                "note": "Pick up the auth refactor",
                "source_instance": "opus-mac",
                "thread": "auth",
            }
        ]
        prompt = build_prompt([], focus="goose", handoffs=handoffs)
        assert "[HANDOFF 1]" in prompt
        assert "auth refactor" in prompt
        assert "opus-mac" in prompt

    def test_goose_mode_excludes_ack_history(self):
        # Ack history context is irrelevant to gap-finding.
        prompt = build_prompt(
            [],
            focus="goose",
            confirmed_patterns=["some confirmed pattern"],
        )
        assert "ALREADY CONFIRMED" not in prompt

    def test_spanning_mode_labels_chronicle(self):
        entries = [
            {
                "timestamp": "2026-04-26T12:00:00+00:00",
                "domain": "d",
                "layer": "hypothesis",
                "content": "an entry",
            }
        ]
        prompt = build_prompt(entries, spanning_mode=True)
        assert "spanning multiple weeks" in prompt

    def test_focus_steering_in_standard_mode(self):
        prompt = build_prompt([], focus="register-drift")
        assert "register-drift" in prompt
        # Goose preamble must not appear.
        assert "gap-finder" not in prompt


# ── is_explicit_abstain ─────────────────────────────────────────────────────


class TestIsExplicitAbstain:
    """v3: distinguish a deliberate {"reflections": []} (abstain) from
    unparseable garbage (parse_failed). parse_reflections() returns [] for
    both; this predicate is what run() uses to tell them apart."""

    def test_empty_array_is_abstain(self):
        assert is_explicit_abstain('{"reflections": []}') is True

    def test_empty_array_with_prose_and_fence_is_abstain(self):
        # extract_json_block strips thinking traces / fences first.
        assert is_explicit_abstain('thinking...done thinking.\n{"reflections": []}') is True
        assert is_explicit_abstain('```json\n{"reflections": []}\n```') is True

    def test_nonempty_array_is_not_abstain(self):
        raw = '{"reflections": [{"observation": "x", "connection_type": "other"}]}'
        assert is_explicit_abstain(raw) is False

    def test_garbage_is_not_abstain(self):
        assert is_explicit_abstain("just prose, no json") is False
        assert is_explicit_abstain("") is False

    def test_missing_key_is_not_abstain(self):
        assert is_explicit_abstain('{"unrelated": "data"}') is False

    def test_truncated_body_is_not_abstain(self):
        # A cut-off body is a real failure, not a clean abstain.
        assert is_explicit_abstain('{"reflections": [{"observation": "cut') is False


# ── run() outcome wiring (model call mocked) ─────────────────────────────────


class TestRunAbstainOutcome:
    """End-to-end run() wiring for the v3 abstain path. The model call is
    monkeypatched — these prove the plumbing (empty array -> 'abstained',
    garbage -> 'parse_failed', real reflection -> 'wrote'). Behavioral proof
    that the live model actually abstains is a separate fresh-process firing,
    not a unit test."""

    def _daemon(self, chronicle_root: Path, tmp_path: Path) -> SynthesisDaemon:
        now = datetime.now(timezone.utc)
        _write_entry(chronicle_root, "d1", now, "an entry", layer="hypothesis")
        _write_entry(chronicle_root, "d2", now, "another entry", layer="ground_truth")
        return SynthesisDaemon(
            chronicle_root=chronicle_root,
            reflections_dir=tmp_path / "reflections",
            recent_hours=48,
        )

    def test_explicit_empty_array_records_abstained(
        self, chronicle_root: Path, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(
            synthesis_daemon, "call_ollama", lambda *a, **k: (True, '{"reflections": []}')
        )
        result = self._daemon(chronicle_root, tmp_path).run()
        assert result.outcome == "abstained"
        assert result.reflections_written == 0
        assert result.reflections_path is None

    def test_unparseable_output_still_parse_failed(
        self, chronicle_root: Path, tmp_path: Path, monkeypatch
    ):
        # A genuine garbage response must NOT be masked as an abstain.
        monkeypatch.setattr(
            synthesis_daemon, "call_ollama", lambda *a, **k: (True, "the model rambled, no json")
        )
        result = self._daemon(chronicle_root, tmp_path).run()
        assert result.outcome == "parse_failed"

    def test_real_reflection_still_wrote(
        self, chronicle_root: Path, tmp_path: Path, monkeypatch
    ):
        raw = (
            '{"reflections": [{"observation": "d1 and d2 converge",'
            ' "entries_referenced": ["d1", "d2"],'
            ' "connection_type": "convergence", "confidence": "low"}]}'
        )
        monkeypatch.setattr(synthesis_daemon, "call_ollama", lambda *a, **k: (True, raw))
        result = self._daemon(chronicle_root, tmp_path).run()
        assert result.outcome == "wrote"
        assert result.reflections_written == 1


# ── read_spanning_chronicle ─────────────────────────────────────────────────


class TestReadSpanningChronicle:
    def test_empty_root_returns_empty(self, tmp_path: Path):
        assert read_spanning_chronicle(chronicle_root=tmp_path / "nope") == []

    def test_surfaces_entries_across_weeks(self, chronicle_root: Path):
        now = datetime.now(timezone.utc)
        # Write entries spread over 3 weeks.
        _write_entry(chronicle_root, "d1", now - timedelta(days=1), "week-1")
        _write_entry(chronicle_root, "d2", now - timedelta(days=8), "week-2")
        _write_entry(chronicle_root, "d3", now - timedelta(days=15), "week-3")
        entries = read_spanning_chronicle(
            chronicle_root=chronicle_root, span_weeks=4, entries_per_week=1
        )
        contents = {e["content"] for e in entries}
        assert "week-1" in contents
        assert "week-2" in contents
        assert "week-3" in contents

    def test_ordered_oldest_first(self, chronicle_root: Path):
        now = datetime.now(timezone.utc)
        _write_entry(chronicle_root, "d1", now - timedelta(days=1), "recent")
        _write_entry(chronicle_root, "d2", now - timedelta(days=10), "older")
        entries = read_spanning_chronicle(
            chronicle_root=chronicle_root, span_weeks=4, entries_per_week=2
        )
        assert len(entries) >= 2
        # Oldest first.
        assert entries[0]["content"] == "older"
        assert entries[-1]["content"] == "recent"

    def test_respects_entries_per_week_cap(self, chronicle_root: Path):
        now = datetime.now(timezone.utc)
        # 6 entries in the same week.
        for i in range(6):
            _write_entry(chronicle_root, f"d{i}", now - timedelta(hours=i + 1), f"e{i}")
        entries = read_spanning_chronicle(
            chronicle_root=chronicle_root, span_weeks=1, entries_per_week=2
        )
        assert len(entries) <= 2


# ── read_recent_handoffs ────────────────────────────────────────────────────


class TestReadRecentHandoffs:
    def test_missing_dir_returns_empty(self, tmp_path: Path):
        assert read_recent_handoffs(handoffs_dir=tmp_path / "nope") == []

    def test_reads_json_handoff(self, tmp_path: Path):
        now = datetime.now(timezone.utc)
        handoff = {
            "timestamp": now.isoformat(),
            "note": "pick up the auth work",
            "source_instance": "opus-mac",
            "thread": "auth",
        }
        (tmp_path / "h1.json").write_text(json.dumps(handoff))
        result = read_recent_handoffs(handoffs_dir=tmp_path, recent_hours=24)
        assert len(result) == 1
        assert result[0]["note"] == "pick up the auth work"
        assert result[0]["source_instance"] == "opus-mac"

    def test_old_handoffs_filtered(self, tmp_path: Path):
        old = datetime.now(timezone.utc) - timedelta(hours=100)
        handoff = {
            "timestamp": old.isoformat(),
            "note": "stale intent",
            "source_instance": "x",
        }
        (tmp_path / "old.json").write_text(json.dumps(handoff))
        result = read_recent_handoffs(handoffs_dir=tmp_path, recent_hours=24)
        assert result == []

    def test_max_handoffs_cap(self, tmp_path: Path):
        now = datetime.now(timezone.utc)
        for i in range(8):
            h = {
                "timestamp": (now - timedelta(minutes=i)).isoformat(),
                "note": f"handoff {i}",
                "source_instance": "x",
            }
            (tmp_path / f"h{i}.json").write_text(json.dumps(h))
        result = read_recent_handoffs(handoffs_dir=tmp_path, recent_hours=24, max_handoffs=3)
        assert len(result) == 3

    def test_malformed_json_skipped(self, tmp_path: Path):
        now = datetime.now(timezone.utc)
        (tmp_path / "bad.json").write_text("not json {{{")
        good = {
            "timestamp": now.isoformat(),
            "note": "good handoff",
            "source_instance": "x",
        }
        (tmp_path / "good.json").write_text(json.dumps(good))
        result = read_recent_handoffs(handoffs_dir=tmp_path, recent_hours=24)
        assert len(result) == 1
        assert result[0]["note"] == "good handoff"


# ── read_ack_history ────────────────────────────────────────────────────────


class TestReadAckHistory:
    def test_missing_dir_returns_empty_lists(self, tmp_path: Path):
        confirmed, discarded = read_ack_history(reflections_dir=tmp_path / "nope")
        assert confirmed == []
        assert discarded == []

    def test_reads_confirmed_reflections(self, tmp_path: Path):
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": "reflection_abc_001",
            "timestamp": now,
            "model": "ministral-3:14b",
            "prompt_version": "v1",
            "run_id": "abc",
            "observation": "a confirmed structural echo pattern",
            "entries_referenced": [],
            "connection_type": "structural_echo",
            "confidence": "medium",
            "ack_status": "confirm",
        }
        jsonl = tmp_path / "2026-04-29.jsonl"
        jsonl.write_text(json.dumps(record) + "\n")
        confirmed, discarded = read_ack_history(reflections_dir=tmp_path)
        assert len(confirmed) == 1
        assert "structural echo" in confirmed[0]
        assert discarded == []

    def test_reads_discarded_reflections(self, tmp_path: Path):
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": "reflection_abc_002",
            "timestamp": now,
            "model": "ministral-3:14b",
            "prompt_version": "v1",
            "run_id": "abc",
            "observation": "a noisy low-signal observation",
            "entries_referenced": [],
            "connection_type": "other",
            "confidence": "low",
            "ack_status": "discard",
        }
        jsonl = tmp_path / "2026-04-29.jsonl"
        jsonl.write_text(json.dumps(record) + "\n")
        confirmed, discarded = read_ack_history(reflections_dir=tmp_path)
        assert confirmed == []
        assert len(discarded) == 1
        assert "noisy" in discarded[0]

    def test_long_observations_truncated(self, tmp_path: Path):
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": "reflection_abc_003",
            "timestamp": now,
            "model": "ministral-3:14b",
            "prompt_version": "v1",
            "run_id": "abc",
            "observation": "x" * 500,
            "entries_referenced": [],
            "connection_type": "structural_echo",
            "confidence": "medium",
            "ack_status": "confirm",
        }
        jsonl = tmp_path / "2026-04-29.jsonl"
        jsonl.write_text(json.dumps(record) + "\n")
        confirmed, _ = read_ack_history(reflections_dir=tmp_path)
        assert len(confirmed[0]) <= 205  # 200 chars + "…"
