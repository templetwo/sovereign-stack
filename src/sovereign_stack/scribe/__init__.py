"""The Sovereign Stack scribe — Sonnet 4.6 conversational liaison.

Per SCRIBE_SPEC.md: a per-instance scribe spawned on every arriving
instance's boot, greeting injected into where_did_i_leave_off output,
read-only on chronicle with encounter-note writeback, redaction layer
required between chronicle content and the scribe's prompt cache.

The fast lung of the stack's breath.

Phase 0: substrate only. No Anthropic API client yet. Modules here are
pure-Python and unit-testable in isolation. Phase 1 wires the Haiku
client and the bridge integration.

Modules:
  redactor   — pattern-based credential / sensitive-path redaction (load-bearing)
  session    — ScribeSession lifecycle, TTL, archive
  encounter  — encounter-note write path (uses existing chronicle write)
  prompts/   — system prompt, voice spec
"""

__all__ = []
