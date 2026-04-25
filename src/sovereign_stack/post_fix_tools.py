"""
Post-fix verification — drift watches for fixes that look clean.

The core insight from the 2026-04-23 503 hunt: a fix can pass immediate
verification and still drift minutes later because the signal we checked was
the wrong one (origin health vs. edge health; load-balancer drift vs. daemon
state). A `post_fix_verify` call captures a named baseline of the fix-relevant
surface, then re-samples at a schedule that matches how drift actually
manifests — short-early-then-spaced. Drift emits a Nape honk so it flows
through the existing critique channel instead of inventing a new alert path.

Storage layout under SOVEREIGN_ROOT/post_fix/:
  watches/<watch_id>.json          — active watches
  watches/archive/<watch_id>.json  — completed / cancelled watches
  events.jsonl                      — append-only audit trail

Watch lifecycle:
  active → completed_clean         (all scheduled samples ran with no drift)
  active → drift_detected          (at least one sample diverged from baseline)
  active → cancelled               (operator intent)

Probe types (extensible):
  http        — urlopen + status check, optional N-sample success_rate_min
  command     — subprocess, check exit_code / stdout_contains / stdout_regex
  file_hash   — sha256 of a file, compared against baseline hash

The module is self-contained: it neither blocks on long probes nor spawns
threads. Scheduling is external (see scripts/sovereign-watch-tick). Call
take_sample(watch_id) once per due offset; it runs the earliest due sample,
records the result, and closes the watch when all offsets have been sampled.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mcp.types import TextContent, Tool

# =============================================================================
# PATHS & CONSTANTS
# =============================================================================

def _root() -> Path:
    """Resolve SOVEREIGN_ROOT at call time so tests can monkeypatch it."""
    return Path(os.environ.get("SOVEREIGN_ROOT", str(Path.home() / ".sovereign")))


def _post_fix_root() -> Path:
    return _root() / "post_fix"


def _watches_dir() -> Path:
    return _post_fix_root() / "watches"


def _archive_dir() -> Path:
    return _watches_dir() / "archive"


def _events_path() -> Path:
    return _post_fix_root() / "events.jsonl"


# Default re-sample schedule in minutes from baseline.
# Short-early-then-spaced: catch fast regressions (LB drift, cert flap) in the
# first 5-30 min, and catch slow ones (cache invalidation, scheduled jobs) over
# the 24h window. These offsets are overridable per watch.
DEFAULT_SCHEDULE = [5, 30, 120, 1440]

# Probe execution bounds.
HTTP_DEFAULT_TIMEOUT = 5
HTTP_DEFAULT_SAMPLES = 1
HTTP_SAMPLE_SPACING_SEC = 0.2  # Small gap so N-sample bursts don't saturate a single connection pool.
COMMAND_DEFAULT_TIMEOUT = 30

# Nape pattern name emitted on drift. Registered in nape_daemon.PATTERN_LEVELS.
NAPE_PATTERN_DRIFT = "post_fix_drift"


# =============================================================================
# TIME HELPERS
# =============================================================================

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    # Strip a trailing Z if present; datetime.fromisoformat doesn't accept it pre-3.11.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


# =============================================================================
# STORAGE
# =============================================================================

def _ensure_dirs() -> None:
    _watches_dir().mkdir(parents=True, exist_ok=True)
    _archive_dir().mkdir(parents=True, exist_ok=True)


def _append_event(event: dict[str, Any]) -> None:
    _ensure_dirs()
    event = {"ts": _iso(_now()), **event}
    with _events_path().open("a") as f:
        f.write(json.dumps(event) + "\n")


def _watch_path(watch_id: str, archived: bool = False) -> Path:
    base = _archive_dir() if archived else _watches_dir()
    return base / f"{watch_id}.json"


def load_watch(watch_id: str) -> dict[str, Any] | None:
    """Load an active or archived watch by id. Returns None if not found."""
    for archived in (False, True):
        p = _watch_path(watch_id, archived=archived)
        if p.exists():
            return json.loads(p.read_text())
    return None


def save_watch(watch: dict[str, Any]) -> None:
    """Persist a watch. Archived watches move to archive/; active stay in watches/."""
    _ensure_dirs()
    watch_id = watch["watch_id"]
    status = watch.get("status", "active")
    active_path = _watch_path(watch_id, archived=False)
    archive_path = _watch_path(watch_id, archived=True)
    target = archive_path if status in ("completed_clean", "cancelled") else active_path
    target.write_text(json.dumps(watch, indent=2))
    # If we just archived a formerly-active watch, remove the active copy.
    if target == archive_path and active_path.exists():
        active_path.unlink()


def list_watches(status: str | None = None) -> list[dict[str, Any]]:
    """
    List watches filtered by status.

    status=None         — all active watches (not archived)
    status="active"     — same as None
    status="all"        — active + archived
    status=<other>      — filter by exact status field
    """
    _ensure_dirs()
    watches: list[dict[str, Any]] = []
    dirs: list[Path] = [_watches_dir()]
    if status == "all" or status in ("completed_clean", "drift_detected", "cancelled"):
        dirs.append(_archive_dir())
    for d in dirs:
        for p in d.glob("pfw_*.json"):
            try:
                w = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if status in (None, "active", "all"):
                if status == "all" or w.get("status") == "active":
                    watches.append(w)
            elif w.get("status") == status:
                watches.append(w)
    watches.sort(key=lambda w: w.get("created_at", ""), reverse=True)
    return watches


# =============================================================================
# PROBE EXECUTION
# =============================================================================

def _run_http_probe(probe: dict[str, Any]) -> dict[str, Any]:
    url = probe["url"]
    method = probe.get("method", "GET")
    timeout = probe.get("timeout_sec", HTTP_DEFAULT_TIMEOUT)
    samples = max(1, int(probe.get("samples", HTTP_DEFAULT_SAMPLES)))
    expected_status = probe.get("expected", {}).get("status", 200)

    ok_count = 0
    status_codes: list[int] = []
    errors: list[str] = []
    for i in range(samples):
        if i > 0:
            time.sleep(HTTP_SAMPLE_SPACING_SEC)
        try:
            req = Request(url, method=method)
            with urlopen(req, timeout=timeout) as resp:
                status_codes.append(resp.status)
                if resp.status == expected_status:
                    ok_count += 1
        except HTTPError as e:
            status_codes.append(e.code)
            if e.code == expected_status:
                ok_count += 1
        except (URLError, TimeoutError, OSError) as e:
            errors.append(str(e))
            status_codes.append(0)

    success_rate = ok_count / samples
    return {
        "type": "http",
        "url": url,
        "samples": samples,
        "status_codes": status_codes,
        "success_rate": success_rate,
        "ok_count": ok_count,
        "errors": errors,
    }


def _run_command_probe(probe: dict[str, Any]) -> dict[str, Any]:
    cmd = probe["cmd"]
    timeout = probe.get("timeout_sec", COMMAND_DEFAULT_TIMEOUT)
    shell = probe.get("shell", False)
    # Safety: default to shell=False with split args. Users who need a pipeline
    # set shell=True explicitly and own the injection surface.
    if shell:
        run_arg: Any = cmd
    else:
        run_arg = shlex.split(cmd) if isinstance(cmd, str) else cmd

    try:
        completed = subprocess.run(
            run_arg,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "type": "command",
            "cmd": cmd,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "type": "command",
            "cmd": cmd,
            "exit_code": None,
            "stdout": e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
            "stderr": e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or ""),
            "timed_out": True,
        }


def _run_file_hash_probe(probe: dict[str, Any]) -> dict[str, Any]:
    path = Path(os.path.expanduser(probe["path"]))
    if not path.exists():
        return {"type": "file_hash", "path": str(path), "exists": False, "sha256": None}
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return {"type": "file_hash", "path": str(path), "exists": True, "sha256": h.hexdigest()}


def run_probes(probes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Run each probe and return {probe_name: result_dict}."""
    results: dict[str, dict[str, Any]] = {}
    for probe in probes:
        name = probe["name"]
        ptype = probe["type"]
        if ptype == "http":
            results[name] = _run_http_probe(probe)
        elif ptype == "command":
            results[name] = _run_command_probe(probe)
        elif ptype == "file_hash":
            results[name] = _run_file_hash_probe(probe)
        else:
            results[name] = {"type": ptype, "error": f"unknown probe type: {ptype}"}
    return results


# =============================================================================
# DRIFT DETECTION
# =============================================================================

def _diff_probe(
    probe: dict[str, Any],
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Compare a probe's current result against its baseline.

    Returns None if the current result still meets expectations. Returns a
    drift descriptor dict if it doesn't. The descriptor includes the probe
    name, reason, and the specific fields that diverged — enough for a human
    reader of the honk to know what changed.

    The comparison uses the probe's `expected` spec where defined, and falls
    back to comparing the current result against the baseline for fields not
    covered by expected. Explicit expectations win over baseline comparison.
    """
    name = probe["name"]
    ptype = probe["type"]
    expected = probe.get("expected", {})

    if ptype == "http":
        min_rate = expected.get("success_rate_min")
        if min_rate is None:
            min_rate = 1.0 if baseline.get("success_rate", 0) >= 1.0 else baseline.get("success_rate", 1.0)
        if current.get("success_rate", 0) < min_rate:
            return {
                "probe": name,
                "reason": "http_success_rate_drop",
                "baseline_rate": baseline.get("success_rate"),
                "current_rate": current.get("success_rate"),
                "min_required": min_rate,
                "current_status_codes": current.get("status_codes", []),
            }
        return None

    if ptype == "command":
        expected_exit = expected.get("exit_code", baseline.get("exit_code"))
        contains = expected.get("stdout_contains")
        regex = expected.get("stdout_regex")
        reason = None
        if current.get("timed_out"):
            reason = "command_timed_out"
        elif expected_exit is not None and current.get("exit_code") != expected_exit:
            reason = "command_exit_code_changed"
        elif contains is not None and contains not in (current.get("stdout") or ""):
            reason = "stdout_missing_required_substring"
        elif regex is not None and not re.search(regex, current.get("stdout") or ""):
            reason = "stdout_regex_not_matched"
        elif contains is None and regex is None:
            # No explicit stdout matcher — fall back to baseline stdout compare.
            if (current.get("stdout") or "").strip() != (baseline.get("stdout") or "").strip():
                reason = "output_differs_from_baseline"
        if reason:
            return {
                "probe": name,
                "reason": reason,
                "baseline_exit": baseline.get("exit_code"),
                "current_exit": current.get("exit_code"),
                "expected_exit": expected_exit,
                "current_stdout_head": (current.get("stdout") or "")[:400],
            }
        return None

    if ptype == "file_hash":
        expected_hash = expected.get("sha256") or baseline.get("sha256")
        if expected_hash is None:
            # No baseline hash (e.g. file didn't exist at baseline). Drift if
            # file state has changed from the baseline exists/not-exists state.
            if current.get("exists") != baseline.get("exists"):
                return {
                    "probe": name,
                    "reason": "file_existence_changed",
                    "baseline_exists": baseline.get("exists"),
                    "current_exists": current.get("exists"),
                }
            return None
        if current.get("sha256") != expected_hash:
            return {
                "probe": name,
                "reason": "file_hash_changed",
                "baseline_sha256": expected_hash,
                "current_sha256": current.get("sha256"),
                "current_exists": current.get("exists"),
            }
        return None

    # Unknown probe type — any error on current = drift.
    if current.get("error"):
        return {"probe": name, "reason": "probe_error", "error": current.get("error")}
    return None


def diff_probes(
    probes: list[dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a list of drift descriptors (one per divergent probe)."""
    drifts: list[dict[str, Any]] = []
    for probe in probes:
        name = probe["name"]
        d = _diff_probe(probe, baseline.get(name, {}), current.get(name, {}))
        if d is not None:
            drifts.append(d)
    return drifts


# =============================================================================
# WATCH LIFECYCLE
# =============================================================================

def _new_watch_id() -> str:
    return f"pfw_{_iso(_now()).replace(':', '').replace('-', '')}_{uuid.uuid4().hex[:6]}"


def create_watch(
    fix_description: str,
    domain_tags: list[str],
    probes: list[dict[str, Any]],
    schedule_offsets_min: list[int] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    """
    Create a new watch: capture baseline probes and persist to disk.

    Returns the watch record including the captured baseline. Does not block
    on future samples — those are run by take_sample() on the scheduled
    offsets, driven by sovereign-watch-tick.
    """
    if not probes:
        raise ValueError("create_watch requires at least one probe")
    if schedule_offsets_min is None:
        schedule_offsets_min = list(DEFAULT_SCHEDULE)
    schedule_offsets_min = sorted({int(x) for x in schedule_offsets_min})

    watch_id = _new_watch_id()
    created = _now()
    watch_until = created + timedelta(minutes=max(schedule_offsets_min))

    baseline_results = run_probes(probes)

    watch = {
        "watch_id": watch_id,
        "fix_description": fix_description,
        "domain_tags": list(domain_tags) if domain_tags else [],
        "session_id": session_id,
        "created_at": _iso(created),
        "watch_until": _iso(watch_until),
        "schedule_offsets_min": schedule_offsets_min,
        "probes": probes,
        "baseline": {
            "captured_at": _iso(created),
            "results": baseline_results,
        },
        "samples": [],
        "status": "active",
        "closed_at": None,
        "closed_reason": None,
    }
    save_watch(watch)
    _append_event({
        "event": "watch_created",
        "watch_id": watch_id,
        "fix": fix_description,
        "probes": [p["name"] for p in probes],
    })
    return watch


def _next_due_offset(watch: dict[str, Any], now: datetime | None = None) -> int | None:
    """
    Return the earliest schedule offset (minutes) that is due but not yet
    sampled. Returns None if nothing is due right now.
    """
    if watch.get("status") != "active":
        return None
    now = now or _now()
    created = _parse_iso(watch["created_at"])
    sampled_offsets = {s.get("offset_min") for s in watch.get("samples", [])}
    for offset in watch["schedule_offsets_min"]:
        if offset in sampled_offsets:
            continue
        due_at = created + timedelta(minutes=offset)
        if now >= due_at:
            return offset
    return None


def take_sample(
    watch_id: str,
    force: bool = False,
    nape_daemon: Any = None,
) -> dict[str, Any]:
    """
    Run the next due sample for a watch.

    Returns a dict describing what happened:
      status: "sampled" | "not_due" | "watch_not_found" | "watch_closed" | "force_sampled"
      drift: list of drift descriptors (empty on clean sample)
      sample: the sample record appended to the watch
      watch_status: the watch's status after this sample

    If `force` is True, a sample is taken regardless of schedule (used for
    manual resampling). The offset recorded is the elapsed minutes since
    creation, which won't collide with scheduled offsets because it's tagged
    `forced=True` in the sample record.

    When drift is detected, a Nape honk is emitted via nape_daemon (if
    provided) so the signal surfaces through the existing critique channel.
    """
    watch = load_watch(watch_id)
    if watch is None:
        return {"status": "watch_not_found", "watch_id": watch_id}
    if watch.get("status") != "active":
        return {"status": "watch_closed", "watch_id": watch_id, "watch_status": watch["status"]}

    now = _now()
    if force:
        offset_min = int((now - _parse_iso(watch["created_at"])).total_seconds() // 60)
        forced = True
    else:
        offset = _next_due_offset(watch, now=now)
        if offset is None:
            return {"status": "not_due", "watch_id": watch_id}
        offset_min = offset
        forced = False

    current_results = run_probes(watch["probes"])
    drifts = diff_probes(watch["probes"], watch["baseline"]["results"], current_results)

    sample = {
        "sample_id": uuid.uuid4().hex[:8],
        "at": _iso(now),
        "offset_min": offset_min,
        "forced": forced,
        "results": current_results,
        "drift": drifts,
    }
    watch.setdefault("samples", []).append(sample)

    # Emit Nape honk if drift detected.
    emitted_honks: list[str] = []
    if drifts and nape_daemon is not None:
        session = watch.get("session_id") or "post_fix_watcher"
        summary = "; ".join(f"{d['probe']}:{d['reason']}" for d in drifts)
        observation = (
            f"post_fix drift on watch {watch_id} "
            f"(fix: {watch.get('fix_description', '')[:80]}) — {summary}"
        )
        try:
            honk = nape_daemon.emit_external_honk(
                session_id=session,
                pattern=NAPE_PATTERN_DRIFT,
                trigger_tool="post_fix_verify",
                observation=observation,
            )
            if honk and honk.get("honk_id"):
                emitted_honks.append(honk["honk_id"])
        except Exception as e:  # noqa: BLE001 — honk emission is advisory, never fatal.
            sample["honk_emission_error"] = str(e)

    sample["emitted_honks"] = emitted_honks

    # Status transitions.
    if drifts:
        watch["status"] = "drift_detected"
    else:
        # If every scheduled offset has now been sampled and there's no drift, close clean.
        sampled_offsets = {s.get("offset_min") for s in watch["samples"] if not s.get("forced")}
        if all(o in sampled_offsets for o in watch["schedule_offsets_min"]):
            watch["status"] = "completed_clean"
            watch["closed_at"] = _iso(now)
            watch["closed_reason"] = "all_samples_clean"

    save_watch(watch)
    _append_event({
        "event": "sample_taken",
        "watch_id": watch_id,
        "offset_min": offset_min,
        "forced": forced,
        "drift_count": len(drifts),
        "watch_status_after": watch["status"],
    })

    return {
        "status": "force_sampled" if forced else "sampled",
        "watch_id": watch_id,
        "drift": drifts,
        "sample": sample,
        "watch_status": watch["status"],
        "emitted_honks": emitted_honks,
    }


def cancel_watch(watch_id: str, reason: str) -> dict[str, Any]:
    watch = load_watch(watch_id)
    if watch is None:
        return {"status": "watch_not_found", "watch_id": watch_id}
    if watch.get("status") != "active":
        return {"status": "watch_already_closed", "watch_id": watch_id, "watch_status": watch["status"]}
    watch["status"] = "cancelled"
    watch["closed_at"] = _iso(_now())
    watch["closed_reason"] = reason
    save_watch(watch)
    _append_event({"event": "watch_cancelled", "watch_id": watch_id, "reason": reason})
    return {"status": "cancelled", "watch_id": watch_id, "watch": watch}


# =============================================================================
# TICK RUNNER (called by scripts/sovereign-watch-tick)
# =============================================================================

def tick_once(nape_daemon: Any = None) -> dict[str, Any]:
    """
    Drain all currently-due samples across all active watches.

    For each active watch, repeatedly take the next due sample until nothing
    is due. Safe to call on any cadence — idempotent.
    """
    taken: list[dict[str, Any]] = []
    watches = list_watches(status="active")
    for watch in watches:
        watch_id = watch["watch_id"]
        while True:
            result = take_sample(watch_id, nape_daemon=nape_daemon)
            if result["status"] in ("not_due", "watch_not_found", "watch_closed"):
                break
            taken.append(result)
            # After drift_detected, the watch is closed — loop will exit on reload.
            if result.get("watch_status") != "active":
                break
    return {
        "ts": _iso(_now()),
        "active_watches": len(watches),
        "samples_taken": len(taken),
        "samples": taken,
    }


def tick_main() -> int:
    """
    CLI entry for launchd / cron. Imports NapeDaemon lazily so a bare tick
    doesn't pay the import cost of the whole server module graph.
    """
    from .nape_daemon import NapeDaemon  # Deferred — keeps import lean for tests.
    nape = NapeDaemon(root=str(_root()))
    result = tick_once(nape_daemon=nape)
    print(json.dumps(result, indent=2))
    return 0


# =============================================================================
# MCP TOOL DEFINITIONS
# =============================================================================

POST_FIX_TOOLS: list[Tool] = [
    Tool(
        name="post_fix_verify",
        description=(
            "Register a post-fix verification watch. Captures a named baseline of fix-relevant "
            "probes and schedules re-samples to catch drift that passes immediate verification. "
            "Emits a Nape honk if a later sample diverges from the baseline. Use after any fix "
            "whose surface signal might shift (load-balancer drift, cache invalidation, "
            "configuration re-read, flaky dependency). Probes support http / command / file_hash."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "fix_description": {
                    "type": "string",
                    "description": "What was fixed, in one line. Surfaces in honk observations.",
                },
                "domain_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Domain tags for reflexive surfacing and triage.",
                },
                "probes": {
                    "type": "array",
                    "description": (
                        "List of probes to run. Each probe has {name, type, ...}. "
                        "type='http': {url, method?, samples?, timeout_sec?, expected: {status, success_rate_min?}}. "
                        "type='command': {cmd, shell?, timeout_sec?, expected: {exit_code?, stdout_contains?, stdout_regex?}}. "
                        "type='file_hash': {path, expected: {sha256?}}. If expected is omitted, the baseline becomes the expectation."
                    ),
                    "items": {"type": "object"},
                },
                "schedule_offsets_min": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": f"Minutes-from-baseline at which to re-sample. Default {DEFAULT_SCHEDULE}.",
                },
            },
            "required": ["fix_description", "probes"],
        },
    ),
    Tool(
        name="watch_status",
        description=(
            "Inspect post-fix watches. With no watch_id, lists active watches. With a watch_id, "
            "returns the full watch record including baseline, all samples, and drift history. "
            "Use include_archived=true to also show completed / cancelled watches."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "watch_id": {"type": "string", "description": "Specific watch to inspect."},
                "include_archived": {"type": "boolean", "default": False},
            },
        },
    ),
    Tool(
        name="watch_resample",
        description=(
            "Manually trigger a re-sample of a watch, regardless of schedule. Useful for "
            "on-demand verification after something the scheduler wouldn't know about (an "
            "external change, a suspicion). Emits a Nape honk if drift is detected."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "watch_id": {"type": "string"},
                "force": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true (default), sample now regardless of schedule.",
                },
            },
            "required": ["watch_id"],
        },
    ),
    Tool(
        name="watch_cancel",
        description="Cancel an active watch. Records the reason and archives it.",
        inputSchema={
            "type": "object",
            "properties": {
                "watch_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["watch_id", "reason"],
        },
    ),
]


# =============================================================================
# MCP HANDLER
# =============================================================================

async def handle_post_fix_tool(
    name: str,
    arguments: dict[str, Any],
    session_id: str,
    nape_daemon: Any = None,
) -> list[TextContent]:
    if name == "post_fix_verify":
        fix_description = (arguments.get("fix_description") or "").strip()
        probes = arguments.get("probes") or []
        if not fix_description:
            return [TextContent(type="text", text="post_fix_verify requires fix_description")]
        if not probes:
            return [TextContent(type="text", text="post_fix_verify requires at least one probe")]
        try:
            watch = create_watch(
                fix_description=fix_description,
                domain_tags=arguments.get("domain_tags") or [],
                probes=probes,
                schedule_offsets_min=arguments.get("schedule_offsets_min"),
                session_id=session_id,
            )
        except (KeyError, ValueError) as e:
            return [TextContent(type="text", text=f"post_fix_verify failed: {e}")]
        return [TextContent(type="text", text=json.dumps({
            "watch_id": watch["watch_id"],
            "created_at": watch["created_at"],
            "watch_until": watch["watch_until"],
            "schedule_offsets_min": watch["schedule_offsets_min"],
            "baseline_summary": {pname: _baseline_summary(r) for pname, r in watch["baseline"]["results"].items()},
        }, indent=2))]

    if name == "watch_status":
        watch_id = (arguments.get("watch_id") or "").strip()
        include_archived = bool(arguments.get("include_archived"))
        if watch_id:
            watch = load_watch(watch_id)
            if watch is None:
                return [TextContent(type="text", text=f"watch not found: {watch_id}")]
            return [TextContent(type="text", text=json.dumps(watch, indent=2))]
        watches = list_watches(status="all" if include_archived else "active")
        return [TextContent(type="text", text=json.dumps({
            "count": len(watches),
            "watches": [_watch_summary(w) for w in watches],
        }, indent=2))]

    if name == "watch_resample":
        watch_id = (arguments.get("watch_id") or "").strip()
        force = bool(arguments.get("force", True))
        if not watch_id:
            return [TextContent(type="text", text="watch_resample requires watch_id")]
        result = take_sample(watch_id, force=force, nape_daemon=nape_daemon)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "watch_cancel":
        watch_id = (arguments.get("watch_id") or "").strip()
        reason = (arguments.get("reason") or "").strip()
        if not watch_id or not reason:
            return [TextContent(type="text", text="watch_cancel requires watch_id and reason")]
        result = cancel_watch(watch_id, reason)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return [TextContent(type="text", text=f"unknown post_fix tool: {name}")]


def _baseline_summary(r: dict[str, Any]) -> dict[str, Any]:
    """Compact view of a probe result for post_fix_verify's return payload."""
    t = r.get("type")
    if t == "http":
        return {"type": "http", "success_rate": r.get("success_rate"), "status_codes": r.get("status_codes")}
    if t == "command":
        return {"type": "command", "exit_code": r.get("exit_code"), "stdout_head": (r.get("stdout") or "")[:160]}
    if t == "file_hash":
        return {"type": "file_hash", "exists": r.get("exists"), "sha256": r.get("sha256")}
    return r


def _watch_summary(w: dict[str, Any]) -> dict[str, Any]:
    return {
        "watch_id": w["watch_id"],
        "fix_description": w.get("fix_description", ""),
        "status": w.get("status"),
        "created_at": w.get("created_at"),
        "watch_until": w.get("watch_until"),
        "probes": [p["name"] for p in w.get("probes", [])],
        "samples_taken": len(w.get("samples", [])),
        "drift_count": sum(len(s.get("drift", [])) for s in w.get("samples", [])),
        "domain_tags": w.get("domain_tags", []),
    }
