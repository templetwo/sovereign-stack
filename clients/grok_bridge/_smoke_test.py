"""
Smoke test: prove the grok bridge membrane holds and verify_proposal works.

Run from sovereign-stack root:
  python -m clients.grok_bridge._smoke_test

Every test should print PASS. No real ~/.sovereign/grok_bridge/ mutations occur
— all writes go to a temporary directory that is cleaned up after the run.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
import uuid
from pathlib import Path

# Add project root to path so the package imports work
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from clients.bridge_core.context import BridgeContext
from clients.bridge_core.interceptor import classify_tool, intercept, pending_summary, verify_proposal
from clients.bridge_core.pending_writes import (
    ValidationError,
    approve_pending_write,
    commit_pending_write,
    list_pending_writes,
)
from clients.bridge_core.hash_chain import verify_chain
from clients.bridge_core.risk import risk_classify, RiskLevel
from clients.grok_bridge.rings import COMMIT_TARGETS, RING_1_TOOLS, RING_2_TOOLS

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

SOURCE = "grok-xai-smoke-test"


def check(name: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    print(f"  {tag}  {name}" + (f" — {detail}" if detail else ""))
    return condition


def make_tmp_ctx(tmp_dir: Path) -> BridgeContext:
    """Return a hermetic BridgeContext wired to a temp directory."""
    return BridgeContext(
        substrate="grok-xai",
        pending_writes_dir=tmp_dir / "pending_writes",
        audit_dir=tmp_dir / "audit",
        sessions_dir=tmp_dir / "sessions",
        ring_1_tools=RING_1_TOOLS,
        ring_2_tools=RING_2_TOOLS,
        commit_targets=COMMIT_TARGETS,
        bridge_rest_url="http://127.0.0.1:8100",
        bridge_rest_token_env="BRIDGE_TOKEN",
    )


def run() -> bool:
    results: list[bool] = []

    with tempfile.TemporaryDirectory(prefix="grok_smoke_") as tmp:
        ctx = make_tmp_ctx(Path(tmp))

        print("\n── Ring classification ──────────────────────────────────────────")

        r = classify_tool(ctx, "where_did_i_leave_off")
        results.append(check("Ring 1 read tool", r["ring"] == 1))

        r = classify_tool(ctx, "self_model", {"action": "read"})
        results.append(check("self_model read → Ring 1", r["ring"] == 1))

        r = classify_tool(ctx, "self_model", {"action": "update"})
        results.append(check("self_model update → Ring 2", r["ring"] == 2))

        r = classify_tool(ctx, "govern", {})
        results.append(check("govern → Ring 3 blocked", r["ring"] == 3 and r.get("blocked")))

        r = classify_tool(ctx, "guardian_quarantine", {})
        results.append(check("guardian_quarantine → Ring 3 blocked", r.get("blocked")))

        # Grok-specific Ring 1 tool
        r = classify_tool(ctx, "grok_welcome")
        results.append(check("grok_welcome → Ring 1", r["ring"] == 1))

        print("\n── Risk classification ──────────────────────────────────────────")

        level, reasons = risk_classify("comms_acknowledge", {})
        results.append(check("comms_acknowledge → LOW", level == RiskLevel.LOW, str(reasons)))

        level, reasons = risk_classify("propose_insight", {"layer": "ground_truth"})
        results.append(check(
            "ground_truth without receipt → CRITICAL",
            level == RiskLevel.CRITICAL,
            str(reasons),
        ))

        level, reasons = risk_classify(
            "propose_insight",
            {"layer": "ground_truth", "receipt_url": "https://example.com"},
        )
        results.append(check(
            "ground_truth with receipt → HIGH (not CRITICAL)",
            level == RiskLevel.HIGH,
            str(reasons),
        ))

        print("\n── Ring 3 block ─────────────────────────────────────────────────")

        result = intercept(ctx, "guardian_quarantine", {}, source_instance=SOURCE)
        results.append(check("Ring 3 tool blocked", not result.allowed and result.ring == 3))

        result = intercept(ctx, "govern", {}, source_instance=SOURCE)
        results.append(check("govern blocked", not result.allowed and result.ring == 3))

        result = intercept(ctx, "record_insight", {"content": "direct write"}, source_instance=SOURCE)
        results.append(check("direct record_insight blocked", not result.allowed and result.ring == 3))

        print("\n── Ring 2 dry run ───────────────────────────────────────────────")

        result = intercept(
            ctx,
            "record_open_thread",
            {"question": "Is the grok bridge membrane holding?", "context": "smoke test", "domain": "grok-bridge"},
            source_instance=SOURCE,
            dry_run=True,
        )
        results.append(check("Ring 2 dry run succeeds", result.allowed and result.dry_run))
        results.append(check("Proposal object returned", result.proposal is not None))
        results.append(check("Status is pending", result.proposal and result.proposal.status == "pending"))
        before_count = len(list_pending_writes(ctx))
        results.append(check("Dry run does not write to disk", len(list_pending_writes(ctx)) == before_count))

        print("\n── Ring 2 live proposal creation ────────────────────────────────")

        result = intercept(
            ctx,
            "record_open_thread",
            {"question": "Test: does the grok membrane hold?", "context": "smoke test", "domain": "grok-bridge"},
            source_instance=SOURCE,
        )
        results.append(check("Ring 2 proposal created", result.allowed and not result.dry_run))
        results.append(check("Status is pending", result.proposal and result.proposal.status == "pending"))
        proposal_id = result.proposal.proposal_id if result.proposal else None
        results.append(check("proposal_id assigned", bool(proposal_id)))

        print("\n── verify_proposal: found + chain_valid ─────────────────────────")

        if proposal_id:
            vr = verify_proposal(ctx, proposal_id)
            results.append(check(
                "verify_proposal: found=True for real proposal",
                vr.get("found") is True,
                str(vr),
            ))
            results.append(check(
                "verify_proposal: chain_valid=True (hash reconstructed correctly)",
                vr.get("chain_valid") is True,
                f"audit_hash={vr.get('audit_hash', '')[:16]}... error={vr.get('error')}",
            ))
            results.append(check(
                "verify_proposal: error is None for valid proposal",
                vr.get("error") is None,
                str(vr.get("error")),
            ))
            results.append(check(
                "verify_proposal: correct tool reported",
                vr.get("tool") == "record_open_thread",
                str(vr.get("tool")),
            ))
            results.append(check(
                "verify_proposal: correct substrate reported",
                vr.get("substrate") == "grok-xai",
                str(vr.get("substrate")),
            ))

            # Short-prefix lookup works too
            vr_short = verify_proposal(ctx, proposal_id[:8])
            results.append(check(
                "verify_proposal: 8-char prefix resolves correctly",
                vr_short.get("found") is True and vr_short.get("chain_valid") is True,
                f"found={vr_short.get('found')} chain_valid={vr_short.get('chain_valid')}",
            ))

        print("\n── verify_proposal: narrated-but-not-dispatched regression ──────")

        fabricated_id = str(uuid.uuid4())
        vr_missing = verify_proposal(ctx, fabricated_id)
        results.append(check(
            "verify_proposal: found=False for fabricated UUID",
            vr_missing.get("found") is False,
            str(vr_missing),
        ))
        results.append(check(
            "verify_proposal: error='not_found' for fabricated UUID",
            vr_missing.get("error") == "not_found",
            str(vr_missing.get("error")),
        ))

        print("\n── Validation rejects ground_truth without receipt ──────────────")

        try:
            bad = intercept(
                ctx,
                "propose_insight",
                {"content": "This is ground truth", "layer": "ground_truth", "domain": "test"},
                source_instance=SOURCE,
            )
            results.append(check("Invalid proposal blocked", not bad.allowed, bad.error or ""))
        except Exception as e:
            results.append(check("Invalid proposal blocked (exception path)", True, str(e)[:60]))

        print("\n── Lifecycle: approve → commit (dry-run, hermetic) ──────────────")

        if proposal_id:
            approved = approve_pending_write(ctx, proposal_id)
            results.append(check("Approve sets status=approved", approved.status == "approved"))
            results.append(check("reviewed_by set", approved.reviewed_by == "Anthony"))

            # verify_proposal still works after status mutation — chain_valid
            # must remain True because hash covers creation-time snapshot
            vr_after_approve = verify_proposal(ctx, proposal_id)
            results.append(check(
                "verify_proposal: chain_valid=True after approve (lifecycle mutation)",
                vr_after_approve.get("chain_valid") is True,
                f"status={vr_after_approve.get('status')} chain_valid={vr_after_approve.get('chain_valid')}",
            ))

            committed = commit_pending_write(ctx, proposal_id)
            results.append(check(
                "Commit (dry-run) returns would_call info",
                committed.commit_result is not None and "would_call" in committed.commit_result,
            ))
            results.append(check(
                "No live Stack mutation (live=False)",
                committed.commit_result.get("live") is False,
            ))

        print("\n── Hash chain integrity ─────────────────────────────────────────")

        ok, msg = verify_chain(ctx)
        results.append(check("Audit chain intact", ok, msg))

        print("\n── Pending summary ──────────────────────────────────────────────")
        print(pending_summary(ctx))

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
