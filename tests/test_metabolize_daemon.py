"""
MetabolizeDaemon tests — Step 4 of v1.3.2.

The load-bearing tests this file inherits from Step 3:
  - test_ack_is_distinct_from_read_by (the v1.3.1 ack-split contract)
  - test_halt_file_contains_all_four_required_fields

The new test surface unique to metabolize:
  - delta filter: items already surfaced in the prior digest are subtracted
  - two output sinks: comms post AND ~/.sovereign/decisions/metabolize_<ts>.md
  - "no findings" vs "no changes" — distinct outcome codes
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sovereign_stack.daemons.metabolize_daemon import (
    COMPASS_PAUSE,
    COMPASS_PROCEED,
    CONSECUTIVE_UNACKED_THRESHOLD,
    MAX_DIGEST_ITEMS_PER_CATEGORY,
    OUTCOME_ALREADY_HALTED,
    OUTCOME_DRY_RUN,
    OUTCOME_GROUNDING_FAILED,
    OUTCOME_HALTED,
    OUTCOME_NO_CHANGES,
    OUTCOME_NO_FINDINGS,
    OUTCOME_PAUSED,
    OUTCOME_POSTED,
    STATE_SCHEMA_VERSION,
    MetabolizeDaemon,
    _contradiction_key,
    _stale_hypothesis_key,
    _stale_thread_key,
)
from sovereign_stack.daemons.senders import (
    SENDER_HALT_ALERT,
    SENDER_METABOLIZE,
)
from sovereign_stack.grounding import (
    REASON_NO_EVIDENCE,
    REASON_OK,
    GroundingResult,
)

# ── Fixtures + stubs ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_sovereign():
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    (root / "daemons").mkdir()
    (root / "daemons" / "halts").mkdir()
    (root / "decisions").mkdir()
    # Evidence path: a file outside chronicle that grounded_extract will
    # accept as structural evidence (existence is sufficient for
    # non-chronicle paths). Tests inject a stub grounding_fn anyway, but
    # the file must exist so the daemon constructor succeeds and live
    # wiring works the same as production.
    (root / "metabolism_log.jsonl").touch()
    yield root
    shutil.rmtree(tmp, ignore_errors=True)


class CommsStore:
    def __init__(self):
        self.posts: list[dict] = []
        self.acks: dict[str, list[dict]] = {}

    def post(self, *, sender, content, channel, message_id, extra_fields=None):
        rec = {
            "id": message_id,
            "sender": sender,
            "content": content,
            "channel": channel,
            "read_by": [],
            **(extra_fields or {}),
        }
        self.posts.append(rec)
        return rec

    def mark_read_by(self, message_id, instance_id):
        for rec in self.posts:
            if rec["id"] == message_id:
                if instance_id not in rec["read_by"]:
                    rec["read_by"].append(instance_id)
                break

    def acknowledge(self, message_id, instance_id, note=""):
        self.acks.setdefault(message_id, []).append(
            {
                "message_id": message_id,
                "instance_id": instance_id,
                "note": note,
            }
        )

    def get_acks(self, message_id):
        return list(self.acks.get(message_id, []))


def _digest(
    *,
    n_contradictions: int = 1,
    n_stale_threads: int = 1,
    n_stale_hypotheses: int = 1,
    suffix: str = "a",
):
    """Build a detect_fn return shape with controlled item counts and keys."""
    return {
        "contradictions": [
            {
                "hypothesis_domain": f"hd-{suffix}-{i}",
                "hypothesis_preview": f"hyp content {suffix}-{i}",
                "hypothesis_timestamp": f"2026-01-01T00:00:0{i}Z",
                "ground_truth_domain": f"gd-{suffix}-{i}",
                "ground_truth_preview": f"gt content {suffix}-{i}",
                "ground_truth_timestamp": f"2026-02-01T00:00:0{i}Z",
                "overlap_score": 0.45,
            }
            for i in range(n_contradictions)
        ],
        "stale_threads": [
            {
                "domain": f"std-{suffix}-{i}",
                "question": f"stale thread question {suffix}-{i}",
                "age_days": 45,
                "timestamp": f"2026-03-01T00:00:0{i}Z",
            }
            for i in range(n_stale_threads)
        ],
        "stale_hypotheses": [
            {
                "domain": f"shd-{suffix}-{i}",
                "content": f"aging hypothesis {suffix}-{i}",
                "age_days": 60,
            }
            for i in range(n_stale_hypotheses)
        ],
        "stats": {
            "total_insights": 100,
            "ground_truths": 30,
            "hypotheses": 70,
            "open_threads": 12,
        },
    }


def make_daemon(
    root: Path,
    *,
    comms: CommsStore | None = None,
    compass_decision: str = COMPASS_PROCEED,
    digest: dict | None = None,
    digest_sequence: list | None = None,
    grounding_accept: bool = True,
    now: datetime | None = None,
    unacked_threshold: int = CONSECUTIVE_UNACKED_THRESHOLD,
):
    comms = comms or CommsStore()
    now = now or datetime(2026, 4, 25, 3, 17, 0, tzinfo=timezone.utc)

    if digest_sequence is not None:
        seq_iter = iter(digest_sequence)

        def detect_fn():
            try:
                return next(seq_iter)
            except StopIteration:
                # Empty digest after sequence exhausted.
                return {
                    "contradictions": [],
                    "stale_threads": [],
                    "stale_hypotheses": [],
                    "stats": {},
                }
    else:
        single = digest if digest is not None else _digest()

        def detect_fn():
            return single

    def grounding_fn(claim, evidence_paths, **kw):
        if grounding_accept:
            return GroundingResult(
                accepted=True,
                reason=REASON_OK,
                claim=claim,
                matched_paths=[str(p) for p in evidence_paths],
            )
        return GroundingResult(
            accepted=False,
            reason=REASON_NO_EVIDENCE,
            claim=claim,
        )

    counter = {"n": 0}

    def id_fn():
        counter["n"] += 1
        return f"test-msg-{counter['n']:03d}"

    return MetabolizeDaemon(
        state_path=root / "daemons" / "metabolize_state.json",
        halt_dir=root / "daemons" / "halts",
        decisions_dir=root / "decisions",
        evidence_paths=[root / "metabolism_log.jsonl"],
        compass_fn=lambda action, stakes: {"decision": compass_decision, "rationale": "test"},
        detect_fn=detect_fn,
        comms_post_fn=comms.post,
        comms_get_acks_fn=comms.get_acks,
        grounding_fn=grounding_fn,
        now_fn=lambda: now,
        id_fn=id_fn,
        unacked_threshold=unacked_threshold,
    ), comms


# ── THE LOAD-BEARING TEST ───────────────────────────────────────────────────


class TestAckDistinctFromReadBy:
    """Inherited circuit-breaker contract — read_by glances are NOT acks."""

    def test_ack_is_distinct_from_read_by(self, tmp_sovereign):
        # Sequence with always-fresh fingerprints so the delta filter
        # never blocks a post and we exercise the ack circuit cleanly.
        digests = [_digest(suffix=s) for s in "abcd"]
        daemon, comms = make_daemon(tmp_sovereign, digest_sequence=digests)

        for label in ("claude-iphone", "claude-desktop", "claude-code-macbook"):
            r = daemon.run()
            assert r.outcome == OUTCOME_POSTED
            comms.mark_read_by(r.posted_message_id, label)

        # Three posts read but never acked — next run halts.
        r = daemon.run()
        assert r.outcome == OUTCOME_HALTED, (
            "read_by glances are NOT acks. If this fails, the circuit breaker is broken."
        )

        # Verify acks map is empty for each digest post.
        digest_ids = [p["id"] for p in comms.posts if p["sender"] == SENDER_METABOLIZE]
        for post_id in digest_ids:
            assert comms.get_acks(post_id) == []


# ── Compass gating ──────────────────────────────────────────────────────────


class TestCompassGating:
    def test_compass_pause_skips_post(self, tmp_sovereign):
        daemon, comms = make_daemon(
            tmp_sovereign,
            compass_decision=COMPASS_PAUSE,
        )
        r = daemon.run()
        assert r.outcome == OUTCOME_PAUSED
        assert comms.posts == []

    def test_compass_proceed_allows_post(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)
        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED
        assert len(comms.posts) == 1


# ── Grounding gate ──────────────────────────────────────────────────────────


class TestGroundingGate:
    def test_grounding_failure_skips_without_halt(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign, grounding_accept=False)
        r = daemon.run()
        assert r.outcome == OUTCOME_GROUNDING_FAILED
        assert comms.posts == []

        # Repeated grounding failures never accumulate toward halt.
        for _ in range(10):
            r = daemon.run()
            assert r.outcome == OUTCOME_GROUNDING_FAILED
        assert comms.posts == []

    def test_grounding_pass_allows_post(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign, grounding_accept=True)
        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED
        assert r.grounding_reason == REASON_OK


# ── Halt behaviors ──────────────────────────────────────────────────────────


class TestHalt:
    def test_three_unacked_triggers_halt(self, tmp_sovereign):
        digests = [_digest(suffix=s) for s in "abcd"]
        daemon, _ = make_daemon(tmp_sovereign, digest_sequence=digests)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        r = daemon.run()
        assert r.outcome == OUTCOME_HALTED
        assert r.halt_path is not None
        assert Path(r.halt_path).exists()

    def test_halt_file_contains_all_four_required_fields(self, tmp_sovereign):
        digests = [_digest(suffix=s) for s in "abcd"]
        daemon, _ = make_daemon(tmp_sovereign, digest_sequence=digests)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        r = daemon.run()
        body = Path(r.halt_path).read_text()

        assert "Reason: consecutive_unacked_threshold_reached" in body
        assert "What the daemon tried to do" in body
        assert "Evidence that triggered the halt" in body
        assert "blocked downstream" in body
        assert "To resolve" in body

    def test_halt_posts_alert_to_comms(self, tmp_sovereign):
        digests = [_digest(suffix=s) for s in "abcd"]
        daemon, comms = make_daemon(tmp_sovereign, digest_sequence=digests)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        daemon.run()  # halt
        halt_alerts = [p for p in comms.posts if p["sender"] == SENDER_HALT_ALERT]
        assert len(halt_alerts) == 1
        assert "daemon.metabolize halted" in halt_alerts[0]["content"]

    def test_ack_before_threshold_prevents_halt(self, tmp_sovereign):
        digests = [_digest(suffix=s) for s in "abcd"]
        daemon, comms = make_daemon(tmp_sovereign, digest_sequence=digests)
        daemon.run()
        daemon.run()
        r3 = daemon.run()
        comms.acknowledge(r3.posted_message_id, "claude-iphone", "integrated")

        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED, "An ack within the window must reset the unacked count."

    def test_halt_persists_across_runs(self, tmp_sovereign):
        digests = [_digest(suffix=s) for s in "abcd"]
        daemon, _ = make_daemon(tmp_sovereign, digest_sequence=digests)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        daemon.run()  # halt

        fresh, _ = make_daemon(tmp_sovereign)
        r = fresh.run()
        assert r.outcome == OUTCOME_ALREADY_HALTED


# ── Delta filter (NEW for metabolize) ───────────────────────────────────────


class TestDeltaFilter:
    """The delta filter is what makes a nightly daemon tolerable: only
    surface NEW findings, not the same backlog every night."""

    def test_identical_digest_returns_no_changes(self, tmp_sovereign):
        same = _digest(suffix="x")
        daemon, comms = make_daemon(
            tmp_sovereign,
            digest_sequence=[same, same],
        )
        r1 = daemon.run()
        assert r1.outcome == OUTCOME_POSTED
        r2 = daemon.run()
        assert r2.outcome == OUTCOME_NO_CHANGES, (
            "Identical findings on second run must produce no_changes — "
            "the delta filter is what prevents nightly noise."
        )
        # Only the first run posted to comms.
        digest_posts = [p for p in comms.posts if p["sender"] == SENDER_METABOLIZE]
        assert len(digest_posts) == 1

    def test_partial_overlap_posts_only_new_items(self, tmp_sovereign):
        first = _digest(
            n_contradictions=2,
            n_stale_threads=2,
            n_stale_hypotheses=2,
            suffix="x",
        )
        # Second digest reuses ONE contradiction and ONE stale_thread from
        # the first, plus one new of each.
        second = {
            "contradictions": [
                first["contradictions"][0],  # repeat
                {
                    "hypothesis_domain": "new-hd",
                    "hypothesis_preview": "new hyp",
                    "hypothesis_timestamp": "2026-01-01T00:00:99Z",
                    "ground_truth_domain": "new-gd",
                    "ground_truth_preview": "new gt",
                    "ground_truth_timestamp": "2026-02-01T00:00:99Z",
                    "overlap_score": 0.5,
                },
            ],
            "stale_threads": [
                first["stale_threads"][0],
                {
                    "domain": "new-std",
                    "question": "new q",
                    "age_days": 50,
                    "timestamp": "2026-03-01T00:00:99Z",
                },
            ],
            "stale_hypotheses": [
                {"domain": "new-shd", "content": "new aging hyp", "age_days": 70},
            ],
            "stats": first["stats"],
        }
        daemon, comms = make_daemon(
            tmp_sovereign,
            digest_sequence=[first, second],
        )
        daemon.run()
        r2 = daemon.run()
        assert r2.outcome == OUTCOME_POSTED
        assert r2.contradictions_included == 1
        assert r2.stale_threads_included == 1
        assert r2.stale_hypotheses_included == 1

        # Second comms post content references ONLY the new items.
        second_post = [p for p in comms.posts if p["sender"] == SENDER_METABOLIZE][1]["content"]
        assert "new hyp" in second_post
        # The repeated item should not appear in the second digest.
        assert first["contradictions"][0]["hypothesis_preview"] not in second_post

    def test_fingerprint_keys_are_stable(self):
        """Two equivalent items must hash to the same fingerprint, or
        the delta filter leaks duplicates across runs."""
        c = {
            "hypothesis_domain": "x",
            "hypothesis_timestamp": "t1",
            "ground_truth_domain": "y",
            "ground_truth_timestamp": "t2",
        }
        assert _contradiction_key(c) == _contradiction_key(dict(c))

        t = {"domain": "d", "question": "q" * 200}
        assert _stale_thread_key(t) == _stale_thread_key(dict(t))

        h = {"domain": "d", "content": "c" * 200}
        assert _stale_hypothesis_key(h) == _stale_hypothesis_key(dict(h))


# ── Findings vs. changes (two distinct empty-states) ────────────────────────


class TestEmptyStates:
    def test_no_findings_when_chronicle_clean(self, tmp_sovereign):
        empty = {
            "contradictions": [],
            "stale_threads": [],
            "stale_hypotheses": [],
            "stats": {"total_insights": 0},
        }
        daemon, comms = make_daemon(tmp_sovereign, digest=empty)
        r = daemon.run()
        assert r.outcome == OUTCOME_NO_FINDINGS
        assert comms.posts == []

    def test_no_changes_when_only_duplicates(self, tmp_sovereign):
        same = _digest(suffix="z")
        daemon, comms = make_daemon(
            tmp_sovereign,
            digest_sequence=[same, same],
        )
        daemon.run()
        r = daemon.run()
        assert r.outcome == OUTCOME_NO_CHANGES, (
            "Distinct from no_findings: detection produced findings, but all already surfaced."
        )


# ── Decision file (NEW for metabolize) ──────────────────────────────────────


class TestDecisionFile:
    def test_decision_file_written_alongside_post(self, tmp_sovereign):
        daemon, _ = make_daemon(tmp_sovereign)
        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED
        assert r.decision_path is not None
        path = Path(r.decision_path)
        assert path.exists()
        assert path.parent == tmp_sovereign / "decisions"

    def test_decision_file_contains_full_content(self, tmp_sovereign):
        """Decision file is the durable record — fuller than the comms snippet."""
        digest = _digest(
            n_contradictions=2,
            n_stale_threads=2,
            n_stale_hypotheses=2,
        )
        daemon, _ = make_daemon(tmp_sovereign, digest=digest)
        r = daemon.run()
        body = Path(r.decision_path).read_text()
        assert "## Contradictions" in body
        assert "## Stale threads" in body
        assert "## Aging hypotheses" in body
        assert "## How to act on this" in body
        # Cross-reference back to the comms message.
        assert r.posted_message_id in body

    def test_no_decision_file_on_no_changes(self, tmp_sovereign):
        same = _digest(suffix="q")
        daemon, _ = make_daemon(tmp_sovereign, digest_sequence=[same, same])
        daemon.run()
        before = list((tmp_sovereign / "decisions").glob("*.md"))
        daemon.run()
        after = list((tmp_sovereign / "decisions").glob("*.md"))
        assert len(after) == len(before), "no_changes must NOT produce a new decision file."

    def test_no_decision_file_on_grounding_failure(self, tmp_sovereign):
        daemon, _ = make_daemon(tmp_sovereign, grounding_accept=False)
        daemon.run()
        files = list((tmp_sovereign / "decisions").glob("*.md"))
        assert files == []


# ── Digest shape ────────────────────────────────────────────────────────────


class TestDigestShape:
    def test_digest_capped_per_category(self, tmp_sovereign):
        """If detection returns more than the cap, only N items per category
        appear in the digest."""
        many = _digest(
            n_contradictions=20,
            n_stale_threads=20,
            n_stale_hypotheses=20,
        )
        daemon, _ = make_daemon(tmp_sovereign, digest=many)
        r = daemon.run()
        assert r.contradictions_included == MAX_DIGEST_ITEMS_PER_CATEGORY
        assert r.stale_threads_included == MAX_DIGEST_ITEMS_PER_CATEGORY
        assert r.stale_hypotheses_included == MAX_DIGEST_ITEMS_PER_CATEGORY

    def test_digest_includes_ack_instructions(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)
        daemon.run()
        content = comms.posts[0]["content"]
        assert "comms_acknowledge" in content
        assert f"{CONSECUTIVE_UNACKED_THRESHOLD} consecutive unacked" in content

    def test_posted_message_has_ack_required_and_decision_path(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)
        daemon.run()
        rec = comms.posts[0]
        assert rec["ack_required"] is True
        assert rec["ttl_days"] > 0
        assert rec["sender"] == SENDER_METABOLIZE
        assert "decision_path" in rec


# ── Dry run isolation ───────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_writes_nothing(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)
        r = daemon.run(dry_run=True)
        assert r.outcome == OUTCOME_DRY_RUN
        assert comms.posts == []
        state_path = tmp_sovereign / "daemons" / "metabolize_state.json"
        assert not state_path.exists()
        decisions = list((tmp_sovereign / "decisions").glob("*.md"))
        assert decisions == []

    def test_dry_run_reports_would_halt(self, tmp_sovereign):
        daemon, _ = make_daemon(tmp_sovereign)
        state_path = tmp_sovereign / "daemons" / "metabolize_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": STATE_SCHEMA_VERSION,
                    "posted_digests": [
                        {
                            "message_id": f"m{i}",
                            "posted_at": "2026-04-20T00:00:00+00:00",
                            "content_snippet": "x",
                            "fingerprints": [],
                        }
                        for i in range(CONSECUTIVE_UNACKED_THRESHOLD)
                    ],
                    "halted_at": None,
                    "halt_reason": None,
                }
            )
        )
        r = daemon.run(dry_run=True)
        assert r.outcome == OUTCOME_HALTED
        halts = list((tmp_sovereign / "daemons" / "halts").glob("*.md"))
        assert halts == []


# ── State schema future-proofing ────────────────────────────────────────────


class TestStateSchema:
    def test_state_file_has_schema_version(self, tmp_sovereign):
        daemon, _ = make_daemon(tmp_sovereign)
        daemon.run()
        state_path = tmp_sovereign / "daemons" / "metabolize_state.json"
        data = json.loads(state_path.read_text())
        assert data["schema_version"] == STATE_SCHEMA_VERSION

    def test_unversioned_legacy_state_loads_as_v1(self, tmp_sovereign):
        state_path = tmp_sovereign / "daemons" / "metabolize_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "posted_digests": [],
                    "halted_at": None,
                    "halt_reason": None,
                }
            )
        )
        daemon, _ = make_daemon(tmp_sovereign)
        r = daemon.run()
        assert r.outcome in (OUTCOME_POSTED, OUTCOME_NO_FINDINGS)

    def test_future_schema_version_refuses_to_load(self, tmp_sovereign):
        state_path = tmp_sovereign / "daemons" / "metabolize_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": STATE_SCHEMA_VERSION + 1,
                    "posted_digests": [],
                    "halted_at": None,
                    "halt_reason": None,
                }
            )
        )
        daemon, _ = make_daemon(tmp_sovereign)
        with pytest.raises(ValueError):
            daemon.run()


# ── Real grounded_extract (catches directory-vs-file evidence bugs) ─────────


class TestRealGrounding:
    """Most tests inject a stub grounding_fn. This class uses the REAL
    grounded_extract so we catch evidence-path shape bugs that mocks
    would mask — for instance, passing a directory instead of a file
    to a chronicle-aware grounding check."""

    def test_real_grounded_extract_accepts_metabolism_log(self, tmp_sovereign):
        """The default evidence path (metabolism_log.jsonl, non-chronicle)
        must be accepted by the actual grounded_extract function."""
        # Build the daemon WITHOUT a stub grounding_fn — exercise the real one.
        comms = CommsStore()
        daemon = MetabolizeDaemon(
            state_path=tmp_sovereign / "daemons" / "metabolize_state.json",
            halt_dir=tmp_sovereign / "daemons" / "halts",
            decisions_dir=tmp_sovereign / "decisions",
            evidence_paths=[tmp_sovereign / "metabolism_log.jsonl"],
            compass_fn=lambda action, stakes: {"decision": COMPASS_PROCEED},
            detect_fn=lambda: _digest(),
            comms_post_fn=comms.post,
            comms_get_acks_fn=comms.get_acks,
            # grounding_fn defaults to real grounded_extract
        )
        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED, (
            f"Real grounded_extract must accept the metabolism_log.jsonl "
            f"evidence path. Got outcome={r.outcome}, "
            f"grounding_reason={r.grounding_reason}."
        )
        assert r.grounding_reason == REASON_OK


# ── Sender taxonomy ─────────────────────────────────────────────────────────


class TestSenderTaxonomy:
    def test_all_posts_use_daemon_prefix(self, tmp_sovereign):
        digests = [_digest(suffix=s) for s in "abcd"]
        daemon, comms = make_daemon(tmp_sovereign, digest_sequence=digests)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        daemon.run()  # halt
        for post in comms.posts:
            assert post["sender"].startswith("daemon."), (
                f"All daemon posts must use the daemon.* prefix. Got: {post['sender']}"
            )

    def test_routine_post_uses_metabolize_sender(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)
        daemon.run()
        digest_posts = [p for p in comms.posts if p["sender"] == SENDER_METABOLIZE]
        assert len(digest_posts) == 1
