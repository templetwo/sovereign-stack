from __future__ import annotations

"""
Substrate-agnostic Ring 2 interceptor.

When any external substrate calls a Ring 2 tool via its bridge endpoint,
the call never reaches the Stack directly. It comes here first.

filter → frame → govern → audit → propose

The only possible outcome of a Ring 2 call is:
  - A proposal file in <ctx.pending_writes_dir>           (live)
  - A validated Proposal object returned to the caller    (dry_run)
  - A ValidationError raised with the reasons             (invalid)

Nothing commits to the chronicle. Nothing mutates Stack state.
"""

import logging
from dataclasses import dataclass
from typing import Any

from .context import BridgeContext
from .pending_writes import (
    Proposal,
    ValidationError,
    create_pending_write,
    get_proposal_by_id,
    list_pending_writes,
)
from .risk import risk_classify

logger = logging.getLogger(__name__)


@dataclass
class InterceptResult:
    """Outcome of an intercepted Ring 2 call."""
    allowed: bool
    ring: int | None
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
    ctx: BridgeContext,
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
    Route a tool call through the substrate's membrane.

    Ring 1 → pass through (the SSE server handles routing; interceptor
              returns allowed=True for classification only)
    Ring 2 → create proposal, never call Stack directly
    Ring 3 → block immediately
    """
    # Self_model is direction-sensitive across all substrates
    if tool_name == "self_model":
        action = args.get("action", "read")
        if action == "read":
            return InterceptResult(allowed=True, ring=1, error=None)
        # action=update falls through to Ring 2

    # Ring 3 — never expose
    if ctx.is_ring_3(tool_name):
        logger.warning(
            "Ring 3 block[%s]: source=%s tool=%s",
            ctx.substrate, source_instance, tool_name,
        )
        return InterceptResult(
            allowed=False,
            ring=3,
            error=(
                f"'{tool_name}' is not in the {ctx.substrate} bridge tool surface. "
                f"Ring 3 tools are never callable."
            ),
        )

    # Ring 1 — interceptor classifies but doesn't dispatch
    if tool_name in ctx.ring_1_tools and tool_name != "self_model":
        return InterceptResult(allowed=True, ring=1)

    # Ring 2 — the membrane
    risk_level, risk_reasons = risk_classify(tool_name, args)

    try:
        proposal = create_pending_write(
            ctx,
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


def classify_tool(ctx: BridgeContext, tool_name: str, args: dict | None = None) -> dict:
    """Classify a tool without intercepting (for diagnostics + tool-list builders)."""
    args = args or {}
    if tool_name == "self_model":
        action = args.get("action", "read")
        ring = 1 if action == "read" else 2
        return {"tool": tool_name, "ring": ring, "action": action}
    if tool_name in ctx.ring_1_tools:
        return {"tool": tool_name, "ring": 1}
    if tool_name in ctx.ring_2_tools:
        risk_level, risk_reasons = risk_classify(tool_name, args)
        return {
            "tool": tool_name,
            "ring": 2,
            "risk_level": risk_level.value,
            "risk_reasons": risk_reasons,
        }
    return {"tool": tool_name, "ring": 3, "blocked": True}


def verify_proposal(ctx: BridgeContext, proposal_id: str) -> dict:
    """
    Verify whether a claimed proposal actually exists and its hash is intact.

    Delegates to pending_writes.get_proposal_by_id. Returns the verification
    dict directly — found=False for a missing proposal (the canonical signal
    that a narrated-but-not-dispatched write never landed in the queue).
    """
    return get_proposal_by_id(ctx, proposal_id)


def pending_summary(ctx: BridgeContext) -> str:
    """Quick human-readable summary of the substrate's pending queue."""
    all_pending = list_pending_writes(ctx, status="pending")
    if not all_pending:
        return f"No pending proposals on {ctx.substrate} bridge."
    lines = [f"{len(all_pending)} pending proposal(s) on {ctx.substrate}:"]
    for p in all_pending:
        lines.append(
            f"  [{p['risk_level'].upper():8s}] {p['proposal_id'][:8]}  "
            f"{p['tool']:30s}  {p['timestamp'][:19]}  "
            f"from={p['source_instance']}"
        )
    return "\n".join(lines)
