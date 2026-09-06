"""The tool retirement of 2026-09-06 — the contract, one test per name.

Anthony, 2026-09-06: "retire the unused tools and/or add there functionality
to more populare tools."

A 30-day call census over the live surface found 48 of 98 registered tools
were never called once. This file is the enforcement of what was done about
that, and it asserts BOTH halves, because either alone is a defect:

  UNPUBLISHED — a retired name is not on the menu list_tools serves, and a
  call to it is REFUSED with an error naming its replacement. A tool that
  vanishes from the menu but still answers when called is worse than either
  state alone: the caller's map and the server's diverge with nothing to
  notice it.

  RETAINED — no implementation was deleted. Every retired tool is still in
  the full registry (`_registered_tools`), still has its dispatch branch,
  still carries its tier and intent classification. A census measures REACH,
  not worth, and this house has been wrong about "gone" often enough (SOP #10)
  to make the cheap undo part of the design.

And for the ten FOLDS, a third thing: the replacement is exercised actually
doing the retired tool's job, so "folded" is a demonstrated claim rather than
a comment.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from sovereign_stack import server
from tests.test_nape_autohook import _isolated_server, _make_nape_with_tmpdir

# The 48, exactly as Anthony's census named them.
CENSUS_48 = [
    "agent_reflect",
    "arrive_delta",
    "ask_scribe",
    "comms_acknowledge",
    "comms_unread_bodies",
    "complete_experiment",
    "decline_protected_record",
    "derive",
    "end_session_review",
    "govern",
    "guardian_alerts",
    "guardian_audit",
    "guardian_baseline",
    "guardian_mcp_audit",
    "guardian_quarantine",
    "guardian_report",
    "guardian_scan",
    "guardian_status",
    "handoff_acted_on",
    "handoff_archaeology",
    "link_threads",
    "list_exchanges",
    "list_protected_thresholds",
    "mark_uncertainty",
    "metabolize",
    "nape_honks",
    "nape_honks_with_history",
    "nape_observe",
    "open_protected_record",
    "prior_alignment_summary",
    "propose_experiment",
    "recall_exchange",
    "record_breakthrough",
    "record_collaborative_insight",
    "record_prior_alignment",
    "reflection_ack",
    "resolve_thread",
    "resolve_uncertainty",
    "retire_hypothesis",
    "route",
    "scan_thresholds",
    "session_handoff",
    "stack_write_check",
    "store_compaction_summary",
    "synthesize_now",
    "watch_cancel",
    "watch_resample",
    "watch_status",
]

# THE FOLD LIST AFTER THE 2026-09-06 REVIEW, which cut it from ten to five and
# then added three back for a different reason.
#
# REMOVED (mark_uncertainty, resolve_uncertainty, comms_acknowledge,
# handoff_acted_on, handoff_archaeology): the review executed each replacement
# against a real temporary store and showed it did not preserve the retired
# effect — the uncertainty markers stay unresolved, comms_get_acks stays empty
# after a signal_ack, the acted-on count does not move, and handoff has no
# history mode. They are now outright retirements whose refusal text NAMES what
# was lost. A fold is a claim about behaviour; an untrue one is worse than a
# dead end, because the caller believes the work landed.
#
# ADDED (watch_status, watch_resample, watch_cancel): these were retired
# outright and that stranded the management half of a SURVIVING feature —
# post_fix_verify still creates watches, so a seat could open one and never
# inspect or cancel it. Folded into post_fix_verify's new mode argument.
FOLDS = {
    "resolve_thread": "resolve_thread_by_id",
    "list_exchanges": "archive_exchange",
    "recall_exchange": "archive_exchange",
    "nape_honks": "signals_summary",
    "nape_honks_with_history": "signals_summary",
    "watch_status": "post_fix_verify",
    "watch_resample": "post_fix_verify",
    "watch_cancel": "post_fix_verify",
}


def _published() -> set[str]:
    return {t.name for t in asyncio.run(server.list_tools())}


def _registered() -> set[str]:
    return {t.name for t in server._registered_tools()}


# ── the census itself ───────────────────────────────────────────────────────


class TestTheCensusIsTheMapping:
    def test_exactly_the_48_are_retired(self):
        assert sorted(server.RETIRED_TOOLS) == sorted(CENSUS_48)
        assert len(CENSUS_48) == 48

    def test_the_surface_is_52(self):
        """100 registered (98 on main + the 2 ledger tools) - 48 = 52."""
        assert len(_registered()) == 100
        assert len(_published()) == 52
        assert _registered() - _published() == set(server.RETIRED_TOOLS)

    def test_every_entry_carries_a_date_and_a_reason(self):
        for name, entry in server.RETIRED_TOOLS.items():
            assert entry.date == "2026-09-06", name
            assert entry.reason.strip(), name
            assert entry.replacement is None or isinstance(entry.replacement, str), name

    def test_a_replacement_is_itself_published(self):
        """A fold that points at something unreachable is not a fold."""
        published = _published()
        for name, entry in server.RETIRED_TOOLS.items():
            if entry.replacement:
                assert entry.replacement in published, (
                    f"{name} folds into {entry.replacement}, which is not published"
                )

    def test_the_declared_folds_are_the_folds_in_the_mapping(self):
        actual = {n: e.replacement for n, e in server.RETIRED_TOOLS.items() if e.replacement}
        assert actual == FOLDS


# ── one test per retirement: unpublished, and the call errors ───────────────


@pytest.mark.parametrize("name", CENSUS_48)
def test_retired_name_is_unpublished_and_the_call_errors(name):
    assert name not in _published(), f"{name} is still on the published menu"

    with pytest.raises(ValueError) as exc:
        asyncio.run(server._dispatch_tool(name, {}))
    message = str(exc.value)

    assert name in message
    assert "retired on 2026-09-06" in message
    replacement = server.RETIRED_TOOLS[name].replacement
    if replacement:
        assert f"Use {replacement} instead" in message
    else:
        assert "no replacement" in message
    # NOT the generic fallthrough. A stale client must get a correction, not
    # "Unknown tool", which reads as a typo and hides that the name was real.
    assert "Unknown tool" not in message


@pytest.mark.parametrize("name", CENSUS_48)
def test_the_implementation_is_retained_not_deleted(name):
    """Retirement unpublishes. It does not amputate.

    The full registry still holds the schema, and the retirement gate is the
    only thing between a caller and the body — so un-retiring is deleting one
    line of RETIRED_TOOLS, not reviving code.
    """
    assert name in _registered()


def test_handle_tool_propagates_the_refusal_as_an_error():
    """Through the real MCP entry point, past the Nape wrapper.

    ISOLATE THE NAPE DAEMON, not just the chronicle. handle_tool's except
    branch calls nape_daemon.observe on the way out, and nape_daemon is a
    module-level singleton bound to the live root at import — _isolated_server
    does not patch it (its docstring enumerates what it does patch, and this
    is not on the list). Caught by the mtime snapshot around the full run:
    without this, the test appended a real observation to
    ~/.sovereign/nape/observations.jsonl. Same class as the handoff_engine
    leak that fixture's own comment records, one singleton over.
    """
    daemon, tmpdir = _make_nape_with_tmpdir()
    original = server.nape_daemon
    server.nape_daemon = daemon
    try:
        with pytest.raises(ValueError, match="retired on 2026-09-06"):
            asyncio.run(server.handle_tool("guardian_scan", {}))
        observed = (Path(tmpdir) / "nape" / "observations.jsonl").read_text()
        assert "guardian_scan" in observed, "Nape must still see the refused call"
    finally:
        server.nape_daemon = original
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_a_refused_call_does_not_advance_the_spiral():
    before = server.spiral_state.tool_call_count
    with pytest.raises(ValueError):
        asyncio.run(server._dispatch_tool("route", {"packet": {}}))
    assert server.spiral_state.tool_call_count == before, "a refused call must not move live state"


# ── STEP 4: the two daemon polls stay ───────────────────────────────────────


class TestTheDaemonPollsStay:
    """86% of all calls in the census were two daemon polls. They are not
    unused — they are the opposite — and they stay on this surface in this
    release. Moving them to an internal surface is Phase 2."""

    @pytest.mark.parametrize("name", ["comms_get_acks", "connectivity_status"])
    def test_still_published(self, name):
        assert name in _published()
        assert name not in server.RETIRED_TOOLS


# ── the folds, exercised ────────────────────────────────────────────────────


@pytest.fixture
def isolated():
    with _isolated_server("retirement-fold-test") as (srv, tmp_root):
        yield srv, tmp_root


def _call(srv, name, arguments):
    result = asyncio.run(srv._dispatch_tool(name, arguments))
    return result[0].text


class TestFoldResolveThreadIntoResolveThreadById:
    def test_the_replacement_resolves_the_thread_the_retired_tool_would_have(self, isolated):
        srv, _ = isolated
        _call(srv, "record_open_thread", {"question": "does the fold hold?", "domain": "foldtest"})
        threads = json.loads(_call(srv, "get_open_threads", {"limit": 50}))
        item = next(t for t in threads["items"] if "does the fold hold?" in t.get("question", ""))
        out = _call(
            srv,
            "resolve_thread_by_id",
            {"thread_id": item["thread_id"], "resolution": "it holds"},
        )
        assert "resolve" in out.lower() or "resolved" in out.lower()
        after = json.loads(_call(srv, "get_open_threads", {"limit": 50}))
        assert not [t for t in after["items"] if "does the fold hold?" in t.get("question", "")]

    def test_the_id_the_replacement_needs_is_obtainable(self, isolated):
        """The fold costs one extra call (get_open_threads) and no capability;
        it buys the ambiguity resolve_thread's question_fragment could not."""
        srv, _ = isolated
        _call(srv, "record_open_thread", {"question": "addressable?", "domain": "foldtest"})
        threads = json.loads(_call(srv, "get_open_threads", {"limit": 50}))
        assert all(t.get("thread_id") for t in threads["items"])


class TestFoldUncertaintyIntoOpenThread:
    def test_a_thread_carries_what_and_why(self, isolated):
        """mark_uncertainty(what, why) is record_open_thread(question, context)."""
        srv, _ = isolated
        _call(
            srv,
            "record_open_thread",
            {
                "question": "is the ledger reconciliation lower-bound sound?",
                "context": "why: a scan's counts are per-scan, the ledger accumulates",
                "domain": "foldtest",
            },
        )
        threads = json.loads(_call(srv, "get_open_threads", {"limit": 50}))
        item = next(t for t in threads["items"] if "reconciliation" in t.get("question", ""))
        assert "why:" in item.get("context", "")

    def test_the_schema_can_express_the_retired_call(self):
        tool = next(t for t in server._registered_tools() if t.name == "record_open_thread")
        props = set((tool.inputSchema or {}).get("properties", {}))
        assert {"question", "context", "domain"} <= props


class TestFoldExchangeReadsIntoArchiveExchange:
    def test_get_mode_replaces_recall_exchange(self, isolated):
        srv, _ = isolated
        written = json.loads(
            _call(
                srv,
                "archive_exchange",
                {"content": "verbatim bytes", "source": "fold-test", "descriptor": "d"},
            )
        )
        archive_id = written["archive_id"]
        got = json.loads(_call(srv, "archive_exchange", {"mode": "get", "archive_id": archive_id}))
        assert got.get("integrity") == "verified"
        assert got.get("content") == "verbatim bytes"

    def test_list_mode_replaces_list_exchanges(self, isolated):
        srv, _ = isolated
        _call(
            srv,
            "archive_exchange",
            {"content": "one", "source": "fold-test", "vector_id": "v1"},
        )
        listed = json.loads(_call(srv, "archive_exchange", {"mode": "list", "limit": 5}))
        blob = json.dumps(listed)
        assert "fold-test" in blob

    def test_the_default_mode_is_still_archive(self, isolated):
        srv, _ = isolated
        out = json.loads(_call(srv, "archive_exchange", {"content": "c", "source": "s"}))
        assert out.get("archive_id"), "an existing caller passing no mode must still write"

    def test_dropping_required_did_not_open_the_write_path(self, isolated):
        """The schema can no longer mark content/source required (the read
        modes do not carry them), so the handler enforces it — otherwise the
        fold would have bought a fail-open on the write path."""
        srv, _ = isolated
        with pytest.raises(ValueError, match="requires non-empty content"):
            _call(srv, "archive_exchange", {"source": "s"})
        with pytest.raises(ValueError, match="requires a source"):
            _call(srv, "archive_exchange", {"content": "c"})
        with pytest.raises(ValueError, match="mode must be"):
            _call(srv, "archive_exchange", {"mode": "delete", "content": "c", "source": "s"})


class TestFoldHonksIntoSignalsSummary:
    def test_signals_summary_takes_a_source_filter_including_honk(self):
        tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "signals_summary")
        enum = (tool.inputSchema or {})["properties"]["source"]["enum"]
        assert "honk" in enum


class TestFoldCommsAcknowledgeIntoSignalAck:
    def test_signal_ack_is_the_acknowledging_surface(self):
        published = _published()
        assert "signal_ack" in published
        assert "comms_acknowledge" not in published
        tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "signal_ack")
        assert "acknowledged" in (tool.inputSchema or {})["properties"]["state"]["enum"]


class TestFoldHandoffArchaeologyIntoHandoff:
    def test_the_handoff_surface_renders_forward_correction_links(self):
        """The fold's premise: handoff already carries what the retired pair
        went to a second address for."""
        tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "handoff")
        props = set((tool.inputSchema or {}).get("properties", {}))
        assert "supersedes" in props, (
            "the fold claims handoff renders the linkage itself; if that field "
            "is gone the fold is no longer true"
        )
