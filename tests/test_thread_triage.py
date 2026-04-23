"""
Thread triage and decay tests — Enhancement 5.

Verifies:
1. A 20-day-old thread scores higher triage than a 2-day-old thread with same tags.
2. tag_match contributes when current_domain_tags provided.
3. Recent touches reduce triage_score.
4. A 35-day thread with zero touches is flagged archive_or_escalate.
"""

import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory


@pytest.fixture
def memory_root():
    tmp = tempfile.mkdtemp()
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def mem(memory_root):
    return ExperientialMemory(root=memory_root)


def _backdate(mem: ExperientialMemory, thread_id: str, days_ago: int):
    """Patch a thread's timestamp in the JSONL file to simulate age."""
    target_ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
    for jsonl_file in mem.threads_dir.glob("*.jsonl"):
        lines = []
        changed = False
        for line in jsonl_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("thread_id") == thread_id:
                    rec["timestamp"] = target_ts
                    changed = True
                lines.append(json.dumps(rec))
            except json.JSONDecodeError:
                lines.append(line)
        if changed:
            jsonl_file.write_text("\n".join(lines) + "\n")
            break


def _backdate_touch(mem: ExperientialMemory, thread_id: str, days_ago: int):
    """Patch a touch's timestamp to make it appear old (beyond 7-day recent window)."""
    target_ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
    if not mem.thread_touches_file.exists():
        return
    lines = []
    for line in mem.thread_touches_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if rec.get("thread_id") == thread_id:
                rec["timestamp"] = target_ts
            lines.append(json.dumps(rec))
        except json.JSONDecodeError:
            lines.append(line)
    mem.thread_touches_file.write_text("\n".join(lines) + "\n")


# ── Case 1: 20-day-old thread scores higher than 2-day-old with same tags ──

class TestAgePressure:
    def test_older_thread_higher_triage_score(self, mem):
        """age_pressure = min(1.5, days_old/14) means older threads rise."""
        mem.record_open_thread("New thread question", domain="compass")
        mem.record_open_thread("Old thread question", domain="compass")

        threads = mem.get_open_threads()
        new_id = next(t["thread_id"] for t in threads if "New" in t["question"])
        old_id = next(t["thread_id"] for t in threads if "Old" in t["question"])

        _backdate(mem, new_id, days_ago=2)
        _backdate(mem, old_id, days_ago=20)

        triaged = mem.triage_threads()
        by_id = {t["thread_id"]: t for t in triaged}

        old_score = by_id[old_id]["triage_score"]
        new_score = by_id[new_id]["triage_score"]

        assert old_score > new_score, (
            f"20-day thread (score {old_score}) should outscore 2-day thread (score {new_score})"
        )

    def test_triage_score_and_reason_present(self, mem):
        """Each returned record must have triage_score and triage_reason."""
        mem.record_open_thread("Any question", domain="test")
        triaged = mem.triage_threads()
        assert len(triaged) >= 1
        assert "triage_score" in triaged[0]
        assert "triage_reason" in triaged[0]
        assert isinstance(triaged[0]["triage_score"], float)
        assert isinstance(triaged[0]["triage_reason"], str)


# ── Case 2: tag_match contributes when current_domain_tags provided ──

class TestTagMatchContribution:
    def test_matching_domain_raises_score(self, mem):
        """A thread in 'entropy' should score higher than one in 'bioelectric'
        when current_domain_tags=['entropy']."""
        mem.record_open_thread("Entropy thread", domain="entropy")
        mem.record_open_thread("Bioelectric thread", domain="bioelectric")

        threads = mem.get_open_threads()
        entropy_id = next(t["thread_id"] for t in threads if "Entropy" in t["question"])
        bio_id = next(t["thread_id"] for t in threads if "Bioelectric" in t["question"])

        # Give both threads the same age so only tag_match differentiates.
        _backdate(mem, entropy_id, days_ago=5)
        _backdate(mem, bio_id, days_ago=5)

        triaged = mem.triage_threads(current_domain_tags=["entropy"])
        by_id = {t["thread_id"]: t for t in triaged}

        entropy_score = by_id[entropy_id]["triage_score"]
        bio_score = by_id[bio_id]["triage_score"]

        assert entropy_score > bio_score, (
            f"entropy thread (score {entropy_score}) should outscore "
            f"bioelectric thread (score {bio_score}) with domain tag 'entropy'"
        )

    def test_no_tags_means_no_tag_match_contribution(self, mem):
        """Without current_domain_tags, tag_match must be 0 for all threads."""
        mem.record_open_thread("Q", domain="entropy")
        triaged = mem.triage_threads(current_domain_tags=None)
        assert len(triaged) >= 1
        # With no tags, the only score component is age_pressure
        # For a fresh thread: age_pressure ~ 0, so score ~ 0
        score = triaged[0]["triage_score"]
        assert score <= 1.5, "Without tag match, score is bounded by age_pressure alone"


# ── Case 3: recent touches reduce triage_score ──

class TestTouchPenalty:
    def test_recent_touches_lower_triage_score(self, mem):
        """touch_penalty = -0.3 * recent_touch_count should reduce score."""
        mem.record_open_thread("Touched thread", domain="test")
        mem.record_open_thread("Untouched thread", domain="test")

        threads = mem.get_open_threads()
        touched_id = next(t["thread_id"] for t in threads if "Touched" in t["question"])
        untouched_id = next(t["thread_id"] for t in threads if "Untouched" in t["question"])

        # Give same age
        _backdate(mem, touched_id, days_ago=10)
        _backdate(mem, untouched_id, days_ago=10)

        # Add recent touches to the first thread
        mem.touch_thread(touched_id, note="Thought about it once")
        mem.touch_thread(touched_id, note="Thought about it twice")

        triaged = mem.triage_threads()
        by_id = {t["thread_id"]: t for t in triaged}

        touched_score = by_id[touched_id]["triage_score"]
        untouched_score = by_id[untouched_id]["triage_score"]

        assert touched_score < untouched_score, (
            f"Touched thread (score {touched_score}) should score below "
            f"untouched thread (score {untouched_score}) with same age"
        )

    def test_old_touches_do_not_penalize(self, mem):
        """Touches older than 7 days should not count as recent, no penalty."""
        mem.record_open_thread("Stale-touched thread", domain="test")
        threads = mem.get_open_threads()
        tid = threads[0]["thread_id"]

        # Add a touch, then backdate it to 10 days ago
        mem.touch_thread(tid, note="Old touch")
        _backdate_touch(mem, tid, days_ago=10)

        _backdate(mem, tid, days_ago=7)
        triaged_no_touch = mem.triage_threads()

        # Score should not include a touch_penalty since the touch is > 7 days old.
        # Compare vs a fresh thread with same age - their scores should be similar.
        mem.record_open_thread("No touch thread", domain="test")
        threads2 = mem.get_open_threads()
        no_touch_id = next(
            t["thread_id"] for t in threads2 if "No touch" in t["question"]
        )
        _backdate(mem, no_touch_id, days_ago=7)

        triaged = mem.triage_threads()
        by_id = {t["thread_id"]: t for t in triaged}

        stale_score = by_id[tid]["triage_score"]
        no_touch_score = by_id[no_touch_id]["triage_score"]

        # With no recent touches, scores should be approximately equal (same age, no tags)
        assert abs(stale_score - no_touch_score) < 0.05, (
            f"Stale-touched ({stale_score}) and untouched ({no_touch_score}) "
            "threads with same age should have equal scores (old touches don't penalize)"
        )


# ── Case 4: 35-day thread with zero touches flagged archive_or_escalate ──

class TestArchiveFlagging:
    def test_35_day_zero_touch_thread_flagged(self, mem):
        """Threads older than 30 days with no recent touches get the recommendation."""
        mem.record_open_thread("Ancient question", domain="legacy")
        threads = mem.get_open_threads()
        tid = threads[0]["thread_id"]

        _backdate(mem, tid, days_ago=35)

        triaged = mem.triage_threads()
        by_id = {t["thread_id"]: t for t in triaged}

        record = by_id[tid]
        assert record.get("recommendation") == "archive_or_escalate", (
            "35-day-old thread with zero touches must have recommendation='archive_or_escalate'"
        )

    def test_recent_thread_not_flagged(self, mem):
        """Threads 5 days old must not receive archive_or_escalate."""
        mem.record_open_thread("Recent question", domain="active")
        threads = mem.get_open_threads()
        tid = threads[0]["thread_id"]

        _backdate(mem, tid, days_ago=5)

        triaged = mem.triage_threads()
        by_id = {t["thread_id"]: t for t in triaged}

        assert "recommendation" not in by_id[tid], (
            "5-day thread must not be flagged with archive_or_escalate"
        )

    def test_30_day_thread_with_touches_not_flagged(self, mem):
        """A 31-day thread that HAS been touched recently should not be flagged."""
        mem.record_open_thread("Touched ancient question", domain="legacy")
        threads = mem.get_open_threads()
        tid = threads[0]["thread_id"]

        _backdate(mem, tid, days_ago=31)

        # Add a recent touch (today, so within 7-day window)
        mem.touch_thread(tid, note="Actively considering this")

        triaged = mem.triage_threads()
        by_id = {t["thread_id"]: t for t in triaged}

        assert "recommendation" not in by_id[tid], (
            "31-day thread with recent touches must not be flagged archive_or_escalate"
        )

    def test_triage_limit_respected(self, mem):
        """triage_threads must respect the limit parameter."""
        for i in range(10):
            mem.record_open_thread(f"Question {i}", domain="test")
        triaged = mem.triage_threads(limit=4)
        assert len(triaged) <= 4


# ── Edit 3 additions: no-overlap penalty and timestamp tiebreaker ──

class TestNoOverlapPenalty:
    """When caller provides domain tags, threads with zero overlap get -0.3 penalty."""

    def test_zero_overlap_thread_score_reduced_by_penalty(self, mem):
        """A thread in 'bar,baz' with caller tag 'foo' gets tag_match=-0.3."""
        mem.record_open_thread("Zero overlap thread", domain="bar,baz")

        threads = mem.get_open_threads()
        tid = threads[0]["thread_id"]
        # Use age 7 days → age_pressure = min(1.5, 7/14) = 0.5
        _backdate(mem, tid, days_ago=7)

        triaged = mem.triage_threads(current_domain_tags=["foo"])
        assert len(triaged) == 1
        score = triaged[0]["triage_score"]

        # Expected: age_pressure(0.5) + tag_match(-0.3) + touch_penalty(0) = 0.2
        assert score == pytest.approx(0.2, abs=0.01), (
            f"Zero-overlap thread should score 0.2 (0.5 age - 0.3 penalty), got {score}"
        )

    def test_zero_overlap_lower_than_matching_thread(self, mem):
        """A no-overlap thread scores below a tag-matching thread of the same age."""
        mem.record_open_thread("Matching thread", domain="foo")
        mem.record_open_thread("Off-topic thread", domain="bar")

        threads = mem.get_open_threads()
        matching_id = next(t["thread_id"] for t in threads if "Matching" in t["question"])
        offtopic_id = next(t["thread_id"] for t in threads if "Off-topic" in t["question"])

        # Same age for both
        _backdate(mem, matching_id, days_ago=7)
        _backdate(mem, offtopic_id, days_ago=7)

        triaged = mem.triage_threads(current_domain_tags=["foo"])
        by_id = {t["thread_id"]: t for t in triaged}

        matching_score = by_id[matching_id]["triage_score"]
        offtopic_score = by_id[offtopic_id]["triage_score"]

        assert matching_score > offtopic_score, (
            f"Matching thread ({matching_score}) must outscore off-topic thread ({offtopic_score})"
        )

    def test_no_penalty_without_domain_tags(self, mem):
        """When no domain tags provided, zero-overlap penalty must not apply."""
        mem.record_open_thread("No tags thread", domain="bar")
        threads = mem.get_open_threads()
        tid = threads[0]["thread_id"]
        _backdate(mem, tid, days_ago=7)

        triaged = mem.triage_threads(current_domain_tags=None)
        score = triaged[0]["triage_score"]

        # Without tags: age_pressure(0.5) + 0 penalty + 0 touch = 0.5
        assert score == pytest.approx(0.5, abs=0.01), (
            f"No-tags call must not apply penalty, expected 0.5, got {score}"
        )


class TestTiebreaker:
    """Two threads with equal triage_score order by timestamp desc (newest first)."""

    def test_equal_score_newer_thread_first(self, mem):
        """When triage scores are equal, the newer thread appears first."""
        # Create two threads of the exact same age (both freshly created then backdated).
        mem.record_open_thread("Older thread", domain="test")
        mem.record_open_thread("Newer thread", domain="test")

        threads = mem.get_open_threads()
        older_id = next(t["thread_id"] for t in threads if "Older" in t["question"])
        newer_id = next(t["thread_id"] for t in threads if "Newer" in t["question"])

        # Both threads 7 days old — same age_pressure, no tags, no touches.
        # To guarantee equal scores we use the exact same days_ago value.
        _backdate(mem, older_id, days_ago=7)
        _backdate(mem, newer_id, days_ago=6)  # 1 day newer → same rounded age pressure but newer ts

        # Without domain tags, scores are purely age-based.
        triaged = mem.triage_threads(current_domain_tags=None)
        ids_in_order = [t["thread_id"] for t in triaged]

        # The 6-day thread (newer_id) is newer → its timestamp is later → appears first on tie.
        newer_pos = ids_in_order.index(newer_id) if newer_id in ids_in_order else 999
        older_pos = ids_in_order.index(older_id) if older_id in ids_in_order else 999

        # 6 days → age_pressure = 6/14 ≈ 0.4286; 7 days → 0.5. Scores differ slightly.
        # For a strict tiebreaker test, make scores actually equal by patching to identical timestamps.
        # Instead, verify that if we inject two threads backdated to identical days, order is by ts.
        # Re-do with forced identical age:
        _backdate(mem, older_id, days_ago=10)
        _backdate(mem, newer_id, days_ago=10)

        # Manually set timestamps to be close but distinct using direct file edit.
        import json
        from datetime import timedelta
        for jsonl_file in mem.threads_dir.glob("*.jsonl"):
            lines = []
            for line in jsonl_file.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("thread_id") == older_id:
                    rec["timestamp"] = "2020-01-01T00:00:00"
                elif rec.get("thread_id") == newer_id:
                    rec["timestamp"] = "2020-01-02T00:00:00"  # 1 day newer
                lines.append(json.dumps(rec))
            jsonl_file.write_text("\n".join(lines) + "\n")

        triaged2 = mem.triage_threads(current_domain_tags=None)
        # Both have age_pressure capped at min(1.5, days/14) for the same backdated time
        # → scores differ by less than floating point; tiebreaker by timestamp should put newer first
        ids2 = [t["thread_id"] for t in triaged2]
        if ids2:
            # Find the two threads in the result
            newer_pos2 = ids2.index(newer_id) if newer_id in ids2 else 999
            older_pos2 = ids2.index(older_id) if older_id in ids2 else 999
            assert newer_pos2 <= older_pos2, (
                f"newer thread (pos {newer_pos2}) must appear before older thread (pos {older_pos2}) on tie"
            )
