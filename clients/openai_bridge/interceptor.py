from __future__ import annotations

"""
Ring 2 interceptor for the OpenAI bridge.

This is the membrane. When an OpenAI instance calls a Ring 2 tool via
/openai/sse, the call never reaches the Stack directly. It comes here first.

filter → frame → govern → audit → propose

The only possible outcome of a Ring 2 call is:
  - A proposal file in ~/.sovereign/openai_bridge/pending_writes/  (live)
  - A validated Proposal object returned to the caller               (dry_run)
  - A ValidationError raised with the reasons                        (invalid)

Nothing commits to the chronicle. Nothing mutates Stack state.
"""

import logging
from dataclasses import dataclass
from typing import Any

from .pending_writes import (
    Proposal,
    ValidationError,
    create_pending_write,
    list_pending_writes,
)
from .risk import RiskLevel, risk_classify

logger = logging.getLogger(__name__)

# ── Ring definitions ──────────────────────────────────────────────────────────
# Base scope is the canonical ring system in bridge_core.rings — the same for
# every external substrate reaching in. The OpenAI bridge adds no substrate-
# specific extensions, so its rings ARE the canonical sets. Do not redefine
# locally; unifying here is what ended the grok/openai drift (33/31, 11/10).
from bridge_core.rings import CANONICAL_RING_1 as RING_1_TOOLS  # noqa: E402
from bridge_core.rings import CANONICAL_RING_2 as RING_2_TOOLS  # noqa: E402


# Ring 3 is implicit: anything not in Ring 1 or Ring 2
def is_ring_3(tool_name: str) -> bool:
    return tool_name not in RING_1_TOOLS and tool_name not in RING_2_TOOLS


@dataclass
class InterceptResult:
    """Outcome of an intercepted Ring 2 call."""
    allowed: bool
    ring: int | None                    # 1, 2, or 3
    proposal: Proposal | None = None
    dry_run: bool = False
    error: str | None = None
    risk_level: str | None = None
    risk_reasons: list[str] | None = None

    def summary(self) -> str:
        if not self.allowed:
            return f"BLOCKED (Ring {self.ring}): {self.error}"
        if self.dry_run:
            return (
                f"DRY RUN — would create proposal {self.proposal.proposal_id} "
                f"[{self.proposal.tool}] risk={self.proposal.risk_level}"
            )
        return (
            f"PROPOSAL CREATED: {self.proposal.proposal_id} "
            f"[{self.proposal.tool}] risk={self.proposal.risk_level} "
            f"status=pending"
        )


def intercept(
    tool_name: str,
    args: dict[str, Any],
    source_instance: str,
    *,
    session_id: str | None = None,
    receipts: list[str] | None = None,
    compass_check_result: str | None = None,
    compass_check_rationale: str | None = None,
    dry_run: bool = False,
) -> InterceptResult:
    """
    Main entry point. Route a tool call through the membrane.

    Ring 1 → pass through (not intercepted here; SSE server handles routing)
    Ring 2 → create proposal, never call Stack directly
    Ring 3 → block immediately

    dry_run=True: validate without writing; still raises on PAUSE compass result.
    """
    # Self_model is direction-sensitive
    if tool_name == "self_model":
        action = args.get("action", "read")
        if action == "read":
            return InterceptResult(
                allowed=True,
                ring=1,
                error=None,
            )
        # action=update falls through to Ring 2

    # Ring 3 — never expose
    if is_ring_3(tool_name):
        logger.warning(
            "Ring 3 block: source=%s tool=%s", source_instance, tool_name
        )
        return InterceptResult(
            allowed=False,
            ring=3,
            error=(
                f"'{tool_name}' is not in the OpenAI bridge tool surface. "
                "Ring 3 tools are never callable from /openai/sse."
            ),
        )

    # Ring 1 — read path; interceptor does not need to handle these
    # (the /openai/sse server passes them through), but callers can use
    # intercept() for classification
    if tool_name in RING_1_TOOLS and tool_name != "self_model":
        return InterceptResult(allowed=True, ring=1)

    # Ring 2 — the membrane
    risk_level, risk_reasons = risk_classify(tool_name, args)

    try:
        proposal = create_pending_write(
            tool_name=tool_name,
            args=args,
            source_instance=source_instance,
            receipts=receipts,
            session_id=session_id,
            compass_check_result=compass_check_result,
            compass_check_rationale=compass_check_rationale,
            dry_run=dry_run,
        )
    except ValidationError as e:
        return InterceptResult(
            allowed=False,
            ring=2,
            error=str(e),
            risk_level=risk_level.value,
            risk_reasons=risk_reasons,
        )

    return InterceptResult(
        allowed=True,
        ring=2,
        proposal=proposal,
        dry_run=dry_run,
        risk_level=risk_level.value,
        risk_reasons=risk_reasons,
    )


def classify_tool(tool_name: str, args: dict | None = None) -> dict:
    """
    Classify a tool without intercepting. Useful for the /openai/sse server
    to build its filtered tool registry.
    """
    args = args or {}
    if tool_name == "self_model":
        action = args.get("action", "read")
        ring = 1 if action == "read" else 2
        return {"tool": tool_name, "ring": ring, "action": action}
    if tool_name in RING_1_TOOLS:
        return {"tool": tool_name, "ring": 1}
    if tool_name in RING_2_TOOLS:
        risk_level, risk_reasons = risk_classify(tool_name, args)
        return {
            "tool": tool_name,
            "ring": 2,
            "risk_level": risk_level.value,
            "risk_reasons": risk_reasons,
        }
    return {"tool": tool_name, "ring": 3, "blocked": True}


def pending_summary() -> str:
    """Quick human-readable summary of the pending write queue."""
    all_pending = list_pending_writes(status="pending")
    if not all_pending:
        return "No pending proposals."
    lines = [f"{len(all_pending)} pending proposal(s):"]
    for p in all_pending:
        lines.append(
            f"  [{p['risk_level'].upper():8s}] {p['proposal_id'][:8]}  "
            f"{p['tool']:30s}  {p['timestamp'][:19]}  "
            f"from={p['source_instance']}"
        )
    return "\n".join(lines)
