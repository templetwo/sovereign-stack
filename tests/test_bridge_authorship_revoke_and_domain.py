"""
Three Ring-2 gaps, both substrates, one file — because the last time a
guarantee held on grok and not on openai it stayed half-true for 27 days.

1. FILING TIME NEVER TRAVELLED. A proposal is written at one moment and drained
   at another, routinely days apart: the "does the membrane hold?" proposals are
   from 2026-05-25/27, and the Grok / GPT-5.6 pause acknowledgements sat a full
   day in their queues. Committed bare, every one of those lands in the
   chronicle under the DRAIN OPERATOR's clock. This is the substrate-provenance
   bug one axis over — the proposer's identity was taught to travel on
   2026-08-03 (grok) and 2026-08-30 (openai); their MOMENT was not.

2. A "/" IN A DOMAIN WAS ONLY CAUGHT BY THE FILESYSTEM. Live specimen
   2026-08-28: a propose_learning proposal validated clean, was approved by a
   human, and died at commit as a bare ENOENT, because the Stack turns that
   argument into a shard filename. Detectable when it was typed; reported after
   a human had spent their approval on it, in the vocabulary of a syscall.

3. AN APPROVAL COULD NOT BE TAKEN BACK. The only edges out of `approved` were
   `committed` and `commit_failed`. `bridge reject` answered "Cannot reject
   proposal in status 'approved'", so nine approved-never-committed test
   proposals had no exit at all.

Every test is unit-level on the built REST body or the proposal file. httpx.post
is monkeypatched to a capture stub, the bridge URL is unroutable on purpose, and
every queue/audit binding lives under tmp_path — openai_bridge needs all five
rebindings, because audit.py from-imports two of them BY VALUE.
"""

from __future__ import annotations

import pytest
from bridge_core.context import BridgeContext
from bridge_core.pending_writes import (
    ValidationError,
    approve_pending_write,
    commit_pending_write,
    create_pending_write,
    reject_pending_write,
)
from bridge_core.rings import (
    CANONICAL_COMMIT_TARGETS,
    CANONICAL_RING_1,
    CANONICAL_RING_2,
)

PROPOSER = "grok-4.5-mesh-3of3-20260802"
PROPOSER_SESSION = "proposer-session-d34db33f"


# ── bridge_core (grok) scaffolding ───────────────────────────────────────────


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_DRAIN_TOKEN", "test-token-not-real")
    return BridgeContext(
        substrate="grok-xai",
        pending_writes_dir=tmp_path / "pending_writes",
        audit_dir=tmp_path / "audit",
        sessions_dir=tmp_path / "sessions",
        ring_1_tools=CANONICAL_RING_1,
        ring_2_tools=CANONICAL_RING_2,
        commit_targets=dict(CANONICAL_COMMIT_TARGETS),
        bridge_rest_url="http://127.0.0.1:1",
        bridge_rest_token_env="TEST_DRAIN_TOKEN",
    )


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "result": "stubbed"}


@pytest.fixture
def captured_posts(monkeypatch):
    calls: list[dict] = []

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()

    import bridge_core.pending_writes as pw

    monkeypatch.setattr(pw.httpx, "post", _fake_post)
    return calls


def _propose(ctx, tool, args):
    return create_pending_write(
        ctx, tool, args, source_instance=PROPOSER, session_id=PROPOSER_SESSION
    )


def _drain(ctx, tool, args):
    p = _propose(ctx, tool, args)
    approve_pending_write(ctx, p.proposal_id, approved_by="Anthony")
    commit_pending_write(ctx, p.proposal_id, live=True)
    return p


# ── openai_bridge scaffolding ────────────────────────────────────────────────


@pytest.fixture
def oai(tmp_path, monkeypatch):
    """All FIVE bindings, because audit.py from-imports two of them by value.
    Four is not enough and looks identical from inside the test."""
    import openai_bridge.audit as oai_audit
    import openai_bridge.hash_chain as oai_hash
    import openai_bridge.pending_writes as oai_pw

    audit_dir = tmp_path / "audit"
    monkeypatch.setattr(oai_pw, "PENDING_DIR", tmp_path / "pending_writes")
    monkeypatch.setattr(oai_hash, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(oai_hash, "AUDIT_LOG", audit_dir / "audit.jsonl")
    monkeypatch.setattr(oai_audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(oai_audit, "AUDIT_LOG", audit_dir / "audit.jsonl")
    monkeypatch.setattr(oai_pw, "_BRIDGE_URL", "http://127.0.0.1:1/api/call")
    monkeypatch.setenv("BRIDGE_TOKEN", "test-token-not-real")
    return oai_pw


@pytest.fixture
def oai_posts(monkeypatch):
    calls: list[dict] = []

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()

    import openai_bridge.pending_writes as oai_pw

    monkeypatch.setattr(oai_pw.httpx, "post", _fake_post)
    return calls


def _oai_drain(oai, tool, args):
    p = oai.create_pending_write(tool, args, source_instance="gpt-5.6-bridge-seat")
    oai.approve_pending_write(p.proposal_id, approved_by="Anthony")
    oai.commit_pending_write(p.proposal_id, live=True)
    return p


# ═══ 1. The filing time travels ══════════════════════════════════════════════


class TestFilingTimeTravels:
    def test_grok_commit_carries_the_proposals_timestamp(self, ctx, captured_posts):
        p = _drain(ctx, "propose_insight", {"domain": "d", "content": "c"})
        body = captured_posts[0]["json"]
        assert body["tool"] == "record_insight"
        assert body["arguments"]["original_timestamp"] == p.timestamp

    def test_openai_commit_carries_the_proposals_timestamp(self, oai, oai_posts):
        p = _oai_drain(oai, "propose_insight", {"domain": "d", "content": "c"})
        body = oai_posts[0]["json"]
        assert body["tool"] == "record_insight"
        assert body["arguments"]["original_timestamp"] == p.timestamp

    def test_the_proposals_own_value_wins(self, ctx, captured_posts):
        """An explicit authorship time from the proposer is better evidence
        than the envelope's filing stamp, which is only the best proxy."""
        _drain(
            ctx,
            "propose_insight",
            {"domain": "d", "content": "c", "original_timestamp": "2025-03-04"},
        )
        assert captured_posts[0]["json"]["arguments"]["original_timestamp"] == "2025-03-04"

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            ("handoff", {"note": "n", "thread": "t"}),
            ("end_bridge_session", {"what_i_learned": "x"}),
        ],
    )
    def test_never_injected_into_a_target_that_does_not_declare_it(
        self, ctx, captured_posts, tool, args
    ):
        """THE SAME DISTINCTION VERIFIED_BY_TARGETS EXISTS FOR. Since
        _reject_unknown_params, forwarding an undeclared parameter turns every
        commit carrying it into a hard Stack-side error — a too-wide target set
        would swap a silent gap for a loud outage on handoff and close_session."""
        _drain(ctx, tool, args)
        assert "original_timestamp" not in captured_posts[0]["json"]["arguments"]

    def test_openai_never_injects_it_into_handoff_either(self, oai, oai_posts):
        _oai_drain(oai, "handoff", {"note": "n", "thread": "t"})
        assert "original_timestamp" not in oai_posts[0]["json"]["arguments"]

    @pytest.mark.parametrize("tool", ["propose_insight", "handoff"])
    def test_proposal_id_is_never_injected(self, ctx, captured_posts, tool):
        """DELIBERATE, and asserted rather than left to a comment: no Ring-2
        commit target declares proposal_id, so injecting it would make every
        drained proposal fail on the Stack with 'unknown parameter'."""
        args = {"domain": "d", "content": "c"} if tool == "propose_insight" else {"note": "n"}
        _drain(ctx, tool, args)
        assert "proposal_id" not in captured_posts[0]["json"]["arguments"]

    def test_the_payload_is_otherwise_untouched(self, ctx, captured_posts):
        _drain(ctx, "propose_insight", {"domain": "dom", "content": "body", "intensity": 0.8})
        got = captured_posts[0]["json"]["arguments"]
        assert got["domain"] == "dom"
        assert got["content"] == "body"
        assert got["intensity"] == 0.8
        assert got["source_instance"] == PROPOSER

    def test_the_dry_run_preview_shows_the_same_body_the_wire_would_send(self, ctx):
        """One assembly point, two surfaces, no drift — the review surface must
        not differ from the wire, which is how 'unknown' authorship shipped."""
        from bridge_core.pending_writes import build_commit_arguments

        p = _propose(ctx, "propose_insight", {"domain": "d", "content": "c"})
        assert build_commit_arguments(ctx, p)["original_timestamp"] == p.timestamp


# ═══ 2. A domain is a label, refused when it is typed ════════════════════════

BAD = ["a/b", "..", ".", ".hidden", "a\\b"]


class TestDomainGateAtProposalTime:
    @pytest.mark.parametrize("bad", BAD)
    def test_grok_refuses_a_path_shaped_learning_domain(self, ctx, bad):
        """THE 2026-08-28 SPECIMEN, refused at the moment it was typed."""
        with pytest.raises(ValidationError) as exc:
            _propose(
                ctx,
                "propose_learning",
                {"situation": "s", "what_happened": "w", "what_learned": "l", "applies_to": bad},
            )
        assert "label, not a path" in str(exc.value)

    @pytest.mark.parametrize("bad", BAD)
    def test_grok_refuses_a_path_shaped_insight_domain(self, ctx, bad):
        with pytest.raises(ValidationError):
            _propose(ctx, "propose_insight", {"domain": bad, "content": "c"})

    @pytest.mark.parametrize("bad", BAD)
    def test_openai_refuses_the_same_values(self, oai, bad):
        """A guarantee that holds on one substrate is not a guarantee."""
        with pytest.raises(oai.ValidationError):
            oai.create_pending_write(
                "propose_insight", {"domain": bad, "content": "c"}, source_instance="gpt-5.6"
            )

    def test_a_refused_proposal_writes_no_file(self, ctx, tmp_path):
        with pytest.raises(ValidationError):
            _propose(ctx, "propose_insight", {"domain": "a/b", "content": "c"})
        assert not list((tmp_path / "pending_writes").glob("*.json"))

    def test_the_error_names_the_value_and_the_fix(self, ctx):
        """An ENOENT names a syscall. This must name the mistake."""
        with pytest.raises(ValidationError) as exc:
            _propose(ctx, "propose_insight", {"domain": "tech-debt/compaction", "content": "c"})
        assert "tech-debt/compaction" in str(exc.value)
        assert "commas for compound tags" in str(exc.value)

    @pytest.mark.parametrize("good", ["general", "a,b,c", "sovereign-stack", "v4.4-scout", "a.b"])
    def test_ordinary_labels_still_pass(self, ctx, good):
        """Law #2 from the other side: a gate never shown to PASS is over-tight,
        and one tighter than storage refuses writes the Stack would accept."""
        assert _propose(ctx, "propose_insight", {"domain": good, "content": "c"})

    def test_an_absent_domain_is_not_an_error(self, ctx):
        """record_open_thread defaults its domain upstream; refusing an absent
        one here would be a gate inventing a requirement the Stack has not."""
        assert _propose(ctx, "record_open_thread", {"question": "q?"})

    def test_the_gate_matches_the_storage_gate_exactly(self, ctx):
        """Drift in either direction reintroduces the surprise: looser lets the
        class through, tighter refuses writes the Stack would have taken."""
        from bridge_core.target_risk import domain_label_errors

        from sovereign_stack.memory import _validate_domain_label

        for value in [*BAD, "general", "a,b,c", "a.b", "v4.4-scout"]:
            storage_refuses = False
            try:
                _validate_domain_label(value)
            except ValueError:
                storage_refuses = True
            bridge_refuses = bool(domain_label_errors("propose_insight", {"domain": value}))
            assert storage_refuses == bridge_refuses, value


# ═══ 3. An approval can be withdrawn, on purpose, on the record ══════════════


class TestRevokeApproval:
    def _approved(self, ctx):
        p = _propose(ctx, "propose_insight", {"domain": "d", "content": "c"})
        approve_pending_write(ctx, p.proposal_id, approved_by="Anthony")
        return p

    def test_default_reject_still_refuses_an_approved_proposal(self, ctx):
        """The historical wording is preserved as the prefix — operators and
        any caller matching on it must still recognise the refusal."""
        p = self._approved(ctx)
        with pytest.raises(ValueError) as exc:
            reject_pending_write(ctx, p.proposal_id, "no", rejected_by="HQ")
        assert str(exc.value).startswith("Cannot reject proposal in status 'approved'")

    def test_the_refusal_now_points_at_the_flag(self, ctx):
        p = self._approved(ctx)
        with pytest.raises(ValueError, match="--revoke-approval"):
            reject_pending_write(ctx, p.proposal_id, "no", rejected_by="HQ")

    def test_revoke_succeeds_and_the_proposal_is_rejected(self, ctx):
        p = self._approved(ctx)
        out = reject_pending_write(
            ctx,
            p.proposal_id,
            "retiring stale test proposals",
            rejected_by="HQ",
            revoke_approval=True,
        )
        assert out.status == "rejected"
        assert out.reviewed_by == "HQ"
        assert out.revision_notes == "retiring stale test proposals"

    def test_the_chain_still_verifies_after_a_revoke(self, ctx):
        from bridge_core import verify_chain

        p = self._approved(ctx)
        reject_pending_write(ctx, p.proposal_id, "r", rejected_by="HQ", revoke_approval=True)
        ok, msg = verify_chain(ctx)
        assert ok, msg

    def test_the_audit_entry_names_it_a_revocation(self, ctx):
        from bridge_core import read_audit_trail

        p = self._approved(ctx)
        reject_pending_write(ctx, p.proposal_id, "r", rejected_by="HQ", revoke_approval=True)
        events = list(read_audit_trail(ctx, proposal_id=p.proposal_id))
        kinds = [e.get("event_type") or e.get("event") for e in events]
        assert "approval_revoked" in kinds
        assert "rejected" not in kinds

    def test_the_audit_entry_preserves_the_approval_it_revoked(self, ctx):
        """The three fields the reject stamp is about to overwrite. Without
        them the chain cannot say who had once said yes, or when — a revocation
        that looked like an ordinary rejection would erase that."""
        from bridge_core import read_audit_trail

        p = _propose(ctx, "propose_insight", {"domain": "d", "content": "c"})
        approved_at = approve_pending_write(ctx, p.proposal_id, approved_by="Anthony").reviewed_at
        assert approved_at
        reject_pending_write(ctx, p.proposal_id, "r", rejected_by="HQ", revoke_approval=True)
        entry = next(
            e
            for e in read_audit_trail(ctx, proposal_id=p.proposal_id)
            if (e.get("event_type") or e.get("event")) == "approval_revoked"
        )
        details = entry["details"]
        assert details["prior_status"] == "approved"
        assert details["prior_reviewed_by"] == "Anthony"
        assert details["prior_reviewed_at"] == approved_at
        assert details["resulting_status"] == "rejected"

    def test_a_pending_proposal_still_takes_the_ordinary_path(self, ctx):
        """The flag must not silently relabel every rejection as a revocation."""
        from bridge_core import read_audit_trail

        p = _propose(ctx, "propose_insight", {"domain": "d", "content": "c"})
        reject_pending_write(ctx, p.proposal_id, "r", rejected_by="HQ", revoke_approval=True)
        kinds = [
            (e.get("event_type") or e.get("event"))
            for e in read_audit_trail(ctx, proposal_id=p.proposal_id)
        ]
        assert "rejected" in kinds
        assert "approval_revoked" not in kinds

    def test_committed_is_never_revocable(self, ctx, captured_posts):
        """That write is already in the chronicle. A queue saying 'rejected'
        over a chronicle saying 'written' is a worse record than no edge."""
        p = self._approved(ctx)
        commit_pending_write(ctx, p.proposal_id, live=True)
        with pytest.raises(ValueError, match="already in the chronicle"):
            reject_pending_write(ctx, p.proposal_id, "r", rejected_by="HQ", revoke_approval=True)

    def test_a_revoke_does_not_touch_commit_result(self, ctx):
        """commit_result is the evidence of what happened at the Stack; a
        revocation must not edit that history."""
        p = self._approved(ctx)
        out = reject_pending_write(ctx, p.proposal_id, "r", rejected_by="HQ", revoke_approval=True)
        assert out.commit_result is None

    def test_revoke_still_requires_a_named_reviewer(self, ctx):
        p = self._approved(ctx)
        with pytest.raises(ValueError):
            reject_pending_write(ctx, p.proposal_id, "r", rejected_by="", revoke_approval=True)


class TestRevokeApprovalOpenAI:
    """A guarantee that holds on one substrate is a guarantee-shaped belief."""

    def _approved(self, oai):
        p = oai.create_pending_write(
            "propose_insight", {"domain": "d", "content": "c"}, source_instance="gpt-5.6"
        )
        oai.approve_pending_write(p.proposal_id, approved_by="Anthony")
        return p

    def test_default_reject_still_refuses(self, oai):
        p = self._approved(oai)
        with pytest.raises(ValueError) as exc:
            oai.reject_pending_write(p.proposal_id, "no", rejected_by="HQ")
        assert str(exc.value).startswith("Cannot reject proposal in status 'approved'")

    def test_revoke_succeeds_and_the_chain_verifies(self, oai):
        from openai_bridge.hash_chain import verify_chain

        p = self._approved(oai)
        out = oai.reject_pending_write(
            p.proposal_id, "retiring stale test proposals", rejected_by="HQ", revoke_approval=True
        )
        assert out.status == "rejected"
        ok, msg = verify_chain()
        assert ok, msg

    def test_the_audit_entry_names_the_revocation_and_keeps_the_approval(self, oai):
        from openai_bridge.audit import read_audit_trail

        p = self._approved(oai)
        oai.reject_pending_write(p.proposal_id, "r", rejected_by="HQ", revoke_approval=True)
        entry = next(
            e
            for e in read_audit_trail(proposal_id=p.proposal_id)
            if (e.get("event_type") or e.get("event")) == "approval_revoked"
        )
        assert entry["details"]["prior_reviewed_by"] == "Anthony"
        assert entry["details"]["prior_status"] == "approved"

    def test_committed_is_never_revocable(self, oai, oai_posts):
        p = self._approved(oai)
        oai.commit_pending_write(p.proposal_id, live=True)
        with pytest.raises(ValueError, match="already in the chronicle"):
            oai.reject_pending_write(p.proposal_id, "r", rejected_by="HQ", revoke_approval=True)


class TestTheConsolesExposeIt:
    """A capability absent from the console is unreachable by the human it
    exists for — the bridge-blindness lesson, one layer out."""

    def test_grok_console_declares_the_flag(self):
        from bridge_core.cli import reject as grok_reject

        assert any(o.name == "revoke_approval" for o in grok_reject.params)

    def test_openai_console_declares_the_flag(self):
        from openai_bridge.cli import reject as oai_reject

        assert any(o.name == "revoke_approval" for o in oai_reject.params)

    @pytest.mark.parametrize("mod", ["bridge_core.cli", "openai_bridge.cli"])
    def test_the_flag_is_off_by_default(self, mod):
        """Un-approving is a governance act, not a default."""
        import importlib

        cmd = importlib.import_module(mod).reject
        flag = next(o for o in cmd.params if o.name == "revoke_approval")
        assert flag.default is False
        assert flag.is_flag


class TestTheConsoleLabelsARevocationCorrectly:
    """The console must SAY it revoked an approval. The label is display-only,
    which is precisely why it degrades silently when its status probe is wrong
    — the operator then reads "Rejected" for a governance act on a human's
    decision and has no way to tell the difference."""

    def _ops(self, ctx):
        from bridge_core.cli import _SubstrateOps

        ops = _SubstrateOps.__new__(_SubstrateOps)
        ops._list = lambda status: __import__(
            "bridge_core.pending_writes", fromlist=["list_pending_writes"]
        ).list_pending_writes(ctx, status=status)
        return ops

    def test_the_status_probe_finds_an_approved_proposal(self, ctx):
        """`ops.list("all")` filters for a status literally equal to "all" and
        returns nothing — the list COMMAND maps "all" -> None before calling,
        but the ops shim forwards its argument raw."""
        from bridge_core.cli import _status_of

        p = _propose(ctx, "propose_insight", {"domain": "d", "content": "c"})
        approve_pending_write(ctx, p.proposal_id, approved_by="Anthony")
        assert _status_of(self._ops(ctx), p.proposal_id) == "approved"

    def test_the_probe_degrades_rather_than_raising(self, ctx):
        """Display only: a read failure must cost the label, never the
        operation."""
        from bridge_core.cli import _status_of

        class _Broken:
            def list(self, status):
                raise RuntimeError("queue unreadable")

        assert _status_of(_Broken(), "whatever") is None

    def test_an_unknown_id_probes_to_none(self, ctx):
        from bridge_core.cli import _status_of

        assert _status_of(self._ops(ctx), "no-such-proposal") is None
