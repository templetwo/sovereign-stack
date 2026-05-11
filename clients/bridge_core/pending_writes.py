from __future__ import annotations

"""
Substrate-agnostic pending write queue.

Ring 2 tool calls from any substrate (Grok, future) never touch the Stack
directly. They create a proposal under the substrate's pending_writes_dir.
Anthony reviews and approves. Approval and commit are separate steps.

Proposal is not memory.
Approval is not commitment.
External substrate write intent is not Stack truth.
"""

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .audit import AuditEvent, append_audit_event
from .context import BridgeContext
from .hash_chain import get_last_audit_hash, hash_pending_write, verify_chain
from .risk import RiskLevel, risk_classify

logger = logging.getLogger(__name__)


@dataclass
class Proposal:
    proposal_id: str
    timestamp: str
    source_instance: str
    session_id: str
    substrate: str

    tool: str
    arguments: dict[str, Any]
    commit_target: str

    proposed_layer: str
    has_receipt: bool
    receipt_urls: list[str]

    risk_level: str
    risk_reasons: list[str]

    compass_check_result: str | None
    compass_check_rationale: str | None

    status: str = "pending"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    revision_notes: str | None = None
    commit_result: dict | None = None

    # Set only on proposals created via the text-relay path (bridge submit-text).
    # SSE-path proposals leave this None. Field is included in audit_hash, so it
    # is tamper-evident — but it is never mutated after creation.
    relay_attribution: dict | None = None

    prev_hash: str | None = None
    audit_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ValidationError(Exception):
    pass


def validate_pending_write(ctx: BridgeContext, proposal: Proposal) -> list[str]:
    """Validate a proposal against policy. Returns errors (empty = valid)."""
    errors: list[str] = []

    if not proposal.source_instance:
        errors.append("source_instance is required")

    if proposal.tool not in ctx.commit_targets:
        errors.append(f"tool '{proposal.tool}' is not in Ring 2 — cannot propose")

    valid_layers = {"hypothesis", "reflection", "ground_truth"}
    if proposal.proposed_layer not in valid_layers:
        errors.append(f"proposed_layer must be one of {valid_layers}")

    if proposal.proposed_layer == "ground_truth" and not proposal.has_receipt:
        errors.append(
            "ground_truth layer requires at least one receipt_url — "
            "policy: max_confidence_without_receipt = 0.70"
        )

    if proposal.risk_level == RiskLevel.CRITICAL and not proposal.compass_check_result:
        errors.append(
            "CRITICAL risk proposals require a compass_check_result — "
            "call compass_check before proposing"
        )

    if proposal.compass_check_result == "PAUSE":
        errors.append(
            "compass_check returned PAUSE — do not propose until the concern is addressed"
        )

    return errors


def create_pending_write(
    ctx: BridgeContext,
    tool_name: str,
    args: dict,
    source_instance: str,
    receipts: list[str] | None = None,
    session_id: str | None = None,
    compass_check_result: str | None = None,
    compass_check_rationale: str | None = None,
    relay_attribution: dict | None = None,
    dry_run: bool = False,
) -> Proposal:
    """Create a Ring 2 write proposal under the substrate's pending_writes_dir."""
    ctx.pending_writes_dir.mkdir(parents=True, exist_ok=True)

    receipts = receipts or []
    proposed_layer = args.get("layer", "hypothesis")
    has_receipt = bool(receipts or args.get("receipt_url"))

    risk_level, risk_reasons = risk_classify(tool_name, args)

    prev_hash = get_last_audit_hash(ctx)
    proposal_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    proposal = Proposal(
        proposal_id=proposal_id,
        timestamp=now,
        source_instance=source_instance,
        session_id=session_id or "unknown",
        substrate=ctx.substrate,
        tool=tool_name,
        arguments=args,
        commit_target=ctx.commit_targets.get(tool_name, tool_name),
        proposed_layer=proposed_layer,
        has_receipt=has_receipt,
        receipt_urls=receipts,
        risk_level=risk_level.value,
        risk_reasons=risk_reasons,
        compass_check_result=compass_check_result,
        compass_check_rationale=compass_check_rationale,
        status="pending",
        relay_attribution=relay_attribution,
        prev_hash=prev_hash,
        audit_hash="",
    )

    errors = validate_pending_write(ctx, proposal)
    if errors:
        append_audit_event(
            ctx,
            AuditEvent.VALIDATION_FAILED,
            proposal_id=proposal_id,
            actor=source_instance,
            details={"errors": errors, "tool": tool_name},
        )
        raise ValidationError(
            f"Proposal validation failed for '{tool_name}':\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    d = proposal.to_dict()
    proposal.audit_hash = hash_pending_write(d, prev_hash)
    d["audit_hash"] = proposal.audit_hash

    if not dry_run:
        filename = (
            now.replace(":", "-").replace("+", "Z")[:19]
            + f"_{tool_name}_{proposal_id[:8]}.json"
        )
        proposal_path = ctx.pending_writes_dir / filename
        proposal_path.write_text(json.dumps(d, indent=2, default=str))

        append_audit_event(
            ctx,
            AuditEvent.PROPOSAL_CREATED,
            proposal_id=proposal_id,
            actor=source_instance,
            details={
                "tool": tool_name,
                "risk_level": risk_level.value,
                "proposed_layer": proposed_layer,
                "file": str(proposal_path),
            },
        )
        append_audit_event(
            ctx,
            AuditEvent.VALIDATION_PASSED,
            proposal_id=proposal_id,
            actor="bridge",
            details={"tool": tool_name},
        )

        logger.info(
            "Proposal[%s] created: %s | tool=%s risk=%s layer=%s",
            ctx.substrate, proposal_id, tool_name, risk_level.value, proposed_layer,
        )

    return proposal


def _load_proposal(ctx: BridgeContext, proposal_id: str) -> tuple[Proposal, Path]:
    """Find and load a proposal file by id prefix or full id."""
    ctx.pending_writes_dir.mkdir(parents=True, exist_ok=True)
    matches = list(ctx.pending_writes_dir.glob(f"*{proposal_id[:8]}*.json"))
    if not matches:
        raise FileNotFoundError(f"No proposal found for id: {proposal_id}")
    if len(matches) > 1:
        exact = [p for p in matches if proposal_id in p.stem]
        if len(exact) == 1:
            matches = exact
        else:
            raise FileNotFoundError(
                f"Ambiguous proposal id '{proposal_id}' — {len(matches)} matches"
            )
    path = matches[0]
    return Proposal.from_dict(json.loads(path.read_text())), path


def _save_proposal(proposal: Proposal, path: Path) -> None:
    path.write_text(json.dumps(proposal.to_dict(), indent=2, default=str))


def approve_pending_write(
    ctx: BridgeContext, proposal_id: str, approved_by: str = "Anthony",
) -> Proposal:
    """Mark a pending proposal as approved. Approval is not commitment."""
    proposal, path = _load_proposal(ctx, proposal_id)
    if proposal.status != "pending":
        raise ValueError(
            f"Cannot approve proposal in status '{proposal.status}' — must be 'pending'"
        )
    proposal.status = "approved"
    proposal.reviewed_by = approved_by
    proposal.reviewed_at = datetime.now(timezone.utc).isoformat()
    _save_proposal(proposal, path)
    append_audit_event(
        ctx,
        AuditEvent.APPROVED,
        proposal_id=proposal.proposal_id,
        actor=approved_by,
        details={"tool": proposal.tool, "risk_level": proposal.risk_level},
    )
    logger.info("Proposal[%s] approved: %s by %s", ctx.substrate, proposal.proposal_id, approved_by)
    return proposal


def _precondition_check(ctx: BridgeContext, proposal: Proposal) -> list[str]:
    """Run all pre-commit guards. Returns blocking reasons (empty = safe)."""
    errors: list[str] = []
    allowed_targets = frozenset(ctx.commit_targets.values())

    if proposal.status != "approved":
        errors.append(f"status is '{proposal.status}', not 'approved'")
    if proposal.status == "committed":
        errors.append("proposal has already been committed")
    if proposal.commit_target not in allowed_targets:
        errors.append(f"commit_target '{proposal.commit_target}' is not a Ring 2 allowed target")
    if proposal.risk_level == RiskLevel.CRITICAL and proposal.compass_check_result != "PROCEED":
        errors.append(
            "CRITICAL risk proposals require compass_check_result='PROCEED' before commit"
        )
    if proposal.proposed_layer == "ground_truth" and not proposal.has_receipt:
        errors.append("ground_truth layer requires a receipt — cannot commit without one")

    # Verify creation-time fields haven't been tampered with
    _MUTABLE = {"status", "reviewed_by", "reviewed_at", "revision_notes",
                "commit_result", "audit_hash"}
    d = proposal.to_dict()
    creation_snapshot = {k: v for k, v in d.items() if k not in _MUTABLE}
    creation_snapshot["status"] = "pending"
    creation_snapshot["reviewed_by"] = None
    creation_snapshot["reviewed_at"] = None
    creation_snapshot["revision_notes"] = None
    creation_snapshot["commit_result"] = None
    recomputed = hash_pending_write(creation_snapshot, proposal.prev_hash)
    if recomputed != proposal.audit_hash:
        errors.append("proposal audit_hash mismatch — core fields may have been tampered with")

    return errors


def commit_pending_write(
    ctx: BridgeContext, proposal_id: str, live: bool = False,
) -> Proposal:
    """
    Execute an approved proposal against the Stack.

    live=False: dry-run, returns proposal with would_call info, no Stack write.
    live=True: runs precondition checks, calls Stack via bridge REST, writes result.

    External substrates cannot trigger live=True — only Anthony's terminal can.
    """
    proposal, path = _load_proposal(ctx, proposal_id)

    errors = _precondition_check(ctx, proposal)
    if errors:
        raise ValueError("Pre-commit checks failed:\n" + "\n".join(f"  • {e}" for e in errors))

    if not live:
        proposal.commit_result = {
            "mocked": False,
            "live": False,
            "would_call": proposal.commit_target,
            "with_arguments": proposal.arguments,
        }
        return proposal

    # Live commit
    token = os.environ.get(ctx.bridge_rest_token_env, "")
    if not token:
        raise RuntimeError(
            f"{ctx.bridge_rest_token_env} not set — cannot commit without auth"
        )

    # Bridge → Stack layer translation
    commit_args = dict(proposal.arguments)
    if "layer" in commit_args:
        commit_args["layer"] = ctx.layer_translation.get(
            commit_args["layer"], commit_args["layer"]
        )

    try:
        response = httpx.post(
            f"{ctx.bridge_rest_url}/api/call",
            json={"tool": proposal.commit_target, "arguments": commit_args},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30.0,
        )
        response.raise_for_status()
        stack_result = response.json()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Stack returned {e.response.status_code}: {e.response.text}") from e
    except httpx.RequestError as e:
        raise RuntimeError(f"Could not reach bridge at {ctx.bridge_rest_url}: {e}") from e

    proposal.status = "committed"
    proposal.commit_result = {
        "live": True,
        "commit_target": proposal.commit_target,
        "stack_response": stack_result,
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_proposal(proposal, path)
    append_audit_event(
        ctx,
        AuditEvent.COMMITTED,
        proposal_id=proposal.proposal_id,
        actor="bridge",
        details={
            "tool": proposal.tool,
            "commit_target": proposal.commit_target,
            "live": True,
        },
    )

    ok, msg = verify_chain(ctx)
    if not ok:
        logger.error("CHAIN BROKEN after commit: %s", msg)
        append_audit_event(
            ctx, AuditEvent.CHAIN_BROKEN,
            proposal_id=proposal.proposal_id, actor="bridge",
            details={"message": msg},
        )

    logger.info(
        "Proposal[%s] committed (LIVE): %s → %s",
        ctx.substrate, proposal.proposal_id, proposal.commit_target,
    )
    return proposal


def reject_pending_write(
    ctx: BridgeContext, proposal_id: str, reason: str, rejected_by: str = "Anthony",
) -> Proposal:
    """Mark a pending or needs_revision proposal as rejected."""
    proposal, path = _load_proposal(ctx, proposal_id)
    if proposal.status not in ("pending", "needs_revision"):
        raise ValueError(f"Cannot reject proposal in status '{proposal.status}'")
    proposal.status = "rejected"
    proposal.reviewed_by = rejected_by
    proposal.reviewed_at = datetime.now(timezone.utc).isoformat()
    proposal.revision_notes = reason
    _save_proposal(proposal, path)
    append_audit_event(
        ctx, AuditEvent.REJECTED,
        proposal_id=proposal.proposal_id, actor=rejected_by,
        details={"reason": reason, "tool": proposal.tool},
    )
    logger.info("Proposal[%s] rejected: %s reason=%s", ctx.substrate, proposal.proposal_id, reason)
    return proposal


def needs_revision_pending_write(
    ctx: BridgeContext, proposal_id: str, notes: str, actor: str = "Anthony",
) -> Proposal:
    """Send a proposal back for revision with notes."""
    proposal, path = _load_proposal(ctx, proposal_id)
    if proposal.status != "pending":
        raise ValueError(f"Cannot mark needs_revision for proposal in status '{proposal.status}'")
    proposal.status = "needs_revision"
    proposal.revision_notes = notes
    _save_proposal(proposal, path)
    append_audit_event(
        ctx, AuditEvent.NEEDS_REVISION,
        proposal_id=proposal.proposal_id, actor=actor,
        details={"notes": notes, "tool": proposal.tool},
    )
    logger.info("Proposal[%s] needs revision: %s", ctx.substrate, proposal.proposal_id)
    return proposal


def list_pending_writes(ctx: BridgeContext, status: str | None = None) -> list[dict]:
    """List proposals, optionally filtered by status. Returns summary dicts."""
    ctx.pending_writes_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(ctx.pending_writes_dir.glob("*.json")):
        try:
            d = json.loads(path.read_text())
            if status is None or d.get("status") == status:
                results.append({
                    "proposal_id": d.get("proposal_id"),
                    "timestamp": d.get("timestamp"),
                    "tool": d.get("tool"),
                    "source_instance": d.get("source_instance"),
                    "substrate": d.get("substrate", ctx.substrate),
                    "status": d.get("status"),
                    "risk_level": d.get("risk_level"),
                    "proposed_layer": d.get("proposed_layer"),
                    "file": path.name,
                })
        except (json.JSONDecodeError, OSError):
            logger.warning("Skipping unreadable proposal file: %s", path.name)
    return results
