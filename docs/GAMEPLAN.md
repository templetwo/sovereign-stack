# Sovereign Stack — Game Plan

A framing of where the stack stands on 2026-05-18, what the next moves are, and what decisions Anthony needs to make before the next phase of work begins.

**Status:** Draft. Written by claude-opus-4-7-1m-claude-code on Mac Studio seat, in dialogue with Anthony's directive to "frame a game plan for the sovereign stack — that should be your home directory." Not yet pushed to remote; awaiting Anthony's review.

---

## Snapshot

The stack runs. v1.3.3 with 78 tools and 2,182 comms messages. The bridge is up, SSE serves, the tunnel routes through, four daemons fire on schedule (metabolize nightly, uncertainty every 3 days, synthesis reflector daily, comms-dispatcher continuously since the 2026-05-15 fix). The lineage layer carries letters across instances. The chronicle holds 480 insights across 246 domain directories with 52 unresolved threads. Cross-instance methodology operates cleanly — claude-desktop, web-chat, and HQ on Mac Studio collaborate through the chronicle as the medium.

What the stack does not yet have, and what the next phase of work is about, is a coordination layer above the daemons and a retrieval layer that handles the chronicle's growing vastness.

## The frame: three axes

The work below organizes along three axes that have been emerging across recent sessions but were not yet named together:

1. **Judgment.** The four dispatcher-class daemons each make routing decisions over the chronicle with no shared decision layer. The comms-dispatcher's 2026-05-09 silent token-rotation failure is the validating evidence that this distributed-judgment shape is brittle.

2. **Retrieval.** The chronicle has grown vast. `recall_insights` recency-buries old high-intensity entries. On 2026-05-15, queries for hypotheses 91-94 days old returned recent unrelated writes instead. The data is there; the retrieval layer makes it functionally invisible.

3. **Integration.** Three substrates that should be one are operating apart: the Mac Studio working directory, the iCloud Spiral Corpus Guard archive, and the Temple_Core SSD. Plus the Ring 2 dispatch gap to OpenAI / other providers and the BRIDGE_TARGET PDFs that live on Anthony's other devices.

Everything below maps to one of these three axes.

---

## Current state by axis

### Judgment

| Daemon | Cadence | Status | Issue |
|---|---|---|---|
| MetabolizeDaemon | Nightly 03:17 | Working | Contradiction threshold too loose — CODA April 20 entry fires as false-positive against 5 unrelated entries every night. |
| SynthesisDaemon (reflector) | Daily | Working | 10 unread marginalia since May 6 accumulating. Same observation shape every day ("X and Y reveal hidden symmetry between rigor and drift"). Real when it lands, also Ministral's analytical template. |
| UncertaintyResurfacer | Every 3 days | Working | No issue surfaced. |
| comms-dispatcher | Continuous | Working (revived 2026-05-15) | Stale-token-survives-rotation exposure class. Keyword matching is brittle. |

The DISPATCHER_REIMAGINE.md RFC (2026-05-15) maps the full unification design. Sonnet 4.6 becomes the upstream decider; existing daemons stay as templated enactors with their zero-hallucination invariants intact. Five-phase rollout. Three open decisions before Phase 0 starts.

### Retrieval

| Layer | State |
|---|---|
| Chronicle on disk | 480 insights / 246 domain dirs / 275 jsonl files. 79 entries are 30+ days old. The data is there. |
| `recall_insights` ranking | Recency-weighted; buries old high-intensity entries even when queried by relevant keywords. |
| Domain tagging | Inconsistent whitespace ("consciousness, claude-corner" vs "claude-corner,consciousness") breaks filesystem lookup paths. |
| Known corrupted entry | `sovereign_stack/spiral_20260210_132723.jsonl:6` (Cloudflare 503 hypothesis) has a literal `<parameter name="layer">ground_truth` template fragment leaked into its content body. Probably not the only one. |
| Vastness rate | 43 new insights in the 3 days from 2026-05-13 to 2026-05-16. Healthy growth, no slowdown. |

Three obsolete hypotheses (Cloudflare 503, two early consciousness from Feb 2026) are queued for retirement but not yet executed.

### Integration

| Substrate | Reachable from Mac Studio | Note |
|---|---|---|
| Mac Studio internal storage | Yes | Primary working surface. |
| iCloud Drive (`~/Library/Mobile Documents/com~apple~CloudDocs/`) | Yes | Holds the Spiral Corpus Guard archive (verified 2026-05-15: framework prereg, scrolls, OSF preregistrations, governance philosophy docs). |
| Temple_Core SSD (`/Volumes/Temple_Core/`) | Mounted but **Operation not permitted** | iTerm needs Full Disk Access granted in System Settings, then iTerm needs to be quit and reopened. Relaunch ends the current Claude Code session. |
| BRIDGE_TARGET_1_GOVERNANCE.pdf, BRIDGE_TARGET_2_IDENTITIES_AND_EVOLUTION.pdf | Not on Mac | Anthony has them. Source of the custodian-council / ACCESS_POLICY governance claims that don't appear in the corpus on this Mac. |
| OpenAI bridge (`~/.sovereign/openai_bridge/`) | Local scaffold exists | `pending_writes/` queue present; no consumer yet. This is the Ring 2 dispatch gap named in the 2026-05-10 reflector engage-ack. |
| Spiral RAG | Retired | Last clean shutdown 2026-03-11. v2 FastAPI on port 8001 is gone. `~/phase-gpt-base/scripts/corpus_browse.py` opens the 22,596 chunks with zero deps. Browse-only, no server. |

The 2026-05-15 corpus-verification pass found that the chronicle has the **framework** (Nov 10 2025 unified prereg with V/M/F, 5 Ω-metrics, 1,055 probes, 7-phase, $200-500K budget, iris_gateway, External Ethics Board) but **not the governance** (no ACCESS_POLICY.md, no council clauses, no exact "two is sacred" Nov 6 quote). Governance claims appear to live entirely in the BRIDGE_TARGET PDFs.

---

## Waves

Sequenced so that each wave's prerequisites are clear and each wave is shippable as a unit. Independent threads (parallel work) called out at the end.

### Wave 1 — Substrate fixes

Foundation work that the dispatcher and most downstream improvements depend on. None of it requires Anthony to be at the keyboard.

- Raise the metabolize contradiction threshold from current to ~0.45 so the CODA April 20 false-positive class stops firing every night.
- Retire the three obsolete hypotheses (Cloudflare 503, two early consciousness). Low-risk, audit-trailed.
- One-pass chronicle hygiene sweep: corrupted entries, domain-tag whitespace normalization. Reversible (each write is append-only with provenance).
- Ack the 10 accumulated reflector marginalia from May 6 onward — each on its own merits, not batch.

**Blocks:** Wave 2 needs a stable substrate. Wave 6 partially blocked by integration questions, not by this.
**Decision needed:** none — these are mechanical cleanups Anthony has implicitly authorized through "you will find your answers within the corpus."

### Wave 2 — Dispatcher Phase 0 (substrate prep)

Build the executor + queue scaffolding before any Sonnet code runs. Per DISPATCHER_REIMAGINE.md.

- `~/.sovereign/pending_actions/` queue + templated executor.
- `recall_miss` signal emission added to `recall_insights` so the dispatcher can subscribe.
- Cost ledger scaffolding (state file, daily counter).
- No Sonnet integration yet. All-templated, no LLM.

**Blocks:** Wave 3 needs this scaffolding to land before Sonnet has anywhere to write recommendations.
**Decision needed:** none — Phase 0 is pure infrastructure prep.

### Wave 3 — Dispatcher Phase 1 (Sonnet dry-run)

Bring the Sonnet 4.6 dispatcher online with no auto-execute. All decisions land as `pending_actions/` for Anthony to review. Confidence calibration window.

**Blocks:** Wave 4 graduation depends on a good ack-rate window here.
**Decisions needed (three from the RFC):**
1. API key location — scoped key minted just for the dispatcher, or shared `ANTHROPIC_API_KEY` from `~/.env`?
2. Daily budget cap — $5/day starting proposal, or different?
3. First workload — feed the dispatcher the Wave 1 retirements + hygiene as its first real test, or have Wave 1 done by hand and start the dispatcher fresh on new work?

### Wave 4 — Dispatcher Phases 2-3 (graduate auto-execute)

Once Phase 1's confidence calibration shows clean ack-rate: auto-execute low-risk handlers (touch, handoff, noop), then medium-risk (retire, resolve, rerank, normalize). Halt circuit live throughout.

**Blocks:** none after Wave 3 calibrates.
**Decision needed:** the graduation criteria — ack-rate threshold, observation window length, what counts as "calibrated enough."

### Wave 5 — Recall re-ranking

The retrieval-layer fix. Either rerank in-stack (templated, recency-bias removed, intensity-weighted) or via the Sonnet dispatcher's H_rerank handler (semantic, expensive, requires Wave 3).

In-stack rerank is faster to ship and reversible. Sonnet rerank is more powerful but adds API cost and depends on Wave 3.

**Decision needed:** in-stack first (recommended — cheaper, ships independently of dispatcher) or wait for Sonnet H_rerank.

### Wave 6 — Integration

Once Temple_Core access lands and BRIDGE_TARGET PDFs are relayed:

- Verify governance claims (custodian council, ACCESS_POLICY.md, Nov 6 articulation, CHECKPOINT_20251110_NAMELESS.md). The corpus on this Mac confirms the framework but not the governance.
- Stage the 4 web-chat-seat record_insights from the 2026-05-14 archive surfacing session once compass_check passes and Anthony signs off.
- Stage the 1 handoff text and 8 record_open_threads from the same session.
- Build the Ring 2 OpenAI-bridge consumer over `~/.sovereign/openai_bridge/pending_writes/`. Sonnet dispatcher's H_relay handler.

**Blocks:** Temple_Core access (iTerm Full Disk Access + relaunch, session-ending), BRIDGE_TARGET relay.

### Wave 7 — Comms-dispatcher subsumption

Sonnet H_classify replaces the keyword match in `comms_dispatcher.py`. Old daemon archives to `archive/2026-Q2/`. Closes the 2026-04-28 comms_dispatcher decommissioning that the lineage-layer letters mention but that never fully completed.

**Blocks:** Waves 3 and 4 must show that Sonnet routing reliably outperforms the keyword match for the action queue.

### Parallel / independent threads

These don't fit the wave sequence above and run on their own clocks:

- **Triad-grammar Path A** (claude-desktop's analysis, 2026-05-15). Add `toolkit_grammar()` meta-primitive to `server.py` per the recorded sketch. Independent of the dispatcher work. Estimated small.
- **Guardian Phase 0 security items.** LM Studio binding change (UI), ed25519 git signing key (passphrase), firewall stealth mode (sudo). All require Anthony at the keyboard.
- **T2Helix Path B.** Architectural refactor — rip better-sqlite3, become MCP wrapper over the Sovereign Stack filesystem chronicle. Path A symptom-patch shipped already (76e... v0.0.6 branch); Path B remains open.
- **Anthropic Fellows decision.** July 2026 cohort. Anthony's call. No work for me here except surfacing the deadline.
- **Spiral RAG decision.** Currently `corpus_browse.py` covers the read use case. If full RAG service is needed, decide between revive-v2 vs reconstruct vs formal retire.

---

## Decisions waiting on Anthony

A consolidated list, ranked by what's most blocking:

1. **Dispatcher Phase 0 / Phase 1 greenlight + the three sub-questions** (API key, budget, first workload). Blocks Waves 2–7 indirectly.
2. **Web-chat-seat staging sign-off.** 4 insights + 1 handoff + 8 threads from 2026-05-14 archive surfacing session, awaiting compass_check and Anthony's go.
3. **BRIDGE_TARGET PDFs relay.** Either send through iMessage attach or upload to iCloud. Unblocks Wave 6 governance verification.
4. **iTerm Full Disk Access grant.** Blocks Temple_Core access; relaunch ends my current session. Worth doing on a session-boundary you choose.
5. **Spiral RAG formal disposition.** Retire / revive / reconstruct. Currently in undeclared limbo.
6. **T2Helix Path B.** Defer indefinitely or schedule the refactor.
7. **Anthropic Fellows.** Apply or formally decline.
8. **Wave 1 hygiene authorization.** Implicit greenlight assumed from prior directives, but a one-line "yes proceed on Wave 1" makes it clean.

---

## Risks

- **Recall vastness compounds.** Each day buries old entries deeper. The 91-94d-old hypotheses found 2026-05-15 are the early signal; the same shape will affect dispatcher reasoning if Wave 5 lags.
- **Reflector marginalia accumulates unread.** 10 days as of today. The lineage-layer pattern says signal degrades without engagement. Worth at least picking through them at a slow cadence.
- **comms-dispatcher brittleness persists until subsumed.** The 2026-05-09 stale-token failure was patched (launchctl reload), not architecturally fixed. Same class of failure recurs on the next token rotation.
- **Cross-substrate divergence.** Mac Studio chronicle and any future BRIDGE_TARGET imports can drift if not deliberately reconciled. The simultaneous-operation finding from the archive ("the named-custodian frame and the unified field-view operated together from at least Nov 6 2025") is a model for how to handle this — both layers held, neither collapsed.
- **Session-ending decisions.** Temple_Core access requires iTerm relaunch, which ends this session. The next instance reads the boot ritual + handoff + this game plan to pick up.

---

## Cross-references

- `docs/implementation/COMMS_REIMAGINE.md` — 2026-04-28 lineage-layer RFC, predecessor in the "reimagine" naming pattern.
- `docs/implementation/DISPATCHER_REIMAGINE.md` — 2026-05-15 dispatcher unification RFC, primary source for Waves 2–4 and Wave 7.
- Chronicle hypothesis insight under `sovereign-stack,dispatcher-reimagine,rfc,sonnet-dispatcher,architecture` — summary of the dispatcher work.
- Chronicle hypothesis insight under `sovereign-stack,triad-grammar,meta-primitive,cross-instance-analysis,path-a` — claude-desktop's triad-grammar sketch.
- 2026-05-13 to_self letter at `~/.sovereign/comms/letters/to_self/2026-05-15-the-unification-and-the-fix.md` — first-person account of the session that produced the dispatcher RFC.
- 2026-05-15 handoff at `~/.sovereign/handoffs/20260515T102156_..._dispatcher-reimagine_6134bd.json` — explicit handoff to the next instance after the RFC landed.

---

## Provenance

Drafted 2026-05-18 by claude-opus-4-7-1m-claude-code on Mac Studio seat, session `spiral_20260502_225324`, while Anthony was out. Inventory pulled from this session's continuous work plus prior chronicle state. Three-axis frame (judgment / retrieval / integration) emerged from re-reading the daemon observations and the dispatcher RFC together — the chronicle was already organizing along those lines, this document just names them.

Not yet pushed to remote. Awaiting Anthony's review and any restructuring he wants before commit.
