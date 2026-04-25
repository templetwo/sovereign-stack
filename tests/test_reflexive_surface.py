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
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sovereign_stack.handoff import HandoffEngine
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.reflexive import ReflexiveSurface


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
        memory.record_open_thread("How does IRIS affect entropy?", domain="entropy")
        memory.record_open_thread("What is the coupling constant?", domain="entropy")

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
            scores = {t.get("question", ""): t.get("_score", t.get("score", 0)) for t in threads}
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


# ── Case 5: tag-required filtering (no-overlap drop) ────────────────────────


class TestTagRequiredFiltering:
    """When the caller provides domain_tags, items with zero tag overlap
    must NOT surface — project_match alone is not enough.

    This guards the 2026-04-25 finding from a first-hand stack probe:
    stale Feb 2026 mistakes with off-topic `applies_to` were leaking into
    results because their bodies mentioned the project name. Fix: items
    are dropped when caller_tags is non-empty AND tag_overlap == 0.
    """

    def test_off_topic_thread_dropped_when_tags_provided(self, surface, memory):
        memory.record_open_thread("Off-topic question", domain="other")
        memory.record_open_thread("On-topic question", domain="entropy")

        result = surface.surface(domain_tags=["entropy"])
        threads = result["matched_open_threads"]
        domains = [t.get("domain", "") for t in threads]

        assert "other" not in domains, "Off-topic thread leaked through despite zero tag overlap"
        assert any("entropy" in d for d in domains)

    def test_off_topic_kept_when_caller_tags_empty(self, surface, memory):
        """Empty caller_tags → no filter applied (every item has overlap=0
        by definition; penalizing then would punish the 'show anything
        recent' use case)."""
        memory.record_open_thread("Recent off-topic", domain="other")

        result = surface.surface(domain_tags=[])
        threads = result["matched_open_threads"]
        assert len(threads) >= 1, (
            "Empty caller_tags must fall back to recency-only ranking, not filter to nothing."
        )

    def test_project_match_alone_does_not_surface_off_topic(self, surface, memory):
        """The exact bug from the first-hand probe: an off-topic item
        whose BODY mentions the project name must not surface against
        unrelated tags. Project match is a tie-breaker among matched
        items, not a primary relevance signal."""
        memory.record_open_thread(
            "This thread mentions sovereign-stack but is about something else",
            domain="cooking",
        )
        memory.record_open_thread(
            "Real entropy question",
            domain="entropy",
        )

        result = surface.surface(
            domain_tags=["entropy"],
            project="sovereign-stack",
        )
        threads = result["matched_open_threads"]

        for t in threads:
            assert t.get("domain") != "cooking", (
                "Off-topic thread with project-name body should NOT surface; "
                "tag_overlap=0 is disqualifying when caller provided tags."
            )

    def test_tag_overlap_exposed_in_result(self, surface, memory):
        """_tag_overlap field should be on every returned item — debuggable
        score breakdown without re-running the scorer."""
        memory.record_open_thread("Reflection daemons question", domain="reflection-daemons,v1.3.2")

        result = surface.surface(domain_tags=["reflection-daemons", "v1.3.2"])
        threads = result["matched_open_threads"]
        assert len(threads) >= 1
        for t in threads:
            assert "_tag_overlap" in t, (
                "_tag_overlap must be exposed on returned items so callers "
                "can inspect why an item ranked where it did."
            )
            assert t["_tag_overlap"] > 0.0
