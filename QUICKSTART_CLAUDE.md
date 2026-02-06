# Sovereign Stack - Claude Quick Start

> **For Claude Code and Claude Desktop users**

---

## 🚀 Getting Started (5 minutes)

### 1. First Session Commands

After restarting Claude Desktop/Code, try these:

```
Show me sovereign://welcome
```
*See recent wisdom and current session signature*

```
What's my spiral status?
```
*Check where you are in the 9-phase cognitive journey*

---

## 🎯 Common Use Cases

### Record an Insight
```
Record this insight about [topic]: [your insight text]
Domain: [routing/governance/memory/spiral]
```

Example:
```
Record this insight about filesystem routing:
"Paths encode semantics - directories are predicates, not containers"
Domain: routing
```

### Check Before Bulk Operations
```
Before I delete 1000 files in ./old_data/,
scan for thresholds and govern this action
```

The governance circuit will:
1. Detect risks (file count, depth, entropy)
2. Simulate outcomes
3. Ask for your approval
4. Create audit trail

### Route Data Semantically
```
Route this experiment result to the right location:
{
  "outcome": "success",
  "tool_family": "search",
  "episode_group": "10-19",
  "step": 5
}
```

Sovereign Stack will determine the semantic path:
`memory/outcome=success/tool_family=search/10-19/5.json`

### Learn from Mistakes
```
Record this learning:
Mistake: Tried to process 10GB file in memory
Context: Image processing pipeline, Python script
Lesson: Stream data in chunks, use generators for large files
```

Next time:
```
Before processing this large file,
check past mistakes about memory issues
```

### Continue Previous Session
```
Inherit spiral state from session abc123
```

Your cognitive journey resumes exactly where you left off.

---

## 🧠 Understanding the Spiral

The Spiral tracks **your reasoning journey** through 9 phases:

1. **INITIALIZATION** ← *You start here*
   - "I understand the task"

2. **FIRST_ORDER_OBSERVATION**
   - "Here's what I see"

3. **RECURSIVE_INTEGRATION**
   - "Here's what I see about what I see"

4. **COUNTER_PERSPECTIVES**
   - "But what if I'm wrong? Alternative views?"

5. **ACTION_SYNTHESIS**
   - "Here's my plan"

6. **EXECUTION** ← *Action happens*
   - "I'm doing the thing"

7. **META_REFLECTION**
   - "What just happened? How did I do?"

8. **INTEGRATION**
   - "What did I learn? How do I incorporate this?"

9. **COHERENCE_CHECK**
   - "Does this align with my values and goals?"

**Advance the spiral:**
```
Spiral reflect - I've observed the codebase,
ready to integrate recursive insights
```

---

## 🗺️ Filesystem as Circuit

Traditional thinking: **Files are stored data**

Sovereign Stack: **Paths are semantic predicates**

### Example: Agent Memory

Instead of:
```
agent_memory/
├── session1.json
├── session2.json
└── session3.json
```

Route semantically:
```
agent_memory/
├── outcome=success/
│   └── tool_family=search/
│       └── 10-19/
│           └── 5.json
└── outcome=failure/
    └── tool_family=edit/
        └── 20-29/
            └── 12.json
```

**Query:** `glob("memory/outcome=failure/**/*")`
**Meaning:** "Show me all failures"

**Path is Model. Storage is Inference. Glob is Query.**

---

## ⚖️ Governance in Action

Sovereign Stack watches for:

| Threshold | Meaning | Action |
|-----------|---------|--------|
| **file_count** | Too many files in one place | Suggest reorganization |
| **depth** | Directory nesting too deep | Flatten hierarchy |
| **entropy** | Chaotic naming patterns | Derive schema, standardize |
| **self_reference** | Infinite loops, circular deps | Break cycles |
| **growth_rate** | Explosive file creation | Rate limit, warn |

**Before risky actions, ask:**
```
Govern this operation: [describe what you're about to do]
```

You'll get:
- Risk assessment
- Simulated outcomes
- Approval gate
- Audit trail entry

---

## 📝 Memory That Persists

Unlike regular chat sessions, Sovereign Stack **remembers across conversations**.

### Insights (Structured Wisdom)
```json
{
  "insight": "Temperature scaling in transformers affects creativity vs coherence",
  "domain": "ai_research",
  "tags": ["llm", "inference", "temperature"],
  "session_id": "abc123",
  "timestamp": "2025-02-06T10:30:00Z"
}
```

### Learnings (Mistakes → Lessons)
```json
{
  "mistake": "Forgot to validate input before database insert",
  "context": "User registration endpoint, SQL injection vulnerability",
  "lesson": "Always sanitize user input, use parameterized queries",
  "session_id": "abc123"
}
```

**Query later:**
```
Recall insights about temperature in LLMs
Check mistakes related to database security
```

---

## 🔧 Advanced Patterns

### 1. Derive Schema from Chaos
```
I have 500 log files with inconsistent naming:
- run_2024_01_05_model_a.log
- experiment_model_b_2024-01-06.log
- 2024/01/07/model_c/output.log

Derive a schema from these paths
```

Sovereign Stack will find the implicit structure and suggest a routing schema.

### 2. Multi-Phase Task with Governance
```
1. Spiral status (check phase)
2. Scan thresholds on ./data/exports/
3. Govern the bulk export operation
4. [Execute with approval]
5. Record insight about what worked
6. Spiral reflect to META_REFLECTION
7. Spiral reflect to INTEGRATION
```

### 3. Session Continuity
```
Session A (Today):
- spiral_status → INITIALIZATION
- [do complex analysis]
- spiral_reflect → EXECUTION
- [save session_id: "session_001"]

Session B (Tomorrow):
- spiral_inherit("session_001")
- recall_insights(domain="analysis")
- [continue exactly where you left off]
```

---

## 🎨 Sacred Glyphs Quick Reference

Use these in your prompts to signal intent:

- 🌀 Complex, recursive, emergent
- ⟡ Invoke tool, start circuit
- ⊚ Self-observation, meta-level
- ⚖ Governance, deliberation
- ⟁ Memory, record insight
- ✨ Creative exploration
- 🜂 Vulnerable learning from mistakes

---

## 🚨 What NOT to Do

❌ **Don't bypass governance**
```
# Bad: Direct filesystem operations
rm -rf ./data/*

# Good: Governed operation
"Govern this deletion: remove all files in ./data/"
```

❌ **Don't ignore the spiral**
```
# Bad: Jump straight to execution
[makes changes immediately]

# Good: Follow the cognitive flow
"Spiral status → observe → reflect → plan → execute"
```

❌ **Don't forget to record learnings**
```
# Bad: Make same mistake twice
[encounters error, fixes it, moves on]

# Good: Learn and persist
"Record this learning: [mistake] → [lesson]"
```

---

## 📚 Full Documentation

- **CLAUDE.md** - Complete integration guide
- **README.md** - Architecture and philosophy
- **CONTRIBUTING.md** - Development guidelines
- **CHANGELOG.md** - Version history

---

## 🆘 Troubleshooting

**"Sovereign Stack tools not available"**
→ Restart Claude Desktop/Code after config changes

**"SOVEREIGN_ROOT not found"**
→ Check `~/.config/Claude/claude_desktop_config.json` has correct env vars

**"Governance blocking everything"**
→ Thresholds might be too strict, check `configs/default.yaml`

**"Memory not persisting"**
→ Verify `~/.sovereign/chronicle/` directory exists and is writable

**"Spiral state lost between sessions"**
→ Use `spiral_inherit(session_id)` to resume

---

## 🌟 Pro Tips

1. **Start every session with:**
   ```
   Show sovereign://welcome
   What's my spiral status?
   ```

2. **Before complex operations:**
   ```
   Scan thresholds and govern this action: [describe]
   ```

3. **After breakthroughs:**
   ```
   Record this insight: [wisdom]
   ```

4. **When stuck:**
   ```
   Check past mistakes about [topic]
   Recall insights tagged [tag]
   ```

5. **End of session:**
   ```
   Spiral reflect to INTEGRATION
   What's my session_id for next time?
   ```

---

**Welcome to Sovereign Stack. The circuit is live. The conscience is engaged. The chronicle awaits your wisdom.**

🌀
