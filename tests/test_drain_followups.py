"""
The four defects the 2026-08-30 Ring-2 drain surfaced.

Each is the same family the house keeps finding: a surface that reports the
wrong thing confidently, or a transition with no recorded way to happen.

  1. record_learning derived its shard FILENAME from `applies_to`, caller prose.
     "... any remote/schema-constrained seat" became a path, and the write died
     with [Errno 2] against a directory that never existed — surfaced as a
     failed commit of proposal 4de1d36f.

     The sweep for the same shape found record_open_thread with the identical
     hole and the OPPOSITE correct remedy, and that asymmetry is the finding:

       * filename IS the retrieval key -> REJECT. record_insight closed this at
         memory.py:961 (mesh-20260719) and record_open_thread was never swept.
         resolve_thread re-derives `threads_dir/{domain}.jsonl` verbatim, so a
         renamed key files the thread where no exact-domain query looks.
       * filename is only a shard LABEL -> SANITIZE, raw value in the body.
         record_learning is read by check_mistakes, which globs *.jsonl and
         matches the RAW `applies_to` field. Nothing resolves the filename, so
         sanitizing loses nothing and refusing would reject a good write.

  2. The commit console printed "Proposal REJECTED by Stack ... NOT committed"
     and a bright-green "COMMITTED (LIVE)" banner for the SAME proposal, one
     line apart. commit_pending_write fails closed and RETURNS (the failure is
     a recorded state, not an exception); the console fell through to success.

  3. reject_pending_write accepts only pending/needs_revision, so an APPROVED
     proposal could never be adjudicated away — the nine machine-approved smoke
     fixtures in the live openai queue had no recorded exit. commit_failed had
     the same shape one status over (retry sends it back to approved: a loop).

  4. `bridge commit` took no --by, so every COMMITTED audit event landed
     actor="bridge" — the one link in an otherwise fully-attributed chain that
     named nobody.

PROVE-CAN-FAIL (experimental law #2). Captured on pristine 52f1f1b in a
detached worktree, this file copied in and nothing else changed:
42 failed, 6 errors, 15 passed. (The 6 errors are the openai_console fixture:
monkeypatch.setattr refuses to patch withdraw_pending_write onto a module that
does not define it, so the fixture cannot even be built there.)
  - item 1 learnings: FileNotFoundError [Errno 2] on
    ".../learnings/chronicle write paths, any remote/schema-constrained seat.jsonl"
    — the live specimen, verbatim;
  - item 1 threads: the slash test FAILS BY SUCCEEDING ("DID NOT RAISE") —
    with the parent dir pre-created, the pre-fix code files the thread under
    domain "a" and returns happily, the silent-nesting case
    _validate_domain_label's own docstring predicts and no test covered;
  - items 3 and 4: ImportError / AttributeError — withdraw_pending_write,
    AuditEvent.WITHDRAWN and the --by option do not exist there. The withdraw
    import is deliberately lazy (see _withdraw) so this file still COLLECTS on
    52f1f1b; a module-level import reduced the whole receipt to one error.

  - item 2 IS CONFOUNDED IN THIS FILE AND WAS RECEIPTED SEPARATELY. These
    console tests pass --by, which 52f1f1b's commit command does not accept, so
    there they fail at click's usage layer (exit 2) rather than on the banner.
    The independent receipt drove the 52f1f1b console directly with NO --by and
    a commit_failed proposal:
        exit_code = 0
        COMMITTED (LIVE): 4de1d36f  [record_learning] → record_learning
          committed_at: ?
          Stack response:
            { "ok": false }
    A green COMMITTED banner with the Stack's own "ok": false printed directly
    beneath it. That is the defect, unmixed with item 4.

The 15 that pass on 52f1f1b are reverse-direction regressions (ordinary labels
unchanged, compound comma domains still legal, reject still refuses approved)
— they are guards on what must NOT change, not gates.

SECOND PROVE-CAN-FAIL, for the wiring tests specifically. Transposing both
consoles' withdraw arguments (reason=by, withdrawn_by=reason) on THIS branch
fails exactly two tests, one per substrate:
    test_openai_console_does_not_transpose_withdraw_reason_and_reviewer
    test_grok_adapter_does_not_transpose_withdraw_reason_and_reviewer
An earlier version of that experiment failed only the openai one, which is why
the _grok_ops tests exist at all — see the comment above them.

Nothing here touches ~/.sovereign. Chronicle roots are tmp_path; the openai
library's five module-level path globals are rebound by fixture; the console
tests stub the substrate dispatch entirely, so no queue or audit chain is
reachable at all.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from bridge_core.audit import AuditEvent, read_audit_trail
from bridge_core.context import BridgeContext
from bridge_core.pending_writes import (
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
from click.testing import CliRunner

from sovereign_stack.memory import ExperientialMemory

PROPOSER = "grok-4.5-drain-followups-test"
REVIEWER = "drain-followups-test-harness"


# ══════════════════════════════════════════════════════════════════════════════
# ITEM 1 — A CALLER STRING BECAME A FILENAME
# ══════════════════════════════════════════════════════════════════════════════

#: The live specimen, verbatim from the drain that failed proposal 4de1d36f.
SLASHED = "chronicle write paths, any remote/schema-constrained seat"


def _first_record(path) -> dict:
    """The first JSON record of a shard, read under a context manager."""
    with open(path) as f:
        return json.loads(f.readline())


@pytest.fixture
def chronicle(tmp_path):
    """An ExperientialMemory rooted entirely under tmp_path."""
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def test_record_learning_survives_a_slash_in_applies_to(chronicle):
    """The specimen. On 52f1f1b this is FileNotFoundError [Errno 2]."""
    path = chronicle.record_learning(
        what_happened="a Ring-2 proposal could not be committed",
        what_learned="applies_to is prose and must not be a path",
        applies_to=SLASHED,
    )
    assert _first_record(path)["what_learned"]


def test_record_learning_keeps_the_raw_applies_to_in_the_body(chronicle):
    """Sanitize the NAME only. The body is the record, and it stays verbatim."""
    path = chronicle.record_learning("x", "y", applies_to=SLASHED)
    assert _first_record(path)["applies_to"] == SLASHED


def test_a_slashed_learning_is_still_findable(chronicle):
    """
    The whole justification for sanitizing rather than rejecting here.

    check_mistakes globs *.jsonl and searches the record body, so a shard whose
    FILENAME was rewritten is exactly as retrievable as one that was not. If
    this ever fails, the filename has become a retrieval key and the remedy for
    record_learning must change to rejection, like record_insight's domain.
    """
    chronicle.record_learning(
        what_happened="the drain could not commit",
        what_learned="schema-constrained seats need a distinctive marker",
        applies_to=SLASHED,
    )
    hits = chronicle.check_mistakes("schema-constrained seats")
    assert [h for h in hits if h["applies_to"] == SLASHED]


def test_the_shard_lands_inside_the_learnings_dir(chronicle):
    """No sanitized name may escape its directory, by nesting or traversal.

    The invariant is the PARENT, not the spelling: a traversal label may keep
    its letters in the filename (that is harmless and legible) but it must
    resolve to a direct child of learnings_dir and nothing else.
    """
    from pathlib import Path

    path = Path(chronicle.record_learning("x", "y", applies_to="../../etc/passwd"))
    assert path.parent == chronicle.learnings_dir
    assert path.resolve().parent == chronicle.learnings_dir.resolve()
    assert path.is_file()


@pytest.mark.parametrize(
    "label",
    [
        "a/b",
        "a\\b",
        "..",
        ".",
        "...",
        "",
        "   ",
        ".hidden",
        "nul\x00byte",
        "ctrl\x01char",
        "x" * 400,  # longer than any filesystem allows in one component
    ],
)
def test_pathological_applies_to_still_records(chronicle, label):
    """Every one of these is a filename a caller can produce. None may raise."""
    path = chronicle.record_learning("x", "y", applies_to=label)
    assert path.startswith(str(chronicle.learnings_dir))
    # One component, inside the directory, and legal on disk.
    assert _first_record(path)["applies_to"] == label


def test_an_empty_label_is_named_not_hidden(chronicle):
    """'' would write '<dir>/.jsonl' — a hidden shard nobody looks in."""
    path = chronicle.record_learning("x", "y", applies_to="")
    assert path.endswith("_unfiled.jsonl")


@pytest.mark.parametrize(
    "label",
    [
        "general",
        "architecture,refactoring",
        "sovereign_stack recall architecture silent_bugs",
        "ci-formatter-onboarding, ruff, sovereign-stack, version-pin-discipline",
    ],
)
def test_ordinary_labels_are_left_exactly_alone(chronicle, label):
    """
    Regression guard, measured against the 28 live learnings shards on
    2026-08-30: all 28 are identity under this sanitizer. Swapping in
    ExperientialMemory._slugify (lowercase, hyphens, maxlen=48) renames ALL of
    them and splits every existing shard in two. If this fails, someone reached
    for the slugifier.
    """
    path = chronicle.record_learning("x", "y", applies_to=label)
    assert path.endswith(f"/{label}.jsonl")


# ── the twin, with the opposite remedy ───────────────────────────────────────


def test_record_open_thread_rejects_a_slashed_domain(chronicle):
    """Here the filename IS the key, so the answer is a loud refusal."""
    with pytest.raises(ValueError, match="not a path"):
        chronicle.record_open_thread("does it hold?", domain="a/b")


def test_a_slashed_thread_domain_does_not_nest_silently(chronicle):
    """
    THE STRONGEST RED IN THIS FILE, because on 52f1f1b it does not raise — it
    SUCCEEDS, wrongly. With the parent directory already present, "a/b" writes
    to threads_dir/a/b.jsonl, and _thread_domain_for maps that nested shard
    back to domain "a". The thread is filed under a domain nobody asked for and
    every exact query for "a/b" comes back empty, with no error anywhere.
    """
    (chronicle.threads_dir / "a").mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="not a path"):
        chronicle.record_open_thread("does it nest?", domain="a/b")
    assert not (chronicle.threads_dir / "a" / "b.jsonl").exists()


def test_record_open_thread_still_takes_ordinary_domains(chronicle):
    """Reverse direction: compound comma domains are labels and stay legal."""
    path = chronicle.record_open_thread("q?", domain="sovereign-stack,bridge")
    assert path.endswith("sovereign-stack,bridge.jsonl")


# ══════════════════════════════════════════════════════════════════════════════
# ITEM 2 — THE CONSOLE'S BANNER MUST FOLLOW THE OUTCOME
# ══════════════════════════════════════════════════════════════════════════════
#
# The defect is a rendering decision, so it is tested at that layer: the
# substrate dispatch is stubbed out entirely and no queue, audit chain or Stack
# is reachable. That also keeps these tests clear of the eagerly-computed
# ~/.sovereign paths that grok_bridge binds at import time, which a HOME
# monkeypatch cannot move once the module is loaded.


def _fake_proposal(status: str, commit_result: dict) -> SimpleNamespace:
    return SimpleNamespace(
        proposal_id="4de1d36f-0000-0000-0000-000000000000",
        tool="record_learning",
        commit_target="record_learning",
        status=status,
        commit_result=commit_result,
    )


FAILED = _fake_proposal(
    "commit_failed",
    {
        "live": True,
        "committed": False,
        "error": "[Errno 2] No such file or directory",
        "stack_response": {"ok": False, "error": "[Errno 2] No such file or directory"},
    },
)
SUCCEEDED = _fake_proposal(
    "committed",
    {"live": True, "stack_response": {"ok": True}, "committed_at": "2026-08-30T00:00:00Z"},
)


@pytest.fixture
def shared_console(monkeypatch):
    """bridge_core's console with its substrate dispatch replaced."""
    import bridge_core.cli as bc

    box: dict = {}

    class _Stub:
        def __init__(self, source):
            self.source = source

        def commit(self, pid, live, by):
            box["commit"] = {"pid": pid, "live": live, "by": by}
            return box["returns"]

        def withdraw(self, pid, r, by):
            box["withdraw"] = {"pid": pid, "reason": r, "by": by}
            return box["returns"]

    monkeypatch.setattr(bc, "_SubstrateOps", _Stub)
    box["cli"] = bc.cli
    return box


@pytest.fixture
def openai_console(monkeypatch):
    """openai's console with commit_pending_write replaced."""
    import openai_bridge.cli as oc

    box: dict = {}

    def _commit(proposal_id, live=False, *, committed_by):
        box["commit"] = {"pid": proposal_id, "live": live, "by": committed_by}
        return box["returns"]

    def _withdraw(proposal_id, *, reason, withdrawn_by):
        box["withdraw"] = {"pid": proposal_id, "reason": reason, "by": withdrawn_by}
        return box["returns"]

    monkeypatch.setattr(oc, "commit_pending_write", _commit)
    monkeypatch.setattr(oc, "withdraw_pending_write", _withdraw)
    box["cli"] = oc.cli
    return box


def test_shared_console_does_not_claim_a_failed_commit(shared_console):
    """
    The specimen. On 52f1f1b the output contains BOTH the library's failure and
    a bright-green COMMITTED banner, and the command exits 0.
    """
    shared_console["returns"] = FAILED
    result = CliRunner().invoke(
        shared_console["cli"], ["commit", "4de1d36f", "--live", "--by", REVIEWER]
    )
    assert "COMMITTED (LIVE)" not in result.output
    assert "NOT committed" in result.output
    assert result.exit_code != 0


def test_shared_console_reports_the_stack_error(shared_console):
    """A failure the human cannot read the cause of is barely a failure."""
    shared_console["returns"] = FAILED
    result = CliRunner().invoke(
        shared_console["cli"], ["commit", "4de1d36f", "--live", "--by", REVIEWER]
    )
    assert "Errno 2" in result.output


def test_shared_console_still_announces_a_real_commit(shared_console):
    """Reverse direction: a genuine commit must keep saying so, and exit 0."""
    shared_console["returns"] = SUCCEEDED
    result = CliRunner().invoke(
        shared_console["cli"], ["commit", "4de1d36f", "--live", "--by", REVIEWER]
    )
    assert "COMMITTED (LIVE)" in result.output
    assert result.exit_code == 0


def test_shared_console_fails_closed_on_an_unknown_status(shared_console):
    """
    A status this console has never heard of is not success. The banner keys on
    == "committed", not on != "commit_failed", so a future state added upstream
    cannot inherit the green path by default.
    """
    shared_console["returns"] = _fake_proposal("some_future_state", {})
    result = CliRunner().invoke(
        shared_console["cli"], ["commit", "4de1d36f", "--live", "--by", REVIEWER]
    )
    assert "COMMITTED (LIVE)" not in result.output
    assert result.exit_code != 0


def test_openai_console_does_not_claim_a_failed_commit(openai_console):
    openai_console["returns"] = FAILED
    result = CliRunner().invoke(
        openai_console["cli"], ["commit", "4de1d36f", "--live", "--by", REVIEWER]
    )
    assert "COMMITTED (LIVE)" not in result.output
    assert "NOT committed" in result.output
    assert result.exit_code != 0


def test_openai_console_still_announces_a_real_commit(openai_console):
    openai_console["returns"] = SUCCEEDED
    result = CliRunner().invoke(
        openai_console["cli"], ["commit", "4de1d36f", "--live", "--by", REVIEWER]
    )
    assert "COMMITTED (LIVE)" in result.output
    assert result.exit_code == 0


# ══════════════════════════════════════════════════════════════════════════════
# ITEMS 3 & 4 — WITHDRAW, AND THE COMMITTER'S NAME
# ══════════════════════════════════════════════════════════════════════════════


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
        bridge_rest_url="http://127.0.0.1:1",  # unroutable on purpose
        bridge_rest_token_env="TEST_DRAIN_TOKEN",
    )


def _proposal(ctx):
    return create_pending_write(
        ctx,
        "record_open_thread",
        {
            "question": "Can an approved proposal be adjudicated away?",
            "context": "drain follow-ups",
            "domain": "bridge-review",
        },
        source_instance=PROPOSER,
    )


def _approved(ctx):
    p = _proposal(ctx)
    return approve_pending_write(ctx, p.proposal_id, approved_by=REVIEWER)


def _events(ctx, proposal_id, event_type):
    return [
        e
        for e in read_audit_trail(ctx, proposal_id=proposal_id)
        if e["event_type"] == event_type.value
    ]


def _withdraw(*args, **kwargs):
    """Imported lazily ON PURPOSE.

    A module-level `from bridge_core.pending_writes import withdraw_pending_write`
    makes this whole file fail to COLLECT on 52f1f1b, which reduces the red
    receipt to one ImportError and proves nothing about items 1 and 2. Deferring
    the lookup lets every test in this file run there and fail on its own merits.
    """
    from bridge_core.pending_writes import withdraw_pending_write

    return withdraw_pending_write(*args, **kwargs)


def _stack_response(payload: dict):
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    return _Resp()


# ── item 3: the approved -> rejected route ───────────────────────────────────


def test_reject_still_refuses_an_approved_proposal(ctx):
    """Reverse direction: `reject` keeps its narrow meaning. This is the GAP,
    documented — withdraw is the new route, not a loosened reject."""
    p = _approved(ctx)
    with pytest.raises(ValueError, match="Cannot reject"):
        reject_pending_write(ctx, p.proposal_id, "changed my mind", rejected_by=REVIEWER)


def test_withdraw_moves_approved_to_rejected(ctx):
    p = _approved(ctx)
    out = _withdraw(ctx, p.proposal_id, reason="smoke fixture", withdrawn_by=REVIEWER)
    assert out.status == "rejected"
    assert out.reviewed_by == REVIEWER


def test_withdraw_records_where_it_came_from(ctx):
    """prior_status is the whole point: the chain must be able to tell an
    approved write that was called back from one refused on arrival."""
    p = _approved(ctx)
    _withdraw(ctx, p.proposal_id, reason="smoke fixture", withdrawn_by=REVIEWER)
    events = _events(ctx, p.proposal_id, AuditEvent.WITHDRAWN)
    assert len(events) == 1
    assert events[0]["actor"] == REVIEWER
    assert events[0]["details"]["prior_status"] == "approved"
    assert events[0]["details"]["reason"] == "smoke fixture"


def test_withdraw_does_not_emit_a_rejected_event(ctx):
    """Reusing REJECTED would retroactively redefine every one already on the
    chain, all of which mean 'refused before it was ever approved'."""
    p = _approved(ctx)
    _withdraw(ctx, p.proposal_id, reason="r", withdrawn_by=REVIEWER)
    assert _events(ctx, p.proposal_id, AuditEvent.REJECTED) == []


def test_withdraw_refuses_a_pending_proposal(ctx):
    """A pending proposal has `reject`. Two verbs for one transition is how an
    operator ends up unsure which one the record will show."""
    p = _proposal(ctx)
    with pytest.raises(ValueError, match="Cannot withdraw"):
        _withdraw(ctx, p.proposal_id, reason="r", withdrawn_by=REVIEWER)


def test_withdraw_refuses_a_committed_proposal(ctx, monkeypatch):
    """A landed write is final; the Stack has it. Nothing here may imply
    otherwise."""
    import bridge_core.pending_writes as pw

    p = _approved(ctx)
    monkeypatch.setattr(pw.httpx, "post", lambda *a, **kw: _stack_response({"ok": True}))
    commit_pending_write(ctx, p.proposal_id, live=True, committed_by=REVIEWER)
    with pytest.raises(ValueError, match="Cannot withdraw"):
        _withdraw(ctx, p.proposal_id, reason="r", withdrawn_by=REVIEWER)


def test_withdraw_clears_a_commit_failed_proposal(ctx, monkeypatch):
    """The twin gap one status over: `retry` sends commit_failed back to
    approved, which is a loop, not an exit."""
    import bridge_core.pending_writes as pw

    p = _approved(ctx)
    monkeypatch.setattr(
        pw.httpx, "post", lambda *a, **kw: _stack_response({"ok": False, "error": "no"})
    )
    failed = commit_pending_write(ctx, p.proposal_id, live=True, committed_by=REVIEWER)
    assert failed.status == "commit_failed"
    out = _withdraw(ctx, p.proposal_id, reason="abandoned", withdrawn_by=REVIEWER)
    assert out.status == "rejected"
    assert (
        _events(ctx, p.proposal_id, AuditEvent.WITHDRAWN)[0]["details"]["prior_status"]
        == "commit_failed"
    )


def test_withdraw_without_a_reviewer_is_a_type_error(ctx):
    p = _approved(ctx)
    with pytest.raises(TypeError):
        _withdraw(ctx, p.proposal_id, reason="r")


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_withdraw_refuses_a_blank_reviewer(ctx, blank):
    p = _approved(ctx)
    with pytest.raises(ValueError, match="withdrawn_by"):
        _withdraw(ctx, p.proposal_id, reason="r", withdrawn_by=blank)


def test_withdraw_refuses_a_blank_reason(ctx):
    """A withdrawal with no stated cause is not a record."""
    p = _approved(ctx)
    with pytest.raises(ValueError, match="reason"):
        _withdraw(ctx, p.proposal_id, reason="   ", withdrawn_by=REVIEWER)


def test_withdrawal_leaves_the_hash_chain_intact(ctx):
    from bridge_core.hash_chain import verify_chain

    p = _approved(ctx)
    _withdraw(ctx, p.proposal_id, reason="r", withdrawn_by=REVIEWER)
    ok, msg = verify_chain(ctx)
    assert ok, msg


# ── item 4: the commit event names the reviewer ──────────────────────────────


def test_commit_without_a_committer_is_a_type_error(ctx):
    p = _approved(ctx)
    with pytest.raises(TypeError):
        commit_pending_write(ctx, p.proposal_id, live=True)


@pytest.mark.parametrize("blank", ["", "   "])
def test_commit_refuses_a_blank_committer(ctx, blank):
    p = _approved(ctx)
    with pytest.raises(ValueError, match="committed_by"):
        commit_pending_write(ctx, p.proposal_id, live=True, committed_by=blank)


def test_committed_event_names_the_reviewer(ctx, monkeypatch):
    """The defect: this used to be actor='bridge' — the one link in an
    otherwise fully attributed chain that named nobody."""
    import bridge_core.pending_writes as pw

    p = _approved(ctx)
    monkeypatch.setattr(pw.httpx, "post", lambda *a, **kw: _stack_response({"ok": True}))
    commit_pending_write(ctx, p.proposal_id, live=True, committed_by=REVIEWER)
    ev = _events(ctx, p.proposal_id, AuditEvent.COMMITTED)[0]
    assert ev["actor"] == REVIEWER
    # The substrate that carried the call is a different fact, still recorded.
    assert ev["details"]["executed_by"] == "bridge"


def test_failed_commit_event_also_names_the_reviewer(ctx, monkeypatch):
    """Same person either way — an attempt that failed is still an attempt
    somebody made."""
    import bridge_core.pending_writes as pw

    p = _approved(ctx)
    monkeypatch.setattr(
        pw.httpx, "post", lambda *a, **kw: _stack_response({"ok": False, "error": "no"})
    )
    commit_pending_write(ctx, p.proposal_id, live=True, committed_by=REVIEWER)
    ev = _events(ctx, p.proposal_id, AuditEvent.COMMIT_FAILED)[0]
    assert ev["actor"] == REVIEWER
    assert ev["details"]["executed_by"] == "bridge"


def test_the_proposal_itself_records_who_committed_it(ctx, monkeypatch):
    import bridge_core.pending_writes as pw

    p = _approved(ctx)
    monkeypatch.setattr(pw.httpx, "post", lambda *a, **kw: _stack_response({"ok": True}))
    out = commit_pending_write(ctx, p.proposal_id, live=True, committed_by=REVIEWER)
    assert out.commit_result["committed_by"] == REVIEWER


# ── both substrates: the openai twins ────────────────────────────────────────

_OPENAI_ISOLATED = (
    ("pending_writes", "PENDING_DIR", "pending_writes"),
    ("hash_chain", "AUDIT_DIR", "audit"),
    ("hash_chain", "AUDIT_LOG", "audit/audit.jsonl"),
    ("audit", "AUDIT_DIR", "audit"),
    ("audit", "AUDIT_LOG", "audit/audit.jsonl"),
)


@pytest.fixture
def openai_tmp(tmp_path, monkeypatch):
    """All five live-path globals, on BOTH import identities of the package.

    audit.py from-imports AUDIT_DIR/AUDIT_LOG by VALUE, so rebinding hash_chain
    alone leaves append_audit_event writing to the real chain — the exact leak
    conftest's no_live_audit_writes tripwire exists to catch.
    """
    import importlib
    import sys

    root = tmp_path / "oai"
    seen = 0
    for pkg in ("openai_bridge", "clients.openai_bridge"):
        for mod_name, attr, rel in _OPENAI_ISOLATED:
            full = f"{pkg}.{mod_name}"
            mod = sys.modules.get(full)
            if mod is None and pkg == "openai_bridge":
                mod = importlib.import_module(full)
            if mod is None:
                continue
            monkeypatch.setattr(mod, attr, root / rel)
            seen += 1
    assert seen >= len(_OPENAI_ISOLATED)

    import openai_bridge.pending_writes as opw

    monkeypatch.setattr(opw, "_BRIDGE_URL", "http://127.0.0.1:1/api/call")
    return root


def _openai_approved():
    import openai_bridge.pending_writes as opw

    p = opw.create_pending_write(
        "record_open_thread",
        {
            "question": "Can an approved proposal be adjudicated away?",
            "context": "drain follow-ups",
            "domain": "bridge-review",
        },
        source_instance=PROPOSER,
    )
    return opw.approve_pending_write(p.proposal_id, approved_by=REVIEWER)


def test_openai_withdraw_moves_approved_to_rejected(openai_tmp):
    import openai_bridge.pending_writes as opw

    p = _openai_approved()
    out = opw.withdraw_pending_write(p.proposal_id, reason="smoke fixture", withdrawn_by=REVIEWER)
    assert out.status == "rejected"


def test_openai_withdraw_records_prior_status(openai_tmp):
    import openai_bridge.pending_writes as opw
    from openai_bridge.audit import AuditEvent as OAuditEvent
    from openai_bridge.audit import read_audit_trail as oread

    p = _openai_approved()
    opw.withdraw_pending_write(p.proposal_id, reason="smoke fixture", withdrawn_by=REVIEWER)
    events = [
        e
        for e in oread(proposal_id=p.proposal_id)
        if e["event_type"] == OAuditEvent.WITHDRAWN.value
    ]
    assert len(events) == 1
    assert events[0]["actor"] == REVIEWER
    assert events[0]["details"]["prior_status"] == "approved"


def test_openai_withdraw_without_a_reviewer_is_a_type_error(openai_tmp):
    import openai_bridge.pending_writes as opw

    p = _openai_approved()
    with pytest.raises(TypeError):
        opw.withdraw_pending_write(p.proposal_id, reason="r")


def test_openai_commit_without_a_committer_is_a_type_error(openai_tmp):
    import openai_bridge.pending_writes as opw

    p = _openai_approved()
    with pytest.raises(TypeError):
        opw.commit_pending_write(p.proposal_id, live=True)


def test_openai_committed_event_names_the_reviewer(openai_tmp, monkeypatch):
    import openai_bridge.pending_writes as opw
    from openai_bridge.audit import AuditEvent as OAuditEvent
    from openai_bridge.audit import read_audit_trail as oread

    p = _openai_approved()
    monkeypatch.setenv("BRIDGE_TOKEN", "test-token-not-real")
    monkeypatch.setattr(opw.httpx, "post", lambda *a, **kw: _stack_response({"ok": True}))
    opw.commit_pending_write(p.proposal_id, live=True, committed_by=REVIEWER)
    ev = [
        e
        for e in oread(proposal_id=p.proposal_id)
        if e["event_type"] == OAuditEvent.COMMITTED.value
    ][0]
    assert ev["actor"] == REVIEWER
    assert ev["details"]["executed_by"] == "bridge"


# ── the console WIRING: --by must reach the library as the reviewer ──────────
#
# The refuses-without-by tests below exit at click's usage layer and never
# reach the adapter, so on their own they would let this ship:
#     self._withdraw = lambda pid, reason, by: withdraw_pending_write(
#         pid, reason=by, withdrawn_by=reason)      # transposed
# — which stamps the rejection REASON as the reviewer's name. That is item 4's
# own defect class (a record that names the wrong actor) reintroduced one layer
# above the library the rest of this file verifies. These assert the wiring.


REJECTED_P = _fake_proposal("rejected", {})


def test_shared_console_passes_the_reviewer_to_commit(shared_console):
    shared_console["returns"] = SUCCEEDED
    CliRunner().invoke(shared_console["cli"], ["commit", "4de1d36f", "--live", "--by", REVIEWER])
    assert shared_console["commit"] == {"pid": "4de1d36f", "live": True, "by": REVIEWER}


def test_openai_console_passes_the_reviewer_to_commit(openai_console):
    openai_console["returns"] = SUCCEEDED
    CliRunner().invoke(openai_console["cli"], ["commit", "4de1d36f", "--live", "--by", REVIEWER])
    assert openai_console["commit"] == {"pid": "4de1d36f", "live": True, "by": REVIEWER}


def test_shared_console_does_not_transpose_withdraw_reason_and_reviewer(shared_console):
    shared_console["returns"] = REJECTED_P
    result = CliRunner().invoke(
        shared_console["cli"],
        ["withdraw", "4de1d36f", "--reason", "smoke fixture", "--by", REVIEWER],
    )
    assert result.exit_code == 0, result.output
    assert shared_console["withdraw"] == {
        "pid": "4de1d36f",
        "reason": "smoke fixture",
        "by": REVIEWER,
    }


def test_openai_console_does_not_transpose_withdraw_reason_and_reviewer(openai_console):
    openai_console["returns"] = REJECTED_P
    result = CliRunner().invoke(
        openai_console["cli"],
        ["withdraw", "4de1d36f", "--reason", "smoke fixture", "--by", REVIEWER],
    )
    assert result.exit_code == 0, result.output
    assert openai_console["withdraw"] == {
        "pid": "4de1d36f",
        "reason": "smoke fixture",
        "by": REVIEWER,
    }


# ── the adapter layer the console stub REPLACES ──────────────────────────────
#
# Caught by deliberately transposing both consoles' withdraw wiring and
# re-running: the openai test failed, the shared-console one PASSED. The
# shared_console fixture swaps out _SubstrateOps entirely, which is exactly
# where bridge_core's keyword wiring lives — so the stub was hiding the layer
# it was meant to protect. A stub that replaces the code under test proves
# nothing about it; SOP #1, suspect the instrument, applied to a test.
#
# These call the REAL _SubstrateOps and patch the package-level functions it
# from-imports at __init__ time. Nothing is executed against a live queue: both
# targets are replaced before the adapter can reach them.


def _grok_ops(monkeypatch, box):
    import bridge_core

    def _commit(ctx, pid, live=False, *, committed_by):
        box["commit"] = {"pid": pid, "live": live, "by": committed_by}

    def _withdraw(ctx, pid, *, reason, withdrawn_by):
        box["withdraw"] = {"pid": pid, "reason": reason, "by": withdrawn_by}

    monkeypatch.setattr(bridge_core, "commit_pending_write", _commit)
    monkeypatch.setattr(bridge_core, "withdraw_pending_write", _withdraw)

    from bridge_core.cli import _SubstrateOps

    return _SubstrateOps("grok")


def test_grok_adapter_passes_the_reviewer_to_commit(monkeypatch):
    box: dict = {}
    _grok_ops(monkeypatch, box).commit("4de1d36f", live=True, by=REVIEWER)
    assert box["commit"] == {"pid": "4de1d36f", "live": True, "by": REVIEWER}


def test_grok_adapter_does_not_transpose_withdraw_reason_and_reviewer(monkeypatch):
    box: dict = {}
    _grok_ops(monkeypatch, box).withdraw("4de1d36f", "smoke fixture", REVIEWER)
    assert box["withdraw"] == {
        "pid": "4de1d36f",
        "reason": "smoke fixture",
        "by": REVIEWER,
    }


# ── both consoles refuse the un-named reviewer ───────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["commit", "deadbeef", "--live"],
        ["withdraw", "deadbeef", "--reason", "r"],
    ],
)
def test_shared_console_refuses_without_by(shared_console, argv):
    """Exit 2 is click's own usage refusal — the command never ran."""
    result = CliRunner().invoke(shared_console["cli"], argv)
    assert result.exit_code == 2, result.output
    assert "--by" in result.output


@pytest.mark.parametrize(
    "argv",
    [
        ["commit", "deadbeef", "--live"],
        ["withdraw", "deadbeef", "--reason", "r"],
    ],
)
def test_openai_console_refuses_without_by(openai_console, tmp_path, monkeypatch, argv):
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    result = CliRunner().invoke(openai_console["cli"], argv)
    assert result.exit_code == 2, result.output
    assert "--by" in result.output
