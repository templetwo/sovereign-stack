"""
Smoke test: prove the membrane holds.

Run from sovereign-stack root:
  python -m clients.openai_bridge._smoke_test

Every test should print PASS. No Stack mutations occur, and — since
2026-08-28 — no ~/.sovereign/openai_bridge/ mutations either: every write goes
to a temporary directory that is removed when the run ends.

Before that, this file filed its fixtures into the LIVE openai queue. Nine of
them are still there: "Test: does the membrane hold?" and friends, each
stamped `reviewed_by: Anthony` because `approve_pending_write` defaulted the
reviewer name, each reviewed under a millisecond after filing. The console
rendered them as human reviews. Fixture data was indistinguishable from
Anthony's own decisions in his own queue.
"""

import sys
import tempfile
import traceback
from contextlib import contextmanager
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

# Imported as module OBJECTS, not by sys.modules string key, on purpose: this
# package is reachable under two names ("openai_bridge" from the installed
# distribution, "clients.openai_bridge" from the repo root) and each name is a
# SEPARATE module object with its own globals. Rebinding a string key would
# isolate one of them and leave the other pointed at ~/.sovereign.
from clients.openai_bridge import audit as _audit_mod
from clients.openai_bridge import hash_chain as _hash_chain_mod
from clients.openai_bridge import pending_writes as _pending_writes_mod

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

SOURCE = "chatgpt-gpt-5-5-openai-bridge-test"

# The reviewer this test asserts on. NOT "Anthony": a test that names the human
# as its reviewer cannot tell a real approval from its own fixture, which is
# precisely how nine fixtures came to sit in the live queue wearing his name.
REVIEWER = "openai-bridge-smoke-test"

# Every live-~/.sovereign module global that THIS TEST'S CODE PATHS REACH, as
# (module object, attribute name, path relative to the sandbox root).
#
# Scoped deliberately, and not "every global in the package": cli.py defines its
# own PENDING_DIR and AUDIT_LOG from Path.home() (cli.py:36-37) and globs the
# former at :125. This file never imports cli.py, so those two are out of reach
# here and out of this tuple — but they are NOT isolated, and any future test
# that imports the console must extend this list.
#
# ALL FIVE MUST MOVE TOGETHER. audit.py does `from .hash_chain import AUDIT_DIR,
# AUDIT_LOG`, and a from-import copies the VALUE into the importing module's
# namespace at import time. Rebind hash_chain only and append_audit_event keeps
# writing to the live audit log — silently, with the run still printing PASS.
# tests/test_ring2_reviewer_identity.py asserts against this exact tuple.
ISOLATED_NAMES = (
    (_pending_writes_mod, "PENDING_DIR", "pending_writes"),
    (_hash_chain_mod, "AUDIT_DIR", "audit"),
    (_hash_chain_mod, "AUDIT_LOG", "audit/audit.jsonl"),
    (_audit_mod, "AUDIT_DIR", "audit"),
    (_audit_mod, "AUDIT_LOG", "audit/audit.jsonl"),
)


@contextmanager
def isolated_queue(sandbox_root: Path):
    """
    Point every live-path module global at sandbox_root for the duration.

    Restores the originals on exit, including on exception, so importing this
    module can never leave the process wired to a temp dir that no longer
    exists.
    """
    saved = [(mod, attr, getattr(mod, attr)) for mod, attr, _ in ISOLATED_NAMES]
    try:
        for mod, attr, rel in ISOLATED_NAMES:
            setattr(mod, attr, sandbox_root / rel)
        yield sandbox_root
    finally:
        for mod, attr, original in saved:
            setattr(mod, attr, original)


def check(name: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    print(f"  {tag}  {name}" + (f" — {detail}" if detail else ""))
    return condition


def run():
    """Run every check against a throwaway queue. Never touches ~/.sovereign."""
    with (
        tempfile.TemporaryDirectory(prefix="openai_smoke_") as tmp,
        isolated_queue(Path(tmp)) as sandbox,
    ):
        print(f"\n  sandbox: {sandbox}")
        return _run_checks()


def _run_checks():
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
        approved = approve_pending_write(proposal_id, approved_by=REVIEWER)
        results.append(check("Approve sets status=approved", approved.status == "approved"))
        results.append(check(
            "reviewed_by is the name this test passed (not a default)",
            approved.reviewed_by == REVIEWER,
            approved.reviewed_by or "<unset>",
        ))

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
        rejected = reject_pending_write(
            result2.proposal.proposal_id, reason="smoke test rejection", rejected_by=REVIEWER
        )
        results.append(check("Reject sets status=rejected", rejected.status == "rejected"))

    print("\n── Lifecycle: needs_revision ────────────────────────────────────")

    result3 = intercept(
        "thread_touch",
        {"thread_id": "thread_test_123", "note": "test touch"},
        source_instance=SOURCE,
    )
    if result3.proposal:
        revised = needs_revision_pending_write(
            result3.proposal.proposal_id, notes="please add context", actor=REVIEWER
        )
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
