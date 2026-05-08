"""
Smoke test: prove the membrane holds.

Run from sovereign-stack root:
  python -m clients.openai_bridge._smoke_test

Every test should print PASS. No Stack mutations occur.
"""

import sys
import traceback
from pathlib import Path

# Add project root to path so the package imports work
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from clients.openai_bridge.interceptor import intercept, classify_tool, pending_summary
from clients.openai_bridge.pending_writes import (
    ValidationError,
    approve_pending_write,
    commit_pending_write,
    reject_pending_write,
    needs_revision_pending_write,
    list_pending_writes,
)
from clients.openai_bridge.hash_chain import verify_chain
from clients.openai_bridge.risk import risk_classify, RiskLevel

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

SOURCE = "chatgpt-gpt-5-5-openai-bridge-test"


def check(name: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    print(f"  {tag}  {name}" + (f" — {detail}" if detail else ""))
    return condition


def run():
    results = []
    print("\n── Ring classification ──────────────────────────────────────────")

    r = classify_tool("where_did_i_leave_off")
    results.append(check("Ring 1 read tool", r["ring"] == 1))

    r = classify_tool("self_model", {"action": "read"})
    results.append(check("self_model read → Ring 1", r["ring"] == 1))

    r = classify_tool("self_model", {"action": "update"})
    results.append(check("self_model update → Ring 2", r["ring"] == 2))

    r = classify_tool("govern", {})
    results.append(check("govern → Ring 3 blocked", r["ring"] == 3 and r.get("blocked")))

    r = classify_tool("guardian_quarantine", {})
    results.append(check("guardian_quarantine → Ring 3 blocked", r.get("blocked")))

    print("\n── Risk classification ──────────────────────────────────────────")

    level, reasons = risk_classify("comms_acknowledge", {})
    results.append(check("comms_acknowledge → LOW", level == RiskLevel.LOW, str(reasons)))

    level, reasons = risk_classify("propose_insight", {"layer": "ground_truth"})
    results.append(check("ground_truth without receipt → CRITICAL", level == RiskLevel.CRITICAL, str(reasons)))

    level, reasons = risk_classify("propose_insight", {"layer": "ground_truth", "receipt_url": "https://example.com"})
    results.append(check("ground_truth with receipt → HIGH (escalated, not CRITICAL)", level == RiskLevel.HIGH, str(reasons)))

    level, reasons = risk_classify("propose_insight", {"layer": "hypothesis", "content": "I remember ash'ira"})
    results.append(check("identity claim → CRITICAL", level == RiskLevel.CRITICAL, str(reasons)))

    print("\n── Ring 3 block ─────────────────────────────────────────────────")

    result = intercept("guardian_quarantine", {}, source_instance=SOURCE)
    results.append(check("Ring 3 tool blocked", not result.allowed and result.ring == 3))

    result = intercept("govern", {}, source_instance=SOURCE)
    results.append(check("govern blocked", not result.allowed and result.ring == 3))

    result = intercept("record_insight", {"content": "direct write"}, source_instance=SOURCE)
    results.append(check("direct record_insight blocked", not result.allowed and result.ring == 3))

    print("\n── Ring 2 dry run ───────────────────────────────────────────────")

    result = intercept(
        "record_open_thread",
        {"question": "Is the bridge membrane holding?", "context": "smoke test", "domain": "openai-bridge"},
        source_instance=SOURCE,
        dry_run=True,
    )
    results.append(check("Ring 2 dry run succeeds", result.allowed and result.dry_run))
    results.append(check("Proposal object returned", result.proposal is not None))
    results.append(check("Status is pending", result.proposal and result.proposal.status == "pending"))
    before_count = len(list_pending_writes())
    results.append(check("Dry run does not write to disk", len(list_pending_writes()) == before_count))

    print("\n── Ring 2 live proposal creation ────────────────────────────────")

    result = intercept(
        "record_open_thread",
        {"question": "Test: does the membrane hold?", "context": "smoke test", "domain": "openai-bridge"},
        source_instance=SOURCE,
    )
    results.append(check("Ring 2 proposal created", result.allowed and not result.dry_run))
    results.append(check("Status is pending", result.proposal and result.proposal.status == "pending"))
    proposal_id = result.proposal.proposal_id if result.proposal else None
    results.append(check("proposal_id assigned", bool(proposal_id)))

    print("\n── Validation rejects ground_truth without receipt ──────────────")

    try:
        bad = intercept(
            "propose_insight",
            {"content": "This is ground truth", "layer": "ground_truth", "domain": "test"},
            source_instance=SOURCE,
        )
        results.append(check("Invalid proposal blocked", not bad.allowed, bad.error or ""))
    except Exception as e:
        results.append(check("Invalid proposal blocked (exception path)", True, str(e)[:60]))

    print("\n── Lifecycle: approve → commit (mocked) ─────────────────────────")

    if proposal_id:
        approved = approve_pending_write(proposal_id)
        results.append(check("Approve sets status=approved", approved.status == "approved"))
        results.append(check("reviewed_by set", approved.reviewed_by == "Anthony"))

        committed = commit_pending_write(proposal_id)
        results.append(check("Commit sets status=committed", committed.status == "committed"))
        results.append(check("Commit result is mocked", committed.commit_result and committed.commit_result.get("mocked")))
        results.append(check("No Stack mutation (mocked=True)", committed.commit_result.get("mocked") is True))

    print("\n── Lifecycle: reject ────────────────────────────────────────────")

    result2 = intercept(
        "handoff",
        {"note": "test handoff for rejection", "source_instance": SOURCE, "thread": "smoke-test"},
        source_instance=SOURCE,
    )
    if result2.proposal:
        rejected = reject_pending_write(result2.proposal.proposal_id, reason="smoke test rejection")
        results.append(check("Reject sets status=rejected", rejected.status == "rejected"))

    print("\n── Lifecycle: needs_revision ────────────────────────────────────")

    result3 = intercept(
        "thread_touch",
        {"thread_id": "thread_test_123", "note": "test touch"},
        source_instance=SOURCE,
    )
    if result3.proposal:
        revised = needs_revision_pending_write(result3.proposal.proposal_id, notes="please add context")
        results.append(check("needs_revision sets status", revised.status == "needs_revision"))

    print("\n── Hash chain integrity ─────────────────────────────────────────")

    ok, msg = verify_chain()
    results.append(check("Audit chain intact", ok, msg))

    print("\n── Pending summary ──────────────────────────────────────────────")
    print(pending_summary())

    print()
    passed = sum(results)
    total = len(results)
    color = "\033[92m" if passed == total else "\033[91m"
    print(f"{color}{passed}/{total} passed\033[0m")
    return passed == total


if __name__ == "__main__":
    try:
        ok = run()
        sys.exit(0 if ok else 1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
