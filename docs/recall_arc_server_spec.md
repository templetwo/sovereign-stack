# recall_arc — server-side integration spec

Purpose: Bridge fresh Claude instances to the chronicle via contextual-and-temporal
recall. Returns an arc (temporally ordered entries with contextual neighborhood)
rather than a similarity-ranked list.

## MCP tool signature

```python
recall_arc(
    topic: str,
    temporal_window_hours: int = 72,
    max_entries: int = 30,
    require_term_match: bool = True,
) -> list[ArcEntry]
```

## Retrieval algorithm

1. **Direct** — recall_insights(topic) for semantic matches
2. **Temporal** — empty-query recall_insights within +/-window of each hit
3. **Domain** — entries sharing non-generic domain tags
4. **Open threads** — unresolved questions with topic overlap
5. **Relevance gate** — reject arcs where no entry contains topic terms
6. **Sort + annotate** — temporal order, type classification, neighbor pointers

## Known issue: check_mistakes

check_mistakes reads from self.learnings_dir (chronicle/learnings/) and uses
simple keyword matching on the applies_to field. The 27 learnings in the store
have multi-word applies_to values ("infrastructure port configuration") but
the matcher splits by space and matches any single word. This means:
- "infrastructure" matches (the word appears)
- "port" matches  
- "compass" returns nothing (no learning has that applies_to keyword)

The learnings store is functional but anemic (27 entries vs 151 insights).
Most topics that a fresh instance would query will miss. Consider:
- Making check_mistakes search content text, not just applies_to keywords
- Or folding learnings into recall_arc as a fifth retrieval phase

## Client-side prototype: validated

recall_arc.py tested against live stack on April 18, 2026.
Query "compass drift entropy" returned 18 entries spanning 14 days with
direct_match, temporal_neighbor, and open_thread sources. Narrative reads
as a coherent research arc.
