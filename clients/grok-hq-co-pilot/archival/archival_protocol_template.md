# Full Archival Levels Enforcement Protocol Template
**Grok HQ Co-Pilot Seat**  
**Version:** 2026-07-03-template-v1  
**Backed by:** sovereign-stack bridge_core/hash_chain.py (SHA256), pending_writes.py, audit.py  
**Source SHAs:** sovereign-stack f8766f6...

## Mandate
Every action by this seat (and any Grok non-Claude substrate) MUST produce:
- sha256 hash of content (excluding mutable hash field)
- ISO8601 timestamp (UTC, server-synced preferred)
- session_id (grok-xai-YYYYMMDD-NNN)
- verified_by receipts (where artifact exists: e.g. git SHA, API response hash, file hash, DOI, external URL)
- layer classification: ground_truth | hypothesis | open_thread | reflection (per epistemology in manifest)
- risk classification (via bridge_core/risk.py or compass)

## Levels
1. **Action Log** (mandatory, immediate): local file or stdout + append to substrate audit chain.
2. **Bridge Audit** (mandatory for Grok): uses hash_chain + pending_writes. Every Ring2 proposal hashed with prev.
3. **Chronicle Entry** (via propose_* for writes): record_insight etc. with receipt.
4. **Verbatim Archive** (archive_exchange for large artifacts): content-addressed.
5. **Legal / Prior Art Chain**: invention disclosures, ducks-in-row, hash-linked chronological logs.

## Receipt Format (example)
```json
{
  "action": "fable_safety_diagnosis_step_3",
  "timestamp": "2026-07-03T18:17:34Z",
  "session_id": "grok-xai-20260703-001",
  "content_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "verified_by": [
    {"type": "git_commit", "value": "f8766f6a27486e85fca10fdfffd5ab3a3c8ebcea"},
    {"type": "api_heartbeat", "value": "stack.templetwo.com 2026-07-03T18:16:18Z"},
    {"type": "external", "value": "https://... (if any)"}
  ],
  "prev_audit_hash": "...",
  "audit_hash": "<computed>",
  "layer": "hypothesis",
  "risk": "medium"
}
```

## Enforcement in Scaffolding
- All generated artifacts in this dir have accompanying .sha256 sidecars (see scripts/).
- Boot scripts / handlers compute + log.
- For Fable Revival Kit v2: every flag diagnosis step archived at level 3+ with receipts.
- Use bridge_core.verify_chain() equivalent on audit logs.

## Integration
- t2helix local SQLite for fast recall (with its own logging).
- Sovereign Stack for governed canonical chronicle.

**Ducks in a Row Marker:** Before any high-stakes claim, require N independent receipts + human (Anthony) sign-off via Ring2.

*Template. Populate per action. All entries supersede prior; no erasure.*
