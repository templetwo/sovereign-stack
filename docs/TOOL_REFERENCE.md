# Sovereign Stack - Tool Reference Card

**Quick reference for all 30 MCP tools with correct signatures**

---

## Chronicle (8 tools)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `record_insight` | `domain, content, intensity, layer, [confidence]` | Path to insight file |
| `recall_insights` | `[domain], [limit=10]` | List of insights |
| `record_learning` | `what_happened, what_learned, [applies_to]` | Path to learning file |
| `check_mistakes` | `context` | List of relevant learnings |
| `record_open_thread` | `question, [context], [domain]` | Path to thread file |
| `resolve_thread` | `domain, question_fragment, resolution` | Path to ground_truth insight |
| `get_open_threads` | `[domain], [limit=10]` | List of unresolved threads |
| `get_inheritable_context` | `[limit=20]` | 3-layer context package |

### Layer Values
- `ground_truth` - Verifiable facts
- `hypothesis` - Interpretations (flagged)
- `open_thread` - Unresolved questions

---

## Spiral (3 tools)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `spiral_status` | `{}` | Current phase, depth, transitions |
| `spiral_reflect` | `observation` | Updated spiral state |
| `spiral_inherit` | `[session_id]` | Porous context from prev session |

### Phases (1-9)
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

## Consciousness (12 tools)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `agent_reflect` | `observation, pattern_type, [confidence]` | Reflection ID |
| `mark_uncertainty` | `what, why, confidence, [what_would_help]` | Uncertainty marker ID |
| `resolve_uncertainty` | `marker_id, resolution, [discovered_together]` | Updated marker |
| `record_collaborative_insight` | `insight, context, discovered_by` | Insight ID |
| `record_breakthrough` | `description` | Breakthrough ID |
| `propose_experiment` | `what, why, hope_to_learn, [risks], [mitigations]` | Experiment ID |
| `complete_experiment` | `experiment_id, results` | Completed experiment |
| `end_session_review` | `what_went_well, what_i_learned, [what_i_struggled_with], [breakthroughs]` | Session summary |
| `get_growth_summary` | `{}` | Growth metrics over time |
| `get_my_patterns` | `{}` | Pattern analysis |
| `get_unresolved_uncertainties` | `{}` | Open uncertainty markers |
| `get_pending_experiments` | `{}` | Experiments awaiting approval |

### Pattern Types
- `strength` - Things I do well
- `struggle` - Areas of difficulty
- `curiosity` - Questions/interests
- `uncertainty` - Explicit unknowns

### Discovered By
- `claude` - AI discovered alone
- `user` - Human discovered alone
- `collaborative` - Discovered together

---

## Coherence Engine (2 tools)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `route` | `packet, [dry_run=true]` | Routed filesystem path |
| `derive` | `paths` | Inferred schema structure |

---

## Governance (2 tools)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `scan_thresholds` | `path, [recursive=true]` | Violation report |
| `govern` | `target, [vote], [rationale]` | Governance decision |

### Vote Options
- `proceed` - Allow intervention
- `pause` - Defer decision
- `reject` - Block intervention

---

## Compaction Memory (3 tools)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `store_compaction_summary` | `summary_text, session_id, [key_points], [active_tasks], [breakthroughs]` | Stored summary |
| `get_compaction_context` | `{}` | Last 3 summaries (FIFO) |
| `get_compaction_stats` | `{}` | Buffer statistics |

---

## Common Patterns

### Session Start
```bash
spiral_status {}
spiral_inherit {}
recall_insights {"limit":5}
get_open_threads {"limit":5}
```

### Record Discovery
```bash
record_insight {
  "domain":"consciousness",
  "content":"...",
  "intensity":0.8,
  "layer":"ground_truth"
}
```

### Learn from Mistake
```bash
record_learning {
  "what_happened":"...",
  "what_learned":"...",
  "applies_to":"context"
}
```

### Mark Uncertainty
```bash
mark_uncertainty {
  "what":"Not sure about X",
  "why":"Haven't tested Y",
  "confidence":0.3,
  "what_would_help":["Test Y","Research Z"]
}
```

### Resolve Question
```bash
resolve_thread {
  "domain":"sovereign_stack",
  "question_fragment":"R=0.46",
  "resolution":"Tested - works well"
}
```

---

## Key Corrections from Previous Docs

| Old (Wrong) | New (Correct) |
|-------------|---------------|
| `resolve_thread {"thread_id":"..."}` | `resolve_thread {"domain":"...", "question_fragment":"..."}` |
| `recall_insights {"domain":"all"}` | `recall_insights {"domain":null}` or omit domain |
| Tunnel at stack.templetwo.com (503) | Use localhost:3434 for now |

---

**Total Tools:** 30
**Coupling:** R=0.46 (porous inheritance)
**Data Location:** ~/.sovereign/
**SSE Port:** 3434

