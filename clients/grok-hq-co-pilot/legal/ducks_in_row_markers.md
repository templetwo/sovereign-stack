# "Ducks in a Row" Markers — Legal-Grade Readiness Checklist
**Grok HQ Co-Pilot**  
**Use before any high-stakes declaration, invention filing, or Ring 2 proposal with external weight.**

## Core Markers (all required)
- [ ] **Receipts present:** At least two independent, checkable verified_by for each material claim. No lone citation.
- [ ] **No declare-before-verify:** Cross-reference against recent self-model / reflector marginalia (e.g. "declare-before-verify" persistent markers). Explicit "verified" step logged.
- [ ] **Hash chain intact:** Local audit + bridge audit verified (verify_chain or equivalent). Broken chain = halt.
- [ ] **Session attributed:** grok-xai-YYYYMMDD-NNN (or current convention) on every log/proposal.
- [ ] **Layer classified:** ground_truth only with receipts + human gate; else hypothesis/open_thread.
- [ ] **Risk assessed:** compass_check or risk_classify passed (or documented exception with approval).
- [ ] **Policy aligned:** current_policies() consulted; non-Claude ring rules, protected records, co-authorship rules honored.
- [ ] **Falsifiable:** A third party (or future self) with the listed SHAs + timestamps can reproduce the claim state.
- [ ] **Supersession path:** If correction needed later, new entry points back; old stays.
- [ ] **Human gate:** For Ring 2 or legal weight: proposal created; Anthony sign-off path explicit (pending_writes ID or note).

## Fable Recovery Specific Ducks
- [ ] Multi-architecture verification (Grok + at least one other) for any safety flag diagnosis.
- [ ] Timeline receipts (tool call logs, git SHAs of analysis code).
- [ ] Input-classifier false-positive analysis has raw traces + mitigation hashes.
- [ ] Human (Anthony) confirmation of "VERIFIED" or equivalent.

## Usage
Before emitting a claim:
1. Run scripts/ or manual checklist.
2. Append marker block + hashes to the archival entry.
3. Only then proceed to propose / publish.

**Marker example in log:**
```
ducks_in_row: 2026-07-03T18:20:00Z
session: grok-xai-20260703-001
receipts: [git:f8766f6, heartbeat:1783102578, file:abc123...]
verified_steps: ["re-ran where_did full", "policy re-fetch", "hash_chain_verify: PASS"]
```
