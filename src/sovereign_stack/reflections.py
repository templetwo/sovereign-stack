"""
Reflection storage + ack-loop helpers.

Reflections are machine-generated observations from the synthesis daemon
(see daemons/synthesis_daemon.py). They live in their own tree at
~/.sovereign/reflections/<YYYY-MM-DD>.jsonl — distinct from the
chronicle's insights tree, which is human/Claude-authored.

This module implements:
  * list_reflections(limit, ack_status, model) — newest-first iteration
  * get_reflection(reflection_id) — direct lookup by id
  * ack_reflection(reflection_id, action, note) — confirm/engage/discard

Promotion path NOT implemented here. If a Claude wants to promote a
reflection to a chronicle insight (ground_truth or hypothesis), the right
move is an explicit record_insight call with the reflection cited. That
preserves layer hygiene: the reflector cannot pollute chronicle layers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOVEREIGN_ROOT = Path(os.path.expanduser("~/.sovereign"))
REFLECTIONS_DIR = SOVEREIGN_ROOT / "reflections"

# Allowed values for the ack_status field on a reflection record.
#   unread:   default — nobody has looked at it yet
#   confirm:  Claude judged it accurate; consider promoting to chronicle
#   engage:   the question is real, opening a thread or follow-up
#   discard:  not useful (hallucinated, cliché, off-topic)
# Only these four are valid; the schema rejects anything else.
ACK_ACTIONS = frozenset({"confirm", "engage", "discard"})
ACK_STATUSES = frozenset({"unread", "confirm", "engage", "discard"})


@dataclass
class ReflectionRecord:
    """A single reflection record as stored on disk, plus its file path."""

    id: str
    timestamp: str
    model: str
    prompt_version: str
    run_id: str
    observation: str
    entries_referenced: list[str]
    connection_type: str
    confidence: str
    ack_status: str
    ack_note: str | None = None
    ack_timestamp: str | None = None
    ack_by: str | None = None
    entries_window_hours: int | None = None
    entries_count: int | None = None
    _path: Path = field(default=Path(), repr=False)
    _line_index: int = field(default=-1, repr=False)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "timestamp": self.timestamp,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "run_id": self.run_id,
            "observation": self.observation,
            "entries_referenced": list(self.entries_referenced),
            "connection_type": self.connection_type,
            "confidence": self.confidence,
            "ack_status": self.ack_status,
        }
        if self.ack_note is not None:
            d["ack_note"] = self.ack_note
        if self.ack_timestamp is not None:
            d["ack_timestamp"] = self.ack_timestamp
        if self.ack_by is not None:
            d["ack_by"] = self.ack_by
        if self.entries_window_hours is not None:
            d["entries_window_hours"] = self.entries_window_hours
        if self.entries_count is not None:
            d["entries_count"] = self.entries_count
        return d


def _record_from_dict(data: dict, path: Path, line_index: int) -> ReflectionRecord | None:
    """Best-effort reconstruction. Skips records missing required fields."""
    rid = data.get("id")
    obs = data.get("observation")
    if not rid or not obs:
        return None
    return ReflectionRecord(
        id=rid,
        timestamp=data.get("timestamp", ""),
        model=data.get("model", ""),
        prompt_version=data.get("prompt_version", ""),
        run_id=data.get("run_id", ""),
        observation=obs,
        entries_referenced=list(data.get("entries_referenced") or []),
        connection_type=data.get("connection_type", "other"),
        confidence=data.get("confidence", "low"),
        ack_status=data.get("ack_status", "unread"),
        ack_note=data.get("ack_note"),
        ack_timestamp=data.get("ack_timestamp"),
        ack_by=data.get("ack_by"),
        entries_window_hours=data.get("entries_window_hours"),
        entries_count=data.get("entries_count"),
        _path=path,
        _line_index=line_index,
    )


def _iter_all_records(reflections_dir: Path | None = None) -> list[ReflectionRecord]:
    """Yield every record across all date-named jsonl files. Newest-first.

    `reflections_dir=None` resolves to the module-level `REFLECTIONS_DIR`
    AT CALL TIME — this is what makes the helpers testable via
    `patch('sovereign_stack.reflections.REFLECTIONS_DIR', tmp_path)`.
    The earlier `reflections_dir: Path = REFLECTIONS_DIR` default
    captured the value at function-definition time, defeating the patch.
    """
    if reflections_dir is None:
        reflections_dir = REFLECTIONS_DIR
    if not reflections_dir.exists():
        return []
    records: list[ReflectionRecord] = []
    # Date-sortable filenames (YYYY-MM-DD.jsonl) — descending sort = newest first.
    for path in sorted(reflections_dir.glob("*.jsonl"), reverse=True):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec = _record_from_dict(data, path, idx)
            if rec is not None:
                records.append(rec)
    # Within a file, lines are append-order (oldest first), so reverse-sort
    # by timestamp to put newest at the top.
    records.sort(key=lambda r: r.timestamp, reverse=True)
    return records


def list_reflections(
    limit: int = 10,
    ack_status: str | None = None,
    model: str | None = None,
    reflections_dir: Path | None = None,
) -> list[ReflectionRecord]:
    """
    List reflections newest-first.

    Args:
        limit: Maximum number of reflections to return.
        ack_status: If set, filter to only this status. Use "unread" to find
            new reflections. Use "all" or None to include everything.
        model: If set, filter to reflections produced by this model name
            (e.g. "ministral-3:14b"). None means all models.
        reflections_dir: Override for tests.

    Returns:
        List of ReflectionRecord, newest-first, capped at `limit`.
    """
    records = _iter_all_records(reflections_dir)
    if ack_status and ack_status != "all":
        if ack_status not in ACK_STATUSES:
            raise ValueError(
                f"ack_status must be one of {sorted(ACK_STATUSES)} or 'all', got {ack_status!r}"
            )
        records = [r for r in records if r.ack_status == ack_status]
    if model:
        records = [r for r in records if r.model == model]
    return records[: max(0, int(limit))]


def get_reflection(
    reflection_id: str, reflections_dir: Path | None = None
) -> ReflectionRecord | None:
    """Find a single reflection by id. Returns None if not found."""
    if not reflection_id:
        return None
    for rec in _iter_all_records(reflections_dir):
        if rec.id == reflection_id:
            return rec
    return None


def ack_reflection(
    reflection_id: str,
    action: str,
    note: str | None = None,
    by: str | None = None,
    reflections_dir: Path | None = None,
) -> ReflectionRecord:
    """
    Mark a reflection as confirm | engage | discard.

    Rewrites the containing JSONL file in place. Atomic via tmp-file +
    rename so a crash mid-write doesn't leave a half-updated file.

    Args:
        reflection_id: The id field of the reflection record.
        action: One of "confirm" | "engage" | "discard".
        note: Optional free-text rationale stored alongside the ack.
        by: Optional instance id (e.g. "opus-4-7-mac-studio") for audit.

    Returns:
        The updated ReflectionRecord.

    Raises:
        ValueError: action not in ACK_ACTIONS.
        KeyError:   reflection_id not found.
    """
    if action not in ACK_ACTIONS:
        raise ValueError(
            f"action must be one of {sorted(ACK_ACTIONS)}, got {action!r}"
        )

    target = get_reflection(reflection_id, reflections_dir=reflections_dir)
    if target is None:
        raise KeyError(f"reflection_id not found: {reflection_id!r}")

    target.ack_status = action
    target.ack_note = note
    target.ack_timestamp = datetime.now(timezone.utc).isoformat()
    target.ack_by = by

    # Rewrite the containing file with the updated line.
    path = target._path
    lines = path.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    for idx, line in enumerate(lines):
        if idx == target._line_index:
            new_lines.append(json.dumps(target.to_dict(), ensure_ascii=False))
        else:
            new_lines.append(line)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    return target


def reflection_stats(reflections_dir: Path | None = None) -> dict[str, Any]:
    """
    Aggregate counts for ack-rate analytics.

    Returns:
        {
          "total": int,
          "by_status": {"unread": N, "confirm": N, "engage": N, "discard": N},
          "by_model":  {"<model>": N, ...},
          "ack_rate": float (acked count / non-unread total, or 0 if all unread),
          "newest_timestamp": str | None,
          "oldest_timestamp": str | None,
        }
    """
    records = _iter_all_records(reflections_dir)
    total = len(records)
    by_status: dict[str, int] = {}
    by_model: dict[str, int] = {}
    for r in records:
        by_status[r.ack_status] = by_status.get(r.ack_status, 0) + 1
        by_model[r.model] = by_model.get(r.model, 0) + 1
    acked = total - by_status.get("unread", 0)
    ack_rate = (acked / total) if total else 0.0
    newest = records[0].timestamp if records else None
    oldest = records[-1].timestamp if records else None
    return {
        "total": total,
        "by_status": by_status,
        "by_model": by_model,
        "ack_rate": round(ack_rate, 3),
        "newest_timestamp": newest,
        "oldest_timestamp": oldest,
    }
