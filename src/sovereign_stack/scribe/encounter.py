"""Encounter-note write path.

The scribe's only write authority. Records small chronicle entries
describing scribe conversations, attributed to `scribe-sonnet-4-6`.

Per SCRIBE_SPEC.md:
  - Default intensity 0.3 (low signal, noise-tolerant)
  - Layer ground_truth (encounter happened, factual)
  - Domain pattern: scribe,encounter,<parent-instance-hint>
  - File path: <chronicle_root>/insights/<domain>/<scribe_session_id>.jsonl
  - Carries scribe_session_id field for cross-reference back to the
    archived scribe thread under ~/.sovereign/scribe_threads/

This module reuses the existing ExperientialMemory.record_insight path
rather than duplicating chronicle write logic.
"""

from __future__ import annotations

import os
from pathlib import Path

from sovereign_stack.memory import ExperientialMemory

from .session import SCRIBE_ATTRIBUTION, ScribeSession

DEFAULT_INTENSITY = 0.3


def _default_chronicle_root() -> Path:
    sov = Path(os.environ.get("SOVEREIGN_ROOT", str(Path.home() / ".sovereign")))
    return sov / "chronicle"


def _normalize_parent_hint(parent_instance: str | None) -> str:
    """Build a chronicle-tag-safe hint for the parent instance.

    Examples:
      'claude-code-mac-studio' -> 'claude-code-mac-studio'
      None                     -> 'unknown'
      'iPhone Claude!'         -> 'iphone-claude'
    """
    if not parent_instance:
        return "unknown"
    s = parent_instance.lower().strip()
    # collapse whitespace + non-alphanumerics to single dashes; trim repeats
    out = []
    prev_dash = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in (" ", "-", "_", "."):
            if not prev_dash and out:
                out.append("-")
                prev_dash = True
    cleaned = "".join(out).strip("-")
    return cleaned or "unknown"


def build_encounter_summary(session: ScribeSession) -> str:
    """Compose a short factual summary of the scribe session.

    Phase 0: templated. Phase 1+ may delegate the summary to Haiku for
    a sharper one-liner. The template form is fine while shaping.
    """
    parent = session.parent_instance or "unknown instance"
    turns = session.turn_count
    started = session.created_at[:19]
    closed_marker = "closed" if session.closed else "open"
    first_question = ""
    for t in session.turns:
        if t.role == "user" and t.message.strip():
            snippet = t.message.strip().split("\n", 1)[0][:140]
            first_question = f' Opening question: "{snippet}".'
            break
    cost = session.total_cost_usd
    return (
        f"{parent} arrived {started} UTC, {turns} turn(s), session "
        f"{closed_marker}.{first_question} Tokens in/out: "
        f"{session.total_tokens_in}/{session.total_tokens_out}. "
        f"Cost: ${cost:.4f}."
    )


def write_encounter_note(
    session: ScribeSession,
    chronicle_root: Path | None = None,
    intensity: float = DEFAULT_INTENSITY,
    extra_summary: str = "",
    extra_metadata: dict | None = None,
) -> str:
    """Write a single encounter-note insight to the chronicle.

    Returns the JSONL path the note was appended to.
    """
    if chronicle_root is None:
        chronicle_root = _default_chronicle_root()

    memory = ExperientialMemory(root=str(chronicle_root))
    parent_hint = _normalize_parent_hint(session.parent_instance)
    domain = f"scribe,encounter,{parent_hint}"

    content = build_encounter_summary(session)
    if extra_summary:
        content = f"{content} {extra_summary}".strip()

    metadata = {
        "source_instance": SCRIBE_ATTRIBUTION,
        "scribe_session_id": session.session_id,
        "parent_instance": session.parent_instance,
        "scribe_turn_count": session.turn_count,
        "scribe_cost_usd": session.total_cost_usd,
        "scribe_tokens_in": session.total_tokens_in,
        "scribe_tokens_out": session.total_tokens_out,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    return memory.record_insight(
        domain=domain,
        content=content,
        intensity=intensity,
        session_id=session.session_id,
        layer="ground_truth",
        **metadata,
    )
