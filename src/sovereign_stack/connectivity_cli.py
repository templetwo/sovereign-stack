"""
sovereign-connectivity — CLI for the connectivity manager.

Usage:
    sovereign-connectivity                  # status (pretty)
    sovereign-connectivity status [--json]  # status, optionally JSON
    sovereign-connectivity restart <name|all>
    sovereign-connectivity start   <name|all>
    sovereign-connectivity stop    <name|all>
    sovereign-connectivity list             # show registry only
"""

from __future__ import annotations

import argparse
import json
import sys

from .connectivity import (
    ENDPOINTS,
    STATUS_DEGRADED,
    STATUS_DOWN,
    STATUS_OK,
    STATUS_STALE,
    STATUS_UNKNOWN,
    Endpoint,
    EndpointStatus,
    aggregate,
    check_all,
    get_endpoint,
    restart,
    start,
    stop,
)

# ── Output helpers ──────────────────────────────────────────────────────────


_STATUS_GLYPH = {
    STATUS_OK: "✓",
    STATUS_DEGRADED: "~",
    STATUS_DOWN: "✗",
    STATUS_STALE: "·",
    STATUS_UNKNOWN: "?",
}


def _format_status_row(s: EndpointStatus) -> str:
    glyph = _STATUS_GLYPH.get(s.status, "?")
    name_col = s.name.ljust(12)
    status_col = s.status.upper().ljust(9)
    pid_part = f"pid={s.pid}" if s.pid else "—"
    extra: list[str] = []
    if s.http_status is not None:
        extra.append(f"http={s.http_status}")
    if s.http_error:
        extra.append(f"http_err={s.http_error}")
    if s.log_age_seconds is not None:
        extra.append(f"log_age={int(s.log_age_seconds)}s")
    if s.notes:
        extra.append("; ".join(s.notes))
    extra_str = "  " + " | ".join(extra) if extra else ""
    return f"  {glyph} {name_col} {status_col} {pid_part}{extra_str}"


def _print_status(statuses: list[EndpointStatus]) -> None:
    summary = aggregate(statuses)
    overall = summary["overall"]
    glyph = _STATUS_GLYPH.get(overall, "?")
    print("━━━ Sovereign Stack Connectivity ━━━")
    print(f"  overall: {glyph} {overall.upper()}")
    counts_str = "  ".join(
        f"{k}={v}" for k, v in sorted(summary["counts"].items())
    )
    if counts_str:
        print(f"  counts:  {counts_str}")
    print()
    for s in statuses:
        print(_format_status_row(s))
    print()


# ── Action dispatch ─────────────────────────────────────────────────────────


def _resolve_targets(target: str) -> list[Endpoint]:
    if target == "all":
        return [e for e in ENDPOINTS if e.label]
    try:
        return [get_endpoint(target)]
    except KeyError:
        names = ", ".join(e.name for e in ENDPOINTS)
        raise SystemExit(f"unknown endpoint: {target!r}. known: {names}") from None


def _do_action(action: str, target: str, *, as_json: bool) -> int:
    targets = _resolve_targets(target)
    fn = {"start": start, "stop": stop, "restart": restart}[action]
    results = [fn(e) for e in targets]
    if as_json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        for r in results:
            ok_glyph = "✓" if r.ok else "✗"
            print(f"  {ok_glyph} {r.action} {r.name}: rc={r.returncode}")
            if r.stderr:
                print(f"      stderr: {r.stderr}")
    return 0 if all(r.ok for r in results) else 1


# ── Main entry ──────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sovereign-connectivity",
        description="Manage Sovereign Stack endpoints (launchd-backed).",
    )
    sub = p.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help="show endpoint status")
    p_status.add_argument("--json", action="store_true",
                          help="machine-readable output")

    sub.add_parser("list", help="list registered endpoints")

    for cmd in ("start", "stop", "restart"):
        sp = sub.add_parser(cmd, help=f"{cmd} an endpoint or 'all'")
        sp.add_argument("target", help="endpoint name or 'all'")
        sp.add_argument("--json", action="store_true",
                        help="machine-readable output")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "status"

    if command == "status":
        statuses = check_all()
        if getattr(args, "json", False):
            print(json.dumps(aggregate(statuses), indent=2))
        else:
            _print_status(statuses)
        # Exit nonzero on degraded/down so this can drive monitoring.
        summary = aggregate(statuses)
        return 0 if summary["overall"] == STATUS_OK else 2

    if command == "list":
        for e in ENDPOINTS:
            print(f"  {e.name:<12} {e.kind:<10} {e.label or '-':<40} "
                  f"{e.description}")
        return 0

    if command in ("start", "stop", "restart"):
        return _do_action(
            command, args.target, as_json=getattr(args, "json", False),
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
