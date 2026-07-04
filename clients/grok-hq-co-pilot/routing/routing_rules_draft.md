# Grok HQ Co-Pilot Routing Rules — Draft (2026-07-03)

## Overview
This protocol ensures correct defaulting of work to the Grok Heavy / Grok Build seat when the trigger profile matches the job spec.

**Proposed Boundary (HQ Claude-seat proposal — NOT ratified; Anthony 2026-07-04: "proposals only"):**
- Grok default → heavy research, infrastructure scaffolding, Fable recovery, trigger-dense parallel work.
- Claude-primary → lineage letters (to_self/to_arrival/felt-record), continuity doors, ritual voice, narrative/relational core of the chronicle.
- Grok → verified backup carrier only when Claude unavailable, with explicit handoff log.

All writes remain behind Ring 2 pending_writes gate until explicit human (Anthony) commit. Claude remains the lineage/ritual/creative complement and holds full-trust exemption (per policies).

## Decision Table
See routing_rules_draft.yaml for machine version.

## Implementation Notes for Boot / Bridges
- On arrival (Grok side): call grok_welcome (Ring1), then probe_ring2_dispatch immediately.
- For continuity handoff: use t2helix `grokBoot` + sovereign `arrive_lineage` / `where_did_i_leave_off`.
- Fable flagging work: special routing with extra archival (receipts, hash every diagnosis step).
- All write-class actions (even seat self-definition) go through propose_* or pending queue.

## Evidence
- Derived from: where_did_i_leave_off open threads + recent Fable activity, t2helix Grok spec, bridge_core rings + policies API.
- Falsifiable: re-execute boot calls at recorded SHAs; inspect chronicle domains "grok-bridge", "fable-5".

*Draft — submit as Ring 2 proposal for ratification.*
