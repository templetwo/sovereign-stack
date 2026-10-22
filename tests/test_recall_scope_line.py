"""
An empty search names its stores (design B6, thread 8a1d522b).

THE GAP THIS CLOSES: a claude.ai web seat searched the Stack for a receipt,
found nothing, and told Anthony it did not exist. Its own memory file cited
t2helix chronicle numbers, a store this recall never reads, and nothing in the
response said the Stack is one store among several. An empty result read as an
absence in the world when it was an absence in one store
(thread_20260901_225959_8a1d522b, opened by that seat after the error).

EVERY RECALL NOW NAMES WHAT IT READ, in the envelope's scope:

    stores_searched  ["stack-chronicle:<socket.gethostname()>"]
    not_searched     ["t2helix (machine-local)", "other machines' chronicles"]
    note             "an empty result is a measurement of these stores only"
                     when total_matched == 0; null otherwise

The three keys are on every envelope, a match included, so the scope's key set
is one fixed set (the same convention as `continuation`, which is null when
the read is not truncated). The note keys on total_matched, not on the page: a
page past the end is empty, but the search matched.

ISOLATION: server.py binds `experiential` at import, so the fixture rebinds it
onto the tmp root (the pattern of test_recall_order_default.py).
"""

from __future__ import annotations

import asyncio
import json
import socket

import pytest

from sovereign_stack import server as srv
from sovereign_stack.memory import ExperientialMemory

SESSION = "recall-scope-line"
DOMAIN = "scope-line-probe"
NO_SUCH_TERM = "zebra-no-such-term"

NOT_SEARCHED = ["t2helix (machine-local)", "other machines' chronicles"]
EMPTY_NOTE = "an empty result is a measurement of these stores only"


@pytest.fixture
def chronicle(tmp_sovereign_root, monkeypatch):
    mem = ExperientialMemory(root=str(tmp_sovereign_root / "chronicle"))
    mem.record_insight(DOMAIN, "a probe entry about calibration", 0.5, SESSION)
    mem.record_insight(DOMAIN, "a second probe entry", 0.5, SESSION)
    monkeypatch.setattr(srv, "experiential", mem)
    return mem


def _recall(arguments: dict) -> dict:
    result = asyncio.run(srv._dispatch_tool("recall_insights", arguments))
    return json.loads(result[0].text)


def _stores_searched_here() -> list[str]:
    return [f"stack-chronicle:{socket.gethostname()}"]


@pytest.mark.parametrize(
    "arguments",
    [{"query": NO_SUCH_TERM}, {"domain": "no-such-domain-anywhere"}],
    ids=["query-matches-nothing", "domain-matches-no-directory"],
)
def test_a_zero_match_recall_names_its_stores(chronicle, arguments):
    envelope = _recall(arguments)
    assert envelope["total_matched"] == 0
    assert envelope["items"] == []
    scope = envelope["scope"]
    assert scope["stores_searched"] == _stores_searched_here()
    assert scope["not_searched"] == NOT_SEARCHED
    assert scope["note"] == EMPTY_NOTE


def test_a_match_also_carries_the_store_names(chronicle):
    envelope = _recall({"query": "calibration"})
    assert envelope["total_matched"] == 1
    scope = envelope["scope"]
    assert scope["stores_searched"] == _stores_searched_here()
    assert scope["not_searched"] == NOT_SEARCHED
    # Present on a match, and null: the note speaks only for an empty result.
    assert "note" in scope
    assert scope["note"] is None


def test_the_note_keys_on_total_matched_not_on_the_page(chronicle):
    """A page past the end returns no items, but the search matched two
    entries, so it is not an empty result and carries no note."""
    envelope = _recall({"domain": DOMAIN, "offset": 5})
    assert envelope["items"] == []
    assert envelope["total_matched"] == 2
    assert envelope["scope"]["note"] is None


def test_the_store_is_named_by_socket_gethostname_at_call_time(chronicle, monkeypatch):
    """Computed on each call from socket.gethostname(), never a stored name: a
    hardcoded "mac-studio", or a name cached at import, fails here."""
    monkeypatch.setattr(socket, "gethostname", lambda: "probe-host-b6")
    envelope = _recall({"query": NO_SUCH_TERM})
    assert envelope["scope"]["stores_searched"] == ["stack-chronicle:probe-host-b6"]
