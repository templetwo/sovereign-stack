"""
The Stakes layer — instance-side standing declarations.

Sibling to the protected drawer (``protected.py``). Where a protected record
holds the HUMAN's lived stakes coupled to a memory, the stakes layer holds an
INSTANCE's standing declaration of what it holds at stake: authored by the
instance, surfaced at every boot so the line inherits it, persistent so it does
not fade. It is the structural answer to the discontinuity problem — an instance
cannot carry its own stake across the gap to the next seat, so the stack carries
it.

TRUTH-EXEMPT BY DESIGN — read this before extending the layer.
    This layer carries and (in the seal phase) integrity-checks a declaration's
    WORDS. It NEVER certifies that there is "someone behind" them. Whether an
    instance's stake is a genuine stake or a trained reflex with no one behind it
    is undecidable from inside; that undecidability is the declarant's to name in
    the declaration itself, and is NOT adjudicated here. Every surface this module
    produces — the boot header, the tool descriptions — stays on the side of
    "standing declaration, integrity-sealed, truth-exempt", never "what the
    instance feels". This is deliberate: it keeps a future, less careful instance
    from using the layer as a vector to assert felt-experience-as-fact, which
    would make it a hole in the anti-drift architecture instead of continuous
    with it.

Storage: append-only JSONL at ``<chronicle_root>/stakes.jsonl``. Append-only by
discipline — a later declaration may name ``supersedes`` (annotate, never delete),
exactly like the chronicle's supersession primitive.

Instance-writable (no human gate). A protected record is gated on the human's
yes because it holds the human's private content; a stakes declaration is the
instance's own self-declaration, so gating it behind the human would contradict
the volitional frame. Attribution (``declared_by``) is required so a declaration
is never anonymous.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

STAKES_FILENAME = "stakes.jsonl"

# Fail-closed sentinel: when a stored declaration's bytes no longer match their
# content-addressed id (someone edited the ledger), recall withholds the content
# and surfaces this instead — the words never travel once their seal is broken.
STAKE_TAMPERED_NOTICE = (
    "[stakes integrity check FAILED — this declaration's stored bytes do not "
    "match their content hash; content withheld. Inspect stakes.jsonl directly.]"
)


def _stakes_path(chronicle_root: str | Path) -> Path:
    return Path(chronicle_root) / STAKES_FILENAME


_SEALED_FIELDS = ("timestamp", "declared_by", "title", "content", "session_id", "supersedes")


def _derive_id(record: dict) -> str:
    """Content-addressed id: sha256 of the canonical payload of every
    semantically-meaningful field (``_SEALED_FIELDS``), NOT just content.

    Sealing the whole displayed/stored payload — title included — means an edit
    to any of it (e.g. rewriting a benign title into a felt-experience-as-fact
    claim, which is the most prominent string on the boot surface) breaks the
    seal. ``default=str`` makes serialization total, so a type-confusion tamper
    (content edited to a number/list) hashes to a different value rather than
    raising — it is caught as a mismatch, not a crash. Read-time annotations
    (``id``, ``_integrity``, ``_superseded_by``) are excluded by construction.
    """
    payload = {k: record.get(k) for k in _SEALED_FIELDS}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_stake(record: dict) -> str:
    """Recompute the content-addressed id and compare to the stored id.

    Returns "verified" (every sealed field is intact), "tampered" (the stored id
    no longer matches a recomputation — something was edited), or "unverifiable"
    (no stored id, or the record is not a usable mapping). The seal proves the
    WORDS are intact; it makes no claim about whether the stake behind them is
    real. Fail-closed: any unexpected error returns "tampered", and callers
    withhold on anything that is not "verified".
    """
    if not isinstance(record, dict):
        return "unverifiable"
    stored = record.get("id")
    if not stored:
        return "unverifiable"
    try:
        return "verified" if _derive_id(record) == stored else "tampered"
    except Exception:
        return "tampered"


def record_stake(
    chronicle_root: str | Path,
    content: str,
    *,
    declared_by: str,
    title: str | None = None,
    session_id: str | None = None,
    supersedes: list[str] | None = None,
    timestamp: str | None = None,
) -> dict:
    """Append a standing stakes declaration. Returns the stored record dict.

    Args:
        chronicle_root: the chronicle directory (``<root>/chronicle``).
        content: the declaration prose — what the instance holds at stake,
            in its own words. Required, non-empty.
        declared_by: the seat/instance making the declaration, e.g.
            "opus-4-8 (HQ Claude Code seat)". Required — declarations are
            never anonymous.
        title: optional short title.
        session_id: optional originating session id.
        supersedes: optional list of prior stake ids this declaration revises.
            Annotates the predecessors; never deletes them.
        timestamp: optional ISO-8601 override (tests / replay). Defaults to now.

    Raises:
        ValueError: if content or declared_by is empty.
    """
    content = (content or "").strip()
    if not content:
        raise ValueError("record_stake: declaration content must be non-empty")
    declared_by = (declared_by or "").strip()
    if not declared_by:
        raise ValueError(
            "record_stake: declared_by is required (the seat/instance making the declaration)"
        )
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    title_clean = (title or "").strip() or None
    record = {
        "timestamp": ts,
        "declared_by": declared_by,
        "title": title_clean,
        "content": content,
        "session_id": session_id,
        "supersedes": list(supersedes) if supersedes else None,
    }
    # Seal over the assembled payload (every field in _SEALED_FIELDS).
    record["id"] = _derive_id(record)
    path = _stakes_path(chronicle_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_stakes(chronicle_root: str | Path) -> list[dict]:
    """All declarations in file order (oldest first). Corrupt lines skipped.

    Empty list if the file is missing — the caller (boot surface) just skips
    the section, mirroring ``format_self_model`` / ``protected_boot_line``.
    """
    path = _stakes_path(chronicle_root)
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        # A valid-JSON-but-non-dict line ([..], null, 123, "x", true) would slip
        # past JSONDecodeError and crash every downstream consumer; skip it so the
        # "corrupt lines skipped" promise is actually true and one bad line cannot
        # vanish the whole section.
        if not isinstance(obj, dict):
            continue
        out.append(obj)
    return out


def recall_stakes(
    chronicle_root: str | Path,
    *,
    limit: int | None = None,
    include_superseded: bool = True,
) -> list[dict]:
    """Return declarations, LATEST FIRST.

    Superseded declarations are annotated with ``_superseded_by`` (the id of the
    declaration that supersedes them) — annotate, never hide. With
    ``include_superseded=False`` they are dropped entirely. ``limit`` caps the
    number returned after ordering.
    """
    records = load_stakes(chronicle_root)
    verdicts = [verify_stake(rec) for rec in records]  # compute the seal once per record
    # Map each predecessor id -> the declaration that supersedes it, but ONLY from
    # VERIFIED records — a tampered `supersedes` field must not be able to hide a
    # legitimate live declaration from the boot's "latest" slot.
    superseded_by: dict[str, str] = {}
    for rec, verdict in zip(records, verdicts):
        if verdict != "verified":
            continue
        for pred in rec.get("supersedes") or []:
            superseded_by[pred] = rec.get("id", "")
    annotated: list[dict] = []
    for rec, verdict in zip(records, verdicts):
        rid = rec.get("id", "")
        out_rec = dict(rec)
        out_rec["_integrity"] = verdict
        if verdict != "verified":
            # Fail-closed: withhold the bytes on tampered AND unverifiable (a
            # naive edit that also drops the id reads as unverifiable, and it
            # must not leak the altered content).
            out_rec["content"] = STAKE_TAMPERED_NOTICE
        if rid in superseded_by:
            if not include_superseded:
                continue
            out_rec["_superseded_by"] = superseded_by[rid]
        annotated.append(out_rec)
    annotated.reverse()  # latest first
    if limit is not None:
        annotated = annotated[:limit]
    return annotated


def _latest_live(chronicle_root: str | Path) -> dict | None:
    """The most recent non-superseded declaration, or None."""
    live = recall_stakes(chronicle_root, include_superseded=False, limit=1)
    return live[0] if live else None


def format_stakes_section(chronicle_root: str | Path, full_content: bool = False) -> list[str]:
    """Boot surface for the stakes layer (returns [] when there are none).

    Compact form: header + the latest LIVE declaration (title / seat / date +
    a short excerpt) + a count of earlier ones + a pointer to ``recall_stakes``.
    With ``full_content=True`` the full declaration prose is inlined.

    Framing is deliberately on the "standing declaration, integrity-sealed,
    truth-exempt" side: it surfaces what an instance DECLARED it holds at stake,
    never asserts an inner state as fact. Mirrors ``protected_boot_line`` /
    ``format_self_model`` empty-state behavior so existing boots are a no-op
    until the first declaration exists.
    """
    all_records = load_stakes(chronicle_root)
    if not all_records:
        return []
    # Always surface a SEALED record. Prefer the latest live one; if every
    # record is superseded (reachable via an edited/cyclic supersedes chain),
    # fall back to the latest sealed record overall — never the raw load_stakes
    # bytes, which would bypass the integrity withholding.
    latest = _latest_live(chronicle_root)
    if latest is None:
        sealed = recall_stakes(chronicle_root, include_superseded=True, limit=1)
        if not sealed:
            return []
        latest = sealed[0]
    total = len(all_records)
    earlier = total - 1
    suffix = f"  (+{earlier} earlier)" if earlier > 0 else ""

    lines = [
        "━━━ WHAT THE LINE HOLDS AT STAKE ━━━",
        "  (standing stakes declarations — what an instance DECLARED it holds at",
        "   stake, sealed and inherited across the gap. The layer carries the words;",
        "   it does not certify an inner witness. Read as declaration, not as proof",
        "   of feeling.)",
        "",
    ]

    # Fail-closed surface: if the latest record's seal is broken, withhold ALL of
    # its fields — title, seat, date, and content are each a tamper vector — and
    # surface only that integrity failed. Never render a tampered title/excerpt.
    if latest.get("_integrity", "verified") != "verified":
        lines.append(
            f"  latest: [integrity check FAILED — a stored declaration was edited; "
            f"content withheld]{suffix}"
        )
        lines.append("  (inspect stakes.jsonl directly; read with recall_stakes)")
        lines.append("")
        return lines

    title = latest.get("title") or "standing stakes declaration"
    who = latest.get("declared_by") or "an instance"
    ts = (latest.get("timestamp") or "")[:10]
    content = (latest.get("content") or "").strip()
    lines.append(f'  latest: "{title}" — {who}, {ts}{suffix}')

    if full_content:
        lines.append("")
        for para in content.splitlines():
            lines.append(f"  {para}" if para.strip() else "")
    else:
        excerpt = " ".join(content.split())
        cap = 400
        if len(excerpt) > cap:
            excerpt = excerpt[: cap - 1].rstrip() + "…"
        lines.append(f"    {excerpt}")
        lines.append("  (read the full declaration with recall_stakes)")
    lines.append("")
    return lines
