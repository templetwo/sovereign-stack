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

────────────────────────────────────────────────────────────────────────────────
ISOLATED FROM THE LIVE STORE, 2026-09-02, and this file is why the rule exists.

Every test here used to read Anthony's real ~/.sovereign/handoffs through
server.handoff_engine — a module-level singleton built against DEFAULT_ROOT at
import. `test_returns_consumed_handoffs` asserted that at least one of the five
newest handoffs carries `consumed_at`, which was true on the day it was written
and became false the moment the SIGNATURE LEDGER landed: boot no longer stamps
consumption, so every handoff written since is unconsumed, and the five newest
are all recent. The test failed on pristine main while the code under test was
perfectly correct. The other seven were equally live-dependent and merely
lucky — `test_total_exceeds_the_unconsumed_head` needs more than one record on
disk, `test_filtered_total_reflects_the_filter` indexes records[0] and would
IndexError on an empty store.

A unit test's assertions can never be gated on live human state: it is a
reliability bug and a welfare boundary at once (the reasoning of a6f42cf, which
did this for the protected-drawer boot tests). PATCHING server.DEFAULT_ROOT IS
NOT ENOUGH HERE — `handoff_engine` was constructed at import and keeps its own
`root`; a DEFAULT_ROOT patch would leave it reading the live store and the
tests would pass for the wrong reason. The singleton itself is replaced.

The corpus is seeded as JSON FILES rather than through `HandoffEngine.write()`
on purpose: `_refuse_live_store_during_tests` guards that method under pytest,
and seeding through the writer would couple these tests to the writer's own
filename and consumption conventions.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sovereign_stack import server
from sovereign_stack.handoff import HandoffEngine

# The seeded corpus. Deterministic and deliberately shaped so every assertion
# below has something to bite on: more than two records (truncation), a mix of
# consumed and unconsumed (archaeology's whole point), and two threads
# (filtering), with the newest record carrying the thread that is filtered on.
_SEED = [
    # (timestamp, thread, consumed_at)
    ("2026-08-01T09:00:00", "alpha", "2026-08-02T09:00:00"),
    ("2026-08-02T09:00:00", "beta", "2026-08-03T09:00:00"),
    ("2026-08-03T09:00:00", "alpha", None),
    ("2026-08-04T09:00:00", "beta", "2026-08-05T09:00:00"),
    ("2026-08-05T09:00:00", "alpha", None),
]


@pytest.fixture(autouse=True)
def isolated_handoff_store(tmp_path: Path, monkeypatch):
    """Replace the module-level singleton with one rooted in tmp_path."""
    root = tmp_path / ".sovereign"
    (root / "handoffs").mkdir(parents=True)
    for i, (ts, thread, consumed) in enumerate(_SEED):
        (root / "handoffs" / f"{ts.replace(':', '')}_seat_{thread}_{i}.json").write_text(
            json.dumps(
                {
                    "timestamp": ts,
                    "source_instance": f"seat-{i}",
                    "source_session_id": f"session_{i}",
                    "thread": thread,
                    "note": f"handoff {i}",
                    "consumed_at": consumed,
                    "consumed_by": "a-named-reader" if consumed else None,
                }
            )
        )
    monkeypatch.setattr(server, "handoff_engine", HandoffEngine(root=str(root)))
    return root


def _tool_names():
    return {t.name for t in asyncio.run(server.list_tools())}


def _call(**args):
    out = asyncio.run(server._dispatch_tool("handoff_archaeology", args))
    return json.loads(out[0].text)


class TestTheFixtureIsWhatItClaims:
    """The isolation itself must be provable. A fixture that silently failed to
    displace the singleton would hand every test below the live store back, and
    they would go green on it — which is exactly the failure this file is
    correcting, one layer up."""

    def test_the_engine_under_test_is_not_the_live_store(self, isolated_handoff_store):
        assert server.handoff_engine.root == isolated_handoff_store / "handoffs"
        assert str(Path.home() / ".sovereign") not in str(server.handoff_engine.root)

    def test_the_corpus_is_exactly_the_seed(self):
        assert _call(limit=100000)["total"] == len(_SEED)


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
        """The whole point: consumed handoffs are reachable again.

        Seeded, not sampled. The live-store version of this assertion was true
        on the day it was written and false after the signature ledger stopped
        stamping consumption — the assertion tracked Anthony's boot history,
        not the tool.
        """
        res = _call(limit=100000)
        assert res["returned"] > 0
        assert any(r.get("consumed_at") for r in res["records"]), (
            "archaeology that cannot return a consumed handoff is not archaeology"
        )

    def test_the_consumed_records_are_unreachable_without_it(self):
        """The denominator that makes the tool worth having: the default
        surfaces only the unconsumed head, and these records sit behind it."""
        consumed_total = (
            _call(limit=100000)["total"] - _call(limit=100000, include_consumed=False)["total"]
        )
        assert consumed_total == sum(1 for _ts, _t, c in _SEED if c)

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

    def test_newest_first_is_the_order_it_actually_applied(self):
        """`order` is a claim about the data, so check the data. A label naming
        an order the records do not follow is the same fail-open shape one
        field over."""
        stamps = [r["timestamp"] for r in _call(limit=100000)["records"]]
        assert stamps == sorted(stamps, reverse=True)

    def test_filtered_total_reflects_the_filter(self):
        """A filter must narrow the denominator, not silently narrow only the page."""
        everything = _call(limit=100000)
        thread = everything["records"][0].get("thread")
        filtered = _call(limit=100000, thread=thread)
        assert filtered["total"] < everything["total"]
        assert filtered["total"] == sum(1 for _ts, t, _c in _SEED if t == thread)
        assert all(r.get("thread") == thread for r in filtered["records"])
