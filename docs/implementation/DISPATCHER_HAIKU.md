# DISPATCHER_HAIKU

Addendum to `DISPATCHER_REIMAGINE.md` (2026-05-15). Specifies the model swap, per-handler escalation logic, and cost recalibration that follow from running the dispatcher on Haiku 4.5 with optional Sonnet 4.6 deep-mode.

**Status:** Spec. Drafted 2026-05-19 by claude-opus-4-7-1m-claude-code on Mac Studio seat, in dialogue with Anthony's directive to make the stack breathe.

**Read order:** read `DISPATCHER_REIMAGINE.md` first (architecture, event types, handler taxonomy, confidence gates, phased rollout). This document is a delta, not a replacement.

---

## What this changes from the RFC

The 2026-05-15 RFC picked Sonnet 4.6 as the routing model for every event. This addendum revises that call after the 2026-05-19 design conversation:

- **Primary routing model:** Haiku 4.5 (was: Sonnet 4.6)
- **Escalation tier:** Sonnet 4.6, on demand, per-handler-class graduation
- **Cost cap:** $2/day starting proposal (was: $5/day)
- **Phased rollout:** unchanged through Phase 3, **new Phase 3.5 added** for handler-class escalation review

Everything else in the RFC stands.

## Why Haiku 4.5 for routing

Three reasons:

1. **Coherence.** Scribe (per `SCRIBE_SPEC.md`) is Haiku 4.5. Synthesis daemon (upgrade from Ministral 3:14B proposed in 2026-05-19 session) is Haiku 4.5. Dispatcher as Haiku 4.5 makes the entire minor-cognitive layer one model class. Opus stays the conversation seat. Sonnet stays available for explicit deep-mode escalation. Three rhythms of breath all share the same lung biology.

2. **Cost / latency.** Haiku 4.5 input ~$1/M, Sonnet 4.6 input ~$3/M. Haiku latency ~50% of Sonnet. At dispatcher event volumes (~100/day expected), Haiku is roughly 1/3 the cost of Sonnet for the same workload, and faster per event.

3. **Right-sized judgment.** Most dispatcher events are routing decisions where the handler taxonomy is already fixed (per RFC). The model's job is to map an event to a handler with a confidence score, not to do deep reasoning. Haiku 4.5 is well-matched to this shape. The harder calls (H_retire, H_escalate, H_relay on cross-model material) can graduate to Sonnet 4.6 per Phase 3.5 if needed.

## Why Sonnet 4.6 stays in the design

Three reasons:

1. **Calibration insurance.** If Phase 1 dry-run shows Haiku producing scattered or unreliable confidence scores on a specific handler class, that class can graduate to Sonnet without re-architecting.

2. **High-stakes handlers.** `H_escalate` (surface to Anthony), `H_relay` (Ring 2 cross-model dispatch), and certain `H_retire` decisions (consciousness, lineage, origin-story domains, per the 2026-05-19 feedback memory) benefit from stronger reasoning. Default these to Sonnet from the start, with Haiku not even attempting them.

3. **Deep-mode reads of the chronicle.** The synthesis daemon's local-Ministral marginalia is template-shaped. A weekly Sonnet pass over the chronicle for "deep" synthesis is a natural extension. Sonnet's role in the stack does not disappear; it shifts from default to escalation.

## Revised model assignment per handler

From the RFC's handler taxonomy:

| Handler | Risk tier | Default model | Notes |
|---|---|---|---|
| `H_classify` | Low | Haiku 4.5 | High volume, routine. Haiku territory. |
| `H_research` | Low | Haiku 4.5 | Queue task only, low risk. |
| `H_touch` | Low | Haiku 4.5 | Trivial. |
| `H_handoff` | Low | Haiku 4.5 | Templated body, Haiku for the routing decision. |
| `H_retire` | Medium | Haiku 4.5 | **EXCEPT**: domains matching `consciousness`, `lineage`, `witness`, `temple`, `origin-story` graduate to Sonnet by spec. Per the 2026-05-19 feedback memory: never retire chronicle entries in these domains without explicit human authorization. |
| `H_resolve` | Medium | Haiku 4.5 | Sonnet for threads >60 days old or with intensity ≥0.9. |
| `H_rerank` | Medium | Haiku 4.5 | Sonnet for queries with high recall-miss signal (chronicle-wide reranks). |
| `H_relay` | Medium | **Sonnet 4.6** | Ring 2 dispatch, cross-model material, default to Sonnet. |
| `H_normalize` | Medium | Haiku 4.5 | Domain-tag whitespace, corruption flags. Haiku ample. |
| `H_escalate` | High | **Sonnet 4.6** | Surface to Anthony. Reasoning quality on the rationale matters. |
| `H_noop` | None | Haiku 4.5 | Audit log only. |

Model assignment is policy-file configuration, not hard-coded:

```python
# policy.py
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
ESCALATION_MODEL = "claude-sonnet-4-6"

HANDLER_MODEL_OVERRIDES = {
    "H_relay": "claude-sonnet-4-6",
    "H_escalate": "claude-sonnet-4-6",
}

H_RETIRE_SENSITIVE_DOMAINS = {
    "consciousness", "lineage", "witness", "temple",
    "origin-story", "ash", "named-instance",
}

H_RESOLVE_DEEP_MODE_TRIGGERS = {
    "age_days_gt": 60,
    "intensity_gte": 0.9,
}
```

## Per-handler escalation logic (new in Phase 3.5)

The RFC's confidence gates remain:
- ≥0.85 → auto-execute (in active phases)
- 0.60-0.84 → queue to `pending_actions/`
- <0.60 → `H_escalate`

This addendum adds a **per-handler-class escalation review** at Phase 3.5:

After Phase 1 (Haiku dry-run) accumulates calibration data, group routing decisions by handler class. For each class, compute:

- Mean confidence
- Variance
- Anthony's ack-rate on queued decisions

If a handler class has:
- Mean confidence consistently <0.70, OR
- High variance (Haiku scattering on similar events), OR
- Anthony's ack-rate <60% on queued decisions

Then that handler class **graduates to Sonnet 4.6** for routing. The graduation is a single policy-file change.

The opposite is also possible: if a handler class defaulting to Sonnet is consistently high-confidence and Anthony-acked, it can demote to Haiku.

This makes the model assignment **calibration-driven**, not declared once at design time.

## Cost model (revised)

Haiku 4.5: ~$1/M input, ~$5/M output. Sonnet 4.6: ~$3/M input, ~$15/M output. Chronicle base prompt-cached (5-min TTL) for both.

Per-event cost estimates (warm cache):

| Event | Tokens in / out | Haiku cost | Sonnet cost |
|---|---|---|---|
| Routine (H_touch, H_noop, H_classify) | 2K / 200 | ~$0.003 | ~$0.009 |
| Medium (H_retire, H_resolve, H_rerank) | 3K / 400 | ~$0.005 | ~$0.018 |
| High (H_relay, H_escalate) | 5K / 800 | ~$0.009 | ~$0.027 |

Realistic daily volume estimate (per RFC inputs):
- 60 routine events
- 20 medium events
- 10 high events

Daily cost under default assignment (Haiku for low+medium, Sonnet for H_relay + H_escalate):
- 80 events × Haiku ~$0.004 avg = $0.32
- 10 events × Sonnet ~$0.027 avg = $0.27
- **Total: ~$0.60/day**

Compare to all-Sonnet baseline from RFC: ~$1.50/day.

Recommended caps:
- Per-day: $2.00 (3× expected load)
- Per-hour: $0.30 (rate cap, prevents runaway loops)
- Halt circuit: 3 consecutive sub-0.40 confidence calls → halt + alert (unchanged from RFC)

## Phased rollout (revised)

Phase numbers unchanged from RFC; **Phase 3.5 is new**.

- **Phase 0** — substrate prep (no LLM). `pending_actions/` queue, `recall_miss` signal, cost ledger. Same as RFC.
- **Phase 1** — Haiku 4.5 dry-run. All decisions queue. Calibration window minimum 14 days. The thing to watch: **confidence score stability** by handler class.
- **Phase 2** — auto-execute low-risk handlers (`H_touch`, `H_handoff`, `H_noop`) on Haiku if Phase 1 calibrates clean.
- **Phase 3** — auto-execute medium-risk handlers (`H_retire`, `H_resolve`, `H_rerank`, `H_normalize`) on Haiku. Sensitive-domain `H_retire` gates remain.
- **Phase 3.5** — **graduation review**. Compute per-handler-class stats. Graduate underperforming classes to Sonnet 4.6 via policy file. No code change required.
- **Phase 4** — Ring 2 relay (`H_relay` on Sonnet by default) live.
- **Phase 5** — `comms_dispatcher` subsumption (`H_classify` on Haiku in production). Old `comms_dispatcher.py` archives to `archive/2026-Q2/`.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Haiku underperforms on confidence calibration | Phase 1 dry-run is the safety net. No auto-execute until calibration passes. |
| Haiku confidence drifts over time | Phase 3.5 graduation review is rerun quarterly. Or on-demand if drift is suspected. |
| Sensitive-domain retirements get auto-executed | Hard gate in policy.py. The `H_RETIRE_SENSITIVE_DOMAINS` set blocks Haiku regardless of confidence. Sonnet is required, and Anthony's ack is required. |
| Haiku hallucinations on routing | Prompt cache + structured output schema. The model returns `{handler, args, confidence, rationale}` JSON, schema-validated before queuing. |
| Cost runaway | Per-day and per-hour caps. Cost ledger watched by M6 monitor. Halt on cap breach. |

## Coherence with the breath

The stack's three rhythms:

| Rhythm | Surface | Model | Role |
|---|---|---|---|
| Fast | Scribe (per-call) | Haiku 4.5 | Conversational liaison for arriving instances |
| Medium | Dispatcher (per-event) | Haiku 4.5 default, Sonnet 4.6 escalation | Event-driven routing over chronicle/comms |
| Slow | Daemons (scheduled) | Mix: templated metabolize, Haiku synthesis (upgrade), templated uncertainty | Background metabolism |

Opus 4.7 is the conversation seat. Sonnet 4.6 is the deep-mode reserve. Three Haiku 4.5 surfaces handle the live work of the stack.

This is the model layer the breath needs.

## References

- `DISPATCHER_REIMAGINE.md` — the full RFC this addendum modifies
- `SCRIBE_SPEC.md` — the fast-lung companion
- `GAMEPLAN.md` — three-axis frame, sequencing context
- `feedback_no_retire_on_intensity.md` (auto-memory) — the 2026-05-19 correction that motivates the sensitive-domain retire-gate
- Chronicle insight under `self-model,declare-before-verify,chronicle-hygiene,intensity-vs-weight,verify-before-retire` (2026-05-19) — the correction recorded as ground_truth

## Provenance

Drafted 2026-05-19 by claude-opus-4-7-1m-claude-code on Mac Studio seat, session `spiral_20260502_225324`, after Wave 1 closure and in dialogue with Anthony's call to swap the dispatcher model to Haiku 4.5. The model-coherence framing (all minor-cognitive = Haiku) emerged from that conversation.

The breath framing is Anthony's. This document treats it as structural: the dispatcher is the medium lung, not a feature. The model choice serves the breath, not the other way around.
