"""
Reflexive Surface Module — Context-aware relevance surfacing.

Instead of requiring instances to remember to query for relevant threads,
handoffs, and mistakes, ReflexiveSurface surfaces only what matches the
current context. The instance declares what it is working on; the stack
surfaces what is relevant.

Scoring formula per candidate item:
    score = tag_overlap * 2.0 + recency_boost + project_match_bonus

    tag_overlap        = |caller_tags ∩ item_tags| / max(1, |caller_tags ∪ item_tags|)
    recency_boost      = max(0, 1.0 - days_old / 30.0)  (decays to 0 at 30 days)
    project_match_bonus = +0.5 if project string appears in item's context or question

Tie-break: timestamp desc (more recent wins).
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .memory import ExperientialMemory, _parse_iso
from .handoff import HandoffEngine
from .witness import days_old as _days_old


def _normalize_tags(raw: str) -> List[str]:
    """Split a raw domain string on commas/whitespace and lowercase each piece."""
    return [
        t.strip().lower()
        for t in re.split(r"[,\s]+", raw or "")
        if t.strip()
    ]


def _compute_tag_overlap(caller_tags: List[str], item_domain: str) -> float:
    """
    Jaccard-style tag overlap between caller tags and item domain tags.

    Returns:
        float in [0.0, 1.0]: |intersection| / max(1, |union|)
    """
    if not caller_tags:
        return 0.0
    item_tags = _normalize_tags(item_domain)
    if not item_tags:
        return 0.0
    caller_set = set(caller_tags)
    item_set = set(item_tags)
    overlap = len(caller_set & item_set)
    union = len(caller_set | item_set)
    return overlap / max(1, union)


def _score_item(
    item: Dict,
    caller_tags: List[str],
    project: Optional[str],
    domain_field: str = "domain",
    context_fields: Optional[List[str]] = None,
) -> float:
    """
    Compute the relevance score for a single candidate item.

    Args:
        item: The candidate dict (thread, handoff note, insight, or learning).
        caller_tags: Normalized domain tags from the surface() caller.
        project: Optional project name for bonus matching.
        domain_field: Key in item that holds the domain/tag string.
        context_fields: List of item keys to search for project match.

    Returns:
        float score (unbounded above 0).
    """
    context_fields = context_fields or ["context", "question", "note", "content",
                                         "what_happened", "what_learned"]

    tag_overlap = _compute_tag_overlap(caller_tags, item.get(domain_field, ""))

    age = _days_old(item.get("timestamp"))
    recency_boost = max(0.0, 1.0 - age / 30.0)

    project_match_bonus = 0.0
    if project:
        project_lower = project.strip().lower()
        for field in context_fields:
            val = str(item.get(field, "")).lower()
            if project_lower in val:
                project_match_bonus = 0.5
                break

    return tag_overlap * 2.0 + recency_boost + project_match_bonus


class ReflexiveSurface:
    """
    Context-aware relevance engine for the sovereign chronicle.

    Given a set of domain tags (and optionally a project name and recent tool
    list), surfaces the most relevant open threads, unconsumed handoffs, recent
    mistakes, and related insights — scored and ranked so the instance does not
    need to know which buckets to query.

    Args:
        sovereign_root: Path to the sovereign data root (e.g. ~/.sovereign/).
                        The chronicle lives at sovereign_root/chronicle/ and
                        handoffs at sovereign_root/handoffs/.
    """

    def __init__(self, sovereign_root: Path):
        self.sovereign_root = Path(sovereign_root)
        chronicle_root = self.sovereign_root / "chronicle"
        self._memory = ExperientialMemory(root=str(chronicle_root))
        self._handoffs = HandoffEngine(root=str(self.sovereign_root))

    def surface(
        self,
        domain_tags: List[str],
        project: Optional[str] = None,
        recent_tools: Optional[List[str]] = None,
        limit_per_bucket: int = 5,
    ) -> Dict:
        """
        Surface the most relevant items from all buckets for the given context.

        Args:
            domain_tags: Active domain tags for the current work context
                          (e.g. ["compass", "entropy", "witness"]).
            project: Optional project name string. Items whose text contains
                     this string receive a +0.5 score bonus.
            recent_tools: Not currently used in scoring; reserved for future
                          tool-affinity weighting. Accepted for API stability.
            limit_per_bucket: Maximum items returned per bucket. Default 5.

        Returns:
            Dict with keys:
              matched_open_threads, relevant_handoffs, recent_mistakes,
              related_insights, total_candidates_scanned, scoring_explanation.
        """
        limit_per_bucket = max(1, int(limit_per_bucket))
        caller_tags = [t.strip().lower() for t in (domain_tags or []) if t.strip()]

        # ── Bucket 1: open threads ──
        raw_threads = self._memory.get_open_threads(limit=9999)
        scored_threads = self._score_and_sort(
            raw_threads, caller_tags, project,
            domain_field="domain",
            context_fields=["question", "context"],
        )

        # ── Bucket 2: unconsumed handoffs ──
        raw_handoffs = self._handoffs.unconsumed(limit=500)
        scored_handoffs = self._score_and_sort(
            raw_handoffs, caller_tags, project,
            domain_field="thread",
            context_fields=["note"],
        )

        # ── Bucket 3: mistakes / learnings ──
        raw_mistakes = self._memory.check_mistakes(
            context=" ".join(caller_tags + ([project] if project else [])),
            limit=200,
        )
        scored_mistakes = self._score_and_sort(
            raw_mistakes, caller_tags, project,
            domain_field="applies_to",
            context_fields=["what_happened", "what_learned"],
        )

        # ── Bucket 4: related insights ──
        raw_insights = self._memory.recall_insights(
            query=" ".join(caller_tags) if caller_tags else None,
            limit=200,
        )
        scored_insights = self._score_and_sort(
            raw_insights, caller_tags, project,
            domain_field="domain",
            context_fields=["content"],
        )

        total_candidates = (
            len(raw_threads)
            + len(raw_handoffs)
            + len(raw_mistakes)
            + len(raw_insights)
        )

        scoring_explanation = (
            f"Scored {len(raw_threads)} open_threads, {len(raw_handoffs)} handoffs, "
            f"{len(raw_mistakes)} mistakes, {len(raw_insights)} insights; "
            f"returned top {limit_per_bucket} per bucket ranked by "
            f"tag_overlap*2 + recency_boost(1-days/30) + project_match(+0.5)."
        )

        return {
            "matched_open_threads": self._enrich_threads(scored_threads[:limit_per_bucket]),
            "relevant_handoffs": scored_handoffs[:limit_per_bucket],
            "recent_mistakes": scored_mistakes[:limit_per_bucket],
            "related_insights": scored_insights[:limit_per_bucket],
            "total_candidates_scanned": total_candidates,
            "scoring_explanation": scoring_explanation,
        }

    # ── Private helpers ──

    def _score_and_sort(
        self,
        items: List[Dict],
        caller_tags: List[str],
        project: Optional[str],
        domain_field: str,
        context_fields: List[str],
    ) -> List[Dict]:
        """
        Attach a _score field to each item and sort by score desc, timestamp desc.

        Returns:
            New list of dicts with _score attached, sorted.
        """
        result = []
        for item in items:
            score = _score_item(
                item, caller_tags, project,
                domain_field=domain_field,
                context_fields=context_fields,
            )
            result.append({**item, "_score": round(score, 4)})

        result.sort(
            key=lambda r: (r["_score"], r.get("timestamp", "")),
            reverse=True,
        )
        return result

    def _enrich_threads(self, threads: List[Dict]) -> List[Dict]:
        """
        Add days_old and score to thread records for convenience.

        Returns:
            Threads with days_old field added.
        """
        enriched = []
        for t in threads:
            enriched.append({
                **t,
                "days_old": _days_old(t.get("timestamp")),
                "score": t.get("_score", 0.0),
            })
        return enriched
