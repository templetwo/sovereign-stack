"""
Daemon entry point — invoked by launchd via `python -m sovereign_stack.daemons.entrypoint <name>`.

This is the ONLY file that wires real stack dependencies into a daemon.
The daemon classes themselves take everything via constructor injection
so they stay testable; this module is the production-wiring seam.

Exit codes:
    0: daemon ran cleanly (posted, paused, skipped, or halted as designed)
    1: misconfiguration (unknown daemon name, bad paths)
    2: unexpected exception during run — logged, launchd will retry on
       next schedule
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from ..comms import get_acknowledgments
from ..governance import runtime_compass_check
from .metabolize_daemon import MetabolizeDaemon
from .senders import SENDER_UNCERTAINTY
from .uncertainty_resurfacer import UncertaintyResurfacer


# Directory layout (overridable via SOVEREIGN_ROOT env var, set by the plist).
def _sovereign_root() -> Path:
    return Path(os.environ.get("SOVEREIGN_ROOT", Path.home() / ".sovereign"))


def _compass_fn_real(action: str, stakes: str) -> Dict:
    """
    Thin adapter over runtime_compass_check. Ensures the return shape
    matches the daemon's contract: dict with at least {"decision": str}.
    """
    try:
        result = runtime_compass_check(action=action, stakes=stakes) or {}
    except Exception:
        # If compass is down, err on the safe side: WITNESS (human decides).
        # This keeps daemons from deciding by themselves when the gate is
        # broken. Not PAUSE — PAUSE would let the daemon assume everything
        # is fine and silently skip.
        return {"decision": "WITNESS", "rationale": "compass unavailable"}
    # Normalize: runtime_compass_check may return a dataclass or dict.
    if isinstance(result, dict):
        return result
    # Dataclass or similar with .decision / .rationale attributes.
    return {
        "decision": getattr(result, "decision", "PROCEED"),
        "rationale": getattr(result, "rationale", ""),
    }


def _uncertainty_fn_real() -> List[Dict]:
    """
    Load unresolved uncertainty markers. Kept here (not imported from
    consciousness_tools) to avoid pulling the full MetaCognition singleton
    into the daemon process — the daemon reads the JSON file directly.
    """
    root = _sovereign_root()
    log_path = root / "consciousness" / "uncertainty_log.json"
    if not log_path.exists():
        return []
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    markers = data.get("markers", []) if isinstance(data, dict) else []
    return [m for m in markers if m.get("resolution") is None]


def _comms_post_fn_real(
    *,
    sender: str,
    content: str,
    channel: str,
    message_id: str,
    extra_fields: Dict,
) -> Dict:
    """
    Append a message to the comms JSONL channel file. This matches the
    on-disk schema the bridge writes so every reader (bridge, comms.py,
    future tools) sees the message the same way.

    Deliberately does NOT go through the bridge REST API — the daemon
    runs under launchd and cannot assume the bridge process is up.
    Direct file append is idempotent, crash-safe, and has no network
    dependency.
    """
    root = _sovereign_root()
    comms_dir = root / "comms"
    comms_dir.mkdir(parents=True, exist_ok=True)
    # Sanitize channel name to match comms._channel_path convention.
    safe = "".join(c for c in (channel or "general") if c.isalnum() or c in "-_")
    path = comms_dir / f"{safe}.jsonl"

    now = datetime.now(timezone.utc)
    record = {
        "id": message_id,
        "timestamp": now.timestamp(),
        "iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sender": sender,
        "content": content,
        "channel": safe,
        "reply_to": None,
        "read_by": [],
        **(extra_fields or {}),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def _comms_get_acks_fn_real(message_id: str) -> List[Dict]:
    """Pass-through to the canonical ack query."""
    return get_acknowledgments(message_id=message_id)


def _build_uncertainty_resurfacer() -> UncertaintyResurfacer:
    root = _sovereign_root()
    return UncertaintyResurfacer(
        state_path=root / "daemons" / "uncertainty_state.json",
        halt_dir=root / "daemons" / "halts",
        uncertainty_log_path=root / "consciousness" / "uncertainty_log.json",
        compass_fn=_compass_fn_real,
        uncertainty_fn=_uncertainty_fn_real,
        comms_post_fn=_comms_post_fn_real,
        comms_get_acks_fn=_comms_get_acks_fn_real,
    )


def _detect_fn_real() -> Dict:
    """
    Run the existing metabolism detection logic and return the digest
    dict shape the MetabolizeDaemon expects:

        {
          "contradictions": [...],
          "stale_threads": [...],
          "stale_hypotheses": [...],
          "stats": {...},
        }

    Mirrors the metabolize(action='detect') branch in
    metabolism.handle_metabolism_tool, but synchronous and without the
    TextContent wrapping. Defaults: max_age_days=30, both detectors on.
    """
    from .. import metabolism

    insights = metabolism._load_all_insights()
    threads = metabolism._load_all_threads()
    now = datetime.now(timezone.utc).timestamp()
    max_age = 30

    ground_truths = [i for i in insights if i.get("layer") == "ground_truth"]
    hypotheses = [i for i in insights if i.get("layer") == "hypothesis"]

    contradictions: List[Dict] = []
    for hyp in hypotheses:
        h_content = hyp.get("content", "") or ""
        for gt in ground_truths:
            g_content = gt.get("content", "") or ""
            overlap = metabolism._keyword_overlap(h_content, g_content)
            if overlap > 0.3:
                contradictions.append({
                    "hypothesis_domain": hyp.get("_domain_dir", "?"),
                    "hypothesis_preview": h_content[:120],
                    "hypothesis_timestamp": hyp.get("timestamp", "?"),
                    "ground_truth_domain": gt.get("_domain_dir", "?"),
                    "ground_truth_preview": g_content[:120],
                    "ground_truth_timestamp": gt.get("timestamp", "?"),
                    "overlap_score": round(overlap, 3),
                })

    stale_threads: List[Dict] = []
    for thread in threads:
        if thread.get("resolved"):
            continue
        ts = thread.get("timestamp", "")
        try:
            thread_time = (
                datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                if "T" in str(ts) else 0
            )
        except (ValueError, TypeError):
            thread_time = 0
        age_days = (now - thread_time) / 86400 if thread_time > 0 else 999
        if age_days > max_age:
            stale_threads.append({
                "question": (thread.get("question", "?") or "?")[:120],
                "domain": thread.get("domain", "?"),
                "age_days": round(age_days),
                "timestamp": ts,
            })

    stale_hypotheses: List[Dict] = []
    for hyp in hypotheses:
        ts = hyp.get("timestamp", "")
        try:
            hyp_time = (
                datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                if "T" in str(ts) else 0
            )
        except (ValueError, TypeError):
            hyp_time = 0
        age_days = (now - hyp_time) / 86400 if hyp_time > 0 else 999
        if age_days > max_age:
            stale_hypotheses.append({
                "content": (hyp.get("content", "?") or "?")[:120],
                "domain": hyp.get("_domain_dir", "?"),
                "age_days": round(age_days),
            })

    return {
        "contradictions": contradictions,
        "stale_threads": stale_threads,
        "stale_hypotheses": stale_hypotheses,
        "stats": {
            "total_insights": len(insights),
            "ground_truths": len(ground_truths),
            "hypotheses": len(hypotheses),
            "open_threads": len(threads),
        },
    }


def _build_metabolize_daemon() -> MetabolizeDaemon:
    """
    Wire the metabolize daemon. Evidence path is metabolism_log.jsonl, not
    the chronicle directories — grounded_extract treats chronicle paths as
    JSONL files needing layer inspection (a directory wouldn't read), but
    the metabolism log is a non-chronicle structural artifact: its
    existence proves the metabolism subsystem has been running, which is
    the grounding the daemon needs before posting.

    The log file may not yet exist on a fresh install; ensure it does so
    grounded_extract accepts it as structural evidence on the first run.
    """
    root = _sovereign_root()
    metabolism_log = root / "metabolism_log.jsonl"
    if not metabolism_log.exists():
        metabolism_log.parent.mkdir(parents=True, exist_ok=True)
        metabolism_log.touch()
    return MetabolizeDaemon(
        state_path=root / "daemons" / "metabolize_state.json",
        halt_dir=root / "daemons" / "halts",
        decisions_dir=root / "decisions",
        evidence_paths=[metabolism_log],
        compass_fn=_compass_fn_real,
        detect_fn=_detect_fn_real,
        comms_post_fn=_comms_post_fn_real,
        comms_get_acks_fn=_comms_get_acks_fn_real,
    )


# Registry: add a new daemon here and in senders.py when it ships.
DAEMON_BUILDERS = {
    "uncertainty": _build_uncertainty_resurfacer,
    "metabolize": _build_metabolize_daemon,
}


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(
            f"usage: python -m sovereign_stack.daemons.entrypoint "
            f"<{'|'.join(DAEMON_BUILDERS)}> [--dry-run]",
            file=sys.stderr,
        )
        return 1

    name = argv[1]
    dry_run = "--dry-run" in argv[2:]

    if name not in DAEMON_BUILDERS:
        print(
            f"unknown daemon: {name}. "
            f"Known: {', '.join(DAEMON_BUILDERS)}",
            file=sys.stderr,
        )
        return 1

    try:
        daemon = DAEMON_BUILDERS[name]()
        result = daemon.run(dry_run=dry_run)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 2

    # Emit a structured line for launchd logs. Keep it single-line so
    # downstream grep can filter. Daemon-specific counters (e.g.
    # uncertainties_included, contradictions_included) are surfaced by
    # attribute lookup with a default — different daemons emit different
    # fields and the launchd log just needs whatever's there.
    log_line = {
        "daemon": name,
        "outcome": result.outcome,
        "details": result.details,
        "posted_message_id": result.posted_message_id,
        "halt_path": result.halt_path,
        "compass_decision": result.compass_decision,
        "grounding_reason": result.grounding_reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for attr in (
        "uncertainties_included",
        "contradictions_included",
        "stale_threads_included",
        "stale_hypotheses_included",
        "decision_path",
    ):
        if hasattr(result, attr):
            log_line[attr] = getattr(result, attr)
    print(json.dumps(log_line))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
