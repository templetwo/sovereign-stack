"""
Ring 2 reviewer identity is an ASSERTION, never a default.

The defect this closes (verified on disk 2026-08-28): every review entry point
in both bridge libraries defaulted the reviewer name to the human's —

    approve_pending_write(..., approved_by: str = "Anthony")
    reject_pending_write(...,  rejected_by: str = "Anthony")
    needs_revision_pending_write(..., actor: str = "Anthony")
    @click.option("--by", default="Anthony")      # x6, both consoles

— so any automated caller that omitted the name was stamped as Anthony, and
`bridge show` renders "Reviewed: Anthony at ..." identically for a human
decision and for a test fixture. This is the fail-open class exactly: the
surface reports a human decision that never happened, and the human cannot
tell from the record.

Measured read-only on the live openai queue 2026-08-28, by
`reviewed_by == "Anthony"` AND `reviewed_at - timestamp < 1s`: 37 stamped
proposals, of which 27 are sub-second. 24 of those 27 carry this repo's own
fixture strings verbatim ("Test: does the membrane hold?" x12, "test handoff
for rejection" x12) across runs on 2026-05-08, 05-25 and 05-27. The other 3
sub-second ones carry REAL content, not fixtures — machine-speed approvals
wearing the human's name.

Prove-can-fail (experimental law #2). On the UNFIXED tree 29 of these 32
fail — 23 failed, 6 errored:
  - the bridge_core reviewer-required tests: the call succeeds and stamps
    "Anthony", so pytest.raises finds no exception;
  - the CLI tests: click supplies the default, the command runs, and exits 1
    from the not-found handler instead of 2 from click's own refusal;
  - the isolation tests: `isolated_queue` / `ISOLATED_NAMES` do not exist;
  - the openai tests ERROR rather than fail, at fixture import, because that
    fixture uses `isolated_queue`. They are therefore coupled to the helper
    and do NOT independently witness the openai default. The receipt that
    does is a subprocess run of the unfixed smoke test under a redirected
    HOME: it created
    `<HOME>/.sovereign/openai_bridge/pending_writes/*.json` with
    `"reviewed_by": "Anthony"` x2. See the commit message.

The remaining 3 pass on the unfixed tree by design — they are
reverse-direction regression tests (a supplied name survives; the audit event
carries it; the hash chain is undisturbed), not gates.

Nothing here touches ~/.sovereign. Queue and audit state live under tmp_path;
the openai library's module-level paths are rebound by fixture.
"""

from __future__ import annotations

import pytest
from bridge_core.context import BridgeContext
from bridge_core.pending_writes import (
    approve_pending_write,
    create_pending_write,
    needs_revision_pending_write,
    reject_pending_write,
)
from bridge_core.rings import (
    CANONICAL_COMMIT_TARGETS,
    CANONICAL_RING_1,
    CANONICAL_RING_2,
)
from click.testing import CliRunner

PROPOSER = "grok-4.5-reviewer-identity-test"
# A machine reviewer, named as a machine. The point of the fix is that this
# value has to be supplied; no test here may rely on a name it did not pass.
ROBOT = "ring2-identity-test-harness"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """Substrate context fully scoped under tmp_path. Never ~/.sovereign."""
    monkeypatch.setenv("TEST_REVIEWER_TOKEN", "test-token-not-real")
    return BridgeContext(
        substrate="grok-xai",
        pending_writes_dir=tmp_path / "pending_writes",
        audit_dir=tmp_path / "audit",
        sessions_dir=tmp_path / "sessions",
        ring_1_tools=CANONICAL_RING_1,
        ring_2_tools=CANONICAL_RING_2,
        commit_targets=dict(CANONICAL_COMMIT_TARGETS),
        bridge_rest_url="http://127.0.0.1:1",  # unroutable on purpose
        bridge_rest_token_env="TEST_REVIEWER_TOKEN",
    )


def _proposal(ctx):
    """File one ordinary pending proposal in the sandboxed queue."""
    return create_pending_write(
        ctx,
        "record_open_thread",
        {
            "question": "Is reviewer identity asserted?",
            "context": "reviewer identity test",
            "domain": "bridge-review",
        },
        source_instance=PROPOSER,
    )


# ── (i) bridge_core: the reviewer must be named ──────────────────────────────


def test_approve_without_reviewer_is_a_type_error(ctx):
    """Omitting the reviewer must be impossible, not silently attributed."""
    p = _proposal(ctx)
    with pytest.raises(TypeError):
        approve_pending_write(ctx, p.proposal_id)


def test_reject_without_reviewer_is_a_type_error(ctx):
    p = _proposal(ctx)
    with pytest.raises(TypeError):
        reject_pending_write(ctx, p.proposal_id, "no reason given")


def test_needs_revision_without_actor_is_a_type_error(ctx):
    p = _proposal(ctx)
    with pytest.raises(TypeError):
        needs_revision_pending_write(ctx, p.proposal_id, "notes")


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n  "])
def test_approve_refuses_blank_reviewer(ctx, blank):
    """A blank name is the same lie with less confidence. Refuse it."""
    p = _proposal(ctx)
    with pytest.raises(ValueError, match="approved_by"):
        approve_pending_write(ctx, p.proposal_id, approved_by=blank)


@pytest.mark.parametrize("blank", ["", "   "])
def test_reject_refuses_blank_reviewer(ctx, blank):
    p = _proposal(ctx)
    with pytest.raises(ValueError, match="rejected_by"):
        reject_pending_write(ctx, p.proposal_id, "reason", rejected_by=blank)


@pytest.mark.parametrize("blank", ["", "   "])
def test_needs_revision_refuses_blank_actor(ctx, blank):
    p = _proposal(ctx)
    with pytest.raises(ValueError, match="actor"):
        needs_revision_pending_write(ctx, p.proposal_id, "notes", actor=blank)


def test_refusal_happens_before_any_mutation(ctx):
    """
    The guard runs first. A refused approve must leave the proposal pending —
    otherwise the fix would trade a false reviewer for a corrupted status.
    """
    p = _proposal(ctx)
    with pytest.raises(ValueError):
        approve_pending_write(ctx, p.proposal_id, approved_by="  ")

    from bridge_core.pending_writes import list_pending_writes

    (still,) = [d for d in list_pending_writes(ctx) if d["proposal_id"] == p.proposal_id]
    assert still["status"] == "pending"


def test_named_reviewer_is_recorded_verbatim(ctx):
    """The reverse direction: a supplied name must survive to the record."""
    p = _proposal(ctx)
    approved = approve_pending_write(ctx, p.proposal_id, approved_by=ROBOT)
    assert approved.reviewed_by == ROBOT
    assert approved.status == "approved"


def test_reviewer_name_is_stripped_not_mangled(ctx):
    p = _proposal(ctx)
    approved = approve_pending_write(ctx, p.proposal_id, approved_by=f"  {ROBOT}  ")
    assert approved.reviewed_by == ROBOT


def test_approve_audit_event_carries_the_named_actor(ctx):
    """The audit trail is the durable half — it must not say 'Anthony' either."""
    p = _proposal(ctx)
    approve_pending_write(ctx, p.proposal_id, approved_by=ROBOT)

    from bridge_core.audit import read_audit_trail

    events = read_audit_trail(ctx, proposal_id=p.proposal_id)
    approvals = [e for e in events if e["event_type"] == "approved"]
    assert approvals, "no approved event was written"
    assert all(e["actor"] == ROBOT for e in approvals)
    assert not any(e["actor"] == "Anthony" for e in events)


def test_reviewer_identity_does_not_disturb_the_hash_chain(ctx):
    """
    reviewed_by is a lifecycle mutable; chain_valid is computed against the
    creation-time snapshot. Changing who reviews must not read as tampering.
    """
    from bridge_core.pending_writes import get_proposal_by_id

    p = _proposal(ctx)
    approve_pending_write(ctx, p.proposal_id, approved_by=ROBOT)
    verified = get_proposal_by_id(ctx, p.proposal_id)
    assert verified["found"] is True
    assert verified["chain_valid"] is True


# ── (i) openai_bridge: the same guard, in the legacy library ─────────────────
#
# Imported under the `clients.` name, the same one _smoke_test.py uses. This
# package is reachable under two names ("openai_bridge" from the installed
# distribution, "clients.openai_bridge" from the repo root) and each is a
# separate module object with its own globals — mixing them would leave one
# copy pointed at ~/.sovereign.


@pytest.fixture
def openai_queue(tmp_path):
    """Rebind every live-path global in the openai library onto tmp_path."""
    from clients.openai_bridge._smoke_test import isolated_queue

    with isolated_queue(tmp_path / "openai") as sandbox:
        yield sandbox


def _openai_proposal():
    from clients.openai_bridge.pending_writes import create_pending_write as _create

    return _create(
        "record_open_thread",
        {
            "question": "Is reviewer identity asserted?",
            "context": "reviewer identity test",
            "domain": "bridge-review",
        },
        source_instance=PROPOSER,
    )


def test_openai_approve_without_reviewer_is_a_type_error(openai_queue):
    from clients.openai_bridge.pending_writes import approve_pending_write as _approve

    p = _openai_proposal()
    with pytest.raises(TypeError):
        _approve(p.proposal_id)


def test_openai_reject_without_reviewer_is_a_type_error(openai_queue):
    from clients.openai_bridge.pending_writes import reject_pending_write as _reject

    p = _openai_proposal()
    with pytest.raises(TypeError):
        _reject(p.proposal_id, "no reason given")


def test_openai_needs_revision_without_actor_is_a_type_error(openai_queue):
    from clients.openai_bridge.pending_writes import (
        needs_revision_pending_write as _needs_revision,
    )

    p = _openai_proposal()
    with pytest.raises(TypeError):
        _needs_revision(p.proposal_id, "notes")


@pytest.mark.parametrize("blank", ["", "   "])
def test_openai_approve_refuses_blank_reviewer(openai_queue, blank):
    from clients.openai_bridge.pending_writes import approve_pending_write as _approve

    p = _openai_proposal()
    with pytest.raises(ValueError, match="approved_by"):
        _approve(p.proposal_id, approved_by=blank)


def test_openai_named_reviewer_is_recorded_verbatim(openai_queue):
    from clients.openai_bridge.pending_writes import approve_pending_write as _approve

    p = _openai_proposal()
    approved = _approve(p.proposal_id, approved_by=ROBOT)
    assert approved.reviewed_by == ROBOT


# ── (ii) the consoles refuse without --by ────────────────────────────────────
#
# HOME is redirected as belt-and-braces: click runs the group callback before
# it parses subcommand params, so _SubstrateOps.__init__ executes even on the
# runs that are about to be refused.


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    return CliRunner()


@pytest.mark.parametrize(
    ("argv", "label"),
    [
        (["approve", "deadbeef"], "approve"),
        (["reject", "deadbeef", "--reason", "r"], "reject"),
        (["needs-revision", "deadbeef", "--notes", "n"], "needs-revision"),
    ],
)
def test_bridge_console_refuses_without_by(runner, argv, label):
    """
    Exit 2 is click's own usage refusal. Exit 1 would mean the command RAN
    and failed later — which is what the defaulted build did.
    """
    from bridge_core.cli import cli

    result = runner.invoke(cli, argv)
    assert result.exit_code == 2, f"{label}: {result.output}"
    assert "--by" in result.output


@pytest.mark.parametrize(
    ("argv", "label"),
    [
        (["approve", "deadbeef"], "approve"),
        (["reject", "deadbeef", "--reason", "r"], "reject"),
        (["needs-revision", "deadbeef", "--notes", "n"], "needs-revision"),
    ],
)
def test_openai_console_refuses_without_by(runner, argv, label):
    from clients.openai_bridge.cli import cli as openai_cli

    result = runner.invoke(openai_cli, argv)
    assert result.exit_code == 2, f"{label}: {result.output}"
    assert "--by" in result.output


def test_no_console_option_defaults_to_a_human_name():
    """
    Structural backstop: grep the declarations themselves. A future edit that
    reintroduces `default="Anthony"` on any console option fails here even if
    it never reaches a code path a behavioural test covers.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for rel in ("clients/bridge_core/cli.py", "clients/openai_bridge/cli.py"):
        text = (root / rel).read_text()
        assert 'default="Anthony"' not in text, f"{rel} reintroduced a defaulted reviewer"


# ── (iii) the openai smoke test cannot reach the live queue ──────────────────


def test_smoke_test_isolation_moves_every_live_path(tmp_path):
    """
    All five names must move together. audit.py does
    `from .hash_chain import AUDIT_DIR, AUDIT_LOG`, which copies the VALUES
    into audit's own namespace at import — patch hash_chain alone and
    append_audit_event keeps writing to the live audit log while the run still
    prints PASS.

    Five is the set the smoke test's code paths REACH, not every live-path
    global in the package: cli.py:36-37 define their own PENDING_DIR and
    AUDIT_LOG and are not isolated. The smoke test never imports cli.py, so
    that is sound today; a future test that does must extend ISOLATED_NAMES,
    and this equality assertion is what will make it say so.
    """
    from clients.openai_bridge._smoke_test import ISOLATED_NAMES, isolated_queue

    covered = {(mod.__name__, attr) for mod, attr, _ in ISOLATED_NAMES}
    assert covered == {
        ("clients.openai_bridge.pending_writes", "PENDING_DIR"),
        ("clients.openai_bridge.hash_chain", "AUDIT_DIR"),
        ("clients.openai_bridge.hash_chain", "AUDIT_LOG"),
        ("clients.openai_bridge.audit", "AUDIT_DIR"),
        ("clients.openai_bridge.audit", "AUDIT_LOG"),
    }

    sandbox = tmp_path / "sandbox"
    originals = {(m.__name__, a): getattr(m, a) for m, a, _ in ISOLATED_NAMES}

    with isolated_queue(sandbox):
        for mod, attr, _ in ISOLATED_NAMES:
            value = getattr(mod, attr)
            assert sandbox in value.parents or value == sandbox, (
                f"{mod.__name__}.{attr} = {value} is outside the sandbox"
            )

    for mod, attr, _ in ISOLATED_NAMES:
        assert getattr(mod, attr) == originals[(mod.__name__, attr)], (
            f"{mod.__name__}.{attr} was not restored"
        )


def test_smoke_test_paths_are_never_under_dot_sovereign(tmp_path):
    """The named defect: fixtures landing in ~/.sovereign/openai_bridge/."""
    from pathlib import Path

    from clients.openai_bridge._smoke_test import ISOLATED_NAMES, isolated_queue

    live_root = Path.home() / ".sovereign"
    with isolated_queue(tmp_path / "sandbox"):
        for mod, attr, _ in ISOLATED_NAMES:
            value = getattr(mod, attr)
            assert live_root not in value.parents, (
                f"{mod.__name__}.{attr} still resolves under {live_root}"
            )


def test_smoke_test_does_not_assert_the_human_as_its_reviewer():
    """
    A test that names Anthony as its reviewer cannot tell a real approval from
    its own fixture — which is how nine fixtures came to wear his name.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for rel in (
        "clients/openai_bridge/_smoke_test.py",
        "clients/grok_bridge/_smoke_test.py",
    ):
        text = (root / rel).read_text()
        assert 'reviewed_by == "Anthony"' not in text, f"{rel} still asserts the human's name"
