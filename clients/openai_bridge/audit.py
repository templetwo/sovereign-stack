from __future__ import annotations

"""
Append-only audit log for the OpenAI bridge.

Every proposal lifecycle event is recorded here — creation, validation,
approval, rejection, commit. The log is hash-chained: each entry
hashes the previous, making tampering detectable.
"""

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .hash_chain import AUDIT_DIR, AUDIT_LOG, get_last_audit_hash, hash_pending_write

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
    event_type: AuditEvent,
    proposal_id: str,
    actor: str,
    details: dict[str, Any] | None = None,
) -> dict:
    """
    Append one event to the audit log.

    Returns the full audit entry (useful for testing).
    The entry is hash-chained to the previous entry.
    """
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    prev_hash = get_last_audit_hash()

    entry: dict[str, Any] = {
        "event_type": event_type.value,
        "proposal_id": proposal_id,
        "actor": actor,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prev_hash": prev_hash,
        "details": details or {},
        "audit_hash": "",  # computed next
    }

    entry["audit_hash"] = hash_pending_write(entry, prev_hash)

    try:
        with AUDIT_LOG.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError as e:
        logger.error("Failed to write audit event: %s", e)
        raise

    logger.debug("Audit: %s proposal=%s actor=%s", event_type.value, proposal_id, actor)
    return entry


def read_audit_trail(proposal_id: str | None = None) -> list[dict]:
    """
    Read audit entries, optionally filtered to a single proposal_id.
    Returns entries in chronological order.
    """
    if not AUDIT_LOG.exists():
        return []
    entries = []
    try:
        with AUDIT_LOG.open() as f:
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
