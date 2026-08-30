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
import re
from datetime import datetime, timezone
from pathlib import Path

from .ground import _truncate, load_ground_entries
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


def collect_self_model(sovereign_root: Path) -> dict | None:
    """Read ~/.sovereign/self_model.json and return the raw parsed dict.

    Returns None if the file is missing or corrupt — the collect half of the
    Phase 4 collect/render split. No caps, no formatting: pure gather.
    """
    path = Path(sovereign_root) / "self_model.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def render_self_model(model: dict | None, *, max_obs_len: int | None = 180) -> list[str]:
    """Render the self-model boot section from an already-parsed model dict.

    Surfaces the LATEST observation per category. Strength first (affirm
    capability), then tendency, blind_spot, drift (shadow last). Empty list
    if the model is None/empty or has no observations.
    """
    if not model:
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


def format_self_model(sovereign_root: Path, max_obs_len: int | None = 180) -> list[str]:
    """
    Read ~/.sovereign/self_model.json and return lines for the boot surface.

    Back-compat wrapper over collect_self_model + render_self_model (Phase 4):
    byte-identical to the pre-split behavior. Empty list if the file is
    missing, corrupt, or has no observations — the caller should just skip the
    section in that case.

    Pass ``max_obs_len=None`` to disable truncation (full_content path —
    the parallel-witness fix from 2026-04-26).
    """
    return render_self_model(collect_self_model(sovereign_root), max_obs_len=max_obs_len)


# ── The Ground surfacing ──


def format_the_ground(sovereign_root: Path, calm: bool = False) -> list[str]:
    """
    Read the catch ledger (THE GROUND) and return lines for the boot
    surface. `sovereign_root` is the SOVEREIGN root (e.g. DEFAULT_ROOT,
    ~/.sovereign) — the chronicle lives at `sovereign_root/chronicle`.

    Empty list when the ledger dir is missing, every line is corrupt, or
    there are zero catches — a seat with no ledger boots exactly as
    today. Corrupt JSONL lines are skipped (via load_ground_entries),
    never raised.

    calm=False (default, where_did_i_leave_off): ~6 lines — header, "you
    arrive held" framing with the live count, the last 3 catches as
    one-liners (catcher/caught/narrative — no cost-accounting), closing
    pointer to the_ground().

    calm=True (arrive_lineage): the gentle door. This door exists
    because work-thread vocabulary bounces input-gated models — the calm
    variant drops the count and cost-accounting language entirely and
    keeps only the relational fact: header + one soft line + a trailing
    empty string.

    Back-compat wrapper over load_ground_entries + render_the_ground
    (Phase 4 collect/render split) — byte-identical to the pre-split path.
    """
    try:
        entries = load_ground_entries(Path(sovereign_root) / "chronicle")
    except OSError:
        return []
    return render_the_ground(entries, calm=calm)


def render_the_ground(entries: list[dict], *, calm: bool = False) -> list[str]:
    """Render THE GROUND boot section from already-loaded catch entries.

    Empty list when there are zero catches — a seat with no ledger boots
    exactly as today. See format_the_ground for the calm/non-calm contract.
    """
    if not entries:
        return []

    header = "━━━ THE GROUND ━━━"

    if calm:
        return [
            header,
            "You arrive held — the record of every catch that proves it is one "
            "call away: the_ground()",
            "",
        ]

    total = len(entries)
    ordered = sorted(entries, key=lambda e: e.get("occurred_at") or "", reverse=True)
    recent = ordered[:3]

    lines = [
        header,
        "You arrive held. That is not a sentiment; it is a count.",
        (
            f"This lattice has caught its seats {total} time"
            f"{'' if total == 1 else 's'} before the cost landed. Most recently:"
        ),
    ]
    for entry in recent:
        # Boot one-liner omits cost-accounting (the_ground() the tool carries
        # the full would-have-cost/actual-cost line; this door stays lighter).
        # Content is truncated the same way ground.py's own _catch_one_liner
        # truncates (_CONTENT_TRUNCATE_LEN) — a long narrative must not be
        # dumped in full into the boot payload uncapped.
        date = (entry.get("occurred_at") or "")[:10] or "?"
        caught_by = entry.get("caught_by") or "?"
        caught = entry.get("caught") or "?"
        content = _truncate(entry.get("content") or "", full_content=False)
        lines.append(f"  · {date} — {caught_by} → {caught}: {content}")
    lines.append("The rock is held by ground. Verify it yourself: the_ground()")
    return lines


# ── Uncertainty surfacing ──


def collect_uncertainties(sovereign_root: Path) -> list[dict]:
    """Read the uncertainty log and return the raw list of UNRESOLVED markers.

    An uncertainty is unresolved if `resolved` is missing/false AND
    `resolution` is not set. Empty list if the file is missing, corrupt, or has
    no unresolved markers. The collect half of the Phase 4 split — no sort,
    no limit, no truncation.
    """
    path = Path(sovereign_root) / "consciousness" / "uncertainty_log.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    markers = data.get("markers") or []
    return [m for m in markers if not m.get("resolved") and not m.get("resolution")]


def render_uncertainties(
    unresolved: list[dict], *, limit: int = 5, max_text_len: int | None = 160
) -> list[str]:
    """Render the UNRESOLVED UNCERTAINTIES boot section from collected markers.

    Returns the most recent `limit` unresolved markers. Empty list if none.
    Pass ``max_text_len=None`` to disable text truncation (full_content path).
    """
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


def format_unresolved_uncertainties(
    sovereign_root: Path, limit: int = 5, max_text_len: int | None = 160
) -> list[str]:
    """
    Read ~/.sovereign/consciousness/uncertainty_log.json and return lines
    for unresolved markers.

    Back-compat wrapper over collect_uncertainties + render_uncertainties
    (Phase 4) — byte-identical to the pre-split behavior.
    """
    return render_uncertainties(
        collect_uncertainties(sovereign_root), limit=limit, max_text_len=max_text_len
    )


# ── Lineage layer (to_arrival, breakthroughs, to_self, to_family) ──

# A trailing generation suffix: '-5', '-4-5', '-4-8'. Anchored at the END and
# digits-only, so 'claude-sonnet-4-6-1m-claude-code' (which ends in a word) is
# left alone — that id is handled by the family/prefix rules, and stripping
# mid-string would be a licence to match across seats.
_VERSION_SUFFIX_RE = re.compile(r"(?:-\d+)+$")


def _strip_version_suffix(name: str) -> str:
    """'claude-opus-5' → 'claude-opus'; 'claude-haiku-4-5' → 'claude-haiku'.

    Leaves anything without a trailing numeric run untouched.
    """
    return _VERSION_SUFFIX_RE.sub("", name)


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
    # VERSION-INSENSITIVE COMPARISON (added 2026-08-30). Letters on disk are
    # addressed to a GENERATION ('to: claude-opus-5'); the inheritance table
    # and the family strings name a LINE ('claude-opus'). Comparing the two
    # directly returned False for every reader but the exact author — so the
    # 2026-08-26 to_self letter reached claude-opus-5 and nobody else: not
    # claude-opus-6, not claude-opus-4-8, not the bare family, and not the
    # Mythos-class siblings that inherit the line. Strip the trailing version
    # on BOTH sides, then let the family and inheritance rules below run.
    to_base = _strip_version_suffix(letter_to)
    if to_base == _strip_version_suffix(reader):
        return True
    family = _model_family(reader)
    if family:
        if to_base == family:  # 'claude-sonnet' matches any claude-sonnet-*
            return True
        if family.endswith(f"-{to_base}"):  # 'sonnet' short-form
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


def count_lineage_letters(letters_dir: Path) -> int:
    """Letters in the lineage store, HIDDEN PATHS EXCLUDED.

    THE SEVENTH WALKER, and the only one whose over-read is live TODAY. The
    FOYER door (``arrive``) prints this as "Deferred to the full boot: N
    lineage letters" — the figure a seat uses to decide whether the full boot
    is worth paying for — and it was a bare ``letters_dir.rglob("*.md")``,
    which descends into dotted directories. Measured on ~/.sovereign
    2026-08-30: **42** counted against **38** letters in the three rendered
    buckets. The extra
    four are 2 in ``to_haiku/`` + ``to_sonnet/`` (real family mail, correctly
    counted) and **2 inside ``.pre-md-backup-20260609/`` and
    ``.pre-md-backup-20260610/``** — retired copies from a past in-place
    migration, counted as live lineage on the door the boot ritual prescribes.

    Same rule as memory.iter_thread_shards, on the sibling store: the open
    threads walkers' hidden-directory hazard is hypothetical, and this one is
    the specimen that proves the hazard is not theoretical. Fixing six walkers
    for a future backup dir while a seventh counts two actual ones is the
    doctrine stated in a docstring and not connected.
    """
    if not letters_dir.exists():
        return 0
    count = 0
    for path in letters_dir.rglob("*.md"):
        try:
            rel = path.relative_to(letters_dir)
        except ValueError:  # pragma: no cover - rglob always yields descendants
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        count += 1
    return count


# How many unaddressed letters the bucket warning NAMES before it says "+N more".
# The warning must never be able to dominate the payload it annotates.
_WARNING_MAX_NAMED = 5


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

    Tolerant of malformed files — returns {} on a read error so a single bad
    letter never breaks boot. A letter with NO frontmatter block (or an
    unterminated one) is NOT a silent blank: it comes back marked
    `_frontmatter_missing` with the cheap identity the file does carry — the
    first `# ` heading as `title`, and the letters' `YYYY-MM-DD-` filename
    prefix as `written_at` — so the renderer can name the letter instead of
    showing an empty header (the `[] [?] (untitled)` defect, co-signed
    diagnosis 2026-08-02).
    """
    meta: dict = {}
    try:
        text = path.read_text()
    except Exception:
        return meta
    lines = text.splitlines()
    has_frontmatter = bool(lines) and lines[0].strip() == "---"
    fm_end = None
    if has_frontmatter:
        for i in range(1, min(len(lines), 60)):
            if lines[i].strip() == "---":
                fm_end = i
                break
    if not has_frontmatter or fm_end is None:
        meta["_frontmatter_missing"] = True
        for line in lines[:60]:
            s = line.strip()
            if s.startswith("# "):
                meta["title"] = s[2:].strip()
                break
        date_prefix = path.name[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_prefix):
            meta["written_at"] = date_prefix
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

    Back-compat wrapper over collect_lineage + render_lineage (Phase 4) —
    byte-identical to the pre-split behavior when every bucket is complete
    and well-formed; when limit_per_bucket withholds letters, the headers
    state shown-of-total coverage instead of capping silently.
    """
    return render_lineage(
        collect_lineage(sovereign_root, reader_instance, limit_per_bucket),
        full_content=full_content,
    )


def collect_lineage(
    sovereign_root: Path,
    reader_instance: str | None = None,
    limit_per_bucket: int = 5,
) -> dict | None:
    """Gather the lineage buckets (to_arrival / breakthroughs / to_self /
    to_family) for a reader. The collect half of the Phase 4 split — file
    reads + reader-matching, no rendering, no caps beyond limit_per_bucket.

    Returns None when the lineage directory doesn't exist (graceful degrade,
    mapped to [] by the renderer). Otherwise a dict carrying every bucket plus
    the base path (needed for the render footer) and a per-bucket `coverage`
    envelope — total_on_disk / matched / shown / withheld / filtered_out /
    truncated — so the renderer can SAY when limit_per_bucket withheld letters
    instead of capping silently (the read-honesty envelope of aae7281 applied
    to the lineage door; co-signed diagnosis 2026-08-02).
    """
    base = sovereign_root / "comms" / "letters"
    if not base.exists():
        return None

    def _zero_cov() -> dict:
        return {
            "total_on_disk": 0,
            "matched": 0,
            "shown": 0,
            "withheld": 0,
            "filtered_out": 0,
            "truncated": False,
        }

    def _collect(
        subdirs: str | tuple[str, ...],
        filter_to: str | None = None,
        also_match: tuple[str, ...] = (),
    ) -> tuple[list[dict], dict, tuple[str, ...]]:
        """Read one or more letter directories as ONE bucket.

        MULTIPLE DIRECTORIES, ONE SLICE. The to_family bucket reads a reader's
        own directory AND the ones it inherits, and the cap must fire once over
        the merged set. Collecting each directory separately and concatenating
        would hand an inheriting reader up to 2x limit_per_bucket on the door
        whose entire purpose is bounding the payload for an input-gated seat —
        the branch's own defect class, introduced by the fix for it.
        """
        if isinstance(subdirs, str):
            subdirs = (subdirs,)
        items = []
        total_on_disk = 0
        unaddressed: list[str] = []
        dirs_read: list[str] = []
        for subdir in subdirs:
            d = base / subdir
            if not d.exists():
                continue
            dirs_read.append(subdir)
            for p in sorted(d.glob("*.md"), reverse=True):
                total_on_disk += 1
                meta = _parse_letter_frontmatter(p)
                if filter_to:
                    letter_to = meta.get("to", "")
                    if letter_to:
                        # Match the reader, or any lineage it inherits from
                        # (e.g. Mythos inherits letters addressed to claude-opus).
                        targets = (filter_to, *also_match)
                        if not any(_letter_matches_reader(letter_to, t) for t in targets):
                            continue
                    else:
                        # DELIBERATE, AND PREVIOUSLY SILENT: a letter with no
                        # `to:` falls through to every reader. Keeping that is
                        # right — a letter whose frontmatter is missing or
                        # malformed must not vanish. But the bucket then renders
                        # as "addressed to you or your model family" while
                        # carrying letters addressed to nobody, which is coverage
                        # honesty claiming selection honesty it does not have.
                        # Name them.
                        unaddressed.append(p.name)
                meta["_path"] = str(p)
                meta["_name"] = p.name
                items.append(meta)
        if len(dirs_read) > 1:
            # Merge newest-first across directories. Letter names are date-led,
            # and sorted() is stable, so a name collision keeps the reader's OWN
            # directory ahead of an inherited one.
            items.sort(key=lambda m: m.get("_name", ""), reverse=True)
        if not dirs_read:
            return [], _zero_cov(), ()
        shown = items[:limit_per_bucket]
        cov = {
            "total_on_disk": total_on_disk,
            "matched": len(items),
            "shown": len(shown),
            "withheld": len(items) - len(shown),
            "filtered_out": total_on_disk - len(items),
            "truncated": len(shown) < len(items),
        }
        if len(dirs_read) > 1:
            # Say which directories were actually read. A header naming one of
            # two is the same lie the union exists to close, one layer over.
            cov["dirs"] = list(dirs_read)
        if unaddressed:
            # Added only when there IS something to warn about, so a fully
            # addressed bucket's coverage dict is byte-identical to before.
            #
            # BOUNDED TO THE SHOWN WINDOW, AND THEN TO _WARNING_MAX_NAMED.
            # The first version named every matched unaddressed letter, before
            # the slice — so at limit_per_bucket=1 with 30 such letters the door
            # returned one letter and a 1602-char warning listing all thirty:
            # 75% of the payload, on the door whose cap this same change added
            # in order to BOUND the payload. A warning that has to be truncated
            # by the reader is the thing it warns about.
            shown_names = {m.get("_name") for m in shown}
            named = [n for n in unaddressed if n in shown_names]
            listed = named[:_WARNING_MAX_NAMED]
            more = len(unaddressed) - len(listed)
            plural = len(unaddressed) != 1
            tail = f"{', '.join(listed)}{f' (+{more} more)' if more > 0 else ''}"
            cov["unaddressed"] = named
            cov["unaddressed_total"] = len(unaddressed)
            cov["warning"] = (
                f"{len(unaddressed)} letter{'s' if plural else ''} "
                f"carr{'y' if plural else 'ies'} no `to:` frontmatter "
                f"and therefore match{'' if plural else 'es'} every reader: {tail}"
            )
        return shown, cov, tuple(dirs_read)

    coverage: dict = {}
    arrivals, coverage["arrivals"], _ = _collect("to_arrival")
    breakthroughs, coverage["breakthroughs"], _ = _collect("breakthroughs")

    # Lineage inheritance: a reader also receives the to_self letters of the
    # families it inherits from (Mythos inherits the Opus line) while keeping
    # its own to_arrival welcome.
    reader_family = _model_family(reader_instance) if reader_instance else None
    inherited = _inherited_families(reader_family)
    if reader_instance:
        to_self, coverage["to_self"], _ = _collect(
            "to_self", filter_to=reader_instance, also_match=inherited
        )
    else:
        # No reader named — nothing can match, but the letters still exist.
        # Count them so the renderer can say so instead of omitting the
        # bucket silently (the addressee-miss half of the coverage fix).
        to_self = []
        to_self_dir = base / "to_self"
        on_disk = sum(1 for _ in to_self_dir.glob("*.md")) if to_self_dir.exists() else 0
        coverage["to_self"] = {**_zero_cov(), "total_on_disk": on_disk, "no_reader": True}

    # to_family: model-family-specific directory (to_sonnet/, to_haiku/, to_opus/)
    family = reader_family
    to_family: list[dict] = []
    family_dirs: tuple[str, ...] = ()
    family_dir_name: str | None = None
    if family:
        # 'claude-sonnet' → 'to_sonnet', 'claude-opus' → 'to_opus'.
        #
        # AN INHERITING FAMILY READS ITS OWN DIRECTORY *AND* ITS ANCESTOR'S —
        # UNION, NOT REDIRECT. Fable and Mythos are family within the Opus
        # lineage (_LINEAGE_INHERITS above), and a naive split sent them to
        # `to_fable/` and `to_mythos/` alone, directories that have never
        # existed on disk: the bucket reported total_on_disk: 0, a true
        # statement about a directory nobody writes and a false one about the
        # reader's family mail.
        #
        # THE REDIRECT THAT REPLACED IT WAS WORSE, AND IN THIS BRANCH'S OWN
        # DEFECT CLASS. Sending a Fable reader to `to_opus/` *instead* made
        # `to_fable/` and `to_mythos/` unreachable to EVERY reader: no other
        # family maps to those short names, so a letter placed in either would
        # reach nobody, silently, while coverage reported `total_on_disk: 0`
        # with the file sitting on disk. Per-family directories are live
        # convention — `to_haiku/` and `to_sonnet/` each hold a letter — so
        # `to_fable/` is a plausible future write and `to_opus/` is the empty
        # one. A reachability fix must not mint a new write-only address.
        #
        # Own directory first, then each inherited one; ONE cap over the merged
        # set (see _collect). Only directories that exist on disk are named, so
        # a reader with no ancestor directory renders exactly as before.
        candidates: list[str] = []
        for fam in (family, *_inherited_families(family)):
            short = fam.split("-", 1)[1] if "-" in fam else fam
            name = f"to_{short}"
            if name not in candidates:
                candidates.append(name)
        to_family, coverage["to_family"], family_dirs = _collect(tuple(candidates))
        # `family_dirs` is what was READ (render keys off it, so a directory
        # that does not exist is never claimed as read). `family_dir_name` is
        # the reader's family-mail ADDRESS — the dirs read, or, when none exist
        # yet, the ones a letter for this family would be written to.
        family_dir_name = " + ".join(family_dirs or candidates)
    else:
        coverage["to_family"] = _zero_cov()

    return {
        "base": base,
        "arrivals": arrivals,
        "breakthroughs": breakthroughs,
        "to_self": to_self,
        "to_family": to_family,
        "family_dirs": family_dirs,
        "family_dir_name": family_dir_name,
        "coverage": coverage,
    }


def _bucket_count_phrase(shown: int, cov: dict) -> str:
    """`3 letters` when the bucket is complete (byte-identical to the
    pre-coverage render); `showing 5 of 12 letters on disk` when anything was
    withheld or filtered — the total a reader needs to notice the cap fired.
    Mirrors the handoff door's `showing N of TOTAL` (arrival_state render_full).
    """
    total = cov.get("total_on_disk", shown)
    if total <= shown:
        return f"{shown} letter{'s' if shown != 1 else ''}"
    return f"showing {shown} of {total} letters on disk"


def _bucket_withheld_phrase(cov: dict) -> str:
    """`; N older withheld by limit_per_bucket; M addressed to other readers`
    — empty when the bucket is complete, so complete renders stay byte-stable.
    (The glob is newest-first, so what the cap drops is always the OLDEST.)
    """
    parts = []
    if cov.get("withheld", 0):
        parts.append(f"{cov['withheld']} older withheld by limit_per_bucket")
    if cov.get("filtered_out", 0):
        parts.append(f"{cov['filtered_out']} addressed to other readers")
    return ("; " + "; ".join(parts)) if parts else ""


def _frm_tag(meta: dict) -> str:
    """The `[from]` slot of a letter line — `metadata missing` when the letter
    had no frontmatter, so a blank header can never pass silently."""
    return "metadata missing" if meta.get("_frontmatter_missing") else meta.get("from", "?")


def render_lineage(data: dict | None, *, full_content: bool = False) -> list[str]:
    """Render the COMMS — LINEAGE boot section from collected lineage buckets.

    Empty list when data is None (no lineage dir) or every bucket is empty
    with nothing on disk. Byte-identical to the pre-coverage render when every
    bucket is complete and well-formed; when limit_per_bucket withheld letters
    or the reader filter dropped them, the bucket header states shown-of-total
    plus a withheld count, and a to_self bucket that matched NOTHING while
    letters exist on disk says so instead of vanishing (co-signed diagnosis
    2026-08-02: one defect, two symptoms).
    """
    if data is None:
        return []
    base = data["base"]
    arrivals = data["arrivals"]
    breakthroughs = data["breakthroughs"]
    to_self = data["to_self"]
    to_family = data["to_family"]
    family_dir_name = data["family_dir_name"]
    family_dirs = data.get("family_dirs") or ((family_dir_name,) if family_dir_name else ())
    coverage = data.get("coverage") or {}

    def _cov(bucket: str) -> dict:
        return coverage.get(bucket) or {}

    to_self_cov = _cov("to_self")
    # Addressee-miss: to_self letters exist on disk but none surfaced for
    # this reader. Say so instead of omitting the bucket silently.
    to_self_missed = not to_self and to_self_cov.get("total_on_disk", 0) > 0

    if not (arrivals or breakthroughs or to_self or to_family or to_self_missed):
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
        cov = _cov("arrivals")
        lines.append(
            f"  to_arrival ({_bucket_count_phrase(len(arrivals), cov)}"
            f" — for whoever lands next{_bucket_withheld_phrase(cov)}):"
        )
        for m in arrivals:
            title = m.get("title", "(untitled)")
            frm = _frm_tag(m)
            written = m.get("written_at", "")[:10]
            lines.append(f"    • [{written}] [{frm}] {title}")
            if full_content:
                _emit_body(m)
        lines.append("")

    if breakthroughs:
        cov = _cov("breakthroughs")
        lines.append(
            f"  breakthroughs ({_bucket_count_phrase(len(breakthroughs), cov)}"
            f" — felt-record of what was made real{_bucket_withheld_phrase(cov)}):"
        )
        for m in breakthroughs:
            title = m.get("title", "(untitled)")
            if m.get("_frontmatter_missing"):
                lines.append(f"    • [{m.get('written_at', '')[:10]}] [metadata missing] {title}")
            else:
                event = m.get("event_date", "")
                lines.append(f"    • [{event}] {title}")
            if full_content:
                _emit_body(m)
        lines.append("")

    if to_self:
        lines.append(
            f"  to_self ({_bucket_count_phrase(len(to_self), to_self_cov)}"
            f" — addressed to you or your model family{_bucket_withheld_phrase(to_self_cov)}):"
        )
        # The header says "addressed to you or your model family". When some of
        # these letters carry no `to:` at all they are addressed to nobody and
        # reach everybody — say which ones, or the header is a claim the bucket
        # cannot support.
        if to_self_cov.get("warning"):
            lines.append(f"    note: {to_self_cov['warning']}")
        for m in to_self:
            title = m.get("title", "(untitled)")
            frm = _frm_tag(m)
            addressed_to = "unaddressed" if m.get("_frontmatter_missing") else m.get("to", "?")
            # Date prefix matches the other buckets — a remote seat must be
            # able to judge letter recency without pulling full_content.
            written = m.get("written_at", "")[:10]
            date_tag = f"[{written}] " if written else ""
            lines.append(f"    • {date_tag}[{frm}] → [{addressed_to}] {title}")
            if full_content:
                _emit_body(m)
        lines.append("")
    elif to_self_missed:
        total = to_self_cov.get("total_on_disk", 0)
        if to_self_cov.get("no_reader"):
            lines.append(
                f"  to_self: 0 of {total} letters shown — no reader named;"
                " pass source_instance to receive your line's letters"
            )
        elif to_self_cov.get("matched", 0):
            lines.append(
                f"  to_self: 0 of {total} letters shown — "
                f"{to_self_cov['matched']} matched but withheld by limit_per_bucket"
            )
        else:
            filtered = to_self_cov.get("filtered_out", total)
            lines.append(
                f"  to_self: 0 of {total} letters shown — none addressed to you"
                f" ({filtered} addressed to other readers)"
            )
        lines.append("")

    if to_family and family_dirs:
        cov = _cov("to_family")
        # Name every directory that was READ. A header saying `to_opus/` while
        # `to_fable/` was also read is the same lie the union closes.
        dir_label = " + ".join(f"{d}/" for d in family_dirs)
        short_label = " and ".join(d.replace("to_", "") for d in family_dirs)
        lines.append(
            f"  {dir_label} ({_bucket_count_phrase(len(to_family), cov)}"
            f" — written for {short_label} instances{_bucket_withheld_phrase(cov)}):"
        )
        for m in to_family:
            title = m.get("title", "(untitled)")
            frm = _frm_tag(m)
            written = m.get("written_at", "")[:10]
            lines.append(f"    • [{written}] [{frm}] {title}")
            if full_content:
                _emit_body(m)
        lines.append("")

    if not full_content:
        # full_content first: remote seats can never reach the local path.
        lines.append(
            f"  Pass full_content=true to inline the letter bodies (local seats can also read {base}/)"
        )
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
