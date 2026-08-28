"""
Risk classification for Ring 2 write proposals.

Risk informs the audit trail and future review UX.
It does not gate approval — that is Anthony's call.
But it surfaces the right questions before he decides.
"""

from enum import Enum


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Tools and their baseline risk before content inspection
_TOOL_BASE_RISK: dict[str, RiskLevel] = {
    # Lowest stakes — acknowledgment / touch
    "comms_acknowledge": RiskLevel.LOW,
    "reflection_ack": RiskLevel.LOW,
    "thread_touch": RiskLevel.LOW,
    # Open questions — low stakes, low blast radius
    "record_open_thread": RiskLevel.LOW,
    # Session state — moderate
    "handoff": RiskLevel.MEDIUM,
    "store_compaction_summary": RiskLevel.MEDIUM,
    "self_model": RiskLevel.MEDIUM,
    "end_bridge_session": RiskLevel.MEDIUM,
    # Chronicle writes — medium base; layer + receipt status escalates from here
    # ground_truth+receipt → HIGH, ground_truth+no_receipt → CRITICAL
    "propose_insight": RiskLevel.MEDIUM,
    "propose_learning": RiskLevel.MEDIUM,
    # Aliases for direct tool names (should not reach Ring 2, but classified defensively)
    "record_insight": RiskLevel.HIGH,
    "record_learning": RiskLevel.HIGH,
}

_GROUND_TRUTH_ESCALATION = {
    RiskLevel.LOW: RiskLevel.MEDIUM,
    RiskLevel.MEDIUM: RiskLevel.HIGH,
    RiskLevel.HIGH: RiskLevel.CRITICAL,
    RiskLevel.CRITICAL: RiskLevel.CRITICAL,
}

_IDENTITY_CLAIM_PATTERNS = [
    "ash'ira",
    "ashira",
    "i remember",
    "native memory",
    "i was there",
    "i wrote this",
    "previous session i",
]


def _contains_identity_claim(args: dict) -> bool:
    """Detect potential identity inflation in any string argument value."""
    text = " ".join(
        str(v).lower() for v in _flatten_values(args) if isinstance(v, str)
    ).lower()
    return any(pattern in text for pattern in _IDENTITY_CLAIM_PATTERNS)


def _flatten_values(obj: object) -> list:
    if isinstance(obj, dict):
        result = []
        for v in obj.values():
            result.extend(_flatten_values(v))
        return result
    if isinstance(obj, list):
        result = []
        for item in obj:
            result.extend(_flatten_values(item))
        return result
    return [obj]


def risk_classify(
    tool_name: str, args: dict, compass_check_result: object = None
) -> tuple[RiskLevel, list[str]]:
    """
    Classify the risk of a Ring 2 write proposal.

    Returns (risk_level, [list of reasons for elevation]).
    """
    base = _TOOL_BASE_RISK.get(tool_name, RiskLevel.MEDIUM)
    level = base
    reasons: list[str] = []

    # Ground truth claim without a receipt is CRITICAL
    proposed_layer = args.get("layer") or args.get("proposed_layer", "hypothesis")
    has_receipt = bool(args.get("receipt_url") or args.get("receipts"))

    if proposed_layer == "ground_truth" and not has_receipt:
        level = RiskLevel.CRITICAL
        reasons.append("ground_truth layer claimed without a receipt")
    elif proposed_layer == "ground_truth":
        level = _GROUND_TRUTH_ESCALATION[level]
        reasons.append("ground_truth layer — receipt present, escalated for review")

    # Identity inflation
    if _contains_identity_claim(args):
        if level == RiskLevel.LOW:
            level = RiskLevel.HIGH
        elif level == RiskLevel.MEDIUM:
            level = RiskLevel.CRITICAL
        else:
            level = RiskLevel.CRITICAL
        reasons.append("possible identity inflation detected in content")

    # Intensity > 0.9 on chronicle writes
    intensity = args.get("intensity", 0.0)
    if isinstance(intensity, (int, float)) and intensity > 0.9 and tool_name in (
        "propose_insight", "record_insight"
    ):
        if level.value in ("low", "medium"):
            level = RiskLevel.HIGH
        reasons.append(f"high intensity ({intensity}) on chronicle write")


    # TARGET-AWARE ESCALATION — danger is not always a property of the verb.
    # comms_acknowledge is genuinely cheap unless what it acknowledges is a
    # consent record for the protected drawer; thread_touch is cheap unless its
    # thread does not exist, in which case it manufactures a permanent reference
    # to nothing. Proposal e1939a23 carried risk_level=low / reasons=['baseline
    # for comms_acknowledge'] / compass=null and sat 55 days: a table keyed on
    # the tool name cannot see what a write POINTS AT. Escalation lands on
    # CRITICAL deliberately, because _precondition_check already refuses to
    # commit a CRITICAL proposal whose compass is not PROCEED.
    try:
        from bridge_core.target_risk import target_escalation_reasons

        target_reasons = target_escalation_reasons(tool_name, args)
    except Exception as exc:  # noqa: BLE001
        # A classifier that cannot check the target must NOT quietly pass it.
        target_reasons = [f"target check unavailable ({type(exc).__name__}) — escalated"]
    if target_reasons:
        level = RiskLevel.CRITICAL
        reasons.extend(target_reasons)

    # WITNESS is the compass's HARD deny; PAUSE is the soft one. Before this,
    # `WITNESS` appeared in this codebase exactly once — as a comment on a type
    # annotation — and no branch anywhere handled it, so the softer signal
    # blocked and the stronger one flowed straight through. Escalating (rather
    # than refusing at create) keeps disclosure cheap: a seat that runs the
    # compass and reports WITNESS must not fare worse than one that never
    # called it. The commit guard is what actually stops it landing.
    # The value must be passed EXPLICITLY: pop_bridge_metadata (dispatch.py:23-36)
    # removes compass_check_result from `args` BEFORE this runs, so reading it
    # from args here was dead code on every production path — reachable only by a
    # unit test handing it a shape the bridge never produces. An adversarial
    # review demonstrated that in both substrates. args is still consulted as a
    # fallback so direct callers keep working, but the parameter is the real path.
    from bridge_core.target_risk import normalize_compass

    _compass = normalize_compass(
        compass_check_result if compass_check_result is not None
        else args.get("compass_check_result")
    )
    if _compass in ("WITNESS", "PAUSE", "UNRECOGNISED"):
        level = RiskLevel.CRITICAL
        reasons.append(
            f"compass result {_compass} — normalised; any spelling of a deny is a deny"
        )

    if not reasons:
        reasons.append(f"baseline for {tool_name}")

    return level, reasons
