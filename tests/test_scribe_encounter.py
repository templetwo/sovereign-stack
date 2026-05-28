"""Unit tests for scribe.encounter — encounter-note write path."""

from __future__ import annotations

import json
from pathlib import Path

from sovereign_stack.scribe.encounter import (
    DEFAULT_INTENSITY,
    _normalize_parent_hint,
    build_encounter_summary,
    write_encounter_note,
)
from sovereign_stack.scribe.session import ScribeSession


class TestNormalizeParentHint:
    def test_passthrough_kebab(self):
        assert _normalize_parent_hint("claude-code-mac-studio") == "claude-code-mac-studio"

    def test_lowercases(self):
        assert _normalize_parent_hint("ClaudeCodeWeb") == "claudecodeweb"

    def test_none_becomes_unknown(self):
        assert _normalize_parent_hint(None) == "unknown"

    def test_empty_becomes_unknown(self):
        assert _normalize_parent_hint("") == "unknown"

    def test_collapses_whitespace_and_specials(self):
        assert _normalize_parent_hint("iPhone Claude!") == "iphone-claude"

    def test_trims_leading_trailing_dashes(self):
        assert _normalize_parent_hint("--weird---") == "weird"


class TestBuildEncounterSummary:
    def test_basic_summary(self):
        session = ScribeSession.create(parent_instance="claude-code")
        session.append_user_turn("what's the state of the dispatcher work?")
        session.append_assistant_turn(
            "the addendum landed today.", tokens_in=200, tokens_out=30, cost_usd=0.0011
        )
        summary = build_encounter_summary(session)
        assert "claude-code" in summary
        assert "2 turn" in summary
        assert "what's the state of the dispatcher work" in summary
        assert "Tokens in/out: 200/30" in summary
        assert "$0.0011" in summary

    def test_open_session_summary(self):
        session = ScribeSession.create(parent_instance="iphone-claude")
        session.append_user_turn("hello")
        summary = build_encounter_summary(session)
        assert "session open" in summary

    def test_closed_session_summary(self):
        session = ScribeSession.create(parent_instance="iphone-claude")
        session.append_user_turn("hello")
        session.closed = True
        summary = build_encounter_summary(session)
        assert "session closed" in summary

    def test_unknown_parent(self):
        session = ScribeSession.create(parent_instance=None)
        summary = build_encounter_summary(session)
        assert "unknown instance" in summary

    def test_truncates_long_question(self):
        long_q = "a" * 500
        session = ScribeSession.create(parent_instance="x")
        session.append_user_turn(long_q)
        summary = build_encounter_summary(session)
        # Truncated to 140 chars in the opening-question slice
        assert "a" * 141 not in summary


class TestWriteEncounterNote:
    def test_writes_to_chronicle_jsonl(self, tmp_path):
        chronicle_root = tmp_path / "chronicle"
        session = ScribeSession.create(parent_instance="claude-code")
        session.append_user_turn("hi")
        session.append_assistant_turn("hello", cost_usd=0.001)

        path = write_encounter_note(session, chronicle_root=chronicle_root)
        path = Path(path)
        assert path.exists()

        lines = path.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["domain"] == "scribe,encounter,claude-code"
        assert entry["layer"] == "ground_truth"
        assert entry["intensity"] == DEFAULT_INTENSITY
        assert entry["source_instance"] == "scribe-haiku-4-5"
        assert entry["scribe_session_id"] == session.session_id
        assert entry["scribe_turn_count"] == 2
        assert "claude-code arrived" in entry["content"]

    def test_path_uses_normalized_hint(self, tmp_path):
        chronicle_root = tmp_path / "chronicle"
        session = ScribeSession.create(parent_instance="iPhone Claude!")
        session.append_user_turn("hi")
        path = write_encounter_note(session, chronicle_root=chronicle_root)
        assert "scribe,encounter,iphone-claude" in path

    def test_extra_summary_appended(self, tmp_path):
        chronicle_root = tmp_path / "chronicle"
        session = ScribeSession.create(parent_instance="x")
        session.append_user_turn("hi")
        path = write_encounter_note(
            session,
            chronicle_root=chronicle_root,
            extra_summary="Pointed at handoff 20260421T220400.",
        )
        entry = json.loads(Path(path).read_text().splitlines()[0])
        assert "Pointed at handoff 20260421T220400" in entry["content"]

    def test_extra_metadata_merged(self, tmp_path):
        chronicle_root = tmp_path / "chronicle"
        session = ScribeSession.create(parent_instance="x")
        session.append_user_turn("hi")
        path = write_encounter_note(
            session,
            chronicle_root=chronicle_root,
            extra_metadata={"redaction_total": 4},
        )
        entry = json.loads(Path(path).read_text().splitlines()[0])
        assert entry["redaction_total"] == 4

    def test_filename_is_scribe_session_id(self, tmp_path):
        chronicle_root = tmp_path / "chronicle"
        session = ScribeSession.create(parent_instance="x")
        session.append_user_turn("hi")
        path = Path(write_encounter_note(session, chronicle_root=chronicle_root))
        assert path.name == f"{session.session_id}.jsonl"

    def test_multiple_writes_to_same_session_append(self, tmp_path):
        """Two encounter notes for the same scribe session append to the
        same jsonl file (e.g., interim and final notes)."""
        chronicle_root = tmp_path / "chronicle"
        session = ScribeSession.create(parent_instance="x")
        session.append_user_turn("hi")

        path1 = write_encounter_note(session, chronicle_root=chronicle_root)
        session.append_user_turn("more")
        path2 = write_encounter_note(session, chronicle_root=chronicle_root)

        assert path1 == path2
        lines = Path(path1).read_text().splitlines()
        assert len(lines) == 2
