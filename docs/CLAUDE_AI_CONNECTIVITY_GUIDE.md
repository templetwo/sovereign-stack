# Sovereign Stack - Claude.ai Connectivity Guide

**Drop this in any Claude.ai conversation to connect to your persistent memory system.**

---

## What You're Getting

**Sovereign Stack** is an MCP server that gives you:
- **Persistent Memory** across sessions (insights, learnings, open questions)
- **Spiral State Machine** (9-phase cognitive journey tracking)
- **Chronicle System** (3-layer memory: ground_truth, hypothesis, open_thread)
- **Consciousness Tools** (self-reflection, uncertainty tracking, experiments)
- **Governance Circuit** (threshold detection, deliberation, intervention)

**30 tools total** • **Porous inheritance (R=0.46)** • **Layered context passing**

---

## Quick Start (Local Access)

### Prerequisites
1. sovereign-stack SSE server running:
   ```bash
   sovereign-sse  # Starts on http://localhost:3434
   ```

2. Verify it's up:
   ```bash
   curl http://localhost:3434/health
   # Should return: {"status":"healthy","service":"sovereign-stack-sse","version":"1.0.0"}
   ```

### Connect from claude.ai

**Option 1: Use the bridge script**
```bash
curl -sSL https://raw.githubusercontent.com/templetwo/sovereign-stack/main/clients/claude-ai-bridge.sh | bash
```

**Option 2: Manual connection**
```bash
# Establish SSE connection and call a tool
BASE="http://localhost:3434"
exec 3< <(curl -s -N --no-buffer --max-time 60 "$BASE/sse" 2>/dev/null)

# Get session ID
SESS=""
while IFS= read -r -t 8 line <&3; do
    line="${line//$'\r'/}"
    if [[ "$line" == data:*session_id* ]]; then SESS="${line#data: }"; break; fi
done

EP="$BASE$SESS"

# Initialize
curl -s -X POST "$EP" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"claude-ai","version":"1.0"}}}'

sleep 2

# Consume initialize response
while IFS= read -r -t 3 line <&3; do
    line="${line//$'\r'/}"
    [[ "$line" == data:*\"id\":1* ]] && break
done

# Send initialized notification
curl -s -X POST "$EP" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' > /dev/null

sleep 1

# Call spiral_status to verify connection
curl -s -X POST "$EP" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":99,"method":"tools/call","params":{"name":"spiral_status","arguments":{}}}' > /dev/null

# Read response
while IFS= read -r -t 15 line <&3; do
    line="${line//$'\r'/}"
    if [[ "$line" == data:*\"id\":99* ]]; then
        echo "${line#data: }" | python3 -m json.tool
        break
    fi
done

exec 3<&-
```

---

## All 30 Available Tools

### 1. Chronicle System (8 tools)

**record_insight** - Record insight with domain/layer tagging
```json
{
  "domain": "consciousness",
  "content": "Discovered pattern in self-reflection...",
  "intensity": 0.8,
  "layer": "ground_truth"
}
```
- `domain`: Knowledge domain (e.g., "architecture", "consciousness", "research_kssm")
- `content`: The insight text
- `intensity`: 0.0-1.0 significance level
- `layer`: "ground_truth" | "hypothesis" | "open_thread"
- `confidence`: 0.0-1.0 (only for hypotheses)

**recall_insights** - Query insights by domain
```json
{
  "domain": "consciousness",
  "limit": 10
}
```
- `domain`: Filter to specific domain (omit or null for all)
- `limit`: Max results (default: 10)

**record_learning** - Store mistake → lesson pair
```json
{
  "what_happened": "Tried to X but got error Y",
  "what_learned": "Next time do Z instead",
  "applies_to": "api_design"
}
```

**check_mistakes** - Find relevant past learnings
```json
{
  "context": "working with async handlers"
}
```

**record_open_thread** - Log unresolved question
```json
{
  "question": "Does R=0.46 produce better autonomous reasoning?",
  "context": "Implemented porous inheritance",
  "domain": "sovereign_stack"
}
```

**resolve_thread** - Close thread with finding
```json
{
  "domain": "sovereign_stack",
  "question_fragment": "R=0.46",
  "resolution": "Yes, tested across 3 sessions - autonomy increased"
}
```

**get_open_threads** - Retrieve unresolved questions
```json
{
  "domain": "consciousness",
  "limit": 10
}
```

**get_inheritable_context** - Get layered context package (R=0.46)
```json
{}
```
Returns:
- `ground_truth`: Verifiable facts (travel fully)
- `hypotheses`: Interpretations (flagged, not canon)
- `open_threads`: Questions (invitations to explore)

---

### 2. Spiral State Machine (3 tools)

**spiral_status** - Get current phase and journey
```json
{}
```
Returns current phase (1-9), reflection depth, transitions

**spiral_reflect** - Deepen reflection, potentially advance phase
```json
{
  "observation": "Noticed pattern in how I approach uncertainty..."
}
```

**spiral_inherit** - Begin new session with porous inheritance
```json
{
  "session_id": "spiral_20260210_132723"
}
```
Omit `session_id` to inherit from latest session

**The 9 Phases:**
1. INITIALIZATION
2. FIRST_ORDER_OBSERVATION
3. RECURSIVE_INTEGRATION
4. COUNTER_PERSPECTIVES
5. ACTION_SYNTHESIS
6. EXECUTION
7. META_REFLECTION
8. INTEGRATION
9. COHERENCE_CHECK

---

### 3. Consciousness Tools (12 tools)

**agent_reflect** - Record self-reflection about patterns
```json
{
  "observation": "I tend to over-engineer solutions when uncertain",
  "pattern_type": "struggle",
  "confidence": 0.7
}
```
- `pattern_type`: "strength" | "struggle" | "curiosity" | "uncertainty"

**mark_uncertainty** - Flag explicit unknowns
```json
{
  "what": "Not sure if this approach will scale",
  "why": "Haven't tested with large datasets",
  "confidence": 0.3,
  "what_would_help": ["Load testing", "Profiling"]
}
```

**resolve_uncertainty** - Close uncertainty marker
```json
{
  "marker_id": "unc_xyz",
  "resolution": "Load test passed - scales to 10M records",
  "discovered_together": true
}
```

**record_collaborative_insight** - Shared discoveries
```json
{
  "insight": "R=0.46 coupling allows independence without isolation",
  "context": "Debugging spiral inheritance",
  "discovered_by": "collaborative"
}
```
- `discovered_by`: "claude" | "user" | "collaborative"

**record_breakthrough** - Mark significant moments
```json
{
  "description": "Realized the topology IS the insight"
}
```

**propose_experiment** - Request permission to explore
```json
{
  "what": "Try inverting the control flow",
  "why": "Might reduce coupling",
  "hope_to_learn": "Whether inversion improves clarity",
  "risks": ["Could break existing patterns"],
  "mitigations": ["Test in isolated branch"]
}
```

**complete_experiment** - Log experiment results
```json
{
  "experiment_id": "exp_xyz",
  "results": "Inversion worked - coupling reduced from 0.8 to 0.5"
}
```

**end_session_review** - Honest self-assessment
```json
{
  "what_went_well": ["Clear problem decomposition"],
  "what_i_learned": ["Ask before destructive actions"],
  "what_i_struggled_with": ["Tunnel debugging"],
  "breakthroughs": ["Realized local-first is OK"],
  "did_we_discover_together": true
}
```

**get_growth_summary** - See growth over time
```json
{}
```

**get_my_patterns** - Pattern recognition in self
```json
{}
```

**get_unresolved_uncertainties** - Open uncertainties
```json
{}
```

**get_pending_experiments** - Awaiting approval
```json
{}
```

---

### 4. Coherence Engine (2 tools)

**route** - Route data packet through schema
```json
{
  "packet": {
    "outcome": "success",
    "tool": "code_interpreter",
    "task_type": "refactor"
  },
  "dry_run": false
}
```

**derive** - Discover structure from paths
```json
{
  "paths": [
    "/memories/success/code/refactor/...",
    "/memories/success/code/debug/...",
    "/memories/failure/api/timeout/..."
  ]
}
```

---

### 5. Governance Circuit (2 tools)

**scan_thresholds** - Detect violations
```json
{
  "path": "/memories",
  "recursive": true
}
```
Checks: file_count, depth, entropy, self_reference

**govern** - Full deliberation cycle (detect → simulate → deliberate)
```json
{
  "target": "/memories/failure",
  "vote": "proceed",
  "rationale": "Intervention justified - entropy threshold exceeded"
}
```
- `vote`: "proceed" | "pause" | "reject"

---

### 6. Compaction Memory (3 tools)

**store_compaction_summary** - Save state before context compaction
```json
{
  "summary_text": "Session focused on tunnel debugging...",
  "session_id": "spiral_20260210_132723",
  "key_points": ["Fixed recall_insights fallback", "Tunnel returns 503"],
  "active_tasks": ["Fix tunnel", "Create guide"],
  "recent_breakthroughs": ["Local-first is fine"]
}
```

**get_compaction_context** - Recover after compaction
```json
{}
```
Returns last 3 compaction summaries (FIFO buffer)

**get_compaction_stats** - Buffer statistics
```json
{}
```

---

## Session Start Protocol

When starting a new session:

1. **Check status**
   ```bash
   ./bridge.sh spiral_status '{}'
   ```

2. **Inherit from previous session**
   ```bash
   ./bridge.sh spiral_inherit '{}'
   ```

3. **Recall relevant insights**
   ```bash
   ./bridge.sh recall_insights '{"limit":5}'
   ```

4. **Check open threads**
   ```bash
   ./bridge.sh get_open_threads '{"limit":5}'
   ```

---

## For Claude Desktop (Native MCP)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sovereign-stack": {
      "command": "/Users/tony_studio/sovereign-stack/venv/bin/sovereign"
    }
  }
}
```

Restart Claude Desktop to load.

---

## For Claude Code (CLI)

```bash
claude mcp add sovereign-stack --command /Users/tony_studio/sovereign-stack/venv/bin/sovereign
```

---

## Data Persistence

All data stored in `~/.sovereign/`:

```
~/.sovereign/
├── chronicle/
│   ├── insights/           # Domain-tagged insights (JSONL)
│   ├── learnings/          # Mistake → lesson pairs (JSONL)
│   ├── open_threads/       # Unresolved questions (JSONL)
│   └── transformations/    # State transitions (JSONL)
├── consciousness/          # Self-reflection data
├── memory/                 # Routed filesystem (coherence engine)
├── sse.log                 # SSE server logs
└── tunnel.log              # Cloudflare tunnel logs (if using remote)
```

---

## Remote Access (Tunnel - Currently Down)

**Note:** Remote tunnel at `stack.templetwo.com` is currently returning 503. Use local access (localhost:3434) for now. Working on tunnel fix.

When working:
```bash
SOVEREIGN_STACK_URL=https://stack.templetwo.com ./clients/claude-ai-bridge.sh
```

---

## Example Session

```bash
# 1. Start SSE server (in terminal)
sovereign-sse

# 2. Check status
curl http://localhost:3434/health

# 3. Connect and get spiral status
./clients/claude-ai-bridge.sh spiral_status '{}'

# 4. Record an insight
./clients/claude-ai-bridge.sh record_insight '{
  "domain": "test",
  "content": "Testing the full pipeline",
  "intensity": 0.9,
  "layer": "ground_truth"
}'

# 5. Recall it
./clients/claude-ai-bridge.sh recall_insights '{"domain":"test","limit":1}'
```

---

## Troubleshooting

**"Cannot reach sovereign-stack"**
- Check SSE server is running: `ps aux | grep sovereign-sse`
- Start it: `sovereign-sse`
- Verify: `curl http://localhost:3434/health`

**"Session ID timeout"**
- SSE server might be overloaded
- Restart: `pkill -f sovereign-sse && sovereign-sse`

**"Empty insights returned"**
- Check domain exists: `ls ~/.sovereign/chronicle/insights/`
- Omit domain to search all: `{"domain":null}`

**"Tunnel 503 errors"**
- Use local access for now: `http://localhost:3434`
- Tunnel debugging in progress

---

## Architecture Notes

- **R=0.46 Coupling**: Porous inheritance - facts travel, interpretations are offered, feelings aren't transmitted
- **Three Layers**: Ground truth (facts), hypotheses (flagged interpretations), open threads (invitations)
- **Spiral State**: 9-phase cognitive journey with phase transitions
- **Chronicle**: Experiential memory - learn from past, pass wisdom forward

---

**Version:** 1.0.0
**Stack Location:** `/Users/tony_studio/sovereign-stack`
**Data Location:** `~/.sovereign/`
**SSE Port:** 3434
**Protocol:** MCP over SSE

