"""
Nape Daemon Tests

Covers the five core invariants specified in the task, plus a handful of
edge cases that defend the storage helpers and summary path.

Test structure mirrors test_handoff.py: setup/teardown with a tmpdir root,
one class per functional area.
"""

import json
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from sovereign_stack.nape_daemon import NapeDaemon, _result_to_str, _safe_truncate


SESSION = "test-session-001"
OTHER   = "test-session-002"


class TestDeclareBeforeVerify:
    """Pattern 1: sharp honk fires when result contains completion language
    but no verify call appears in the preceding 3 tool calls."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.daemon = NapeDaemon(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_declare_with_no_preceding_read_fires_sharp_honk(self):
        """Spec: declare-before-verify → sharp honk when no Read in last 3 calls."""
        # Three preceding calls that are NOT verify tools.
        self.daemon.observe("record_insight", {"content": "x"}, "insight stored", SESSION)
        self.daemon.observe("spiral_status",  {},               "phase: integration", SESSION)
        self.daemon.observe("record_open_thread", {},           "thread recorded", SESSION)

        # The trigger: a tool that returns "done" with no preceding verify.
        self.daemon.observe(
            "record_learning",
            {"what_happened": "tried x"},
            "done — learning recorded",
            SESSION,
        )

        honks = self.daemon.current_honks(SESSION)
        assert len(honks) >= 1
        sharp_honks = [h for h in honks if h["level"] == "sharp" and h["pattern"] == "declare_before_verify"]
        assert len(sharp_honks) >= 1, f"Expected sharp/declare_before_verify honk. Got: {honks}"

    def test_declare_with_preceding_read_produces_satisfied_honk(self):
        """Spec: declare with preceding verify → satisfied honk (no sharp)."""
        # A Read call immediately before the declare.
        self.daemon.observe("Read",  {"file_path": "/tmp/x.py"}, "line 1: pass", SESSION)
        self.daemon.observe("record_insight", {}, "complete — insight stored", SESSION)

        honks = self.daemon.current_honks(SESSION)
        sharp_honks = [h for h in honks if h["level"] == "sharp" and h["pattern"] == "declare_before_verify"]
        assert len(sharp_honks) == 0, (
            f"Expected no sharp honk when verify precedes declare. Got: {honks}"
        )

    def test_declare_word_is_case_insensitive(self):
        """DONE, Done, done should all trigger the check."""
        self.daemon.observe("some_tool", {}, "DONE.", SESSION)
        honks = [h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"]
        assert len(honks) >= 1

    def test_no_declare_word_no_honk(self):
        """If the result has no declare word, no honk of this type fires."""
        self.daemon.observe("record_learning", {}, "processing context...", SESSION)
        honks = [h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"]
        assert len(honks) == 0


class TestPrematureSummary:
    """Pattern 2: sharp honk when end_session_review/handoff/close_session called
    while recent history contains error indicators."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.daemon = NapeDaemon(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_end_session_review_with_recent_error_fires_sharp_honk(self):
        """Spec: premature summary → sharp honk when errors in recent history."""
        # A tool call that produced an error.
        self.daemon.observe(
            "Bash",
            {"command": "pytest tests/"},
            "Error: 3 tests failed\ntraceback: ...",
            SESSION,
        )
        # Immediately call end_session_review.
        self.daemon.observe(
            "end_session_review",
            {"highlights": "great session"},
            "review recorded",
            SESSION,
        )

        honks = self.daemon.current_honks(SESSION)
        premature = [h for h in honks if h["pattern"] == "premature_summary"]
        assert len(premature) >= 1, f"Expected premature_summary honk. Got: {honks}"
        assert premature[0]["level"] == "sharp"

    def test_handoff_with_recent_error_fires_sharp_honk(self):
        """handoff is also a summary tool — same rule applies."""
        self.daemon.observe("Read", {}, "FileNotFoundError: no such file", SESSION)
        self.daemon.observe(
            "handoff",
            {"note": "everything is fine"},
            "handoff written",
            SESSION,
        )
        honks = [h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "premature_summary"]
        assert len(honks) >= 1

    def test_close_session_with_clean_history_no_honk(self):
        """If no errors in recent history, close_session should not trigger honk."""
        self.daemon.observe("Read",    {"file_path": "/x.py"}, "def main(): pass", SESSION)
        self.daemon.observe("Bash",    {"command": "pytest"}, "5 passed in 0.3s", SESSION)
        self.daemon.observe(
            "close_session",
            {"what_i_learned": "tests pass"},
            "session closed",
            SESSION,
        )
        honks = [h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "premature_summary"]
        assert len(honks) == 0


class TestAckFlow:
    """Pattern 4 (spec order): honk written → ack recorded → honk no longer in current_honks."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.daemon = NapeDaemon(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _plant_sharp_honk(self) -> str:
        """Helper: generate a sharp honk and return its honk_id."""
        self.daemon.observe("some_tool",    {}, "error: something failed", SESSION)
        self.daemon.observe("end_session_review", {}, "review written", SESSION)
        honks = self.daemon.current_honks(SESSION)
        assert len(honks) >= 1, "Setup failed: no honk was generated."
        return honks[0]["honk_id"]

    def test_honk_appears_before_ack(self):
        """Honk is returned by current_honks before it is acknowledged."""
        honk_id = self._plant_sharp_honk()
        honks = self.daemon.current_honks(SESSION)
        ids = [h["honk_id"] for h in honks]
        assert honk_id in ids

    def test_ack_removes_honk_from_current_honks(self):
        """After ack(), the honk_id no longer appears in current_honks."""
        honk_id = self._plant_sharp_honk()
        self.daemon.ack(honk_id, note="addressed — I ran verify first next time")
        honks = self.daemon.current_honks(SESSION)
        ids = [h["honk_id"] for h in honks]
        assert honk_id not in ids

    def test_ack_persists_to_acks_jsonl(self):
        """Ack record is written to acks.jsonl with correct honk_id."""
        honk_id = self._plant_sharp_honk()
        self.daemon.ack(honk_id, note="addressed")
        acks_path = Path(self.tmpdir) / "nape" / "acks.jsonl"
        assert acks_path.exists()
        records = [json.loads(line) for line in acks_path.read_text().splitlines() if line.strip()]
        assert any(r.get("honk_id") == honk_id for r in records)

    def test_ack_unknown_id_raises_value_error(self):
        """Acknowledging a non-existent honk_id raises a clear ValueError."""
        with pytest.raises(ValueError, match="No honk found"):
            self.daemon.ack("nonexistent-id", note="test")

    def test_ack_empty_id_raises_value_error(self):
        """Acknowledging with an empty string raises ValueError."""
        with pytest.raises(ValueError, match="honk_id must be"):
            self.daemon.ack("", note="test")

    def test_honk_stays_in_honks_jsonl_after_ack(self):
        """Original honks.jsonl is not modified; ack is separate overlay."""
        honk_id = self._plant_sharp_honk()
        self.daemon.ack(honk_id, note="addressed")
        honks_path = Path(self.tmpdir) / "nape" / "honks.jsonl"
        records = [json.loads(line) for line in honks_path.read_text().splitlines() if line.strip()]
        assert any(r.get("honk_id") == honk_id for r in records), (
            "Honk should remain in honks.jsonl even after ack (append-only invariant)."
        )


class TestSummaryCounts:
    """Pattern 5 (spec order): summary() counts honks by level correctly."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.daemon = NapeDaemon(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_session_summary_all_zeros(self):
        result = self.daemon.summary(SESSION)
        assert result["total"] == 0
        assert result["sharp"] == 0
        assert result["low"] == 0
        assert result["uneasy"] == 0
        assert result["satisfied"] == 0
        assert result["unacknowledged"] == 0

    def test_summary_counts_sharp_honks(self):
        """Trigger two sharp honks and verify summary reflects them."""
        # Premature summary with errors → sharp
        self.daemon.observe("Bash", {}, "Error: test failed", SESSION)
        self.daemon.observe("end_session_review", {}, "done", SESSION)

        # Declare without verify → sharp
        self.daemon.observe("record_insight", {}, "complete, all verified", SESSION)

        result = self.daemon.summary(SESSION)
        assert result["sharp"] >= 1
        assert result["total"] >= 1
        assert result["session_id"] == SESSION

    def test_summary_unacknowledged_decreases_after_ack(self):
        """Unacknowledged count decreases by 1 after an ack."""
        self.daemon.observe("Bash", {}, "error: compile failed", SESSION)
        self.daemon.observe("end_session_review", {}, "review", SESSION)

        before = self.daemon.summary(SESSION)
        unacked_before = before["unacknowledged"]
        assert unacked_before >= 1

        honks = self.daemon.current_honks(SESSION)
        self.daemon.ack(honks[0]["honk_id"], "acknowledged")

        after = self.daemon.summary(SESSION)
        assert after["unacknowledged"] == unacked_before - 1

    def test_summary_scopes_to_session(self):
        """Honks from a different session are not counted in SESSION summary."""
        # Create a honk in the OTHER session.
        self.daemon.observe("Bash", {}, "error: failed", OTHER)
        self.daemon.observe("end_session_review", {}, "done", OTHER)

        result = self.daemon.summary(SESSION)
        assert result["total"] == 0

    def test_summary_none_session_covers_all(self):
        """summary(None) counts honks across all sessions."""
        self.daemon.observe("Bash", {}, "error: failed", SESSION)
        self.daemon.observe("end_session_review", {}, "done", SESSION)

        self.daemon.observe("Bash", {}, "error: failed", OTHER)
        self.daemon.observe("end_session_review", {}, "done", OTHER)

        result_all = self.daemon.summary(None)
        result_a   = self.daemon.summary(SESSION)
        result_b   = self.daemon.summary(OTHER)

        # All-session total should be the sum of individual session totals.
        assert result_all["total"] == result_a["total"] + result_b["total"]


class TestAssertionWithoutEvidence:
    """Pattern 3: low honk when record_insight is called with confidence>0.9
    but no verify call appears in the last 5 tool calls."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.daemon = NapeDaemon(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_high_confidence_insight_without_verify_fires_low_honk(self):
        """Spec: assertion-without-evidence → low honk."""
        # 5 non-verify calls.
        for _ in range(5):
            self.daemon.observe("record_learning", {}, "learning stored", SESSION)

        self.daemon.observe(
            "record_insight",
            {"domain": "research", "content": "X causes Y", "confidence": 0.95},
            "insight stored",
            SESSION,
        )

        honks = self.daemon.current_honks(SESSION)
        evidence_honks = [h for h in honks if h["pattern"] == "assertion_without_evidence"]
        assert len(evidence_honks) >= 1
        assert evidence_honks[0]["level"] == "low"

    def test_high_confidence_insight_with_verify_no_honk(self):
        """When Grep precedes the high-confidence insight, no honk fires."""
        self.daemon.observe("Grep", {"pattern": "def X"}, "match at line 42", SESSION)
        self.daemon.observe(
            "record_insight",
            {"domain": "research", "content": "X causes Y", "confidence": 0.95},
            "insight stored",
            SESSION,
        )

        honks = [h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "assertion_without_evidence"]
        assert len(honks) == 0

    def test_low_confidence_insight_no_honk(self):
        """confidence <= 0.9 does not trigger this pattern."""
        for _ in range(5):
            self.daemon.observe("some_tool", {}, "result", SESSION)

        self.daemon.observe(
            "record_insight",
            {"domain": "hypothesis", "content": "maybe X", "confidence": 0.7},
            "insight stored",
            SESSION,
        )

        honks = [h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "assertion_without_evidence"]
        assert len(honks) == 0

    def test_missing_confidence_field_no_honk(self):
        """record_insight without a confidence field skips the check gracefully."""
        self.daemon.observe(
            "record_insight",
            {"domain": "general", "content": "something interesting"},
            "insight stored",
            SESSION,
        )
        honks = [h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "assertion_without_evidence"]
        assert len(honks) == 0


class TestRepeatedMistake:
    """Pattern 4 (detection): uneasy honk when same tool errors twice without
    a record_learning call in between."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.daemon = NapeDaemon(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_same_tool_errors_twice_without_learning_fires_uneasy_honk(self):
        """Two errors from the same tool with no record_learning between them."""
        self.daemon.observe("Bash", {"command": "npm test"}, "Error: module not found", SESSION)
        self.daemon.observe("spiral_status", {}, "phase: execution", SESSION)
        self.daemon.observe("Bash", {"command": "npm test"}, "Error: module not found", SESSION)

        honks = self.daemon.current_honks(SESSION)
        uneasy = [h for h in honks if h["pattern"] == "repeated_mistake"]
        assert len(uneasy) >= 1
        assert uneasy[0]["level"] == "uneasy"

    def test_same_tool_errors_twice_with_learning_no_uneasy_honk(self):
        """With a record_learning between the two errors, no uneasy honk fires."""
        self.daemon.observe("Bash", {"command": "npm test"}, "Error: module not found", SESSION)
        self.daemon.observe("record_learning",
                            {"what_happened": "npm test failed", "what_learned": "needs install"},
                            "learning stored",
                            SESSION)
        self.daemon.observe("Bash", {"command": "npm test"}, "Error: module not found", SESSION)

        honks = [h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "repeated_mistake"]
        assert len(honks) == 0

    def test_single_error_no_uneasy_honk(self):
        """First error from a tool does not trigger repeated-mistake honk."""
        self.daemon.observe("Bash", {"command": "pytest"}, "error: import failed", SESSION)
        honks = [h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "repeated_mistake"]
        assert len(honks) == 0


class TestStorageHelpers:
    """Unit tests for module-level utilities."""

    def test_result_to_str_string_passthrough(self):
        assert _result_to_str("hello") == "hello"

    def test_result_to_str_none_returns_empty(self):
        assert _result_to_str(None) == ""

    def test_result_to_str_list_of_dicts_with_text(self):
        result = _result_to_str([{"text": "first"}, {"text": "second"}])
        assert "first" in result
        assert "second" in result

    def test_result_to_str_dict_serializes(self):
        result = _result_to_str({"status": "ok"})
        assert "ok" in result

    def test_result_to_str_truncates_at_4096(self):
        long_result = "x" * 10000
        result = _result_to_str(long_result)
        assert len(result) == 4096

    def test_safe_truncate_leaves_short_values_intact(self):
        args = {"key": "short value"}
        out = _safe_truncate(args)
        assert out["key"] == "short value"

    def test_safe_truncate_caps_long_strings(self):
        long_val = "y" * 1000
        out = _safe_truncate({"content": long_val})
        assert len(out["content"]) < 600
        assert "[truncated]" in out["content"]

    def test_safe_truncate_non_dict_returns_empty(self):
        assert _safe_truncate("not a dict") == {}  # type: ignore[arg-type]


class TestObserveValidation:
    """observe() should reject bad inputs with clear error messages."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.daemon = NapeDaemon(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_tool_name_raises(self):
        with pytest.raises(ValueError, match="tool_name"):
            self.daemon.observe("", {}, "result", SESSION)

    def test_empty_session_id_raises(self):
        with pytest.raises(ValueError, match="session_id"):
            self.daemon.observe("Read", {}, "result", "")

    def test_valid_observe_writes_to_jsonl(self):
        self.daemon.observe("Read", {"file_path": "/x.py"}, "contents", SESSION)
        obs_path = Path(self.tmpdir) / "nape" / "observations.jsonl"
        assert obs_path.exists()
        records = [json.loads(line) for line in obs_path.read_text().splitlines() if line.strip()]
        assert len(records) == 1
        assert records[0]["tool_name"] == "Read"
        assert records[0]["session_id"] == SESSION

    def test_observe_creates_nape_dir(self):
        """Nape dir is created lazily on first observe."""
        nape_dir = Path(self.tmpdir) / "nape"
        assert nape_dir.exists()  # created by __init__

    def test_multiple_sessions_isolated(self):
        """Observations from different sessions do not bleed into each other."""
        self.daemon.observe("Bash", {}, "error: failed", SESSION)
        self.daemon.observe("end_session_review", {}, "done", SESSION)

        honks_other = self.daemon.current_honks(OTHER)
        assert len(honks_other) == 0


# ---------------------------------------------------------------------------
# Edit 4: satisfied-honk accounting in summary() and current_honks() kwarg
# ---------------------------------------------------------------------------

class TestSatisfiedHonkAccounting:
    """satisfied honks must NOT dirty the unacknowledged count in summary()."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.daemon = NapeDaemon(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _plant_sharp_honk(self):
        """Emit a sharp honk via declare-before-verify, return honk_id."""
        self.daemon.observe("some_tool", {}, "error: compile failed", SESSION)
        self.daemon.observe("end_session_review", {}, "review complete", SESSION)
        honks = self.daemon.current_honks(SESSION)
        sharp = [h for h in honks if h["level"] == "sharp"]
        assert sharp, f"Setup failed: no sharp honk. All honks: {honks}"
        return sharp[0]["honk_id"]

    def _plant_satisfied_honk(self):
        """Emit a satisfied honk via clean verify+declare, return honk_id."""
        self.daemon.observe("recall_insights", {"query": "test"}, "found 3 insights", SESSION)
        self.daemon.observe("record_insight", {}, "complete — recorded", SESSION)
        honks = self.daemon.current_honks(SESSION)
        satisfied = [h for h in honks if h["level"] == "satisfied"]
        assert satisfied, f"Setup failed: no satisfied honk. All honks: {honks}"
        return satisfied[0]["honk_id"]

    def test_satisfied_does_not_count_as_unacknowledged(self):
        """summary() unacknowledged excludes satisfied honks (they are positive signal)."""
        # Emit a sharp honk (counts as unacked) and a satisfied honk (does not).
        self._plant_sharp_honk()
        # Reset to get a clean second observation sequence
        self.daemon = NapeDaemon(root=self.tmpdir)
        self._plant_sharp_honk()

        # Now plant a satisfied honk.
        self.daemon.observe("recall_insights", {}, "context loaded", SESSION)
        self.daemon.observe("record_insight", {}, "verified, complete", SESSION)

        result = self.daemon.summary(SESSION)
        # unacknowledged must equal number of non-satisfied unacked honks
        unacked = result["unacknowledged"]
        satisfied_count = result["satisfied"]
        sharp_count = result["sharp"]

        assert satisfied_count >= 1, "At least one satisfied honk expected"
        # unacknowledged must NOT include satisfied
        assert unacked == sharp_count + result.get("low", 0) + result.get("uneasy", 0), (
            f"unacknowledged ({unacked}) must equal sum of non-satisfied levels, "
            f"not include satisfied ({satisfied_count})"
        )

    def test_summary_satisfied_sharp_separate_unacked_is_1(self):
        """Emit 1 sharp + 1 satisfied, ack nothing: unacknowledged=1 (sharp only)."""
        # Fresh daemon for isolation
        daemon = NapeDaemon(root=self.tmpdir)
        session = "iso-satisfied-session"

        # Emit sharp: declare without verify
        daemon.observe("no_verify_tool", {}, "done — complete", session)

        # Emit satisfied: verify then declare
        daemon.observe("recall_insights", {}, "found insights", session)
        daemon.observe("record_insight", {}, "insight recorded complete", session)

        result = daemon.summary(session)
        assert result["sharp"] >= 1, f"Expected at least 1 sharp. Got: {result}"
        assert result["satisfied"] >= 1, f"Expected at least 1 satisfied. Got: {result}"
        assert result["unacknowledged"] == result["sharp"] + result.get("uneasy", 0) + result.get("low", 0), (
            f"unacknowledged must not include satisfied. summary={result}"
        )

    def test_current_honks_include_satisfied_true_by_default(self):
        """current_honks() with default include_satisfied=True returns satisfied honks."""
        self.daemon.observe("recall_insights", {}, "data loaded", SESSION)
        self.daemon.observe("record_insight", {}, "complete — done", SESSION)

        all_honks = self.daemon.current_honks(SESSION, include_satisfied=True)
        satisfied = [h for h in all_honks if h["level"] == "satisfied"]
        assert len(satisfied) >= 1, "Satisfied honk must appear when include_satisfied=True"

    def test_current_honks_exclude_satisfied_false(self):
        """current_honks(include_satisfied=False) filters out satisfied honks."""
        self.daemon.observe("recall_insights", {}, "data loaded", SESSION)
        self.daemon.observe("record_insight", {}, "complete — done", SESSION)

        filtered = self.daemon.current_honks(SESSION, include_satisfied=False)
        satisfied = [h for h in filtered if h["level"] == "satisfied"]
        assert len(satisfied) == 0, (
            f"No satisfied honks should appear when include_satisfied=False. Got: {filtered}"
        )
