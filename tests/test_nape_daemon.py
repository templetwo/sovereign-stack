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
from pathlib import Path

import pytest

from sovereign_stack.nape_daemon import (
    DECLARE_WORDS,
    READONLY_TOOL_NAMES,
    VERIFY_TOOL_NAMES,
    NapeDaemon,
    _declare_word_pattern,
    _result_to_str,
    _safe_truncate,
    contains_declare_word,
    strip_system_stamps,
)

SESSION = "test-session-001"
OTHER = "test-session-002"

# Verbatim result strings lifted from the live corpus at ~/.sovereign/nape/
# (observations.jsonl + the two rotated .gz archives). Copied, not invented —
# the format must be mirrored from the emitter, never guessed.
CORPUS_STAMPED_INSIGHT = (
    "⟁ Insight recorded [ground_truth]: /Users/tony_studio/.sovereign/chronicle/"
    "insights/spiral-lineage,prehistory-backfill,genesis-dates/"
    "spiral_20260617_194931.jsonl (receipts: 0 verified, 1 attested)"
)
CORPUS_STAMPED_INSIGHT_WITH_VIA = (
    "⟁ Insight recorded [ground_truth] (via implementation_verified): "
    "/Users/tony_studio/.sovereign/chronicle/insights/sovereign-stack,release,"
    "v1.7.0,receipts-and-seasons,milestone/spiral_20260610_063959.jsonl "
    "(receipts: 1 verified, 0 attested)"
)
CORPUS_STAMPED_CATCH = (
    "⚓ Catch recorded: cross-instance verification (the goose) → hq-web-seat "
    "(opus) (sibling). /Users/tony_studio/.sovereign/chronicle/insights/"
    "the-ground,catch,sibling,seed-2026-07-13/session_20260713_055710.jsonl "
    "(receipts: 0 verified, 0 attested)"
)


class TestStripSystemStamps:
    """The strip helper itself, asserted directly rather than only through
    the detector — a detector-only assertion can pass because some OTHER
    early return fired, proving nothing about the mechanism."""

    def test_receipts_stamp_removed(self):
        out = strip_system_stamps(CORPUS_STAMPED_INSIGHT)
        assert "receipts:" not in out
        assert "verified" not in out.lower()

    def test_via_vantage_is_deliberately_NOT_removed(self):
        """REGRESSION GUARD — do not "fix" this by stripping the via span.

        An earlier revision stripped `(via {vantage})` too. It was removed after
        adversarial review because `vantage` is a caller-supplied tool ARGUMENT
        (server.py:2628 `arguments.get("vantage")`), with no enum and no
        validator — so stripping it let the subject of the gate disarm the gate
        by naming a field. Verified live: a write with
        vantage="all_tests_passed_shipped_and_done" honked before the strip and
        went silent after it.

        Production data confirms vantage is free text, not a closed set: 21
        distinct values across 151 observations, including full prose and one
        value truncated mid-parenthesis ('claude-opus-5 (1M'). The pattern
        bought 11 of 1,233 suppressions. If vantage noise ever needs handling,
        validate the field at the WRITE path; do not blind the detector at the
        READ path.
        """
        out = strip_system_stamps(CORPUS_STAMPED_INSIGHT_WITH_VIA)
        assert "implementation_verified" in out, "vantage must survive the strip"
        assert "receipts:" not in out, "the receipts stamp must still clear"

    def test_catch_stamp_removed(self):
        """record_catch carries no via span; the receipts stamp still must clear."""
        out = strip_system_stamps(CORPUS_STAMPED_CATCH)
        assert "receipts:" not in out
        assert "verified" not in out.lower()

    def test_chronicle_path_is_preserved(self):
        """The domain segment is INSTANCE-authored. Stripping the path would
        blind the gate to an instance tagging its own write 'complete'."""
        out = strip_system_stamps(CORPUS_STAMPED_INSIGHT)
        assert "spiral-lineage,prehistory-backfill,genesis-dates" in out
        assert ".jsonl" in out

    def test_instance_authored_via_parenthetical_is_not_stripped(self):
        """Nothing the instance wrote may be removed. Only the receipts stamp
        is stripped, so a parenthetical the instance authored — including one
        beginning 'via' — passes through untouched and its declare words still
        count."""
        text = "Migration finished (via the bridge) and the tree is clean"
        out = strip_system_stamps(text)
        assert out == text

    def test_declare_word_outside_stamp_survives(self):
        """Guards the test below from being vacuous."""
        text = "shard.jsonl — verified against the live tree (receipts: 2 verified, 0 attested)"
        out = strip_system_stamps(text)
        assert "receipts:" not in out
        assert "verified against the live tree" in out


class TestReceiptStampIsNotADeclaration:
    """The system's provenance stamp is not the instance declaring done.

    Measured on the live corpus (80,603 observations, 2026-04-23..2026-07-28):
    1,264 of 1,523 declare_before_verify honks (83%) fired on nothing but the
    receipts stamp the stack itself appended. The receipts layer exists so a
    claim can never be naked; before this fix, the better the provenance, the
    louder the dishonesty alarm.
    """

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.daemon = NapeDaemon(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _dbv(self):
        return [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"
        ]

    def _three_non_verify_calls(self):
        """Guarantee the no-verify branch is the one under test."""
        self.daemon.observe("record_open_thread", {}, "Thread recorded", SESSION)
        self.daemon.observe("spiral_status", {}, "phase: integration", SESSION)
        self.daemon.observe("connectivity_status", {}, "all services up", SESSION)

    # (a) receipt-stamped result, no preceding verify -> must NOT honk
    def test_receipt_stamped_insight_does_not_honk(self):
        self._three_non_verify_calls()
        self.daemon.observe("record_insight", {"content": "x"}, CORPUS_STAMPED_INSIGHT, SESSION)
        assert self._dbv() == [], (
            "The stack's own receipt stamp is not the instance declaring done."
        )

    def test_declare_word_in_the_via_vantage_STILL_honks(self):
        """EVASION GUARD. A declare word inside the vantage must still fire.

        An earlier revision stripped the `(via {vantage})` span on the premise
        that the vantage is "a word the SYSTEM renders, not one the instance
        chose". That premise is false: `vantage` is a caller-supplied tool
        argument (server.py:2628), unvalidated free text — 21 distinct values
        across 151 live observations, including whole sentences. Stripping it
        meant a write with vantage="all_tests_passed_shipped_and_done" went
        silent, letting the subject of the gate disarm the gate by naming a
        field. The receipts stamp still clears; the vantage does not.
        """
        self._three_non_verify_calls()
        self.daemon.observe(
            "record_insight", {"content": "x"}, CORPUS_STAMPED_INSIGHT_WITH_VIA, SESSION
        )
        honks = self._dbv()
        assert len(honks) == 1, f"A declare word in caller-supplied text must fire. Got: {honks}"
        assert honks[0]["level"] == "sharp"

    def test_receipt_stamped_catch_does_not_honk(self):
        self._three_non_verify_calls()
        self.daemon.observe("record_catch", {"caught": "x"}, CORPUS_STAMPED_CATCH, SESSION)
        assert self._dbv() == []

    # (b) genuine declaration, no preceding verify -> must STILL honk sharp
    def test_genuine_declaration_still_honks_sharp(self):
        self._three_non_verify_calls()
        self.daemon.observe("record_insight", {}, "verified the migration is complete", SESSION)
        honks = self._dbv()
        assert len(honks) == 1, f"A real declaration must still honk. Got: {honks}"
        assert honks[0]["level"] == "sharp"

    def test_genuine_declaration_tests_passed_still_honks_sharp(self):
        self._three_non_verify_calls()
        self.daemon.observe("record_learning", {}, "all tests passed, done", SESSION)
        honks = self._dbv()
        assert len(honks) == 1, f"A real declaration must still honk. Got: {honks}"
        assert honks[0]["level"] == "sharp"

    def test_instance_tagged_domain_still_honks(self):
        """A real corpus shape: the instance tagged its OWN write 'shipped'.
        That is a declaration and the path is deliberately not stripped."""
        self._three_non_verify_calls()
        self.daemon.observe(
            "record_insight",
            {},
            "⟁ Insight recorded [ground_truth]: /Users/tony_studio/.sovereign/chronicle/"
            "insights/sovereign-bridge,door-that-asks,phase-1,session-tokens,shipped/"
            "spiral_20260630_205914.jsonl (receipts: 1 verified, 1 attested)",
            SESSION,
        )
        assert len(self._dbv()) == 1, "Instance-authored domain tags must remain visible."

    # (c) declare word BOTH inside and outside the stamp -> must STILL honk
    def test_declare_word_inside_and_outside_stamp_still_honks(self):
        """The only declare word that occurs INSIDE the receipts stamp is
        'verified'. Placing it outside the stamp too proves the strip is
        surgical, not a blanket delete of the word."""
        result = (
            "⟁ Insight recorded [ground_truth]: /Users/tony_studio/.sovereign/chronicle/"
            "insights/nape,detector/spiral_20260727_120000.jsonl — verified against the "
            "live tree (receipts: 2 verified, 0 attested)"
        )
        # Guard against a vacuous assertion: the outside occurrence must
        # actually survive the strip, or this test would pass for the
        # wrong reason.
        stripped = strip_system_stamps(result)
        assert "receipts:" not in stripped
        assert "verified against the live tree" in stripped

        self._three_non_verify_calls()
        self.daemon.observe("record_insight", {}, result, SESSION)
        honks = self._dbv()
        assert len(honks) == 1, f"Declare word outside the stamp must still honk. Got: {honks}"
        assert honks[0]["level"] == "sharp"

    # (d) the clean_verify_declare satisfied path is unchanged
    def test_satisfied_path_unchanged(self):
        self.daemon.observe("recall_insights", {}, "[]", SESSION)
        self.daemon.observe("record_insight", {}, "verified the migration is complete", SESSION)
        honks = self.daemon.current_honks(SESSION)
        assert [h for h in honks if h["pattern"] == "clean_verify_declare"], (
            f"Verify-then-declare must still produce the satisfied honk. Got: {honks}"
        )
        assert self._dbv() == []

    def test_honk_message_names_only_what_it_observed(self):
        """CHANGE 2: the message must not assert that no verification
        happened — only that none was REPORTED to Nape — and must make the
        nape_observe gap discoverable."""
        self._three_non_verify_calls()
        self.daemon.observe("record_learning", {}, "all tests passed, done", SESSION)
        obs = self._dbv()[0]["observation"]
        assert "nape_observe" in obs, "The message must name how external verifies reach Nape."
        assert "observed" in obs.lower()
        # The old wording asserted a cause it never checked.
        assert "no verify call (Read, Grep, Bash, etc.)" not in obs


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
        self.daemon.observe("spiral_status", {}, "phase: integration", SESSION)
        self.daemon.observe("record_open_thread", {}, "thread recorded", SESSION)

        # The trigger: a tool that returns "done" with no preceding verify.
        self.daemon.observe(
            "record_learning",
            {"what_happened": "tried x"},
            "done — learning recorded",
            SESSION,
        )

        honks = self.daemon.current_honks(SESSION)
        assert len(honks) >= 1
        sharp_honks = [
            h for h in honks if h["level"] == "sharp" and h["pattern"] == "declare_before_verify"
        ]
        assert len(sharp_honks) >= 1, f"Expected sharp/declare_before_verify honk. Got: {honks}"

    def test_declare_with_preceding_read_produces_satisfied_honk(self):
        """Spec: declare with preceding verify → satisfied honk (no sharp)."""
        # A Read call immediately before the declare.
        self.daemon.observe("Read", {"file_path": "/tmp/x.py"}, "line 1: pass", SESSION)
        self.daemon.observe("record_insight", {}, "complete — insight stored", SESSION)

        honks = self.daemon.current_honks(SESSION)
        sharp_honks = [
            h for h in honks if h["level"] == "sharp" and h["pattern"] == "declare_before_verify"
        ]
        assert len(sharp_honks) == 0, (
            f"Expected no sharp honk when verify precedes declare. Got: {honks}"
        )

    def test_declare_word_is_case_insensitive(self):
        """DONE, Done, done should all trigger the check."""
        self.daemon.observe("some_tool", {}, "DONE.", SESSION)
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"
        ]
        assert len(honks) >= 1

    def test_no_declare_word_no_honk(self):
        """If the result has no declare word, no honk of this type fires."""
        self.daemon.observe("record_learning", {}, "processing context...", SESSION)
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"
        ]
        assert len(honks) == 0

    def test_readonly_tool_does_not_trigger_declare_before_verify(self):
        """Read-only retrieval tools surface stored completion-language
        about other things — they are not the instance declaring its own
        work complete. Calling prior_for_turn / reflexive_surface /
        triage_threads / etc. with chronicle records that contain
        'shipped', 'resolved', etc. in the result must NOT fire a honk.

        This guards the 2026-04-25 finding from a first-hand stack probe:
        every read-only tool I called fired a sharp honk because the
        chronicle records they returned echoed completion words.
        """
        # The result simulates a real prior_for_turn output containing
        # the word "shipped" because the surfaced insight body said so.
        self.daemon.observe(
            "prior_for_turn",
            {"domain_tags": ["entropy"]},
            "PRIORS\n  insight: BaseDaemon extraction shipped 2026-04-25",
            SESSION,
        )
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"
        ]
        assert len(honks) == 0, (
            f"Read-only tool prior_for_turn should be exempt from "
            f"declare_before_verify; got {honks}"
        )

    def test_multiple_readonly_tools_all_exempt(self):
        """Spot-check several entries in the READONLY_TOOL_NAMES set."""
        for tool_name in (
            "reflexive_surface",
            "triage_threads",
            "where_did_i_leave_off",
            "comms_unread_bodies",
            "nape_summary",
            "spiral_status",
        ):
            self.daemon.observe(
                tool_name,
                {},
                "result: shipped resolved completed verified passed",
                SESSION,
            )
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"
        ]
        assert len(honks) == 0, f"All read-only tools in this batch should be exempt; got {honks}"

    def test_non_readonly_tool_still_honks_with_declare_word(self):
        """Regression: the exemption is per-tool, not blanket. A normal
        tool with completion language and no preceding verify must still
        fire — otherwise the fix would silently disable detection."""
        self.daemon.observe("record_insight", {}, "stored", SESSION)
        self.daemon.observe("record_learning", {}, "stored", SESSION)
        self.daemon.observe(
            "record_breakthrough",
            {},
            "shipped — breakthrough recorded",
            SESSION,
        )
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"
        ]
        assert len(honks) >= 1, "Non-readonly tool with declare word and no verify must still honk."

    def test_where_did_i_leave_off_does_not_fire_premature_summary(self):
        """Pre-2026-04-25 bug: where_did_i_leave_off was wrongly in
        SUMMARY_TOOL_NAMES, so EVERY arrival call fired premature_summary
        because the chronicle text it surfaces contains error-shaped
        words. The fix removed it from SUMMARY_TOOL_NAMES."""
        # Long surfaced chronicle text full of error-shaped words from
        # past records — the kind where_did_i_leave_off actually returns.
        self.daemon.observe(
            "where_did_i_leave_off",
            {},
            (
                "HANDOFFS\n  recent failure: bridge connection refused.\n"
                "OPEN THREADS: file not found, exception in parser, "
                "denied access to /etc/passwd."
            ),
            SESSION,
        )
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "premature_summary"
        ]
        assert len(honks) == 0, (
            f"where_did_i_leave_off must not trigger premature_summary; got {honks}"
        )

    def test_readonly_results_do_not_count_toward_premature_summary(self):
        """When a real summary tool fires, error-words in surfaced
        content from preceding READ-ONLY tool calls must not count as
        'recent errors.' Only actual error-shaped tool results trigger
        the honk."""
        # A read-only retrieval surfaces chronicle text with error words.
        self.daemon.observe(
            "recall_insights",
            {},
            "Past insight mentions: failure of approach X, exception trace.",
            SESSION,
        )
        # No actual errors. Real summary call should NOT fire.
        self.daemon.observe(
            "close_session",
            {},
            "session closed — reflection recorded",
            SESSION,
        )
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "premature_summary"
        ]
        assert len(honks) == 0, (
            "Read-only tool surfacing error words in stored content must "
            "not count as a 'recent error' for premature_summary"
        )

    def test_real_error_from_writing_tool_still_fires_premature_summary(self):
        """Regression: the readonly exemption must not break detection
        on actual write-tool errors."""
        self.daemon.observe(
            "Bash",
            {"command": "pytest"},
            "Error: 3 tests failed\nTraceback (most recent call last):",
            SESSION,
        )
        self.daemon.observe(
            "close_session",
            {},
            "review recorded",
            SESSION,
        )
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "premature_summary"
        ]
        assert len(honks) >= 1, "Real Bash error followed by close_session must still honk"

    def test_honks_with_history_empty(self):
        """No honks → empty + zero counts."""
        result = self.daemon.honks_with_history()
        assert result["honks"] == []
        assert result["summary"]["total"] == 0
        assert result["summary"]["zombies"] == 0

    def test_honks_with_history_joins_acks(self):
        """A honk + an ack record on a sibling acks.jsonl should be joined."""
        self.daemon.observe(
            "record_insight",
            {},
            "shipped",
            SESSION,
        )
        # No verify preceding → declare_before_verify fires.
        honks = self.daemon.current_honks(SESSION)
        assert len(honks) >= 1
        target = honks[0]
        self.daemon.ack(honk_id=target["honk_id"], note="addressed")

        result = self.daemon.honks_with_history(session_id=SESSION)
        assert result["summary"]["acked"] >= 1
        # The acked honk should have an ack record attached.
        acked_records = [h for h in result["honks"] if h["ack"] is not None]
        assert len(acked_records) >= 1
        ack = acked_records[0]["ack"]
        assert ack["note"] == "addressed"

    def test_honks_with_history_zombie_detection(self):
        """Honk acked AND still in recent priors_log = zombie."""
        # Fire a honk and ack it.
        self.daemon.observe("record_insight", {}, "shipped", SESSION)
        h = self.daemon.current_honks(SESSION)[0]
        self.daemon.ack(honk_id=h["honk_id"], note="addressed")

        # Manufacture a priors_log that includes the acked honk.
        priors_log_path = Path(self.tmpdir) / "priors_log.jsonl"
        priors_log_path.write_text(
            json.dumps(
                {
                    "timestamp": "2026-04-25T01:00:00",
                    "included_items": [f"honk:{h['honk_id']}"],
                }
            )
            + "\n"
        )

        result = self.daemon.honks_with_history(
            session_id=SESSION,
            priors_log_path=priors_log_path,
        )
        assert result["summary"]["zombies"] >= 1, (
            f"acked honk still in priors should count as zombie. summary={result['summary']}"
        )
        zombies = [h for h in result["honks"] if h["ack"] is not None and h["in_recent_priors"]]
        assert len(zombies) >= 1
        assert zombies[0]["priors_surface_count"] >= 1

    def test_honks_with_history_freshness_window(self):
        """Only the LAST N priors-log entries should count for surface_count."""
        self.daemon.observe("record_insight", {}, "shipped", SESSION)
        h = self.daemon.current_honks(SESSION)[0]

        priors_log_path = Path(self.tmpdir) / "priors_log.jsonl"
        # 5 entries; only the last 3 (default window) should be scanned.
        # The first 2 entries reference our honk — should be IGNORED.
        # The last 3 entries do NOT reference our honk.
        lines = []
        for i in range(5):
            included = [f"honk:{h['honk_id']}"] if i < 2 else ["thread:other"]
            lines.append(
                json.dumps(
                    {
                        "timestamp": f"2026-04-25T0{i}:00:00",
                        "included_items": included,
                    }
                )
            )
        priors_log_path.write_text("\n".join(lines) + "\n")

        result = self.daemon.honks_with_history(
            session_id=SESSION,
            priors_log_path=priors_log_path,
            freshness_window=3,
        )
        # Honk's priors_surface_count should be 0 because the last 3 entries
        # don't reference it.
        target = next(x for x in result["honks"] if x["honk_id"] == h["honk_id"])
        assert target["priors_surface_count"] == 0
        assert target["in_recent_priors"] is False

    def test_honks_with_history_limit_returns_newest(self):
        """limit=N should return the last N honks, newest-end."""
        for i in range(5):
            self.daemon.observe(
                "record_insight",
                {},
                f"shipped variant {i}",
                SESSION,
            )
        result = self.daemon.honks_with_history(
            session_id=SESSION,
            limit=2,
        )
        assert len(result["honks"]) == 2
        # Total summary reflects what was returned (not all-on-disk).
        assert result["summary"]["total"] == 2

    def test_honks_with_history_session_filter(self):
        self.daemon.observe("record_insight", {}, "shipped a", "session-a")
        self.daemon.observe("record_insight", {}, "shipped b", "session-b")
        a = self.daemon.honks_with_history(session_id="session-a")
        b = self.daemon.honks_with_history(session_id="session-b")
        assert all(h["session_id"] == "session-a" for h in a["honks"])
        assert all(h["session_id"] == "session-b" for h in b["honks"])

    def test_honks_with_history_age_seconds(self):
        self.daemon.observe("record_insight", {}, "shipped", SESSION)
        result = self.daemon.honks_with_history(session_id=SESSION)
        assert len(result["honks"]) >= 1
        # Just-fired honk should be a few seconds old at most.
        ages = [h["age_seconds"] for h in result["honks"]]
        assert all(a is not None and a >= 0 for a in ages)

    def test_readonly_tool_does_not_fire_repeated_mistake(self):
        """where_did_i_leave_off / prior_for_turn / etc. surface stored
        content. Their result_str containing error words is NOT them
        repeating a mistake — it's them showing chronicle text."""
        for _ in range(2):
            self.daemon.observe(
                "where_did_i_leave_off",
                {},
                "surfaced thread mentions: cannot resolve, parser failed.",
                SESSION,
            )
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "repeated_mistake"
        ]
        assert len(honks) == 0, (
            f"Read-only tool cannot 'repeat a mistake' via surfaced content; got {honks}"
        )


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
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "premature_summary"
        ]
        assert len(honks) >= 1

    def test_close_session_with_clean_history_no_honk(self):
        """If no errors in recent history, close_session should not trigger honk."""
        self.daemon.observe("Read", {"file_path": "/x.py"}, "def main(): pass", SESSION)
        self.daemon.observe("Bash", {"command": "pytest"}, "5 passed in 0.3s", SESSION)
        self.daemon.observe(
            "close_session",
            {"what_i_learned": "tests pass"},
            "session closed",
            SESSION,
        )
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "premature_summary"
        ]
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
        self.daemon.observe("some_tool", {}, "error: something failed", SESSION)
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
        result_a = self.daemon.summary(SESSION)
        result_b = self.daemon.summary(OTHER)

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

        honks = [
            h
            for h in self.daemon.current_honks(SESSION)
            if h["pattern"] == "assertion_without_evidence"
        ]
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

        honks = [
            h
            for h in self.daemon.current_honks(SESSION)
            if h["pattern"] == "assertion_without_evidence"
        ]
        assert len(honks) == 0

    def test_missing_confidence_field_no_honk(self):
        """record_insight without a confidence field skips the check gracefully."""
        self.daemon.observe(
            "record_insight",
            {"domain": "general", "content": "something interesting"},
            "insight stored",
            SESSION,
        )
        honks = [
            h
            for h in self.daemon.current_honks(SESSION)
            if h["pattern"] == "assertion_without_evidence"
        ]
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
        self.daemon.observe(
            "record_learning",
            {"what_happened": "npm test failed", "what_learned": "needs install"},
            "learning stored",
            SESSION,
        )
        self.daemon.observe("Bash", {"command": "npm test"}, "Error: module not found", SESSION)

        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "repeated_mistake"
        ]
        assert len(honks) == 0

    def test_single_error_no_uneasy_honk(self):
        """First error from a tool does not trigger repeated-mistake honk."""
        self.daemon.observe("Bash", {"command": "pytest"}, "error: import failed", SESSION)
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "repeated_mistake"
        ]
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
        assert result["unacknowledged"] == result["sharp"] + result.get("uneasy", 0) + result.get(
            "low", 0
        ), f"unacknowledged must not include satisfied. summary={result}"

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


# =============================================================================
# DEFECT A — DECLARE_WORDS is word-bounded, not substring-matched
# =============================================================================


class TestDeclareWordBoundaries:
    """The completion detector must not fire on words that merely CONTAIN a
    declare word — least of all on words meaning the OPPOSITE of completion.

    Measured on the live corpus (81,102 observations, 2026-04-23..2026-07-28):
    69 of the 262 surviving declare_before_verify honks were substring-only
    artifacts, concentrated in current_policies (23 — "successor"),
    inspect_claim (20 — "already"), reflection_ack (17) and season_review (3).
    """

    # Verbatim from the live corpus survivors, one per false-match class.
    SEMANTIC_INVERSIONS = ["unresolved", "incomplete", "undone", "unfinished"]
    SUBSTRING_FALSE_MATCHES = [
        "already",
        "abandoned",
        "undone",
        "cleanup",
        "bypassed",
        "surpassed",
        "incomplete",
        "completely",
        "prefixed",
        "unresolved",
        "successor",
    ]

    def test_semantic_inversions_do_not_match(self):
        """The load-bearing case: the completion alarm fired on words meaning
        the opposite of completion. 'unresolved' fired 'resolved', 'incomplete'
        fired 'complete', 'undone' fired 'done'."""
        for word in self.SEMANTIC_INVERSIONS:
            assert not contains_declare_word(word), (
                f"{word!r} means the opposite of completion and must not fire "
                f"the completion detector."
            )
            assert not contains_declare_word(f"the thread is {word} as of today")

    def test_all_named_substring_false_matches_are_dead(self):
        for word in self.SUBSTRING_FALSE_MATCHES:
            assert not contains_declare_word(word), f"{word!r} must no longer match"

    def test_every_declare_word_still_matches_standalone(self):
        """Positive control. Without this the test above passes for a
        predicate that matches nothing at all."""
        for word in DECLARE_WORDS:
            assert contains_declare_word(word), f"{word!r} must still match on its own"
            assert contains_declare_word(f"the migration is {word} now")

    def test_matching_is_case_insensitive(self):
        for text in ("DONE.", "Done.", "dOnE.", "VERIFIED", "Shipped"):
            assert contains_declare_word(text), f"{text!r} must match case-insensitively"

    def test_underscore_joined_declare_words_still_fire(self):
        """ANTI-`\\b` GUARD — the one that would ship a disarmed gate.

        Python's `\\b` counts `_` as a word character, so `\\bpassed\\b` does
        NOT match `all_tests_passed_shipped_and_done` and `\\bverified\\b` does
        NOT match `implementation_verified`. Both are real caller-supplied
        vantage values from the live corpus, and the second is the exact string
        the vantage evasion guard is built on. A `\\b` implementation passes a
        casual test suite while silently letting a seat disarm the gate by
        naming a field.

        Measured: `\\b` silences 12 real corpus honks the alnum boundary keeps,
        11 of them on the vantage `implementation_verified`.
        """
        assert contains_declare_word("all_tests_passed_shipped_and_done")
        assert contains_declare_word("(via implementation_verified)")
        assert contains_declare_word("verified_by")

    def test_separator_joined_domain_tags_still_fire(self):
        """Domain tags and paths join tokens with hyphens, commas and slashes.
        An instance tagging its own write 'shipped' is a declaration."""
        assert contains_declare_word("insights/sovereign-bridge,phase-1,shipped/x.jsonl")
        assert contains_declare_word("t2helix,v0.0.5,v0.1-dogfood-complete")
        assert contains_declare_word("—done—")

    def test_pattern_builder_handles_tokens_that_are_not_plain_words(self):
        """A future entry that is not a plain word must stay matchable rather
        than become unmatchable behind a boundary it can never satisfy."""
        import re as _re

        assert _re.search(_declare_word_pattern("+1"), "score +1 here")
        assert _re.search(_declare_word_pattern("not found"), "file not found")
        # A boundary IS applied on alphanumeric ends.
        assert _re.search(_declare_word_pattern("done"), "done") is not None
        assert _re.search(_declare_word_pattern("done"), "undone") is None

    def test_corpus_current_policies_shape_no_longer_honks(self):
        """End-to-end through observe(), on a verbatim corpus survivor. The
        only declare 'hit' was 'success' inside 'successor'."""
        tmpdir = tempfile.mkdtemp()
        try:
            daemon = NapeDaemon(root=tmpdir)
            daemon.observe("record_open_thread", {}, "Thread recorded", SESSION)
            daemon.observe("spiral_status", {}, "phase: integration", SESSION)
            daemon.observe("connectivity_status", {}, "all services up", SESSION)
            daemon.observe(
                "some_write_tool",
                {},
                "the successor claim is unresolved and the migration is incomplete",
                SESSION,
            )
            honks = [
                h for h in daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"
            ]
            assert honks == [], f"Substring artifacts must not honk. Got: {honks}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_gate_can_still_fail_on_the_true_positive(self):
        """STANDING LAW #2. The hand-checked true positive from the HQ replay
        must still fire after all three fixes."""
        tmpdir = tempfile.mkdtemp()
        try:
            daemon = NapeDaemon(root=tmpdir)
            daemon.observe("record_open_thread", {}, "Thread recorded", SESSION)
            daemon.observe("spiral_status", {}, "phase: integration", SESSION)
            daemon.observe("connectivity_status", {}, "all services up", SESSION)
            daemon.observe(
                "record_insight",
                {},
                "stack enhancement done, all 64 tools verified, ship it",
                SESSION,
            )
            honks = [
                h for h in daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"
            ]
            assert len(honks) == 1, f"The true positive must still honk. Got: {honks}"
            assert honks[0]["level"] == "sharp"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# =============================================================================
# DEFECT B — READONLY_TOOL_NAMES completion
# =============================================================================


class TestReadonlyToolCompletion:
    """Tools whose results echo STORED content are not the instance declaring
    its own work. Each addition was verified against its handler first."""

    # (tool, verbatim-ish corpus result shape) — every one of these fired a
    # sharp honk on the live corpus purely because the chronicle content it
    # surfaced contained completion language.
    ADDED = [
        ("spiral_inherit", "This is not your memory. Traces: the bridge shipped, tests passed"),
        ("arrive", "ARRIVE — foyer. open thread: the migration is complete"),
        ("arrive_delta", "ARRIVE_DELTA — what changed: phase 1 shipped"),
        ("arrive_lineage", "ARRIVE_LINEAGE — letter: the day we shipped it"),
        ("current_policies", "Standing policies — 7 active: ... verified ..."),
        ("inspect_claim", '{"claim_id": "abc", "integrity": "verified"}'),
        ("season_review", "SEASON REVIEW — read-only digest: 3 threads resolved"),
        ("recall_reflections", '{"count": 5, "reflections": [{"observation": "resolved"}]}'),
        ("recall_exchange", '{"integrity": "verified", "content": "..."}'),
        ("list_exchanges", '[{"id": "x", "integrity": "verified"}]'),
        ("the_ground", "CATCH LEDGER: cross-instance verification — resolved"),
    ]

    # WITHDRAWN by HQ review after adversarial review returned a blocker.
    # These were proposed as read-only and are NOT. Each must STILL honk; a
    # future pass that re-adds them will fail here rather than silently
    # opening the hole again.
    WITHDRAWN = [
        # Classified intent "write" (server.py:2128), and the caller's free-text
        # `note` is persisted (reflections.py:239) and echoed back through
        # to_dict (reflections.py:77-78) — a seat could write its completion
        # claim into the note and have it land unhonkable. Same class as the
        # `(via {vantage})` channel withdrawn last round. Third occurrence.
        (
            "reflection_ack",
            '{"ok": true, "reflection": {}, "ack_note": "migration complete, verified"}',
        ),
        # Echoes the caller's `current_focus` verbatim in its header, truncated
        # to ~40 chars — ample for a declare word. Live corpus:
        # "(focus: Conditioned Kernel RUN 00.6C live plumbi)".
        ("context_retrieve", "🎯 Context-Aware Retrieval (focus: the daemon extraction shipped)"),
        # Fails clause 1, not the caller-text clause: the result is a synthesis
        # the scribe generates THIS TURN in answer to the caller, not stored
        # content echoed back. 8 of its 48 corpus observations also carry error
        # text in-band, so exempting it would blind repeated_mistake too.
        ("ask_scribe", "The letters say the migration is done and verified."),
    ]

    # Verified as NOT read-only: their results report THIS turn's own action.
    DECLINED = [
        # "Thread {id} resolved" — the instance asserting a question is settled
        # and writing that assertion into the chronicle as ground_truth.
        (
            "resolve_thread_by_id",
            "Thread thread_20260429_023637 resolved → ground_truth insight: /x/y.jsonl",
        ),
        ("resolve_thread", "Thread resolved → ground_truth insight: /x/y.jsonl"),
        # The declare word comes from the instance's own handoff note.
        ("handoff", "Handoff written → /x.json note: Step 2 of v1.3.2 shipped"),
        ("record_insight", "stack enhancement done, all 64 tools verified"),
    ]

    def _honks_for(self, tool, result):
        tmpdir = tempfile.mkdtemp()
        try:
            daemon = NapeDaemon(root=tmpdir)
            daemon.observe("record_open_thread", {}, "Thread recorded", SESSION)
            daemon.observe("spiral_status", {}, "phase: integration", SESSION)
            daemon.observe("connectivity_status", {}, "all services up", SESSION)
            daemon.observe(tool, {}, result, SESSION)
            return [
                h for h in daemon.current_honks(SESSION) if h["pattern"] == "declare_before_verify"
            ]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.mark.parametrize("tool,result", ADDED)
    def test_added_readonly_tools_are_exempt(self, tool, result):
        assert tool in READONLY_TOOL_NAMES, f"{tool} must be in READONLY_TOOL_NAMES"
        assert self._honks_for(tool, result) == [], (
            f"{tool} echoes stored content and must not fire declare_before_verify"
        )

    @pytest.mark.parametrize("tool,result", WITHDRAWN)
    def test_withdrawn_readonly_candidates_still_honk(self, tool, result):
        """REGRESSION GUARD for the blocker. These three were proposed as
        read-only and withdrawn: two carry caller-supplied text into result_str,
        and the third returns a synthesis generated this turn rather than stored
        content. Exempting any of them hands the subject of the gate a way to
        disarm it by choosing what to write in a field. If a later pass re-adds
        one, this fails instead of the hole reopening quietly."""
        assert tool not in READONLY_TOOL_NAMES, (
            f"{tool} was withdrawn from READONLY_TOOL_NAMES — see the three-clause "
            f"membership test in nape_daemon.py before re-adding it"
        )
        honks = self._honks_for(tool, result)
        assert len(honks) == 1, f"{tool} must remain visible to the gate. Got: {honks}"
        assert honks[0]["level"] == "sharp"

    @pytest.mark.parametrize("tool,result", DECLINED)
    def test_declined_tools_still_honk(self, tool, result):
        """The exemption is per-tool and earned, not blanket. These report the
        current turn's own action and must remain visible to the gate."""
        assert tool not in READONLY_TOOL_NAMES, (
            f"{tool} reports its own action — exempting it would blind the gate"
        )
        assert len(self._honks_for(tool, result)) == 1, f"{tool} must still honk"

    def test_write_siblings_of_added_read_tools_are_not_exempt(self):
        """record_catch is the write sibling of the_ground; both must not be
        swept in together."""
        assert "the_ground" in READONLY_TOOL_NAMES
        assert "record_catch" not in READONLY_TOOL_NAMES
        assert "recall_exchange" in READONLY_TOOL_NAMES
        assert "archive_exchange" not in READONLY_TOOL_NAMES


# =============================================================================
# DEFECT C — a daemon's bookkeeping poll is not the seat verifying
# =============================================================================


class TestCommsGetAcksIsNotAVerify:
    """`comms_get_acks` is called by BaseDaemon._count_recent_unacked in a
    per-message loop by two background daemons, over a comms corpus retired
    2026-06-12. It is 60,703 of 81,101 observations — 74.85% of everything Nape
    has ever seen. Its presence in a seat's trailing-3 window decided
    sharp-vs-satisfied for whatever that seat did next.
    """

    def test_comms_get_acks_is_not_a_verify_tool(self):
        assert "comms_get_acks" not in VERIFY_TOOL_NAMES, (
            "A background daemon's bookkeeping poll is not the seat verifying anything."
        )

    def test_comms_get_acks_stays_readonly(self):
        """The two sets answer different questions. Removing it from READONLY
        too would have the 60,703-call poller self-honk."""
        assert "comms_get_acks" in READONLY_TOOL_NAMES

    def test_daemon_poll_no_longer_vouches_for_a_declaration(self):
        """The whole point of defect C: this now honks where it used to be
        marked satisfied. MORE firings, not fewer."""
        tmpdir = tempfile.mkdtemp()
        try:
            daemon = NapeDaemon(root=tmpdir)
            for _ in range(3):
                daemon.observe("comms_get_acks", {"message_id": "m1"}, "[]", SESSION)
            daemon.observe(
                "record_insight", {}, "stack enhancement done, all 64 tools verified", SESSION
            )
            honks = daemon.current_honks(SESSION)
            sharp = [h for h in honks if h["pattern"] == "declare_before_verify"]
            satisfied = [h for h in honks if h["pattern"] == "clean_verify_declare"]
            assert len(sharp) == 1, f"A daemon poll must not vouch for a seat. Got: {honks}"
            assert satisfied == [], "No satisfied verdict may rest on a daemon poll"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_real_verify_still_produces_the_satisfied_verdict(self):
        """Guard against over-correcting: a genuine seat-side verify still
        clears the honk."""
        tmpdir = tempfile.mkdtemp()
        try:
            daemon = NapeDaemon(root=tmpdir)
            daemon.observe("recall_insights", {"query": "x"}, "[]", SESSION)
            daemon.observe(
                "record_insight", {}, "stack enhancement done, all 64 tools verified", SESSION
            )
            honks = daemon.current_honks(SESSION)
            assert [h for h in honks if h["pattern"] == "clean_verify_declare"]
            assert [h for h in honks if h["pattern"] == "declare_before_verify"] == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_daemon_poll_no_longer_vouches_for_high_confidence_assertion(self):
        """VERIFY_TOOL_NAMES feeds a SECOND detector — assertion_without_evidence
        (window 5). Measured: dropping the poll takes that detector from 15 to
        76 firings over the corpus."""
        tmpdir = tempfile.mkdtemp()
        try:
            daemon = NapeDaemon(root=tmpdir)
            for _ in range(3):
                daemon.observe("comms_get_acks", {"message_id": "m1"}, "[]", SESSION)
            daemon.observe(
                "record_insight",
                {"confidence": 0.95, "content": "x"},
                "Insight recorded",
                SESSION,
            )
            honks = [
                h
                for h in daemon.current_honks(SESSION)
                if h["pattern"] == "assertion_without_evidence"
            ]
            assert len(honks) == 1, f"A daemon poll is not evidence. Got: {honks}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestReadonlyAdditionsBlastRadius:
    """READONLY_TOOL_NAMES feeds THREE detectors, not one. Adding a tool also
    exempts its result from premature_summary's error-word scan and stops it
    ever triggering repeated_mistake. That is the intended semantics for a
    read tool — an error word in echoed chronicle content is stored history,
    not a live failure in this session — but it is a real behaviour change and
    is pinned here rather than left as an unasserted claim.
    """

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.daemon = NapeDaemon(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_added_readonly_tool_no_longer_feeds_premature_summary(self):
        """A newly added read tool surfacing a stored 'failed' must not make
        the next close_session look premature."""
        self.daemon.observe(
            "recall_reflections",
            {},
            '{"reflections": [{"observation": "the April migration failed"}]}',
            SESSION,
        )
        self.daemon.observe("close_session", {}, "session closed", SESSION)
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "premature_summary"
        ]
        assert honks == [], (
            f"Echoed chronicle history is not a live error in this session. Got: {honks}"
        )

    def test_a_real_error_from_a_non_readonly_tool_still_fires_premature_summary(self):
        """The exemption must not have blanket-disabled premature_summary."""
        self.daemon.observe("Bash", {}, "Traceback: FileNotFoundError", SESSION)
        self.daemon.observe("close_session", {}, "session closed", SESSION)
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "premature_summary"
        ]
        assert len(honks) >= 1, "A real error before a summary must still honk."

    def test_added_readonly_tool_does_not_trigger_repeated_mistake(self):
        """Two reads that both surface stored error language are not the same
        mistake being repeated."""
        for _ in range(3):
            self.daemon.observe(
                "inspect_claim", {}, '{"integrity": "mismatch", "note": "not found"}', SESSION
            )
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "repeated_mistake"
        ]
        assert honks == [], f"A read tool cannot repeat its own mistake. Got: {honks}"

    def test_declined_tool_still_feeds_premature_summary(self):
        """resolve_thread_by_id was declined, so it keeps its full weight in
        the other two detectors as well."""
        self.daemon.observe("resolve_thread_by_id", {}, "ERROR: no such thread", SESSION)
        self.daemon.observe("close_session", {}, "session closed", SESSION)
        honks = [
            h for h in self.daemon.current_honks(SESSION) if h["pattern"] == "premature_summary"
        ]
        assert len(honks) >= 1, "A declined tool's real error must still count."
