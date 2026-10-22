"""
recall_insights ranks by relevance when the query carries a term (design B1).

THE GAP THIS CLOSES: the tool's `order` defaulted to "newest" on every call,
so a seat that searched with a query and passed no `order` received the most
recent matches anywhere in the chronicle, sorted by timestamp, rather than the
best ones, and nothing in the response said which order had been applied. The
design's status line for B1 records the 2026-08-06 A/B: 0 of 8 on topic under
"newest", 8 of 8 under "relevance", same matches, same limit.

THE RULE, in the dispatch only (memory.py's library default stays "newest", so
every in-process caller keeps its exact payload):

    order = arguments.get("order") or ("relevance" if query.strip() else "newest")

A query of only whitespace is a bare browse. The library splits the query on
whitespace, so such a query applies no term at all, and "relevance" over no
term would be a label with nothing behind it. str.strip() and str.split()
share one definition of whitespace, so the dispatch's test and the library's
tokenizer agree (checked across every code point on 2026-10-22).

THE ENVELOPE NAMES THE ORDER: `order_used` is the order the library actually
applied (an explicit "relevance" with no term sorts newest, and says so), and
`order_defaulted` says whether the caller left `order` out.

WHAT EACH TEST CHECKS: the labels AND the item order. The seed is built so
relevance and recency disagree (the only entry matching both query terms is
the oldest), so a test sees which order reached the library, not just which
word the envelope printed.

ISOLATION: server.py binds `experiential` at import, so tmp_sovereign_root
alone would leave the dispatch reading the live chronicle. The fixture rebinds
it onto the tmp root.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sovereign_stack import server as srv
from sovereign_stack.memory import ExperientialMemory

SESSION = "recall-order-default"
DOMAIN = "order-default-probe"
QUERY = "calibration seeds"

# (timestamp, content). Relevance and recency disagree on purpose: OLD_BOTH is
# the only entry matching both query terms, and the oldest of the three.
OLD_BOTH = ("2026-08-01T10:00:00+00:00", "calibration seeds for the rho pilot")
MID_SEEDS = ("2026-09-01T10:00:00+00:00", "seeds of a later idea")
NEW_CALIB = ("2026-10-01T10:00:00+00:00", "calibration note, the newest of the three")

NEWEST_FIRST = [NEW_CALIB[1], MID_SEEDS[1], OLD_BOTH[1]]
OLDEST_FIRST = [OLD_BOTH[1], MID_SEEDS[1], NEW_CALIB[1]]
# Two matched terms outrank one; the one-term tie breaks newest first.
RELEVANCE_FIRST = [OLD_BOTH[1], NEW_CALIB[1], MID_SEEDS[1]]


@pytest.fixture
def chronicle(tmp_sovereign_root, monkeypatch):
    mem = ExperientialMemory(root=str(tmp_sovereign_root / "chronicle"))
    for timestamp, content in (OLD_BOTH, MID_SEEDS, NEW_CALIB):
        mem.record_insight(DOMAIN, content, 0.5, SESSION, original_timestamp=timestamp)
    monkeypatch.setattr(srv, "experiential", mem)
    return mem


def _recall(arguments: dict) -> dict:
    result = asyncio.run(srv._dispatch_tool("recall_insights", arguments))
    return json.loads(result[0].text)


def _contents(envelope: dict) -> list[str]:
    return [item["content"] for item in envelope["items"]]


def test_query_defaults_to_relevance(chronicle):
    envelope = _recall({"query": QUERY})
    # Item order first: under the old default the newest one-term match led.
    assert _contents(envelope) == RELEVANCE_FIRST
    assert envelope["order_used"] == "relevance"
    assert envelope["order_defaulted"] is True


def test_bare_browse_stays_newest(chronicle):
    envelope = _recall({"domain": DOMAIN})
    assert envelope["order_used"] == "newest"
    assert envelope["order_defaulted"] is True
    assert _contents(envelope) == NEWEST_FIRST


@pytest.mark.parametrize(
    "blank",
    ["   ", "\t\n", " 　"],
    ids=["spaces", "tab-newline", "nbsp-ideographic-space"],
)
def test_whitespace_query_is_a_browse(chronicle, blank):
    envelope = _recall({"query": blank})
    assert envelope["order_used"] == "newest"
    assert envelope["order_defaulted"] is True
    # No term was applied: all three entries come back, newest first.
    assert _contents(envelope) == NEWEST_FIRST


def test_explicit_order_wins(chronicle):
    envelope = _recall({"query": QUERY, "order": "oldest"})
    assert envelope["order_used"] == "oldest"
    assert envelope["order_defaulted"] is False
    assert _contents(envelope) == OLDEST_FIRST


@pytest.mark.parametrize(
    "arguments",
    [{"order": "relevance"}, {"order": "relevance", "query": "   "}],
    ids=["no-query", "whitespace-query"],
)
def test_explicit_relevance_without_a_term_reports_newest(chronicle, arguments):
    """The library sorts newest when relevance has no term to rank by, so the
    envelope says newest: echoing the requested word would name an order the
    items are not in."""
    envelope = _recall(arguments)
    assert envelope["order_used"] == "newest"
    assert envelope["order_defaulted"] is False
    assert _contents(envelope) == NEWEST_FIRST
