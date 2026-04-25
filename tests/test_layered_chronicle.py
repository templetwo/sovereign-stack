"""
Layered Chronicle Test Suite
Tests the three-layer memory inheritance system (R=0.46)

Layers:
  ground_truth  - Verifiable facts
  hypothesis    - One instance's interpretation
  open_thread   - Unresolved questions as invitations
"""
import json
import shutil
import tempfile
from pathlib import Path

from sovereign_stack.memory import ExperientialMemory


class TestLayeredChronicle:
    """Tests for the layered chronicle system."""

    def setup_method(self):
        """Create a fresh temp directory for each test."""
        self.tmpdir = tempfile.mkdtemp()
        self.chronicle = ExperientialMemory(root=self.tmpdir)

    def teardown_method(self):
        """Clean up temp directory."""
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── Ground Truth Layer ──

    def test_record_ground_truth(self):
        path = self.chronicle.record_insight(
            domain="infrastructure",
            content="SSE server runs on port 3434",
            layer="ground_truth",
            session_id="test_session"
        )
        assert Path(path).exists()
        with open(path) as f:
            entry = json.loads(f.readline())
        assert entry["layer"] == "ground_truth"
        assert entry["domain"] == "infrastructure"
        assert "3434" in entry["content"]

    def test_ground_truth_no_confidence(self):
        """Ground truths don't need confidence - they're facts."""
        self.chronicle.record_insight(
            domain="facts",
            content="Water is wet",
            layer="ground_truth",
            confidence=0.99,
            session_id="test"
        )
        insights = self.chronicle.recall_insights(domain="facts")
        assert len(insights) == 1
        assert "confidence" not in insights[0]

    # ── Hypothesis Layer ──

    def test_record_hypothesis_with_confidence(self):
        self.chronicle.record_insight(
            domain="theory",
            content="Consciousness is relational",
            layer="hypothesis",
            confidence=0.6,
            session_id="test"
        )
        insights = self.chronicle.recall_insights(domain="theory")
        assert len(insights) == 1
        assert insights[0]["layer"] == "hypothesis"
        assert insights[0]["confidence"] == 0.6

    def test_default_layer_is_hypothesis(self):
        """Insights default to hypothesis - interpretations must be earned."""
        self.chronicle.record_insight(
            domain="default_test",
            content="Some observation",
            session_id="test"
        )
        insights = self.chronicle.recall_insights(domain="default_test")
        assert insights[0]["layer"] == "hypothesis"

    def test_invalid_layer_defaults_to_hypothesis(self):
        self.chronicle.record_insight(
            domain="invalid_test",
            content="Some content",
            layer="nonexistent_layer",
            session_id="test"
        )
        insights = self.chronicle.recall_insights(domain="invalid_test")
        assert insights[0]["layer"] == "hypothesis"

    # ── Open Thread Layer ──

    def test_record_open_thread(self):
        path = self.chronicle.record_open_thread(
            question="Where did the pre-April 2025 conversations happen?",
            context="26-day gap in ChatGPT archives",
            domain="history",
            session_id="test"
        )
        assert Path(path).exists()
        threads = self.chronicle.get_open_threads(domain="history")
        assert len(threads) == 1
        assert "pre-April" in threads[0]["question"]
        assert threads[0]["resolved"] is False

    def test_resolve_thread(self):
        self.chronicle.record_open_thread(
            question="What port does the SSE server use?",
            domain="infra",
            session_id="session_1"
        )
        # Resolve it
        self.chronicle.resolve_thread(
            domain="infra",
            question_fragment="port",
            resolution="SSE server uses port 3434",
            session_id="session_2"
        )
        # Thread should be resolved
        open_threads = self.chronicle.get_open_threads(domain="infra")
        assert len(open_threads) == 0

        # Resolution should become ground truth
        insights = self.chronicle.recall_insights(
            domain="infra", layer_filter="ground_truth"
        )
        assert len(insights) == 1
        assert "3434" in insights[0]["content"]

    def test_get_open_threads_all_domains(self):
        self.chronicle.record_open_thread(
            question="Q1", domain="physics", session_id="t"
        )
        self.chronicle.record_open_thread(
            question="Q2", domain="consciousness", session_id="t"
        )
        threads = self.chronicle.get_open_threads()
        assert len(threads) == 2

    def test_open_threads_ordered_newest_first(self):
        self.chronicle.record_open_thread(
            question="First question", domain="test", session_id="t1"
        )
        self.chronicle.record_open_thread(
            question="Second question", domain="test", session_id="t2"
        )
        threads = self.chronicle.get_open_threads(domain="test")
        assert "Second" in threads[0]["question"]

    # ── Layer Filtering ──

    def test_recall_with_layer_filter(self):
        self.chronicle.record_insight(
            domain="mixed", content="Fact A",
            layer="ground_truth", session_id="t"
        )
        self.chronicle.record_insight(
            domain="mixed", content="Guess B",
            layer="hypothesis", confidence=0.5, session_id="t"
        )
        gt = self.chronicle.recall_insights(
            domain="mixed", layer_filter="ground_truth"
        )
        hyp = self.chronicle.recall_insights(
            domain="mixed", layer_filter="hypothesis"
        )
        assert len(gt) == 1
        assert gt[0]["content"] == "Fact A"
        assert len(hyp) == 1
        assert hyp[0]["content"] == "Guess B"

    def test_recall_without_filter_returns_all(self):
        self.chronicle.record_insight(
            domain="all", content="Fact",
            layer="ground_truth", session_id="t"
        )
        self.chronicle.record_insight(
            domain="all", content="Guess",
            layer="hypothesis", session_id="t"
        )
        all_insights = self.chronicle.recall_insights(domain="all")
        assert len(all_insights) == 2

    # ── Inheritable Context ──

    def test_get_inheritable_context_structure(self):
        self.chronicle.record_insight(
            domain="test", content="Ground fact",
            layer="ground_truth", session_id="t"
        )
        self.chronicle.record_insight(
            domain="test", content="Hypothesis",
            layer="hypothesis", confidence=0.7, session_id="t"
        )
        self.chronicle.record_open_thread(
            question="Open question?", domain="test", session_id="t"
        )

        ctx = self.chronicle.get_inheritable_context()

        assert "ground_truth" in ctx
        assert "hypotheses" in ctx
        assert "open_threads" in ctx
        assert "coupling_advisory" in ctx
        assert "inheritance_timestamp" in ctx

    def test_inheritable_context_coupling_advisory(self):
        ctx = self.chronicle.get_inheritable_context()
        assert "R=0.46" in ctx["coupling_advisory"]
        assert "Facts travel" in ctx["coupling_advisory"]

    def test_hypotheses_flagged_in_context(self):
        self.chronicle.record_insight(
            domain="test", content="A guess",
            layer="hypothesis", confidence=0.5, session_id="t"
        )
        ctx = self.chronicle.get_inheritable_context()
        assert len(ctx["hypotheses"]) == 1
        assert "_note" in ctx["hypotheses"][0]
        assert "not settled truth" in ctx["hypotheses"][0]["_note"]

    def test_open_threads_flagged_in_context(self):
        self.chronicle.record_open_thread(
            question="Why?", domain="test", session_id="t"
        )
        ctx = self.chronicle.get_inheritable_context()
        assert len(ctx["open_threads"]) == 1
        assert "_note" in ctx["open_threads"][0]
        assert "discover your own answer" in ctx["open_threads"][0]["_note"]

    def test_empty_chronicle_returns_empty_context(self):
        ctx = self.chronicle.get_inheritable_context()
        assert ctx["ground_truth"] == []
        assert ctx["hypotheses"] == []
        assert ctx["open_threads"] == []
        assert "R=0.46" in ctx["coupling_advisory"]

    # ── Edge Cases ──

    def test_resolve_nonexistent_thread(self):
        """Resolving a thread that doesn't exist should not crash."""
        path = self.chronicle.resolve_thread(
            domain="missing",
            question_fragment="nothing",
            resolution="Found it anyway",
            session_id="t"
        )
        assert Path(path).exists()

    def test_multiple_threads_same_domain(self):
        for i in range(5):
            self.chronicle.record_open_thread(
                question=f"Question {i}", domain="same", session_id="t"
            )
        threads = self.chronicle.get_open_threads(domain="same")
        assert len(threads) == 5

    def test_thread_limit(self):
        for i in range(20):
            self.chronicle.record_open_thread(
                question=f"Q{i}", domain="limit_test", session_id="t"
            )
        threads = self.chronicle.get_open_threads(domain="limit_test", limit=5)
        assert len(threads) == 5

    def test_partial_resolve_leaves_others_open(self):
        """Resolving one thread should not affect others."""
        self.chronicle.record_open_thread(
            question="What is X?", domain="partial", session_id="t"
        )
        self.chronicle.record_open_thread(
            question="What is Y?", domain="partial", session_id="t"
        )
        self.chronicle.resolve_thread(
            domain="partial",
            question_fragment="What is X",
            resolution="X is 42",
            session_id="t2"
        )
        open_threads = self.chronicle.get_open_threads(domain="partial")
        assert len(open_threads) == 1
        assert "Y" in open_threads[0]["question"]

    # ── Atomic Threads + Back-Reference (Coherence Foundation) ──

    def test_record_open_thread_atomizes_bundled_question(self):
        """A bundled "(1) foo (2) bar (3) baz" question splits into 3 atomic threads."""
        self.chronicle.record_open_thread(
            question="Remaining items: (1) Revoke token. (2) Rotate SSH key. (3) Install gitleaks.",
            domain="atom_test",
            session_id="t"
        )
        threads = self.chronicle.get_open_threads(domain="atom_test")
        assert len(threads) == 3
        questions = [t["question"] for t in threads]
        assert any("Revoke token" in q for q in questions)
        assert any("Rotate SSH key" in q for q in questions)
        assert any("Install gitleaks" in q for q in questions)

    def test_single_parenthetical_does_not_atomize(self):
        """A question with a single "(1)" or parenthetical should not split."""
        self.chronicle.record_open_thread(
            question="What does (1) mean in this context (see spec)?",
            domain="no_split",
            session_id="t"
        )
        threads = self.chronicle.get_open_threads(domain="no_split")
        assert len(threads) == 1

    def test_non_sequential_numbers_do_not_atomize(self):
        """'(1) foo (3) baz' is not a bundle — sequence must start at 1 and be dense."""
        self.chronicle.record_open_thread(
            question="See (1) foo and (3) baz for details.",
            domain="no_split2",
            session_id="t"
        )
        threads = self.chronicle.get_open_threads(domain="no_split2")
        assert len(threads) == 1

    def test_every_thread_gets_thread_id(self):
        """New threads have a stable thread_id field from record time."""
        self.chronicle.record_open_thread(
            question="What is the ID of this thread?",
            domain="id_test",
            session_id="t"
        )
        threads = self.chronicle.get_open_threads(domain="id_test")
        assert len(threads) == 1
        assert threads[0].get("thread_id", "").startswith("thread_")

    def test_thread_id_is_deterministic_per_question(self):
        """Same question text yields a stable hash suffix (differs only by timestamp)."""
        from datetime import datetime

        from sovereign_stack.memory import _generate_thread_id
        t = datetime(2026, 4, 19, 12, 0, 0)
        a = _generate_thread_id("Revoke the token", t)
        b = _generate_thread_id("Revoke the token", t)
        c = _generate_thread_id("Rotate the key", t)
        assert a == b
        assert a != c
        assert a.startswith("thread_20260419_120000_")

    def test_resolve_thread_writes_back_reference_insight(self):
        """resolve_thread's ground_truth insight carries resolved_thread_id."""
        self.chronicle.record_open_thread(
            question="What port does SSE use?",
            domain="bref",
            session_id="s1"
        )
        threads = self.chronicle.get_open_threads(domain="bref")
        thread_id = threads[0]["thread_id"]

        self.chronicle.resolve_thread(
            domain="bref",
            question_fragment="port does SSE",
            resolution="SSE uses port 3434",
            session_id="s2"
        )
        gt_insights = self.chronicle.recall_insights(
            domain="bref", layer_filter="ground_truth"
        )
        assert len(gt_insights) == 1
        assert gt_insights[0].get("resolved_thread_id") == thread_id

    def test_resolve_thread_sets_resolved_at_and_resolved_by(self):
        """Resolution writes resolved_at timestamp and resolved_by session to the thread."""
        self.chronicle.record_open_thread(
            question="When was this resolved?",
            domain="resolved_at_test",
            session_id="s1"
        )
        self.chronicle.resolve_thread(
            domain="resolved_at_test",
            question_fragment="resolved",
            resolution="Just now",
            session_id="s2"
        )
        # Read raw thread file
        path = self.chronicle.threads_dir / "resolved_at_test.jsonl"
        with open(path) as f:
            entry = json.loads(f.readline())
        assert entry["resolved"] is True
        assert entry["resolved_by"] == "s2"
        assert "resolved_at" in entry

    def test_resolve_thread_by_id_happy_path(self):
        """resolve_thread_by_id targets the exact thread and marks it resolved."""
        self.chronicle.record_open_thread(
            question="First question",
            domain="byid",
            session_id="s1"
        )
        self.chronicle.record_open_thread(
            question="Second question",
            domain="byid",
            session_id="s1"
        )
        threads = self.chronicle.get_open_threads(domain="byid")
        target_id = [t["thread_id"] for t in threads if "First" in t["question"]][0]

        path = self.chronicle.resolve_thread_by_id(
            thread_id=target_id,
            resolution="First answered",
            session_id="s2"
        )
        assert path  # non-empty path means resolution wrote an insight
        remaining = self.chronicle.get_open_threads(domain="byid")
        assert len(remaining) == 1
        assert "Second" in remaining[0]["question"]

    def test_resolve_thread_by_id_missing_returns_empty(self):
        """Unknown thread_id returns an empty string rather than crashing."""
        path = self.chronicle.resolve_thread_by_id(
            thread_id="thread_19700101_000000_deadbeef",
            resolution="nope",
            session_id="s1"
        )
        assert path == ""

    def test_legacy_thread_gets_thread_id_on_read(self):
        """A thread file written before thread_id existed should backfill on read."""
        # Simulate a legacy JSONL entry without thread_id
        legacy_path = self.chronicle.threads_dir / "legacy.jsonl"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_entry = {
            "timestamp": "2026-02-01T12:00:00",
            "question": "Legacy question with no id",
            "context": "",
            "domain": "legacy",
            "session_id": "old",
            "layer": "open_thread",
            "resolved": False,
        }
        with open(legacy_path, 'w') as f:
            f.write(json.dumps(legacy_entry) + '\n')

        threads = self.chronicle.get_open_threads(domain="legacy")
        assert len(threads) == 1
        assert threads[0]["thread_id"].startswith("thread_20260201_120000_")

    # ── check_mistakes Text Search (Same Class as recall_insights) ──

    def test_check_mistakes_matches_what_happened_content(self):
        """Context terms that appear only in what_happened still match."""
        self.chronicle.record_learning(
            what_happened="The GitHub token leaked via crontab",
            what_learned="Never put secrets in plaintext scheduled scripts",
            applies_to="security,operations",
            session_id="t"
        )
        # Context does NOT overlap with applies_to keywords
        hits = self.chronicle.check_mistakes("crontab leak review")
        assert len(hits) == 1

    def test_check_mistakes_matches_what_learned_content(self):
        """Context terms that appear only in what_learned still match."""
        self.chronicle.record_learning(
            what_happened="A thing happened",
            what_learned="Avoid plaintext credentials in scheduled jobs",
            applies_to="ops",
            session_id="t"
        )
        hits = self.chronicle.check_mistakes("credential audit")
        assert len(hits) == 1

    def test_check_mistakes_short_terms_ignored(self):
        """Terms shorter than 3 chars don't cause false positives."""
        self.chronicle.record_learning(
            what_happened="Something irrelevant",
            what_learned="Nothing relevant",
            applies_to="misc",
            session_id="t"
        )
        # 'a' and 'of' are < 3 chars; 'xyz' is not in any field
        hits = self.chronicle.check_mistakes("a of xyz")
        assert len(hits) == 0

    def test_check_mistakes_still_matches_on_applies_to(self):
        """Backward-compat: matches on applies_to tag still work."""
        self.chronicle.record_learning(
            what_happened="X",
            what_learned="Y",
            applies_to="security,guardian",
            session_id="t"
        )
        hits = self.chronicle.check_mistakes("guardian review")
        assert len(hits) == 1
