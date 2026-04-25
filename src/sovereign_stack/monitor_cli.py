"""
sovereign-monitor — CLI for the auto-recovery monitor.

Usage:
    sovereign-monitor                       # 30s interval, real restarts
    sovereign-monitor --interval 60         # custom interval
    sovereign-monitor --dry-run             # log what would be done
    sovereign-monitor --once                # one tick + exit (testing)
    sovereign-monitor --exclude listener    # skip endpoints
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import List, Optional

from .monitor import (
    DEFAULT_INTERVAL,
    DEFAULT_MAX_RESTARTS,
    MonitorConfig,
    run_loop,
    run_once,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sovereign-monitor",
        description="Auto-recovery monitor for Sovereign Stack endpoints.",
    )
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                   help="seconds between checks (default: %(default)s)")
    p.add_argument("--max-restarts", type=int, default=DEFAULT_MAX_RESTARTS,
                   help="per-endpoint restart cap before giving up "
                        "(default: %(default)s)")
    p.add_argument("--dry-run", action="store_true",
                   help="log decisions without invoking launchctl")
    p.add_argument("--once", action="store_true",
                   help="run a single tick and exit")
    p.add_argument("--exclude", action="append", default=[],
                   help="endpoint name to skip (repeatable)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON tick summary on --once")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    config = MonitorConfig(
        interval=args.interval,
        max_restarts=args.max_restarts,
        dry_run=args.dry_run,
        exclude=args.exclude,
    )

    if args.once:
        summary = run_once(config)
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"checked: {summary['checked']}")
            print(f"down: {summary['down']}")
            print(f"degraded: {summary['degraded']}")
            for a in summary["actions"]:
                print(f"  - {a}")
        return 0

    try:
        asyncio.run(run_loop(config))
    except KeyboardInterrupt:
        print("\nmonitor stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
