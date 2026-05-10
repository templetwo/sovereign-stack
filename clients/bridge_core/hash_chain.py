from __future__ import annotations

"""
Substrate-agnostic hash chain for bridge audit trails.

Each entry hashes the previous entry. A broken chain is evidence of
tampering or corruption. Chain operations take a BridgeContext so each
substrate maintains its own independent chain.
"""

import hashlib
import json
import logging

from .context import BridgeContext

logger = logging.getLogger(__name__)


def _normalize(obj: dict) -> str:
    """Stable JSON for hashing — sorted keys, no trailing whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def hash_object(obj: dict, exclude_keys: tuple[str, ...] = ("audit_hash",)) -> str:
    """SHA-256 of obj with specified keys removed before hashing."""
    clean = {k: v for k, v in obj.items() if k not in exclude_keys}
    return hashlib.sha256(_normalize(clean).encode()).hexdigest()


def hash_pending_write(proposal: dict, previous_hash: str | None) -> str:
    """
    Compute the audit_hash for a proposal.

    The hash covers all proposal fields except audit_hash itself,
    plus the previous_hash, binding this entry to the chain.
    """
    hashable = {k: v for k, v in proposal.items() if k != "audit_hash"}
    hashable["prev_hash"] = previous_hash
    return hashlib.sha256(_normalize(hashable).encode()).hexdigest()


def get_last_audit_hash(ctx: BridgeContext) -> str | None:
    """Return the audit_hash of the most recent audit log entry, or None."""
    log_path = ctx.audit_log_path
    if not log_path.exists():
        return None
    last_line = None
    try:
        with log_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line
    except OSError:
        logger.warning("Could not read audit log for last hash: %s", log_path)
        return None
    if last_line is None:
        return None
    try:
        return json.loads(last_line).get("audit_hash")
    except json.JSONDecodeError:
        logger.warning("Malformed last audit entry — chain may be broken")
        return None


def verify_chain(ctx: BridgeContext) -> tuple[bool, str]:
    """Walk the audit log and verify every entry's hash matches its content."""
    log_path = ctx.audit_log_path
    if not log_path.exists():
        return True, "Audit log empty — chain intact"
    ctx.audit_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    try:
        with log_path.open() as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append((i + 1, json.loads(line)))
                except json.JSONDecodeError:
                    return False, f"Malformed JSON at line {i + 1}"
    except OSError as e:
        return False, f"Could not read audit log: {e}"

    for lineno, entry in entries:
        stored_hash = entry.get("audit_hash")
        computed = hash_pending_write(
            {k: v for k, v in entry.items() if k != "prev_hash"},
            entry.get("prev_hash"),
        )
        if stored_hash != computed:
            return False, f"Hash mismatch at line {lineno} — chain broken"

    return True, f"Chain intact ({len(entries)} entries)"
