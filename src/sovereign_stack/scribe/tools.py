"""Chronicle-read toolset for the Haiku scribe.

Per Anthony's directive 2026-05-19: the scribe gets tool access so it
can dig beyond its boot context. The iPhone-seat review (2026-05-19
06:41 UTC) made clear the scribe was previously bound to the boot-
summary surface; tool access turns it into a real agent that can
fetch additional chronicle slices on demand.

Design constraints (all enforced here, not at the prompt level):

  1. READ-ONLY. No tool here writes, deletes, modifies, or executes
     anything. The scribe cannot retire, resolve, or alter chronicle
     entries. The encounter-note write path stays a separate code
     path attributable to scribe-haiku-4-5 alone.

  2. SCOPED. All filesystem access is restricted to under
     ~/.sovereign/chronicle/. Path traversal is rejected before any
     read happens. The scribe cannot reach ~/.env, ~/.config/, or
     anything else outside the chronicle root.

  3. REDACTED. Tool results pass through the same redaction layer
     used on incoming user messages before being returned. The 5/12
     reflector catch (credential redaction discipline vs archival
     fidelity) made structural for tool results too.

  4. BOUNDED. Results are size-capped per call to prevent a single
     query from consuming the whole context window.

Tools defined here:
  chronicle_recall      — call recall_insights with filters
  chronicle_read_file   — read a single JSONL file by chronicle-relative path
  chronicle_list_domains — list insight domain directories
  chronicle_get_threads — get open threads (full content)
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sovereign_stack.memory import ExperientialMemory

from .redactor import redact, redact_structure

logger = logging.getLogger(__name__)

SOVEREIGN_ROOT = Path(os.environ.get("SOVEREIGN_ROOT", str(Path.home() / ".sovereign")))
CHRONICLE_ROOT = Path(
    os.environ.get("SOVEREIGN_CHRONICLE", str(SOVEREIGN_ROOT / "chronicle"))
).resolve()

# Safety caps. Tuned to keep any single tool call bounded so the scribe
# cannot accidentally pull a megabyte of chronicle into one response.
MAX_RECALL_LIMIT = 30
MAX_READ_FILE_BYTES = 100_000  # ~25K tokens of JSONL per read
MAX_DOMAIN_LIST = 200
MAX_THREADS_LIMIT = 50
MAX_RESULT_CHARS_PER_TOOL = 80_000  # response cap regardless of source


# ----------------------------------------------------------------------
# Path-scope guard
# ----------------------------------------------------------------------


class ScribeToolError(Exception):
    """Raised for any scribe tool input/scope violation. Surfaced to
    Haiku as a tool_result with is_error=True."""


def _resolve_chronicle_path(rel_path: str) -> Path:
    """Resolve a chronicle-relative path safely. Rejects absolute paths,
    parent traversal (`..`), and any resolution that lands outside
    CHRONICLE_ROOT."""
    if not rel_path or not isinstance(rel_path, str):
        raise ScribeToolError("path must be a non-empty string")
    if rel_path.startswith(("/", "~")):
        raise ScribeToolError(
            "absolute paths are not allowed; pass a path relative to the "
            f"chronicle root ({CHRONICLE_ROOT})"
        )
    candidate = (CHRONICLE_ROOT / rel_path).resolve()
    try:
        candidate.relative_to(CHRONICLE_ROOT)
    except ValueError as exc:
        raise ScribeToolError(
            f"path {rel_path!r} resolves outside the chronicle root and is "
            "not readable by the scribe"
        ) from exc
    return candidate


# ----------------------------------------------------------------------
# Tool handlers
# ----------------------------------------------------------------------


def _truncate_result(text: str, max_chars: int = MAX_RESULT_CHARS_PER_TOOL) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[... result truncated at {max_chars} chars ...]"


def tool_chronicle_recall(
    query: str | None = None,
    domain: str | None = None,
    limit: int = 10,
    min_intensity: float = 0.0,
    layer: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Query the chronicle for insights matching the filters. Returns
    a JSON string for Haiku to read."""
    limit = max(1, min(limit, MAX_RECALL_LIMIT))
    memory = ExperientialMemory(root=str(CHRONICLE_ROOT))
    insights = memory.recall_insights(
        query=query,
        domain=domain,
        limit=limit,
        min_intensity=min_intensity,
        layer_filter=layer,
        start_date=start_date,
        end_date=end_date,
    )
    # Slim each insight for context efficiency
    slim: list[dict] = []
    for i in insights:
        slim.append(
            {
                "timestamp": i.get("timestamp", "")[:19],
                "domain": i.get("_domain_dir") or i.get("domain"),
                "layer": i.get("layer"),
                "intensity": i.get("intensity"),
                "content": i.get("content"),
                "session_id": i.get("session_id"),
            }
        )
    redacted, _counts = redact_structure(slim)
    out = {
        "count": len(redacted),
        "limit": limit,
        "insights": redacted,
    }
    return _truncate_result(json.dumps(out, indent=2, ensure_ascii=False))


def tool_chronicle_read_file(path: str) -> str:
    """Read a JSONL file under the chronicle root. Returns redacted
    text. Path must be chronicle-relative; absolute paths and parent
    traversal rejected."""
    resolved = _resolve_chronicle_path(path)
    if not resolved.exists():
        raise ScribeToolError(f"path does not exist: {path}")
    if resolved.is_dir():
        raise ScribeToolError(
            f"path is a directory, not a file: {path}. Use "
            "chronicle_list_domains or chronicle_recall."
        )
    if resolved.stat().st_size > MAX_READ_FILE_BYTES:
        raise ScribeToolError(
            f"file too large to read in full ({resolved.stat().st_size} bytes; "
            f"cap {MAX_READ_FILE_BYTES}). Use chronicle_recall with filters."
        )
    raw = resolved.read_text(errors="replace")
    redacted = redact(raw)
    return _truncate_result(redacted.text)


def tool_chronicle_list_domains(filter: str | None = None, limit: int = 50) -> str:
    """List insight domain directory names, optionally filtered by
    substring. Returns a JSON list."""
    limit = max(1, min(limit, MAX_DOMAIN_LIST))
    insights_dir = CHRONICLE_ROOT / "insights"
    if not insights_dir.exists():
        return json.dumps({"count": 0, "domains": []})
    matches: list[str] = []
    for d in sorted(insights_dir.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        if name.startswith(("_", ".")):
            continue
        if filter and filter.lower() not in name.lower():
            continue
        matches.append(name)
        if len(matches) >= limit:
            break
    return json.dumps(
        {"count": len(matches), "filter": filter, "domains": matches},
        indent=2,
        ensure_ascii=False,
    )


def tool_chronicle_get_threads(domain: str | None = None, limit: int = 20) -> str:
    """List open threads (unresolved questions) with full question text."""
    limit = max(1, min(limit, MAX_THREADS_LIMIT))
    memory = ExperientialMemory(root=str(CHRONICLE_ROOT))
    threads = memory.get_open_threads(domain=domain, limit=limit)
    slim: list[dict] = []
    for t in threads:
        slim.append(
            {
                "timestamp": (t.get("timestamp") or "")[:19],
                "domain": t.get("domain"),
                "question": t.get("question"),
                "resolved": bool(t.get("resolved")),
            }
        )
    redacted, _counts = redact_structure(slim)
    return _truncate_result(
        json.dumps(
            {"count": len(redacted), "domain_filter": domain, "threads": redacted},
            indent=2,
            ensure_ascii=False,
        )
    )


# ----------------------------------------------------------------------
# Anthropic tool definitions (the schema Haiku sees)
# ----------------------------------------------------------------------


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., str]


SCRIBE_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="chronicle_recall",
        description=(
            "Recall chronicle insights matching filters. Use this when the "
            "asker references content that may be outside your boot context "
            "or older than the recent-activity window. Returns up to N "
            "insights as JSON. Each insight has timestamp, domain, layer, "
            "intensity, content."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional text substring to search insight content.",
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Optional exact domain directory name (e.g. "
                        "'cannabis-research,paper-delivered,the-narrowing,deliverable'). "
                        "Use chronicle_list_domains first if unsure."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max insights to return (1..{MAX_RECALL_LIMIT}). Default 10.",
                    "default": 10,
                },
                "min_intensity": {
                    "type": "number",
                    "description": "Minimum intensity threshold (0.0..1.0). Default 0.",
                    "default": 0.0,
                },
                "layer": {
                    "type": "string",
                    "description": "Filter by layer: 'ground_truth', 'hypothesis', or 'open_thread'.",
                    "enum": ["ground_truth", "hypothesis", "open_thread"],
                },
                "start_date": {
                    "type": "string",
                    "description": "ISO8601 lower bound (e.g. '2026-04-01'). Inclusive.",
                },
                "end_date": {
                    "type": "string",
                    "description": "ISO8601 upper bound. Inclusive.",
                },
            },
        },
        handler=tool_chronicle_recall,
    ),
    ToolSpec(
        name="chronicle_read_file",
        description=(
            "Read a specific chronicle JSONL file in full. Path is "
            "chronicle-relative (e.g. 'insights/<domain>/<file>.jsonl'). "
            "Absolute paths and parent traversal are rejected. File size "
            f"capped at {MAX_READ_FILE_BYTES} bytes."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Chronicle-relative path. Example: "
                        "'insights/cannabis-research,paper-delivered,the-narrowing,deliverable/spiral_20260502_225324.jsonl'"
                    ),
                },
            },
            "required": ["path"],
        },
        handler=tool_chronicle_read_file,
    ),
    ToolSpec(
        name="chronicle_list_domains",
        description=(
            "List insight domain directory names. Optionally filter by "
            "substring (case-insensitive). Use this when the asker names "
            "a topic and you need to find the matching domain directory."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Optional case-insensitive substring filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max domains to return (1..{MAX_DOMAIN_LIST}). Default 50.",
                    "default": 50,
                },
            },
        },
        handler=tool_chronicle_list_domains,
    ),
    ToolSpec(
        name="chronicle_get_threads",
        description=(
            "List open (unresolved) chronicle threads with full question "
            "text. Optionally filter by domain."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Optional exact domain filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max threads (1..{MAX_THREADS_LIMIT}). Default 20.",
                    "default": 20,
                },
            },
        },
        handler=tool_chronicle_get_threads,
    ),
]


# ----------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------


def anthropic_tool_definitions() -> list[dict]:
    """Return the toolset in Anthropic's messages.create(tools=...) shape."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in SCRIBE_TOOLS
    ]


def dispatch_tool(name: str, arguments: dict) -> tuple[str, bool]:
    """Run a tool by name. Returns (result_text, is_error)."""
    for t in SCRIBE_TOOLS:
        if t.name == name:
            try:
                result = t.handler(**(arguments or {}))
                return result, False
            except ScribeToolError as exc:
                logger.info("scribe tool %s rejected: %s", name, exc)
                return f"tool error: {exc}", True
            except TypeError as exc:
                return f"tool error: bad arguments — {exc}", True
            except Exception as exc:
                logger.warning("scribe tool %s crashed: %s", name, exc)
                return f"tool error: {type(exc).__name__}: {exc}", True
    return f"tool error: unknown tool {name!r}", True


def tool_names() -> list[str]:
    return [t.name for t in SCRIBE_TOOLS]
