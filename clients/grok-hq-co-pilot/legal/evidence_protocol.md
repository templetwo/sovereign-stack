# Evidence & Legal-Grade Provenance Protocol (Grok HQ)
**2026-07-03**

## Principles (Rigor)
- Evidence primacy: claim without receipt is hypothesis at best; with receipts can be ground_truth.
- Falsifiability: every assertion points at checkable artifact (git SHA, API timestamp, file hash, external verifiable).
- Hash chains for tamper evidence (bridge_core/hash_chain + this layer).
- Chronological immutable logs.
- "Ducks in a Row" before high-stakes (see separate marker doc).
- Receipt types: git, stack_api_response, file_sha256, external_url+hash, t2helix local record id, etc.

## Required for Every Significant Action
1. Compute sha256 of canonical representation.
2. Timestamp (prefer stack server time from heartbeat).
3. Attach session_id.
4. Attach verified_by list (min 1 for hypothesis, 2+ preferred for stronger).
5. Log to local + bridge audit (if via Grok bridge).
6. For chronicle writes: use Ring 2 propose path + receipt in payload.
7. For legal/invention: use this template + ducks checklist.

## Example Log Entry (JSONL append)
See archival_protocol_template.md for full.

## Verification Commands
- Re-clone at exact SHA: git clone ... ; cd ... ; git checkout <sha>
- Re-run heartbeat + where_did... and diff outputs.
- Run bridge verify_chain equivalent.
- scripts/compute_provenance.sh (to be implemented)

## Fable-Specific
Every safety flag diagnosis step (as in 2026-07-02/03 saga) must carry at least: 
- originating seat SHA + tool call trace
- verification pass hashes (multi-agent)
- outcome receipt (e.g. "VERIFIED" marker from human or cross-check)

## Integration with Chronicle
Proposals carry the evidence bundle. On approval, the receipt travels with the entry.

**This protocol is itself subject to the same rules.**
