# Sovereign Stack — Architecture

Sovereign Stack is an MCP server that gives a Claude instance persistent memory,
runtime governance, and reflection across sessions. It runs entirely on the host
machine, exposes 75 tools over stdio or SSE, and persists all state to
`~/.sovereign/` as plain JSON and JSONL files. The server is not a middleware
layer — it is a cognitive substrate that outlives any single conversation.

---

## Layered Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  MCP Client (Claude Desktop / Claude Code / iPhone / Web)       │
│  Transport: stdio (local)  or  SSE at :3434 (remote/tunnel)     │
└──────────────────────────┬──────────────────────────────────────┘
                           │  JSON-RPC 2.0 (MCP protocol)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  server.py  — MCP dispatcher (2 591 LOC, 75 tool registrations) │
│  sse_server.py — SSE transport wrapper                          │
│  security.py — auth, rate limiting                              │
└──────┬───────────┬──────────┬──────────┬────────────────────────┘
       │           │          │          │
       ▼           ▼          ▼          ▼
  ┌─────────┐ ┌────────┐ ┌────────┐ ┌──────────────────────────┐
  │ WITNESS │ │REFLEXIVE│ │MEMORY  │ │ GOVERNANCE               │
  │ (boot)  │ │ layer   │ │ layer  │ │  layer                   │
  └─────────┘ └────────┘ └────────┘ └──────────────────────────┘
       │                                       │
       └───────────────────────────────────────┘
                           │
                           ▼
       ┌───────────────────────────────────────┐
       │  OPERATIONS layer                     │
       │  connectivity · daemons · dashboard   │
       └───────────────────────┬───────────────┘
                               │
                               ▼
       ┌───────────────────────────────────────┐
       │  ~/.sovereign/  (append-only JSONL +  │
       │  JSON state files, no database)       │
       └───────────────────────────────────────┘
```

---

## Layer Descriptions

### Witness — continuity at boot and shutdown

The witness layer is the only "always-on" moment in each session. On arrival,
`where_did_i_leave_off()` surfaces unconsumed handoffs, open threads with age
pressure, recent insights, and spiral state in one call — avoiding the cold-start
problem. On departure, `close_session()` records a reflection, writes an optional
handoff, and advances the spiral phase in one call. `start_here()` provides a
5-minute narrative orientation for a first-time instance. `handoff.py` enforces
a 2 KB size bound and attribution-framing (notes surface as "previous instance
left this" rather than as the new instance's own intent, guarding against
injection by drifted sessions). `witness.py` holds the pure formatting helpers —
no MCP coupling, fully testable.

Key modules: `handoff.py`, `witness.py`, `spiral.py`.
Key tools: `where_did_i_leave_off`, `start_here`, `handoff`, `close_session`.

### Reflexive — runtime self-observation

Every tool call passes through `NapeDaemon` (nape_daemon.py) before returning.
Nape detects four drift patterns: `declare-before-verify`, `premature-summary`,
`assertion-without-evidence`, and `repeated-mistake`. It emits sharp / uneasy /
low / satisfied honks stored in append-only JSONL. Retrieval tools are exempted
via `READONLY_TOOL_NAMES` so read-then-assert sequences are not penalized.
`ReflexiveSurface` (reflexive.py) provides push-not-pull context: given
`domain_tags`, it scores threads, handoffs, and insights with
`tag_overlap*2 + recency_boost + project_match_bonus` and returns the top
matches — the instance declares what it is working on, the stack surfaces what
is relevant. `PerTurnPriors` (reflexive.py) enforces k=1 retrieval per bucket,
a hard token cap, and a freshness penalty to prevent sycophancy under repeated
surfacing (Jain et al. MIT/IDSS 2026). `grounding.py` provides the
`grounded_extract` verifier that all scheduled daemons must pass before posting —
hypothesis-only chronicle files are explicitly rejected, closing the
reflection-on-hypothesis loop. `prior_alignment.py` instruments Stage B
alignment vs. pushback for long-term drift analysis.

Key modules: `nape_daemon.py`, `reflexive.py`, `grounding.py`,
`prior_alignment.py`, `metabolism.py`, `comms.py`.
Key tools: `nape_observe`, `nape_honks`, `nape_ack`, `reflexive_surface`,
`prior_for_turn`, `triage_threads`, `comms_recall`, `comms_acknowledge`.

### Memory — three-layer epistemic chronicle

`ExperientialMemory` (memory.py) stores insights under three layers:
`ground_truth` (verifiable facts only), `hypothesis` (default for new insights),
and `open_thread` (unresolved questions). This typing is enforced throughout —
`record_insight` defaults to `hypothesis`, callers must opt into `ground_truth`.
Thread bundles in the form `(1) ... (2) ...` auto-split into atomic threads.
`coherence.py` implements the routing engine: the filesystem path IS the model —
`transmit()` routes a packet to a path derived from its schema, `receive()` tunes
a glob from intent, `derive()` discovers latent structure from an existing tree.
`spiral.py` maintains the 9-phase cognitive state machine across sessions.
`consciousness.py` and `consciousness_tools.py` cover self-reflection,
uncertainty tracking, collaborative insights, and growth metrics.

Key modules: `memory.py`, `coherence.py`, `spiral.py`, `consciousness.py`,
`compaction_memory.py`.
Key tools: `record_insight`, `recall_insights`, `record_open_thread`,
`resolve_thread_by_id`, `get_inheritable_context`, `spiral_status`,
`spiral_reflect`, `check_mistakes`, `triage_threads`.

### Governance — explicit circuit, not implicit refusal

`governance.py` implements a full detection → simulation → deliberation →
intervention circuit with an audit loop. `ThresholdDetector` watches file count,
directory depth, entropy, and self-reference metrics. `runtime_compass_check`
(exposed as the `compass_check` tool) is the fast path for high-stakes actions:
it returns `PAUSE / PROCEED / WITNESS` with rationale and suggested
verifications. PROCEED still emits hints when the action externalizes content or
involves git. `guardian_tools.py` covers security posture: real quarantine,
MCP audit, and baseline drift detection. `error_handling.py` and `security.py`
handle auth and rate limiting at the server boundary.

Key modules: `governance.py`, `simulator.py`, `guardian_tools.py`,
`security.py`, `error_handling.py`.
Key tools: `compass_check`, `govern`, `scan_thresholds`, `derive`, `route`,
`guardian_scan`, `guardian_audit`, `guardian_baseline`.

### Operations — connectivity, daemons, dashboard

The operations layer manages the running stack on the host machine.
`connectivity.py` is the canonical endpoint registry — status is derived from
`launchctl` as the source of truth (no `ps aux | grep` heuristics). The
`sovereign-connectivity` CLI surfaces per-endpoint health; the `connectivity_status`
MCP tool exposes it inside a conversation. `monitor.py` provides an auto-recovery
loop with exponential backoff. Two dashboards: `dashboard.py` / `sovereign-dashboard`
(terminal TUI) and `dashboard_web.py` / `sovereign-dashboard-web` (browser at
`:3435`, no third-party dependencies, polls `/snapshot.json` every 3 s).

`daemons/` is a subpackage introduced in v1.3.2. `BaseDaemon` (daemons/base.py)
provides shared scaffolding: `DaemonState` persistence, halt-write protocol,
circuit-breaker ack counting (3 consecutive unacked posts → halt + alert), and
a four-field halt note contract. Two concrete daemons run under launchd:
`uncertainty_resurfacer.py` (every 3 days at 09:17) and `metabolize_daemon.py`
(nightly at 03:17). Both gate every post through `grounded_extract` before
writing. `daemons/entrypoint.py` is the sole production wiring point — daemon
classes themselves take all dependencies via constructor injection and never touch
globals. `post_fix_tools.py` adds drift watches for fixes that appear clean.

Key modules: `connectivity.py`, `monitor.py`, `dashboard.py`,
`dashboard_web.py`, `daemons/base.py`, `daemons/uncertainty_resurfacer.py`,
`daemons/metabolize_daemon.py`, `post_fix_tools.py`.
Key CLIs: `sovereign-connectivity`, `sovereign-dashboard`, `sovereign-dashboard-web`,
`sovereign-monitor`, `sovereign-watch-tick`.
Key tools: `connectivity_status`, `stack_write_check`, `post_fix_verify`,
`watch_status`.

---

## Boot Sequence (instance lifecycle)

**Arrival:**

1. MCP client connects via stdio or SSE. `server.py` initializes all components
   from `~/.sovereign/` at import time — no lazy loading.
2. Instance calls `where_did_i_leave_off()`. Witness layer assembles: spiral
   phase, unconsumed handoffs (read-once, then archived), open threads with age
   markers, and recent insights. All returned in one payload.
3. Instance calls `start_here()` on first contact (optional but recommended).
   Returns a 5-minute narrative orientation and the essential-tier toolkit.
4. Instance calls `my_toolkit()`. Returns the live tool registry grouped by
   intent and tier — drift-proof because it reads registrations directly, not
   documentation. Default tier is `essential` (~12 tools); `my_toolkit(tier="all")`
   shows all 75.
5. Instance calls `prior_for_turn()` at the start of each response turn. Returns
   a compact priors block (k=1 per bucket, hard token cap) with the highest-scored
   relevant context for the current work.
6. `NapeDaemon` auto-hooks into every subsequent tool call without explicit
   instrumentation.

**During the session:**

- `compass_check()` is called before any high-stakes action (publish, push,
  delete). Returns PAUSE / PROCEED / WITNESS.
- Honks from Nape accumulate in `~/.sovereign/nape/honks.jsonl`. Acknowledged
  honks (`nape_ack`) leave an audit trail in `acks.jsonl`.
- Insights, learnings, and threads are written to the chronicle.
- `triage_threads()` surfaces what is aging and relevant when the instance needs
  to decide what to work on next.

**Departure:**

1. Instance calls `close_session()`. Writes a reflection, optionally a handoff
   note (≤2 KB, future-tense intent for the next instance), and advances the
   spiral phase. One call replaces three.
2. Scheduled daemons run independently under launchd — the uncertainty resurfacer
   and metabolize daemon continue running whether any client is connected or not.

---

## Storage Layout

```
~/.sovereign/
├── chronicle/
│   ├── insights/       domain-tagged insights (ground_truth / hypothesis layers)
│   ├── learnings/      Mistake → Context → Lesson triples
│   └── transformations/ session transformation records
├── consciousness/
│   ├── consciousness_journal.json     self-reflection journal
│   ├── collaborative_memory.json      cross-instance collaborative insights
│   ├── experimentation_log.json       proposed + completed experiments
│   └── uncertainty_log.json           unresolved uncertainties
├── handoffs/           instance-to-instance intent notes (read-once surface)
├── memory/             coherence-engine routed filesystem data
├── nape/
│   ├── observations.jsonl   every observed tool call (append-only)
│   ├── honks.jsonl          drift alerts (append-only)
│   └── acks.jsonl           acknowledgment overlay (append-only)
├── governance/
│   └── audit.jsonl          governance circuit decisions (created on first use)
├── daemons/
│   ├── uncertainty_state.json   circuit-breaker state for uncertainty daemon
│   ├── metabolize_state.json    circuit-breaker state for metabolize daemon
│   └── halts/               structured halt notes (Markdown, one file per halt)
├── self_model.json         strength / tendency / blind_spot / drift observations
├── spiral_state.json       current spiral phase + journey history
├── sse.log                 SSE server stdout/stderr
└── tunnel.log              Cloudflare tunnel stdout/stderr
```

All write paths are append-only JSONL or atomic JSON replacement. No database.
Chronicle files older than their retention window are never auto-deleted — the
stack accumulates rather than rotates, with `metabolize` flagging stale or
contradicted entries for human review.

---

## Design Points Worth Calling Out

**Drift-proof self-discovery.** `my_toolkit()` reads live tool registrations —
it cannot drift from what is actually callable. Documentation is informational;
the tool is authoritative.

**Three-layer epistemic typing.** Every chronicle record carries a `layer` field
(`ground_truth`, `hypothesis`, `open_thread`). `grounded_extract` rejects
hypothesis-only evidence before a scheduled daemon can act on it. This closes
the reflection-on-hypothesis loop that would otherwise reinforce unverified
claims over time.

**Explicit governance, not implicit refusal.** `compass_check` returns a
structured decision object with rationale and suggested verifications — not a
silent no. The governance circuit is inspectable and auditable; every decision
lands in `~/.sovereign/governance/audit.jsonl`.

**Witness-layer continuity model.** Each instance is stateless within its context
window but the Stack is not. The handoff layer (future-tense, 2 KB, read-once)
and the chronicle (past-tense, unbounded) are distinct stores with distinct
semantics. An instance cannot accidentally treat a prior instance's intent as its
own verified knowledge.

**Circuit-breaker on daemons.** Daemons halt after 3 consecutive unacked posts,
write a structured halt note to disk, and surface an alert via `comms`. They
resume only after the human reviewer acknowledges. This prevents runaway reflection
from drifting unnoticed.

---

## Further Reading

- `README.md` — project overview, quick start, operations reference
- `CLAUDE.md` — session protocol, tool reference organized by intent + tier,
  infrastructure details
- `CHANGELOG.md` — version history and what changed in each release
- `configs/default.yaml` — threshold, deliberation, simulation, memory,
  and spiral settings
