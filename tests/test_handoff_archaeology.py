"""
Handoff archaeology — the 286 records that were written, preserved, and unreachable.

FAILURE SPECIMEN (measured 2026-08-27, not synthesized):
`HandoffEngine.all()` exists at handoff.py:255 with the docstring "All handoffs
(for archaeology), newest first." It has ZERO callers anywhere in the repository.
287 handoff files sit on disk; exactly one — the unconsumed head — is reachable
through any tool. The other 286 are notes written by instances that knew they
were ending, preserved perfectly, and returnable to nobody.

This is SOP #12 in its purest form: the fix was written and never connected.

DESIGN CONSTRAINT, deliberately not inherited from the sibling tool:
`handoff_acted_on_records` returns {"count": len(records)} where `records` is
already sliced to the limit — `count` names the RETURNED slice, not the total.
That is the silent-dropper shape this entire lane exists to close. This tool
carries a real coverage envelope instead, and the tests below FAIL if it ever
reports a returned-count as if it were a total.

Coverage honesty is not selection honesty, but it is the half we can close here.
"""

from __future__ import annotations

import asyncio
import json

from sovereign_stack import server


def _tool_names():
    return {t.name for t in asyncio.run(server.list_tools())}


def _call(**args):
    out = asyncio.run(server._dispatch_tool("handoff_archaeology", args))
    return json.loads(out[0].text)


class TestToolExists:
    def test_registered(self):
        """RED until the archaeology path is wired to anything at all."""
        assert "handoff_archaeology" in _tool_names()

    def test_schema_exposes_its_parameters(self):
        """
        The bridge-blindness lesson: a parameter absent from the published
        schema is unreachable by every schema-constrained caller, however
        well it works underneath.
        """
        tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "handoff_archaeology")
        props = set((tool.inputSchema or {}).get("properties", {}))
        assert {"limit", "thread", "include_consumed"} <= props


class TestReturnsTheDarkRecords:
    def test_returns_consumed_handoffs(self):
        """The whole point: consumed handoffs are reachable again."""
        res = _call(limit=5)
        assert res["returned"] > 0
        assert any(r.get("consumed_at") for r in res["records"]), (
            "archaeology that cannot return a consumed handoff is not archaeology"
        )

    def test_total_exceeds_the_unconsumed_head(self):
        res = _call(limit=1)
        assert res["total"] > 1


class TestCoverageEnvelopeIsHonest:
    """These are the tests that must be able to FAIL. A gate never shown to
    reject is decoration."""

    def test_total_is_not_the_returned_slice(self):
        """The sibling tool's exact bug, pinned so it cannot be reintroduced."""
        res = _call(limit=2)
        assert res["returned"] == 2
        assert res["total"] > res["returned"]

    def test_truncated_is_true_when_capped(self):
        res = _call(limit=2)
        assert res["truncated"] is True

    def test_truncated_is_false_when_complete(self):
        res = _call(limit=100000)
        assert res["truncated"] is False
        assert res["returned"] == res["total"]

    def test_states_the_order_it_applied(self):
        """
        Selection honesty, in the smallest form available here: a caller must
        be told the basis on which these records and not others survived.
        """
        res = _call(limit=3)
        assert res.get("order") == "newest_first"

    def test_filtered_total_reflects_the_filter(self):
        """A filter must narrow the denominator, not silently narrow only the page."""
        everything = _call(limit=100000)
        thread = everything["records"][0].get("thread")
        filtered = _call(limit=100000, thread=thread)
        assert filtered["total"] <= everything["total"]
        assert all(r.get("thread") == thread for r in filtered["records"])
