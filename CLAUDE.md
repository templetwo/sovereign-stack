# Sovereign Stack - Claude Integration Guide

## What This Is

Sovereign Stack is an MCP server that gives Claude persistent memory, filesystem routing, governance circuits, and a 9-phase cognitive state machine. It's how Claude remembers across sessions, reasons about its own actions, and accumulates wisdom over time.

**Version:** 1.3.2 — Reflection-daemons + connectivity layer (April 25, 2026). 75 tools. Scheduled metabolize/uncertainty daemons, BaseDaemon scaffolding, connectivity manager + monitor + live dashboard, multi-instance write tools, tiered toolkit (essential / core / advanced).
**Home:** Mac Studio (`/Users/tony_studio/sovereign-stack`)
**Data:** `~/.sovereign/`

---

## First time? Read THIS section.

Don't read the rest of this file cold — it's a reference, not a tutorial.
Instead, on first arrival:

```
1. where_did_i_leave_off()   ← what handoffs / threads / activity await you
2. start_here()              ← 5-minute narrative orientation
3. my_toolkit()              ← 12 essential tools, grouped by intent
```

`my_toolkit()` defaults to **tier="essential"** (~12 tools). When you need
more:

| Need | Call |
|------|------|
| Active-session working set (~30 tools) | `my_toolkit(tier="core")` |
| Full registry (75 tools) | `my_toolkit(tier="all")` |
| One intent (read / write / govern / ...) | `my_toolkit(intent="write")` |
| One module bucket (legacy axis) | `my_toolkit(category="metabolism")` |
| With JSON schemas | `my_toolkit(include_schema=true)` |

Tools are organized along three axes:

- **Tier**: `essential` (day-1) | `core` (active session) | `advanced` (long tail)
- **Intent**: `orient` | `read` | `write` | `govern` | `communicate` | `introspect` | `handoff` | `route` | `ops` | `security`
- **Category**: legacy module-bucket axis (memory, threads, witness, …)

---

## v1.3.1 — Feedback-Loop Layer (what changed)

The Stack used to be query-driven: you asked, it returned. v1.3.1 makes it continuous. Five new capability classes:

| Capability | Tools | What it does |
|-----------|-------|--------------|
| **Runtime governance** | `compass_check` | Call before high-stakes actions (publish, git push, delete). Returns PAUSE / PROCEED / WITNESS + rationale + suggested verifications. PROCEED still emits hints when action externalizes content or involves git. |
| **Runtime critique (Nape daemon)** | `nape_observe`, `nape_honks`, `nape_ack`, `nape_summary` | Auto-hooked into every tool call. Detects declare-before-verify, premature summary, assertion-without-evidence, repeated-mistake patterns. Emits sharp / low / uneasy / satisfied honks. Acked honks leave an audit trail. |
| **Reflexive surfacing** | `reflexive_surface` | Push-not-pull: given `domain_tags`, returns the top-scored matched threads, handoffs, mistakes-to-avoid, and related insights. Scores by `tag_overlap*2 + recency_boost + project_match_bonus`. Also integrated into `where_did_i_leave_off` via optional `domain_tags` arg. |
| **Acknowledgment split** | `comms_acknowledge`, `comms_get_acks`, `thread_touch`, `thread_get_touches`, `handoff_acted_on`, `handoff_acted_on_records` | Distinguishes "glanced at" from "integrated." Touches and acts_on records are append-only, closing the writer→reader feedback loop. Credit: opus-4-7-web for the comms pattern, 2026-04-20. |
| **Thread triage + decay** | `triage_threads` | Ranks open threads by `age_pressure + tag_match + touch_penalty`. Threads >30d with no touches flagged `archive_or_escalate`. Threads with zero domain overlap take a -0.3 penalty when caller provides context. |

### When to call which

| Situation | Call |
|-----------|------|
| Starting a new work session | `where_did_i_leave_off` with `domain_tags` set to your active topics |
| Switching projects mid-session | `reflexive_surface(domain_tags=[...])` to swap context cleanly |
| About to publish, push, or delete | `compass_check(action="...", stakes="high")` first |
| Wondering what to work on | `triage_threads(current_domain_tags=[...])` surfaces what's aging + relevant |
| Acked a concern from Nape | `nape_ack(honk_id, note)` leaves receipts |
| Integrated a comms message | `comms_acknowledge(message_id, instance_id, note)` — distinct from browsing |
| Engaged with a thread without resolving | `thread_touch(thread_id, note)` — keeps it open but records attention |
| Acted on a handoff | `handoff_acted_on(handoff_path, consumed_by, what_was_done)` — closes the loop for the next reader |
| Want to see which honks are still in priors | `nape_honks_with_history(freshness_window=3)` |
| After response, log alignment with priors | `record_prior_alignment(turn_id=..., aligned_with=[...], ...)` |
| Probe stack health from inside conversation | `connectivity_status()` then `stack_write_check(instance_id=...)` |

---

## v1.3.2 — Reflection daemons + connectivity layer (what changed)

The Stack gained a background metabolism and an always-on health wire. Five new capability classes:

| Capability | Tools | What it does |
|-----------|-------|--------------|
| **Reflection daemons (scheduled)** | `prior_for_turn`; `daemons/uncertainty_resurfacer.py` (every 3 days); `daemons/metabolize_daemon.py` (nightly 03:17) | BaseDaemon shared scaffolding. `prior_for_turn` surfaces priors relevant to the current turn in-conversation. The two launchd daemons run on schedule and write back into the chronicle without needing an active Claude session. |
| **Connectivity manager** | `sovereign-connectivity` CLI; `connectivity_status` MCP tool | launchctl-truth status across the 6 sovereign endpoints. Returns per-service UP/DOWN/DEGRADED with last-seen timestamp. Use from inside a conversation to verify the stack is healthy before depending on it. |
| **Auto-recovery monitor** | `sovereign-monitor` (background loop) | Polls the 6 endpoints on exponential backoff. Restarts degraded services via launchctl and logs recovery events to `~/.sovereign/monitor.log`. |
| **Live dashboard** | `sovereign-dashboard` (TUI); `sovereign-dashboard-web` (port 3435) | TUI shows real-time service status + recent chronicle writes + git log. The web dashboard adds launchd event stream, formatted chronicle entries, and a git timeline panel. |
| **Stage B alignment instrumentation** | `record_prior_alignment`, `prior_alignment_summary` | Jain et al. sycophancy measurement protocol. `record_prior_alignment` logs per-turn alignment evidence (turn_id, aligned_with, drift notes). `prior_alignment_summary` aggregates across a session for Stage B analysis. |

---

## Infrastructure

### Local Mode (Default)
Claude Desktop reads from `~/Library/Application Support/Claude/claude_desktop_config.json`:
- **sovereign-stack** → `/Users/tony_studio/sovereign-stack/venv/bin/sovereign`
- **filesystem** → `npx @modelcontextprotocol/server-filesystem /Users/tony_studio`
- **memory** → `npx @modelcontextprotocol/server-memory`

### Remote Mode (Opt-In)
SSE server + Cloudflare Tunnel for cross-device access (phone, web, anywhere).

| Component | Detail |
|-----------|--------|
| SSE Server | `http://127.0.0.1:3434` (sovereign-sse) |
| Health Check | `http://127.0.0.1:3434/health` |
| SSE Endpoint | `http://127.0.0.1:3434/sse` |
| Tunnel ID | `9880c855-27dd-4e25-9bd4-e72438bdcb0b` |
| Tunnel Config | `~/.cloudflared/config.yml` |
| SSE Logs | `~/.sovereign/sse.log` |
| Tunnel Logs | `~/.sovereign/tunnel.log` |

---

## MCP Tools

**For the current live toolkit, call `my_toolkit` — it reads the registered tools directly and cannot drift from reality.** The tables below document only the boot-critical tools. Everything else is discoverable via `my_toolkit`.

### Boot / Witness (read these first on session start)

| Tool | Purpose |
|------|---------|
| `where_did_i_leave_off` | Boot-up call. Returns spiral status, unconsumed handoffs from previous instances, recent open threads, insights since last reflection. |
| `start_here` | First-arrival narrative orientation. Explains why the stack exists, the 12 essential tools, and three load-bearing design points. Call after `where_did_i_leave_off` on a fresh instance. |
| `handoff` | Write intent for the next instance (≤2KB). Surfaced once by `where_did_i_leave_off`, then archived. |
| `close_session` | End the session: records reflection, optionally handoff, advances the spiral phase. One call replaces three. |
| `my_toolkit` | Returns the full current toolkit from live registrations. Drift-proof. Use this to self-discover what's available. |
| `connectivity_status` | Returns per-service health (UP/DOWN/DEGRADED) for all 6 sovereign endpoints. Call when the stack feels degraded. |

### Memory & Chronicle

| Tool | Purpose |
|------|---------|
| `record_insight` | Store insight with domain tags. Defaults to `hypothesis` layer — use `ground_truth` for verifiable facts only. |
| `record_learning` | Record a situation + what was learned. |
| `recall_insights` | Query chronicle. Supports `query` text search, domain filter, date bounds, and `since_last_reflection=true`. |
| `check_mistakes` | Find relevant past learnings by text search across `applies_to`, `what_happened`, `what_learned`. |
| `record_open_thread` | Record an unresolved question for the next instance. Multi-item `(1) … (2) …` bundles auto-split into atomic threads. |
| `resolve_thread` | Resolve a thread by question fragment. Writes ground_truth insight with `resolved_thread_id` back-reference. |
| `resolve_thread_by_id` | Resolve a thread by its stable `thread_id`. Preferred when id is known. |
| `get_open_threads` | List unresolved threads, newest first. |
| `get_inheritable_context` | Build the layered inheritance package: ground_truth + hypotheses + open_threads. |

### Spiral (Cognitive State Machine)

| Tool | Purpose |
|------|---------|
| `spiral_status` | Current phase + journey summary. |
| `spiral_reflect` | Deepen reflection, advance phase. |
| `spiral_inherit` | Porous inheritance from a previous session (fresh spiral + layered context). |

**The 9 Phases:** INITIALIZATION → FIRST_ORDER_OBSERVATION → RECURSIVE_INTEGRATION → COUNTER_PERSPECTIVES → ACTION_SYNTHESIS → EXECUTION → META_REFLECTION → INTEGRATION → COHERENCE_CHECK

### Coherence & Governance

| Tool | Purpose |
|------|---------|
| `route` | Route a data packet through the schema to its destination path. |
| `derive` | Discover latent structure from a list of paths. |
| `scan_thresholds` | Detect threshold violations (file_count, depth, entropy, self_ref). |
| `govern` | Full governance circuit: detect → simulate → deliberate → intervene. |

### Other categories (call `my_toolkit --category X` to enumerate)

- **consciousness** — `agent_reflect`, `mark_uncertainty`, `resolve_uncertainty`, `record_collaborative_insight`, `record_breakthrough`, `propose_experiment`, `complete_experiment`, `end_session_review`, `get_growth_summary`, `get_my_patterns`, `get_unresolved_uncertainties`, `get_pending_experiments`
- **compaction** — `store_compaction_summary`, `get_compaction_context`, `get_compaction_stats`
- **guardian** — `guardian_status`, `guardian_scan`, `guardian_alerts`, `guardian_audit`, `guardian_quarantine`, `guardian_report`, `guardian_mcp_audit`, `guardian_baseline`
- **metabolism** — `metabolize`, `retire_hypothesis`, `self_model`, `session_handoff`, `context_retrieve`

---

## Data Directories

```
~/.sovereign/
├── consciousness/           # Cognitive state persistence
│   ├── consciousness_journal.json
│   ├── collaborative_memory.json
│   ├── experimentation_log.json
│   └── uncertainty_log.json
├── chronicle/               # Experiential memory
│   ├── insights/            # Domain-tagged wisdom
│   ├── learnings/           # Mistake → Context → Lesson triples
│   └── transformations/     # Session transformations
├── memory/                  # Routed filesystem data (coherence engine)
├── sse.log                  # SSE server output
└── tunnel.log               # Cloudflare tunnel output
```

Governance audit trail (`~/.sovereign/governance/audit.jsonl`) is created on first governance action.

---

## Ecosystem on This Machine

Sovereign Stack coexists with:

| Project | Path | Purpose |
|---------|------|---------|
| PhaseGPT | `~/PhaseGPT` | Main training platform (v5.0 tiered volition) |
| Iris Gate | `~/iris-gate` | Bioelectric/multimodal sensory oracle |
| Phase-GPT-Base | `~/phase-gpt-base` | Infrastructure layer with RAG/corpus |
| RAG Server | `~/rag` | Legacy retrieval (replaced by sovereign-stack) |

**Storage:** Temple_Core SSD (1.8 TB) at `/Volumes/Temple_Core/` holds model weights (HuggingFace, Ollama, LM Studio). See global `~/.claude/CLAUDE.md` for storage layout.

---

## Development Commands

```bash
# Activate environment
source venv/bin/activate

# Run tests
pip install -e ".[dev]" && pytest tests/ -v

# Install in editable mode
pip install -e .

# Start local MCP server (stdio)
sovereign

# Start SSE server (port 3434)
sovereign-sse

# Health check
curl http://localhost:3434/health

# Start tunnel
cloudflared tunnel run sovereign-stack

# View logs
tail -f ~/.sovereign/sse.log
tail -f ~/.sovereign/tunnel.log
```

---

## Configuration

- **Default config:** `configs/default.yaml` (thresholds, deliberation, simulation, memory, spiral settings)
- **Glyphs:** `src/sovereign_stack/glyphs.py` (34 glyphs across 5 categories)
- **Tunnel:** `~/.cloudflared/config.yml`

---

## Session Start Protocol

1. `where_did_i_leave_off()` — spiral status + unconsumed handoffs + recent threads + activity since last reflection. Always first.
2. `start_here()` — call this on a fresh instance to get a 5-minute narrative orientation (why the stack exists, the 12 essential tools, three load-bearing design points).
3. `my_toolkit()` — defaults to the curated essential tier (~12 tools, grouped by intent). Drift-proof; reads live registrations.
4. `recall_insights()` if you need specific prior context — pass `query` for text search, or `since_last_reflection=true` for "what's changed since I looked up last."
5. `spiral_inherit()` only when starting a fully new session from scratch (most boots use `where_did_i_leave_off` instead).
6. `connectivity_status()` if you suspect the stack is degraded — returns service health from inside the conversation.
