from __future__ import annotations

"""
Multi-substrate Bridge CLI — human approval console for Ring 2 proposals.

The same `bridge` command serves all substrates via --source flag:

    bridge list-pending                          # default --source=openai
    bridge --source=grok list-pending
    bridge --source=grok approve <proposal_id>
    bridge --source=grok commit <proposal_id> --live
    bridge --source=grok verify-chain
    bridge --source=grok audit-tail

For --source=openai, dispatches to the legacy openai_bridge module
(unchanged — preserves Phase 4 production behaviour).

For --source=grok (and future substrates), uses bridge_core functions
with the substrate's registered BridgeContext.

Live commit (--live flag) is required to actually write to the Stack;
without it commits are dry-run.
"""

import json
import sys
from pathlib import Path

import click

# ── Color helpers ─────────────────────────────────────────────────────────────

RISK_COLORS = {
    "low": "green",
    "medium": "yellow",
    "high": "red",
    "critical": "bright_red",
}

STATUS_COLORS = {
    "pending": "yellow",
    "approved": "cyan",
    "committed": "green",
    "rejected": "red",
    "needs_revision": "magenta",
    "expired": "bright_black",
}


def _risk_label(risk: str) -> str:
    color = RISK_COLORS.get(risk.lower(), "white")
    return click.style(f"[{risk.upper():8s}]", fg=color, bold=True)


def _status_label(status: str) -> str:
    color = STATUS_COLORS.get(status.lower(), "white")
    return click.style(status, fg=color)


def _short(proposal_id: str) -> str:
    return proposal_id[:8] if proposal_id else "?"


# ── Substrate dispatch ────────────────────────────────────────────────────────

class _SubstrateOps:
    """Bundle of operation functions + paths for one substrate."""

    def __init__(self, source: str):
        self.source = source

        if source == "openai":
            # Legacy openai_bridge — uses its own module-level state
            from openai_bridge.audit import read_audit_trail
            from openai_bridge.hash_chain import verify_chain
            from openai_bridge.pending_writes import (
                approve_pending_write,
                commit_pending_write,
                list_pending_writes,
                needs_revision_pending_write,
                reject_pending_write,
            )
            self._approve = lambda pid, by: approve_pending_write(pid, approved_by=by)
            self._reject = lambda pid, reason, by: reject_pending_write(pid, reason=reason, rejected_by=by)
            self._needs_revision = lambda pid, notes, by: needs_revision_pending_write(pid, notes=notes, actor=by)
            self._commit = lambda pid, live: commit_pending_write(pid, live=live)
            self._list = lambda status: list_pending_writes(status=status)
            self._read_audit = lambda pid: read_audit_trail(proposal_id=pid)
            self._verify = lambda: verify_chain()
            self._pending_dir = Path.home() / ".sovereign" / "openai_bridge" / "pending_writes"

        elif source == "grok":
            # bridge_core path — import grok_bridge to register context
            import grok_bridge  # noqa: F401  (registers BridgeContext)

            from bridge_core import (
                approve_pending_write,
                commit_pending_write,
                get_context,
                list_pending_writes,
                needs_revision_pending_write,
                read_audit_trail,
                reject_pending_write,
                verify_chain,
            )
            ctx = get_context("grok-xai")
            self._approve = lambda pid, by: approve_pending_write(ctx, pid, approved_by=by)
            self._reject = lambda pid, reason, by: reject_pending_write(ctx, pid, reason=reason, rejected_by=by)
            self._needs_revision = lambda pid, notes, by: needs_revision_pending_write(ctx, pid, notes=notes, actor=by)
            self._commit = lambda pid, live: commit_pending_write(ctx, pid, live=live)
            self._list = lambda status: list_pending_writes(ctx, status=status)
            self._read_audit = lambda pid: read_audit_trail(ctx, proposal_id=pid)
            self._verify = lambda: verify_chain(ctx)
            self._pending_dir = ctx.pending_writes_dir

        else:
            raise click.ClickException(
                f"Unknown --source '{source}'. Known: openai, grok"
            )

    @property
    def pending_dir(self) -> Path:
        return self._pending_dir

    def list(self, status):       return self._list(status)
    def approve(self, pid, by):   return self._approve(pid, by)
    def reject(self, pid, r, by): return self._reject(pid, r, by)
    def needs_revision(self, pid, n, by): return self._needs_revision(pid, n, by)
    def commit(self, pid, live):  return self._commit(pid, live)
    def read_audit(self, pid):    return self._read_audit(pid)
    def verify(self):             return self._verify()


# ── CLI group ─────────────────────────────────────────────────────────────────

@click.group()
@click.option(
    "--source",
    default="openai",
    type=click.Choice(["openai", "grok"]),
    help="Which bridge substrate to operate on (default: openai for backward compat)",
)
@click.pass_context
def cli(ctx, source: str):
    """Bridge — human approval console for Ring 2 proposals.

    Supports multiple substrates via --source. Default is openai for
    backward compatibility with Phase 4 ChatGPT bridge usage.
    """
    ctx.ensure_object(dict)
    ctx.obj["source"] = source
    ctx.obj["ops"] = _SubstrateOps(source)


# ── list-pending ──────────────────────────────────────────────────────────────

@cli.command("list-pending")
@click.option(
    "--status",
    default=None,
    type=click.Choice(["pending", "approved", "committed", "rejected", "needs_revision", "all"]),
    help="Filter by status (default: pending)",
)
@click.pass_context
def list_pending(ctx, status: str | None):
    """List proposals in the pending_writes queue."""
    ops = ctx.obj["ops"]
    source = ctx.obj["source"]
    effective = None if status == "all" else (status or "pending")
    proposals = ops.list(status=effective)

    if not proposals:
        label = effective or "pending"
        click.echo(f"No {label} proposals on {source} bridge.")
        return

    label = effective or "pending"
    click.echo(f"\n{len(proposals)} {label} proposal(s) on {source} bridge:\n")
    for p in proposals:
        pid = _short(p["proposal_id"])
        ts = str(p.get("timestamp", ""))[:19]
        tool = p.get("tool", "?")
        src = p.get("source_instance", "?")
        risk = p.get("risk_level", "?")
        st = p.get("status", "?")

        click.echo(
            f"  {_risk_label(risk)}  "
            f"{click.style(pid, bold=True)}  "
            f"{tool:30s}  "
            f"{_status_label(st):10s}  "
            f"{ts}  "
            f"from={src}"
        )
    click.echo()


# ── show ──────────────────────────────────────────────────────────────────────

@cli.command("show")
@click.argument("proposal_id")
@click.pass_context
def show(ctx, proposal_id: str):
    """Show full details for a proposal."""
    ops = ctx.obj["ops"]
    matches = list(ops.pending_dir.glob(f"*{proposal_id[:8]}*.json"))
    if not matches:
        click.echo(f"No proposal found for id: {proposal_id}", err=True)
        sys.exit(1)
    path = matches[0]
    try:
        d = json.loads(path.read_text())
    except Exception as e:
        click.echo(f"Error reading proposal: {e}", err=True)
        sys.exit(1)

    pid = d.get("proposal_id", "?")
    click.echo(f"\n{'─'*60}")
    click.echo(f"  Proposal:  {click.style(pid, bold=True)}")
    click.echo(f"  Substrate: {d.get('substrate', ctx.obj['source'])}")
    click.echo(f"  Tool:      {click.style(d.get('tool','?'), fg='cyan')}")
    click.echo(f"  Target:    {d.get('commit_target','?')}")
    click.echo(f"  Status:    {_status_label(d.get('status','?'))}")
    click.echo(f"  Risk:      {_risk_label(d.get('risk_level','?'))}  {', '.join(d.get('risk_reasons',[]))}")
    click.echo(f"  Layer:     {d.get('proposed_layer','?')}  receipt={d.get('has_receipt', False)}")
    click.echo(f"  Source:    {d.get('source_instance','?')}")
    click.echo(f"  Session:   {d.get('session_id','?')}")
    click.echo(f"  Created:   {str(d.get('timestamp',''))[:19]}")

    if d.get("compass_check_result"):
        click.echo(f"  Compass:   {d['compass_check_result']} — {d.get('compass_check_rationale','')}")
    if d.get("reviewed_by"):
        click.echo(f"  Reviewed:  {d['reviewed_by']} at {str(d.get('reviewed_at',''))[:19]}")
    if d.get("revision_notes"):
        click.echo(f"  Notes:     {d['revision_notes']}")

    click.echo(f"\n  Arguments:")
    for k, v in d.get("arguments", {}).items():
        v_str = str(v)[:120] + ("…" if len(str(v)) > 120 else "")
        click.echo(f"    {k}: {v_str}")

    if d.get("receipt_urls"):
        click.echo(f"\n  Receipts:")
        for url in d["receipt_urls"]:
            click.echo(f"    {url}")

    if d.get("commit_result"):
        click.echo(f"\n  Commit result:")
        click.echo(f"    {json.dumps(d['commit_result'], indent=4)}")

    click.echo(f"{'─'*60}\n")


# ── approve / reject / needs-revision / commit ────────────────────────────────

@cli.command("approve")
@click.argument("proposal_id")
@click.option("--by", default="Anthony", help="Approver name")
@click.pass_context
def approve(ctx, proposal_id: str, by: str):
    """Approve a pending proposal. Approval and commit are separate steps."""
    ops = ctx.obj["ops"]
    try:
        p = ops.approve(proposal_id, by)
        click.echo(
            f"Approved: {_short(p.proposal_id)}  [{p.tool}]  "
            f"by={p.reviewed_by}  status={_status_label(p.status)}"
        )
        click.echo(f"  Run 'bridge --source={ctx.obj['source']} commit <id>' to execute (mocked unless --live).")
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command("reject")
@click.argument("proposal_id")
@click.option("--reason", required=True, help="Rejection reason")
@click.option("--by", default="Anthony", help="Reviewer name")
@click.pass_context
def reject(ctx, proposal_id: str, reason: str, by: str):
    """Reject a pending proposal."""
    ops = ctx.obj["ops"]
    try:
        p = ops.reject(proposal_id, reason, by)
        click.echo(f"Rejected: {_short(p.proposal_id)}  [{p.tool}]  reason={reason}")
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command("needs-revision")
@click.argument("proposal_id")
@click.option("--notes", required=True, help="Revision instructions")
@click.option("--by", default="Anthony", help="Reviewer name")
@click.pass_context
def needs_revision(ctx, proposal_id: str, notes: str, by: str):
    """Send a pending proposal back for revision with notes."""
    ops = ctx.obj["ops"]
    try:
        p = ops.needs_revision(proposal_id, notes, by)
        click.echo(f"Needs revision: {_short(p.proposal_id)}  [{p.tool}]  notes={notes}")
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command("commit")
@click.argument("proposal_id")
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Write to the Stack for real. Without --live, shows what would happen.",
)
@click.pass_context
def commit(ctx, proposal_id: str, live: bool):
    """Commit an approved proposal. --live required for real Stack write."""
    ops = ctx.obj["ops"]
    try:
        p = ops.commit(proposal_id, live=live)
        result = p.commit_result or {}

        if not live:
            click.echo(f"\nDry-run commit (pass --live to execute):")
            click.echo(f"  proposal:   {_short(p.proposal_id)}")
            click.echo(f"  would call: {result.get('would_call', p.commit_target)}")
            click.echo(f"  with args:  {json.dumps(result.get('with_arguments', p.arguments), indent=4)}")
            click.echo()
            return

        stack_response = result.get("stack_response", {})
        click.echo(
            click.style(
                f"COMMITTED (LIVE): {_short(p.proposal_id)}  [{p.tool}] → {p.commit_target}",
                fg="bright_green",
                bold=True,
            )
        )
        click.echo(f"  committed_at: {result.get('committed_at','?')}")
        click.echo(f"  Stack response:")
        click.echo(f"    {json.dumps(stack_response, indent=4)}")

    except (FileNotFoundError, ValueError, RuntimeError) as e:
        click.echo(click.style(f"Error: {e}", fg="red"), err=True)
        sys.exit(1)


# ── audit-tail / verify-chain ─────────────────────────────────────────────────

@cli.command("audit-tail")
@click.option("--n", default=20, show_default=True, help="Number of recent entries to show")
@click.option("--proposal-id", default=None, help="Filter to a specific proposal")
@click.pass_context
def audit_tail(ctx, n: int, proposal_id: str | None):
    """Show recent audit log entries."""
    ops = ctx.obj["ops"]
    entries = ops.read_audit(proposal_id)
    entries = entries[-n:]

    if not entries:
        click.echo(f"Audit log empty for {ctx.obj['source']} bridge.")
        return

    click.echo(f"\nAudit log for {ctx.obj['source']} bridge (last {len(entries)} entries):\n")
    for e in entries:
        ts = str(e.get("timestamp", ""))[:19]
        event = e.get("event_type", "?")
        pid = _short(e.get("proposal_id", ""))
        actor = e.get("actor", "?")
        details = e.get("details", {})
        detail_str = "  ".join(f"{k}={v}" for k, v in details.items() if k != "file")

        color = {
            "proposal_created": "cyan",
            "approved": "green",
            "committed": "bright_green",
            "rejected": "red",
            "needs_revision": "magenta",
            "validation_failed": "bright_red",
            "validation_passed": "bright_black",
            "chain_verified": "green",
            "chain_broken": "bright_red",
        }.get(event, "white")

        click.echo(
            f"  {ts}  "
            f"{click.style(f'{event:<26s}', fg=color)}  "
            f"pid={pid}  actor={actor:12s}  {detail_str}"
        )
    click.echo()


@cli.command("verify-chain")
@click.pass_context
def verify_chain_cmd(ctx):
    """Verify the audit log hash chain integrity."""
    ops = ctx.obj["ops"]
    ok, msg = ops.verify()
    if ok:
        click.echo(click.style(f"OK: {msg}", fg="green"))
    else:
        click.echo(click.style(f"BROKEN: {msg}", fg="bright_red"), err=True)
        sys.exit(1)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    cli()


if __name__ == "__main__":
    main()
