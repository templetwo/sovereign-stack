from __future__ import annotations

"""
Substrate-agnostic audit log. Each substrate has its own hash-chained
audit log under its audit_dir.
"""

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .context import BridgeContext
from .hash_chain import get_last_audit_hash, hash_pending_write

logger = logging.getLogger(__name__)


class AuditEvent(str, Enum):
    PROPOSAL_CREATED = "proposal_created"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"
    APPROVED = "approved"
    COMMITTED = "committed"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"
    CHAIN_VERIFIED = "chain_verified"
    CHAIN_BROKEN = "chain_broken"


def append_audit_event(
    ctx: BridgeContext,
    event_type: AuditEvent,
    proposal_id: str,
    actor: str,
    details: dict[str, Any] | None = None,
) -> dict:
    """Append one event to the substrate's audit log, hash-chained to prior."""
    ctx.audit_dir.mkdir(parents=True, exist_ok=True)

    prev_hash = get_last_audit_hash(ctx)

    entry: dict[str, Any] = {
        "event_type": event_type.value,
        "proposal_id": proposal_id,
        "actor": actor,
        "substrate": ctx.substrate,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prev_hash": prev_hash,
        "details": details or {},
        "audit_hash": "",
    }
    entry["audit_hash"] = hash_pending_write(entry, prev_hash)

    try:
        with ctx.audit_log_path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError as e:
        logger.error("Failed to write audit event: %s", e)
        raise

    logger.debug(
        "Audit[%s]: %s proposal=%s actor=%s",
        ctx.substrate, event_type.value, proposal_id, actor,
    )
    return entry


def read_audit_trail(ctx: BridgeContext, proposal_id: str | None = None) -> list[dict]:
    """Read audit entries, optionally filtered to one proposal_id."""
    log_path = ctx.audit_log_path
    if not log_path.exists():
        return []
    entries = []
    try:
        with log_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if proposal_id is None or entry.get("proposal_id") == proposal_id:
                        entries.append(entry)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed audit entry")
    except OSError as e:
        logger.error("Could not read audit log: %s", e)
    return entries
