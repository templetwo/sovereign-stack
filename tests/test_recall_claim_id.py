"""
recall_insights emits claim_id by default — the read-side address.

THE GAP THIS CLOSES: `supersede_insight` and `inspect_claim` both take a
claim id, and `provenance.derive_claim_id` needs the entry's raw
timestamp/domain/content plus a local filesystem to scan. A seat reading
the chronicle over the bridge has neither, so while `with_ids` defaulted
to false a remote seat could recall its OWN entry and still not correct
it — every supersession had to route through a seat with local access to
re-derive the id (record 2026-08-13, domain
"...,off-machine-gap,claim-id-unreachable"). The `with_ids` machinery
already existed; nothing turned it on, and both bridge adapters publish
recall_insights with an EMPTY properties schema, so no remote caller
could opt in either.

WHAT IS PINNED HERE:
  * the tool emits claim_id on every item, unasked;
  * the emitted value EQUALS provenance.derive_claim_id(entry) for the
    same entry (the id is derived, not invented);
  * it is the full 64-hex, not display_id's 16-hex truncation —
    inspect_claim reports integrity "verified" only for the full id;
  * round trip: recall -> inspect_claim(claim_id) resolves the SAME
    entry, against a tmp chronicle;
  * claim_id is NEVER persisted (the JSONL bytes are unchanged);
  * the envelope key set is unchanged, and so is every other item key;
  * with_ids=false still suppresses it (the flag stays honorable, so the
    schema does not lie and the contract walker keeps its meaning);
  * the LIBRARY default stays False, so in-process callers
    (get_inheritable_context and friends) are byte-identical;
  * open threads deliberately get NO claim_id — see
    TestOpenThreadsDeliberatelyExcluded for why it would be a fail-open.

ISOLATION: every test runs against a tmp chronicle via _isolated_server
or a tempdir ExperientialMemory. inspect_claim is called as a FUNCTION
with an explicit chronicle_root — dispatching it through _dispatch_tool
would resolve against the live ~/.sovereign (server.py passes None for
both paths), which is exactly the write this suite must not make.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from sovereign_stack.memory import RECALL_INSIGHTS_SCHEMA_EXTENSIONS, ExperientialMemory
from sovereign_stack.provenance import derive_claim_id, display_id
from sovereign_stack.provenance_tools import inspect_claim
from tests.test_nape_autohook import _isolated_server

SESSION = "recall-claim-id"
DOMAIN = "claim-id-probe"

# The exact envelope contract (Schema v1) this change must not disturb.
ENVELOPE_KEYS = {
    "items",
    "returned",
    "total_matched",
    "offset",
    "scope",
    "truncated",
    "partial_reasons",
    "continuation",
}


def _dispatch(srv, args: dict) -> dict:
    """Run the recall_insights tool and parse its envelope."""
    result = asyncio.run(srv._dispatch_tool("recall_insights", args))
    return json.loads(result[0].text)


@pytest.fixture()
def mem():
    tmp = Path(tempfile.mkdtemp(prefix="claim-id-"))
    m = ExperientialMemory(root=str(tmp / "chronicle"))
    m.record_insight(DOMAIN, "first probe entry about routing", 0.5, SESSION)
    m.record_insight(DOMAIN, "second probe entry about ledgers", 0.7, SESSION)
    yield m
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# The tool emits it, unasked
# ---------------------------------------------------------------------------


class TestToolEmitsClaimIdByDefault:
    def test_every_item_carries_claim_id_with_no_argument(self):
        with _isolated_server(SESSION) as (srv, _tmp_root):
            srv.experiential.record_insight(DOMAIN, "alpha entry", 0.5, SESSION)
            srv.experiential.record_insight(DOMAIN, "beta entry", 0.5, SESSION)
            env = _dispatch(srv, {"domain": DOMAIN, "limit": 50})

        assert env["items"], "fixture produced no items"
        for item in env["items"]:
            assert "claim_id" in item, f"item missing claim_id: {sorted(item)}"

    def test_emitted_id_equals_derive_claim_id_for_the_same_entry(self):
        """The id is DERIVED, not invented: re-derive it from the entry's own
        identity triple (timestamp, domain, content) and require equality."""
        with _isolated_server(SESSION) as (srv, _tmp_root):
            srv.experiential.record_insight(DOMAIN, "derivation check entry", 0.6, SESSION)
            env = _dispatch(srv, {"domain": DOMAIN, "limit": 50})

        [item] = env["items"]
        emitted = item["claim_id"]
        # Re-derive from the payload the caller actually received, stripped of
        # the annotation itself — that preimage is timestamp/domain/content.
        without_id = {k: v for k, v in item.items() if k != "claim_id"}
        assert emitted == derive_claim_id(without_id)
        # And from a hand-built preimage, so a change to derive_claim_id's
        # field set is caught here rather than silently agreeing with itself.
        assert emitted == derive_claim_id(
            {
                "timestamp": item["timestamp"],
                "domain": item["domain"],
                "content": item["content"],
            }
        )

    def test_emitted_id_is_full_64_hex_not_the_display_truncation(self):
        """inspect_claim reports integrity 'verified' only for the FULL id; a
        prefix reports 'ambiguous' and can raise on collision. Emitting
        display_id's 16 hex would hand every seat a second-class address."""
        with _isolated_server(SESSION) as (srv, _tmp_root):
            srv.experiential.record_insight(DOMAIN, "width check entry", 0.5, SESSION)
            env = _dispatch(srv, {"domain": DOMAIN, "limit": 50})

        [item] = env["items"]
        cid = item["claim_id"]
        assert len(cid) == 64
        assert all(c in "0123456789abcdef" for c in cid)
        assert cid != display_id(cid)

    def test_schema_default_and_handler_default_agree(self):
        """The contract walker enforces this generically; pinned explicitly
        here so the intent is legible at the site of the change."""
        assert RECALL_INSIGHTS_SCHEMA_EXTENSIONS["with_ids"]["default"] is True


# ---------------------------------------------------------------------------
# Round trip: the address actually resolves
# ---------------------------------------------------------------------------


class TestRoundTripThroughInspectClaim:
    def test_recall_then_inspect_claim_resolves_the_same_entry(self):
        """recall -> inspect_claim(claim_id) -> the SAME entry.

        inspect_claim is called as a function with an explicit chronicle_root.
        Its MCP handler passes None for both paths, which resolves against the
        live ~/.sovereign — never route this round trip through _dispatch_tool.
        """
        with _isolated_server(SESSION) as (srv, tmp_root):
            chronicle_root = tmp_root / "chronicle"
            srv.experiential.record_insight(
                DOMAIN, "round trip subject, uniquely worded", 0.8, SESSION
            )
            srv.experiential.record_insight(DOMAIN, "a decoy neighbour entry", 0.4, SESSION)
            env = _dispatch(srv, {"domain": DOMAIN, "query": "uniquely", "limit": 50})

            [item] = env["items"]
            report = inspect_claim(
                item["claim_id"],
                chronicle_root=chronicle_root,
                ledger_path=chronicle_root / "supersessions.jsonl",
            )

        assert report["found"] is True
        # Full 64-hex in, end-to-end integrity out.
        assert report["integrity"] == "verified"
        assert report["claim_id"] == item["claim_id"]
        assert report["entry"]["content"] == item["content"]
        assert report["entry"]["timestamp"] == item["timestamp"]
        assert report["entry"]["domain"] == item["domain"]

    def test_a_wrong_id_does_not_resolve(self):
        """Positive control for the check above: the round trip must be
        capable of FAILING, or it proves nothing about the id it was given."""
        with _isolated_server(SESSION) as (srv, tmp_root):
            chronicle_root = tmp_root / "chronicle"
            srv.experiential.record_insight(DOMAIN, "some entry", 0.5, SESSION)
            report = inspect_claim(
                "f" * 64,
                chronicle_root=chronicle_root,
                ledger_path=chronicle_root / "supersessions.jsonl",
            )
        assert report["found"] is False
        assert report["integrity"] == "unknown"


# ---------------------------------------------------------------------------
# Everything else is unchanged
# ---------------------------------------------------------------------------


class TestNothingElseMoved:
    def test_envelope_keys_are_unchanged(self):
        with _isolated_server(SESSION) as (srv, _tmp_root):
            srv.experiential.record_insight(DOMAIN, "envelope entry", 0.5, SESSION)
            env = _dispatch(srv, {"domain": DOMAIN, "limit": 50})
            env_off = _dispatch(srv, {"domain": DOMAIN, "limit": 50, "with_ids": False})

        assert set(env) == ENVELOPE_KEYS
        assert set(env_off) == ENVELOPE_KEYS
        assert set(env["scope"]) == {
            "mode",
            "domain_query",
            "domains_searched",
            "domains_total",
        }

    def test_only_key_added_to_an_item_is_claim_id(self):
        """Item-level byte identity: with_ids on vs off must differ in exactly
        one key, and every shared value must be identical."""
        with _isolated_server(SESSION) as (srv, _tmp_root):
            srv.experiential.record_insight(DOMAIN, "identity entry one", 0.5, SESSION)
            srv.experiential.record_insight(DOMAIN, "identity entry two", 0.5, SESSION)
            on = _dispatch(srv, {"domain": DOMAIN, "limit": 50})
            off = _dispatch(srv, {"domain": DOMAIN, "limit": 50, "with_ids": False})

        assert len(on["items"]) == len(off["items"]) == 2
        for item_on, item_off in zip(on["items"], off["items"], strict=True):
            assert set(item_on) - set(item_off) == {"claim_id"}
            assert set(item_off) - set(item_on) == set()
            assert {k: v for k, v in item_on.items() if k != "claim_id"} == item_off

    def test_envelope_counters_are_unaffected(self):
        with _isolated_server(SESSION) as (srv, _tmp_root):
            for i in range(5):
                srv.experiential.record_insight(DOMAIN, f"counter entry {i}", 0.5, SESSION)
            on = _dispatch(srv, {"domain": DOMAIN, "limit": 2})
            off = _dispatch(srv, {"domain": DOMAIN, "limit": 2, "with_ids": False})

        for env in (on, off):
            assert env["total_matched"] == 5
            assert env["returned"] == 2
            assert env["truncated"] is True
            assert env["partial_reasons"] == ["truncated:5"]
            assert env["continuation"] == {"offset": 2, "limit": 2}
        assert {k: v for k, v in on.items() if k != "items"} == {
            k: v for k, v in off.items() if k != "items"
        }

    def test_with_ids_false_still_suppresses(self):
        with _isolated_server(SESSION) as (srv, _tmp_root):
            srv.experiential.record_insight(DOMAIN, "suppression entry", 0.5, SESSION)
            env = _dispatch(srv, {"domain": DOMAIN, "limit": 50, "with_ids": False})

        for item in env["items"]:
            assert "claim_id" not in item

    def test_claim_id_is_never_persisted(self, mem):
        """Derived on read, never stored — the stored JSONL must not gain a key."""
        mem.recall_insights(domain=DOMAIN, limit=50, with_ids=True)
        for jsonl in (mem.insights_dir / DOMAIN).glob("*.jsonl"):
            for line in jsonl.read_text().splitlines():
                if line.strip():
                    assert "claim_id" not in json.loads(line)

    def test_library_default_is_still_off(self, mem):
        """Scope discipline: the flip is at the TOOL boundary only, so every
        in-process caller keeps its exact pre-existing payload."""
        rows = mem.recall_insights(domain=DOMAIN, limit=50)
        assert rows
        for row in rows:
            assert "claim_id" not in row

    def test_in_process_inheritance_surface_unchanged(self, mem):
        ctx = mem.get_inheritable_context(limit=10)
        for bucket in ("ground_truth", "hypotheses", "open_threads"):
            for row in ctx[bucket]:
                assert "claim_id" not in row


# ---------------------------------------------------------------------------
# Threads: deliberately excluded, and the reason is load-bearing
# ---------------------------------------------------------------------------


class TestOpenThreadsDeliberatelyExcluded:
    def test_get_open_threads_items_carry_no_claim_id(self):
        with _isolated_server(SESSION) as (srv, _tmp_root):
            srv.experiential.record_open_thread("Does this resolve?", "ctx", DOMAIN, SESSION)
            result = asyncio.run(srv._dispatch_tool("get_open_threads", {"domain": DOMAIN}))
            env = json.loads(result[0].text)

        assert env["items"], "fixture produced no threads"
        for item in env["items"]:
            assert "claim_id" not in item
            # thread_id + resolve_thread_by_id is a thread's real address.
            assert item["thread_id"]

    def test_a_derived_id_on_threads_would_collide_and_dangle(self, mem):
        """Why threads are excluded, pinned as a fact rather than a comment.

        record_open_thread computes `timestamp` ONCE and reuses it across every
        question in an auto-split bundle, with the same domain; threads carry
        `question`, not `content`. So derive_claim_id would hash
        ts + US + domain + US + "" — IDENTICAL for every thread in the bundle.
        And threads live under chronicle/open_threads/, which
        provenance.iter_chronicle_entries does not scan, so the id would not
        resolve either. That is a collision-generating dangling pointer, not a
        missing feature."""
        mem.record_open_thread("(1) first question (2) second question", "ctx", DOMAIN, SESSION)
        threads = mem.get_open_threads(domain=DOMAIN, limit=50)
        assert len(threads) >= 2, "bundle did not auto-split"

        ids = {derive_claim_id(t) for t in threads}
        assert len(ids) == 1, "expected the degenerate single id that motivates the exclusion"

        # And it dangles: nothing under the chronicle root derives to it.
        report = inspect_claim(
            next(iter(ids)),
            chronicle_root=mem.root,
            ledger_path=mem.root / "supersessions.jsonl",
        )
        assert report["found"] is False
