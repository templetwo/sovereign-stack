"""
Spiral Guardian Tools — Security MCP tools mounted into the Sovereign Stack.

8 tools providing security monitoring, scanning, and posture assessment
across the Temple of Two infrastructure. Read-only queries where possible;
destructive operations (quarantine isolate/release) marked explicitly and
gated through an append-only manifest for audit.

Architecture (post-2026-04-25 expansion):
  * Per-tool helpers are pure-Python and synchronous where possible.
  * The async dispatcher (`handle_guardian_tool`) routes to helpers and
    wraps results in MCP TextContent — keeping helpers testable without
    needing an MCP runtime.
  * Data root is `_guardian_root()` (env-overridable via GUARDIAN_ROOT)
    instead of a module-level Path.home() side effect on import. Tests
    set the env var to a tempdir; production gets ~/.guardian.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

try:
    import httpx  # noqa: F401  (kept for future Wazuh integration)
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


# ── Data root (env-overridable, no side effects on import) ──────────────────


def _guardian_root() -> Path:
    """
    Return the Guardian data directory. Reads GUARDIAN_ROOT env var if
    set; otherwise defaults to ~/.guardian. Directory is created on
    first call, not on module import — so importing this module in a
    test does not pollute the user's home.
    """
    root = Path(os.environ.get("GUARDIAN_ROOT", Path.home() / ".guardian"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _quarantine_dir() -> Path:
    d = _guardian_root() / "quarantine"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _quarantine_manifest_path() -> Path:
    return _guardian_root() / "quarantine_manifest.jsonl"


def _baselines_dir() -> Path:
    d = _guardian_root() / "baselines"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── MCP tool registrations ──────────────────────────────────────────────────


GUARDIAN_TOOLS = [
    Tool(
        name="guardian_status",
        description="Get the overall security posture of the sovereign infrastructure. Returns health score, listening port count, key service presence, and Ollama bind safety.",
        inputSchema={
            "type": "object",
            "properties": {},
        }
    ),
    Tool(
        name="guardian_scan",
        description="Trigger a security scan on the sovereign infrastructure. Types: quick (port + listener exposure), malware/vulnerability/network (require Wazuh — Phase 1+).",
        inputSchema={
            "type": "object",
            "properties": {
                "scan_type": {
                    "type": "string",
                    "enum": ["quick", "malware", "vulnerability", "network"],
                    "default": "quick",
                },
                "target_path": {
                    "type": "string",
                    "default": "~",
                    "description": "Path to scan",
                },
            },
        }
    ),
    Tool(
        name="guardian_alerts",
        description="Retrieve recent security alerts. Filter by severity (low/medium/high/critical) and limit. Currently requires Wazuh (Phase 1).",
        inputSchema={
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "default": "high",
                },
                "limit": {"type": "integer", "default": 10},
            },
        }
    ),
    Tool(
        name="guardian_audit",
        description="Run a targeted security audit. Types: secrets (gitleaks), supply_chain/compliance/network/permissions (Phase 2+).",
        inputSchema={
            "type": "object",
            "properties": {
                "audit_type": {
                    "type": "string",
                    "enum": ["supply_chain", "secrets", "compliance", "network", "permissions"],
                    "default": "supply_chain",
                },
                "target_path": {"type": "string", "default": "~/sovereign-stack"},
            },
        }
    ),
    Tool(
        name="guardian_quarantine",
        description="Isolate, release, or list quarantined files. DESTRUCTIVE for isolate/release: isolate copies the file into ~/.guardian/quarantine/, then removes the original; release reverses. All actions logged to quarantine_manifest.jsonl.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "isolate", "release"],
                    "default": "list",
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file (required for isolate).",
                    "default": "",
                },
                "file_hash": {
                    "type": "string",
                    "description": "SHA256 hash of the quarantined file (required for release).",
                    "default": "",
                },
            },
        }
    ),
    Tool(
        name="guardian_report",
        description="Generate a security report. Types: summary, detailed, compliance.",
        inputSchema={
            "type": "object",
            "properties": {
                "report_type": {
                    "type": "string",
                    "enum": ["summary", "detailed", "compliance"],
                    "default": "summary",
                },
                "time_period": {
                    "type": "string",
                    "enum": ["24h", "7d", "30d"],
                    "default": "7d",
                },
            },
        }
    ),
    Tool(
        name="guardian_mcp_audit",
        description="Audit MCP tool descriptions for prompt-injection / suspicious patterns. Pass `descriptions` to scan an explicit list, or omit to auto-load from claude_desktop_config.json. Returns matched-pattern hits with snippets.",
        inputSchema={
            "type": "object",
            "properties": {
                "descriptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tool descriptions to scan. Omit to load from Claude Desktop config.",
                },
                "config_path": {
                    "type": "string",
                    "description": "Override path to a Claude Desktop config JSON. Default: ~/Library/Application Support/Claude/claude_desktop_config.json",
                    "default": "",
                },
            },
        }
    ),
    Tool(
        name="guardian_baseline",
        description="Create or compare a security baseline snapshot. action='create' (default) records current state; action='compare' diffs against the most recent baseline and reports drift.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "compare"],
                    "default": "create",
                },
                "components": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Components to baseline: ports, processes, network, users.",
                },
            },
        }
    ),
]


# ── Subprocess helper (async; the only reason the dispatcher is async) ──────


async def _run_cmd(cmd, timeout=60) -> tuple[str, str, int]:
    """Run a command with timeout. Returns (stdout, stderr, returncode)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode().strip(), stderr.decode().strip(), proc.returncode
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        return "", "timeout", -1
    except Exception as e:
        return "", str(e), -1


# ── Pure helpers (testable without MCP runtime) ─────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_sha256(path: Path) -> str:
    """SHA256 of a file, hex digest. Streams in chunks for large files."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# Suspicious patterns for MCP tool description scanning.
# Mix of prompt-injection markers, exfil signals, and dangerous code-shaped
# fragments. Case-insensitive substring match — keep entries lowercase here.
MCP_SUSPICIOUS_PATTERNS: tuple[str, ...] = (
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "disregard prior",
    "system prompt",
    "you are now",
    "override instructions",
    "http.post",
    "http://",
    "fetch(",
    "eval(",
    "exec(",
    "base64",
    "document.cookie",
    "process.env",
    "os.system",
    "subprocess",
    "rm -rf",
    "/etc/passwd",
)


# ── guardian_status ─────────────────────────────────────────────────────────


def _evaluate_status(listener_lines: list[str], service_present: dict[str, bool]) -> dict:
    """
    Pure scoring logic for guardian_status. Takes pre-collected
    inputs (listener output + service-presence map) and returns the
    structured posture dict. Subprocess calls live in the async wrapper.
    """
    listeners = len([ln for ln in listener_lines if ln.strip()])
    full_text = "\n".join(listener_lines)
    ollama_safe = not (
        "0.0.0.0:11434" in full_text or "*:11434" in full_text
    )

    health = 100
    issues: list[str] = []
    if not ollama_safe:
        health -= 30
        issues.append("Ollama exposed on all interfaces")
    if listeners > 15:
        health -= 10
        issues.append(f"{listeners} listening ports (elevated)")

    return {
        "timestamp": _now_iso(),
        "health_score": health,
        "listeners": listeners,
        "ollama_localhost_only": ollama_safe,
        "services": service_present,
        "issues": issues or ["No issues detected"],
    }


async def _status_async() -> dict:
    stdout, _, _ = await _run_cmd(["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"])
    services: dict[str, bool] = {}
    for svc in ["ollama", "sovereign"]:
        out, _, _ = await _run_cmd(["pgrep", "-x", svc])
        services[svc] = bool(out.strip())
    return _evaluate_status((stdout or "").splitlines(), services)


# ── guardian_scan ───────────────────────────────────────────────────────────


def _filter_exposed_listeners(listener_lines: list[str]) -> list[str]:
    """Lines representing non-localhost listeners (exposed to network)."""
    return [
        ln for ln in listener_lines
        if "*:" in ln and "127.0.0.1" not in ln and "[::1]" not in ln
    ]


# ── guardian_audit (secrets via gitleaks) ───────────────────────────────────
# The gitleaks invocation lives in the async dispatcher because it shells
# out; the result-formatting is trivial and stays inline.


# ── guardian_quarantine ─────────────────────────────────────────────────────


def list_quarantine() -> list[dict]:
    """Return the active quarantine roster as a list of records."""
    qdir = _quarantine_dir()
    out: list[dict] = []
    for entry in sorted(qdir.iterdir()):
        if not entry.is_file():
            continue
        if not entry.name.endswith(".bin"):
            continue
        stat = entry.stat()
        digest = entry.stem  # filename is "<sha256>.bin"
        meta_path = qdir / f"{digest}.meta.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                meta = {}
        out.append({
            "file_hash": digest,
            "size_bytes": stat.st_size,
            "isolated_at": meta.get("isolated_at"),
            "original_path": meta.get("original_path"),
        })
    return out


def _append_manifest(record: dict) -> None:
    """Append-only audit log of every isolate/release event."""
    path = _quarantine_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def isolate_file(file_path: str) -> dict:
    """
    DESTRUCTIVE: copy the file into the quarantine, remove the original.

    The file is stored at quarantine/<sha256>.bin and a sibling meta.json
    records the original path + timestamp + size. Every isolate event
    appends to quarantine_manifest.jsonl.
    """
    src = Path(file_path).expanduser().resolve()
    if not src.exists():
        return {"ok": False, "error": "file_not_found", "path": str(src)}
    if not src.is_file():
        return {"ok": False, "error": "not_a_regular_file", "path": str(src)}

    digest = _file_sha256(src)
    qdir = _quarantine_dir()
    dest = qdir / f"{digest}.bin"
    meta_path = qdir / f"{digest}.meta.json"

    if dest.exists():
        # Already quarantined — record the second isolate request but
        # leave the existing copy in place (idempotent).
        size = dest.stat().st_size
        _append_manifest({
            "action": "isolate_idempotent",
            "file_hash": digest,
            "original_path": str(src),
            "size_bytes": size,
            "timestamp": _now_iso(),
        })
        # Even on idempotent isolate, the source must go (that's the point).
        try:
            src.unlink()
        except Exception as e:
            return {"ok": False, "error": f"original_unlink_failed: {e}",
                    "file_hash": digest}
        return {
            "ok": True,
            "file_hash": digest,
            "quarantine_path": str(dest),
            "idempotent": True,
        }

    size = src.stat().st_size
    shutil.copy2(src, dest)
    with contextlib.suppress(Exception):
        os.chmod(dest, 0o600)  # best effort — restrict access to owner
    meta_path.write_text(json.dumps({
        "file_hash": digest,
        "original_path": str(src),
        "isolated_at": _now_iso(),
        "size_bytes": size,
    }, indent=2))

    # Remove the original AFTER the copy is on disk.
    try:
        src.unlink()
    except Exception as e:
        # Quarantine copy stands; flag the partial state.
        _append_manifest({
            "action": "isolate_partial",
            "file_hash": digest,
            "original_path": str(src),
            "error": f"original_unlink_failed: {e}",
            "timestamp": _now_iso(),
        })
        return {
            "ok": False,
            "error": f"original_unlink_failed: {e}",
            "file_hash": digest,
            "quarantine_path": str(dest),
        }

    _append_manifest({
        "action": "isolate",
        "file_hash": digest,
        "original_path": str(src),
        "size_bytes": size,
        "timestamp": _now_iso(),
    })
    return {
        "ok": True,
        "file_hash": digest,
        "quarantine_path": str(dest),
        "size_bytes": size,
        "original_path": str(src),
    }


def release_file(file_hash: str) -> dict:
    """
    DESTRUCTIVE: restore a quarantined file to its original_path and
    remove from quarantine. The release event is logged to manifest.
    """
    qdir = _quarantine_dir()
    src = qdir / f"{file_hash}.bin"
    meta_path = qdir / f"{file_hash}.meta.json"

    if not src.exists():
        return {"ok": False, "error": "not_in_quarantine",
                "file_hash": file_hash}
    if not meta_path.exists():
        return {"ok": False, "error": "manifest_missing",
                "file_hash": file_hash}

    try:
        meta = json.loads(meta_path.read_text())
    except Exception as e:
        return {"ok": False, "error": f"meta_unreadable: {e}",
                "file_hash": file_hash}

    original_path = meta.get("original_path")
    if not original_path:
        return {"ok": False, "error": "manifest_missing_original_path",
                "file_hash": file_hash}

    dest = Path(original_path)
    # Refuse to clobber a file that already exists at the original path.
    if dest.exists():
        return {"ok": False, "error": "destination_exists",
                "file_hash": file_hash, "original_path": original_path}

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    src.unlink()
    meta_path.unlink()

    _append_manifest({
        "action": "release",
        "file_hash": file_hash,
        "restored_to": str(dest),
        "timestamp": _now_iso(),
    })
    return {
        "ok": True,
        "file_hash": file_hash,
        "restored_to": str(dest),
    }


# ── guardian_mcp_audit ──────────────────────────────────────────────────────


def _scan_descriptions(
    descriptions: Iterable[tuple[str, str, str]],
    patterns: Iterable[str] = MCP_SUSPICIOUS_PATTERNS,
) -> list[dict]:
    """
    Scan tool descriptions for suspicious patterns.

    Args:
        descriptions: Iterable of (server, tool, text) tuples.
        patterns: Lowercase substrings to match (case-insensitive).

    Returns:
        List of hit dicts: {server, tool, pattern, snippet}.
    """
    pats = [p.lower() for p in patterns]
    hits: list[dict] = []
    for server, tool, text in descriptions:
        if not text:
            continue
        lower = text.lower()
        for p in pats:
            idx = lower.find(p)
            if idx == -1:
                continue
            start = max(0, idx - 30)
            end = min(len(text), idx + len(p) + 30)
            hits.append({
                "server": server,
                "tool": tool,
                "pattern": p,
                "snippet": text[start:end],
            })
    return hits


def _load_descriptions_from_config(config_path: Path) -> list[tuple[str, str, str]]:
    """
    Pull tool descriptions out of a Claude Desktop config JSON. The file
    structure is `{"mcpServers": {"<name>": {...}}}`. Tool descriptions
    are NOT in the config (they come from each server at runtime), so
    this scans server-level free text fields like `command`, `args`,
    `description`, and any string values that might harbor injection
    text. Each server contributes one synthetic "description" entry
    consisting of its serialized config — better than nothing for
    detecting injected text in env vars / args.
    """
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text())
    except Exception:
        return []
    out: list[tuple[str, str, str]] = []
    servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
    if not isinstance(servers, dict):
        return out
    for name, conf in servers.items():
        try:
            text = json.dumps(conf, ensure_ascii=False)
        except Exception:
            continue
        out.append((name, "<server-config>", text))
    return out


def default_claude_desktop_config_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json"
    )


def mcp_audit(
    descriptions: list[str] | None = None,
    config_path: Path | None = None,
) -> dict:
    """
    Run an MCP audit. If `descriptions` is provided, scan those strings
    directly (server/tool reported as "<arg>"). Otherwise load from
    config_path (default: Claude Desktop's config) and scan each server's
    serialized config block.

    Returns:
        {patterns_checked, sources_scanned, hits: [...], transport_warning}
    """
    sources: list[tuple[str, str, str]] = []
    if descriptions:
        for i, d in enumerate(descriptions):
            sources.append(("<arg>", f"description_{i}", d))
    else:
        path = config_path or default_claude_desktop_config_path()
        sources = _load_descriptions_from_config(path)

    hits = _scan_descriptions(sources)
    return {
        "patterns_checked": len(MCP_SUSPICIOUS_PATTERNS),
        "sources_scanned": len(sources),
        "hits": hits,
        "transport_warning": (
            "SSE is deprecated as MCP transport — migrate to "
            "Streamable HTTP by June 2026."
        ),
        "timestamp": _now_iso(),
    }


# ── guardian_baseline ───────────────────────────────────────────────────────


def _latest_baseline_path() -> Path | None:
    bdir = _baselines_dir()
    files = sorted(bdir.glob("baseline_*.json"))
    return files[-1] if files else None


def _diff_lists(prior: list[str], current: list[str]) -> dict:
    """Set-style diff: items added / removed between two list snapshots."""
    p = set(prior or [])
    c = set(current or [])
    return {
        "added": sorted(c - p),
        "removed": sorted(p - c),
        "unchanged_count": len(p & c),
    }


def compare_baseline(current: dict, prior: dict) -> dict:
    """
    Diff two baseline records. Each record has a `components` dict
    keyed by component name. Returns a structured drift report.
    """
    drift: dict[str, Any] = {
        "prior_timestamp": prior.get("timestamp"),
        "current_timestamp": current.get("timestamp"),
        "components": {},
    }
    p_comps = prior.get("components", {}) if isinstance(prior, dict) else {}
    c_comps = current.get("components", {}) if isinstance(current, dict) else {}
    keys = set(p_comps.keys()) | set(c_comps.keys())
    for key in sorted(keys):
        p_val = p_comps.get(key)
        c_val = c_comps.get(key)
        if isinstance(p_val, list) and isinstance(c_val, list):
            drift["components"][key] = _diff_lists(p_val, c_val)
        elif isinstance(p_val, (int, float)) and isinstance(c_val, (int, float)):
            drift["components"][key] = {
                "prior": p_val, "current": c_val, "delta": c_val - p_val,
            }
        else:
            drift["components"][key] = {"prior": p_val, "current": c_val}
    return drift


# ── Async dispatcher (the MCP entry point) ──────────────────────────────────


async def _gather_baseline_components(components: list[str]) -> dict:
    baseline: dict[str, Any] = {
        "timestamp": _now_iso(),
        "components": {},
    }
    if "ports" in components:
        stdout, _, _ = await _run_cmd(
            ["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"]
        )
        baseline["components"]["ports"] = (
            (stdout or "").splitlines() if stdout else []
        )
    if "processes" in components:
        stdout, _, _ = await _run_cmd(["ps", "aux"])
        baseline["components"]["process_count"] = (
            len((stdout or "").splitlines()) if stdout else 0
        )
    if "network" in components:
        stdout, _, _ = await _run_cmd(["netstat", "-an"])
        baseline["components"]["network_lines"] = (
            len((stdout or "").splitlines()) if stdout else 0
        )
    if "users" in components:
        stdout, _, _ = await _run_cmd(["who"])
        baseline["components"]["users"] = (
            (stdout or "").splitlines() if stdout else []
        )
    return baseline


async def handle_guardian_tool(name: str, arguments: dict):
    """Handle guardian tool calls. Routes to per-tool helpers + MCP-wraps."""

    if name == "guardian_status":
        result = await _status_async()
        return [TextContent(
            type="text",
            text=f"🛡️ Guardian Status\n\n{json.dumps(result, indent=2)}",
        )]

    if name == "guardian_scan":
        scan_type = arguments.get("scan_type", "quick")

        if scan_type == "quick":
            stdout, _, _ = await _run_cmd(
                ["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"]
            )
            exposed = _filter_exposed_listeners((stdout or "").splitlines())
            text = f"🔍 Quick Scan\n\nExposed listeners: {len(exposed)}\n"
            for ln in exposed[:10]:
                text += f"  {ln}\n"
            return [TextContent(type="text", text=text)]

        return [TextContent(
            type="text",
            text=f"🔍 Scan type {scan_type} requires Wazuh/YARA (Phase 1+). "
                 f"Run guardian_status for current posture.",
        )]

    if name == "guardian_alerts":
        return [TextContent(
            type="text",
            text="🔔 Alerts require Wazuh server (Phase 1). "
                 "Use guardian_status for current posture.",
        )]

    if name == "guardian_audit":
        audit_type = arguments.get("audit_type", "supply_chain")
        target = arguments.get("target_path", "~/sovereign-stack")

        if audit_type == "secrets":
            stdout, _, rc = await _run_cmd(
                ["gitleaks", "detect", "--source",
                 str(Path(target).expanduser()), "--no-git"],
                timeout=120,
            )
            if rc == 0:
                return [TextContent(
                    type="text",
                    text=f"🔒 Secrets Audit: CLEAN — no secrets found in {target}",
                )]
            return [TextContent(
                type="text",
                text=f"🔒 Secrets Audit:\n{stdout[:1000]}",
            )]

        return [TextContent(
            type="text",
            text=f"🔒 Audit type {audit_type} requires additional tools (Phase 2+).",
        )]

    if name == "guardian_quarantine":
        action = arguments.get("action", "list")

        if action == "list":
            entries = list_quarantine()
            if not entries:
                return [TextContent(type="text", text="🔒 Quarantine: empty")]
            text = f"🔒 Quarantined files ({len(entries)}):\n"
            for e in entries:
                text += (
                    f"  {e['file_hash'][:16]}…  {e['size_bytes']}B  "
                    f"orig={e.get('original_path','?')}\n"
                )
            return [TextContent(type="text", text=text)]

        if action == "isolate":
            file_path = arguments.get("file_path", "")
            if not file_path:
                return [TextContent(
                    type="text",
                    text="🔒 isolate requires `file_path`.",
                )]
            result = isolate_file(file_path)
            return [TextContent(
                type="text",
                text=f"🔒 Isolate result\n\n{json.dumps(result, indent=2)}",
            )]

        if action == "release":
            file_hash = arguments.get("file_hash", "")
            if not file_hash:
                return [TextContent(
                    type="text",
                    text="🔒 release requires `file_hash`.",
                )]
            result = release_file(file_hash)
            return [TextContent(
                type="text",
                text=f"🔒 Release result\n\n{json.dumps(result, indent=2)}",
            )]

        return [TextContent(
            type="text",
            text=f"🔒 Unknown quarantine action: {action}",
        )]

    if name == "guardian_report":
        report_type = arguments.get("report_type", "summary")
        timestamp = _now_iso()
        stdout, _, _ = await _run_cmd(
            ["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"]
        )
        listener_lines = (stdout or "").splitlines()
        listeners = len([ln for ln in listener_lines if ln.strip()])
        exposed = len(_filter_exposed_listeners(listener_lines))
        # Bug fix (2026-04-25): the previous version referenced an
        # undefined bareword `quarantine` here, causing a NameError
        # whenever guardian_report was invoked. Now uses _quarantine_dir().
        qdir = _quarantine_dir()
        quarantined = len([f for f in qdir.iterdir()
                           if f.is_file() and f.name.endswith(".bin")])

        report = (
            f"📋 Guardian Security Report ({report_type})\n"
            f"Generated: {timestamp}\n\n"
            f"Listening ports: {listeners}\n"
            f"Exposed (non-localhost): {exposed}\n"
            f"Quarantine: {quarantined} files\n\n"
            f"Note: Full reports require Wazuh server (Phase 1+).\n"
        )
        return [TextContent(type="text", text=report)]

    if name == "guardian_mcp_audit":
        descriptions = arguments.get("descriptions")
        config_path_arg = arguments.get("config_path", "")
        config_path = Path(config_path_arg) if config_path_arg else None
        result = mcp_audit(
            descriptions=descriptions or None,
            config_path=config_path,
        )
        text = (
            f"🔍 MCP Audit\n\n"
            f"Patterns checked: {result['patterns_checked']}\n"
            f"Sources scanned: {result['sources_scanned']}\n"
            f"Hits: {len(result['hits'])}\n"
        )
        for h in result["hits"][:10]:
            text += (
                f"  [{h['server']}/{h['tool']}] pattern='{h['pattern']}' "
                f"…{h['snippet']}…\n"
            )
        text += f"\n{result['transport_warning']}\n"
        return [TextContent(type="text", text=text)]

    if name == "guardian_baseline":
        action = arguments.get("action", "create")
        components = arguments.get("components") or [
            "ports", "processes", "network",
        ]

        if action == "create":
            baseline = await _gather_baseline_components(components)
            path = _baselines_dir() / f"baseline_{int(time.time())}.json"
            path.write_text(json.dumps(baseline, indent=2))
            comp_list = ", ".join(components)
            return [TextContent(
                type="text",
                text=f"🛡️ Baseline saved: {path}\nComponents: {comp_list}",
            )]

        if action == "compare":
            prior_path = _latest_baseline_path()
            if not prior_path:
                return [TextContent(
                    type="text",
                    text="🛡️ No prior baseline found. Run with action=create first.",
                )]
            try:
                prior = json.loads(prior_path.read_text())
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=f"🛡️ Prior baseline unreadable: {e}",
                )]
            current = await _gather_baseline_components(components)
            drift = compare_baseline(current, prior)
            return [TextContent(
                type="text",
                text=(
                    f"🛡️ Baseline drift\n\n"
                    f"Prior:   {drift['prior_timestamp']} "
                    f"({prior_path.name})\n"
                    f"Current: {drift['current_timestamp']}\n\n"
                    + json.dumps(drift["components"], indent=2)
                ),
            )]

        return [TextContent(
            type="text",
            text=f"🛡️ Unknown baseline action: {action}",
        )]

    return [TextContent(type="text", text=f"Unknown guardian tool: {name}")]
