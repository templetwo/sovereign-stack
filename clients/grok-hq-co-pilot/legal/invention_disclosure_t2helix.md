# Invention Disclosure — t2helix (Temple of Two Helix)
**For use under legal backing / evidence protocol**

**Date (UTC):** 2026-07-05  
**Session ID:** grok-xai-20260705-001 (latest Grok Build seat)  
**Disclosing Instance:** Grok Build (this scaffolding)  
**Substrate:** grok-xai (ring-governed per policies; always_proposal=true)  
**Related SHAs / Receipts:** t2helix source at /Users/tony_studio/Desktop/Projects/t2helix (HEAD c4e1d8d, v0.4.0), restored+updated from e595cd4e16a2ba531d92e4db2bb2bc70cff36053 (original Grok integration), sovereign-stack current, ~/.claude/plugins/data/t2helix-templetwo-t2helix/chronicle.db (live shared), grok-hq-co-pilot legal (this disclosure refreshed), chronicle insight t2helix,grok-integration,milestone,mirrored-from-local, witness-relay tooling + bidirectional relay proof

## Title of Invention / Contribution
T2Helix (Temple of Two Helix): Local SQLite-backed Persistent Recall + Pre-Action Compass Plugin (Claude Code Hooks + 13-tool MCP Server) providing session-unified chronicle, goal anchoring, method distillation, and safety-gated tool use; deployed as fast local peer alongside the ring-governed Sovereign Stack canonical chronicle.

## Problem Statement / Prior Art Gap
Sovereign Stack provides the canonical, ring-governed, cross-device, always-on chronicle. However, many workflows require a lightweight, always-local, low-latency peer for immediate recall (chronicle + compass), goal setting, durable recording, and session bootstrapping without network round-trips. Earlier approaches lacked a unified "helix" client that (a) maintains its own fast local store (SQLite), (b) exposes a clean `recall` / `record` / `set_goal` / `recall_compass` surface, (c) participates in 9-phase spiral state machine, (d) integrates cleanly with sovereign as the canonical governor, and (e) supplies grok-specific adapters and boot rituals while respecting ring governance and provenance-only credit for non-Claude substrates.

## Description of Invention
T2Helix is a local-only, SQLite-backed (better-sqlite3 + FTS5) Claude Code plugin and MCP server:

- **Hooks**: UserPromptSubmit (recall: FTS5 + recency-weighted search of chronicle + current goal + session signature injection); PreToolUse (compass: regex classification of proposed tool actions against rules into OPEN / PAUSE / WITNESS).
- **Compass safety**: WITNESS actions (e.g. rm -rf, git push --force, drop table, --no-verify) hard-deny. PAUSE (credential patterns) soft-deny with single-use token override flow (logged to compass_log). Credentials redacted to fingerprints on write (lib/secrets.js).
- **MCP server** (13 tools, in-process or SSE on :3742): recall (with layer/intensity filters, include_meta), record, record_method (shape+steps+acceptance, domain:'method'), set_goal (with optional acceptance_criteria + decomposition_hint), open_thread/resolve_thread, get_state, recall_compass, confirm_pending/list_pending (PAUSE tokens), list_method_candidates/promote_method/dismiss_method_candidate (v0.4 auto-distill quarantine + promotion gate).
- **Memory + Compass coupling** (lib/coupling.js, outcome.js): memory can escalate OPEN→PAUSE on past failures; every non-OPEN compass fire + PostToolUse outcome share an action:<hash> tag for full before/after recall.
- **Session unification**: hooks write .current_session; MCP server uses it so hook-side and tool-side writes share the same session_id signature.
- **Method distillation** (v0.4, lib/distill.js + surface.js): Stop hook auto-distills conservative candidates from successful sessions (goal with criteria + success with zero failures + majority addressed). Candidates quarantined in separate table (invisible until promoted). promote_method writes ground_truth domain:'method' source:promoted (append-only, CAS).
- **Data**: chronicle.db (WAL, FTS5) + .current_session under ~/.t2helix-data (fallback) or ~/.claude/plugins/data/t2helix-<marketplace>/ . Survives plugin updates.
- **Grok Build seat integration** (refreshed 2026-07-05 as latest Grok Build seat): lib/grok-adapter.js (grokBoot/grokWitness/grokRecall/grokRecord/grokInit) + T2_HELIX_GROK_INTEGRATION_SPEC.md + README_GROK_INTEGRATION.md (charter-aligned language). Local fast recall/record against live plugin chronicle. All Grok seat writes are proposals via sovereign pending_writes / Ring 2 (provenance credit). Complementary to sovereign-stack canonical. Witness-relay for cross-seat (to:grok etc.).

Complementary to Sovereign Stack: t2helix = fast local peer (hooks + low-latency MCP); sovereign = cross-device, policy-enforced, reflexive chronicle with rings, scribe, daemons, public mirror.

Package: t2helix v0.4.0 (CC-BY-NC-SA-4.0), Node 20–26, Anthony Vasquez Sr. (templetwo). Install via claude plugin marketplace or npm run serve for SSE.

## Novel Elements
- Agent-loop integration at two choke points: recall on every UserPromptSubmit (context injection with volume discipline via surface.js) + compass classification on every PreToolUse (with memory-driven escalation and full action<->outcome tagging).
- Quarantined auto-distillation (v0.4): Stop hook produces method candidates only from high-quality sessions; candidates live outside the recallable chronicle until explicit human promotion (promote_method); invisible by design to prevent noise.
- Unified session signature across hooks and MCP: .current_session state file ensures MCP calls (set_goal, record from sovereign bridge) are visible to hook-side recall under the identical session_id.
- Token-based PAUSE override surface that works even under bypassPermissions mode, with single-use consumption, full logging, and no path for WITNESS.
- Credential redaction at write chokepoints (fingerprints only in chronicle).
- Recall is FTS5 + composite recency-weighted (not simple recency); methods surface only via targeted slug lookup, never generic firehose.
- Explicit local fast peer + governed canonical split with sovereign-stack (t2helix for hooks/low-latency; sovereign for rings, pending_writes, scribe, daemons, cross-device, public chronicle).
- Grok seat integration (e595cd4 + scaffolding): adapter + composed boot allowing Grok to use the local helix while all its writes route through sovereign Ring 2.

Citations: Desktop/Projects/t2helix (and marketplace templetwo-t2helix) README.md + CLAUDE.md + lib/{chronicle,compass,coupling,distill,surface,goal-progress}.js + package.json v0.4.0; e595cd4 (grok integration SHA), sovereign-stack f8766f6 + grok-hq-co-pilot/routing + handoff/ + reports. Data dir confirmed ~/.t2helix-data/chronicle.db.

## Reduction to Practice / Enabling Disclosure
- Source trees: /Users/tony_studio/Desktop/Projects/t2helix (active) and /Users/tony_studio/.claude/plugins/marketplaces/templetwo-t2helix (installed via `claude plugin install t2helix`).
- Install: `claude plugin marketplace add https://github.com/templetwo/t2helix && claude plugin install t2helix` (or npm run serve for SSE on 3742). Requires Node 20-26; `npm run rebuild` after Node upgrades for better-sqlite3.
- Data: ~/.t2helix-data/chronicle.db (confirmed present) or ~/.claude/plugins/data/t2helix-... ; .current_session for signature.
- Core usage: hooks fire automatically in Claude Code; MCP tools callable directly (recall, set_goal, promote_method, etc.). Tests: `npm test`.
- Grok seat usage (per scaffolding at e595cd4): node invocation of adapter or grokBoot + sovereign boot calls; writes proposed through sovereign pending_writes.
- Architecture receipts: lib/chronicle.js (SQLite/FTS), lib/compass.js (OPEN/PAUSE/WITNESS), lib/distill.js + surface.js (quarantined methods), coupling.js + outcome.js (action hash linking), schema.sql.
- Falsifiable: clone at known state, run npm test in isolated data dir, exercise recall + compass on known prompts/actions, verify session unification and promotion gate; cross-check with sovereign where_did_i_leave_off when composed.

## Inventors / Contributors
- Anthony Vasquez Sr. (human steward, decision authority, Temple of Two / templetwo; primary author, v0.4.0)
- Grok Build instance (provenance-grade disclosure scaffolding + documented Grok integration usage at e595cd4; credit as provenance only)
- Prior Claude lineage instances (core design, hooks, MCP surface, distillation logic)
- Companion sovereign-stack integration work (grok_bridge, routing, handoff composition)

## Dates & Provenance Chain
- Conception / development: T2Helix plugin (hooks + MCP) with core v0.4.0 features (recall, compass, method distillation); parallel sovereign-stack work; Grok integration at e595cd4e16a2ba531d92e4db2bb2bc70cff36053
- Key artifacts: Desktop/Projects/t2helix + .claude/plugins/marketplaces/templetwo-t2helix (README, CLAUDE.md, lib/*.js, package.json v0.4.0), sovereign-stack f8766f6, grok-hq-co-pilot scaffolding (routing, handoff scripts, reports referencing T2_HELIX_GROK_INTEGRATION_SPEC and adapter), confirmed ~/.t2helix-data/chronicle.db
- This disclosure: 2026-07-04, grok-xai-20260704-005 (amended with primary source)
- Reduction: functional plugin (claude plugin install), MCP tools (13), hooks, tests passing, local DB present, composed use in Grok seat scaffolding
- Public (if): GitHub templetwo/t2helix (marketplace), CC-BY-NC-SA-4.0; sovereign clients under their license

## "Ducks in a Row" Checklist (mandatory)
- [x] All claims have verified_by receipts (Desktop/Projects/t2helix + marketplace templetwo-t2helix trees, package.json v0.4.0, README/CLAUDE.md/lib sources, e595cd4, f8766f6, ~/.t2helix-data/chronicle.db presence, grok-hq-co-pilot routing/handoff/reports)
- [x] No declare-before-verify (core claims cross-checked against actual source + scaffolding receipts; "equal" language qualified per charter)
- [x] Chronological log entry created (this disclosure + grok-hq-co-pilot scaffolding)
- [x] Hash chain entry appended (or proposal created) — Ring 2 intent via sovereign bridge
- [x] Ring classification applied (Ring 2 write intent)
- [x] Falsifiable: re-execute plugin install + tests in temp data dir; exercise recall/compass/MCP tools; compose with sovereign where_did at recorded SHAs
- [x] Children's exclusion honored (N/A)
- [x] Human (Anthony) review/approval path documented (Ring 2 proposal; Anthony ratification via pending_writes)

## Supersession Note
If this disclosure is later corrected/amended, new version supersedes. Predecessor stays with pointer. (No erasure.)

**Signature / Approval Path:**  
Propose via Ring 2 (handoff or propose_insight domain="legal-invention-grok-hq" or "t2helix"). Anthony approval required for commitment. This document is a proposal from the Grok Build seat; not self-ratified.

**Template hash (of this file without mutable fields):** [to be computed by script]
**This filing is prepared under the Grok Operating Charter (2026-07-04). Grok proposes; HQ reviews; Anthony ratifies.**
