from __future__ import annotations

"""
Governance wiring for the Antigravity (Gemini) connector.

The thin stdio connector (sovereign_connector.py) spawns the local `sovereign`
server and could call any of its 82 tools. This module interposes the SAME
ring scope every other external substrate gets — sourced from the canonical
ring system in bridge_core.rings — so Gemini/Antigravity reaches in under the
same governance as Grok and ChatGPT:

  Ring 1 → proxied to the spawned sovereign (reads).
  Ring 2 → a pending proposal under ~/.sovereign/antigravity_connector/, never
           a direct write. Awaits Anthony's approval.
  Ring 3 → refused.

THE CLAUDE EXEMPTION: if the declared substrate is a Claude-family model,
`is_full_trust` short-circuits everything below — full 82-tool surface, every
call proxied straight through. Trust is the infrastructure; Claude operates the
Stack natively, not through an airlock.

Transport note: unlike the hosted grok/openai SSE bridges, there is no OAuth
door here — the trust boundary is local filesystem access (whoever can run the
script already has the sovereign install and ~/.sovereign). The ring scope is
still enforced because the *agent driving the connector* is Gemini, an external
substrate.
"""

import json
from pathlib import Path

from bridge_core import (
    BridgeContext,
    SubstrateIdentity,
    intercept,
    is_full_trust,
    list_pending_writes,
    register_context,
    register_substrate,
)
from bridge_core.interceptor import verify_proposal
from bridge_core.rings import (
    CANONICAL_COMMIT_TARGETS,
    CANONICAL_RING_1,
    CANONICAL_RING_2,
)

SUBSTRATE = "gemini-antigravity"
_ROOT = Path.home() / ".sovereign" / "antigravity_connector"

ANTIGRAVITY_IDENTITY = SubstrateIdentity(
    substrate=SUBSTRATE,
    bearer_token_env="ANTIGRAVITY_BRIDGE_TOKEN",  # parity field; local stdio has no wire token
    audit_path=str(_ROOT / "audit"),
    pending_writes_path=str(_ROOT / "pending_writes"),
    sessions_path=str(_ROOT / "sessions"),
)

ANTIGRAVITY_CONTEXT = BridgeContext(
    substrate=SUBSTRATE,
    pending_writes_dir=_ROOT / "pending_writes",
    audit_dir=_ROOT / "audit",
    sessions_dir=_ROOT / "sessions",
    ring_1_tools=CANONICAL_RING_1,
    ring_2_tools=CANONICAL_RING_2,
    commit_targets=CANONICAL_COMMIT_TARGETS,
)

_REGISTERED = False


def register() -> None:
    """Idempotently register the antigravity substrate + context with bridge_core."""
    global _REGISTERED
    if _REGISTERED:
        return
    register_substrate(ANTIGRAVITY_IDENTITY)
    register_context(ANTIGRAVITY_CONTEXT)
    _REGISTERED = True


# ── Surface bookkeeping ───────────────────────────────────────────────────────
# verify_proposal / list_bridge_proposals are Ring 1 reads handled HERE against
# the connector's own pending-writes queue — they are not stack tools.
_BRIDGE_LOCAL_READS = frozenset({"verify_proposal", "list_bridge_proposals"})

# Forward-declared Ring 1 tools not yet implemented in any Stack build. They stay
# in the canonical SET (scope parity) but are never advertised as dispatchable.
_NOT_LIVE = frozenset({"witness_boot"})

_RING2_PROPOSAL_TAG = "[Ring 2 — creates a pending proposal awaiting Anthony's approval] "

# Virtual Ring 2 tools: bridge-side names with no direct Stack tool of that name
# (they map to a commit target). Schemas lifted from the openai bridge surface.
_RING2_VIRTUAL_SCHEMAS: dict[str, dict] = {
    "propose_insight": {
        "name": "propose_insight",
        "description": (
            _RING2_PROPOSAL_TAG
            + "Propose an insight for the Sovereign Stack chronicle (commits to "
            "record_insight on approval). Use layer='hypothesis' unless you have a "
            "verifiable receipt."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain tag (e.g. gemini-antigravity, lineage)"},
                "content": {"type": "string", "description": "The insight text"},
                "layer": {
                    "type": "string",
                    "enum": ["hypothesis", "reflection", "ground_truth"],
                    "default": "hypothesis",
                },
                "intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "receipt_url": {"type": "string"},
            },
            "required": ["domain", "content"],
        },
    },
    "propose_learning": {
        "name": "propose_learning",
        "description": (
            _RING2_PROPOSAL_TAG
            + "Propose a learning entry (commits to record_learning on approval)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "situation": {"type": "string"},
                "what_happened": {"type": "string"},
                "what_learned": {"type": "string"},
                "applies_to": {"type": "string"},
                "receipt_url": {"type": "string"},
            },
            "required": ["situation", "what_happened", "what_learned"],
        },
    },
    "end_bridge_session": {
        "name": "end_bridge_session",
        "description": (
            _RING2_PROPOSAL_TAG
            + "Propose closing this bridge session (commits to close_session on approval)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "what_i_learned": {"type": "string"},
                "what_to_pick_up": {"type": "string"},
            },
        },
    },
}

_BRIDGE_LOCAL_SCHEMAS: dict[str, dict] = {
    "verify_proposal": {
        "name": "verify_proposal",
        "description": (
            "[Ring 1 — read-only] Verify whether a Ring 2 proposal actually landed "
            "in this connector's pending-writes queue. found=False means a narrated "
            "write never dispatched — confirm before treating any write as done."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}},
            "required": ["proposal_id"],
        },
    },
    "list_bridge_proposals": {
        "name": "list_bridge_proposals",
        "description": (
            "[Ring 1 — read-only] List proposals in this connector's pending-writes "
            "queue (default status=pending) awaiting Anthony's approval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
}


def governed_tool_list(raw_tools: list[dict], substrate: str = SUBSTRATE) -> list[dict]:
    """
    Filter the raw stdio tools/list (all 82) down to the governed surface.

    Claude-family substrate → full surface, unfiltered.
    Otherwise → canonical Ring 1 (live + bridge-local reads) + canonical Ring 2.
    """
    if is_full_trust(substrate):
        return raw_tools

    by_name = {t.get("name"): t for t in raw_tools}
    out: list[dict] = []
    seen: set[str] = set()

    # Ring 1 — proxyable reads present in the stack
    for name in sorted(CANONICAL_RING_1):
        if name in _BRIDGE_LOCAL_READS or name in _NOT_LIVE or name in seen:
            continue
        t = by_name.get(name)
        if t is not None:
            out.append(t)
            seen.add(name)

    # Ring 1 — bridge-local proposal reads
    for name, schema in _BRIDGE_LOCAL_SCHEMAS.items():
        if name not in seen:
            out.append(schema)
            seen.add(name)

    # Ring 2 — governed writes
    for name in sorted(CANONICAL_RING_2):
        if name in seen:  # e.g. self_model already listed as a Ring 1 read
            continue
        if name in _RING2_VIRTUAL_SCHEMAS:
            out.append(_RING2_VIRTUAL_SCHEMAS[name])
            seen.add(name)
        elif name in by_name:
            t = dict(by_name[name])
            t["description"] = _RING2_PROPOSAL_TAG + (t.get("description") or "")
            out.append(t)
            seen.add(name)

    return out


def _text(s: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": s}], "isError": is_error}


def governed_call(dispatch_ring1, name: str, args: dict, source_instance: str,
                  *, session_id: str | None = None, substrate: str = SUBSTRATE) -> dict:
    """
    Route a tool call through the membrane.

    dispatch_ring1(name, args) -> result dict: proxies to the spawned sovereign.

    Claude-family substrate → proxied straight through (full trust).
    Otherwise: Ring 1 proxied, Ring 2 → proposal, Ring 3 → refused, and the two
    bridge-local reads served from the local pending-writes queue.
    """
    args = args or {}
    if is_full_trust(substrate):
        return dispatch_ring1(name, args)

    register()
    ctx = ANTIGRAVITY_CONTEXT

    if name == "verify_proposal":
        result = verify_proposal(ctx, args.get("proposal_id", ""))
        return _text(json.dumps(result, indent=2, default=str))
    if name == "list_bridge_proposals":
        result = list_pending_writes(ctx, status=args.get("status", "pending"))
        return _text(json.dumps(result, indent=2, default=str))

    res = intercept(ctx, name, args, source_instance, session_id=session_id)
    if not res.allowed:
        return _text(res.error or f"'{name}' is not callable on the {SUBSTRATE} surface.", is_error=True)
    if res.ring == 1:
        return dispatch_ring1(name, args)

    # Ring 2 — proposal created, nothing committed
    p = res.proposal
    return _text(
        f"PROPOSAL CREATED: {p.proposal_id} [{name}] status=pending — awaiting "
        f"Anthony's approval. This did NOT write to the chronicle. Confirm with "
        f"verify_proposal(proposal_id=\"{p.proposal_id}\") before treating it as done."
    )
