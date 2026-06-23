"""
Instrumented tests for the three navigability fixes (v1.7.7).

Each test is designed so the DEFAULT behavior would FAIL to surface
the target, proving the new param actually changes the result.

FIX 1 — domain_contains: substring filter that narrows compound dirs.
FIX 2 — get_open_threads total/has_more: exposes the silent cap.
FIX 3 — order: oldest-first reaches the floor; relevance ranks old exact
         match above fresh weak match.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sovereign_stack.memory import ExperientialMemory

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _ts(delta_days: int = 0) -> str:
    """Return an ISO8601 UTC timestamp offset from today by delta_days."""
    dt = datetime.now(timezone.utc) + timedelta(days=delta_days)
    return dt.isoformat()


class TestNavigabilityFixes:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nav-fix-")
        self.mem = ExperientialMemory(root=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # FIX 1 — domain_contains narrows compound domain dirs
    # -----------------------------------------------------------------------

    def test_domain_contains_narrows_compound_dir(self):
        """domain_contains='frank-jones' returns entries from
        'frank-jones,greene-street,cbd' and NOT from unrelated domains.

        Proof: exact domain= match would fail (compound dir name != tag).
        """
        # Write two entries: one in a compound domain, one unrelated.
        self.mem.record_insight(
            "frank-jones,greene-street,cbd",
            "compound domain entry",
            0.6,
            "sess1",
        )
        self.mem.record_insight(
            "unrelated-domain",
            "should not appear",
            0.6,
            "sess1",
        )

        # Exact domain= would NOT find the compound dir entry.
        self.mem.recall_insights(domain="frank-jones")
        # The exact domain search falls back to all-domains when the path
        # doesn't exist — so we test that domain_contains narrows while
        # the unnarrowed all-domains returns both.
        all_results = self.mem.recall_insights()
        assert len(all_results) == 2, "baseline: both domains visible"

        # domain_contains should return ONLY the compound dir entry.
        narrowed = self.mem.recall_insights(domain_contains="frank-jones")
        assert len(narrowed) == 1, (
            f"domain_contains='frank-jones' should return 1 entry; got {len(narrowed)}"
        )
        assert "compound domain entry" in narrowed[0]["content"]

    def test_domain_contains_case_insensitive(self):
        """Substring match is case-insensitive."""
        self.mem.record_insight("FrAnK-JoNeS,CBD", "mixed case domain", 0.5, "sess1")
        results = self.mem.recall_insights(domain_contains="frank-jones")
        assert len(results) == 1
        assert "mixed case domain" in results[0]["content"]

    def test_domain_contains_no_match_returns_empty(self):
        """A non-existent tag returns an empty list, not all entries."""
        self.mem.record_insight("alpha-domain", "alpha entry", 0.5, "sess1")
        results = self.mem.recall_insights(domain_contains="nonexistent-tag")
        assert results == []

    def test_get_open_threads_domain_contains_narrows(self):
        """domain_contains narrows get_open_threads too.

        Proof: seeding threads under a compound domain file; an unrelated
        thread must NOT appear in the narrowed result.
        """
        self.mem.record_open_thread(
            "does compound domain filtering work?",
            "context",
            "frank-jones,cbd",
            "sess1",
        )
        self.mem.record_open_thread(
            "unrelated thread question?",
            "context",
            "unrelated-topic",
            "sess1",
        )

        all_threads = self.mem.get_open_threads(limit=50)
        assert len(all_threads) == 2, "baseline: both threads visible"

        narrowed = self.mem.get_open_threads(domain_contains="frank-jones", limit=50)
        assert len(narrowed) == 1
        assert "compound" in narrowed[0]["question"]

    # -----------------------------------------------------------------------
    # FIX 2 — get_open_threads total + has_more (silent cap exposed)
    # -----------------------------------------------------------------------

    def test_get_open_threads_total_exceeds_limit(self):
        """When total > limit, has_more=True and total reflects the real count.

        Proof: with_total=False (default) returns a list of `limit` items
        with no count information; the test confirms that with_total=True
        surfaces total > len(returned) when capped.
        """
        # Seed 14 threads but request limit=10.
        for i in range(14):
            self.mem.record_open_thread(
                f"seed question {i:02d}: is total reported?",
                "seed context",
                "general",
                "sess1",
            )

        default_result = self.mem.get_open_threads(limit=10)
        assert isinstance(default_result, list), "default return is still a plain list"
        assert len(default_result) == 10

        result = self.mem.get_open_threads(limit=10, with_total=True)
        assert isinstance(result, dict)
        assert result["total"] == 14, f"expected total=14, got {result['total']}"
        assert result["has_more"] is True
        assert len(result["threads"]) == 10
        assert result["offset"] == 0

    def test_get_open_threads_has_more_false_when_all_fit(self):
        """has_more=False when total <= limit."""
        for i in range(5):
            self.mem.record_open_thread(f"fits in window {i}?", "ctx", "general", "sess1")

        result = self.mem.get_open_threads(limit=10, with_total=True)
        assert result["total"] == 5
        assert result["has_more"] is False
        assert len(result["threads"]) == 5

    def test_get_open_threads_offset_pagination(self):
        """offset skips the first N entries; combining pages covers all."""
        for i in range(7):
            self.mem.record_open_thread(f"page question {i:02d}?", "ctx", "general", "sess1")

        page1 = self.mem.get_open_threads(limit=4, offset=0, with_total=True)
        page2 = self.mem.get_open_threads(limit=4, offset=4, with_total=True)

        assert page1["total"] == 7
        assert page1["has_more"] is True
        assert len(page1["threads"]) == 4

        assert page2["total"] == 7
        assert page2["has_more"] is False
        assert len(page2["threads"]) == 3

        # No overlap between pages.
        ids1 = {t.get("thread_id") for t in page1["threads"]}
        ids2 = {t.get("thread_id") for t in page2["threads"]}
        assert ids1.isdisjoint(ids2), "pages must not overlap"

    def test_get_open_threads_default_unchanged(self):
        """Default (with_total=False) still returns a plain list — backward-compat."""
        self.mem.record_open_thread("a question?", "ctx", "general", "sess1")
        result = self.mem.get_open_threads()
        assert isinstance(result, list)
        assert len(result) == 1

    # -----------------------------------------------------------------------
    # FIX 3a — oldest-first reaches the floor
    # -----------------------------------------------------------------------

    def test_oldest_first_surfaces_floor_entry(self):
        """oldest-first returns the chronologically earliest entry at index 0.

        Proof: we seed N+1 entries (so newest-first with limit=N would NOT
        include the oldest entry), then confirm:
          - default (newest) OMITS the oldest entry (verifies the proof scenario)
          - order='oldest' RETURNS the oldest entry as the first result.
        """
        # Oldest entry — predates everything by 100 days.
        old_ts = _ts(-100)
        floor_entry = {
            "domain": "test-floor",
            "content": "THE FLOOR ENTRY",
            "intensity": 0.5,
            "layer": "hypothesis",
            "timestamp": old_ts,
            "session_id": "sess-old",
        }
        # Write directly to the insights dir to control timestamp.
        domain_dir = Path(self.tmpdir) / "insights" / "test-floor"
        domain_dir.mkdir(parents=True, exist_ok=True)
        import json

        (domain_dir / "floor.jsonl").write_text(json.dumps(floor_entry) + "\n")

        # Seed 10 newer entries so the floor is buried at position 11.
        for i in range(10):
            self.mem.record_insight(
                "test-floor",
                f"recent entry {i:02d}",
                0.5,
                "sess-new",
            )

        # Default newest-first, limit=10 — floor entry should NOT be in results.
        newest_results = self.mem.recall_insights(limit=10)
        newest_contents = [r["content"] for r in newest_results]
        assert "THE FLOOR ENTRY" not in newest_contents, (
            "Proof setup failed: floor entry should be absent from newest-10"
        )

        # order='oldest', limit=1 — floor entry MUST be at position 0.
        oldest_results = self.mem.recall_insights(order="oldest", limit=1)
        assert len(oldest_results) == 1
        assert oldest_results[0]["content"] == "THE FLOOR ENTRY", (
            f"oldest-first should surface the floor entry; got: {oldest_results[0]['content']}"
        )

    # -----------------------------------------------------------------------
    # FIX 3b — relevance mode ranks old exact match above fresh weak match
    # -----------------------------------------------------------------------

    def test_relevance_mode_ranks_old_exact_match_above_fresh_weak(self):
        """order='relevance' sorts by term-match count, not by recency.

        Setup:
          - OLD entry: all three query terms present in content (strong match).
          - 5 FRESH entries: only one query term present (weak match).
        Limit = 3 (smaller than fresh count, so newest-first would bury the old one).

        Expected: order='relevance' returns the old strong entry first.
        Proof: newest-first order omits it (it's entry #6 overall, beyond limit).
        """
        import json

        query = "alpha beta gamma"
        query_terms = ["alpha", "beta", "gamma"]

        # OLD strong-match entry (100 days ago).
        old_ts = _ts(-100)
        strong_entry = {
            "domain": "relevance-test",
            "content": "alpha beta gamma all three terms present in this old entry",
            "intensity": 0.5,
            "layer": "hypothesis",
            "timestamp": old_ts,
            "session_id": "sess-old",
        }
        domain_dir = Path(self.tmpdir) / "insights" / "relevance-test"
        domain_dir.mkdir(parents=True, exist_ok=True)
        (domain_dir / "old_strong.jsonl").write_text(json.dumps(strong_entry) + "\n")

        # 5 FRESH weak-match entries (each has only 1 of the 3 query terms).
        for i in range(5):
            term = query_terms[i % 3]
            self.mem.record_insight(
                "relevance-test",
                f"fresh entry {i:02d} has only {term} as a match",
                0.5,
                "sess-fresh",
            )

        total = self.mem.recall_insights(query=query, limit=20)
        assert len(total) == 6, f"should have 6 matching entries total; got {len(total)}"

        # Verify proof: newest-first with limit=5 buries the old strong entry.
        newest_5 = self.mem.recall_insights(query=query, limit=5)
        newest_contents = [r["content"] for r in newest_5]
        assert not any("all three terms" in c for c in newest_contents), (
            "Proof setup failed: old strong entry should be absent from newest-5"
        )

        # order='relevance', limit=3 — old strong entry must be first.
        relevance_results = self.mem.recall_insights(query=query, order="relevance", limit=3)
        assert len(relevance_results) >= 1
        assert "all three terms" in relevance_results[0]["content"], (
            f"relevance mode should return old strong match first; "
            f"got: {relevance_results[0]['content']}"
        )

    def test_relevance_strips_internal_match_count(self):
        """The _match_count annotation used for sorting must not leak into results."""
        self.mem.record_insight(
            "clean-test",
            "alpha beta gamma content here",
            0.5,
            "sess1",
        )
        results = self.mem.recall_insights(query="alpha beta gamma", order="relevance")
        assert len(results) == 1
        assert "_match_count" not in results[0], (
            "_match_count internal key must be stripped before returning"
        )

    def test_order_newest_default_unchanged(self):
        """order='newest' (default) returns newest entry first — backward-compat."""
        import json

        old_ts = _ts(-50)
        old_entry = {
            "domain": "order-test",
            "content": "old entry",
            "intensity": 0.5,
            "layer": "hypothesis",
            "timestamp": old_ts,
            "session_id": "sess1",
        }
        domain_dir = Path(self.tmpdir) / "insights" / "order-test"
        domain_dir.mkdir(parents=True, exist_ok=True)
        (domain_dir / "old.jsonl").write_text(json.dumps(old_entry) + "\n")

        self.mem.record_insight("order-test", "new entry", 0.5, "sess1")

        results = self.mem.recall_insights()
        assert results[0]["content"] == "new entry", "Default order must still be newest-first"

    def test_relevance_without_query_falls_back_to_newest(self):
        """order='relevance' with no query degrades to newest-first."""
        import json

        old_ts = _ts(-50)
        old_entry = {
            "domain": "fallback-test",
            "content": "old entry no query",
            "intensity": 0.5,
            "layer": "hypothesis",
            "timestamp": old_ts,
            "session_id": "sess1",
        }
        domain_dir = Path(self.tmpdir) / "insights" / "fallback-test"
        domain_dir.mkdir(parents=True, exist_ok=True)
        (domain_dir / "old.jsonl").write_text(json.dumps(old_entry) + "\n")

        self.mem.record_insight("fallback-test", "new entry no query", 0.5, "sess1")

        results = self.mem.recall_insights(order="relevance")
        # No query terms → no filtering, all entries returned, fallback to newest.
        assert len(results) == 2
        assert results[0]["content"] == "new entry no query"
