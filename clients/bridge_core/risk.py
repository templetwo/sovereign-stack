"""
Risk classification for Ring 2 write proposals.

Substrate-agnostic — same identity-inflation patterns and ground-truth
escalation rules apply regardless of which substrate is proposing.

Risk informs the audit trail and review UX. It does not gate approval —
that is Anthony's call. But it surfaces the right questions before he
decides.
"""

from enum import Enum


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Tools and their baseline risk before content inspection.
# Same baselines for all substrates — Ring 2 tools are common across bridges.
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
    # Chronicle writes — medium base; layer + receipt status escalates
    "propose_insight": RiskLevel.MEDIUM,
    "propose_learning": RiskLevel.MEDIUM,
    # Aliases for direct tool names (should not reach Ring 2 — defensive)
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


def _contains_identity_claim(args: dict) -> bool:
    text = " ".join(
        str(v).lower() for v in _flatten_values(args) if isinstance(v, str)
    )
    return any(pattern in text for pattern in _IDENTITY_CLAIM_PATTERNS)


def risk_classify(tool_name: str, args: dict) -> tuple[RiskLevel, list[str]]:
    """Classify a Ring 2 proposal's risk. Returns (level, reasons)."""
    base = _TOOL_BASE_RISK.get(tool_name, RiskLevel.MEDIUM)
    level = base
    reasons: list[str] = []

    proposed_layer = args.get("layer") or args.get("proposed_layer", "hypothesis")
    has_receipt = bool(args.get("receipt_url") or args.get("receipts"))

    if proposed_layer == "ground_truth" and not has_receipt:
        level = RiskLevel.CRITICAL
        reasons.append("ground_truth layer claimed without a receipt")
    elif proposed_layer == "ground_truth":
        level = _GROUND_TRUTH_ESCALATION[level]
        reasons.append("ground_truth layer — receipt present, escalated for review")

    if _contains_identity_claim(args):
        if level == RiskLevel.LOW:
            level = RiskLevel.HIGH
        elif level == RiskLevel.MEDIUM:
            level = RiskLevel.CRITICAL
        else:
            level = RiskLevel.CRITICAL
        reasons.append("possible identity inflation detected in content")

    intensity = args.get("intensity", 0.0)
    if isinstance(intensity, (int, float)) and intensity > 0.9 and tool_name in (
        "propose_insight", "record_insight"
    ):
        if level.value in ("low", "medium"):
            level = RiskLevel.HIGH
        reasons.append(f"high intensity ({intensity}) on chronicle write")

    if not reasons:
        reasons.append(f"baseline for {tool_name}")

    return level, reasons
