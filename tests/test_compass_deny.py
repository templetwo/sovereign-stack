"""Tests for the compass deny signal — through the REAL gate, not around it.

WHY THIS FILE EXISTS SEPARATELY. An adversarial review on 2026-08-28 found that
`test_witness_escalates_to_critical` in test_target_risk.py "would still pass
unchanged even if _precondition_check's WITNESS block were deleted outright,
because it never calls that function." It tested `risk_classify` with
`compass_check_result` placed inside `args` — a shape production never produces,
because `pop_bridge_metadata` (dispatch.py:23-36) removes that key from `args`
BEFORE `risk_classify` runs. The gate was certified by an instrument that could
not exercise it. That is the OC-sim category error: certify instrument A, license
instrument B.

So every test here drives `_precondition_check` and `validate_pending_write`
directly — the functions the bridge actually calls — in BOTH substrates. And
test_the_gate_can_actually_fail is the law-#2 control: it removes the gate and
asserts the suite notices. A gate that cannot be shown to fail has not been shown
to work.

No I/O: Proposal objects are built in memory. Nothing touches a live queue.
"""

from __future__ import annotations

import pytest
from bridge_core.pending_writes import Proposal
from bridge_core.pending_writes import _precondition_check as core_precheck
from bridge_core.pending_writes import validate_pending_write as core_validate
from bridge_core.target_risk import compass_commit_block_reason
from openai_bridge.pending_writes import _precondition_check as oai_precheck

# Every spelling the adversary demonstrated committing clean against `== "WITNESS"`.
BYPASS_SPELLINGS = [
    "witness",
    "Witness",
    "WITNESS ",
    " WITNESS",
    "WITNESS\n",
    "WiTnEsS",
    "​WITNESS",
    "WITNESS ",
]
PAUSE_SPELLINGS = ["pause", "Pause ", "PAUSE\n", "pAuSe"]


def _proposal(compass, tool="record_open_thread", status="approved"):
    return Proposal(
        proposal_id="p-test",
        timestamp="2026-08-28T00:00:00",
        source_instance="test",
        session_id="s",
        substrate="test",
        tool=tool,
        arguments={"question": "ordinary", "context": "c", "domain": "d"},
        commit_target=tool,
        proposed_layer="hypothesis",
        has_receipt=False,
        receipt_urls=[],
        risk_level="low",
        risk_reasons=["baseline"],
        compass_check_result=compass,
        compass_check_rationale=None,
        status=status,
    )


class _Ctx:
    """Minimal context — _precondition_check only reads commit_targets."""

    commit_targets = {"record_open_thread": "record_open_thread"}


def _compass_errors(errors):
    return [e for e in errors if "compass" in e.lower()]


# ── The bypass, closed, in both substrates ───────────────────────────────────


@pytest.mark.parametrize("spelling", BYPASS_SPELLINGS)
def test_witness_spellings_blocked_at_commit_bridge_core(spelling):
    errs = core_precheck(_Ctx(), _proposal(spelling))
    assert _compass_errors(errs), f"{spelling!r} committed clean"


@pytest.mark.parametrize("spelling", BYPASS_SPELLINGS)
def test_witness_spellings_blocked_at_commit_openai_legacy(spelling):
    """The openai substrate dispatches through a SEPARATE legacy module. A fix
    present in bridge_core only would leave half the traffic bypassable."""
    errs = oai_precheck(_proposal(spelling))
    assert _compass_errors(errs), f"{spelling!r} committed clean on openai"


@pytest.mark.parametrize("spelling", PAUSE_SPELLINGS + ["PAUSE"])
def test_pause_is_rechecked_at_commit_not_only_at_create(spelling):
    """PAUSE was checked ONLY at create before this, so a mangled-case PAUSE that
    slipped creation was never caught again."""
    assert _compass_errors(core_precheck(_Ctx(), _proposal(spelling)))
    assert _compass_errors(oai_precheck(_proposal(spelling)))


def test_canonical_witness_still_blocked():
    """Sanity: the exact spelling was always caught and must remain caught."""
    assert _compass_errors(core_precheck(_Ctx(), _proposal("WITNESS")))


@pytest.mark.parametrize("junk", ["garbage", "", "  ", "PROCEEDING", "OK", "yes"])
def test_unrecognised_values_block_rather_than_pass(junk):
    """An unknown string is not null. It is a typo or an attempt, and neither is
    something to commit on."""
    errs = core_precheck(_Ctx(), _proposal(junk))
    if junk.strip():
        assert _compass_errors(errs), f"{junk!r} committed clean"


def test_non_string_compass_blocks():
    for junk in (123, True, ["WITNESS"], {"v": "WITNESS"}):
        assert _compass_errors(core_precheck(_Ctx(), _proposal(junk)))


# ── The allow direction must NOT be loosened ─────────────────────────────────


def test_exact_proceed_is_the_only_allow():
    """Normalising the DENY checks can only ever block more. Normalising the
    ALLOW check would let 'proceed' satisfy a gate it does not satisfy today —
    loosening the one comparison whose failure mode is permitting a write."""
    assert compass_commit_block_reason("PROCEED") is None
    assert _compass_errors(core_precheck(_Ctx(), _proposal("PROCEED"))) == []


def test_absent_compass_is_not_a_deny():
    """None means no compass was claimed. The risk rules handle that by requiring
    one at CRITICAL; it is not itself a refusal."""
    assert compass_commit_block_reason(None) is None
    assert _compass_errors(core_precheck(_Ctx(), _proposal(None))) == []


# ── Create-time: anything reaching storage is exactly canonical ──────────────


@pytest.mark.parametrize("spelling", BYPASS_SPELLINGS + PAUSE_SPELLINGS + ["proceed", "Proceed"])
def test_non_canonical_spellings_refused_at_create(spelling):
    p = _proposal(spelling, status="pending")
    errs = core_validate(_Ctx(), p)
    assert _compass_errors(errs), f"{spelling!r} was accepted at create"


# ── LAW #2: the gate must be demonstrably able to FAIL ───────────────────────


def test_the_gate_can_actually_fail(monkeypatch):
    """Remove the gate; the deny must stop being detected.

    Without this, every test above could be passing for a reason unrelated to
    the gate — which is exactly what happened to the test this file replaces. If
    this assertion ever fails, the tests above are no longer testing the gate.
    """
    import bridge_core.target_risk as tr

    before = _compass_errors(core_precheck(_Ctx(), _proposal("witness")))
    assert before, "precondition failed to block 'witness' even before mutation"

    before_oai = _compass_errors(oai_precheck(_proposal("witness")))
    assert before_oai, "openai precondition failed to block 'witness' before mutation"

    monkeypatch.setattr(tr, "compass_commit_block_reason", lambda raw: None)
    after = _compass_errors(core_precheck(_Ctx(), _proposal("witness")))
    assert after == [], (
        "removing compass_commit_block_reason changed nothing — the tests above "
        "are not actually exercising this gate"
    )
    # The openai substrate dispatches through a separate legacy module. Proving
    # sensitivity for bridge_core alone would leave half the traffic's gate
    # uncertified — the exact half-fix shape that bit this change once already.
    after_oai = _compass_errors(oai_precheck(_proposal("witness")))
    assert after_oai == [], (
        "the openai gate did not respond to the mutation — its guarantee is not "
        "demonstrated by this suite"
    )


# ── The STORED risk_level must see the compass, not just the transient one ───


def test_stored_risk_level_sees_the_compass_value():
    """`create_pending_write` recomputes risk independently of the interceptor.

    Before this, it called `risk_classify(tool_name, args)` with no compass
    kwarg — and `pop_bridge_metadata` had already stripped the key from `args` —
    so an exact-spelled WITNESS stored `risk_level="low"`. Commit was still
    blocked (the compass check is independent of risk_level), but the proposal
    rendered GREEN/LOW in `bridge list-pending`: the same visual profile as
    e1939a23, the write this guard exists to stop. A gate that blocks correctly
    while displaying "safe" trains the human to trust the wrong signal.
    """
    from bridge_core.risk import RiskLevel, risk_classify

    args_after_pop = {"question": "q", "context": "c", "domain": "d"}
    level, reasons = risk_classify(
        "record_open_thread", args_after_pop, compass_check_result="WITNESS"
    )
    assert level == RiskLevel.CRITICAL, f"stored risk would render {level}"
    assert any("WITNESS" in r for r in reasons)


@pytest.mark.parametrize("spelling", ["witness", "WITNESS ", "pause", "garbage"])
def test_stored_risk_level_escalates_on_every_deny_spelling(spelling):
    from bridge_core.risk import RiskLevel, risk_classify

    level, _ = risk_classify("record_open_thread", {}, compass_check_result=spelling)
    assert level == RiskLevel.CRITICAL


def test_proceed_does_not_escalate_stored_risk():
    """Positive control — escalation must be selective, or the field is noise."""
    from bridge_core.risk import RiskLevel, risk_classify

    level, _ = risk_classify("record_open_thread", {}, compass_check_result="PROCEED")
    assert level == RiskLevel.LOW


# ── Finding D: the third substrate had no channel for the compass at all ────


def test_antigravity_can_carry_a_compass_result():
    """`governed_call` passed no compass kwarg to `intercept`, so this substrate
    could never populate `proposal.compass_check_result` — it was always None,
    making the entire deny mechanism structurally inert there, before AND after
    the hardening. Not a regression; a third entry point outside the fix's
    stated scope."""
    import inspect

    from antigravity_connector import bridge_setup

    sig = inspect.signature(bridge_setup.governed_call)
    assert "compass_check_result" in sig.parameters
    assert "compass_check_rationale" in sig.parameters
    src = inspect.getsource(bridge_setup.governed_call)
    assert "compass_check_result=" in src, "accepted but never forwarded to intercept"
