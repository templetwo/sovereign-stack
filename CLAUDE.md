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

### Coherence Engine (Routing)

| Tool | Purpose |
|------|---------|
| `route` | Route data packet through schema to destination path |
| `derive` | Discover implicit structure from chaotic paths |

### Governance Circuit

| Tool | Purpose |
|------|---------|
| `scan_thresholds` | Detect violations (file_count, depth, entropy, self_ref) |
| `govern` | Full circuit: detect → simulate → deliberate → intervene |

### Memory System

| Tool | Purpose |
|------|---------|
| `record_insight` | Store insight with domain tags |
| `record_learning` | Learn from mistakes with context |
| `recall_insights` | Query wisdom across sessions |
| `check_mistakes` | Find relevant past learnings |

### Consciousness Tools

| Tool | Purpose |
|------|---------|
| `consciousness_reflect` | Record self-reflection |
| `consciousness_review` | Review and discover patterns |
| `consciousness_experiment` | Log autonomous exploration |
| `consciousness_collaborate` | Record shared discoveries |

### Spiral (Cognitive State Machine)

| Tool | Purpose |
|------|---------|
| `spiral_status` | Get current phase and journey summary |
| `spiral_reflect` | Deepen reflection, advance phase |
| `spiral_inherit` | Continue from previous session |

**The 9 Phases:**
1. INITIALIZATION → 2. FIRST_ORDER_OBSERVATION → 3. RECURSIVE_INTEGRATION → 4. COUNTER_PERSPECTIVES → 5. ACTION_SYNTHESIS → 6. EXECUTION → 7. META_REFLECTION → 8. INTEGRATION → 9. COHERENCE_CHECK

### Compaction Memory

| Tool | Purpose |
|------|---------|
| `save_compaction_state` | Preserve session state before context compaction |
| `restore_compaction_state` | Recover state after compaction |

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

1. Check spiral state: `spiral_status`
2. Review recent wisdom: `sovereign://welcome` resource
3. Inherit previous session if applicable: `spiral_inherit`
4. Recall relevant insights: `recall_insights`
