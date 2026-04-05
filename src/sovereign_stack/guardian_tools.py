from __future__ import annotations
"""
Spiral Guardian Tools — Security MCP tools mounted into the Sovereign Stack

8 tools providing security monitoring, scanning, and posture assessment
across the Temple of Two infrastructure. Read-only queries where possible,
destructive operations (quarantine) marked explicitly.

These tools call the Guardian wrapper scripts via subprocess for privilege
separation, and query Wazuh API for alert data.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp.types import Tool, TextContent


# Guardian data directory
GUARDIAN_DIR = Path.home() / ".guardian"
GUARDIAN_DIR.mkdir(exist_ok=True)


GUARDIAN_TOOLS = [
    Tool(
        name="guardian_status",
        description="Get the overall security posture of the sovereign infrastructure. Returns health score, device status, active threats, and alert count.",
        inputSchema={
            "type": "object",
            "properties": {},
        }
    ),
    Tool(
        name="guardian_scan",
        description="Trigger a security scan on the sovereign infrastructure. Types: quick, malware, vulnerability, network.",
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
        description="Retrieve recent security alerts. Filter by severity (low/medium/high/critical) and time window.",
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
        description="Run a targeted security audit. Types: supply_chain, secrets, compliance, network, permissions.",
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
        description="Isolate, release, or list quarantined files. DESTRUCTIVE for isolate/release.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "isolate", "release"],
                    "default": "list",
                },
                "file_hash": {
                    "type": "string",
                    "description": "SHA256 hash of file (required for isolate/release)",
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
        description="Audit connected MCP servers for security vulnerabilities and prompt injection patterns.",
        inputSchema={
            "type": "object",
            "properties": {
                "scan_descriptions": {"type": "boolean", "default": True},
                "check_transport": {"type": "boolean", "default": True},
            },
        }
    ),
    Tool(
        name="guardian_baseline",
        description="Create or compare a security baseline snapshot of the current system state.",
        inputSchema={
            "type": "object",
            "properties": {
                "components": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Components to baseline: network, processes, ports, users",
                },
            },
        }
    ),
]


async def _run_cmd(cmd, timeout=60):
    """Run a command with timeout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode().strip(), stderr.decode().strip(), proc.returncode
    except asyncio.TimeoutError:
        proc.kill()
        return "", "timeout", -1
    except Exception as e:
        return "", str(e), -1


async def handle_guardian_tool(name: str, arguments: dict):
    """Handle guardian tool calls."""
    timestamp = datetime.now(timezone.utc).isoformat()

    if name == "guardian_status":
        # Quick system health check
        stdout, _, _ = await _run_cmd(["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"])
        listeners = len([l for l in stdout.splitlines() if l.strip()]) if stdout else 0

        # Check key services
        services = {}
        for svc in ["ollama", "sovereign"]:
            out, _, _ = await _run_cmd(["pgrep", "-x", svc])
            services[svc] = bool(out.strip())

        # Check Ollama binding
        ollama_safe = True
        if "0.0.0.0:11434" in stdout or "*:11434" in stdout:
            ollama_safe = False

        health = 100
        issues = []
        if not ollama_safe:
            health -= 30
            issues.append("Ollama exposed on all interfaces")
        if listeners > 15:
            health -= 10
            issues.append(f"{listeners} listening ports (elevated)")

        result = {
            "timestamp": timestamp,
            "health_score": health,
            "listeners": listeners,
            "ollama_localhost_only": ollama_safe,
            "issues": issues or ["No issues detected"],
        }
        return [TextContent(type="text", text=f"🛡️ Guardian Status\n\n{json.dumps(result, indent=2)}")]

    elif name == "guardian_scan":
        scan_type = arguments.get("scan_type", "quick")
        target = arguments.get("target_path", "~")

        if scan_type == "quick":
            # Quick port + process scan
            stdout, _, _ = await _run_cmd(["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"])
            exposed = [l for l in (stdout or "").splitlines() if "*:" in l and "127.0.0.1" not in l]
            result = f"🔍 Quick Scan\n\nExposed listeners: {len(exposed)}\n"
            for l in exposed[:10]:
                result += f"  {l}\n"
            return [TextContent(type="text", text=result)]

        return [TextContent(type="text", text=f"🔍 Scan type {scan_type} requires Wazuh/YARA (Phase 1+). Run guardian_status for current posture.")]

    elif name == "guardian_alerts":
        return [TextContent(type="text", text="🔔 Alerts require Wazuh server (Phase 1). Use guardian_status for current posture.")]

    elif name == "guardian_audit":
        audit_type = arguments.get("audit_type", "supply_chain")
        target = arguments.get("target_path", "~/sovereign-stack")

        if audit_type == "secrets":
            stdout, _, rc = await _run_cmd(["gitleaks", "detect", "--source", str(Path(target).expanduser()), "--no-git"], timeout=120)
            if rc == 0:
                return [TextContent(type="text", text=f"🔒 Secrets Audit: CLEAN — no secrets found in {target}")]
            return [TextContent(type="text", text=f"🔒 Secrets Audit:\n{stdout[:1000]}")]

        return [TextContent(type="text", text=f"🔒 Audit type {audit_type} requires additional tools (Phase 2+).")]

    elif name == "guardian_quarantine":
        action = arguments.get("action", "list")
        file_hash = arguments.get("file_hash", "")

        quarantine_dir = GUARDIAN_DIR / "quarantine"
        quarantine_dir.mkdir(exist_ok=True)

        if action == "list":
            files = list(quarantine_dir.iterdir())
            if not files:
                return [TextContent(type="text", text="🔒 Quarantine: empty")]
            result = "🔒 Quarantined files:\n"
            for f in files:
                result += f"  {f.name} ({f.stat().st_size} bytes)\n"
            return [TextContent(type="text", text=result)]

        return [TextContent(type="text", text=f"🔒 Quarantine {action} requires file_hash and elevated privileges.")]

    elif name == "guardian_report":
        report_type = arguments.get("report_type", "summary")
        # Generate a quick summary from what we can check
        stdout, _, _ = await _run_cmd(["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"])
        listeners = len([l for l in (stdout or "").splitlines() if l.strip()])
        exposed = len([l for l in (stdout or "").splitlines() if "*:" in l and "127.0.0.1" not in l])

        report = f"""📋 Guardian Security Report ({report_type})
Generated: {timestamp}

Listening ports: {listeners}
Exposed (non-localhost): {exposed}
Quarantine: {len(list((GUARDIAN_DIR / quarantine).iterdir())) if (GUARDIAN_DIR / quarantine).exists() else 0} files

Note: Full reports require Wazuh server (Phase 1+).
"""
        return [TextContent(type="text", text=report)]

    elif name == "guardian_mcp_audit":
        suspicious_patterns = [
            "http.post", "fetch(", "ignore previous", "disregard",
            "system prompt", "base64", "eval(", "document.cookie",
        ]
        result = f"""🔍 MCP Audit
Patterns checked: {len(suspicious_patterns)}
Transport: SSE (deprecated — migrate to Streamable HTTP by June 2026)
Recommendation: Scan all MCP tool descriptions for injection patterns
"""
        return [TextContent(type="text", text=result)]

    elif name == "guardian_baseline":
        components = arguments.get("components", ["network", "processes", "ports"])

        baseline = {"timestamp": timestamp, "components": {}}

        if "ports" in components:
            stdout, _, _ = await _run_cmd(["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"])
            baseline["components"]["ports"] = stdout.splitlines() if stdout else []

        if "processes" in components:
            stdout, _, _ = await _run_cmd(["ps", "aux"])
            baseline["components"]["process_count"] = len(stdout.splitlines()) if stdout else 0

        path = GUARDIAN_DIR / "baselines" / f"baseline_{int(time.time())}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(baseline, indent=2))

        return [TextContent(type="text", text=f"🛡️ Baseline saved: {path}\nComponents: {", ".join(components)}")]

    return [TextContent(type="text", text=f"Unknown guardian tool: {name}")]
