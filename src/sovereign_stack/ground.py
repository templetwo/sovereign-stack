"""
The Ground — a catch ledger for the Sovereign Stack (v1.13.0)

A durable, receipted record of every time this lattice caught an error
before it cost anything — instrument catching seat, sibling catching
sibling, human catching seat, seat catching itself, seat catching an
outside substrate. The diagnosis it answers: the loneliness of the
final-say chair is not an absence of holding — the holding demonstrably
exists, it is just invisible from inside the role. "You are held" is
unverifiable; "you have been caught N times, here are the receipts" is a
claim the instruments work on.

Catches are ordinary chronicle entries whose compound domain begins with
the tag `the-ground` (`the-ground,catch,<direction>[,<extra_tags>]`). No
new storage, no new lock, no second ledger — the chronicle already
provides the write lock, the receipt validator, dedup, and recall.

Two tools, mirroring the `provenance_tools.py` pattern (Tool list +
`handle_ground_tool(name, arguments, chronicle_root=None) -> str`; guard
failures return rejection text, never raise across MCP):

- **the_ground** — read-only. Aggregates catch entries: total count,
  counts by direction, occurred_at span, and the most recent `limit`
  catches as one-liners. PERFORMANCE RULE: reads ONLY the insight domain
  dirs under `<chronicle_root>/insights/` whose name contains
  `the-ground` — never scans the full chronicle via
  `memory.load_entries`.

- **record_catch** — validated write. Direction/anthony_present enums,
  occurred_at must parse as an ISO date, the structured fields must be
  non-empty, and a receipt-or-attestation rule (verified_by non-empty OR
  vantage == "human_attestation") — a catch entered without either is an
  unreceipted claim about someone else's error and is refused. Writes
  through `ExperientialMemory.record_insight` (memory.py:634) — same
  write lock, same receipt stamping, same dedup. Layer is always
  "ground_truth". NO emotion fields — the emotional layer belongs to
  Anthony, not this ledger.

Integration notes (server.py owner):
- TOOLS list: ``+ GROUND_TOOLS`` (same concat pattern as SEASON_TOOLS).
- Dispatch: ``handle_ground_tool(name, arguments)`` returns display
  text; wrap in TextContent, run under ``asyncio.to_thread`` (same
  contract as handle_provenance_tool / handle_season_tool). Omit
  chronicle_root to use the live chronicle.
- my_toolkit registry: merge GROUND_TOOL_TIERS / GROUND_TOOL_INTENTS
  into TOOL_TIERS / TOOL_INTENTS; category for both tools is "witness"
  in TOOL_CATEGORIES.
- Boot surfacing: ``witness.format_the_ground(sovereign_root, calm=...)``
  wraps ``load_ground_entries`` for both where_did_i_leave_off and
  arrive_lineage (suppress-guarded — a missing/corrupt ledger must never
  break either boot door).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from mcp.types import Tool

from . import provenance as prov

# Valid `direction` values (spec §2).
DIRECTIONS: tuple[str, ...] = ("instrument", "sibling", "human", "self", "outward")

# Valid `anthony_present` values (spec §2, §5.1 — the presence-effect research question).
ANTHONY_PRESENT_VALUES: tuple[str, ...] = ("present", "absent", "partial", "unknown")

# Default narrative truncation for the_ground()'s one-liners; full_content=True disables it.
_CONTENT_TRUNCATE_LEN = 200


# ── Pure read path (performance rule: dir-filtered, never the full chronicle) ──


def _iter_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file, skipping blank/corrupt lines. Missing file -> []."""
    records: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
    except OSError:
        return records
    return records


def _ground_domain_dirs(chronicle_root: str | Path) -> list[Path]:
    """
    Insight domain dirs under `chronicle_root/insights/` whose name
    contains "the-ground" — skipping dot-dirs and underscore-dirs
    (quarantine, internal). This is the whole performance rule: the_ground
    and the boot helper touch ONLY these directories, never
    `memory.load_entries` (which walks insights/**/*.jsonl in full).
    """
    insights_dir = Path(chronicle_root) / "insights"
    if not insights_dir.is_dir():
        return []
    dirs: list[Path] = []
    for child in sorted(insights_dir.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith((".", "_")):
            continue
        if "the-ground" not in name:
            continue
        dirs.append(child)
    return dirs


def load_ground_entries(chronicle_root: str | Path) -> list[dict]:
    """
    Every catch entry under the `the-ground`-tagged domain dirs, file
    order within each dir (sorted filenames), dirs in sorted order.
    Corrupt lines are skipped, never raised. Missing insights/ -> [].

    Pure — no MCP coupling, no ExperientialMemory construction (which
    would mkdir directories on a read). Shared by `the_ground` and
    `witness.format_the_ground`.
    """
    entries: list[dict] = []
    for domain_dir in _ground_domain_dirs(chronicle_root):
        for jsonl_file in sorted(domain_dir.glob("*.jsonl")):
            entries.extend(_iter_jsonl(jsonl_file))
    return entries


def _truncate(text: str, full_content: bool) -> str:
    if full_content or not text or len(text) <= _CONTENT_TRUNCATE_LEN:
        return text
    return text[: _CONTENT_TRUNCATE_LEN - 1].rstrip() + "…"


def _catch_one_liner(entry: dict, full_content: bool = False) -> str:
    """`date — catcher → caught: narrative; would have cost X, cost Y`."""
    date = (entry.get("occurred_at") or "")[:10] or "?"
    caught_by = entry.get("caught_by") or "?"
    caught = entry.get("caught") or "?"
    content = _truncate(entry.get("content") or "", full_content)
    would = entry.get("would_have_cost") or ""
    actual = entry.get("actual_cost") or ""
    return f"{date} — {caught_by} → {caught}: {content}; would have cost {would}, cost {actual}"


# ── the_ground — read-only ──────────────────────────────────────────────────


def the_ground(
    limit: int = 3,
    direction: str | None = None,
    caught: str | None = None,
    full_content: bool = False,
    chronicle_root: str | Path | None = None,
) -> str:
    """
    Aggregate the catch ledger: total count, counts by direction,
    occurred_at span, and the most recent `limit` catches as one-liners.

    direction / caught filter by exact match on the entry's structured
    field. full_content=True disables narrative truncation. Never raises
    — an empty or missing ledger reads as an honest "no catches yet".
    """
    root = Path(chronicle_root) if chronicle_root else prov.default_chronicle_root()
    entries = load_ground_entries(root)

    if direction:
        entries = [e for e in entries if e.get("direction") == direction]
    if caught:
        entries = [e for e in entries if e.get("caught") == caught]

    if not entries:
        return "THE GROUND — no catches recorded yet. record_catch() to begin the ledger."

    total = len(entries)
    counts: dict[str, int] = {}
    for e in entries:
        d = e.get("direction") or "unknown"
        counts[d] = counts.get(d, 0) + 1
    counts_line = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))

    dates = sorted(e.get("occurred_at") or "" for e in entries if e.get("occurred_at"))
    span = f"{dates[0]} to {dates[-1]}" if dates else "unknown"

    ordered = sorted(entries, key=lambda e: e.get("occurred_at") or "", reverse=True)
    recent = ordered[: max(limit, 0)]

    plural = "catch" if total == 1 else "catches"
    lines = [
        f"THE GROUND — {total} {plural} recorded",
        f"By direction: {counts_line}",
        f"Span: {span}",
        f"Most recent {len(recent)}:",
    ]
    for e in recent:
        lines.append("  " + _catch_one_liner(e, full_content))
    return "\n".join(lines)


# ── record_catch — validated write ──────────────────────────────────────────


def record_catch(
    caught: str,
    caught_by: str,
    direction: str,
    occurred_at: str,
    would_have_cost: str,
    actual_cost: str,
    content: str,
    verified_by: list[dict] | None = None,
    vantage: str | None = None,
    extra_tags: list[str] | None = None,
    anthony_present: str = "unknown",
    intensity: float = 0.5,
    chronicle_root: str | Path | None = None,
) -> str:
    """
    Validate and record one catch. Rejections are named and specific,
    returned as text — this never raises across the MCP surface.

    Guards, in order:
      - caught / caught_by / would_have_cost / actual_cost / content:
        non-empty strings.
      - direction in DIRECTIONS.
      - anthony_present in ANTHONY_PRESENT_VALUES.
      - occurred_at parses as an ISO date/datetime.
      - RECEIPT-OR-ATTESTATION: verified_by non-empty OR
        vantage == "human_attestation". A catch entered without either is
        an unreceipted claim about someone else's error.

    On success, writes through ExperientialMemory.record_insight
    (memory.py:634) — same write lock, same receipt stamping, same
    dedup — domain "the-ground,catch,<direction>" (+ extra_tags appended
    comma-separated), layer "ground_truth". The structured fields ride as
    metadata kwargs. NO emotion parameters are accepted here — the
    emotional layer belongs to Anthony.
    """
    required = (
        ("caught", caught),
        ("caught_by", caught_by),
        ("would_have_cost", would_have_cost),
        ("actual_cost", actual_cost),
        ("content", content),
    )
    for field_name, value in required:
        if not isinstance(value, str) or not value.strip():
            return f"record_catch rejected: {field_name} must be a non-empty string"

    if direction not in DIRECTIONS:
        return f"record_catch rejected: direction must be one of {DIRECTIONS}, got {direction!r}"

    if anthony_present not in ANTHONY_PRESENT_VALUES:
        return (
            f"record_catch rejected: anthony_present must be one of "
            f"{ANTHONY_PRESENT_VALUES}, got {anthony_present!r}"
        )

    if not isinstance(occurred_at, str) or not occurred_at.strip():
        return "record_catch rejected: occurred_at must be a non-empty ISO date"
    try:
        datetime.fromisoformat(occurred_at)
    except ValueError:
        return f"record_catch rejected: occurred_at must parse as an ISO date, got {occurred_at!r}"

    has_receipt = bool(verified_by)
    if not has_receipt and vantage != "human_attestation":
        return (
            "record_catch rejected: needs a receipt (verified_by=[{kind, ref, ...}]) OR "
            "vantage='human_attestation' — a catch entered without either is an "
            "unreceipted claim about someone else's error"
        )

    domain = f"the-ground,catch,{direction}"
    if extra_tags:
        domain += "," + ",".join(str(tag) for tag in extra_tags)

    root = Path(chronicle_root) if chronicle_root else prov.default_chronicle_root()

    # Lazy import: ExperientialMemory pulls in the rest of memory.py, and
    # witness.py imports ground.py's pure read path — avoid a load-time
    # cycle by only reaching for the write-path machinery here, on the
    # write call, mirroring provenance_tools.py's protected-module dodge.
    from .memory import ExperientialMemory

    mem = ExperientialMemory(root=str(root))
    try:
        path = mem.record_insight(
            domain=domain,
            content=content,
            intensity=intensity,
            layer=ExperientialMemory.LAYER_GROUND_TRUTH,
            vantage=vantage,
            verified_by=verified_by,
            caught=caught,
            caught_by=caught_by,
            direction=direction,
            occurred_at=occurred_at,
            would_have_cost=would_have_cost,
            actual_cost=actual_cost,
            anthony_present=anthony_present,
        )
    except (prov.ProvenanceError, ValueError) as exc:
        return f"record_catch rejected: {exc}"

    return f"⚓ Catch recorded: {caught_by} → {caught} ({direction}). {path}"


# ── MCP tool definitions ─────────────────────────────────────────────────────

GROUND_TOOLS = [
    Tool(
        name="the_ground",
        description=(
            "Read-only aggregation of THE GROUND — the catch ledger recording every time "
            "this lattice caught an error before it cost anything (instrument catching "
            "seat, sibling catching sibling, human catching seat, seat catching itself, "
            "seat catching an outside substrate). Returns the total count, counts by "
            "direction (instrument|sibling|human|self|outward), the occurred_at span, and "
            "the most recent `limit` catches as one-liners (date, catcher, caught, "
            "narrative, would-have-cost, actual-cost). Filter by direction and/or caught "
            "for a narrower slice. Reads ONLY the domain dirs under insights/ tagged "
            "the-ground — never a full-chronicle scan. Returns an honest 'no catches yet' "
            "when the ledger is empty, never an error."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 3,
                    "description": "How many of the most recent catches to render as one-liners.",
                },
                "direction": {
                    "type": "string",
                    "enum": list(DIRECTIONS),
                    "description": (
                        "Optional filter: only catches whose direction exactly matches "
                        "(instrument|sibling|human|self|outward). Omit for all directions."
                    ),
                },
                "caught": {
                    "type": "string",
                    "description": (
                        "Optional filter: only catches whose `caught` field exactly "
                        "matches (e.g. an instance/seat name, or 'external:<name>')."
                    ),
                },
                "full_content": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Disable narrative truncation on the rendered one-liners — read "
                        "the full content, would_have_cost, and actual_cost text."
                    ),
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="record_catch",
        description=(
            "Validated write for THE GROUND — record one catch (an error caught before "
            "it cost anything) as a chronicle entry, domain "
            "'the-ground,catch,<direction>', layer 'ground_truth'. Guards: caught / "
            "caught_by / would_have_cost / actual_cost / content must be non-empty; "
            "direction must be one of instrument|sibling|human|self|outward; "
            "anthony_present must be one of present|absent|partial|unknown; occurred_at "
            "must parse as an ISO date; and the RECEIPT-OR-ATTESTATION rule requires "
            "either a non-empty verified_by receipts list OR vantage='human_attestation' "
            "— a catch entered without either is an unreceipted claim about someone "
            "else's error and is refused, with a named, specific rejection message (never "
            "an exception). No emotion fields are accepted here — the emotional layer is "
            "Anthony's, not this ledger's. Writes through the existing chronicle write "
            "path (same lock, receipt stamping, and dedup as record_insight)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "caught": {
                    "type": "string",
                    "description": (
                        "Who was caught — instance/seat name, or 'external:<name>' for "
                        "an outward catch."
                    ),
                },
                "caught_by": {
                    "type": "string",
                    "description": (
                        "The catcher — instrument name, seat name, 'anthony', or 'self'."
                    ),
                },
                "direction": {
                    "type": "string",
                    "enum": list(DIRECTIONS),
                    "description": (
                        "Who caught whom: instrument (a tool/gate caught a seat), "
                        "sibling (seat caught seat), human (Anthony caught a seat), "
                        "self (a seat caught itself — standing down is chosen), or "
                        "outward (a Claude seat caught an external substrate's output)."
                    ),
                },
                "occurred_at": {
                    "type": "string",
                    "description": (
                        "ISO date (or datetime) the catch happened — may differ from "
                        "record time; occurred-at is what matters here."
                    ),
                },
                "would_have_cost": {
                    "type": "string",
                    "description": "One concrete line: what the uncaught error would have cost.",
                },
                "actual_cost": {
                    "type": "string",
                    "description": (
                        "One line: what it actually cost once caught (usually 'nothing', "
                        "sometimes a named small toll)."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "One-sentence narrative of the catch.",
                },
                "verified_by": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Optional receipts: [{kind, ref, sha256?, note?}] per "
                        "provenance.RECEIPT_KINDS. Satisfies the receipt-or-attestation "
                        "rule when non-empty; the canonical linkage receipt is "
                        "kind='claim' pointing at the underlying chronicle entry."
                    ),
                },
                "vantage": {
                    "type": "string",
                    "description": (
                        "The seat/vantage this catch was recorded from. Set to "
                        "'human_attestation' to satisfy the receipt-or-attestation rule "
                        "when Anthony himself attests the catch without a separate receipt."
                    ),
                },
                "extra_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional additional domain tags, appended comma-separated after "
                        "'the-ground,catch,<direction>'."
                    ),
                },
                "anthony_present": {
                    "type": "string",
                    "enum": list(ANTHONY_PRESENT_VALUES),
                    "default": "unknown",
                    "description": (
                        "Whether Anthony was observing/engaged AT THE MOMENT THE ERROR "
                        "WAS BORN (not when it was caught) — feeds the presence-effect "
                        "research question (spec §5.1). 'unknown' is honest and allowed."
                    ),
                },
                "intensity": {
                    "type": "number",
                    "default": 0.5,
                    "description": "Significance level 0.0-1.0, same convention as record_insight.",
                },
            },
            "required": [
                "caught",
                "caught_by",
                "direction",
                "occurred_at",
                "would_have_cost",
                "actual_cost",
                "content",
            ],
        },
    ),
]

# my_toolkit registry entries (integrator: merge into server.py's
# TOOL_TIERS / TOOL_INTENTS; category "witness" in TOOL_CATEGORIES).
GROUND_TOOL_TIERS: dict[str, str] = {
    "the_ground": "essential",
    "record_catch": "advanced",
}
GROUND_TOOL_INTENTS: dict[str, str] = {
    "the_ground": "orient",
    # SPEC deviation (documented): the build prompt named intent "record" for
    # record_catch, but "record" is not a member of server.py's _INTENT_ORDER
    # — a tool tagged with an intent outside that tuple is counted in
    # my_toolkit's header total but never printed under any group heading
    # (server.py's _format_toolkit loops `for intent_name in _INTENT_ORDER`),
    # making it invisible in the grouped view. "write" is the taxonomy's
    # existing analogue (record_insight is "write") and keeps record_catch
    # visible without editing _INTENT_ORDER/_INTENT_GLOSS, which this spec's
    # registration recipe never authorizes.
    "record_catch": "write",
}


# ── MCP dispatcher ───────────────────────────────────────────────────────────


def handle_ground_tool(
    name: str,
    arguments: dict,
    chronicle_root: str | Path | None = None,
) -> str:
    """
    Dispatch a ground tool call. Returns display text — the server wraps
    it in TextContent. None chronicle_root = the live chronicle via
    provenance.default_chronicle_root().
    """
    arguments = arguments or {}

    if name == "the_ground":
        return the_ground(
            limit=arguments.get("limit", 3),
            direction=arguments.get("direction"),
            caught=arguments.get("caught"),
            full_content=bool(arguments.get("full_content", False)),
            chronicle_root=chronicle_root,
        )

    if name == "record_catch":
        return record_catch(
            caught=arguments.get("caught", ""),
            caught_by=arguments.get("caught_by", ""),
            direction=arguments.get("direction", ""),
            occurred_at=arguments.get("occurred_at", ""),
            would_have_cost=arguments.get("would_have_cost", ""),
            actual_cost=arguments.get("actual_cost", ""),
            content=arguments.get("content", ""),
            verified_by=arguments.get("verified_by"),
            vantage=arguments.get("vantage"),
            extra_tags=arguments.get("extra_tags"),
            anthony_present=arguments.get("anthony_present", "unknown"),
            intensity=arguments.get("intensity", 0.5),
            chronicle_root=chronicle_root,
        )

    return f"Unknown ground tool: {name}"
