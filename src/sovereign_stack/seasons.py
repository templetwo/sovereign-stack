"""
Seasons Module — Thread Families & Season Review (v1.7.0 "Receipts & Seasons")

The digestion layer. Two surfaces:

1. **Thread families** — append-only thread_families.jsonl groups
   split-rot threads into display-side families. Thread files are NEVER
   touched: family state is a fold (latest action per thread_id wins;
   unlink removes), and coalescing happens at read time. The ledger
   lives at the CHRONICLE ROOT, deliberately outside open_threads/ —
   get_open_threads globs ``threads_dir/*.jsonl`` (memory.py:664), so a
   ledger placed inside it would surface every record as a phantom
   thread (the D2 fatal).

2. **season_review** — a READ-ONLY digest: supersession candidates
   (cross-domain token overlap + legacy markers), thread-family
   candidates, policy candidates (the seeding path), hygiene findings,
   and dormancy/fragmentation stats. Every candidate line carries a
   ready-to-paste tool call. The pass changes nothing — it never even
   instantiates ExperientialMemory (whose constructor mkdirs); all
   reads go straight to the files.

House style per witness.py/memory.py: pure data → formatted text, zero
side effects at import, lazy file/dir creation on first write, all
paths parameterized (defaults point at ~/.sovereign).

Integration notes (server.py owner):
- TOOLS list: ``+ SEASON_TOOLS`` (same concat pattern as METABOLISM_TOOLS).
- Dispatch: ``handle_season_tool(name, arguments)`` returns display
  text; wrap in TextContent (same contract as handle_policy_tool).
  ``chronicle_root`` defaults to the live chronicle when omitted.
- my_toolkit registry: merge SEASON_TOOL_TIERS / SEASON_TOOL_INTENTS
  into TOOL_TIERS / TOOL_INTENTS; category for both tools is "seasons".
- get_open_threads coalescing: ``fold = fold_families(load_families(
  families_path_for(root)))`` then ``coalesce_threads(threads, fold)``
  (data-gated: empty fold returns the input unchanged). triage_threads
  uses ``coalesce_triaged(threads, fold)`` after scoring.
- witness.format_threads_with_age renders the ``family`` annotation
  (``[family "<label>" ×N]``) — annotation shape is stable here.
"""

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from mcp.types import Tool

from .memory import ExperientialMemory
from .policies import PolicyRegistry
from .protected import load_protected_fold
from .provenance import (
    LIVED_VANTAGES,
    derive_claim_id,
    display_id,
    fold_supersessions,
    has_legacy_marker,
    iter_chronicle_entries,
    load_supersessions,
    token_overlap,
    verify_archive_ref,
)
from .witness import days_old

# ── Constants ──

FAMILY_ACTIONS = ("link", "unlink")

# Load-bearing placement: the family ledger lives at the chronicle ROOT,
# never inside open_threads/ (get_open_threads globs threads_dir/*.jsonl
# and would surface ledger records as phantom threads).
FAMILIES_FILENAME = "thread_families.jsonl"

# Spec section 2 thresholds.
SUPERSESSION_OVERLAP = 0.5  # insight token-Jaccard
THREAD_OVERLAP = 0.45  # thread-question token-Jaccard
SENTINEL_INTENSITY = 0.9  # "persistent marker" threshold
BOOT_SENTINEL_SLOTS = 5  # where_did_i_leave_off limit (server.py:2743)
_POLICY_REGISTRY_OVERLAP = 0.5  # "already registered" similarity

SEASON_FOOTER = (
    "This pass changed nothing. Act via supersede_insight / link_threads / "
    "set_policy. Destructive merges remain human-gated."
)

_FAMILY_ID_RE = re.compile(r"^fam_\d{8}_\d{6}_[0-9a-f]{8}$")

# Policy-shaped: normative/imperative language a standing rule would use.
_POLICY_SHAPE_RE = re.compile(
    r"\b(always|never|must(?: not)?|do not|don't|required?|prefer|policy|non-negotiable)\b",
    re.IGNORECASE,
)

# Label-suggestion stopwords (thread questions are full sentences).
_LABEL_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "be",
        "can",
        "does",
        "do",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "should",
        "that",
        "the",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "why",
        "will",
        "with",
    ]
)

_TOKEN_RE = re.compile(r"[a-z0-9-]+")
_PREVIEW_CHARS = 80


def default_families_path() -> Path:
    """The live family ledger path. Computed on call, never at import."""
    return Path.home() / ".sovereign" / "chronicle" / FAMILIES_FILENAME


def families_path_for(chronicle_root: str | Path) -> Path:
    """Family ledger path for a given chronicle root (chronicle ROOT, not open_threads/)."""
    return Path(chronicle_root) / FAMILIES_FILENAME


# ── Exceptions ──


class FamilyError(ValueError):
    """A link_threads guard fired or a family record is invalid."""


# ── Family ledger: load / append / fold ──


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


def load_families(families_path: str | Path) -> list[dict]:
    """
    Read all family ledger records in file (append) order. Missing
    ledger -> [] — the file is created lazily on first append; reading
    never creates it.
    """
    return _iter_jsonl(Path(families_path))


def generate_family_id(label: str, timestamp: datetime | None = None) -> str:
    """fam_YYYYMMDD_HHMMSS_<8hex sha1(label)> — mirrors the thread_id scheme."""
    ts = timestamp or datetime.now(timezone.utc)
    digest = hashlib.sha1(label.strip().encode("utf-8")).hexdigest()[:8]
    return f"fam_{ts.strftime('%Y%m%d_%H%M%S')}_{digest}"


def build_family_record(
    *,
    action: str,
    family_id: str,
    label: str,
    member_thread_ids: list[str],
    primary_thread_id: str | None = None,
    note: str = "",
    by: str = "",
    timestamp: str | None = None,
) -> dict:
    """
    Build one ledger record in the exact spec schema (section 3).

    Raises:
        FamilyError: invalid action, malformed family_id, or an empty
            member list.
    """
    if action not in FAMILY_ACTIONS:
        raise FamilyError(f"invalid family action {action!r} (valid: {FAMILY_ACTIONS})")
    if not isinstance(family_id, str) or not _FAMILY_ID_RE.match(family_id):
        raise FamilyError(f"malformed family_id {family_id!r} (expected fam_YYYYMMDD_HHMMSS_8hex)")
    members = [t for t in (member_thread_ids or []) if isinstance(t, str) and t.strip()]
    if not members:
        raise FamilyError("member_thread_ids must name at least one thread")
    return {
        "action": action,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "family_id": family_id,
        "label": label,
        "member_thread_ids": members,
        "primary_thread_id": primary_thread_id,
        "note": note,
        "by": by,
    }


def append_family_record(families_path: str | Path, record: dict) -> dict:
    """
    Append one record to the family ledger (parent directory created
    lazily — first write, never import or read). Validates the
    action/id core so a hand-built record can't poison the fold.
    """
    if record.get("action") not in FAMILY_ACTIONS:
        raise FamilyError(f"invalid family action {record.get('action')!r}")
    fid = record.get("family_id")
    if not isinstance(fid, str) or not _FAMILY_ID_RE.match(fid):
        raise FamilyError(f"malformed family_id {fid!r}")
    path = Path(families_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def fold_families(records: list[dict]) -> dict[str, dict]:
    """
    Fold the append-only ledger into effective state: a map of
    thread_id -> the latest "link" record covering it. Latest action
    per thread_id wins; "unlink" removes the thread from its family.

    The full record is kept (not a bare family_id) so coalescing gets
    label/primary for free; membership of a family is the set of
    thread_ids whose folded record carries that family_id.
    """
    fold: dict[str, dict] = {}
    for record in records:
        action = record.get("action")
        members = record.get("member_thread_ids") or []
        if action == "link":
            for tid in members:
                if isinstance(tid, str) and tid:
                    fold[tid] = record
        elif action == "unlink":
            for tid in members:
                if isinstance(tid, str):
                    fold.pop(tid, None)
    return fold


def family_state(fold: dict[str, dict]) -> dict[str, dict]:
    """
    Per-family view of a fold: family_id -> {family_id, label,
    primary_thread_id, member_thread_ids, member_count}. Label and
    primary come from the family's LATEST folded record (extensions may
    rename or re-prime); member_thread_ids is every live member.
    """
    families: dict[str, dict] = {}
    for tid, record in fold.items():
        fid = record.get("family_id", "")
        state = families.setdefault(
            fid,
            {
                "family_id": fid,
                "label": "",
                "primary_thread_id": None,
                "member_thread_ids": [],
                "_latest": "",
            },
        )
        state["member_thread_ids"].append(tid)
        stamp = record.get("timestamp", "")
        if stamp >= state["_latest"]:
            state["_latest"] = stamp
            state["label"] = record.get("label", "")
            state["primary_thread_id"] = record.get("primary_thread_id")
    for state in families.values():
        del state["_latest"]
        state["member_count"] = len(state["member_thread_ids"])
    return families


# ── Family coalescing (engine-level fold for the thread read path) ──


def _group_present(threads: list[dict], fold: dict[str, dict]) -> dict[str, list[dict]]:
    """family_id -> the member rows present in this thread list (list order)."""
    present: dict[str, list[dict]] = {}
    for thread in threads:
        record = fold.get(thread.get("thread_id", ""))
        if record is not None:
            present.setdefault(record.get("family_id", ""), []).append(thread)
    return present


def coalesce_threads(threads: list[dict], fold: dict[str, dict]) -> list[dict]:
    """
    Display-side family fold for get_open_threads: non-primary members
    fold into the primary row; the primary gains a ``family``
    annotation {family_id, label, member_count, folded_thread_ids} —
    folded_thread_ids is ALWAYS present (folded rows must show their
    members), even when empty.

    Data-gated: an empty fold returns the input list unchanged —
    zero ledger records, zero change. When the recorded primary is not
    in the list (resolved, or none recorded), the family's first row in
    list order survives, preserving the caller's sort. member_count is
    the family's full live membership per the fold, not just the rows
    visible here. Thread dicts are never mutated; survivors are copies.
    """
    if not fold:
        return threads
    state = family_state(fold)
    present = _group_present(threads, fold)
    emitted: set[str] = set()
    out: list[dict] = []
    for thread in threads:
        record = fold.get(thread.get("thread_id", ""))
        if record is None:
            out.append(thread)
            continue
        fid = record.get("family_id", "")
        if fid in emitted:
            continue
        emitted.add(fid)
        members_here = present[fid]
        fam = state[fid]
        primary_id = fam.get("primary_thread_id")
        survivor = next(
            (m for m in members_here if m.get("thread_id") == primary_id),
            members_here[0],
        )
        row = dict(survivor)
        row["family"] = {
            "family_id": fid,
            "label": fam.get("label", ""),
            "member_count": fam["member_count"],
            "folded_thread_ids": [
                m.get("thread_id", "") for m in members_here if m is not survivor
            ],
        }
        out.append(row)
    return out


def family_max_score(members: list[dict], key: str = "triage_score") -> float:
    """Triage support: a folded family row scores as the MAX member score."""
    return max((float(m.get(key) or 0.0) for m in members), default=0.0)


def coalesce_triaged(threads: list[dict], fold: dict[str, dict]) -> list[dict]:
    """
    triage_threads support: coalesce families, then give each family
    row the MAX member triage_score and a reason suffix ", family of N".
    Re-sorts by (triage_score, timestamp) desc — a folded row may have
    inherited a hotter member's score. Data-gated like coalesce_threads.
    """
    if not fold:
        return threads
    present = _group_present(threads, fold)
    out: list[dict] = []
    for row in coalesce_threads(threads, fold):
        fam = row.get("family")
        if not fam:
            out.append(row)
            continue
        row = dict(row)
        row["triage_score"] = family_max_score(present[fam["family_id"]])
        suffix = f"family of {fam['member_count']}"
        reason = row.get("triage_reason", "")
        row["triage_reason"] = f"{reason}, {suffix}" if reason else suffix
        out.append(row)
    out.sort(key=lambda r: (r.get("triage_score", 0.0), r.get("timestamp", "")), reverse=True)
    return out


# ── link_threads (write path) ──


def _all_threads(memory: ExperientialMemory) -> dict[str, dict]:
    """
    Every thread record by thread_id — RESOLVED INCLUDED (families
    outlive resolution). First occurrence wins on duplicate ids. The
    family ledger never appears here: it lives at the chronicle root,
    outside memory.threads_dir.
    """
    threads: dict[str, dict] = {}
    for jsonl_file in sorted(memory.threads_dir.glob("*.jsonl")):
        for record in _iter_jsonl(jsonl_file):
            tid = record.get("thread_id")
            if isinstance(tid, str) and tid and tid not in threads:
                threads[tid] = record
    return threads


def link_threads(
    thread_ids: list[str],
    label: str,
    primary_thread_id: str | None = None,
    note: str = "",
    by: str = "",
    action: str = "link",
    *,
    memory: ExperientialMemory,
    families_path: str | Path,
) -> dict:
    """
    Link threads into a display-side family, or unlink members out.
    Append-only and reversible; thread files are never touched.

    Semantics (spec section 2):
      - every thread_id must exist in the thread ledger (resolved
        members allowed — families outlive resolution);
      - linking a thread already in a family EXTENDS that family (the
        appended record carries the union of live + new members, and
        may rename / re-prime it);
      - linking threads that span two existing families is refused —
        that is a merge; unlink first (merges stay human-gated);
      - a NEW family needs >=2 threads (a family of one is a thread);
      - unlink appends an "unlink" record for members of one family.

    Returns:
        {family_id, action, label, primary_thread_id, members (live,
        post-fold), member_count, record} — the caller formats the fold
        preview.

    Raises:
        FamilyError: any guard above, unknown thread ids (named), or a
            primary that is not a member.
    """
    if action not in FAMILY_ACTIONS:
        raise FamilyError(f"invalid action {action!r} (valid: {FAMILY_ACTIONS})")

    ids: list[str] = []
    for tid in thread_ids or []:
        tid = (tid or "").strip() if isinstance(tid, str) else ""
        if not tid:
            raise FamilyError("thread_ids must be non-empty strings")
        if tid not in ids:
            ids.append(tid)
    if not ids:
        raise FamilyError("thread_ids must name at least one thread")

    known = _all_threads(memory)
    unknown = [tid for tid in ids if tid not in known]
    if unknown:
        raise FamilyError(
            f"unknown thread id(s): {', '.join(unknown)} — "
            "link_threads only groups threads that exist in the ledger"
        )

    families_path = Path(families_path)
    fold = fold_families(load_families(families_path))
    state = family_state(fold)
    touched = {fold[tid]["family_id"] for tid in ids if tid in fold}

    if action == "link":
        if not (label or "").strip():
            raise FamilyError("label is required when linking")
        if len(touched) > 1:
            raise FamilyError(
                f"threads span {len(touched)} existing families"
                f" ({', '.join(sorted(touched))}) — that is a merge;"
                " unlink first (destructive merges remain human-gated)"
            )
        if touched:
            # Extension: the family keeps its id; the record carries the
            # union of live members + new ones (fold-coherent either way —
            # latest action per thread wins).
            family_id = touched.pop()
            existing = state[family_id]
            members = list(existing["member_thread_ids"])
            members.extend(tid for tid in ids if tid not in members)
            primary = primary_thread_id or existing.get("primary_thread_id")
        else:
            if len(ids) < 2:
                raise FamilyError(
                    "a new family needs at least 2 threads"
                    " (pass a thread already in a family to extend it)"
                )
            family_id = generate_family_id(label)
            members = ids
            primary = primary_thread_id
        if primary is not None and primary not in members:
            raise FamilyError(f"primary_thread_id {primary!r} is not a member of this family")
        record = build_family_record(
            action="link",
            family_id=family_id,
            label=label.strip(),
            member_thread_ids=members,
            primary_thread_id=primary,
            note=note,
            by=by,
        )
    else:  # unlink
        orphans = [tid for tid in ids if tid not in fold]
        if orphans:
            raise FamilyError(f"thread(s) not in any family: {', '.join(orphans)}")
        if len(touched) > 1:
            raise FamilyError(f"threads span {len(touched)} families — unlink one family per call")
        family_id = touched.pop()
        record = build_family_record(
            action="unlink",
            family_id=family_id,
            label=(label or "").strip() or state[family_id].get("label", ""),
            member_thread_ids=ids,
            primary_thread_id=None,
            note=note,
            by=by,
        )

    append_family_record(families_path, record)

    after = family_state(fold_families(load_families(families_path))).get(family_id, {})
    return {
        "family_id": family_id,
        "action": action,
        "label": record["label"],
        "primary_thread_id": after.get("primary_thread_id"),
        "members": after.get("member_thread_ids", []),
        "member_count": after.get("member_count", 0),
        "record": record,
    }


# ── season_review (read-only digest) ──


def _intensity(entry: dict) -> float:
    try:
        return float(entry.get("intensity") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _domain_matches(wanted: str, domain_field: str) -> bool:
    """Comma-tag ELEMENT match — the memory.py get_open_threads convention."""
    return wanted in [tag.strip() for tag in (domain_field or "").split(",")]


def _preview(text: str) -> str:
    text = (text or "").replace("\n", " ")
    return text[:_PREVIEW_CHARS] + ("…" if len(text) > _PREVIEW_CHARS else "")


def _load_threads_readonly(chronicle_root: Path) -> list[dict]:
    """
    Unresolved threads, newest first — WITHOUT ExperientialMemory
    (whose constructor mkdirs; season_review must not change the
    filesystem hash). Mirrors get_open_threads' glob.
    """
    threads: list[dict] = []
    for jsonl_file in sorted((chronicle_root / "open_threads").glob("*.jsonl")):
        for record in _iter_jsonl(jsonl_file):
            if not record.get("resolved", False):
                threads.append(record)
    threads.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    return threads


def _suggest_label(questions: list[str]) -> str:
    """Family label from the cluster's most-shared meaningful tokens."""
    counts: Counter[str] = Counter()
    for question in questions:
        counts.update(
            tok
            for tok in set(_TOKEN_RE.findall((question or "").lower()))
            if tok not in _LABEL_STOPWORDS and len(tok) > 2
        )
    shared = sorted(
        (tok for tok, n in counts.items() if n >= 2),
        key=lambda tok: (-counts[tok], tok),
    )[:3]
    return "-".join(shared) if shared else "thread-family"


def _section(title: str, body: list[str], empty: str) -> list[str]:
    lines = [title]
    lines.extend(body if body else [f"  {empty}"])
    lines.append("")
    return lines


def season_review(
    domain: str | None = None,
    window_days: int = 90,
    max_candidates: int = 10,
    *,
    chronicle_root: str | Path | None = None,
    families_path: str | Path | None = None,
    policies_path: str | Path | None = None,
) -> str:
    """
    READ-ONLY season digest (spec section 2). Five numbered sections:

      1. Supersession candidates — cross-domain token-Jaccard >= 0.5
         AND a legacy text marker (CORRECTED|DEFINITIVE|supersedes) on
         the newer entry; pairs already in the ledger are excluded.
      2. Thread-family candidates — unresolved-question clusters at
         Jaccard >= 0.45 not already co-familied.
      3. Policy candidates — policy-shaped sentinels (intensity >= 0.9,
         normative language) absent from the registry. This IS the
         seeding path; nothing is auto-registered.
      4. Hygiene — dangling supersession pointers, receipt re-verify
         failures, unreceipted >=0.9 ground_truth sentinels, pin-loss
         warnings, sentinel-count-vs-boot-budget.
      5. Dormant domains / fragmentation stats.

    Every candidate line includes a ready-to-paste tool call. The pass
    changes nothing: no ExperientialMemory (its constructor mkdirs), no
    appends, no lazy creation — a filesystem-hash-unchanged test ships
    with it. ``window_days`` scopes the candidate scans (sections 1/3)
    and the dormancy cutoff; threads are deliberately un-windowed
    (split-rot is old by definition); hygiene always covers everything.
    """
    root = Path(chronicle_root) if chronicle_root else Path.home() / ".sovereign" / "chronicle"
    families_path = Path(families_path) if families_path else families_path_for(root)
    policies_path = (
        Path(policies_path) if policies_path else root.parent / "policies" / "policies.jsonl"
    )

    scanned = list(iter_chronicle_entries(root))
    entries = [entry for entry, _file, location in scanned if location == "insights"]
    # ids_present must stay COMPLETE — the hygiene dangling-pointer / receipt
    # claim-ref checks key off it, so filtering protected ids here would make
    # ledger-referenced protected claims look falsely dangling.
    ids_present = {derive_claim_id(entry) for entry, _file, _location in scanned}
    # §5.4: season_review is a model-facing digest that PREVIEWS insight content
    # in its candidate scans (supersession / policy lines render _preview(...)).
    # A preview surface cannot carry the full stakes, so it cannot honor the
    # coupling invariant — it WITHHOLDS by filtering protected records out of
    # the content-based scans entirely (the consent gate is the full-content
    # path; bulk supersession/policy suggestions for a protected record belong
    # there, not here). ids_present above stays complete. Empty fold -> no-op,
    # so a protected-free chronicle reads byte-identical (golden baseline).
    protected_fold = load_protected_fold(root)
    if protected_fold:
        entries = [e for e in entries if derive_claim_id(e) not in protected_fold]
    sup_fold = fold_supersessions(load_supersessions(root / "supersessions.jsonl"))
    fam_fold = fold_families(load_families(families_path))
    threads = _load_threads_readonly(root)
    policies = list(PolicyRegistry(policies_path).fold().values())

    def in_window(entry: dict) -> bool:
        return days_old(entry.get("timestamp")) <= window_days

    def in_domain(field: str) -> bool:
        return domain is None or _domain_matches(domain, field)

    lines = ["🍂 SEASON REVIEW — read-only digest"]
    scope = f"window: {window_days}d · candidates per section: {max_candidates}"
    if domain:
        scope += f' · domain: "{domain}"'
    lines.append(scope)
    lines.append("")

    # ── 1. Supersession candidates ──
    markers = [
        e
        for e in entries
        if has_legacy_marker(e) and in_window(e) and in_domain(e.get("domain", ""))
    ]
    markers.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    seen_pairs: set[frozenset[str]] = set()
    sup_lines: list[str] = []
    for marker in markers:
        marker_id = derive_claim_id(marker)
        for other in entries:
            other_id = derive_claim_id(other)
            if other_id == marker_id:
                continue
            pair = frozenset((marker_id, other_id))
            if pair in seen_pairs:
                continue
            overlap = token_overlap(marker.get("content", ""), other.get("content", ""))
            if overlap < SUPERSESSION_OVERLAP:
                continue
            seen_pairs.add(pair)
            # Older entry is the predecessor; the marker declares the correction.
            pred, succ = sorted((other, marker), key=lambda e: e.get("timestamp", ""))
            pred_id, succ_id = derive_claim_id(pred), derive_claim_id(succ)
            if pred_id in sup_fold:
                continue  # already formalized in the ledger
            cross = " · CROSS-DOMAIN" if pred.get("domain") != succ.get("domain") else ""
            sup_lines.append(
                f"  • overlap {overlap:.2f}{cross}\n"
                f"    {display_id(pred_id)} [{pred.get('domain', '?')}]"
                f" {_preview(pred.get('content', ''))}\n"
                f"    {display_id(succ_id)} [{succ.get('domain', '?')}]"
                f" {_preview(succ.get('content', ''))}\n"
                f'    → supersede_insight(predecessor_id="{display_id(pred_id)}",'
                f' successor_id="{display_id(succ_id)}",'
                ' carry_forward_summary="<what the predecessor still teaches>")'
            )
    overflow = len(sup_lines) - max_candidates
    sup_lines = sup_lines[:max_candidates]
    if overflow > 0:
        sup_lines.append(f"  …and {overflow} more (raise max_candidates to see them).")
    lines += _section(
        "1. SUPERSESSION CANDIDATES (legacy markers, token-Jaccard >= 0.5)",
        sup_lines,
        "none — no marker-bearing entries overlap an unledgered predecessor.",
    )

    # ── 2. Thread-family candidates ──
    eligible = [
        t
        for t in threads
        if t.get("thread_id") and t["thread_id"] not in fam_fold and in_domain(t.get("domain", ""))
    ]
    clustered: set[str] = set()
    fam_lines: list[str] = []
    for i, seed in enumerate(eligible):
        if seed["thread_id"] in clustered:
            continue
        cluster = [seed]
        for other in eligible[i + 1 :]:
            if other["thread_id"] in clustered:
                continue
            if token_overlap(seed.get("question", ""), other.get("question", "")) >= (
                THREAD_OVERLAP
            ):
                cluster.append(other)
        if len(cluster) < 2:
            continue
        clustered.update(t["thread_id"] for t in cluster)
        label = _suggest_label([t.get("question", "") for t in cluster])
        ids_json = ", ".join(f'"{t["thread_id"]}"' for t in cluster)
        fam_lines.append(
            f"  • {len(cluster)} threads cluster (Jaccard >= {THREAD_OVERLAP}):\n"
            + "\n".join(
                f"    - {t['thread_id']}: {_preview(t.get('question', ''))}" for t in cluster
            )
            + f'\n    → link_threads(thread_ids=[{ids_json}], label="{label}")'
        )
    overflow = len(fam_lines) - max_candidates
    fam_lines = fam_lines[:max_candidates]
    if overflow > 0:
        fam_lines.append(f"  …and {overflow} more (raise max_candidates to see them).")
    lines += _section(
        f"2. THREAD-FAMILY CANDIDATES (question clusters, Jaccard >= {THREAD_OVERLAP})",
        fam_lines,
        "none — open threads look atomic (or are already familied).",
    )

    # ── 3. Policy candidates (the seeding path) ──
    pol_lines: list[str] = []
    for entry in entries:
        if _intensity(entry) < SENTINEL_INTENSITY:
            continue
        content = entry.get("content", "")
        if not _POLICY_SHAPE_RE.search(content):
            continue
        if not in_window(entry) or not in_domain(entry.get("domain", "")):
            continue
        if derive_claim_id(entry) in sup_fold:
            continue  # superseded sentinels don't seed policies
        if any(
            token_overlap(content, p.get("statement", "")) >= _POLICY_REGISTRY_OVERLAP
            for p in policies
        ):
            continue  # already registered
        statement = json.dumps(content[:200] + ("…" if len(content) > 200 else ""))
        pol_lines.append(
            f"  • {display_id(derive_claim_id(entry))} [{entry.get('domain', '?')}]"
            f" {_preview(content)}\n"
            f"    → set_policy(statement={statement},"
            f' domain="{entry.get("domain", "general")}", set_by="<approving human>")'
        )
    overflow = len(pol_lines) - max_candidates
    pol_lines = pol_lines[:max_candidates]
    if overflow > 0:
        pol_lines.append(f"  …and {overflow} more (raise max_candidates to see them).")
    lines += _section(
        "3. POLICY CANDIDATES (policy-shaped sentinels absent from the registry)",
        pol_lines,
        "none — no unregistered policy-shaped sentinels in the window.",
    )

    # ── 4. Hygiene ──
    hyg: list[str] = []

    dangling: list[str] = []
    for sid, record in sup_fold.items():
        if sid not in ids_present:
            dangling.append(
                f"  • dangling predecessor {display_id(sid)}"
                f" ({record.get('predecessor_domain') or '?'}:"
                f" {_preview(record.get('predecessor_preview') or '')})"
            )
        successor = record.get("successor_id")
        if successor and successor not in ids_present:
            dangling.append(f"  • dangling successor {display_id(successor)}")
    hyg.extend(dangling or ["  ✓ supersession pointers: all resolve."])

    reverify_failures: list[str] = []
    receipted = 0
    for entry in entries:
        receipts = entry.get("verified_by")
        if not isinstance(receipts, list) or not receipts:
            continue
        receipted += 1
        eid = display_id(derive_claim_id(entry))
        for receipt in receipts:
            if not isinstance(receipt, dict):
                continue
            kind, ref = receipt.get("kind"), str(receipt.get("ref") or "")
            if kind == "archive":
                verdict = verify_archive_ref(ref, root)
            elif kind == "file":
                path = Path(ref).expanduser()
                if not path.is_file():
                    verdict = "missing"
                else:
                    try:
                        recomputed = hashlib.sha256(path.read_bytes()).hexdigest()
                        verdict = "verified" if recomputed == receipt.get("sha256") else "mismatch"
                    except OSError:
                        verdict = "missing"
            elif kind == "claim":
                hits = {cid for cid in ids_present if cid.startswith(ref)}
                verdict = "cites" if len(hits) == 1 else ("ambiguous" if hits else "dangling")
            else:
                verdict = "attested"
            if verdict not in ("verified", "cites", "attested"):
                reverify_failures.append(f"  • {eid} {kind}:{ref} re-verifies as {verdict}")
    hyg.extend(
        reverify_failures
        or [f"  ✓ receipt re-verify: {receipted} receipted entr(ies), no failures."]
    )

    sentinels = [e for e in entries if _intensity(e) >= SENTINEL_INTENSITY]
    # Lived-ground-truth exemption (v1.7.2): a human-authored lived/attested
    # sentinel (vantage in LIVED_VANTAGES) cannot carry an external receipt and
    # is NOT a hygiene gap. Seat-tag / external / absent vantages are still held
    # to the receipt expectation.
    unreceipted = [
        e
        for e in sentinels
        if e.get("layer") == "ground_truth"
        and (e.get("vantage") or "") not in LIVED_VANTAGES
        and not any(
            isinstance(r, dict) and r.get("checked_at_write") == "verified"
            for r in (e.get("verified_by") or [])
        )
    ]
    if unreceipted:
        hyg.append(
            f"  • {len(unreceipted)} unreceipted ground_truth sentinel(s) at >=0.9"
            " (no receipt stamped 'verified' — attestation-only counts as unreceipted;"
            " lived vantages exempt),"
            f" e.g. {display_id(derive_claim_id(unreceipted[0]))}"
        )
    else:
        hyg.append(
            "  ✓ every >=0.9 ground_truth sentinel carries a verified receipt"
            " (or is an exempt lived vantage)."
        )

    entries_by_id = {derive_claim_id(e): e for e in entries}
    pin_losses: list[str] = []
    for sid, record in sup_fold.items():
        if record.get("action") != "supersede":
            continue
        pred = entries_by_id.get(sid)
        if pred is None or _intensity(pred) < SENTINEL_INTENSITY:
            continue
        succ = entries_by_id.get(record.get("successor_id") or "")
        if succ is None or _intensity(succ) < SENTINEL_INTENSITY:
            pin_losses.append(
                f"  • pin loss: sentinel {display_id(sid)} superseded by"
                f" {'a missing entry' if succ is None else display_id(derive_claim_id(succ))}"
                f"{'' if succ is None else f' at intensity {_intensity(succ)}'}"
                " — the successor will not pin at boot"
            )
    hyg.extend(pin_losses or ["  ✓ no pin-loss: every superseded sentinel has a >=0.9 successor."])

    hyg.append(
        f"  • sentinel budget: {len(sentinels)} marker(s) at >=0.9 competing for"
        f" {BOOT_SENTINEL_SLOTS} boot slots"
        + (" — consolidation pressure." if len(sentinels) > BOOT_SENTINEL_SLOTS else ".")
    )
    lines += _section("4. HYGIENE", hyg, "nothing to check — the chronicle is empty.")

    # ── 5. Dormant domains / fragmentation ──
    latest_by_domain: dict[str, str] = {}
    count_by_domain: dict[str, int] = {}
    for entry in entries:
        dom = entry.get("domain", "?")
        count_by_domain[dom] = count_by_domain.get(dom, 0) + 1
        if entry.get("timestamp", "") > latest_by_domain.get(dom, ""):
            latest_by_domain[dom] = entry.get("timestamp", "")
    dormant = sorted(
        ((dom, days_old(ts)) for dom, ts in latest_by_domain.items() if days_old(ts) > window_days),
        key=lambda pair: pair[1],
        reverse=True,
    )
    stats: list[str] = []
    for dom, age in dormant[:max_candidates]:
        stats.append(f"  • dormant: [{dom}] last entry {age}d ago")
    if len(dormant) > max_candidates:
        stats.append(f"  …and {len(dormant) - max_candidates} more dormant domain(s).")
    singletons = sum(1 for n in count_by_domain.values() if n == 1)
    stats.append(
        f"  • fragmentation: {len(entries)} entries across {len(count_by_domain)} domain(s);"
        f" {singletons} single-entry domain(s)."
    )
    stats.append(
        f"  • threads: {len(threads)} open; {len(family_state(fam_fold))} famil(ies)"
        f" covering {len(fam_fold)} thread(s)."
    )
    lines += _section("5. DORMANT DOMAINS / FRAGMENTATION", stats, "the chronicle is empty.")

    lines.append("---")
    lines.append(SEASON_FOOTER)
    return "\n".join(lines)


# ── MCP tool definitions ──

SEASON_TOOLS = [
    Tool(
        name="link_threads",
        description=(
            "Group split-rot threads into a display-side FAMILY (append-only "
            "thread_families.jsonl at the chronicle root; thread files are never touched). "
            "Folded views (get_open_threads, triage_threads, boot) show one row per family "
            "with the members visible. Linking a thread already in a family extends that "
            "family; action='unlink' reverses (append-only, fully reversible). Resolved "
            "threads may be members — families outlive resolution. Threads spanning two "
            "families are refused: merges remain human-gated."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "thread_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Stable thread_ids to link (>=2 for a new family; 1+ extends an "
                        "existing family) or to unlink."
                    ),
                },
                "label": {
                    "type": "string",
                    "description": (
                        "Human label for the family (also seeds the family_id hash). "
                        "Required when linking; optional on unlink."
                    ),
                },
                "primary_thread_id": {
                    "type": "string",
                    "description": (
                        "Member that survives as the visible row in folded views. "
                        "Omit to let the newest visible member stand in."
                    ),
                },
                "note": {"type": "string", "description": "Why these threads belong together."},
                "by": {
                    "type": "string",
                    "description": "Recording-instance id for audit (NOT a session_id).",
                },
                "action": {
                    "type": "string",
                    "enum": ["link", "unlink"],
                    "default": "link",
                    "description": "'link' groups/extends; 'unlink' appends a reversal record.",
                },
            },
            "required": ["thread_ids", "label"],
        },
    ),
    Tool(
        name="season_review",
        description=(
            "READ-ONLY season digest — the chronicle's digestion pass. Five sections: "
            "(1) supersession candidates (legacy CORRECTED/DEFINITIVE/supersedes markers + "
            "cross-domain token overlap >= 0.5), (2) thread-family candidates (question "
            "clusters >= 0.45), (3) policy candidates (policy-shaped >=0.9 sentinels absent "
            "from the registry — the seeding path), (4) hygiene (dangling pointers, receipt "
            "re-verify, unreceipted sentinels, pin-loss, sentinel-vs-boot-budget), "
            "(5) dormant domains / fragmentation. Every candidate line carries a "
            "ready-to-paste tool call. This pass changes nothing — act via "
            "supersede_insight / link_threads / set_policy."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": (
                        "Comma-tag ELEMENT filter for the candidate scans (hygiene and "
                        "stats always cover everything)."
                    ),
                },
                "window_days": {
                    "type": "integer",
                    "default": 90,
                    "description": (
                        "Recency window for candidate scans and the dormancy cutoff. "
                        "Threads are never windowed — split-rot is old by definition."
                    ),
                },
                "max_candidates": {
                    "type": "integer",
                    "default": 10,
                    "description": "Cap per candidate section (overflow is counted, not hidden).",
                },
            },
        },
    ),
]

# my_toolkit registry entries (integrator: merge into server.py's
# TOOL_TIERS / TOOL_INTENTS; category "seasons" in TOOL_CATEGORIES).
SEASON_TOOL_TIERS: dict[str, str] = {
    "link_threads": "core",
    "season_review": "core",
}
SEASON_TOOL_INTENTS: dict[str, str] = {
    "link_threads": "write",
    "season_review": "introspect",
}


# ── MCP dispatcher ──


def _format_link_result(result: dict, families_path: Path) -> str:
    """Render a link_threads result: family_id + fold preview."""
    members = result["members"]
    primary = result.get("primary_thread_id")
    rendered = ", ".join(f"{tid} (primary)" if tid == primary else tid for tid in members)
    verb = "linked" if result["action"] == "link" else "unlinked"
    lines = [f'🧵 Family {verb}: {result["family_id"]} "{result["label"]}"']
    if members:
        folded = max(0, result["member_count"] - 1)
        lines.append(f"  members ({result['member_count']}): {rendered}")
        lines.append(
            f"  fold preview: folded views show 1 row for this family"
            f" ({folded} member(s) fold into the primary)."
        )
    else:
        lines.append("  family is now empty — it no longer folds anything.")
    lines.append(f"  → {families_path}")
    lines.append(
        "Display-side only — thread files untouched; link_threads(action='unlink') reverses."
    )
    return "\n".join(lines)


def handle_season_tool(name: str, arguments: dict, chronicle_root: str | Path | None = None) -> str:
    """
    Dispatch a season tool call. Returns display text — the server wraps
    it in TextContent (same contract as handle_policy_tool).

    link_threads guard failures come back as rejection text, not
    exceptions, so the MCP surface never throws at the caller.
    ``chronicle_root`` defaults to the live ~/.sovereign/chronicle.
    """
    arguments = arguments or {}
    root = Path(chronicle_root) if chronicle_root else Path.home() / ".sovereign" / "chronicle"
    families_path = families_path_for(root)

    if name == "link_threads":
        try:
            result = link_threads(
                thread_ids=arguments.get("thread_ids") or [],
                label=arguments.get("label", ""),
                primary_thread_id=arguments.get("primary_thread_id"),
                note=arguments.get("note", ""),
                by=arguments.get("by", ""),
                action=arguments.get("action", "link"),
                memory=ExperientialMemory(root=str(root)),
                families_path=families_path,
            )
        except ValueError as exc:
            return f"⚠️ link_threads rejected: {exc}"
        return _format_link_result(result, families_path)

    if name == "season_review":
        return season_review(
            domain=(arguments.get("domain") or "").strip() or None,
            window_days=int(arguments.get("window_days", 90)),
            max_candidates=int(arguments.get("max_candidates", 10)),
            chronicle_root=root,
            families_path=families_path,
        )

    return f"Unknown season tool: {name}"
