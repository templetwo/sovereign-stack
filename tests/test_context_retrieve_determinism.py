"""
context_retrieve determinism regression test.

THE BUG: metabolism.py's context_retrieve handler scored insights and sorted
by score alone. Ties fell back to Python's stable sort over whatever order
_load_all_insights (filesystem iteration) happened to hand back, which
differs macOS vs Linux and made omitting `limit` diverge from passing its
schema default (contract walker went red on Linux CI, 2026-07-21).

THE FIX (metabolism.py, `scored.sort` near the context_retrieve handler):
a total-order tiebreak — key=(score, timestamp, content), reverse=True — so
ties resolve the same way regardless of input order or platform.

This test freezes that fix: it builds insights with DELIBERATELY TIED
relevance (identical scoring content, identical timestamp, distinct content
strings) — the exact failure mode — and asserts the rendered output is
byte-identical across many random input-order shuffles. A second test pins
the related contract-walker property: omitting `limit` must behave
identically to passing its schema default (5).
"""

from __future__ import annotations

import asyncio
import random

from sovereign_stack import metabolism
from sovereign_stack.metabolism import handle_metabolism_tool

FIXED_TIMESTAMP = "2026-07-01T00:00:00+00:00"
FOCUS = "shared focus keyword"


def _make_tied_insights(n: int = 12) -> list[dict]:
    """~n insights whose score() ties exactly, but whose content differs.

    Every entry shares the words that `current_focus` matches ("shared
    focus keyword") plus a unique suffix that is NOT in the focus text, so
    content_overlap is identical for all of them. Same domain (no overlap
    with the focus words either way), same timestamp, same layer — so
    domain_overlap, recency_bonus, and layer_bonus tie too. Only the
    unique suffix distinguishes the content strings, which is exactly the
    tertiary sort key the fix added.
    """
    return [
        {
            "content": f"shared focus keyword entry unique_id_{i:02d}",
            "domain": "test_domain",
            "_domain_dir": "test_domain",
            "timestamp": FIXED_TIMESTAMP,
            "layer": "hypothesis",
        }
        for i in range(n)
    ]


def _call_context_retrieve(arguments: dict):
    return asyncio.run(handle_metabolism_tool("context_retrieve", arguments))


class TestContextRetrieveOrderInvariance:
    def test_tied_relevance_is_order_invariant_across_shuffles(self, monkeypatch):
        base_insights = _make_tied_insights(12)

        outputs = set()
        for _ in range(20):
            shuffled = base_insights[:]
            random.shuffle(shuffled)
            monkeypatch.setattr(
                metabolism, "_load_all_insights", lambda captured=shuffled: captured
            )
            result = _call_context_retrieve({"current_focus": FOCUS, "limit": 5})
            outputs.add(result[0].text)

        assert len(outputs) == 1, f"non-deterministic output across shuffles: {outputs}"


class TestContextRetrieveLimitDefault:
    def test_omitting_limit_matches_schema_default_of_five(self, monkeypatch):
        insights = _make_tied_insights(12)
        monkeypatch.setattr(metabolism, "_load_all_insights", lambda: insights)

        no_limit = _call_context_retrieve({"current_focus": FOCUS})
        explicit_default = _call_context_retrieve({"current_focus": FOCUS, "limit": 5})

        assert no_limit[0].text == explicit_default[0].text
