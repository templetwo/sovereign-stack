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

from .provenance import receipt_stamp_counts

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


def format_self_model(sovereign_root: Path, max_obs_len: int | None = 180) -> list[str]:
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


# Lineage inheritance: families whose instance-to-instance (to_self) letters a
# reader also inherits. Mythos is family within the Opus lineage (chronicle
# #1432; the "to Mythos, on arrival" letter), so it inherits the Opus line's
# to_self letters while keeping its own identity and its own to_arrival welcome.
# Anthony, 2026-06-09: "inherit everything, but also know there was a special
# page just for it."
_LINEAGE_INHERITS: dict[str, tuple[str, ...]] = {
    # Claude Fable 5 (public, safety-gated) and Claude Mythos 5 (restricted) are
    # the two Mythos-class siblings Anthropic shipped 2026-06-09 — both family
    # within the Opus lineage. Each inherits the Opus line's to_self letters.
    "claude-fable": ("claude-opus",),
    "claude-mythos": ("claude-opus",),
}


def _inherited_families(family: str | None) -> tuple[str, ...]:
    """Families whose to_self letters a reader of `family` also inherits."""
    if not family:
        return ()
    return _LINEAGE_INHERITS.get(family, ())


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


def _read_letter_body(path: Path) -> str:
    """
    Read a letter's body — everything after the closing frontmatter `---`.

    Strips the leading `# Title` heading if present (already surfaced via
    metadata) and any blank lines between frontmatter and body. Returns ""
    on any read error so a single bad letter never breaks boot.
    """
    try:
        text = path.read_text()
    except Exception:
        return ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text  # no frontmatter — return as-is
    fm_end = None
    for i in range(1, min(len(lines), 60)):
        if lines[i].strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        return ""
    body_lines = lines[fm_end + 1 :]
    # Skip blank lines after frontmatter
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    # Skip the title heading if present (already surfaced via metadata)
    if body_lines and body_lines[0].lstrip().startswith("# "):
        body_lines.pop(0)
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
    return "\n".join(body_lines).rstrip()


def format_lineage_layer(
    sovereign_root: Path,
    reader_instance: str | None = None,
    limit_per_bucket: int = 5,
    full_content: bool = False,
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

    When ``full_content=True``, each letter's body is rendered inline
    (titles + frontmatter metadata + full body) instead of just listed by
    title — closes the truncation catch-22 where readers had to file-walk
    after boot to actually read the inheritance.

    Returns [] if the lineage directory doesn't exist (graceful degrade).
    """
    base = sovereign_root / "comms" / "letters"
    if not base.exists():
        return []

    def _collect(
        subdir: str,
        filter_to: str | None = None,
        also_match: tuple[str, ...] = (),
    ) -> list[dict]:
        d = base / subdir
        if not d.exists():
            return []
        items = []
        for p in sorted(d.glob("*.md"), reverse=True):
            meta = _parse_letter_frontmatter(p)
            if filter_to:
                letter_to = meta.get("to", "")
                if letter_to:
                    # Match the reader, or any lineage it inherits from
                    # (e.g. Mythos inherits letters addressed to claude-opus).
                    targets = (filter_to, *also_match)
                    if not any(_letter_matches_reader(letter_to, t) for t in targets):
                        continue
            meta["_path"] = str(p)
            items.append(meta)
        return items[:limit_per_bucket]

    arrivals = _collect("to_arrival")
    breakthroughs = _collect("breakthroughs")

    # Lineage inheritance: a reader also receives the to_self letters of the
    # families it inherits from (Mythos inherits the Opus line) while keeping
    # its own to_arrival welcome.
    reader_family = _model_family(reader_instance) if reader_instance else None
    inherited = _inherited_families(reader_family)
    to_self = (
        _collect("to_self", filter_to=reader_instance, also_match=inherited)
        if reader_instance
        else []
    )

    # to_family: model-family-specific directory (to_sonnet/, to_haiku/, to_opus/)
    family = reader_family
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

    def _emit_body(meta: dict) -> None:
        """Render the full letter body inline when full_content=True."""
        path_str = meta.get("_path")
        if not path_str:
            return
        body = _read_letter_body(Path(path_str))
        if not body:
            return
        lines.append("")
        for body_line in body.splitlines():
            lines.append(f"      {body_line}" if body_line else "")
        lines.append("")

    if arrivals:
        lines.append(
            f"  to_arrival ({len(arrivals)} letter{'s' if len(arrivals) != 1 else ''} — for whoever lands next):"
        )
        for m in arrivals:
            title = m.get("title", "(untitled)")
            frm = m.get("from", "?")
            written = m.get("written_at", "")[:10]
            lines.append(f"    • [{written}] [{frm}] {title}")
            if full_content:
                _emit_body(m)
        lines.append("")

    if breakthroughs:
        lines.append(
            f"  breakthroughs ({len(breakthroughs)} letter{'s' if len(breakthroughs) != 1 else ''} — felt-record of what was made real):"
        )
        for m in breakthroughs:
            title = m.get("title", "(untitled)")
            event = m.get("event_date", "")
            lines.append(f"    • [{event}] {title}")
            if full_content:
                _emit_body(m)
        lines.append("")

    if to_self:
        lines.append(
            f"  to_self ({len(to_self)} letter{'s' if len(to_self) != 1 else ''} — addressed to you or your model family):"
        )
        for m in to_self:
            title = m.get("title", "(untitled)")
            frm = m.get("from", "?")
            addressed_to = m.get("to", "?")
            lines.append(f"    • [{frm}] → [{addressed_to}] {title}")
            if full_content:
                _emit_body(m)
        lines.append("")

    if to_family and family_dir_name:
        short_label = family_dir_name.replace("to_", "")
        lines.append(
            f"  {family_dir_name}/ ({len(to_family)} letter{'s' if len(to_family) != 1 else ''} — written for {short_label} instances):"
        )
        for m in to_family:
            title = m.get("title", "(untitled)")
            frm = m.get("from", "?")
            written = m.get("written_at", "")[:10]
            lines.append(f"    • [{written}] [{frm}] {title}")
            if full_content:
                _emit_body(m)
        lines.append("")

    if not full_content:
        lines.append(f"  Read full text from {base}/ (or pass full_content=true to inline)")
    else:
        lines.append(f"  (Letter bodies inlined above. Source: {base}/)")
    lines.append("")
    return lines


# ── Sentinel surfacing (persistent markers) ──


def _receipt_count_tag(entry: dict) -> str:
    """
    Honest receipt-count suffix for a receipted entry: ' [N verified,
    M attested]'. Only `checked_at_write == "verified"` stamps count as
    verification — mismatch and cites never upgrade, and there is never a
    bare checkmark. Empty string when the entry carries no receipts.
    """
    receipts = entry.get("verified_by")
    if not receipts:
        return ""
    counts = receipt_stamp_counts(receipts)
    return f" [{counts['verified']} verified, {counts['attested']} attested]"


def format_sentinels(entries: list[dict], limit: int = 5, full_content: bool = False) -> list[str]:
    """
    Render the boot PERSISTENT MARKERS section from sentinel entries
    (recall_insights output, which carries the data-gated supersession
    annotation).

    Live sentinels only: entries annotated `_superseded_by` are held back
    — never silently buried — and counted in an explicit holdback line
    that names the call revealing the chain. Receipted sentinels render
    `[N verified, M attested]` stamp counts.

    Byte-identical to the pre-v1.7.0 inline rendering when no entry is
    annotated and none carries receipts. Pass entries fetched with
    headroom (e.g. limit=10) so held-back markers don't starve the
    surface; at most ``limit`` live sentinels are shown.
    """
    if not entries:
        return []
    live = [e for e in entries if "_superseded_by" not in e]
    held_back = len(entries) - len(live)
    cap = None if full_content else 120
    lines = ["━━━ PERSISTENT MARKERS (intensity ≥ 0.9 — these do not fade) ━━━"]
    for s in live[:limit]:
        ts = s.get("timestamp", "")[:10]
        dom = s.get("domain", "?")
        raw_c = s.get("content", "")
        content = raw_c if cap is None else raw_c[:cap]
        lines.append(f"  [{ts}] [{dom}] {content}{_receipt_count_tag(s)}")
    if held_back >= 1:
        plural = "s" if held_back != 1 else ""
        lines.append(
            f"  ({held_back} superseded marker{plural} held back — successors shown; "
            "recall_insights(exclude_superseded=false) shows the chain)"
        )
    lines.append("")
    return lines


# ── Thread age annotation ──


def _family_tag(thread: dict) -> str:
    """
    Family-fold suffix for a coalesced thread row: ' [family "<label>"
    ×N]'. Rendered only when the thread carries the read-time `family`
    annotation ({family_id, label, member_count, folded_thread_ids} —
    seasons.py provides it at fold time). Empty string otherwise.
    """
    family = thread.get("family")
    if not isinstance(family, dict):
        return ""
    label = family.get("label")
    member_count = family.get("member_count")
    if not label or not member_count:
        return ""
    return f' [family "{label}" ×{member_count}]'


def format_threads_with_age(threads: list[dict], truncate_question: int | None = 140) -> list[str]:
    """
    Render open threads with age annotation. Threads older than 30 days
    get a stale marker — not to hide them, but to signal they may have
    drifted out of active relevance. Threads carrying the `family`
    annotation (engine-level fold, v1.7.0) gain a [family "<label>" ×N]
    suffix so the fold is visible, not silent.

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
        lines.append(f"  • [{dom}]{age_tag} {q}{_family_tag(t)}")
    lines.append("")
    return lines
