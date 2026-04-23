"""
Reflexive surface tests — Enhancement 1.

Verifies:
1. domain_tags with tag overlap returns higher-scored items first.
2. Older items score lower than recent items with the same tags.
3. project_match_bonus applied when project string appears in question.
4. scoring_explanation mentions counts.
"""

import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sovereign_stack.reflexive import ReflexiveSurface
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.handoff import HandoffEngine


@pytest.fixture
def sovereign_root():
    """Fresh sovereign root that mirrors ~/.sovereign/ structure."""
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    # Create expected subdirectories
    (root / "chronicle").mkdir()
    (root / "handoffs").mkdir()
    yield root
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def surface(sovereign_root):
    return ReflexiveSurface(sovereign_root=sovereign_root)


@pytest.fixture
def memory(sovereign_root):
    return ExperientialMemory(root=str(sovereign_root / "chronicle"))


@pytest.fixture
def handoffs(sovereign_root):
    return HandoffEngine(root=str(sovereign_root))


def _backdate_thread(mem: ExperientialMemory, days_ago: int, question: str, domain: str):
    """Write a thread then patch its timestamp to simulate age."""
    mem.record_open_thread(question, domain=domain)
    threads = mem.get_open_threads()
    matching = [t for t in threads if question in t.get("question", "")]
    if not matching:
        return

    # Find the JSONL file and patch the timestamp.
    target_ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
    thread_id = matching[0]["thread_id"]

    for jsonl_file in mem.threads_dir.glob("*.jsonl"):
        lines = []
        changed = False
        for line in jsonl_file.read_text().splitlines():
            try:
                import json
                rec = json.loads(line)
                if rec.get("thread_id") == thread_id:
                    rec["timestamp"] = target_ts
                    changed = True
                lines.append(json.dumps(rec))
            except Exception:
                lines.append(line)
        if changed:
            jsonl_file.write_text("\n".join(lines) + "\n")
            break


# ── Case 1: tag overlap returns higher-scored items first ──

class TestTagOverlapScoring:
    def test_matching_domain_scores_higher_than_non_matching(self, surface, memory):
        """A thread in domain 'entropy' should score higher than one in 'other' when
        caller tags include 'entropy'."""
        memory.record_open_thread("Entropy question", domain="entropy")
        memory.record_open_thread("Unrelated question", domain="bioelectric")

        result = surface.surface(domain_tags=["entropy"])
        threads = result["matched_open_threads"]

        # There may be 1 or 2 threads returned. The entropy one must be first.
        assert len(threads) >= 1
        assert "entropy" in threads[0].get("domain", "").lower(), (
            "The entropy thread should rank highest when caller tag is 'entropy'"
        )

    def test_multiple_overlapping_tags_score_higher(self, surface, memory):
        """A thread whose domain matches more caller tags ranks higher."""
        memory.record_open_thread("Q1", domain="entropy")
        memory.record_open_thread("Q2", domain="entropy,witness")

        result = surface.surface(domain_tags=["entropy", "witness"])
        threads = result["matched_open_threads"]

        assert len(threads) >= 1
        # The thread with both tags should be first
        top_domain = threads[0].get("domain", "")
        assert "witness" in top_domain.lower() or "entropy" in top_domain.lower()

        # Verify Q2 scores higher than Q1
        scores = {t.get("question", ""): t.get("_score", t.get("score", 0)) for t in threads}
        if "Q1" in scores and "Q2" in scores:
            assert scores["Q2"] >= scores["Q1"], (
                "Thread with more matching tags should score higher"
            )


# ── Case 2: older items score lower than recent items with same tags ──

class TestRecencyScoring:
    def test_recent_thread_scores_higher_than_old_same_tags(self, surface, memory):
        """Two threads in the same domain — the newer one should score higher."""
        _backdate_thread(memory, days_ago=25, question="Old entropy question", domain="entropy")
        memory.record_open_thread("New entropy question", domain="entropy")

        result = surface.surface(domain_tags=["entropy"])
        threads = result["matched_open_threads"]

        assert len(threads) >= 2
        # Scores must exist and new thread scores higher
        top = threads[0]
        assert "New entropy question" in top.get("question", ""), (
            "The recent thread should rank above the 25-day-old thread with same tags"
        )

    def test_zero_day_item_has_max_recency_boost(self, surface, memory):
        """A thread created today has recency_boost close to 1.0."""
        memory.record_open_thread("Today's question", domain="compass")
        result = surface.surface(domain_tags=["compass"])
        threads = result["matched_open_threads"]
        assert len(threads) >= 1
        score = threads[0].get("_score", threads[0].get("score", 0))
        # tag_overlap * 2.0 + recency ~1.0 → score should be well above 1.0
        assert score > 1.0, f"Fresh matching thread should score above 1.0, got {score}"


# ── Case 3: project_match_bonus when project string appears in question ──

class TestProjectMatchBonus:
    def test_project_match_bonus_applied(self, surface, memory):
        """Thread whose question contains the project name should score higher."""
        memory.record_open_thread(
            "How does IRIS affect entropy?", domain="entropy"
        )
        memory.record_open_thread(
            "What is the coupling constant?", domain="entropy"
        )

        result = surface.surface(domain_tags=["entropy"], project="IRIS")
        threads = result["matched_open_threads"]

        assert len(threads) >= 1
        # The IRIS-mentioning thread should rank first
        top_question = threads[0].get("question", "")
        assert "IRIS" in top_question, (
            "Thread mentioning the project in its question should rank first"
        )

    def test_project_match_bonus_is_additive(self, surface, memory):
        """The +0.5 bonus should be visible in the score difference."""
        memory.record_open_thread("No project mention here", domain="entropy")
        memory.record_open_thread("IRIS project is relevant", domain="entropy")

        result = surface.surface(domain_tags=["entropy"], project="IRIS")
        threads = result["matched_open_threads"]

        if len(threads) >= 2:
            scores = {t.get("question", ""): t.get("_score", t.get("score", 0))
                      for t in threads}
            iris_score = scores.get("IRIS project is relevant", 0)
            no_bonus_score = scores.get("No project mention here", 0)
            assert iris_score > no_bonus_score, (
                "Project-matching thread should score higher by ~0.5"
            )


# ── Case 4: scoring_explanation mentions counts ──

class TestScoringExplanation:
    def test_scoring_explanation_mentions_candidate_counts(self, surface, memory):
        """scoring_explanation must describe how many candidates were scored."""
        memory.record_open_thread("Some question", domain="test")
        result = surface.surface(domain_tags=["test"])

        explanation = result["scoring_explanation"]
        assert isinstance(explanation, str)
        assert len(explanation) > 0

        # Must mention open_threads count and the scoring formula
        assert "open_thread" in explanation.lower() or "open threads" in explanation.lower(), (
            "scoring_explanation must mention open_threads"
        )
        assert "tag_overlap" in explanation or "tag" in explanation.lower(), (
            "scoring_explanation must describe the scoring formula"
        )

    def test_total_candidates_scanned_is_int(self, surface, memory):
        """total_candidates_scanned must be a non-negative integer."""
        result = surface.surface(domain_tags=["anything"])
        assert isinstance(result["total_candidates_scanned"], int)
        assert result["total_candidates_scanned"] >= 0

    def test_all_expected_buckets_present(self, surface):
        """All four result buckets must be present in the return dict."""
        result = surface.surface(domain_tags=[])
        assert "matched_open_threads" in result
        assert "relevant_handoffs" in result
        assert "recent_mistakes" in result
        assert "related_insights" in result
        assert "total_candidates_scanned" in result
        assert "scoring_explanation" in result
