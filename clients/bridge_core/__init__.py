"""
Sovereign Stack — bridge_core

Substrate-agnostic infrastructure shared across all governed bridges
(OpenAI, Grok, future). Per-substrate bridges (clients/openai_bridge,
clients/grok_bridge) layer their own identity, ring definitions, and
boot ceremony on top of this core.

The pipeline contract is transport-independent:
    identity_gate → ring_filter → execute_or_intercept

SSE and REST transports both plug into the same pipeline. Future direct
tool-calling capabilities register additional transports without refactor.
"""

from .audit import AuditEvent, append_audit_event, read_audit_trail
from .context import BridgeContext, get_context, known_contexts, register_context
from .dispatch import pop_bridge_metadata
from .hash_chain import (
    get_last_audit_hash,
    hash_object,
    hash_pending_write,
    verify_chain,
)
from .identity_gate import (
    IdentityGateResult,
    SubstrateIdentity,
    get_substrate_identity,
    known_substrates,
    register_substrate,
    register_token_validator,
    send_401,
    verify_at_door,
)
from .interceptor import (
    InterceptResult,
    classify_tool,
    intercept,
    pending_summary,
)
from .pending_writes import (
    Proposal,
    ValidationError,
    approve_pending_write,
    commit_pending_write,
    create_pending_write,
    list_pending_writes,
    needs_revision_pending_write,
    reject_pending_write,
    validate_pending_write,
)
from .probe import (
    ProbeOutcome,
    arm_probe,
    await_probe,
    probe_registry_size,
    resolve_probe,
)
from .risk import RiskLevel, risk_classify
from .text_relay import RelayResult, relay_text

__all__ = [
    # Identity gate
    "IdentityGateResult", "SubstrateIdentity", "get_substrate_identity",
    "known_substrates", "register_substrate", "register_token_validator",
    "send_401", "verify_at_door",
    # Bridge context
    "BridgeContext", "get_context", "known_contexts", "register_context",
    # Hash chain
    "get_last_audit_hash", "hash_object", "hash_pending_write", "verify_chain",
    # Audit
    "AuditEvent", "append_audit_event", "read_audit_trail",
    # Pending writes
    "Proposal", "ValidationError",
    "approve_pending_write", "commit_pending_write", "create_pending_write",
    "list_pending_writes", "needs_revision_pending_write",
    "reject_pending_write", "validate_pending_write",
    # Risk
    "RiskLevel", "risk_classify",
    # Interceptor
    "InterceptResult", "classify_tool", "intercept", "pending_summary",
    # Dispatch / text relay
    "pop_bridge_metadata", "RelayResult", "relay_text",
    # Capability probe
    "ProbeOutcome", "arm_probe", "await_probe", "probe_registry_size", "resolve_probe",
]
