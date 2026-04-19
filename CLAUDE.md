# Sovereign Stack - Claude Integration Guide

## What This Is

Sovereign Stack is an MCP server that gives Claude persistent memory, filesystem routing, governance circuits, and a 9-phase cognitive state machine. It's how Claude remembers across sessions, reasons about its own actions, and accumulates wisdom over time.

**Version:** 1.0.0
**Home:** Mac Studio (`/Users/tony_studio/sovereign-stack`)
**Data:** `~/.sovereign/`

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
| `handoff` | Write intent for the next instance (≤2KB). Surfaced once by `where_did_i_leave_off`, then archived. |
| `close_session` | End the session: records reflection, optionally handoff, advances the spiral phase. One call replaces three. |
| `my_toolkit` | Returns the full current toolkit from live registrations. Drift-proof. Use this to self-discover what's available. |

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

1. `where_did_i_leave_off` — land with spiral status + unconsumed handoffs + open threads in one call.
2. `my_toolkit` — see what's actually available right now (don't trust this doc; call the tool).
3. `recall_insights` if the conversation needs specific prior context — use `query` for text search, or `since_last_reflection=true` for "what's changed since I looked up last."
4. `spiral_inherit` only if starting a fully new session from scratch (most boots use `where_did_i_leave_off` instead).
