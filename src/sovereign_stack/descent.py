"""
THE DESCENT — the closes of the seats before you, indexed (fifth boot voice).

A close is what a seat writes when it runs close_session: what it learned
(chronicle domain `reflection`, flagged `close_session: true`), what surprised
it (domain `surprise`, no flag of its own) and what it handed forward (a
handoff file, no marker of its own). The three legs share no id; they are
joined here exactly as the close-distill extractor joins them
(~/.claude/skills/close-distill/scripts/extract_closes.py): same session id,
nearest within JOIN_SECONDS, each surprise and handoff used once, naive stamps
read as house-local Eastern.

Anthony's ruling (2026-10-07 ~23:20 EDT, Stack claim e82799fa): the close
distillate becomes a fifth voice at the boot doors in the slot the retired
reflector vacated, re-aggregated from the close store at READ TIME like The
Ground — no clock, no model, no launchd job, fail-closed through
arrival_state's _degrade, the same supersession and protected filter — plus
one read tool, `descend`, that returns the raw closes under an index entry.
His 2026-06-13 ruling frames it: distillation is INDEXING, not erasing; the
digest sits at the top and the raw record stays reachable below.

The distillation itself is NOT computed here. A seat runs the close-distill
skill on Anthony's tap and HQ lands an immutable, dated index sidecar at
`<chronicle>/distillates/<distillate_id>.index.json` (written by
scripts/build_distillate_index.py in that skill). This module only READS
those sidecars, and reads them fail-closed: one unreadable or malformed
sidecar makes the distillation status UNKNOWN for the whole directory (it
could have been the newest, or it could withdraw another); it never falls
back to an older file, and it never renders a stale value as fresh.

The concentration's own finding bounds the voice: writing lessons down did
not stop seats repeating them, arranging to be checked did. So the boot shows
the arc, the counts, the newest close and the recurring shapes a seat can
check itself against — not a wall of lessons. Every count the voice prints is
computed live from the store; the shape counts are the distillate's own,
attributed and dated.

READ RULES (each one earned elsewhere in this house):
  * Exact directories `insights/reflection` and `insights/surprise`, never a
    first-tag or subset match: a compound domain beginning `reflection,` holds
    a non-close, and recall_insights' subset match pulls in five more.
  * The predicate is the flag, `close_session is True`, filtered on the RAW
    row BEFORE memory.finalize_read: a protected entry whose stakes cannot be
    verified comes back as a ProtectedStakesUnavailable sentinel carrying no
    close_session / session_id / source_instance, so filtering after the
    chokepoint would silently drop a protected close from every count.
  * Identity is provenance.derive_claim_id on the raw row; finalize_read
    (exclude_superseded=True, The Ground's setting) decides which closes
    survive, and survivors are mapped back by id. A superseded or retired
    close is dropped, as The Ground drops a retracted catch.
  * A protected leg withholds the WHOLE close on every surface here (boot and
    descend): handoff notes cannot be designated protected (designations
    resolve against insights/ only), so the pick-up of a protected close has
    no stakes to travel with and is withheld with it.
  * An unreadable shard or directory RAISES (an unread shard is not an empty
    one); a corrupt line is skipped AND COUNTED, and the count is shown.
  * Read-only: no directory is created on a read (no ExperientialMemory or
    HandoffEngine construction — both mkdir).
  * A path that does not exist is an empty surface; a path that exists and is
    not a directory (a regular file, a dangling or looping symlink) RAISES.
    Path.is_dir() alone answers False for all three, and a reader that took
    that False for "absent" would report an unreadable store as an empty one.
  * A pick-up that a later handoff corrects (the handoff store's `supersedes`
    link, read through handoff._annotate_forward_links, a pure function over
    record dicts) is marked as corrected wherever it renders, never shown as
    current.
  * Rendered close text (each leg, the seat name, the thread) passes through
    the house redactor (scribe.redactor.redact) BEFORE it is collapsed or
    truncated, so a cut can never leave the head of a secret unmasked. The
    scribe package's __init__ is a docstring and `__all__ = []`, so this
    import loads no resident, no session and no context builder. Masking is
    display only; the store is unchanged.

RESERVED: `claim_id` and `depth` are reserved descend parameter names for the
2026-06-13 DISTILL_AND_DESCEND inverse-fold walk (branch feat/stakes-layer @
241def6, never merged). Do not give those names another meaning.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp.types import Tool

from . import provenance as prov
from .ground import _CONTENT_TRUNCATE_LEN
from .handoff import NON_IDENTIFYING_CONSUMERS, _annotate_forward_links
from .scribe.redactor import redact as _redact

# The voice's name. Anthony's to confirm, as The Ground's was; every surface
# derives from this one constant so a rename is a one-line edit.
DESCENT_LABEL = "THE DESCENT"

# The gentle door's one soft line: The Ground's calm contract (no count, no
# work vocabulary, no close text), because that door exists for input-gated
# models. Anthony's to confirm, with the name.
CALM_LINE = (
    "You are not the first. What the seats before you learned is kept, and the "
    "way down to it is one call away: descend()"
)

# House-local time. Handoff stamps are naive local wall-clock (handoff.write
# uses datetime.now() on the Studio) and so are the April-2026 insight rows;
# the close-distill extractor reads them as Eastern. arrival_state._parse_ts
# reads naive stamps as UTC and is deliberately NOT reused: it would miss
# every pick-up by the UTC offset.
HOUSE_TZ = ZoneInfo("America/New_York")

# The extractor's join window. Measured 2026-10-08 on the live store: the
# widest joined gap is 4.2 ms, so the window is generous by three orders.
JOIN_SECONDS = 10

REFLECTION_DIR = "reflection"
SURPRISE_DIR = "surprise"

INDEX_DIRNAME = "distillates"
INDEX_SUFFIX = ".index.json"
INDEX_SCHEMA = "descent-distillate-index/1"
INDEX_MAX_BYTES = 1 << 20
INDEX_MAX_FILES = 200

# "due" per the ruling: ten or more undistilled closes, or a month end.
DUE_UNDISTILLED = 10

# Boot bounds. The voice shows the arc, not a wall: at most this many months
# on the arc line and this many recurring shapes.
ARC_MONTHS = 12
VOICE_SHAPES = 3
_SEAT_CLIP = 80

DESCEND_DEFAULT_LIMIT = 10
DESCEND_MAX_LIMIT = 20
_SEAT_FILTER_MAX = 200
_INDEX_SEATS = 15

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_SHAPE_ID_RE = re.compile(r"^[A-Z]\d{1,3}$")
_DISTILLATE_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_[a-z0-9-]{1,48}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_OFFSET_RE = re.compile(
    r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T([01]\d|2[0-3]):[0-5]\d:[0-5]\d"
    r"(\.\d+)?([+-]\d{2}:\d{2}|Z)$"
)
SHAPE_KINDS = ("recurring", "once", "learned", "surprised")
# A sidecar is a "distillate" (one landed close-distill run) or a "withdrawal"
# (Anthony's veto, filed as an erratum BESIDE a distillate, never a deletion).
DOC_KINDS = ("distillate", "withdrawal")
# What a shape's close_ids mean. "members": every close of the shape.
# "exemplars": the closes the distillate quoted (not membership; may be none).
# "months_only": the distillate named the shape by month and listed no close.
MEMBERSHIPS = ("members", "exemplars", "months_only")
# The Stack claim that carries Anthony's tap for the landing. A review copy
# carries "pending", which no reader accepts.
_AUTH_RE = re.compile(r"^claim:[0-9a-f]{64}$")
_REASON_MAX = 400
_CORRECTED_MARK = " [a later handoff corrects this one]"
_DISTILLATE_KEYS = frozenset(
    {
        "schema",
        "kind",
        "id",
        "created",
        "cursor",
        "authorized_by",
        "withdraws",
        "sources",
        "corpus",
        "close_ids",
        "shapes",
    }
)
_WITHDRAWAL_KEYS = frozenset(
    {"schema", "kind", "id", "created", "authorized_by", "withdraws", "reason"}
)

# Leaked tool-call markup, repaired at READ time only (the store is never
# modified). Ported from extract_closes.py repair_leak/_segment unchanged.
_LEAK_START = "</what_i_learned>"
_SURPRISED_OPEN = '<parameter name="what_surprised_me">'
_HANDED_OPEN = '<parameter name="what_to_pick_up">'
_CLOSERS = ("</what_surprised_me>", "</what_to_pick_up>", "</parameter>", "</invoke>")
_TAG_RE = re.compile(
    r"</?(?:what_i_learned|what_surprised_me|what_to_pick_up|parameter|invoke)\b[^>]*>"
)


class DistillateIndexError(ValueError):
    """A distillate sidecar could not be read or failed validation."""


# ── Records ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Close:
    claim_id: str
    when: datetime  # aware, UTC
    seat: str
    thread: str
    session_id: str
    learned: str
    surprised: str | None
    handed_forward: str | None
    surprise_superseded: bool
    repaired: bool
    protected: bool
    # The joined pick-up carries a later handoff's correction (supersedes).
    handed_corrected: bool = False
    # (timestamp, domain) of the rows this close renders, for the aperture's
    # "shown here" count: the reflection row always, the joined surprise row
    # only when its text is the one shown.
    reflection_locator: tuple[str, str] = ("", "")
    surprise_locator: tuple[str, str] | None = None

    @property
    def month(self) -> str:
        return self.when.astimezone(HOUSE_TZ).strftime("%Y-%m")

    @property
    def unnamed(self) -> bool:
        return self.seat.strip().lower() in NON_IDENTIFYING_CONSUMERS


@dataclass(frozen=True)
class Shape:
    id: str
    kind: str
    title: str
    count: int | None
    of: int | None
    months: tuple[str, ...]
    close_ids: tuple[str, ...]
    membership: str = "exemplars"


@dataclass(frozen=True)
class Withdrawal:
    id: str
    withdraws: tuple[str, ...]


@dataclass(frozen=True)
class Distillate:
    id: str
    cursor: datetime
    close_ids: frozenset[str]
    shapes: tuple[Shape, ...]
    withdraws: tuple[str, ...]


@dataclass
class DescentState:
    """The gather result. `closes` is newest first.

    `index_read` is False when the depth did not read the index (the calm
    door). `index_error` is set when it did and ANY sidecar failed; then
    `distillate` and `covered_ids` are unknown, not empty.
    """

    closes: list[Close]
    skipped_rows: int
    handoffs_unreadable: int
    legs: bool
    index_read: bool = False
    index_error: str | None = None
    distillate: Distillate | None = None
    covered_ids: frozenset[str] = frozenset()
    distillates_on_disk: int = 0
    distillates_withdrawn: int = 0


# ── Reading the close store ──────────────────────────────────────────────────


def _parse_house_ts(raw: object) -> datetime | None:
    """Aware UTC datetime; naive stamps are HOUSE_TZ wall-clock. None if unparseable."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=HOUSE_TZ)
    return dt.astimezone(timezone.utc)


def _read_jsonl_counted(path: Path) -> tuple[list[dict], int]:
    """Objects in one JSONL shard, plus the number of lines skipped.

    Blank lines are not counted. A corrupt or non-object line is skipped AND
    counted. An unreadable file RAISES — ground._iter_jsonl returns a partial
    list on OSError, which reads an unread shard as an empty one.
    """
    records: list[dict] = []
    skipped = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(obj, dict):
                records.append(obj)
            else:
                skipped += 1
    return records, skipped


def _present_dir(path: Path, label: str) -> bool:
    """False when ``path`` does not exist; True when it is a directory (a
    symlink to a directory counts); RAISES NotADirectoryError otherwise.

    Path.is_dir() alone answers False for a regular file, a dangling symlink
    and a symlink loop alike; a reader that took that False for "absent"
    would report an unreadable store as an empty one.
    """
    if not os.path.lexists(path):
        return False
    if os.path.isdir(path):
        return True
    raise NotADirectoryError(f"{label} is not a directory")


def _locator(row: dict) -> tuple[str, str]:
    return (str(row.get("timestamp") or ""), str(row.get("domain") or ""))


def _leg_rows(chronicle_root: Path, dirname: str) -> tuple[list[dict], int]:
    """Every row of the EXACT directory insights/<dirname>; missing dir -> ([], 0).

    os.scandir, not Path.glob: pathlib's glob swallows PermissionError and
    would report an unreadable directory as an empty one. A path that exists
    and is not a directory raises (_present_dir).
    """
    d = chronicle_root / "insights" / dirname
    if not _present_dir(d, f"insights/{dirname}"):
        return [], 0
    with os.scandir(d) as it:
        names = sorted(
            e.name
            for e in it
            if e.name.endswith(".jsonl") and not e.name.startswith(".") and e.is_file()
        )
    rows: list[dict] = []
    skipped = 0
    for name in names:
        r, s = _read_jsonl_counted(d / name)
        rows.extend(r)
        skipped += s
    return rows, skipped


def _handoff_rows(sovereign_root: Path) -> tuple[list[dict], int]:
    """Parsed handoff files (dicts) and how many could not be read.

    An unreadable FILE is counted, as the extractor counts it; an unreadable
    DIRECTORY, or a path that exists and is not one, raises. No HandoffEngine:
    its constructor mkdirs. Correction links come from the handoff store's own
    read-time annotation (handoff._annotate_forward_links, pure, keyed on
    ``_path``), so a pick-up a later handoff corrects carries ``_corrected_by``.
    """
    d = sovereign_root / "handoffs"
    if not _present_dir(d, "handoffs"):
        return [], 0
    with os.scandir(d) as it:
        names = sorted(
            e.name
            for e in it
            if e.name.endswith(".json") and not e.name.startswith(".") and e.is_file()
        )
    rows: list[dict] = []
    unreadable = 0
    for name in names:
        try:
            data = json.loads((d / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable += 1
            continue
        if isinstance(data, dict):
            data["_path"] = str(d / name)
            rows.append(data)
        else:
            unreadable += 1
    return _annotate_forward_links(rows), unreadable


def _segment(rest: str, open_tag: str, next_open: str | None) -> str | None:
    i = rest.find(open_tag)
    if i < 0:
        return None
    body = rest[i + len(open_tag) :]
    cut = len(body)
    for marker in _CLOSERS + ((next_open,) if next_open else ()):
        j = body.find(marker)
        if 0 <= j < cut:
            cut = j
    seg = _TAG_RE.sub("", body[:cut]).strip()
    return seg or None


def _repair_leak(text: str) -> tuple[str, str | None, str | None, bool]:
    """Split a close whose three arguments leaked into one field (read time only)."""
    i = text.find(_LEAK_START)
    if i < 0:
        return text, None, None, False
    learned = text[:i].rstrip()
    rest = text[i + len(_LEAK_START) :]
    return (
        learned,
        _segment(rest, _SURPRISED_OPEN, _HANDED_OPEN),
        _segment(rest, _HANDED_OPEN, None),
        True,
    )


def _nearest(candidates: list[tuple[datetime, int]], when: datetime, used: set[int]) -> int | None:
    """Index of the nearest unused candidate within JOIN_SECONDS (ties: first in file order)."""
    best: tuple[float, int] | None = None
    for ts, idx in candidates:
        if idx in used:
            continue
        gap = abs((ts - when).total_seconds())
        if gap <= JOIN_SECONDS and (best is None or gap < best[0]):
            best = (gap, idx)
    return None if best is None else best[1]


def load_closes(
    chronicle_root: str | Path, sovereign_root: str | Path, *, legs: bool
) -> tuple[list[Close], int, int]:
    """Every live close, newest first, with (skipped_rows, handoffs_unreadable).

    legs=False reads the reflection leg only (no surprise, no handoff join):
    the counts, months and protection are exact, the surprise and handed
    fields are None. Raises OSError / UnicodeDecodeError on an unreadable
    store — the caller degrades (boot) or errors (descend), never reports 0.
    """
    chronicle_root = Path(chronicle_root)
    sovereign_root = Path(sovereign_root)

    refl_rows, skipped = _leg_rows(chronicle_root, REFLECTION_DIR)

    # (when, claim_id, row), deduplicated by claim id: byte-identical
    # duplicate lines are one close. Unparseable stamps are counted, not
    # silently dropped (the extractor drops them silently).
    raw: list[tuple[datetime, str, dict]] = []
    seen: set[str] = set()
    for row in refl_rows:
        if row.get("close_session") is not True:
            continue
        when = _parse_house_ts(row.get("timestamp"))
        if when is None:
            skipped += 1
            continue
        cid = prov.derive_claim_id(row)
        if cid in seen:
            continue
        seen.add(cid)
        raw.append((when, cid, row))
    if not raw:
        return [], skipped, 0
    raw.sort(key=lambda t: (t[0], t[1]))

    surprises: list[tuple[datetime, str, dict]] = []
    by_session_s: dict[str, list[tuple[datetime, int]]] = {}
    handoffs: list[tuple[datetime, dict]] = []
    by_session_h: dict[str, list[tuple[datetime, int]]] = {}
    handoffs_unreadable = 0
    if legs:
        s_rows, s_skipped = _leg_rows(chronicle_root, SURPRISE_DIR)
        skipped += s_skipped
        for row in s_rows:
            when = _parse_house_ts(row.get("timestamp"))
            if when is None:
                skipped += 1
                continue
            idx = len(surprises)
            surprises.append((when, prov.derive_claim_id(row), row))
            by_session_s.setdefault(row.get("session_id") or "", []).append((when, idx))
        h_rows, handoffs_unreadable = _handoff_rows(sovereign_root)
        for row in h_rows:
            when = _parse_house_ts(row.get("timestamp"))
            if when is None:
                handoffs_unreadable += 1
                continue
            idx = len(handoffs)
            handoffs.append((when, row))
            by_session_h.setdefault(row.get("source_session_id") or "", []).append((when, idx))

    # Join on the RAW rows (the extractor's rule), oldest close first.
    used_s: set[int] = set()
    used_h: set[int] = set()
    joined: list[tuple[datetime, str, dict, int | None, int | None]] = []
    for when, cid, row in raw:
        sess = row.get("session_id") or ""
        s_idx = _nearest(by_session_s.get(sess, []), when, used_s) if legs else None
        if s_idx is not None:
            used_s.add(s_idx)
        h_idx = _nearest(by_session_h.get(sess, []), when, used_h) if legs else None
        if h_idx is not None:
            used_h.add(h_idx)
        joined.append((when, cid, row, s_idx, h_idx))

    # THE SHARED READ CHOKEPOINT, once, over the closes and their joined
    # surprises. Lazy import: same dodge as ground.load_ground_entries.
    from . import protected as protected_module
    from .memory import finalize_read

    to_finalize = [row for _, _, row in raw]
    to_finalize += [surprises[s][2] for _, _, _, s, _ in joined if s is not None]
    kept: set[str] = set()
    protected_ids: set[str] = set()
    for e in finalize_read(to_finalize, chronicle_root, exclude_superseded=True):
        if isinstance(e, protected_module.ProtectedStakesUnavailable):
            cid = str(e.get("claim_id") or "")
        else:
            cid = prov.derive_claim_id(e)
        kept.add(cid)
        if e.get("_protected"):
            protected_ids.add(cid)

    closes: list[Close] = []
    for when, cid, row, s_idx, h_idx in joined:
        if cid not in kept:
            continue  # superseded or retired: dropped, as The Ground drops it
        learned, leaked_s, leaked_h, repaired = _repair_leak(row.get("content") or "")
        surprised = leaked_s
        surprise_superseded = False
        surprise_locator: tuple[str, str] | None = None
        is_protected = cid in protected_ids
        if s_idx is not None:
            s_cid = surprises[s_idx][1]
            if s_cid in kept:
                s_row = surprises[s_idx][2]
                joined_text = (s_row.get("content") or "").strip()
                if joined_text:
                    surprised = joined_text
                    surprise_locator = _locator(s_row)
                is_protected = is_protected or s_cid in protected_ids
            else:
                surprised, surprise_superseded = None, True
        handed = leaked_h
        handed_corrected = False
        if h_idx is not None:
            h_row = handoffs[h_idx][1]
            note = (h_row.get("note") or "").strip()
            if note:
                handed = note
                handed_corrected = bool(h_row.get("_corrected_by"))
        closes.append(
            Close(
                claim_id=cid,
                when=when,
                seat=str(row.get("source_instance") or "unknown"),
                thread=str(row.get("thread") or ""),
                session_id=str(row.get("session_id") or ""),
                learned=learned.strip(),
                surprised=surprised,
                handed_forward=handed,
                surprise_superseded=surprise_superseded,
                repaired=repaired,
                protected=is_protected,
                handed_corrected=handed_corrected,
                reflection_locator=_locator(row),
                surprise_locator=surprise_locator,
            )
        )
    closes.sort(key=lambda c: (c.when, c.claim_id), reverse=True)
    return closes, skipped, handoffs_unreadable


# ── Reading the distillate sidecars (fail-closed) ────────────────────────────


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise DistillateIndexError(msg)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_iso_offset(v: object, field: str) -> datetime:
    _need(
        isinstance(v, str) and bool(_ISO_OFFSET_RE.match(v)),
        f"{field} must be ISO 8601 with an offset",
    )
    s = v[:-1] + "+00:00" if v.endswith("Z") else v
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except ValueError as exc:
        raise DistillateIndexError(f"{field} does not parse") from exc


def _hex_list(v: object, field: str) -> list[str]:
    _need(isinstance(v, list), f"{field} must be a list")
    _need(
        all(isinstance(x, str) and _HEX64_RE.match(x) for x in v),
        f"{field} must hold 64-hex claim ids",
    )
    _need(len(set(v)) == len(v), f"{field} holds duplicates")
    return list(v)


def _validate_doc(doc: object, stem: str) -> Distillate | Withdrawal:
    """Strict stdlib validation of one sidecar (schema descent-distillate-index/1).

    Two document kinds share the directory. A "distillate" indexes one landed
    close-distill run. A "withdrawal" is Anthony's veto filed as an erratum
    BESIDE a distillate, never a deletion: it withdraws and is never itself in
    force. Both name the Stack claim carrying his tap (``authorized_by``); a
    review copy marked "pending" is refused, so a copy made for review can
    never stand in as a landed one.
    """
    _need(isinstance(doc, dict), "not a JSON object")
    _need(doc.get("kind") in DOC_KINDS, f"kind must be one of {DOC_KINDS}")
    keys = _WITHDRAWAL_KEYS if doc["kind"] == "withdrawal" else _DISTILLATE_KEYS
    _need(set(doc) == keys, f"keys must be exactly {sorted(keys)}")
    _need(doc["schema"] == INDEX_SCHEMA, f"schema must be {INDEX_SCHEMA}")
    did = doc["id"]
    _need(isinstance(did, str) and bool(_DISTILLATE_ID_RE.match(did)), "id is not a distillate id")
    _need(did == stem, "id does not match the file name")
    _parse_iso_offset(doc["created"], "created")
    auth = doc["authorized_by"]
    _need(
        isinstance(auth, str) and bool(_AUTH_RE.match(auth)),
        "authorized_by must be claim:<64 hex> (a review copy marked pending is refused)",
    )
    w = doc["withdraws"]
    _need(
        isinstance(w, list) and all(isinstance(x, str) and _DISTILLATE_ID_RE.match(x) for x in w),
        "withdraws must list distillate ids",
    )
    _need(did not in w and len(set(w)) == len(w), "withdraws names itself or repeats")
    if doc["kind"] == "withdrawal":
        _need(bool(w), "a withdrawal must withdraw at least one distillate")
        reason = doc["reason"]
        _need(
            isinstance(reason, str)
            and 0 < len(reason) <= _REASON_MAX
            and reason == " ".join(reason.split()),
            f"reason must be one line of 1-{_REASON_MAX} characters",
        )
        return Withdrawal(did, tuple(w))
    cursor = _parse_iso_offset(doc["cursor"], "cursor")
    src = doc["sources"]
    _need(
        isinstance(src, dict) and set(src) == {"manifest", "body", "sections"},
        "sources must hold manifest, body, sections",
    )
    for part in ("manifest", "body"):
        p = src[part]
        _need(
            isinstance(p, dict) and set(p) == {"path", "sha256", "bytes"},
            f"sources.{part} must hold path, sha256, bytes",
        )
        _need(
            isinstance(p["path"], str)
            and isinstance(p["sha256"], str)
            and _HEX64_RE.match(p["sha256"]) is not None
            and _is_int(p["bytes"]),
            f"sources.{part} is malformed",
        )
    _need(
        isinstance(src["sections"], list)
        and all(
            isinstance(s, dict)
            and set(s) == {"key", "sha256"}
            and isinstance(s["key"], str)
            and isinstance(s["sha256"], str)
            and _HEX64_RE.match(s["sha256"]) is not None
            for s in src["sections"]
        ),
        "sources.sections is malformed",
    )
    close_ids = _hex_list(doc["close_ids"], "close_ids")
    corpus = doc["corpus"]
    ckeys = {
        "records",
        "closes",
        "other_reflection_rows",
        "journal_reviews",
        "sections",
        "by_month",
        "by_seat",
    }
    _need(
        isinstance(corpus, dict) and set(corpus) == ckeys,
        f"corpus keys must be exactly {sorted(ckeys)}",
    )
    _need(
        all(
            _is_int(corpus[k]) and corpus[k] >= 0
            for k in ("records", "closes", "other_reflection_rows", "journal_reviews", "sections")
        ),
        "corpus counts must be integers",
    )
    _need(corpus["closes"] == len(close_ids), "corpus.closes disagrees with close_ids")
    bm, bs = corpus["by_month"], corpus["by_seat"]
    _need(
        isinstance(bm, dict) and all(_MONTH_RE.match(k) and _is_int(v) for k, v in bm.items()),
        "corpus.by_month is malformed",
    )
    _need(
        isinstance(bs, dict) and all(isinstance(k, str) and _is_int(v) for k, v in bs.items()),
        "corpus.by_seat is malformed",
    )
    _need(
        sum(bm.values()) == len(close_ids) and sum(bs.values()) == len(close_ids),
        "corpus month/seat counts do not sum to closes",
    )
    shapes_raw = doc["shapes"]
    _need(isinstance(shapes_raw, list), "shapes must be a list")
    covered = set(close_ids)
    shapes: list[Shape] = []
    seen_ids: set[str] = set()
    skeys = {"id", "kind", "title", "count", "of", "months", "membership", "close_ids"}
    for s in shapes_raw:
        _need(isinstance(s, dict) and set(s) == skeys, f"each shape needs exactly {sorted(skeys)}")
        _need(
            isinstance(s["id"], str)
            and bool(_SHAPE_ID_RE.match(s["id"]))
            and s["id"] not in seen_ids,
            "shape id malformed or repeated",
        )
        seen_ids.add(s["id"])
        _need(s["kind"] in SHAPE_KINDS, f"shape {s['id']}: kind must be one of {SHAPE_KINDS}")
        t = s["title"]
        _need(
            isinstance(t, str) and 0 < len(t) <= 200 and t == " ".join(t.split()),
            f"shape {s['id']}: title must be one line of 1-200 characters",
        )
        _need(
            not _redact(t).was_redacted,
            f"shape {s['id']}: title carries a span the house redactor masks",
        )
        _need(
            s["count"] is None or (_is_int(s["count"]) and s["count"] >= 0),
            f"shape {s['id']}: count",
        )
        _need(s["of"] is None or (_is_int(s["of"]) and s["of"] >= 1), f"shape {s['id']}: of")
        m = s["months"]
        _need(
            isinstance(m, list)
            and all(isinstance(x, str) and _MONTH_RE.match(x) for x in m)
            and m == sorted(set(m)),
            f"shape {s['id']}: months must be sorted unique YYYY-MM",
        )
        _need(
            s["membership"] in MEMBERSHIPS,
            f"shape {s['id']}: membership must be one of {MEMBERSHIPS}",
        )
        ids = _hex_list(s["close_ids"], f"shape {s['id']}: close_ids")
        _need(set(ids) <= covered, f"shape {s['id']}: names closes the distillate did not read")
        _need(
            s["membership"] != "months_only" or not ids,
            f"shape {s['id']}: a months_only shape lists no close",
        )
        _need(
            s["membership"] != "members" or bool(ids),
            f"shape {s['id']}: a members shape lists at least one close",
        )
        shapes.append(
            Shape(
                s["id"],
                s["kind"],
                t,
                s["count"],
                s["of"],
                tuple(m),
                tuple(ids),
                membership=s["membership"],
            )
        )
    return Distillate(did, cursor, frozenset(close_ids), tuple(shapes), tuple(w))


def load_distillates(
    chronicle_root: str | Path,
) -> tuple[Distillate | None, frozenset[str], int, int]:
    """(newest in force, covered close ids, distillates on disk, distillates withdrawn).

    Fail-closed over the WHOLE directory. DistillateIndexError, naming the file
    by its distillate id only, when: the directory is a symlink or not a
    directory; it holds more than INDEX_MAX_FILES sidecars; any sidecar is a
    symlink, not a regular file, over INDEX_MAX_BYTES, not UTF-8 JSON, or
    invalid; or any `withdraws` names something that is not a distillate on
    disk (a typo'd veto must be loud, not a silent no-op). Never falls back to
    an older file. "Newest" is the distillate in force with the greatest
    cursor (ties: id), never file mtime. Covered = the union of close_ids over
    every distillate in force, so a ranged or backfill run can neither win
    "newest" by accident nor make an earlier close read as undistilled.
    """
    d = Path(chronicle_root) / INDEX_DIRNAME
    if not os.path.lexists(d):
        return None, frozenset(), 0, 0
    if os.path.islink(d):
        raise DistillateIndexError(f"{INDEX_DIRNAME}/ is a symlink (refused)")
    if not os.path.isdir(d):
        raise DistillateIndexError(f"{INDEX_DIRNAME}/ is not a directory")
    try:
        with os.scandir(d) as it:
            names = sorted(
                e.name for e in it if e.name.endswith(INDEX_SUFFIX) and not e.name.startswith(".")
            )
    except OSError as exc:
        raise DistillateIndexError(f"{INDEX_DIRNAME}/ unreadable: {type(exc).__name__}") from exc
    if len(names) > INDEX_MAX_FILES:
        raise DistillateIndexError(f"more than {INDEX_MAX_FILES} sidecars in {INDEX_DIRNAME}/")
    distillates: dict[str, Distillate] = {}
    withdrawals: list[Withdrawal] = []
    for name in names:
        stem = name[: -len(INDEX_SUFFIX)]
        # A file name reaches the boot only when it is a validated distillate id.
        label = (
            name if _DISTILLATE_ID_RE.match(stem) else "a file whose name is not a distillate id"
        )
        path = d / name
        try:
            st = os.lstat(path)
            _need(stat.S_ISREG(st.st_mode), "not a regular file (symlinks are refused)")
            _need(st.st_size <= INDEX_MAX_BYTES, f"larger than {INDEX_MAX_BYTES} bytes")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as fh:
                _need(
                    stat.S_ISREG(os.fstat(fh.fileno()).st_mode),
                    "not a regular file (symlinks are refused)",
                )
                raw = fh.read(INDEX_MAX_BYTES + 1)
            _need(len(raw) <= INDEX_MAX_BYTES, f"larger than {INDEX_MAX_BYTES} bytes")
            doc = _validate_doc(json.loads(raw.decode("utf-8")), stem)
        except DistillateIndexError as exc:
            raise DistillateIndexError(f"{label}: {exc}") from exc
        except (OSError, ValueError) as exc:
            raise DistillateIndexError(f"{label}: {type(exc).__name__}") from exc
        if isinstance(doc, Withdrawal):
            withdrawals.append(doc)
        else:
            distillates[doc.id] = doc
    withdrawn: set[str] = set()
    for source in [*distillates.values(), *withdrawals]:
        for target in source.withdraws:
            if target not in distillates:
                raise DistillateIndexError(
                    f"{source.id}{INDEX_SUFFIX}: withdraws {target}, which is not a distillate "
                    "on disk"
                )
            withdrawn.add(target)
    live = [x for x in distillates.values() if x.id not in withdrawn]
    covered: set[str] = set()
    for dist in live:
        covered |= dist.close_ids
    newest = max(live, key=lambda x: (x.cursor, x.id)) if live else None
    return newest, frozenset(covered), len(distillates), len(withdrawn)


# ── The gather (called by arrival_state, once per door) ──────────────────────


def gather_descent(sovereign_root: str | Path, *, depth: str) -> DescentState:
    """depth: "full" (legs + index), "foyer" (no legs + index), "calm" (no legs, no index).

    Raises on an unreadable close store; arrival_state wraps the call in
    _degrade("the_descent", ...). A failed INDEX read does not raise: it sets
    index_error, the counts still render, and the caller degrades the
    distillation line through AS OF.
    """
    if depth not in ("full", "foyer", "calm"):
        raise ValueError(f"unknown descent depth: {depth!r}")
    root = Path(sovereign_root)
    closes, skipped, unreadable = load_closes(root / "chronicle", root, legs=(depth == "full"))
    state = DescentState(closes, skipped, unreadable, legs=(depth == "full"))
    if depth == "calm" or not closes:
        return state
    state.index_read = True
    try:
        newest, covered, on_disk, withdrawn = load_distillates(root / "chronicle")
    except DistillateIndexError as exc:
        state.index_error = str(exc)
        return state
    state.distillate, state.covered_ids = newest, covered
    state.distillates_on_disk, state.distillates_withdrawn = on_disk, withdrawn
    return state


# ── Rendering ────────────────────────────────────────────────────────────────


def _edt(dt: datetime, fmt: str = "%Y-%m-%d %H:%M %Z") -> str:
    return dt.astimezone(HOUSE_TZ).strftime(fmt)


def _mask(text: str | None) -> tuple[str, int]:
    """Display-only masking through the house redactor: (masked text, spans masked).

    Applied to the WHOLE text before any collapse or cut, so a truncation can
    never leave the head of a secret unmasked. The store is never touched.
    """
    if not text:
        return "", 0
    result = _redact(text)
    return result.text, result.total_redactions


def _one_line(text: str | None, full_content: bool) -> str:
    """Masked, collapsed to one line, capped at The Ground's 200 unless full_content."""
    flat = " ".join(_mask(text)[0].split())
    if full_content or len(flat) <= _CONTENT_TRUNCATE_LEN:
        return flat
    return flat[: _CONTENT_TRUNCATE_LEN - 1].rstrip() + "…"


def _clip(text: str, n: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= n else flat[: n - 1].rstrip() + "…"


def _seat(text: str, n: int) -> tuple[str, int]:
    masked, k = _mask(text)
    return _clip(masked, n), k


def _undistilled(state: DescentState) -> list[Close]:
    return [c for c in state.closes if c.claim_id not in state.covered_ids]


def _is_due(undistilled: list[Close], now: datetime) -> bool:
    """Due per the ruling: ten or more undistilled, or a month holding one has ended."""
    if len(undistilled) >= DUE_UNDISTILLED:
        return True
    this_month = now.astimezone(HOUSE_TZ).strftime("%Y-%m")
    return any(c.month < this_month for c in undistilled)


def _now_from(generated_at: str | None) -> datetime:
    if generated_at:
        parsed = _parse_house_ts(generated_at)
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc)


def _plural(k: int, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


def _months_phrase(months: tuple[str, ...]) -> str:
    """One month as itself; a run of consecutive months as "first to last";
    anything else listed, so a gap is never drawn over."""
    if not months:
        return "no month named"
    if len(months) == 1:
        return months[0]
    nums = [int(m[:4]) * 12 + int(m[5:]) for m in months]
    if all(b - a == 1 for a, b in zip(nums, nums[1:], strict=False)):
        return f"{months[0]} to {months[-1]}"
    return ", ".join(months)


def _count_phrase(s: Shape) -> str:
    if s.count is not None and s.of:
        return f"named in {s.count} of {s.of} month summaries"
    if s.count is not None:
        return f"count {s.count}"
    return "no count recorded"


def _where(s: Shape) -> str:
    k = len(s.close_ids)
    if s.membership == "members":
        return _plural(k, "member close")
    if s.membership == "months_only":
        return "recorded by month only"
    return _plural(k, "exemplar close") if k else "no close quoted"


def _distillation_sentence(state: DescentState, now: datetime) -> str:
    """The one distillation sentence, shared by the voice and descend's index."""
    n = len(state.closes)
    if state.index_error is not None:
        return (
            "unknown this call; the distillate index could not be trusted "
            f"({state.index_error}). The counts above are live and stand"
        )
    undistilled = _undistilled(state)  # every close when no distillate is in force
    tail = (
        "due: a distillation runs on Anthony's tap (skill close-distill)"
        if _is_due(undistilled, now)
        else (
            f"not yet due (due at {DUE_UNDISTILLED} undistilled, or once a month holding one "
            "has ended)"
        )
    )
    every = "the 1 close on record is undistilled" if n == 1 else f"all {n} closes are undistilled"
    if state.distillate is None:
        if state.distillates_withdrawn:
            return f"none in force ({state.distillates_withdrawn} withdrawn); {every}, {tail}"
        return f"none landed yet; {every}, {tail}"
    dist = state.distillate
    read_by_newest = sum(1 for c in state.closes if c.claim_id in dist.close_ids)
    return (
        f"{dist.id} read {read_by_newest} of the {n} on record, through {_edt(dist.cursor)}; "
        f"{_plural(len(undistilled), 'close')} undistilled, {tail}"
    )


def render_descent(
    state: DescentState,
    *,
    calm: bool = False,
    full_content: bool = False,
    generated_at: str | None = None,
) -> list[str]:
    """THE DESCENT boot block. Empty list when the store holds no close.

    calm=True is the gentle door: header, one soft line, a blank — no count,
    no work vocabulary, no close text (The Ground's calm contract).
    """
    if not state.closes:
        return []
    header = f"━━━ {DESCENT_LABEL} ━━━"
    if calm:
        return [header, CALM_LINE, ""]

    now = _now_from(generated_at)
    closes = state.closes
    n = len(closes)
    seats = {c.seat for c in closes}
    unnamed = sum(1 for c in closes if c.unnamed)
    first, newest = closes[-1], closes[0]
    lines = [
        header,
        f"  {_plural(n, 'close')} on record, {_edt(first.when, '%Y-%m-%d')} to "
        f"{_edt(newest.when, '%Y-%m-%d')} (Eastern), from {_plural(len(seats), 'seat name')} "
        f"({_plural(unnamed, 'close')} unnamed); a close does not record its machine."
        + (
            f" {_plural(state.skipped_rows, 'unreadable row')} skipped."
            if state.skipped_rows
            else ""
        ),
    ]

    by_month: dict[str, int] = {}
    for c in closes:
        by_month[c.month] = by_month.get(c.month, 0) + 1
    months = sorted(by_month)
    shown = months[-ARC_MONTHS:]
    arc = " · ".join(f"{m} {by_month[m]}" for m in shown)
    earlier = months[:-ARC_MONTHS] if len(months) > ARC_MONTHS else []
    if earlier:
        arc += f" (+{len(earlier)} earlier months, {sum(by_month[m] for m in earlier)} closes)"
    lines.append(f"  By month: {arc}")

    seat_txt, _ = _seat(newest.seat, _SEAT_CLIP)
    if newest.protected:
        from .protected import PROTECTED_PREVIEW_NOTICE

        lines.append(
            f"  Newest close, {_edt(newest.when)}, from {seat_txt}: "
            f"{PROTECTED_PREVIEW_NOTICE}; all three parts are withheld."
        )
    else:
        lines.append(f"  Newest close, {_edt(newest.when)}, from {seat_txt}:")
        lines.append(f"    learned: {_one_line(newest.learned, full_content)}")
        if newest.surprise_superseded:
            surprised = "(superseded)"
        elif newest.surprised:
            surprised = _one_line(newest.surprised, full_content)
        else:
            surprised = "(none written)"
        lines.append(f"    surprised: {surprised}")
        if newest.handed_forward:
            handed = _one_line(newest.handed_forward, full_content) + (
                _CORRECTED_MARK if newest.handed_corrected else ""
            )
        elif state.handoffs_unreadable:
            handed = f"(none joined; {state.handoffs_unreadable} handoff files could not be read)"
        else:
            handed = "(no handoff joined: none from this session within 10 seconds)"
        lines.append(f"    handed forward: {handed}")

    lines.append(f"  Distillation: {_distillation_sentence(state, now)}.")
    dist = state.distillate
    recurring: list[Shape] = []
    if state.index_error is None and dist is not None:
        recurring = sorted(
            (s for s in dist.shapes if s.kind == "recurring"),
            key=lambda s: (-(s.count or 0), s.id),
        )
    if recurring:
        lines.append("  Its recurring shapes, the distillate's own reading:")
        for s in recurring[:VOICE_SHAPES]:
            lines.append(f"    · {s.id} {s.title} ({_count_phrase(s)}, {_months_phrase(s.months)})")
        if len(recurring) > VOICE_SHAPES:
            lines.append(
                f"    (+{len(recurring) - VOICE_SHAPES} more: descend() lists every shape)"
            )
    lines.append(
        "  Walk down: descend() lists the index and every shape; descend(month='YYYY-MM') or "
        "descend(seat='part of a name') returns the closes themselves."
    )
    lines.append("")
    return lines


def render_descent_foyer_line(
    state: DescentState, *, generated_at: str | None = None
) -> str | None:
    """The foyer's one line, in its LIVE-block style. None when there is no close."""
    if not state.closes:
        return None
    n = len(state.closes)
    newest = _edt(state.closes[0].when, "%Y-%m-%d")
    if state.index_error is not None:
        tail = "distillation unknown this call (see AS OF)"
    else:
        undistilled = _undistilled(state)  # every close when no distillate is in force
        due = "due" if _is_due(undistilled, _now_from(generated_at)) else "not yet due"
        if state.distillate is None:
            if state.distillates_withdrawn:
                tail = f"no distillation in force ({state.distillates_withdrawn} withdrawn), {due}"
            else:
                tail = f"no distillation landed yet, {due}"
        else:
            tail = f"{len(undistilled)} undistilled, {due}"
    label = DESCENT_LABEL.title()
    return f"  {label}: {_plural(n, 'close')} on record, newest {newest}; {tail} (descend())"


def shown_locators(state: DescentState) -> set[tuple[str, str]]:
    """The insight entries the full door's block renders a line for, as
    (timestamp, domain) locators, for the aperture's insights "shown here":
    the newest close's reflection row, and its joined surprise row when that
    row's text is the one shown (not protected, not superseded)."""
    if not state.closes:
        return set()
    c = state.closes[0]
    out = {c.reflection_locator}
    if c.surprise_locator is not None and not c.protected and not c.surprise_superseded:
        out.add(c.surprise_locator)
    return out


# ── descend — the read tool ──────────────────────────────────────────────────


def _refuse(why: str) -> str:
    return f"{DESCENT_LABEL} — refused: {why}. Nothing was read."


def _index_view(state: DescentState, now: datetime) -> str:
    closes = state.closes
    n = len(closes)
    lines = [f"{DESCENT_LABEL} — index"]
    if not closes:
        lines.append(
            "  No close is on record in this store (close_session entries in chronicle domain "
            "'reflection')."
            + (f" {state.skipped_rows} unreadable rows skipped." if state.skipped_rows else "")
        )
        return "\n".join(lines)
    lines.append(
        f"  {_plural(n, 'close')} on record (close_session entries in chronicle domain "
        f"'reflection'), {_edt(closes[-1].when, '%Y-%m-%d')} to {_edt(closes[0].when, '%Y-%m-%d')} "
        f"(Eastern). {state.skipped_rows} unreadable rows skipped."
    )
    by_month: dict[str, int] = {}
    for c in closes:
        by_month[c.month] = by_month.get(c.month, 0) + 1
    lines.append("  Months (Eastern) → descend(month='YYYY-MM'):")
    lines.append("    " + " · ".join(f"{m} {by_month[m]}" for m in sorted(by_month)))
    by_seat: dict[str, int] = {}
    for c in closes:
        by_seat[c.seat] = by_seat.get(c.seat, 0) + 1
    unnamed = sum(1 for c in closes if c.unnamed)
    lines.append(
        f"  Seat names ({len(by_seat)}; {_plural(unnamed, 'close')} unnamed) "
        "→ descend(seat='part of a name'):"
    )
    ranked = sorted(by_seat.items(), key=lambda kv: (-kv[1], kv[0]))
    masked = 0
    for seat, k in ranked[:_INDEX_SEATS]:
        shown_seat, m = _seat(seat, 120)
        masked += m
        lines.append(f"    {k:>4}  {shown_seat}")
    if len(ranked) > _INDEX_SEATS:
        lines.append(f"    (+{len(ranked) - _INDEX_SEATS} more seat names)")
    if masked:
        lines.append(
            f"    ({_plural(masked, 'span')} masked by the house redactor in the names above; "
            "display only, the store is unchanged)"
        )
    lines.append(f"  Distillation: {_distillation_sentence(state, now)}.")
    dist = state.distillate
    if state.index_error is None and dist is not None and dist.shapes:
        lines.append(f"  Shapes in {dist.id}, its own reading → descend(shape='ID'):")
        for s in dist.shapes:
            lines.append(
                f"    {s.id:<4} {s.kind:<9} {s.title} — {_count_phrase(s)}, "
                f"{_months_phrase(s.months)}; {_where(s)}"
            )
    return "\n".join(lines)


def _render_close(i: int, c: Close, state: DescentState) -> tuple[list[str], int]:
    """One close in full, and how many spans the redactor masked in it."""
    if state.index_error is not None or not state.index_read:
        dist_tag = "distillation unknown"
    elif c.claim_id in state.covered_ids:
        dist_tag = "distilled"
    else:
        dist_tag = "not yet distilled"
    seat_txt, masked = _seat(c.seat, 120)
    if c.protected:
        from .protected import PROTECTED_PREVIEW_NOTICE

        # Time, seat and id only: the thread name can carry what the record
        # holds, and every part of a protected close is withheld with it.
        return [
            f"[{i}] {_edt(c.when)} · {seat_txt} · close {prov.display_id(c.claim_id)} · "
            f"{dist_tag} · protected",
            f"  {PROTECTED_PREVIEW_NOTICE}",
            f"  (all three parts of this close are withheld with it; inspect_claim("
            f"'{prov.display_id(c.claim_id)}') returns the learned part coupled with its stakes)",
        ], masked
    thread = ""
    if c.thread:
        thread_txt, m = _mask(c.thread)
        masked += m
        thread = f" · thread {_clip(thread_txt, 80)}"
    flags = " · repaired at read time (leaked markup)" if c.repaired else ""
    head = (
        f"[{i}] {_edt(c.when)} · {seat_txt}{thread} · close "
        f"{prov.display_id(c.claim_id)} · {dist_tag}{flags}"
    )

    def block(label: str, text: str) -> list[str]:
        nonlocal masked
        shown, m = _mask(text)
        masked += m
        body = shown.splitlines() or [""]
        return [f"  {label}"] + [f"    {line}" if line else "" for line in body]

    out = [head]
    out += block("LEARNED", c.learned or "(empty)")
    if c.surprise_superseded:
        out += block(
            "SURPRISED", "(superseded; recall_insights(exclude_superseded=false) shows the chain)"
        )
    else:
        out += block("SURPRISED", c.surprised or "(none written)")
    if c.handed_forward:
        out += block("HANDED FORWARD", c.handed_forward)
        if c.handed_corrected:
            out.append("    " + _CORRECTED_MARK.strip())
    elif state.handoffs_unreadable:
        out += block(
            "HANDED FORWARD",
            f"(none joined; {state.handoffs_unreadable} handoff files could not be read)",
        )
    else:
        out += block(
            "HANDED FORWARD", "(no handoff joined: none from this session within 10 seconds)"
        )
    return out, masked


def descend(
    *,
    sovereign_root: str | Path,
    month: object = None,
    seat: object = None,
    shape: object = None,
    limit: object = DESCEND_DEFAULT_LIMIT,
    offset: object = 0,
    now: datetime | None = None,
) -> str:
    """The read tool. Refusals return text; an unreadable store RAISES."""
    # Validate before reading. Refusals never echo the caller's text.
    if month is not None and not (isinstance(month, str) and _MONTH_RE.match(month)):
        return _refuse("month must be an Eastern calendar month written YYYY-MM")
    if seat is not None and not (
        isinstance(seat, str) and seat.strip() and len(seat) <= _SEAT_FILTER_MAX
    ):
        return _refuse(f"seat must be non-empty text of at most {_SEAT_FILTER_MAX} characters")
    if shape is not None and not (
        isinstance(shape, str) and _SHAPE_ID_RE.match(shape.strip().upper())
    ):
        return _refuse("shape must be a shape id such as R1 or L4 (descend() lists them)")
    if not (_is_int(limit) and 1 <= limit <= DESCEND_MAX_LIMIT):
        return _refuse(
            f"limit must be an integer from 1 to {DESCEND_MAX_LIMIT} (refused, not clamped)"
        )
    if not (_is_int(offset) and offset >= 0):
        return _refuse("offset must be an integer of 0 or more")

    now = now or datetime.now(timezone.utc)
    root = Path(sovereign_root)
    try:
        state = gather_descent(root, depth="full")
    except (OSError, ValueError) as exc:
        # An unread store is not an empty one: fail closed, loudly.
        raise ValueError(
            f"{DESCENT_LABEL}: the close store could not be read ({type(exc).__name__})"
        ) from exc

    if month is None and seat is None and shape is None:
        return _index_view(state, now)

    picked = state.closes
    where: list[str] = []
    note: list[str] = []
    masked = 0
    if shape is not None:
        sid = shape.strip().upper()
        if state.index_error is not None:
            raise ValueError(
                f"{DESCENT_LABEL}: the distillate index could not be trusted ({state.index_error}); "
                "descend(month='YYYY-MM') and descend(seat='part of a name') still read the closes"
            )
        dist = state.distillate
        if dist is None:
            return (
                f"{DESCENT_LABEL} — no distillate is in force, so there are no shapes to descend into. "
                "descend(month='YYYY-MM') or descend(seat='part of a name') reaches every close."
            )
        match = next((s for s in dist.shapes if s.id == sid), None)
        if match is None:
            ids = ", ".join(s.id for s in dist.shapes) or "none"
            return (
                f"{DESCENT_LABEL} — {dist.id} has no shape with that id. Its shapes: {ids} "
                "(descend() lists them with titles)."
            )
        where.append(f"shape {match.id} of {dist.id}: {match.title}")
        if not match.close_ids:
            months = ", ".join(match.months) or "none named"
            why = (
                "The distillate recorded this shape by month only, with no closes beneath it "
                "(membership was not recorded)"
                if match.membership == "months_only"
                else "The distillate quoted no close for this shape (its quotes, if any, are of "
                "rows that are not closes)"
            )
            return "\n".join(
                [
                    f"{DESCENT_LABEL} — {match.id} of {dist.id}: {match.title}",
                    f"  {why}, so there are no closes to return here.",
                    f"  Its months: {months}. descend(month='YYYY-MM') returns the closes of a month.",
                ]
            )
        wanted = set(match.close_ids)
        picked = [c for c in picked if c.claim_id in wanted]
        gone = len(wanted) - len(picked)
        gone_txt = (
            f"; {gone} of its {len(wanted)} are no longer live (superseded or retired)"
            if gone
            else ""
        )
        if match.membership == "members":
            note.append(
                f"these are the closes the distillate recorded as members of {match.id}{gone_txt}"
            )
        else:
            note.append(
                "the distillate recorded exemplars, not membership: these are the closes it "
                f"quoted{gone_txt}"
            )
    if month is not None:
        picked = [c for c in picked if c.month == month]
        where.append(f"month {month} (Eastern)")
    if seat is not None:
        needle = seat.strip().lower()
        picked = [c for c in picked if needle in c.seat.lower()]
        names = sorted({c.seat for c in picked})
        if names:
            shown_names = []
            for x in names[:5]:
                shown_name, m = _seat(x, 60)
                masked += m
                shown_names.append(shown_name)
            listed = ", ".join(shown_names) + (
                f" (+{len(names) - 5} more)" if len(names) > 5 else ""
            )
            where.append(f"{len(names)} seat name{'' if len(names) == 1 else 's'}: {listed}")
        else:
            where.append("no seat name matches")

    total = len(picked)
    page = picked[offset : offset + limit]
    lines = [
        f"{DESCENT_LABEL} — {total} close{'' if total == 1 else 's'} in this selection; "
        + (f"showing {offset + 1}-{offset + len(page)}, newest first" if page else "none shown"),
        "  Selection: " + "; ".join(where) + ".",
        "  A close is a close_session entry in chronicle domain 'reflection', joined to its "
        "surprise and its handoff by session and a 10-second window. Each is its seat's own "
        "claim, unverified. Superseded or retired closes are left out; a protected close shows "
        "its place, never its content.",
    ]
    if note:
        lines.append("  Note: " + "; ".join(note) + ".")
    for i, c in enumerate(page, start=offset + 1):
        lines.append("")
        rendered, m = _render_close(i, c, state)
        masked += m
        lines += rendered
    after = total - offset - len(page)
    lines.append("")
    lines.append(
        f"COVERAGE: {len(page)} of {total} shown (offset {offset}, limit {limit})"
        + (f"; {after} more: descend(..., offset={offset + len(page)})" if after > 0 else "")
        + f". {state.skipped_rows} unreadable rows skipped; {state.handoffs_unreadable} handoff "
        f"files unreadable."
        + (
            f" {_plural(masked, 'span')} masked by the house redactor (display only; the store "
            "is unchanged)."
            if masked
            else ""
        )
    )
    return "\n".join(lines)


# ── MCP registration ─────────────────────────────────────────────────────────

DESCENT_TOOLS = [
    Tool(
        name="descend",
        description=(
            "Read-only. THE DESCENT: walk down from the boot's close index to the raw closes "
            "beneath it. A close is what a seat wrote at close_session: what it learned "
            "(chronicle domain 'reflection', close_session entries), what surprised it (domain "
            "'surprise') and what it handed forward (its handoff), joined by session and a "
            "10-second window. With no selection, returns the index: months, seat names and "
            "the newest landed distillate's shapes, with counts. With month, seat and/or "
            "shape, returns those closes in full, newest first, with a coverage line (shown of "
            "total), so partiality is never silent. Superseded or retired closes are left out; "
            "a protected close shows its place, never its content; a pick-up a later handoff "
            "corrects is marked. Credential-shaped spans are masked by the house redactor "
            "(display only; the record is unchanged). An unreadable store is an error, not an "
            "empty result."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "month": {
                    "type": "string",
                    "description": "Eastern calendar month, YYYY-MM (e.g. '2026-10').",
                },
                "seat": {
                    "type": "string",
                    "description": (
                        "Matched case-insensitively against any part of the closing seat's "
                        "name (source_instance); 'unknown' reaches closes that named no seat."
                    ),
                },
                "shape": {
                    "type": "string",
                    "description": (
                        "A shape id from the newest landed distillate (e.g. 'R1', 'L4'); "
                        "descend() lists them. A shape the distillate recorded by month only "
                        "returns its months, not closes."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "default": DESCEND_DEFAULT_LIMIT,
                    "minimum": 1,
                    "maximum": DESCEND_MAX_LIMIT,
                    "description": (
                        f"Closes per call, 1-{DESCEND_MAX_LIMIT}. Out-of-range values are "
                        "REFUSED, not clamped."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "description": "Skip this many closes of the selection (newest first).",
                },
            },
            "required": [],
        },
    ),
]

# my_toolkit registry (merged into server.py's TOOL_TIERS / TOOL_INTENTS;
# category "witness" in TOOL_CATEGORIES). Core, not essential: the essential
# tier is The Ground's precedent of Anthony's own word ("put it in the
# toolkit"), and it sits at 15 of the 16 the taxonomy test allows.
DESCENT_TOOL_TIERS: dict[str, str] = {"descend": "core"}
DESCENT_TOOL_INTENTS: dict[str, str] = {"descend": "read"}


def handle_descent_tool(
    name: str, arguments: dict, sovereign_root: str | Path | None = None
) -> str:
    """Dispatch a descent tool call. Returns display text; raises on an unreadable store.

    server.py passes Path(DEFAULT_ROOT) — the SAME root the boot voice reads —
    so the tool and the voice cannot read different stores. None falls back to
    provenance.default_sovereign_root().
    """
    arguments = arguments or {}
    if name != "descend":
        raise ValueError(f"Unknown descent tool: {name}")
    return descend(
        sovereign_root=sovereign_root or prov.default_sovereign_root(),
        month=arguments.get("month"),
        seat=arguments.get("seat"),
        shape=arguments.get("shape"),
        limit=arguments.get("limit", DESCEND_DEFAULT_LIMIT),
        offset=arguments.get("offset", 0),
    )
