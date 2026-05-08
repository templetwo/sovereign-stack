from __future__ import annotations

"""
Bridge CLI — human approval console for the OpenAI bridge.

Usage (after install or python -m invocation):
    bridge list-pending
    bridge show <proposal_id>
    bridge approve <proposal_id>
    bridge reject <proposal_id> --reason "..."
    bridge needs-revision <proposal_id> --reason "..."
    bridge commit <proposal_id> [--live]
    bridge audit-tail [--n 20]
    bridge verify-chain

Live commit is disabled by default. Pass --live explicitly to commit.
"""

import json
import sys
from pathlib import Path

import click

from .audit import AuditEvent, read_audit_trail
from .hash_chain import verify_chain
from .pending_writes import (
    ValidationError,
    approve_pending_write,
    commit_pending_write,
    list_pending_writes,
    needs_revision_pending_write,
    reject_pending_write,
)

PENDING_DIR = Path.home() / ".sovereign" / "openai_bridge" / "pending_writes"
AUDIT_LOG = Path.home() / ".sovereign" / "openai_bridge" / "audit" / "audit.jsonl"

# ── Colour helpers ────────────────────────────────────────────────────────────

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


# ── CLI group ─────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """OpenAI Bridge — human approval console for Ring 2 proposals."""


# ── list-pending ──────────────────────────────────────────────────────────────

@cli.command("list-pending")
@click.option(
    "--status",
    default=None,
    type=click.Choice(["pending", "approved", "committed", "rejected", "needs_revision", "all"]),
    help="Filter by status (default: pending)",
)
def list_pending(status: str | None):
    """List proposals in the pending_writes queue."""
    effective = None if status == "all" else (status or "pending")
    proposals = list_pending_writes(status=effective)

    if not proposals:
        label = effective or "pending"
        click.echo(f"No {label} proposals.")
        return

    label = effective or "pending"
    click.echo(f"\n{len(proposals)} {label} proposal(s):\n")
    for p in proposals:
        pid = _short(p["proposal_id"])
        ts = str(p.get("timestamp", ""))[:19]
        tool = p.get("tool", "?")
        source = p.get("source_instance", "?")
        risk = p.get("risk_level", "?")
        st = p.get("status", "?")

        click.echo(
            f"  {_risk_label(risk)}  "
            f"{click.style(pid, bold=True)}  "
            f"{tool:30s}  "
            f"{_status_label(st):10s}  "
            f"{ts}  "
            f"from={source}"
        )
    click.echo()


# ── show ──────────────────────────────────────────────────────────────────────

@cli.command("show")
@click.argument("proposal_id")
def show(proposal_id: str):
    """Show full details for a proposal."""
    matches = list(PENDING_DIR.glob(f"*{proposal_id[:8]}*.json"))
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
    click.echo(f"  Proposal: {click.style(pid, bold=True)}")
    click.echo(f"  Tool:     {click.style(d.get('tool','?'), fg='cyan')}")
    click.echo(f"  Target:   {d.get('commit_target','?')}")
    click.echo(f"  Status:   {_status_label(d.get('status','?'))}")
    click.echo(f"  Risk:     {_risk_label(d.get('risk_level','?'))}  {', '.join(d.get('risk_reasons',[]))}")
    click.echo(f"  Layer:    {d.get('proposed_layer','?')}  receipt={d.get('has_receipt', False)}")
    click.echo(f"  Source:   {d.get('source_instance','?')}")
    click.echo(f"  Session:  {d.get('session_id','?')}")
    click.echo(f"  Created:  {str(d.get('timestamp',''))[:19]}")

    if d.get("compass_check_result"):
        click.echo(f"  Compass:  {d['compass_check_result']} — {d.get('compass_check_rationale','')}")

    if d.get("reviewed_by"):
        click.echo(f"  Reviewed: {d['reviewed_by']} at {str(d.get('reviewed_at',''))[:19]}")

    if d.get("revision_notes"):
        click.echo(f"  Notes:    {d['revision_notes']}")

    click.echo(f"\n  Arguments:")
    args = d.get("arguments", {})
    for k, v in args.items():
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


# ── approve ───────────────────────────────────────────────────────────────────

@cli.command("approve")
@click.argument("proposal_id")
@click.option("--by", default="Anthony", help="Approver name")
def approve(proposal_id: str, by: str):
    """Approve a pending proposal. Does not commit — approve and commit are separate."""
    try:
        p = approve_pending_write(proposal_id, approved_by=by)
        click.echo(
            f"Approved: {_short(p.proposal_id)}  [{p.tool}]  "
            f"by={p.reviewed_by}  status={_status_label(p.status)}"
        )
        click.echo("  Run 'bridge commit <id>' to execute (still mocked unless --live).")
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ── reject ────────────────────────────────────────────────────────────────────

@cli.command("reject")
@click.argument("proposal_id")
@click.option("--reason", required=True, help="Rejection reason")
@click.option("--by", default="Anthony", help="Reviewer name")
def reject(proposal_id: str, reason: str, by: str):
    """Reject a pending proposal."""
    try:
        p = reject_pending_write(proposal_id, reason=reason, rejected_by=by)
        click.echo(
            f"Rejected: {_short(p.proposal_id)}  [{p.tool}]  "
            f"reason={reason}"
        )
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ── needs-revision ────────────────────────────────────────────────────────────

@cli.command("needs-revision")
@click.argument("proposal_id")
@click.option("--notes", required=True, help="Revision instructions")
@click.option("--by", default="Anthony", help="Reviewer name")
def needs_revision(proposal_id: str, notes: str, by: str):
    """Send a pending proposal back for revision with notes."""
    try:
        p = needs_revision_pending_write(proposal_id, notes=notes, actor=by)
        click.echo(
            f"Needs revision: {_short(p.proposal_id)}  [{p.tool}]  "
            f"notes={notes}"
        )
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ── commit ────────────────────────────────────────────────────────────────────

@cli.command("commit")
@click.argument("proposal_id")
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Write to the Stack for real. Without this flag, shows what would happen.",
)
def commit(proposal_id: str, live: bool):
    """
    Commit an approved proposal.

    Without --live: shows what would execute (safe, no Stack write).
    With --live: runs all precondition checks and writes to the Stack.

    ChatGPT cannot trigger --live. This terminal is the commit boundary.
    """
    try:
        p = commit_pending_write(proposal_id, live=live)
        result = p.commit_result or {}

        if not live:
            click.echo(f"\nDry-run commit (pass --live to execute):")
            click.echo(f"  proposal:  {_short(p.proposal_id)}")
            click.echo(f"  would call: {result.get('would_call', p.commit_target)}")
            click.echo(f"  with args:  {json.dumps(result.get('with_arguments', p.arguments), indent=4)}")
            click.echo()
            return

        # Live commit succeeded
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


# ── audit-tail ────────────────────────────────────────────────────────────────

@cli.command("audit-tail")
@click.option("--n", default=20, show_default=True, help="Number of recent entries to show")
@click.option("--proposal-id", default=None, help="Filter to a specific proposal")
def audit_tail(n: int, proposal_id: str | None):
    """Show recent audit log entries."""
    entries = read_audit_trail(proposal_id=proposal_id)
    entries = entries[-n:]

    if not entries:
        click.echo("Audit log is empty.")
        return

    click.echo(f"\nAudit log (last {len(entries)} entries):\n")
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


# ── verify-chain ──────────────────────────────────────────────────────────────

@cli.command("verify-chain")
def verify_chain_cmd():
    """Verify the audit log hash chain integrity."""
    ok, msg = verify_chain()
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
