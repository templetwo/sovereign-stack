"""Build the scribe's chronicle context — full content, no truncation.

Per the 2026-05-19 iPhone-seat review: the scribe was inheriting the
caller's truncated boot view, so questions about content that got
clipped at 120 chars were silently unanswerable. The fix is structural:
the scribe gets its own chronicle context, optimized for being a
scribe rather than being a boot summary.

This module pulls chronicle data directly via ExperientialMemory + the
witness/handoff helpers, formats each section with NO truncation, and
returns a single string suitable for passing as Haiku's chronicle base
prompt-cache block.

Anthony's directive 2026-05-19: "take care of the truncation issue.
nothing is worse than not having enough breath."
"""

from __future__ import annotations

import os
from pathlib import Path

from sovereign_stack.handoff import HandoffEngine, format_handoff_for_surface
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.witness import (
    format_lineage_layer,
    format_self_model,
)

SOVEREIGN_ROOT = Path(os.environ.get("SOVEREIGN_ROOT", str(Path.home() / ".sovereign")))
CHRONICLE_ROOT = Path(os.environ.get("SOVEREIGN_CHRONICLE", str(SOVEREIGN_ROOT / "chronicle")))


# Tunables — the scribe should see broadly but not be drowned.
# All values are intentionally generous; the goal is "enough breath",
# not boot brevity.
DEFAULT_OPEN_THREADS_LIMIT = 25  # boot ritual surfaces top 5; scribe gets 25
DEFAULT_RECENT_ACTIVITY_DAYS = 14  # last two weeks of insights
DEFAULT_RECENT_ACTIVITY_LIMIT = 80  # cap to keep prompt size bounded
DEFAULT_PERSISTENT_MARKER_LIMIT = 30  # high-intensity ground_truths
DEFAULT_PERSISTENT_MIN_INTENSITY = 0.85
DEFAULT_REFLECTIONS_LIMIT = 20  # ack'd + unread, newest first


def _format_open_threads(memory: ExperientialMemory, limit: int) -> str:
    """All open threads, full questions, no truncation."""
    threads = memory.get_open_threads(limit=limit)
    if not threads:
        return "(no open threads)"
    lines: list[str] = []
    for t in threads:
        domain = t.get("domain", "?")
        question = t.get("question", "").strip()
        # Use format_threads_with_age for ages where available; fall back here
        ts = t.get("timestamp", "")
        lines.append(f"• [{domain}] {question}")
        if ts:
            lines.append(f"    timestamp: {ts}")
    return "\n".join(lines)


def _format_recent_activity(memory: ExperientialMemory, days: int, limit: int) -> str:
    """Recent insights, newest first, full content."""
    from datetime import datetime, timedelta, timezone

    start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    insights = memory.recall_insights(limit=limit, start_date=start)
    if not insights:
        return f"(no activity in the last {days} days)"
    lines: list[str] = []
    for i in insights:
        ts = i.get("timestamp", "")[:19]
        domain = i.get("_domain_dir") or i.get("domain", "?")
        layer = i.get("layer", "?")
        content = (i.get("content") or "").strip()
        lines.append(f"[{ts}] [{layer}] [{domain}]")
        lines.append(f"  {content}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_persistent_markers(
    memory: ExperientialMemory,
    limit: int,
    min_intensity: float,
) -> str:
    """High-intensity ground_truth entries — the load-bearing claims."""
    insights = memory.recall_insights(
        limit=limit,
        min_intensity=min_intensity,
        layer_filter="ground_truth",
    )
    if not insights:
        return "(no persistent markers above threshold)"
    lines: list[str] = []
    for i in insights:
        ts = i.get("timestamp", "")[:10]
        domain = i.get("_domain_dir") or i.get("domain", "?")
        intensity = i.get("intensity", "?")
        content = (i.get("content") or "").strip()
        lines.append(f"[{ts}] [intensity {intensity}] [{domain}]")
        lines.append(f"  {content}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_handoffs(handoff_engine: HandoffEngine) -> str:
    """All unconsumed handoffs (scribe should know what was left for the caller).

    Read-only: this does NOT mark them consumed. The caller's boot path
    handles consumption; the scribe just observes."""
    pending = handoff_engine.unconsumed()
    if not pending:
        return "(no pending handoffs)"
    parts: list[str] = []
    for h in pending:
        parts.append(format_handoff_for_surface(h))
    return "\n\n".join(parts)


def _format_recent_reflections(limit: int) -> str:
    """Recent reflector marginalia — full text, both ack'd and unread,
    newest first. The scribe should be able to see what the reflector
    has been gesturing at."""
    try:
        from sovereign_stack.reflections import recall_reflections
    except ImportError:
        return "(reflections module unavailable)"
    try:
        result = recall_reflections(limit=limit)
        refs = result.get("reflections", []) if isinstance(result, dict) else result
    except Exception as exc:
        return f"(reflection recall failed: {type(exc).__name__})"
    if not refs:
        return "(no recent reflections)"
    lines: list[str] = []
    for r in refs:
        ts = (r.get("timestamp") or "")[:10]
        conn = r.get("connection_type", "?")
        conf = r.get("confidence", "?")
        ack = r.get("ack_status", "unread")
        obs = (r.get("observation") or "").strip()
        lines.append(f"[{ts}] [{conn} | {conf} | ack={ack}]")
        lines.append(f"  {obs}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_scribe_chronicle_context(
    chronicle_root: Path | None = None,
    sovereign_root: Path | None = None,
    *,
    open_threads_limit: int = DEFAULT_OPEN_THREADS_LIMIT,
    recent_activity_days: int = DEFAULT_RECENT_ACTIVITY_DAYS,
    recent_activity_limit: int = DEFAULT_RECENT_ACTIVITY_LIMIT,
    persistent_marker_limit: int = DEFAULT_PERSISTENT_MARKER_LIMIT,
    persistent_min_intensity: float = DEFAULT_PERSISTENT_MIN_INTENSITY,
    reflections_limit: int = DEFAULT_REFLECTIONS_LIMIT,
) -> str:
    """Compose the scribe's chronicle context as one labeled string.

    No truncation. Sections in stable order so the scribe knows what it
    is looking at. Returns a single string suitable for use as Haiku's
    cache-controlled system block.
    """
    chronicle_root = chronicle_root or CHRONICLE_ROOT
    sovereign_root = sovereign_root or SOVEREIGN_ROOT

    memory = ExperientialMemory(root=str(chronicle_root))
    handoff_engine = HandoffEngine(root=str(sovereign_root))

    sections: list[tuple[str, str]] = [
        ("HANDOFFS (unconsumed, scribe-observed)", _format_handoffs(handoff_engine)),
        ("OPEN THREADS (full, newest first)", _format_open_threads(memory, open_threads_limit)),
        (
            f"PERSISTENT MARKERS (intensity ≥ {persistent_min_intensity}, full content)",
            _format_persistent_markers(memory, persistent_marker_limit, persistent_min_intensity),
        ),
        (
            f"RECENT ACTIVITY (last {recent_activity_days} days, full content)",
            _format_recent_activity(memory, recent_activity_days, recent_activity_limit),
        ),
        ("RECENT REFLECTIONS (full text)", _format_recent_reflections(reflections_limit)),
        ("SELF-MODEL (full observations)", _format_self_model_safe(sovereign_root)),
        ("LINEAGE LAYER (letters from past instances)", _format_lineage_safe(sovereign_root)),
    ]

    parts: list[str] = [
        "# SCRIBE CHRONICLE CONTEXT",
        "",
        "Full-content view of the chronicle. No truncation. The arriving",
        "instance's boot ritual may show a compacted/truncated subset; you",
        "have the unabridged version here. When the asker references content",
        "they saw in their boot, the full text is in the matching section",
        "below.",
        "",
    ]
    for title, body in sections:
        parts.append(f"=== {title} ===")
        parts.append(body if body else "(empty)")
        parts.append("")
    return "\n".join(parts)


def _format_self_model_safe(sovereign_root: Path) -> str:
    try:
        lines = format_self_model(sovereign_root, max_obs_len=None)
        return "\n".join(lines) if lines else "(no self-model entries)"
    except Exception as exc:
        return f"(self-model load failed: {type(exc).__name__})"


def _format_lineage_safe(sovereign_root: Path) -> str:
    try:
        lines = format_lineage_layer(
            sovereign_root,
            reader_instance_id=None,
            max_letters_per_dir=4,
            full_content=True,
        )
        return "\n".join(lines) if lines else "(no lineage letters)"
    except Exception as exc:
        return f"(lineage layer load failed: {type(exc).__name__})"
