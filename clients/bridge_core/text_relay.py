from __future__ import annotations

"""
Text-relay path for Ring 2 proposals.

Some substrates (notably Grok via xAI's Custom Connector) emit well-formed
Ring 2 JSON payloads inside their chat text without ever dispatching an
HTTP tool call. This module accepts that chat text via the bridge CLI's
`submit-text` subcommand, locates a structured fence, validates it, and
creates the same proposal the SSE Ring 2 path would have produced.

Membrane invariants are preserved: nothing here writes to the Stack.
The end product is either a proposal file under `ctx.pending_writes_dir`
or a triage record under one of three sibling queues:

    pending_writes/            valid proposal awaiting Anthony's approval
    relay_malformed/           fence found, content failed to parse
    relay_unsupported/         valid JSON, tool not in ctx.ring_2_tools
    relay_validation_failed/   create_pending_write raised ValidationError

Fences that appear inside a markdown ``` block are skipped silently — they
are almost always Grok quoting the spec back at the user, not a genuine
proposal. No queue file is written and no error surfaces, so a casual
paste containing a copy of the spec does not pollute the malformed queue.

The fence shape is locked:
    <RING2_PROPOSAL_V1>{...JSON...}</RING2_PROPOSAL_V1>
Case-sensitive, first-fence-wins, no whitespace variations on the tags.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context import BridgeContext
from .dispatch import pop_bridge_metadata
from .pending_writes import ValidationError, create_pending_write

logger = logging.getLogger(__name__)

FENCE_OPEN = "<RING2_PROPOSAL_V1>"
FENCE_CLOSE = "</RING2_PROPOSAL_V1>"


@dataclass
class RelayResult:
    """Outcome of one text-relay submission. Never raises on relay failures."""

    outcome: str
    # One of: proposal_created | ignored | no_fence | malformed | unsupported | validation_failed
    queue: str | None
    proposal_id: str | None
    tool: str | None
    error: str | None
    queue_file: Path | None


def _inside_markdown_block(text: str, fence_pos: int) -> bool:
    """True if `fence_pos` lies inside an unclosed ``` markdown block."""
    return text[:fence_pos].count("```") % 2 == 1


def _extract_fence(raw: str) -> tuple[str | None, str | None]:
    """
    Locate the first <RING2_PROPOSAL_V1>...</RING2_PROPOSAL_V1> fence.

    Returns (inner_text, reason). When inner_text is None, reason is one of:
        "no_fence"          — no open tag in the text
        "ignored_markdown"  — open tag found but inside a ``` block
        "unclosed"          — open tag found but no matching close tag
    """
    open_pos = raw.find(FENCE_OPEN)
    if open_pos < 0:
        return None, "no_fence"
    if _inside_markdown_block(raw, open_pos):
        return None, "ignored_markdown"
    inner_start = open_pos + len(FENCE_OPEN)
    close_pos = raw.find(FENCE_CLOSE, inner_start)
    if close_pos < 0:
        return None, "unclosed"
    return raw[inner_start:close_pos].strip(), None


def _write_triage_file(queue_dir: Path, payload: dict[str, Any], suffix: str) -> Path:
    """Drop a JSON triage file under a non-pending_writes queue."""
    queue_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    filename = (
        now.replace(":", "-").replace("+", "Z")[:19]
        + f"_{suffix}_{uuid.uuid4().hex[:8]}.json"
    )
    path = queue_dir / filename
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def relay_text(
    ctx: BridgeContext,
    raw_text: str,
    relayed_by: str,
) -> RelayResult:
    """
    Parse `raw_text` for a Ring 2 proposal fence and route to the right queue.

    Never raises on relay-class failures — always returns a RelayResult whose
    `outcome` names what happened.
    """
    queue_root = ctx.pending_writes_dir.parent
    detected_at = datetime.now(timezone.utc).isoformat()
    source_message_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

    inner, reason = _extract_fence(raw_text)

    if inner is None and reason == "ignored_markdown":
        logger.info("Relay[%s]: fence inside markdown block — ignored", ctx.substrate)
        return RelayResult(
            outcome="ignored",
            queue=None,
            proposal_id=None,
            tool=None,
            error="fence inside ``` markdown block — silent skip",
            queue_file=None,
        )

    if inner is None and reason == "no_fence":
        return RelayResult(
            outcome="no_fence",
            queue=None,
            proposal_id=None,
            tool=None,
            error="no <RING2_PROPOSAL_V1> fence found in input",
            queue_file=None,
        )

    if inner is None and reason == "unclosed":
        path = _write_triage_file(
            queue_root / "relay_malformed",
            {
                "detected_at": detected_at,
                "relayed_by": relayed_by,
                "substrate": ctx.substrate,
                "source_message_hash": source_message_hash,
                "error": "open <RING2_PROPOSAL_V1> tag with no matching </RING2_PROPOSAL_V1>",
            },
            suffix="malformed",
        )
        logger.warning("Relay[%s]: unclosed fence → %s", ctx.substrate, path.name)
        return RelayResult(
            outcome="malformed",
            queue="relay_malformed",
            proposal_id=None,
            tool=None,
            error="fence is open but never closed",
            queue_file=path,
        )

    # Fence found and closed — try to parse the inner JSON.
    try:
        payload = json.loads(inner)
    except json.JSONDecodeError as e:
        path = _write_triage_file(
            queue_root / "relay_malformed",
            {
                "detected_at": detected_at,
                "relayed_by": relayed_by,
                "substrate": ctx.substrate,
                "source_message_hash": source_message_hash,
                "fence_inner_text": inner,
                "json_error": str(e),
            },
            suffix="malformed",
        )
        logger.warning("Relay[%s]: malformed JSON in fence → %s", ctx.substrate, path.name)
        return RelayResult(
            outcome="malformed",
            queue="relay_malformed",
            proposal_id=None,
            tool=None,
            error=f"JSON parse error: {e}",
            queue_file=path,
        )

    if not isinstance(payload, dict):
        path = _write_triage_file(
            queue_root / "relay_malformed",
            {
                "detected_at": detected_at,
                "relayed_by": relayed_by,
                "substrate": ctx.substrate,
                "source_message_hash": source_message_hash,
                "raw_payload": payload,
                "json_error": "top-level payload must be a JSON object",
            },
            suffix="malformed",
        )
        logger.warning("Relay[%s]: non-object payload → %s", ctx.substrate, path.name)
        return RelayResult(
            outcome="malformed",
            queue="relay_malformed",
            proposal_id=None,
            tool=None,
            error="top-level payload must be a JSON object",
            queue_file=path,
        )

    tool = payload.get("tool")
    args = payload.get("arguments") or {}
    if not isinstance(args, dict):
        path = _write_triage_file(
            queue_root / "relay_malformed",
            {
                "detected_at": detected_at,
                "relayed_by": relayed_by,
                "substrate": ctx.substrate,
                "source_message_hash": source_message_hash,
                "tool": tool,
                "raw_arguments": args,
                "json_error": "'arguments' must be a JSON object",
            },
            suffix="malformed",
        )
        logger.warning("Relay[%s]: non-dict arguments → %s", ctx.substrate, path.name)
        return RelayResult(
            outcome="malformed",
            queue="relay_malformed",
            proposal_id=None,
            tool=tool,
            error="'arguments' must be a JSON object",
            queue_file=path,
        )

    # Tool gate — accept only Ring 2 tools known to this substrate.
    if not tool or tool not in ctx.ring_2_tools:
        path = _write_triage_file(
            queue_root / "relay_unsupported",
            {
                "detected_at": detected_at,
                "relayed_by": relayed_by,
                "substrate": ctx.substrate,
                "source_message_hash": source_message_hash,
                "tool": tool,
                "arguments": args,
                "reason": (
                    f"tool '{tool}' is not a Ring 2 tool on {ctx.substrate}"
                ),
            },
            suffix="unsupported",
        )
        logger.warning(
            "Relay[%s]: unsupported tool '%s' → %s",
            ctx.substrate, tool, path.name,
        )
        return RelayResult(
            outcome="unsupported",
            queue="relay_unsupported",
            proposal_id=None,
            tool=tool,
            error=f"tool '{tool}' not in Ring 2 set",
            queue_file=path,
        )

    # Capture what Grok asserted BEFORE pop_bridge_metadata applies fallbacks.
    grok_asserted_session_id = args.get("session_id")
    meta = pop_bridge_metadata(args, substrate=ctx.substrate)
    payload_hash = hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    relay_attribution: dict[str, Any] = {
        "proposed_by": meta["source_instance"],
        "relayed_by": relayed_by,
        "approved_by": None,
        "committed_by": None,
        "payload_hash": payload_hash,
        "source_message_hash": source_message_hash,
        "detected_at": detected_at,
        "xai_worker_ip": None,
        "grok_asserted_session_id": grok_asserted_session_id,
    }

    try:
        proposal = create_pending_write(
            ctx,
            tool_name=tool,
            args=args,
            source_instance=meta["source_instance"],
            session_id=meta["session_id"],
            compass_check_result=meta["compass_check_result"],
            compass_check_rationale=meta["compass_check_rationale"],
            relay_attribution=relay_attribution,
        )
    except ValidationError as e:
        path = _write_triage_file(
            queue_root / "relay_validation_failed",
            {
                "detected_at": detected_at,
                "relayed_by": relayed_by,
                "substrate": ctx.substrate,
                "source_message_hash": source_message_hash,
                "payload_hash": payload_hash,
                "tool": tool,
                "arguments": args,
                "relay_attribution": relay_attribution,
                "validation_error": str(e),
            },
            suffix="validation_failed",
        )
        logger.warning(
            "Relay[%s]: validation_failed for '%s' → %s",
            ctx.substrate, tool, path.name,
        )
        return RelayResult(
            outcome="validation_failed",
            queue="relay_validation_failed",
            proposal_id=None,
            tool=tool,
            error=str(e),
            queue_file=path,
        )

    logger.info(
        "Relay[%s]: proposal_created %s [%s]",
        ctx.substrate, proposal.proposal_id, tool,
    )
    return RelayResult(
        outcome="proposal_created",
        queue="pending_writes",
        proposal_id=proposal.proposal_id,
        tool=tool,
        error=None,
        queue_file=None,
    )
