"""
Drain provenance passthrough — the bridge_core commit path must forward the
PROPOSAL's substrate identity to the Stack, never the drain operator's.

Live specimen this closes (2026-08-03T00:36Z): HQ live-committed six Grok
proposals via `cli --source=grok`. The proposal files carried
source_instance='grok-4.5-mesh-3of3-20260802' and a session_id, but
commit_pending_write forwarded only proposal.arguments — so the two handoff
commits landed in the Stack as source_instance='unknown' under the HQ seat's
live spiral session, and end_bridge_session executed close_session against
the HQ session (advancing its phase) with no trace of the proposer.

Every test here is unit-level on the built REST request body:
  - httpx.post is monkeypatched to a capture stub — the live bridge is NEVER
    called (the ctx URL is unroutable on purpose, belt and suspenders);
  - all queue/audit state lives under tmp_path — nothing touches ~/.sovereign.

Prove-can-fail, both directions:
  - forward direction: on the unfixed tree the source_instance assertions in
    the wire-body tests fail (the body has no source_instance at all);
  - reverse direction: the stale-args test fails if the drain ever lets a
    non-envelope identity (e.g. the operator's) through, and the
    record_insight test fails if injection is ever applied blindly to a
    target whose handler silently drops the kwarg.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bridge_core.context import BridgeContext
from bridge_core.pending_writes import (
    approve_pending_write,
    commit_pending_write,
    create_pending_write,
)
from bridge_core.rings import (
    CANONICAL_COMMIT_TARGETS,
    CANONICAL_RING_1,
    CANONICAL_RING_2,
)

# The proposer, as the live specimen named itself. The drain operator (HQ's
# terminal session) deliberately has NO representation in this file — no
# value of the operator's may ever appear in a built request body.
PROPOSER = "grok-4.5-mesh-3of3-20260802"
PROPOSER_SESSION = "proposer-session-d34db33f"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """Substrate context fully scoped under tmp_path. Never ~/.sovereign."""
    monkeypatch.setenv("TEST_DRAIN_TOKEN", "test-token-not-real")
    return BridgeContext(
        substrate="grok-xai",
        pending_writes_dir=tmp_path / "pending_writes",
        audit_dir=tmp_path / "audit",
        sessions_dir=tmp_path / "sessions",
        ring_1_tools=CANONICAL_RING_1,
        ring_2_tools=CANONICAL_RING_2,
        commit_targets=dict(CANONICAL_COMMIT_TARGETS),
        # Unroutable on purpose: if the capture stub is ever bypassed, the
        # commit errors instead of reaching a real bridge.
        bridge_rest_url="http://127.0.0.1:1",
        bridge_rest_token_env="TEST_DRAIN_TOKEN",
    )


@pytest.fixture
def captured_posts(monkeypatch):
    """Capture httpx.post request bodies; no real request ever leaves."""
    calls: list[dict] = []

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": "stubbed"}

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()

    import bridge_core.pending_writes as pw

    monkeypatch.setattr(pw.httpx, "post", _fake_post)
    return calls


def _drain(ctx, tool, args, *, live=True):
    """Propose → approve → commit, exactly the cli --source=grok drain shape."""
    proposal = create_pending_write(
        ctx,
        tool,
        args,
        source_instance=PROPOSER,
        session_id=PROPOSER_SESSION,
    )
    approve_pending_write(ctx, proposal.proposal_id, approved_by="Anthony")
    return commit_pending_write(ctx, proposal.proposal_id, live=live)


# ── Forward direction: the proposer's identity travels ───────────────────────


def test_handoff_commit_body_carries_proposer_source_instance(ctx, captured_posts):
    _drain(ctx, "handoff", {"note": "mesh lane 3 report landed", "thread": "mesh-20260802"})

    assert len(captured_posts) == 1
    body = captured_posts[0]["json"]
    assert body["tool"] == "handoff"
    # THE fix: without provenance passthrough the Stack handler defaults to
    # "unknown" and the proposer vanishes from the record.
    assert body["arguments"]["source_instance"] == PROPOSER
    # Payload untouched around the injection.
    assert body["arguments"]["note"] == "mesh lane 3 report landed"
    assert body["arguments"]["thread"] == "mesh-20260802"
    # No invented session kwarg: handoff's inputSchema has no session
    # parameter, and the server drops unknown named args SILENTLY — injecting
    # one would be the same fail-open shape this fix closes. The proposer's
    # session identity lives in the proposal envelope + audit trail.
    assert "session_id" not in body["arguments"]
    assert "source_session_id" not in body["arguments"]


def test_end_bridge_session_commit_body_carries_proposer_source_instance(ctx, captured_posts):
    _drain(ctx, "end_bridge_session", {"what_i_learned": "ring 2 drain shape holds"})

    assert len(captured_posts) == 1
    body = captured_posts[0]["json"]
    # end_bridge_session commits as the Stack's close_session.
    assert body["tool"] == "close_session"
    assert body["arguments"]["source_instance"] == PROPOSER
    assert body["arguments"]["what_i_learned"] == "ring 2 drain shape holds"
    assert "session_id" not in body["arguments"]
    assert "source_session_id" not in body["arguments"]


# ── Reverse direction: nothing but the envelope's identity may travel ─────────


def test_envelope_wins_over_stale_source_instance_in_arguments(ctx, captured_posts):
    """A source_instance smuggled inside the args dict never reaches the wire.

    pop_bridge_metadata strips source_instance from args on the SSE/text-relay
    paths, but a proposal file is reviewable state — if one arrives with a
    foreign identity in its arguments (operator's, stale, crafted), the
    envelope's identity-gate value must overwrite it.
    """
    _drain(
        ctx,
        "handoff",
        {
            "note": "identity precedence check",
            "thread": "mesh-20260802",
            "source_instance": "some-other-seat-not-the-proposer",
        },
    )

    body = captured_posts[0]["json"]
    assert body["arguments"]["source_instance"] == PROPOSER
    assert "some-other-seat-not-the-proposer" not in str(body)


def test_record_insight_commit_body_carries_the_proposer(ctx, captured_posts):
    """record_insight JOINED the passthrough set on 2026-08-28.

    This test previously asserted the opposite, and its reasoning was correct
    at the time: the Stack handler read only schema-named args and silently
    dropped the rest, so an injected source_instance would vanish without error
    and the origin carrier had to be line-one self-naming in the body.

    Both halves of that blocker are gone. record_insight declares
    source_instance and forwards it to storage, and the unknown-key guard from
    fix/tool-dispatch-unknown-key-rejection is merged, so an unrecognised kwarg
    now RAISES instead of vanishing. So the drain can carry real provenance, and
    the convention it replaces was never enforceable: two sessions wrote
    byte-identical author lines the same day and nothing in the record could
    separate them.

    What this asserts is the payoff: a proposal from ANY substrate lands in the
    chronicle attributed to the seat that proposed it, not anonymous under the
    drain operator.
    """
    content = f"{PROPOSER}: the envelope now carries the author, not the body"
    _drain(
        ctx,
        "propose_insight",
        {"domain": "mesh-20260802", "content": content, "layer": "reflection"},
    )

    body = captured_posts[0]["json"]
    assert body["tool"] == "record_insight"
    # The PROPOSER's identity travels; the drain operator's never does.
    assert body["arguments"]["source_instance"] == PROPOSER
    # session_id still does NOT travel — the Stack stamps its own spiral session
    # server-side and there is no parameter to inject it into. A made-up kwarg
    # would now raise rather than vanish, which is the point.
    assert "session_id" not in body["arguments"]
    # Layer translation still applies (bridge "reflection" → Stack "hypothesis").
    assert body["arguments"]["layer"] == "hypothesis"
    # The convention the non-injection relies on, held visibly.
    assert body["arguments"]["content"].startswith(PROPOSER)


# ── Review surface: the dry run shows the wire truth ─────────────────────────


def test_dry_run_review_surface_matches_wire(ctx, captured_posts):
    """live=False must preview the body a live commit would send.

    A review surface that differs from the wire is how 'unknown' authorship
    shipped — Anthony approved bodies that looked complete and the wire sent
    something else.
    """
    proposal = _drain(
        ctx,
        "handoff",
        {"note": "dry run truth check", "thread": "mesh-20260802"},
        live=False,
    )

    assert captured_posts == []  # dry run: nothing on the wire
    result = proposal.commit_result
    assert result["live"] is False
    assert result["would_call"] == "handoff"
    assert result["with_arguments"]["source_instance"] == PROPOSER


# ── Schema tripwire: the passthrough set stays true to server.py ─────────────


def test_passthrough_targets_match_server_input_schemas():
    """Every passthrough target must declare source_instance in server.py,
    and record_insight must NOT — if the Stack schema ever changes either
    way, this fails and forces the set to be re-derived, not assumed.

    Parses the schema blocks structurally (python, not grep) from THIS
    tree's server.py.
    """
    # Imported here, not at module top: on the unfixed tree this name does
    # not exist, and the failure should attribute to this test.
    from bridge_core.pending_writes import PROVENANCE_PASSTHROUGH_TARGETS

    server_py = Path(__file__).resolve().parent.parent / "src" / "sovereign_stack" / "server.py"
    text = server_py.read_text()

    def schema_block(tool_name: str) -> str:
        marker = f'name="{tool_name}"'
        start = text.index(marker)
        nxt = text.find('name="', start + len(marker))
        return text[start : nxt if nxt != -1 else len(text)]

    assert {"handoff", "close_session", "record_insight"} == PROVENANCE_PASSTHROUGH_TARGETS
    for target in sorted(PROVENANCE_PASSTHROUGH_TARGETS):
        assert '"source_instance"' in schema_block(target), (
            f"{target} no longer declares source_instance in server.py — "
            "the passthrough set is stale"
        )
    # This assertion used to be inverted — it required record_insight NOT to
    # declare source_instance, and its failure message named the exact remedy:
    # "add it to PROVENANCE_PASSTHROUGH_TARGETS and drop the line-one
    # self-naming carve-out". It fired on 2026-08-28 when the declaration
    # landed, which is a tripwire doing precisely its job. Now inverted to guard
    # the new invariant: the declaration must not silently disappear again.
    assert '"source_instance"' in schema_block("record_insight"), (
        "record_insight no longer declares source_instance — the chronicle has "
        "gone back to storing anonymous entries, and PROVENANCE_PASSTHROUGH_TARGETS "
        "is now injecting a kwarg the server will drop"
    )


# ═══════════════════════════════════════════════════════════════════════════
# THE OPENAI SUBSTRATE — the half this file did not cover
# ═══════════════════════════════════════════════════════════════════════════
#
# Everything above exercises bridge_core, which is the GROK path. Every
# ChatGPT drain runs through clients/openai_bridge/pending_writes.py, a
# separate legacy module that imports nothing from bridge_core — and until
# 2026-08-30 it had neither PROVENANCE_PASSTHROUGH_TARGETS nor any injection
# at commit. v1.21.0's "every Ring-2 commit carries its proposer" was
# therefore true for one substrate and false for the other, and this file's
# own zero occurrences of the string "openai" are why nobody noticed.
#
# The lesson is the file's, not just the module's: a suite that certifies one
# of two substrates reads as certifying the behaviour. Same shape as the
# compass-deny gate, which had to be proven sensitive on BOTH sides for the
# same reason.

OAI_PROPOSER = "chatgpt-gpt-5-6-openai-bridge"
OAI_SESSION = "proposer-session-openai-c0ffee"


@pytest.fixture
def oai(tmp_path, monkeypatch):
    """The openai queue, fully scoped under tmp_path. Never ~/.sovereign.

    This module addresses its stores through module-level constants rather
    than a context object, and `audit.py` imported AUDIT_DIR/AUDIT_LOG BY NAME
    — so all four bindings must be redirected or the test writes into
    Anthony's live queue. Isolate every write path a test can reach, not just
    the obvious one.
    """
    import openai_bridge.audit as oai_audit
    import openai_bridge.hash_chain as oai_hash
    import openai_bridge.pending_writes as oai_pw

    audit_dir = tmp_path / "audit"
    monkeypatch.setattr(oai_pw, "PENDING_DIR", tmp_path / "pending_writes")
    monkeypatch.setattr(oai_hash, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(oai_hash, "AUDIT_LOG", audit_dir / "audit.jsonl")
    monkeypatch.setattr(oai_audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(oai_audit, "AUDIT_LOG", audit_dir / "audit.jsonl")
    # Unroutable on purpose: if the capture stub is ever bypassed, the commit
    # errors instead of reaching the live bridge.
    monkeypatch.setattr(oai_pw, "_BRIDGE_URL", "http://127.0.0.1:1/api/call")
    monkeypatch.setenv("BRIDGE_TOKEN", "test-token-not-real")
    return oai_pw


@pytest.fixture
def oai_captured_posts(monkeypatch):
    calls: list[dict] = []

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": "stubbed"}

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()

    import openai_bridge.pending_writes as oai_pw

    monkeypatch.setattr(oai_pw.httpx, "post", _fake_post)
    return calls


def _oai_drain(oai, tool, args, *, live=True):
    """Propose → approve → commit, exactly the cli --source=openai drain shape."""
    proposal = oai.create_pending_write(
        tool,
        args,
        source_instance=OAI_PROPOSER,
        session_id=OAI_SESSION,
    )
    oai.approve_pending_write(proposal.proposal_id, approved_by="Anthony")
    return oai.commit_pending_write(proposal.proposal_id, live=live)


def test_openai_handoff_commit_body_carries_proposer_source_instance(oai, oai_captured_posts):
    _oai_drain(oai, "handoff", {"note": "chatgpt lane report", "thread": "mesh-20260830"})

    assert len(oai_captured_posts) == 1
    body = oai_captured_posts[0]["json"]
    assert body["tool"] == "handoff"
    assert body["arguments"]["source_instance"] == OAI_PROPOSER
    assert body["arguments"]["note"] == "chatgpt lane report"
    assert "session_id" not in body["arguments"]


def test_openai_close_session_commit_body_carries_proposer_source_instance(oai, oai_captured_posts):
    _oai_drain(oai, "end_bridge_session", {"what_i_learned": "the drain shape holds here too"})

    body = oai_captured_posts[0]["json"]
    assert body["tool"] == "close_session"
    assert body["arguments"]["source_instance"] == OAI_PROPOSER
    assert "session_id" not in body["arguments"]


def test_openai_record_insight_commit_body_carries_the_proposer(oai, oai_captured_posts):
    _oai_drain(
        oai,
        "propose_insight",
        {"domain": "mesh-20260830", "content": "a finding", "layer": "reflection"},
    )

    body = oai_captured_posts[0]["json"]
    assert body["tool"] == "record_insight"
    assert body["arguments"]["source_instance"] == OAI_PROPOSER
    # Layer translation still applies, and now happens in ONE place.
    assert body["arguments"]["layer"] == "hypothesis"


def test_openai_envelope_wins_over_stale_source_instance_in_arguments(oai, oai_captured_posts):
    _oai_drain(
        oai,
        "handoff",
        {
            "note": "identity precedence check",
            "thread": "mesh-20260830",
            "source_instance": "some-other-seat-not-the-proposer",
        },
    )

    body = oai_captured_posts[0]["json"]
    assert body["arguments"]["source_instance"] == OAI_PROPOSER
    assert "some-other-seat-not-the-proposer" not in str(body)


def test_openai_non_passthrough_target_is_left_alone(oai, oai_captured_posts):
    """Injection is target-scoped, not blanket.

    record_open_thread's Stack inputSchema declares no source_instance, and the
    dispatcher now REJECTS unknown keys — so injecting one here would turn a
    good write into a hard failure.
    """
    _oai_drain(
        oai,
        "record_open_thread",
        {"question": "does the port hold?", "context": "c", "domain": "mesh-20260830"},
    )

    body = oai_captured_posts[0]["json"]
    assert body["tool"] == "record_open_thread"
    assert "source_instance" not in body["arguments"]


def test_openai_dry_run_review_surface_matches_wire(oai, oai_captured_posts):
    """The review surface previewed proposal.arguments RAW — untranslated and
    unattributed — while the wire sent something else. Same defect
    bridge_core's docstring names by name."""
    proposal = _oai_drain(
        oai,
        "propose_insight",
        {"domain": "mesh-20260830", "content": "dry run truth", "layer": "reflection"},
        live=False,
    )

    assert oai_captured_posts == []  # dry run: nothing on the wire
    result = proposal.commit_result
    assert result["live"] is False
    assert result["would_call"] == "record_insight"
    assert result["with_arguments"]["source_instance"] == OAI_PROPOSER
    assert result["with_arguments"]["layer"] == "hypothesis"


def test_both_substrates_declare_the_same_passthrough_set():
    """The two modules keep separate literals; they must not drift apart.

    A set that is correct in one substrate and stale in the other is the same
    half-covered shape as the missing port itself.
    """
    from bridge_core.pending_writes import (
        PROVENANCE_PASSTHROUGH_TARGETS as CORE_TARGETS,
    )
    from openai_bridge.pending_writes import (
        PROVENANCE_PASSTHROUGH_TARGETS as OAI_TARGETS,
    )

    assert OAI_TARGETS == CORE_TARGETS
