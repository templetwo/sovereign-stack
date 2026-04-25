"""
sovereign-dashboard — CLI entry point for the live activity monitor.

Usage:
    sovereign-dashboard                       # continuous TUI
    sovereign-dashboard --interval 5          # custom poll cadence
    sovereign-dashboard --once                # render once and exit
    sovereign-dashboard --once --json         # one snapshot, JSON
    sovereign-dashboard --no-bridge           # skip bridge polling
    sovereign-dashboard --bridge-url URL ...  # explicit bridge address
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from . import dashboard

DEFAULT_BRIDGE_URL = os.environ.get("SOVEREIGN_BRIDGE_URL",
                                    "http://127.0.0.1:8100")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sovereign-dashboard",
        description="Live activity monitor for Sovereign Stack.",
    )
    p.add_argument(
        "--interval", type=int, default=dashboard.DEFAULT_POLL_SECONDS,
        help="poll interval in seconds (default: %(default)s)",
    )
    p.add_argument(
        "--once", action="store_true",
        help="render one frame and exit (useful for cron / debugging)",
    )
    p.add_argument(
        "--json", action="store_true",
        help="emit a JSON snapshot to stdout (with --once)",
    )
    p.add_argument(
        "--no-bridge", action="store_true",
        help="skip bridge polling — use when bridge is down or unwanted",
    )
    p.add_argument(
        "--bridge-url", default=DEFAULT_BRIDGE_URL,
        help="bridge base URL (default: %(default)s)",
    )
    p.add_argument(
        "--bridge-token", default=os.environ.get("SOVEREIGN_BRIDGE_TOKEN", ""),
        help="bridge bearer token (default: $SOVEREIGN_BRIDGE_TOKEN)",
    )
    p.add_argument(
        "--instance-id", default="dashboard",
        help="instance id used for comms-unread queries",
    )
    p.add_argument(
        "--no-color", action="store_true", help="disable ANSI colors",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    bridge_url = None if args.no_bridge else args.bridge_url
    bridge_token = args.bridge_token if not args.no_bridge else None

    if args.once and args.json:
        # Build a single snapshot without rendering / async loop.
        feed = dashboard.ActivityFeed()
        state = dashboard.collect_state(feed)
        snapshot = {
            "timestamp": state.timestamp,
            "connectivity": state.connectivity_summary,
            "bridge": {
                "reachable": state.bridge_stats.bridge_reachable,
                "phase": state.bridge_stats.phase,
                "tool_calls": state.bridge_stats.tool_calls,
                "reflection_depth": state.bridge_stats.reflection_depth,
                "duration_seconds": state.bridge_stats.duration_seconds,
                "comms_unread": state.bridge_stats.comms_unread,
            },
            "halts_count": state.halts_count,
            "decisions_count": state.decisions_count,
            "unacked_honks": state.unacked_honks,
            "listener_stale": state.listener_stale,
        }
        print(json.dumps(snapshot, indent=2))
        return 0

    try:
        asyncio.run(dashboard.run_loop(
            interval=args.interval,
            bridge_url=bridge_url,
            bridge_token=bridge_token,
            instance_id=args.instance_id,
            once=args.once,
            color=not args.no_color,
        ))
    except KeyboardInterrupt:
        print("\ndashboard stopped — stack continues to breathe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
