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
tokenizer agree (checked across every code point, and pinned below).

STOP-CHARACTERS, A PROPOSED READING (the plan's Review Focus 2; offered
2026-10-22, not a ruling, HQ's to keep or drop): the library has no stop set,
so the only characters its tokenizer drops are the whitespace str.split()
drops. A punctuation-only query such as "," is a term that filters, and
defaults to relevance; a browse label on it would disagree with the filter
applied.

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
import inspect
import json
import sys

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


# ---------------------------------------------------------------------------
# Pins added in the review's fix round (2026-10-22). Each one is the evidence
# for a question the Task 2 review could not settle from the diff.
# ---------------------------------------------------------------------------


def _live_order_schema() -> dict:
    """The `order` property exactly as the tool publishes it, from the same
    live registry the contract walker reads."""
    tools = asyncio.run(srv.list_tools())
    (tool,) = [t for t in tools if t.name == "recall_insights"]
    return tool.inputSchema["properties"]["order"]


ORDER_ENUM = tuple(_live_order_schema()["enum"])


def _whitespace_disagreements(blank_a, blank_b) -> list[int]:
    return [cp for cp in range(sys.maxunicode + 1) if blank_a(chr(cp)) != blank_b(chr(cp))]


def test_strip_and_split_agree_on_whitespace_for_every_code_point():
    """THE BASIS OF THE PROPOSED STOP-CHARACTER READING (Review Focus 2).

    The dispatch decides "bare browse" with str.strip(); the library decides
    which terms filter with str.split() (memory.py, `query_terms`), and it has
    no stop set. So, read as the characters the tokenizer drops, the plan's
    "stop-characters" are the whitespace split() drops. If the two disagreed on
    any code point, a query of that character would be labelled one way and
    filtered another.
    """
    assert _whitespace_disagreements(lambda c: not c.strip(), lambda c: not c.split()) == []
    # Control: the same walk against an ASCII-only idea of whitespace finds the
    # gap that idea would open (NBSP, the ideographic space), so the zero above
    # is a measurement and can fail.
    ascii_only = _whitespace_disagreements(lambda c: not c.strip(), lambda c: c in " \t\n\r\v\f")
    assert 0xA0 in ascii_only and 0x3000 in ascii_only


@pytest.mark.parametrize(
    ("query", "expected"),
    [(",", [NEW_CALIB[1]]), ("...", [])],
    ids=["comma-matches-one", "ellipsis-matches-none"],
)
def test_punctuation_is_a_term_not_a_browse(chronicle, query, expected):
    """Under the proposed reading of Review Focus 2, a punctuation-only query is a term.

    The library filters by it like any other token, so the envelope says
    relevance and the items are the filtered ones. A browse would have returned
    all three entries; the comma is in exactly one (NEW_CALIB) and the ellipsis
    in none. Labelling these a browse would need the library's tokenizer to drop
    them too, or the label and the filter would disagree.
    """
    envelope = _recall({"query": query})
    assert envelope["order_used"] == "relevance"
    assert envelope["order_defaulted"] is True
    assert _contents(envelope) == expected
    assert envelope["total_matched"] == len(expected)


def test_the_library_default_stays_newest(chronicle):
    """The brief's "leave memory.py's own default untouched" (step 3) names the
    library signature, and it is untouched: a direct ExperientialMemory call
    with a query and no `order` still sorts newest first. Only the tool's
    default moved, in the dispatch."""
    order_param = inspect.signature(ExperientialMemory.recall_insights).parameters["order"]
    assert order_param.default == "newest"
    items = chronicle.recall_insights(query=QUERY, domain=DOMAIN)
    assert [item["content"] for item in items] == NEWEST_FIRST


def test_the_published_order_declares_no_default(chronicle):
    """The tool schema's `default` key on `order` was removed, and must stay
    removed, because no declared value can hold.

    The contract walker calls recall_insights with no arguments and requires
    that omitting a declared default equal passing it explicitly. Whatever the
    value, `order_defaulted` differs between the two by construction. Explicit
    "relevance" with no query even returns the same items and `order_used` as
    omission; `order_defaulted` alone tells them apart.
    """
    assert "default" not in _live_order_schema()
    omitted = _recall({})
    assert omitted["order_defaulted"] is True
    for value in ORDER_ENUM:
        explicit = _recall({"order": value})
        assert explicit["order_defaulted"] is False
        assert explicit != omitted, value  # the comparison the walker would make
    relevance = _recall({"order": "relevance"})
    assert relevance["items"] == omitted["items"]
    assert relevance["order_used"] == omitted["order_used"] == "newest"


# The three sorts put the seed in three distinct sequences, so the order the
# library applied can be read off the items without re-deriving it.
_ORDER_BY_SEQUENCE = {
    tuple(NEWEST_FIRST): "newest",
    tuple(OLDEST_FIRST): "oldest",
    tuple(RELEVANCE_FIRST): "relevance",
}


@pytest.mark.parametrize("order", [None, *ORDER_ENUM], ids=lambda o: f"order-{o or 'omitted'}")
@pytest.mark.parametrize("query", [None, QUERY, "   "], ids=["no-query", "query", "blank-query"])
def test_order_used_names_the_order_the_items_are_in(chronicle, order, query):
    """`order_used` is the order the library applied, observed from the items,
    for every value the schema publishes (and omission), with and without a
    term.

    The dispatch mirrors the library's sort branches to name the order
    (server.py, `order_used`). A new order mode, or a changed fallback in
    memory.py, either puts the items in a sequence this table does not know or
    names the wrong one, and this fails.
    """
    assert len(_ORDER_BY_SEQUENCE) == 3  # the seed keeps the sorts apart
    arguments = {}
    if query is not None:
        arguments["query"] = query
    if order is not None:
        arguments["order"] = order
    envelope = _recall(arguments)
    contents = _contents(envelope)
    observed = _ORDER_BY_SEQUENCE.get(tuple(contents))
    assert observed is not None, (
        f"the items came back in an order no known sort produces: {contents}. A new "
        "order mode reached the library: teach the dispatch's order_used mirror "
        "(server.py) and this table."
    )
    assert envelope["order_used"] == observed
    assert envelope["order_defaulted"] is (order is None)
