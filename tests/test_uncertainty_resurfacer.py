"""
UncertaintyResurfacer daemon tests — Step 3 of v1.3.2.

The most important test in this file is test_ack_is_distinct_from_read_by.
The halt-on-unack circuit breaker depends on comms_acknowledge being a
deliberate act distinct from the bridge's glance-marking read_by mutation.
If a future refactor collapses the two, the daemon's circuit breaker
stops being able to fire, and every subsequent daemon inherits the bug.
That test asserts the contract.

Every external dependency is injected so these tests never touch the real
compass, real chronicle, or real comms channel. State is sandboxed per-test.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack.daemons.senders import (
    SENDER_HALT_ALERT,
    SENDER_UNCERTAINTY,
)
from sovereign_stack.daemons.uncertainty_resurfacer import (
    COMPASS_PAUSE,
    COMPASS_PROCEED,
    CONSECUTIVE_UNACKED_THRESHOLD,
    MAX_DIGEST_UNCERTAINTIES,
    OUTCOME_ALREADY_HALTED,
    OUTCOME_DRY_RUN,
    OUTCOME_GROUNDING_FAILED,
    OUTCOME_HALTED,
    OUTCOME_NO_UNCERTAINTIES,
    OUTCOME_PAUSED,
    OUTCOME_POSTED,
    STATE_SCHEMA_VERSION,
    UncertaintyResurfacer,
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
    (root / "consciousness").mkdir()
    # Non-chronicle path — grounded_extract accepts it as structural
    # evidence on existence alone, no JSONL layer-scan triggered.
    (root / "consciousness" / "uncertainty_log.json").write_text("{}")
    yield root
    shutil.rmtree(tmp, ignore_errors=True)


class CommsStore:
    """
    In-memory stand-in for ~/.sovereign/comms/. Tracks posts and acks
    separately so tests can assert the distinction between browse-read
    (which a bridge would add to read_by) and deliberate acknowledgment.
    """
    def __init__(self):
        self.posts: list[dict] = []
        self.acks: dict[str, list[dict]] = {}

    # Bridge-shaped post: mirrors what a comms_send call would write.
    def post(self, *, sender, content, channel, message_id, extra_fields=None):
        rec = {
            "id": message_id,
            "sender": sender,
            "content": content,
            "channel": channel,
            "read_by": [],          # bridge populates this on glance
            **(extra_fields or {}),
        }
        self.posts.append(rec)
        return rec

    def mark_read_by(self, message_id, instance_id):
        """Simulates a bridge glance-read — populates read_by, NOT acks."""
        for rec in self.posts:
            if rec["id"] == message_id:
                if instance_id not in rec["read_by"]:
                    rec["read_by"].append(instance_id)
                break

    def acknowledge(self, message_id, instance_id, note=""):
        self.acks.setdefault(message_id, []).append({
            "message_id": message_id,
            "instance_id": instance_id,
            "note": note,
        })

    def get_acks(self, message_id):
        return list(self.acks.get(message_id, []))


def make_daemon(
    root: Path,
    *,
    comms: CommsStore | None = None,
    compass_decision: str = COMPASS_PROCEED,
    uncertainties: list[dict] | None = None,
    grounding_accept: bool = True,
    now: datetime | None = None,
    unacked_threshold: int = CONSECUTIVE_UNACKED_THRESHOLD,
):
    comms = comms or CommsStore()
    now = now or datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    uncertainties = uncertainties if uncertainties is not None else [
        {
            "marker_id": "u1",
            "what": "Test uncertainty one",
            "why": "Because testing",
            "timestamp": (now - timedelta(days=10)).isoformat(),
            "confidence": 0.3,
            "what_would_help": ["ask Anthony", "run an experiment"],
        },
    ]

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

    # Sequential ids so assertions can reference them.
    counter = {"n": 0}

    def id_fn():
        counter["n"] += 1
        return f"test-msg-{counter['n']:03d}"

    return UncertaintyResurfacer(
        state_path=root / "daemons" / "uncertainty_state.json",
        halt_dir=root / "daemons" / "halts",
        uncertainty_log_path=root / "consciousness" / "uncertainty_log.json",
        compass_fn=lambda action, stakes: {
            "decision": compass_decision, "rationale": "test"},
        uncertainty_fn=lambda: uncertainties,
        comms_post_fn=comms.post,
        comms_get_acks_fn=comms.get_acks,
        grounding_fn=grounding_fn,
        now_fn=lambda: now,
        id_fn=id_fn,
        unacked_threshold=unacked_threshold,
    ), comms


# ── THE LOAD-BEARING TEST ───────────────────────────────────────────────────


class TestAckDistinctFromReadBy:
    """The single most important test in this file.

    The halt-on-unack circuit breaker depends on comms_acknowledge being
    semantically distinct from the bridge's read_by glance-marking. If the
    two collapse, every daemon's halt condition stops firing."""

    def test_ack_is_distinct_from_read_by(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)

        # Post three digests. Between each, simulate a browse-read
        # (populating read_by) but NEVER call acknowledge().
        posts_made = []
        # Sub-threshold first post should succeed.
        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED
        posts_made.append(r.posted_message_id)
        comms.mark_read_by(r.posted_message_id, "claude-iphone")

        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED
        posts_made.append(r.posted_message_id)
        comms.mark_read_by(r.posted_message_id, "claude-desktop")

        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED
        posts_made.append(r.posted_message_id)
        comms.mark_read_by(r.posted_message_id, "claude-code-macbook")

        # Every post has been "read" (read_by populated on each), but NONE
        # has been acknowledged via comms.acknowledge(). The next run MUST
        # halt — read_by does not count toward ack.
        r = daemon.run()
        assert r.outcome == OUTCOME_HALTED, (
            "read_by glances are NOT acks. If this fails, the circuit "
            "breaker is broken and every downstream daemon inherits the bug."
        )

        # Sanity: all three posts exist and have read_by populated, but
        # acks map is empty for all three.
        for post_id in posts_made:
            rec = next(p for p in comms.posts if p["id"] == post_id)
            assert rec["read_by"], f"Post {post_id} should have read_by set"
            assert comms.get_acks(post_id) == [], (
                f"Post {post_id} must have zero acks — that's the whole point"
            )


# ── Compass gating ──────────────────────────────────────────────────────────


class TestCompassGating:
    def test_compass_pause_skips_post(self, tmp_sovereign):
        daemon, comms = make_daemon(
            tmp_sovereign, compass_decision=COMPASS_PAUSE,
        )
        r = daemon.run()
        assert r.outcome == OUTCOME_PAUSED
        assert r.compass_decision == COMPASS_PAUSE
        assert comms.posts == [], "PAUSE must not post anything"

    def test_compass_proceed_allows_post(self, tmp_sovereign):
        daemon, comms = make_daemon(
            tmp_sovereign, compass_decision=COMPASS_PROCEED,
        )
        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED
        assert len(comms.posts) == 1


# ── Grounding gate ──────────────────────────────────────────────────────────


class TestGroundingGate:
    def test_grounding_failure_skips_post_without_halt(self, tmp_sovereign):
        """Grounding failure skips the post but does NOT count toward
        the unacked threshold — the circuit breaker only counts posts
        that were actually posted and not acked."""
        daemon, comms = make_daemon(
            tmp_sovereign, grounding_accept=False,
        )
        r = daemon.run()
        assert r.outcome == OUTCOME_GROUNDING_FAILED
        assert r.grounding_reason == REASON_NO_EVIDENCE
        assert comms.posts == []

        # Repeat grounding failures never halt — they just keep skipping.
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
        daemon, comms = make_daemon(tmp_sovereign)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        r = daemon.run()
        assert r.outcome == OUTCOME_HALTED
        assert r.halt_path is not None
        assert Path(r.halt_path).exists()

    def test_halt_file_contains_all_four_required_fields(self, tmp_sovereign):
        daemon, _ = make_daemon(tmp_sovereign)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        r = daemon.run()
        body = Path(r.halt_path).read_text()

        # (a) stable reason code
        assert "Reason: consecutive_unacked_threshold_reached" in body
        # (b) what the daemon was trying to do
        assert "What the daemon tried to do" in body
        # (c) evidence that triggered the halt
        assert "Evidence that triggered the halt" in body
        # (d) what's blocked downstream
        assert "blocked downstream" in body
        # Bonus: the halt must tell the human HOW to resolve.
        assert "To resolve" in body

    def test_halt_file_includes_unacked_digest_contents(self, tmp_sovereign):
        """Claude Desktop's concrete ask: when Anthony reads the halt, he
        needs to see WHAT went unacked, not just that something did."""
        daemon, comms = make_daemon(tmp_sovereign)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        r = daemon.run()
        body = Path(r.halt_path).read_text()

        # Filter to uncertainty digests; halt-alert posts are separate.
        digest_ids = [
            p["id"] for p in comms.posts if p["sender"] == SENDER_UNCERTAINTY
        ]
        assert len(digest_ids) == CONSECUTIVE_UNACKED_THRESHOLD
        # Each of the three digest ids must appear in the halt note.
        for mid in digest_ids:
            assert mid in body, f"Halt note missing reference to {mid}"

    def test_halt_posts_alert_to_comms(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        daemon.run()
        halt_alerts = [p for p in comms.posts if p["sender"] == SENDER_HALT_ALERT]
        assert len(halt_alerts) == 1
        assert "daemon.uncertainty halted" in halt_alerts[0]["content"]

    def test_ack_before_threshold_prevents_halt(self, tmp_sovereign):
        """Two unacked + one acked should NOT halt — 'consecutive' means
        the last N are ALL unacked."""
        daemon, comms = make_daemon(tmp_sovereign)
        daemon.run()  # post 1
        daemon.run()  # post 2
        r3 = daemon.run()  # post 3
        # Ack the most recent one.
        comms.acknowledge(r3.posted_message_id, "claude-iphone", "integrated")

        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED, (
            "An ack within the window must reset the unacked count and "
            "allow further posts."
        )

    def test_halt_persists_across_runs(self, tmp_sovereign):
        daemon, _ = make_daemon(tmp_sovereign)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        daemon.run()  # triggers halt

        # Fresh daemon reading same state file should refuse to run.
        fresh, _ = make_daemon(tmp_sovereign)
        r = fresh.run()
        assert r.outcome == OUTCOME_ALREADY_HALTED

    def test_clearing_halted_at_resumes_daemon(self, tmp_sovereign):
        daemon, _ = make_daemon(tmp_sovereign)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        daemon.run()  # halts

        # Manually clear the halt.
        state_path = tmp_sovereign / "daemons" / "uncertainty_state.json"
        state = json.loads(state_path.read_text())
        state["halted_at"] = None
        state["halt_reason"] = None
        # Also clear posted_digests so the circuit breaker counter
        # resets — otherwise the first run after clear would halt again
        # because the unacked window still contains the three stale posts.
        state["posted_digests"] = []
        state_path.write_text(json.dumps(state))

        fresh, _ = make_daemon(tmp_sovereign)
        r = fresh.run()
        assert r.outcome == OUTCOME_POSTED


# ── Digest shape ────────────────────────────────────────────────────────────


class TestDigestShape:
    def test_digest_capped_at_max(self, tmp_sovereign):
        """More than MAX_DIGEST_UNCERTAINTIES unresolved should be truncated."""
        many = [
            {
                "marker_id": f"u{i}",
                "what": f"unc{i}",
                "why": "test",
                "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
            }
            for i in range(10)
        ]
        daemon, comms = make_daemon(tmp_sovereign, uncertainties=many)
        r = daemon.run()
        assert r.uncertainties_included == MAX_DIGEST_UNCERTAINTIES
        # The digest text should only reference the three oldest.
        content = comms.posts[0]["content"]
        assert content.count("\n1. ") == 1
        assert "4. " not in content  # no fourth item

    def test_no_uncertainties_returns_no_post(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign, uncertainties=[])
        r = daemon.run()
        assert r.outcome == OUTCOME_NO_UNCERTAINTIES
        assert comms.posts == []

    def test_digest_includes_ack_instructions(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)
        daemon.run()
        content = comms.posts[0]["content"]
        assert "comms_acknowledge" in content
        assert f"{CONSECUTIVE_UNACKED_THRESHOLD} consecutive unacked" in content

    def test_posted_message_has_ack_required(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)
        daemon.run()
        rec = comms.posts[0]
        assert rec["ack_required"] is True
        assert rec["ttl_days"] > 0
        assert rec["sender"] == SENDER_UNCERTAINTY

    def test_oldest_uncertainties_surface_first(self, tmp_sovereign):
        now = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
        unc = [
            {"marker_id": "young",
             "what": "YOUNG",
             "timestamp": (now - timedelta(days=1)).isoformat()},
            {"marker_id": "old",
             "what": "OLD",
             "timestamp": (now - timedelta(days=100)).isoformat()},
        ]
        daemon, comms = make_daemon(tmp_sovereign, uncertainties=unc, now=now)
        daemon.run()
        content = comms.posts[0]["content"]
        # OLD should appear before YOUNG in the digest.
        assert content.index("OLD") < content.index("YOUNG")


# ── Dry run isolation ───────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_writes_nothing(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)
        r = daemon.run(dry_run=True)
        assert r.outcome == OUTCOME_DRY_RUN
        assert comms.posts == []
        # State file should not exist.
        state_path = tmp_sovereign / "daemons" / "uncertainty_state.json"
        assert not state_path.exists()

    def test_dry_run_reports_would_halt(self, tmp_sovereign):
        """Dry run respects circuit-breaker state but does not write halt."""
        daemon, _ = make_daemon(tmp_sovereign)
        # Seed a state with three unacked digests.
        state_path = tmp_sovereign / "daemons" / "uncertainty_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "schema_version": STATE_SCHEMA_VERSION,
            "posted_digests": [
                {"message_id": f"m{i}", "posted_at": "2026-04-20T00:00:00+00:00",
                 "content_snippet": "x"}
                for i in range(CONSECUTIVE_UNACKED_THRESHOLD)
            ],
            "halted_at": None,
            "halt_reason": None,
        }))
        r = daemon.run(dry_run=True)
        assert r.outcome == OUTCOME_HALTED
        # No halt file written, no state mutation.
        halts_dir = tmp_sovereign / "daemons" / "halts"
        assert list(halts_dir.glob("*.md")) == []


# ── State schema future-proofing ────────────────────────────────────────────


class TestStateSchema:
    def test_state_file_has_schema_version(self, tmp_sovereign):
        daemon, _ = make_daemon(tmp_sovereign)
        daemon.run()
        state_path = tmp_sovereign / "daemons" / "uncertainty_state.json"
        data = json.loads(state_path.read_text())
        assert data["schema_version"] == STATE_SCHEMA_VERSION

    def test_unversioned_legacy_state_loads_as_v1(self, tmp_sovereign):
        """State files written before schema_version existed must still load."""
        state_path = tmp_sovereign / "daemons" / "uncertainty_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "posted_digests": [],
            "halted_at": None,
            "halt_reason": None,
        }))
        daemon, _ = make_daemon(tmp_sovereign)
        r = daemon.run()
        assert r.outcome in (OUTCOME_POSTED, OUTCOME_NO_UNCERTAINTIES)

    def test_future_schema_version_refuses_to_load(self, tmp_sovereign):
        """If a future daemon wrote a newer schema, refuse rather than
        corrupt data on a silent downgrade."""
        state_path = tmp_sovereign / "daemons" / "uncertainty_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "schema_version": STATE_SCHEMA_VERSION + 1,
            "posted_digests": [],
            "halted_at": None,
            "halt_reason": None,
        }))
        daemon, _ = make_daemon(tmp_sovereign)
        with pytest.raises(ValueError):
            daemon.run()


# ── Idempotency / bookkeeping cap ───────────────────────────────────────────


class TestBookkeeping:
    def test_posted_digests_bounded(self, tmp_sovereign):
        """posted_digests should not grow unbounded — we only need the
        last N for the circuit breaker."""
        daemon, _ = make_daemon(tmp_sovereign)
        # Ack each post as it's made so the daemon never halts.
        # Run 20 iterations.
        for _ in range(20):
            r = daemon.run()
            if r.outcome != OUTCOME_POSTED:
                break
            # Skip acking — the daemon will halt after CONSECUTIVE_UNACKED.

        state_path = tmp_sovereign / "daemons" / "uncertainty_state.json"
        data = json.loads(state_path.read_text())
        # Whatever happened, posted_digests must stay small.
        assert len(data["posted_digests"]) <= 10


# ── Sender taxonomy ─────────────────────────────────────────────────────────


class TestSenderTaxonomy:
    def test_all_posts_use_daemon_prefix(self, tmp_sovereign):
        daemon, comms = make_daemon(tmp_sovereign)
        for _ in range(CONSECUTIVE_UNACKED_THRESHOLD):
            daemon.run()
        daemon.run()  # halt
        for post in comms.posts:
            assert post["sender"].startswith("daemon."), (
                f"All daemon posts must use the daemon.* prefix. "
                f"Got: {post['sender']}"
            )


# ── Real grounded_extract (catches evidence-path shape bugs) ────────────────


class TestRealGrounding:
    """Most tests inject a stub grounding_fn. This class uses the REAL
    grounded_extract so we catch evidence-path shape bugs that mocks
    would mask. Locked in after the metabolize daemon caught a
    chronicle-directory-vs-file bug at live dry-run that all 29 unit
    tests passed through; Step 5+ daemons should mirror this pattern."""

    def test_real_grounded_extract_accepts_uncertainty_log(self, tmp_sovereign):
        """uncertainty_log.json is a non-chronicle path — grounded_extract
        accepts it as structural evidence on existence alone."""
        comms = CommsStore()
        daemon = UncertaintyResurfacer(
            state_path=tmp_sovereign / "daemons" / "uncertainty_state.json",
            halt_dir=tmp_sovereign / "daemons" / "halts",
            uncertainty_log_path=tmp_sovereign / "consciousness" / "uncertainty_log.json",
            compass_fn=lambda action, stakes: {"decision": COMPASS_PROCEED},
            uncertainty_fn=lambda: [
                {
                    "marker_id": "u1",
                    "what": "Test uncertainty",
                    "timestamp": "2026-04-15T00:00:00+00:00",
                },
            ],
            comms_post_fn=comms.post,
            comms_get_acks_fn=comms.get_acks,
            # grounding_fn defaults to real grounded_extract
        )
        r = daemon.run()
        assert r.outcome == OUTCOME_POSTED, (
            f"Real grounded_extract must accept uncertainty_log.json. "
            f"Got outcome={r.outcome}, grounding_reason={r.grounding_reason}."
        )
        assert r.grounding_reason == REASON_OK


# ── Unacked-threshold override ──────────────────────────────────────────────


class TestThresholdOverride:
    def test_custom_threshold_is_respected(self, tmp_sovereign):
        """Thresholds are injectable — different daemons may tune differently."""
        daemon, _ = make_daemon(tmp_sovereign, unacked_threshold=2)
        daemon.run()
        daemon.run()
        r = daemon.run()
        assert r.outcome == OUTCOME_HALTED
