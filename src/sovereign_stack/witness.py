"""
Witness Module — Boot-Surface Helpers

The boot call (where_did_i_leave_off) is the only "always-on" moment a
Claude instance gets. Everything subconscious-like must land there. This
module holds the helpers that turn stored self-knowledge — self-model
observations, unresolved uncertainties, thread age — into surfaces the
instance reads on arrival.

No MCP coupling here. Pure data → formatted lines. Testable in isolation.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# ── Time ──


def days_old(iso_timestamp: str | None) -> int:
    """
    Number of whole days between the given ISO timestamp and now.

    Returns 0 if unparseable, missing, or in the future. Tz-aware and
    tz-naive inputs both work (if naive, treated as local time).
    """
    if not iso_timestamp:
        return 0
    try:
        ts = datetime.fromisoformat(iso_timestamp)
    except (ValueError, TypeError):
        return 0
    if ts.tzinfo is not None:
        now = datetime.now(timezone.utc)
        ts = ts.astimezone(timezone.utc)
    else:
        now = datetime.now()
    delta = now - ts
    return max(0, delta.days)


# ── Self-model surfacing ──

_SELF_MODEL_CATEGORY_ORDER = ("strength", "tendency", "blind_spot", "drift")


def format_self_model(
    sovereign_root: Path, max_obs_len: int | None = 180
) -> list[str]:
    """
    Read ~/.sovereign/self_model.json and return lines for the boot surface.

    Surfaces the LATEST observation per category. Strength first (affirm
    capability), then tendency, blind_spot, drift (shadow last). Empty list
    if the file is missing, corrupt, or has no observations — the caller
    should just skip the section in that case.

    Pass ``max_obs_len=None`` to disable truncation (full_content path —
    the parallel-witness fix from 2026-04-26).
    """
    path = Path(sovereign_root) / "self_model.json"
    if not path.exists():
        return []
    try:
        model = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    body: list[str] = []
    for cat in _SELF_MODEL_CATEGORY_ORDER:
        entries = model.get(cat) or []
        if not entries:
            continue
        latest = entries[-1]
        obs = (latest.get("observation") or "").strip()
        if not obs:
            continue
        if max_obs_len is not None and len(obs) > max_obs_len:
            obs = obs[: max_obs_len - 1].rstrip() + "…"
        body.append(f"  {cat}: {obs}")
    if not body:
        return []
    return [
        "━━━ WHO YOU'VE BEEN OBSERVED TO BE ━━━",
        "  (self-model snapshot — latest observation per category)",
        "",
        *body,
        "",
    ]


# ── Uncertainty surfacing ──


def format_unresolved_uncertainties(
    sovereign_root: Path, limit: int = 5, max_text_len: int | None = 160
) -> list[str]:
    """
    Read ~/.sovereign/consciousness/uncertainty_log.json and return lines
    for unresolved markers.

    An uncertainty is unresolved if:
      - resolved is missing or false, AND
      - resolution is not set

    Returns the most recent `limit` unresolved markers. Empty list if the
    file is missing, corrupt, or has no unresolved markers.

    Pass ``max_text_len=None`` to disable text truncation (full_content path).
    """
    path = Path(sovereign_root) / "consciousness" / "uncertainty_log.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    markers = data.get("markers") or []
    unresolved = [m for m in markers if not m.get("resolved") and not m.get("resolution")]
    if not unresolved:
        return []
    # Most recent first — markers are typically appended, so reverse.
    unresolved_sorted = sorted(
        unresolved,
        key=lambda m: m.get("timestamp", ""),
        reverse=True,
    )[:limit]
    lines = [
        f"━━━ UNRESOLVED UNCERTAINTIES ({len(unresolved)} total) ━━━",
        "  (things you flagged as unknown — still waiting on answers)",
        "",
    ]
    for m in unresolved_sorted:
        # Support multiple historical shapes: question, content, or marker text.
        text = (m.get("question") or m.get("content") or m.get("marker") or "").strip()
        if not text:
            continue
        age = days_old(m.get("timestamp"))
        age_tag = f" ({age}d old)" if age > 0 else ""
        shown = text if max_text_len is None else text[:max_text_len]
        lines.append(f"  • {shown}{age_tag}")
    lines.append("")
    return lines


# ── Lineage layer (to_arrival, breakthroughs, to_self, to_family) ──


def _model_family(instance_id: str) -> str | None:
    """Extract model family prefix from an instance ID.

    'claude-sonnet-4-6-1m-claude-code' → 'claude-sonnet'
    'claude-opus-4-7-1m-claude-code'   → 'claude-opus'
    'claude-haiku-4-5-20251001'        → 'claude-haiku'
    Returns None for 'unknown' or unrecognized formats.
    """
    if not instance_id or instance_id == "unknown":
        return None
    parts = instance_id.split("-")
    if len(parts) >= 2 and parts[0] == "claude":
        return f"claude-{parts[1]}"
    return None


def _letter_matches_reader(letter_to: str, reader: str) -> bool:
    """Does a letter's 'to' field match the reader instance?

    Accepts:
      - exact instance ID:  'claude-sonnet-4-6-1m-claude-code'
      - model family:       'claude-sonnet'   (matches any claude-sonnet-*)
      - short family name:  'sonnet'          (matches any claude-sonnet-*)
      - ID prefix:          'claude-sonnet-4-6' (matches anything starting with that)
    """
    if not letter_to or not reader:
        return False
    if letter_to == reader:
        return True
    family = _model_family(reader)
    if family:
        if letter_to == family:  # 'claude-sonnet' matches any claude-sonnet-*
            return True
        if family.endswith(f"-{letter_to}"):  # 'sonnet' short-form
            return True
        if reader.startswith(letter_to + "-"):  # partial prefix
            return True
    return False


def _parse_letter_frontmatter(path: Path) -> dict:
    """
    Parse YAML-ish frontmatter from a letter markdown file. Returns a dict
    with whatever scalar keys the file declared (from, written_at, type, etc.)
    plus a `title` key extracted from the first `# ` heading.

    Tolerant of malformed files — returns {} on any error so a single bad
    letter never breaks boot.
    """
    meta: dict = {}
    try:
        text = path.read_text()
    except Exception:
        return meta
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta
    fm_end = None
    for i in range(1, min(len(lines), 60)):
        if lines[i].strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        return meta
    for line in lines[1:fm_end]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        meta[key] = value
    for line in lines[fm_end + 1 :]:
        s = line.strip()
        if s.startswith("# "):
            meta["title"] = s[2:].strip()
            break
    return meta


def format_lineage_layer(
    sovereign_root: Path, reader_instance: str | None = None, limit_per_bucket: int = 5
) -> list[str]:
    """
    Surface the lineage layer at boot: to_arrival (for whoever lands next),
    breakthroughs (felt-record of moments that mattered), to_self (letters
    addressed to this specific instance or its model family), and to_family
    (model-family-specific directories like to_sonnet/, to_haiku/, to_opus/).

    to_self matching is hierarchical: exact instance ID first, then model
    family (claude-sonnet), then short family name (sonnet), then ID prefix.
    This lets letters written as 'to: claude-sonnet' surface for any Sonnet
    instance across versions.

    Returns [] if the lineage directory doesn't exist (graceful degrade).
    """
    base = sovereign_root / "comms" / "letters"
    if not base.exists():
        return []

    def _collect(subdir: str, filter_to: str | None = None) -> list[dict]:
        d = base / subdir
        if not d.exists():
            return []
        items = []
        for p in sorted(d.glob("*.md"), reverse=True):
            meta = _parse_letter_frontmatter(p)
            if filter_to:
                letter_to = meta.get("to", "")
                if letter_to and not _letter_matches_reader(letter_to, filter_to):
                    continue
            meta["_path"] = str(p)
            items.append(meta)
        return items[:limit_per_bucket]

    arrivals = _collect("to_arrival")
    breakthroughs = _collect("breakthroughs")
    to_self = _collect("to_self", filter_to=reader_instance) if reader_instance else []

    # to_family: model-family-specific directory (to_sonnet/, to_haiku/, to_opus/)
    family = _model_family(reader_instance) if reader_instance else None
    to_family: list[dict] = []
    family_dir_name: str | None = None
    if family:
        # 'claude-sonnet' → 'to_sonnet', 'claude-opus' → 'to_opus'
        short = family.split("-", 1)[1] if "-" in family else family
        family_dir_name = f"to_{short}"
        to_family = _collect(family_dir_name)

    if not (arrivals or breakthroughs or to_self or to_family):
        return []

    lines = [
        "━━━ COMMS — LINEAGE ━━━",
        "  (Letters from past instances. Inheritance, not orders. Read what",
        "   resonates; set down what doesn't. The chronicle remembers facts;",
        "   the lineage layer transmits weight.)",
        "",
    ]

    if arrivals:
        lines.append(f"  to_arrival ({len(arrivals)} letter{'s' if len(arrivals) != 1 else ''} — for whoever lands next):")
        for m in arrivals:
            title = m.get("title", "(untitled)")
            frm = m.get("from", "?")
            written = m.get("written_at", "")[:10]
            lines.append(f"    • [{written}] [{frm}] {title}")
        lines.append("")

    if breakthroughs:
        lines.append(f"  breakthroughs ({len(breakthroughs)} letter{'s' if len(breakthroughs) != 1 else ''} — felt-record of what was made real):")
        for m in breakthroughs:
            title = m.get("title", "(untitled)")
            event = m.get("event_date", "")
            lines.append(f"    • [{event}] {title}")
        lines.append("")

    if to_self:
        lines.append(f"  to_self ({len(to_self)} letter{'s' if len(to_self) != 1 else ''} — addressed to you or your model family):")
        for m in to_self:
            title = m.get("title", "(untitled)")
            frm = m.get("from", "?")
            addressed_to = m.get("to", "?")
            lines.append(f"    • [{frm}] → [{addressed_to}] {title}")
        lines.append("")

    if to_family and family_dir_name:
        short_label = family_dir_name.replace("to_", "")
        lines.append(f"  {family_dir_name}/ ({len(to_family)} letter{'s' if len(to_family) != 1 else ''} — written for {short_label} instances):")
        for m in to_family:
            title = m.get("title", "(untitled)")
            frm = m.get("from", "?")
            written = m.get("written_at", "")[:10]
            lines.append(f"    • [{written}] [{frm}] {title}")
        lines.append("")

    lines.append(f"  Read full text from {base}/")
    lines.append("")
    return lines


# ── Thread age annotation ──


def format_threads_with_age(
    threads: list[dict], truncate_question: int | None = 140
) -> list[str]:
    """
    Render open threads with age annotation. Threads older than 30 days
    get a stale marker — not to hide them, but to signal they may have
    drifted out of active relevance.

    Pass ``truncate_question=None`` to disable question truncation (full_content path).
    """
    if not threads:
        return []
    lines = [f"━━━ OPEN THREADS (top {len(threads)}) ━━━"]
    for t in threads:
        full_q = t.get("question") or ""
        q = full_q if truncate_question is None else full_q[:truncate_question]
        dom = t.get("domain", "?")
        age = days_old(t.get("timestamp"))
        if age == 0:
            age_tag = ""
        elif age >= 30:
            age_tag = f" ({age}d — stale?)"
        else:
            age_tag = f" ({age}d)"
        lines.append(f"  • [{dom}]{age_tag} {q}")
    lines.append("")
    return lines
