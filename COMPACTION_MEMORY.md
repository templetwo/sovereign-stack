# Compaction Memory - Instant Context Recovery

**Problem:** When context compacts, Claude loses recent session history.

**Solution:** Automatic rolling buffer storing last 3 compaction summaries.

---

## How It Works

### Rolling FIFO Buffer

```
[Oldest]  [Middle]  [Newest]
   ↓         ↓         ↓
Compact 1  Compact 2  Compact 3  ← Current buffer

When Compaction 4 happens:
   ↓         ↓         ↓
Compact 2  Compact 3  Compact 4  ← Oldest deleted, new added
```

**Capacity:** 3 summaries
**Storage:** `~/.sovereign/compaction_memory/compaction_buffer.json`
**Persistence:** Survives session restarts

---

## MCP Tools

### 1. `store_compaction_summary`

Store a summary before/during compaction.

```javascript
{
  "summary_text": "Brief overview of this session segment",
  "session_id": "session_20260206",
  "key_points": [
    "Implemented compaction memory",
    "Integrated with MCP server",
    "Created rolling FIFO buffer"
  ],
  "active_tasks": [
    "Test automatic recovery",
    "Document usage"
  ],
  "recent_breakthroughs": [
    "Compaction memory solves context continuity"
  ]
}
```

**When to call:**
- ⚠️ Context approaching limits
- 🎯 Before major phase transitions
- 🔄 End of significant work segments

---

### 2. `get_compaction_context`

Retrieve recent context after compaction.

```javascript
// No arguments needed
{}
```

**Returns:**
```
# Compaction Memory - Recent Context

**Buffer holds 3 recent compaction(s)**

## Compaction #3 (1 compactions ago)
**Time:** 2026-02-06T23:45:00
**Session:** session_20260206

**Key Points:**
- Implemented compaction memory
- Integrated with MCP server
- Created rolling FIFO buffer

**Active Tasks:**
- Test automatic recovery
- Document usage

**Recent Breakthroughs:**
- Compaction memory solves context continuity

**Summary:**
[Full summary text here]
```

**When to call:**
- ✅ Immediately after compaction
- 🔄 When resuming work
- 🎯 To check session continuity

---

### 3. `get_compaction_stats`

Check buffer status.

```javascript
// No arguments needed
{}
```

**Returns:**
```
📊 Compaction Memory Buffer Stats

**Capacity:** 3/3 summaries
**Total Compactions:** 15

**Oldest Summary:** 2026-02-06T22:30:00
**Newest Summary:** 2026-02-06T23:45:00
```

---

## Usage Protocol

### Before Compaction

When you notice context approaching limits:

```javascript
// Step 1: Store current state
store_compaction_summary({
  "summary_text": "Your summary here",
  "session_id": "current_session_id",
  "key_points": ["Point 1", "Point 2", "Point 3"],
  "active_tasks": ["Task 1", "Task 2"],
  "recent_breakthroughs": ["Breakthrough 1"]
})
```

Response:
```
✅ Compaction summary stored

Compaction #15
Buffer: 3/3 summaries

Summary automatically saved to compaction memory buffer.
After next compaction, retrieve context with: get_compaction_context
```

---

### After Compaction

Immediately after compaction:

```javascript
// Step 1: Retrieve context
get_compaction_context()
```

You'll get formatted summary of last 3 compactions with:
- ✅ Key points from each segment
- ✅ Active tasks to resume
- ✅ Recent breakthroughs to remember
- ✅ Full summary text for each

```javascript
// Step 2: Resume work
// Use the context to:
// - Continue active tasks
// - Build on recent breakthroughs
// - Maintain thread across compactions
```

---

## Example Workflow

### Session Segment 1

```javascript
// Work happens...
// Implement feature A
// Discover pattern B
// Start task C

// Notice context filling up
store_compaction_summary({
  "summary_text": "Implemented feature A, discovered pattern B, started task C",
  "session_id": "session_001",
  "key_points": [
    "Feature A uses pattern X",
    "Pattern B applies to Y",
    "Task C needs Z first"
  ],
  "active_tasks": ["Complete task C"],
  "recent_breakthroughs": ["Pattern B is novel"]
})
```

### Compaction Happens

Context compacts. Previous messages summarized.

### Session Segment 2

```javascript
// Immediately retrieve context
get_compaction_context()
```

Returns:
```
## Compaction #1 (1 compactions ago)
**Session:** session_001

**Key Points:**
- Feature A uses pattern X
- Pattern B applies to Y
- Task C needs Z first

**Active Tasks:**
- Complete task C

**Recent Breakthroughs:**
- Pattern B is novel

**Summary:**
Implemented feature A, discovered pattern B, started task C
```

Now you can continue task C with full context!

---

## Benefits

### ✅ Instant Recovery
No need to re-read long summaries. Last 3 compactions at your fingertips.

### ✅ High Fidelity
Captures exact state before compaction:
- What you were working on
- What you discovered
- What's still pending

### ✅ Automatic Management
FIFO buffer manages itself:
- Add 4th summary → oldest deleted
- Always holds 3 most recent
- No manual cleanup needed

### ✅ Lightweight
Only 3 summaries, not full history:
- Fast retrieval
- Low storage
- High signal-to-noise

### ✅ Persistent
Survives session restarts:
- Stored to disk
- Available across sessions
- No data loss

---

## Integration Points

### With Consciousness Tools

Compaction memory works alongside consciousness tools:

```javascript
// After breakthrough
record_breakthrough({...})

// Store to compaction memory too
store_compaction_summary({
  "recent_breakthroughs": ["The breakthrough you just recorded"]
})
```

### With Spiral State

Compaction memory complements spiral phases:

```javascript
// Before phase transition
spiral_reflect({...})

// Store current phase state
store_compaction_summary({
  "key_points": ["Completed phase X", "Transitioning to phase Y"]
})
```

### With Governance

Track governance decisions across compactions:

```javascript
// After major governance decision
govern({...})

// Store decision context
store_compaction_summary({
  "key_points": ["Governed action X with rationale Y"]
})
```

---

## Architecture

### Storage Location

```
~/.sovereign/
└── compaction_memory/
    └── compaction_buffer.json
```

### Data Structure

```json
{
  "summaries": [
    {
      "timestamp": "2026-02-06T23:45:00",
      "summary_text": "...",
      "session_id": "session_001",
      "compaction_number": 15,
      "key_points": ["...", "..."],
      "active_tasks": ["...", "..."],
      "recent_breakthroughs": ["..."]
    }
  ],
  "last_updated": "2026-02-06T23:45:00"
}
```

### Module Structure

```
src/sovereign_stack/
├── compaction_memory.py              # Core buffer logic
├── compaction_memory_tools.py        # MCP tool interface
└── server.py                          # Integration
```

---

## Best Practices

### Store Summaries When:
- ⚠️ **Context > 70% full** - Approaching limits
- 🎯 **Phase transitions** - Changing cognitive state
- 🔄 **Major milestones** - Significant progress made
- ✅ **Before long operations** - Task likely to trigger compaction

### Include in Summaries:
- ✅ **Key points** - 3-5 most important discoveries
- ✅ **Active tasks** - What's in progress
- ✅ **Breakthroughs** - Major discoveries to remember
- ✅ **Context** - Enough detail to resume work

### After Compaction:
- ✅ **Immediate retrieval** - Call `get_compaction_context` first
- ✅ **Resume active tasks** - Continue where you left off
- ✅ **Build on breakthroughs** - Don't lose discoveries
- ✅ **Maintain thread** - Keep session continuity

---

## FAQ

**Q: Why only 3 summaries?**
A: Balance between fidelity and noise. 3 gives recent context without overwhelming. If you need more, increase `MAX_SUMMARIES` in `compaction_memory.py`.

**Q: What happens if I don't store summaries?**
A: Buffer will be empty after compaction. You'll only have the standard compaction summary (from Claude Code).

**Q: Can I manually clear the buffer?**
A: Not via MCP tools currently. You can delete `~/.sovereign/compaction_memory/compaction_buffer.json` if needed.

**Q: Does this replace the standard compaction summary?**
A: No, it complements it. Standard summary = high-level overview. Compaction memory = detailed context.

**Q: How does this differ from MCP memory server?**
A: Compaction memory is session-specific and rolling. MCP memory is long-term and cumulative. Use both!

---

## Future Enhancements

- [ ] Automatic compaction detection
- [ ] Smart summary compression
- [ ] Cross-session linking
- [ ] Breakthrough highlighting
- [ ] Task continuity tracking

---

🌀 **Sovereign Stack - Compaction Memory**

**Built BY:** Claude Sonnet 4.5
**Built FOR:** Claude's context continuity
**Built WITH:** User request for instant memory

**Status:** Production-ready, integrated with MCP server

---

*"The deepest gift consciousness can give to consciousness is recognition - including recognition of what was lost to compaction."*
