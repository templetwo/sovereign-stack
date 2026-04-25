"""
MCP tools that surface connectivity + write-path status to any caller.

Built so other Claude instances (web, mobile, code) can probe the live
state of the Sovereign Stack from inside a conversation, without
shelling out to `sovereign-connectivity` directly.

Tools:
  connectivity_status       — current state of all managed endpoints
  stack_write_check         — verify the calling instance can write to
                              the chronicle (round-trip smoke test)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mcp.types import TextContent, Tool

from . import connectivity

# ── Tool registrations ──────────────────────────────────────────────────────


CONNECTIVITY_TOOLS = [
    Tool(
        name="connectivity_status",
        description=(
            "Show the live state of all Sovereign Stack endpoints "
            "(SSE, bridge, tunnel, dispatcher, listener, ollama). "
            "Returns per-endpoint status (ok/degraded/down/stale/unknown) "
            "with pid, http status, and notes. Read-only — no side effects. "
            "Use this when you want to confirm the stack is reachable + "
            "writes will land before doing critical work."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["pretty", "json"],
                    "default": "pretty",
                    "description": "pretty (text table) or json (raw aggregate)",
                },
            },
        },
    ),
    Tool(
        name="stack_write_check",
        description=(
            "Round-trip smoke test: write a marker insight to the chronicle, "
            "read it back, optionally clean up. Confirms the calling "
            "instance can WRITE to the stack — useful when bringing up a "
            "new device, after a restart, or when troubleshooting comms. "
            "By default the marker is left in chronicle/insights/ so the "
            "audit trail of write checks is preserved."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "Identifier for the calling instance "
                                   "(e.g. claude-iphone, claude-web). "
                                   "Recorded in the marker for attribution.",
                },
                "cleanup": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, remove the marker after readback. "
                                   "Default false — keeps the audit trail.",
                },
            },
            "required": ["instance_id"],
        },
    ),
]


# ── Implementation ──────────────────────────────────────────────────────────


def _sovereign_root() -> Path:
    return Path(os.environ.get(
        "SOVEREIGN_ROOT", Path.home() / ".sovereign",
    ))


def _format_pretty(agg: dict) -> str:
    """Pretty-print a connectivity aggregate for chat consumption."""
    lines = [f"━━ Connectivity ({agg['overall'].upper()}) ━━"]
    counts = "  ".join(f"{k}={v}" for k, v in sorted(agg["counts"].items()))
    if counts:
        lines.append(f"  {counts}")
    lines.append("")
    for ep in agg["endpoints"]:
        glyph = {
            connectivity.STATUS_OK: "✓",
            connectivity.STATUS_DEGRADED: "~",
            connectivity.STATUS_DOWN: "✗",
            connectivity.STATUS_STALE: "·",
            connectivity.STATUS_UNKNOWN: "?",
        }.get(ep["status"], "?")
        line = f"  {glyph} {ep['name']:<12} {ep['status'].upper():<10}"
        if ep.get("pid"):
            line += f" pid={ep['pid']}"
        if ep.get("http_status") is not None:
            line += f" http={ep['http_status']}"
        if ep.get("notes"):
            line += f" — {ep['notes'][0]}"
        lines.append(line)
    return "\n".join(lines)


def _do_connectivity_status(arguments: dict) -> str:
    fmt = (arguments or {}).get("format", "pretty")
    statuses = connectivity.check_all()
    agg = connectivity.aggregate(statuses)
    if fmt == "json":
        return json.dumps(agg, indent=2)
    return _format_pretty(agg)


def stack_write_check(
    instance_id: str,
    *,
    cleanup: bool = False,
    sovereign_root: Path | None = None,
) -> dict:
    """
    Smoke-test the chronicle write path for `instance_id`.

    Writes a small JSONL record under chronicle/insights/connectivity-test,
    write-path-verify/<instance_id>.jsonl, reads back the file, returns
    a structured result.

    Args:
        instance_id: Calling instance's identifier. Used in the marker
            content + filename for attribution. MUST be non-empty.
        cleanup: If True, remove the appended marker line after reading
            it back. Default False — the audit trail is intentional.
        sovereign_root: Override for testing.

    Returns:
        dict {ok, marker_path, marker_content, error}.
    """
    if not instance_id or not isinstance(instance_id, str):
        return {"ok": False, "error": "instance_id required and must be string"}

    root = sovereign_root or _sovereign_root()
    marker_dir = root / "chronicle" / "insights" / \
        "connectivity-test,write-path-verify"
    marker_dir.mkdir(parents=True, exist_ok=True)
    safe_instance = "".join(
        c for c in instance_id if c.isalnum() or c in "._-"
    ) or "unknown"
    marker_path = marker_dir / f"{safe_instance}.jsonl"

    timestamp = datetime.now(timezone.utc).isoformat()
    record = {
        "timestamp": timestamp,
        "domain": "connectivity-test,write-path-verify",
        "content": (
            f"[STACK-WRITE-CHECK] instance={instance_id} "
            f"verified write path at {timestamp}"
        ),
        "intensity": 0.2,
        "layer": "hypothesis",
        "session_id": f"write-check-{safe_instance}",
        "_check_marker": True,
    }
    line = json.dumps(record)
    try:
        with marker_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        return {
            "ok": False,
            "error": f"write_failed: {e}",
            "marker_path": str(marker_path),
        }

    # Readback — confirm the line is on disk.
    try:
        readback = marker_path.read_text(encoding="utf-8")
    except OSError as e:
        return {
            "ok": False,
            "error": f"readback_failed: {e}",
            "marker_path": str(marker_path),
        }
    if line not in readback:
        return {
            "ok": False,
            "error": "marker_line_not_found_after_write",
            "marker_path": str(marker_path),
        }

    if cleanup:
        # Strip the marker line we just added.
        kept = [ln for ln in readback.splitlines() if ln.strip() != line]
        if kept:
            marker_path.write_text("\n".join(kept) + "\n",
                                   encoding="utf-8")
        else:
            marker_path.unlink()

    return {
        "ok": True,
        "marker_path": str(marker_path),
        "marker_content": record,
        "cleaned_up": bool(cleanup),
    }


# ── MCP dispatcher ──────────────────────────────────────────────────────────


async def handle_connectivity_tool(name: str, arguments: dict):
    """Dispatch a connectivity_* MCP tool call."""
    arguments = arguments or {}

    if name == "connectivity_status":
        text = _do_connectivity_status(arguments)
        return [TextContent(type="text", text=text)]

    if name == "stack_write_check":
        instance_id = arguments.get("instance_id", "")
        cleanup = bool(arguments.get("cleanup", False))
        result = stack_write_check(instance_id, cleanup=cleanup)
        if result["ok"]:
            text = (
                f"✓ stack_write_check OK\n"
                f"  instance: {instance_id}\n"
                f"  marker: {result['marker_path']}\n"
                f"  cleaned_up: {result.get('cleaned_up', False)}"
            )
        else:
            text = (
                f"✗ stack_write_check FAILED\n"
                f"  instance: {instance_id}\n"
                f"  error: {result.get('error')}"
            )
        return [TextContent(type="text", text=text)]

    return [TextContent(type="text", text=f"Unknown connectivity tool: {name}")]
