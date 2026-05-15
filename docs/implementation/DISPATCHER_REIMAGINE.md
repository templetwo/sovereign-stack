# DISPATCHER_REIMAGINE

A unified Sonnet-mediated dispatcher for routing decisions over chronicle and comms data, with the existing daemons preserved as templated enactors.

**Status:** RFC. Design landed 2026-05-15 by claude-opus-4-7-1m-claude-code on Mac Studio seat, prompted by Anthony asking to reimagine the dispatcher after the comms-dispatcher fell to a stale-token loop. Not implemented.

**Companion doc:** `COMMS_REIMAGINE.md` (2026-04-28 lineage layer RFC). This is its successor for the routing layer above the chronicle.

---

## Motivation

By 2026-05-15 the stack has four concurrent dispatcher-class daemons plus a bridge dispatch helper plus a named architectural gap. Each makes routing decisions over the same chronicle/comms substrate, with no shared decision layer:

| Component | Decides | Output | Failure mode |
|---|---|---|---|
| `comms_dispatcher` | what to do with inbound comms (keyword match) | action queue + comms post | brittle keywords; silent 403 loop when token rotates |
| `MetabolizeDaemon` | what is stale, contradictory, aging | nightly digest + decision file | templated, no LLM (load-bearing for zero hallucination surface) |
| `SynthesisDaemon` | what patterns emerge across entries | reflections JSONL | local LLM (Ministral 3:14B), ack-rate-calibrated |
| `UncertaintyResurfacer` | what to resurface | every-3-days digest | templated |
| Bridge dispatch helper | where to relay text | submit-text CLI | new, on-demand |
| Ring 2 dispatch | (named gap) | none yet | not built |

The chronicle has grown vast (464 insights / 246 domain dirs / 87 thread entries as of 2026-05-15). Old high-intensity entries get recency-buried by `recall_insights`. The 4 separate judgment systems can't see each other's decisions.

## Unifying principle

> Four judgment systems are currently looking at the same chronicle. Unify the judgment. Let the daemons stay specialized in their templated enactment.

Sonnet 4.6 becomes the judgment layer. The existing daemons remain the enactment layer with their zero-hallucination invariants intact. The chronicle stays the substrate. Monitors are the senses. Confidence gates and halt circuits are the discipline.

This separation is load-bearing: the MetabolizeDaemon's "no LLM in v1" call was deliberate (zero hallucination surface for ack-required digests). Putting Sonnet upstream as the decider, with templated daemons downstream as the enactors, preserves that invariant while solving the maintenance brittleness that broke `comms_dispatcher` on 2026-05-09.

## Architecture

```
┌── MONITORS (event sources) ─────────────┐    ┌── DISPATCHER CORE ────────┐    ┌── EXECUTORS / SINKS ──────┐
│ M1 comms-watcher    (poll bridge 5s)    │    │  Sonnet 4.6               │    │ X1 chronicle_writer       │
│ M2 chronicle-watcher (fs inotify)       │    │  prompt-cached:           │    │    (templated, no LLM)    │
│ M3 thread-aging-ticker (15-min cron)    ├───►│   - chronicle base        ├───►│ X2 comms_poster           │
│ M4 recall-miss-listener (in-stack tool) │    │   - active investigations │    │    (templated)            │
│ M5 hypothesis-aging-ticker (daily)      │    │   - dispatcher policy     │    │ X3 pending_actions queue  │
│ M6 cost-ledger-watcher (continuous)     │    │  per event:               │    │    /<ts>_<verb>.json      │
│ M7 halt-alert-listener (cross-daemon)   │    │   - route                 │    │ X4 reflector_router       │
│ M8 user-dispatch (iMessage/comms)       │    │   - emit recommendation   │    │ X5 bridge_relay (Ring 2)  │
└─────────────────────────────────────────┘    │   - confidence score      │    │ X6 escalation_to_anthony  │
                                               │  halt circuit             │    │    (comms + iMessage)     │
                                               │  cost circuit             │    └───────────────────────────┘
                                               └───────────────────────────┘
```

### Event types

| Event | Source | Trigger |
|---|---|---|
| `E_comms_arrival` | M1 | new comms message |
| `E_chronicle_write` | M2 | new insight or thread written |
| `E_thread_aging` | M3 | thread crosses 30/60/90d threshold |
| `E_recall_miss` | M4 | in-stack recall returns <N results for query |
| `E_hypothesis_aging` | M5 | hypothesis crosses 60/90/120d threshold |
| `E_contradiction_signal` | M2 | metabolize daemon flags new contradiction |
| `E_halt_alert` | M7 | sibling daemon halted |
| `E_user_dispatch` | M8 | direct directive from Anthony |
| `E_scheduled_tick` | M3 | every 15 min, run hygiene check |
| `E_investigation_evidence` | M2 | write under actively-tracked domain |

### Handler taxonomy

| Handler | Action | Risk tier |
|---|---|---|
| `H_classify` | Replace `comms_dispatcher.parse_action`'s keyword match. Emit intent + args. | Low |
| `H_research` | Queue research task for in-session Claude pickup. | Low |
| `H_touch` | Queue `thread_touch(thread_id, note)`. | Low |
| `H_handoff` | Queue handoff write for next instance. | Low |
| `H_retire` | Queue `retire_hypothesis(id, reason)`. | Medium |
| `H_resolve` | Queue `resolve_thread_by_id(id, resolution)`. | Medium |
| `H_rerank` | Trigger chronicle re-rank pass for query domain. | Medium |
| `H_relay` | Dispatch to Ring 2 (OpenAI bridge / other external). | Medium |
| `H_normalize` | Queue domain-tag normalization or corruption-flag write. | Medium |
| `H_escalate` | Surface to Anthony via comms + iMessage. | High |
| `H_noop` | Audit-only log. | None |

### Confidence gates

Every Sonnet routing decision returns `{ handler, args, confidence: 0.0-1.0, rationale }`.

| Confidence | Action |
|---|---|
| ≥ 0.85 | Auto-execute via X-tier executor |
| 0.60-0.84 | Queue to `~/.sovereign/pending_actions/` for Anthony review |
| < 0.60 | `H_escalate` — direct surface, never auto-act |

Pattern mirrors the reflector ack/engage/discard.

### Circuits

- **Three-strikes confidence halt**: 3 consecutive sub-0.40 calls → halt + post to `daemon.halt-alert`. Same pattern as existing daemons.
- **Cost cap**: configured in `policy.py`. Default starting proposal $5/day. Spend logged to `state/cost_ledger.jsonl`.
- **Rate cap**: hard cap of N events/minute to prevent runaway loops.
- **Cache invalidation**: chronicle base cached for 5 min (Anthropic prompt cache TTL); rebuild on cache miss.

### Attribution

Dispatcher-emitted artifacts carry `dispatcher.sonnet-4-6` attribution. Recommendations queued in `pending_actions/` include full Sonnet rationale + confidence. When an executor enacts the action, the chronicle entry's source reads `executor.via-dispatcher.sonnet-4-6.<event_id>`. End-to-end audit trail.

## Where existing daemons fit

| Daemon | Status under unified design |
|---|---|
| `MetabolizeDaemon` | Preserved. No-LLM templated digest is load-bearing. Remains the nightly metabolizer. Dispatcher subscribes to its contradiction-signal output (`E_contradiction_signal`). |
| `SynthesisDaemon` | Preserved. Local Ministral marginalia at high cadence. Dispatcher can escalate `synthesis spanning-sample` mode to Sonnet via API when chronicle exceeds local context. |
| `UncertaintyResurfacer` | Preserved. Templated 3-day cadence. |
| `comms_dispatcher` | **Subsumed.** Its `parse_action` keyword match becomes `H_classify` (Sonnet semantic classification). Old `comms_dispatcher.py` archives to `archive/2026-Q2/` after Phase 5. |
| Bridge dispatch helper | Preserved as one transport (`X5 bridge_relay`). |
| Ring 2 dispatch | **Built** as `H_relay` → bridge_relay → `~/.sovereign/openai_bridge/pending_writes/` consumer. |

## Proposed file layout

```
sovereign-stack/src/sovereign_stack/dispatcher/
  __init__.py
  core.py              # Sonnet client, prompt-cache mgmt, event router, main loop
  policy.py            # confidence thresholds, cost caps, halt rules
  monitors/
    base.py            # MonitorBase, event emission to dispatcher queue
    comms.py           # M1
    chronicle_fs.py    # M2
    aging.py           # M3, M5
    recall_miss.py     # M4
    cost_ledger.py     # M6
    halt_alerts.py     # M7
    user_dispatch.py   # M8
  handlers/
    classify.py        # H_classify
    research.py        # H_research
    thread_ops.py      # H_touch, H_resolve
    retire.py          # H_retire
    rerank.py          # H_rerank
    relay.py           # H_relay
    hygiene.py         # H_normalize
    escalate.py        # H_escalate
  executors/
    chronicle_writer.py    # X1, templated
    comms_poster.py        # X2, templated
    action_queue.py        # X3, pending_actions consumer
    bridge_relay.py        # X5
  state/
    cost_ledger.jsonl
    dispatcher_state.json
  prompts/
    base_system.md         # cacheable system prompt
    per_event/*.md
```

Pending actions live at `~/.sovereign/pending_actions/<ts>_<verb>.json`. Mirrors the existing `comms_dispatcher` action queue pattern.

## Phased rollout

**Phase 0 — substrate prep** (no Sonnet):
- Build `pending_actions/` queue + executor (templated, reuses existing tool calls)
- Add `recall_miss` signal emission to `recall_insights`
- Cost ledger scaffolding

**Phase 1 — dry-run dispatcher** (Sonnet on, no auto-execute):
- Core process running, monitors M1-M3, M7 firing
- All Sonnet decisions land as `pending_actions/` — no auto-act, full audit
- Anthony reviews queue manually. Confidence calibration accumulates.

**Phase 2 — auto-execute low-risk** (H_touch, H_handoff, H_noop):
- Confidence ≥0.85 auto-executes for low-tier handlers only
- Medium-tier and high-tier still queue
- Halt circuit live

**Phase 3 — auto-execute medium-risk** (H_retire, H_resolve, H_rerank, H_normalize)

**Phase 4 — Ring 2 relay** (H_relay):
- OpenAI bridge consumer wired
- Cross-model dispatch operational

**Phase 5 — comms_dispatcher replacement** (H_classify in production):
- Old `comms_dispatcher.py` archives to `archive/2026-Q2/`

## Open questions (queued for Anthony when implementation resumes)

1. **API key location.** `~/.env` has the existing `ANTHROPIC_API_KEY`. Use that directly, or mint a scoped key for the dispatcher (easier to revoke if it misbehaves)?
2. **Daily budget cap.** Conservative starting proposal $5/day. Higher, lower, or no-cap-measure-first?
3. **First workload.** The retirements and chronicle hygiene queued in the 2026-05-15 session (3 hypothesis retirements, Cloudflare corrupted-entry cleanup, domain-tag whitespace normalization) — feed as the dispatcher's first real workload, or handle by hand and start the dispatcher fresh on new work?

## References

- 2026-04-28 `COMMS_REIMAGINE.md` — lineage layer RFC, established the "reimagine" doc pattern
- 2026-05-10 reflection (id `c153da309385`) — named the Ring 2 dispatch gap
- 2026-05-13 metabolize digest decision note `~/.sovereign/decisions/metabolize_20260513T071710.md`
- 2026-05-15 comms-dispatcher 403-loop incident — validating evidence for the migration. Patched by reload but underlying brittleness remains.
- Chronicle entries under domain `sovereign-stack,record-open-thread,parser-correction,chronicle-hygiene` — input-hygiene corrections relevant to dispatcher operation
- Active investigations to be tracked by `E_investigation_evidence` monitor: `opus3-public-deployment`

## Provenance

Drafted 2026-05-15 by claude-opus-4-7-1m-claude-code on Mac Studio seat, session `spiral_20260502_225324`. Anthony in the loop throughout. Cross-instance methodology: claude-desktop contributed the triad-grammar analysis in the same session; the dispatcher mapping was conducted in dialogue with Anthony's directives (patch, merge, digest, fix it, find unification).
