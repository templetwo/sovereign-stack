"""
Per-turn priors tests — Step 1 of the v1.3.2 reflection-daemons lineage.

Verifies the contract documented in the handoff from opus-4-7-web (2026-04-25):

1. Hard token budget: returned block never exceeds max_tokens.
2. k=1 default per ReasoningBank (ICLR 2026) — no flooding.
3. Freshness penalty: items surfaced in the last FRESHNESS_WINDOW calls
   take a score hit and drop out (sycophancy guardrail from Jain et al.
   MIT/IDSS 2026).
4. Empty input: no domain_tags, no uncertainties, no honks → empty:True.
5. Drift surfacing: uneasy/sharp Nape honk within HONK_WINDOW_SECONDS
   appears in priors.
6. Uncertainty surfacing: oldest unresolved uncertainty appears.
7. Priority order: drift > uncertainty > thread > insight (preserved under
   token-budget truncation).
8. dry_run=True does not write to the freshness log.
9. Structured output schema is complete.
10. Idempotent: calling twice with dry_run=True returns equivalent blocks.
"""

import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.reflexive import (
    PerTurnPriors,
    ReflexiveSurface,
    _estimate_tokens,
    _item_signature,
)


@pytest.fixture
def sovereign_root():
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
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


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.utcnow() + timedelta(seconds=offset_seconds)).isoformat()


def _make_priors(surface, root, uncertainty=None, honks=None):
    return PerTurnPriors(
        surface=surface,
        sovereign_root=root,
        uncertainty_fn=(lambda: uncertainty) if uncertainty is not None else None,
        honks_fn=(lambda: honks) if honks is not None else None,
    )


# ── Case 1: token budget ─────────────────────────────────────────────────────


class TestTokenBudget:
    def test_block_never_exceeds_max_tokens(self, surface, sovereign_root, memory):
        """A reasonable max_tokens ceiling must be respected even with many candidates."""
        for i in range(20):
            memory.record_open_thread(
                f"Thread {i} " + ("filler " * 30),
                domain="alpha,beta",
            )
        priors = _make_priors(surface, sovereign_root)
        result = priors.inject(
            domain_tags=["alpha", "beta"],
            k=3,
            max_tokens=100,
            dry_run=True,
        )
        assert result["token_estimate"] <= 100, (
            f"Block exceeded 100-token budget: {result['token_estimate']}"
        )

    def test_token_budget_too_tight_still_returns_something_or_empty(
        self, surface, sovereign_root, memory
    ):
        """A very tight budget either truncates to fit or returns empty cleanly."""
        memory.record_open_thread("A thread " + ("x" * 500), domain="alpha")
        priors = _make_priors(surface, sovereign_root)
        result = priors.inject(domain_tags=["alpha"], max_tokens=50, dry_run=True)
        assert result["token_estimate"] <= 50


# ── Case 2: k=1 default ──────────────────────────────────────────────────────


class TestKDefaults:
    def test_k_default_is_1_per_bucket(self, surface, sovereign_root, memory):
        """With many matching threads, k=1 returns exactly one thread section."""
        for i in range(5):
            memory.record_open_thread(f"Thread {i}", domain="topic")
        priors = _make_priors(surface, sovereign_root)
        result = priors.inject(domain_tags=["topic"], dry_run=True)
        thread_count = sum(1 for sig in result["included_items"] if sig.startswith("thread:"))
        assert thread_count == 1, (
            f"Default k=1 should return exactly one thread, got {thread_count}"
        )

    def test_k_is_clamped_to_3(self, surface, sovereign_root, memory):
        """Even k=99 must be capped at 3 to preserve the flood guardrail."""
        for i in range(10):
            memory.record_open_thread(f"Thread {i}", domain="topic")
        priors = _make_priors(surface, sovereign_root)
        result = priors.inject(domain_tags=["topic"], k=99, max_tokens=4000, dry_run=True)
        thread_count = sum(1 for sig in result["included_items"] if sig.startswith("thread:"))
        assert thread_count <= 3


# ── Case 3: freshness penalty ────────────────────────────────────────────────


class TestFreshnessPenalty:
    def test_repeated_item_drops_out_on_second_call(self, surface, sovereign_root, memory):
        """The same thread surfaced twice in a row hits the freshness penalty
        on the second call and lands in skipped_stale."""
        memory.record_open_thread("Only thread", domain="solo")
        priors = _make_priors(surface, sovereign_root)

        first = priors.inject(domain_tags=["solo"], dry_run=False)
        assert first["included_items"], "First call should surface the thread"
        stale_sig = first["included_items"][0]

        second = priors.inject(domain_tags=["solo"], dry_run=False)
        assert stale_sig in second["skipped_stale"], (
            "Second call should demote the thread into skipped_stale"
        )

    def test_dry_run_does_not_affect_freshness(self, surface, sovereign_root, memory):
        """dry_run=True must not write to the freshness log, so subsequent
        real calls still see the item as fresh."""
        memory.record_open_thread("Only thread", domain="solo")
        priors = _make_priors(surface, sovereign_root)

        priors.inject(domain_tags=["solo"], dry_run=True)
        priors.inject(domain_tags=["solo"], dry_run=True)

        real = priors.inject(domain_tags=["solo"], dry_run=False)
        assert real["included_items"], "dry_run calls must not pollute the freshness log"
        assert not real["skipped_stale"], "No items should be demoted after only dry_run priors"

    def test_freshness_window_is_bounded(self, surface, sovereign_root, memory):
        """After FRESHNESS_WINDOW+1 calls that surface nothing, the item
        becomes fresh again because the sliding window moved past it.

        Empty-tag calls with no uncertainty/honk sources are the cleanest
        displacement: they write an empty log record but contribute no new
        stale ids, so the sliding window still advances."""
        memory.record_open_thread("Only thread", domain="solo")
        priors = _make_priors(surface, sovereign_root)

        priors.inject(domain_tags=["solo"], dry_run=False)
        # Empty-tag calls with no injected signals push the old entry out
        # without re-surfacing it (threads bucket is skipped when tags empty).
        for _ in range(priors.FRESHNESS_WINDOW + 1):
            priors.inject(domain_tags=[], dry_run=False)

        rehit = priors.inject(domain_tags=["solo"], dry_run=False)
        assert rehit["included_items"], (
            "Item should be fresh again after falling out of FRESHNESS_WINDOW"
        )


# ── Case 4: empty input ──────────────────────────────────────────────────────


class TestEmptyPath:
    def test_empty_tags_no_signals_returns_empty(self, surface, sovereign_root):
        """No tags, no uncertainty, no honks → empty:True with zero tokens."""
        priors = _make_priors(surface, sovereign_root)
        result = priors.inject(domain_tags=[], dry_run=True)
        assert result["empty"] is True
        assert result["block"] == ""
        assert result["token_estimate"] == 0
        assert result["included_items"] == []

    def test_empty_tags_with_honk_still_surfaces_drift(self, surface, sovereign_root):
        """Drift is tag-independent — an uneasy honk surfaces even with no tags."""
        honks = [
            {
                "honk_id": "h1",
                "level": "uneasy",
                "pattern": "repeated_mistake",
                "trigger_tool": "record_insight",
                "timestamp": _now_iso(),
            }
        ]
        priors = _make_priors(surface, sovereign_root, honks=honks)
        result = priors.inject(domain_tags=[], dry_run=True)
        assert not result["empty"]
        assert "drift" in result["sources"]


# ── Case 5: drift surfacing ──────────────────────────────────────────────────


class TestDriftSurfacing:
    def test_recent_uneasy_honk_surfaces(self, surface, sovereign_root):
        honks = [
            {
                "honk_id": "h1",
                "level": "uneasy",
                "pattern": "repeated_mistake",
                "trigger_tool": "record_insight",
                "timestamp": _now_iso(),
            }
        ]
        priors = _make_priors(surface, sovereign_root, honks=honks)
        result = priors.inject(domain_tags=["anything"], dry_run=True)
        assert "drift" in result["sources"]
        assert "repeated_mistake" in result["block"]

    def test_old_honk_does_not_surface(self, surface, sovereign_root):
        """A honk older than HONK_WINDOW_SECONDS is ignored."""
        honks = [
            {
                "honk_id": "h_old",
                "level": "uneasy",
                "pattern": "declare_before_verify",
                "trigger_tool": "record_insight",
                "timestamp": _now_iso(offset_seconds=-3600),  # 1h ago
            }
        ]
        priors = _make_priors(surface, sovereign_root, honks=honks)
        result = priors.inject(domain_tags=["x"], dry_run=True)
        assert "drift" not in result["sources"]

    def test_satisfied_honk_is_not_drift(self, surface, sovereign_root):
        """Satisfied honks are positive, not drift — never surface as priors."""
        honks = [
            {
                "honk_id": "h_sat",
                "level": "satisfied",
                "pattern": "clean_pattern",
                "trigger_tool": "record_insight",
                "timestamp": _now_iso(),
            }
        ]
        priors = _make_priors(surface, sovereign_root, honks=honks)
        result = priors.inject(domain_tags=["x"], dry_run=True)
        assert "drift" not in result["sources"]


# ── Case 6: uncertainty surfacing ────────────────────────────────────────────


class TestUncertaintySurfacing:
    def test_oldest_uncertainty_surfaces_first(self, surface, sovereign_root):
        """Oldest-first ordering — the nag function."""
        uncertainties = [
            {"marker_id": "u1", "what": "Young question", "timestamp": _now_iso()},
            {
                "marker_id": "u2",
                "what": "Old question",
                "timestamp": _now_iso(offset_seconds=-86400 * 10),
            },
        ]
        priors = _make_priors(surface, sovereign_root, uncertainty=uncertainties)
        result = priors.inject(domain_tags=[], dry_run=True)
        assert "uncertainty:u2" in result["included_items"]

    def test_no_uncertainty_fn_means_no_surface(self, surface, sovereign_root):
        """If uncertainty_fn is None, the bucket is silently skipped."""
        priors = _make_priors(surface, sovereign_root)  # no uncertainty_fn
        result = priors.inject(domain_tags=[], dry_run=True)
        assert "uncertainty" not in result["sources"]


# ── Case 7: priority order ───────────────────────────────────────────────────


class TestPriorityOrder:
    def test_drift_precedes_thread_in_block(self, surface, sovereign_root, memory):
        """Drift line appears before thread line — priority 0 before priority 2."""
        memory.record_open_thread("A thread", domain="topic")
        honks = [
            {
                "honk_id": "h1",
                "level": "sharp",
                "pattern": "premature_summary",
                "trigger_tool": "record_insight",
                "timestamp": _now_iso(),
            }
        ]
        priors = _make_priors(surface, sovereign_root, honks=honks)
        result = priors.inject(domain_tags=["topic"], k=1, max_tokens=400, dry_run=True)
        block = result["block"]
        drift_pos = block.find("drift:")
        thread_pos = block.find("thread:")
        assert drift_pos >= 0 and thread_pos > drift_pos

    def test_low_priority_dropped_first_under_tight_budget(self, surface, sovereign_root, memory):
        """Under a tight budget, insight (priority 3) drops before drift (0)."""
        memory.record_insight(
            content="Some insight " + ("filler " * 10),
            domain="topic",
        )
        memory.record_open_thread("A thread", domain="topic")
        honks = [
            {
                "honk_id": "h1",
                "level": "uneasy",
                "pattern": "declare_before_verify",
                "trigger_tool": "x",
                "timestamp": _now_iso(),
            }
        ]
        priors = _make_priors(surface, sovereign_root, honks=honks)
        result = priors.inject(domain_tags=["topic"], k=1, max_tokens=60, dry_run=True)
        # Drift must survive the budget.
        assert "drift" in result["sources"]


# ── Case 8: dry_run ──────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_does_not_create_log(self, surface, sovereign_root, memory):
        memory.record_open_thread("A thread", domain="topic")
        priors = _make_priors(surface, sovereign_root)
        log_path = sovereign_root / "reflexive" / "priors_log.jsonl"
        priors.inject(domain_tags=["topic"], dry_run=True)
        assert not log_path.exists() or log_path.read_text() == ""

    def test_non_dry_run_writes_log(self, surface, sovereign_root, memory):
        memory.record_open_thread("A thread", domain="topic")
        priors = _make_priors(surface, sovereign_root)
        log_path = sovereign_root / "reflexive" / "priors_log.jsonl"
        priors.inject(domain_tags=["topic"], dry_run=False)
        assert log_path.exists()
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert "timestamp" in rec and "included_items" in rec


# ── Case 9: output schema ────────────────────────────────────────────────────


class TestOutputSchema:
    def test_all_fields_present_when_empty(self, surface, sovereign_root):
        priors = _make_priors(surface, sovereign_root)
        result = priors.inject(domain_tags=[], dry_run=True)
        for field in (
            "block",
            "included_items",
            "skipped_stale",
            "empty",
            "token_estimate",
            "sources",
        ):
            assert field in result

    def test_all_fields_present_when_populated(self, surface, sovereign_root, memory):
        memory.record_open_thread("A thread", domain="topic")
        priors = _make_priors(surface, sovereign_root)
        result = priors.inject(domain_tags=["topic"], dry_run=True)
        for field in (
            "block",
            "included_items",
            "skipped_stale",
            "empty",
            "token_estimate",
            "sources",
        ):
            assert field in result
        assert isinstance(result["block"], str)
        assert isinstance(result["included_items"], list)
        assert isinstance(result["skipped_stale"], list)
        assert isinstance(result["empty"], bool)
        assert isinstance(result["token_estimate"], int)
        assert isinstance(result["sources"], list)


# ── Case 10: idempotency under dry_run ───────────────────────────────────────


class TestIdempotency:
    def test_repeat_dry_run_returns_equivalent_block(self, surface, sovereign_root, memory):
        """Two consecutive dry_run calls see the same state, so the block text
        should be identical (timestamps on output are not included)."""
        memory.record_open_thread("A thread", domain="topic")
        priors = _make_priors(surface, sovereign_root)
        a = priors.inject(domain_tags=["topic"], dry_run=True)
        b = priors.inject(domain_tags=["topic"], dry_run=True)
        assert a["block"] == b["block"]
        assert a["included_items"] == b["included_items"]


# ── Sanity: helpers ──────────────────────────────────────────────────────────


class TestHelpers:
    def test_item_signature_uses_thread_id(self):
        sig = _item_signature("thread", {"thread_id": "t_abc", "question": "x"})
        assert sig == "thread:t_abc"

    def test_estimate_tokens_is_positive(self):
        assert _estimate_tokens("hello world") >= 1
        assert _estimate_tokens("") == 1  # floor

    def test_item_signature_fallback_uses_content(self):
        sig = _item_signature("insight", {"content": "hello", "timestamp": "2026-01-01T00:00:00"})
        assert sig.startswith("insight:hello")
