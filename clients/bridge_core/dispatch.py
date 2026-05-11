from __future__ import annotations

"""
Shared bridge-layer metadata extraction.

Ring 2 callers — the SSE handler in a substrate's mcp_filtered, the
text relay path, and any future transport — all need to pop the same
set of bridge-layer keys from a tool arguments dict before the args
are stored inside a Proposal. This helper consolidates that pop logic
so every entry point stays structurally consistent and the same
fallbacks (uuid4 session_id, substrate-name source_instance) apply
in one place.

The function mutates `args` in-place (it pops the metadata keys out),
which is what callers want — what remains in `args` is the Stack-bound
payload that will be stored on the Proposal.
"""

import uuid
from typing import Any


def pop_bridge_metadata(args: dict[str, Any], substrate: str) -> dict[str, Any]:
    """
    Pop bridge-layer metadata keys from `args` (in-place) and return them.

    Fallbacks:
      session_id      → fresh uuid4 hex if absent or empty
      source_instance → `substrate` if absent or empty
    """
    return {
        "compass_check_result": args.pop("compass_check_result", None),
        "compass_check_rationale": args.pop("compass_check_rationale", None),
        "session_id": args.pop("session_id", None) or str(uuid.uuid4()),
        "source_instance": args.pop("source_instance", None) or substrate,
    }
