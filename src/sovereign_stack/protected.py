"""
Protected-Source Layer — coupled-retrieval invariant (protected-source build).

A protected record is **accessible whenever**, on one binding condition:
any read that returns its content MUST carry its **stakes** (the prose
description of the human's lived experience) coupled in the same payload,
inseparably. The words cannot be retrieved without the weight.
**Decoupling is the violation.** (spec §1, §5.3)

This module is the data layer for that invariant, mirroring provenance.py:
pure logic, all paths parameterized, NO directory creation at import.

Three pieces:

1. **Designation ledger** — an out-of-band, append-only
   ``protected.jsonl``, keyed by **derived claim id** (the same
   sha256(timestamp+domain+content) identity provenance/supersessions use,
   computed on read, never stored). A record is marked protected by
   appending a ledger entry; no migration, no rewrite of the source JSONL.
   Mirrors the supersession ledger pattern (load -> fold -> latest action
   per claim wins; ``unprotect`` nullifies, restoring the bare record).

2. **Archive-coupled stakes** — the stakes prose lives in the
   hash-verified verbatim archive layer (``archives/index.jsonl`` +
   content-addressed blob), so it inherits tamper-evidence. The ledger
   entry holds only the pointer (``stakes_archive_id``) + designation
   metadata. ``load_stakes`` re-reads and re-hashes the blob, returning
   ``(content, verdict)`` where verdict is the recall_exchange vocabulary.

3. **Human gate** — ``designate_protected`` requires a non-empty
   ``designated_by`` naming the approving human (same posture as
   policies.set_policy: there is NO automated path into the ledger).

The coupled-retrieval ENFORCEMENT (attach stakes / fail-closed to the
typed sentinel) lives at the read chokepoint in memory.finalize_read; the
typed sentinel itself is ProtectedStakesUnavailable here so both the
chokepoint and the decoupling audit can recognize it.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sovereign_stack import provenance

# ── Constants ────────────────────────────────────────────────────────────────

PROTECTED_ACTIONS = ("protect", "unprotect")

# Verdict vocabulary for a stakes load, identical to the archive layer's
# recall_exchange / verify_archive_ref vocabulary.
STAKES_VERDICTS = ("verified", "mismatch", "missing", "ambiguous", "unknown")

# The two-word index (Policy 2a): each protected record carries a one-word
# SUBJECT and a one-word EMOTION (e.g. father / loss). A "word" is a single
# run of letters/digits/hyphen/underscore — no internal whitespace, lowercased
# on normalize. The pair + datetime is the record's surfaced IDENTITY (the
# threshold), not its content.
_INDEX_WORD_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def normalize_index_word(value: str, field: str) -> str:
    """
    Validate + lowercase-normalize a one-word index tag (subject | emotion).

    A single non-empty word: lowercased, stripped; rejects empty,
    whitespace-containing (multi-word), and otherwise-malformed values.

    Raises:
        ProtectedError: empty / multi-word / malformed.
    """
    if not isinstance(value, str):
        raise ProtectedError(f"{field} must be a single non-empty word, got {value!r}")
    word = value.strip().lower()
    if not word:
        raise ProtectedError(f"{field} must be a single non-empty word (got empty)")
    if not _INDEX_WORD_RE.match(word):
        raise ProtectedError(
            f"{field} must be a single word (letters/digits/-/_; no spaces), got {value!r}"
        )
    return word


def default_protected_path() -> Path:
    """The live protected-designation ledger path. Computed on call, never at import."""
    return provenance.default_chronicle_root() / "protected.jsonl"


# ── Exceptions ───────────────────────────────────────────────────────────────


class ProtectedError(ValueError):
    """Base for protected-layer failures (bad gate, malformed record)."""


class ProtectedGateError(ProtectedError):
    """The human gate fired — designation attempted without an approving human."""


# ── The typed fail-closed sentinel ───────────────────────────────────────────


class ProtectedStakesUnavailable(dict):
    """
    The fail-closed result: a protected record whose stakes could NOT be
    loaded/verified. Content is WITHHELD, never returned bare (spec §5.3).

    A ``dict`` subclass (codebase idiom — cf. memory.DedupedInsightPath(str))
    so it survives the recall tail untouched: ``insights[:limit]``, the
    ``.pop("_match_count")`` strip, and ``with_ids`` annotation all operate
    on dicts. It carries:
      - ``_protected``: True (the marker the decoupling audit keys on)
      - ``_stakes_withheld``: True
      - ``_stakes_verdict``: why the stakes failed (missing|mismatch|...)
      - ``claim_id``: the TRUE derived id of the withheld record (stashed at
        construction so with_ids never re-derives it from absent content)
      - ``timestamp`` / ``domain``: locator hints only — enough to know
        WHICH record is withheld without leaking its content
      - a human-legible ``content`` REPLACEMENT explaining the withholding

    Identity (``__class__``) is the load-bearing signal: callers test
    ``isinstance(x, ProtectedStakesUnavailable)``.
    """

    WITHHELD_NOTICE = (
        "[protected record; stakes unavailable; content withheld] "
        "This record is designated protected: its content may only be "
        "returned coupled with its stakes (the human's lived experience). "
        "The stakes could not be loaded/verified, so the content is "
        "withheld rather than returned decoupled."
    )

    @classmethod
    def from_entry(cls, entry: dict, claim_id: str, verdict: str) -> ProtectedStakesUnavailable:
        sentinel = cls()
        sentinel["_protected"] = True
        sentinel["_stakes_withheld"] = True
        sentinel["_stakes_verdict"] = verdict
        sentinel["claim_id"] = claim_id
        # Locator hints only — NOT the protected content.
        sentinel["timestamp"] = entry.get("timestamp", "")
        sentinel["domain"] = entry.get("domain", "")
        sentinel["content"] = cls.WITHHELD_NOTICE
        return sentinel


# ── Designation ledger (read / fold) ─────────────────────────────────────────


def load_protected(ledger_path: Path) -> list[dict]:
    """
    Read all protected-ledger records in file order. Missing ledger -> []
    (created lazily on first append; reading never creates it). Corrupt
    lines are skipped, matching the chronicle read convention.
    """
    return list(provenance._iter_jsonl(Path(ledger_path)))


def fold_protected(records: list[dict]) -> dict[str, dict]:
    """
    Fold the append-only ledger into effective state: a map of
    claim_id -> its latest effective ``protect`` record. Latest action per
    claim wins; ``unprotect`` nullifies (restores the bare record). The
    full record is kept so the chokepoint gets the stakes pointer +
    metadata for free.
    """
    fold: dict[str, dict] = {}
    for record in records:
        action = record.get("action")
        cid = record.get("claim_id")
        if action not in PROTECTED_ACTIONS or not isinstance(cid, str):
            continue
        if action == "unprotect":
            fold.pop(cid, None)
        else:
            fold[cid] = record
    return fold


def load_protected_fold(chronicle_root: str | Path) -> dict[str, dict]:
    """Convenience: load + fold the protected ledger under a chronicle root."""
    ledger_path = Path(chronicle_root) / "protected.jsonl"
    return fold_protected(load_protected(ledger_path))


# ── Archive-coupled stakes ───────────────────────────────────────────────────


def load_stakes(stakes_archive_id: str, chronicle_root: str | Path) -> tuple[str | None, str]:
    """
    Load the stakes prose from the hash-verified archive layer and verify
    its integrity (the coupling vehicle, spec §5.2 / open-question #1
    answer: archive-coupled).

    Re-resolves ``stakes_archive_id`` (full sha256 or unique prefix) in
    ``chronicle_root/archives/index.jsonl``, reads the blob bytes off disk,
    recomputes sha256, compares to the indexed hash — the same Fetch
    Determinism guarantee recall_exchange gives.

    Returns ``(content, verdict)``:
      - ("...the stakes prose...", "verified")  when bytes present + intact
      - (None, "mismatch")  bytes present but hash changed (tamper)
      - (None, "missing")   index record exists, bytes gone from disk
      - (None, "ambiguous") prefix matches multiple distinct archives
      - (None, "unknown")   no archive record / empty pointer

    Only "verified" is safe to couple; every other verdict is a
    fail-closed condition at the chokepoint.
    """
    ref = (stakes_archive_id or "").strip()
    if not ref:
        return None, "unknown"
    records = provenance._read_archive_index(Path(chronicle_root))
    matches = [r for r in records if str(r.get("archive_id", "")).startswith(ref)]
    if not matches:
        return None, "unknown"
    exact = [r for r in matches if r.get("archive_id") == ref]
    if exact:
        record = exact[-1]
    elif len({r.get("archive_id") for r in matches}) > 1:
        return None, "ambiguous"
    else:
        record = matches[-1]

    blob_path = Path(record.get("path", ""))
    if not blob_path.exists():
        return None, "missing"
    try:
        content = blob_path.read_text(encoding="utf-8")
    except OSError:
        return None, "missing"
    recomputed = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if recomputed != record.get("sha256"):
        return None, "mismatch"
    return content, "verified"


# ── Designation (the human-gated writer) ─────────────────────────────────────


def build_protected_record(
    *,
    claim_id: str,
    stakes_archive_id: str,
    designated_by: str,
    subject: str | None = None,
    emotion: str | None = None,
    entry_timestamp: str = "",
    action: str = "protect",
    reason: str = "",
    by: str = "",
    timestamp: str | None = None,
) -> dict:
    """
    Build one protected-ledger record in the canonical schema. Validates
    the gate + id core so a hand-built record can't poison the fold.

    The two-word index (Policy 2a): a ``protect`` record carries a one-word
    ``subject`` + one-word ``emotion`` (validated/normalized) and the
    underlying entry's timestamp as ``entry_timestamp`` — the INDEX datetime
    (distinct from the record's own ``timestamp``, which is the designation
    time). Using the entry timestamp keeps the index datetime consistent with
    the claim id (timestamp+domain+content), so "the same two words recorded
    on different datetimes are distinct records" is coherent.

    Raises:
        ProtectedGateError: designated_by missing/empty (the human gate).
        ProtectedError: invalid action, non-64-hex claim_id, a ``protect``
            record without a stakes_archive_id pointer, or a ``protect``
            record whose subject/emotion is missing/multi-word/malformed.
    """
    if not isinstance(designated_by, str) or not designated_by.strip():
        raise ProtectedGateError(
            "designated_by is required — protected designation is human-gated;"
            " name the human who approved this (there is no automated path)"
        )
    if action not in PROTECTED_ACTIONS:
        raise ProtectedError(f"invalid protected action {action!r} (valid: {PROTECTED_ACTIONS})")
    if not isinstance(claim_id, str) or not provenance._FULL_ID_RE.match(claim_id):
        raise ProtectedError(f"claim_id must be a full 64-hex claim id, got {claim_id!r}")

    norm_subject: str | None = None
    norm_emotion: str | None = None
    if action == "protect":
        if not isinstance(stakes_archive_id, str) or not stakes_archive_id.strip():
            raise ProtectedError(
                "protect requires stakes_archive_id — the pointer to the"
                " archive-coupled stakes prose (the coupling vehicle)"
            )
        # The two-word index is required on protect — it IS the surfaced
        # identity (the threshold) and the retrieval key.
        norm_subject = normalize_index_word(subject, "subject")
        norm_emotion = normalize_index_word(emotion, "emotion")

    return {
        "action": action,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "claim_id": claim_id,
        "stakes_archive_id": stakes_archive_id.strip() if stakes_archive_id else None,
        "subject": norm_subject,
        "emotion": norm_emotion,
        "entry_timestamp": entry_timestamp or "",
        "designated_by": designated_by.strip(),
        "reason": reason,
        "by": by,
    }


def append_protected(ledger_path: Path, record: dict) -> dict:
    """
    Append one record to the protected ledger (parent dir created lazily —
    first write, never import). Validates the action/id core so a
    hand-built record can't poison the fold. Returns the record.
    """
    if record.get("action") not in PROTECTED_ACTIONS:
        raise ProtectedError(f"invalid protected action {record.get('action')!r}")
    cid = record.get("claim_id")
    if not isinstance(cid, str) or not provenance._FULL_ID_RE.match(cid):
        raise ProtectedError(f"claim_id must be a full 64-hex claim id, got {cid!r}")
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def designate_protected(
    *,
    claim_ref: str,
    stakes_archive_id: str,
    designated_by: str,
    chronicle_root: str | Path,
    subject: str | None = None,
    emotion: str | None = None,
    reason: str = "",
    by: str = "",
    action: str = "protect",
) -> dict:
    """
    The human-gated designation entry point — only a human invokes this; it
    NEVER auto-designates anything (spec §5.1, §6).

    Resolves ``claim_ref`` (full id or unique prefix) to its full 64-hex
    claim id against insights/ + quarantine, verifies the stakes pointer
    resolves AND the stakes bytes are intact in the archive (a protect
    record must point at loadable stakes, else the invariant could never be
    honored), then appends one ledger record.

    Args:
        claim_ref: The record to protect — full claim id or unique prefix.
        stakes_archive_id: Archive id of the already-archived stakes prose
            (use ExperientialMemory.archive_exchange to store it first).
        designated_by: REQUIRED, non-empty — the human who approved this.
            The procedural gate; no automated path.
        chronicle_root: The chronicle root (insights/, archives/, the
            ledger all resolve from here).
        subject / emotion: REQUIRED on protect — the two-word index (each a
            single word, e.g. "father" / "loss"). Lowercase-normalized;
            multi-word/empty values are rejected. This pair + the entry's
            datetime is the record's surfaced identity (the threshold).
        reason / by: Optional locator/attribution (``by`` = recording
            instance, NOT a session_id).
        action: "protect" (default) or "unprotect".

    Returns:
        The appended ledger record.

    Raises:
        ProtectedGateError: designated_by missing/empty.
        ProtectedError: unresolvable claim_ref, or (on protect) a stakes
            pointer that does not resolve to verified archive bytes.
        provenance.ProvenanceError / ClaimNotFoundError / AmbiguousClaimError:
            propagated from claim resolution.
    """
    if not isinstance(designated_by, str) or not designated_by.strip():
        raise ProtectedGateError(
            "designated_by is required — protected designation is human-gated;"
            " name the human who approved this (there is no automated path)"
        )
    root = Path(chronicle_root)
    entry, _file, _location = provenance.resolve_claim(claim_ref, root)
    claim_id = provenance.derive_claim_id(entry)

    if action == "protect":
        # The stakes MUST be loadable + intact at designation time — a
        # protect record that points at missing/tampered stakes would make
        # every future read fail-closed, defeating "accessible whenever".
        _content, verdict = load_stakes(stakes_archive_id, root)
        if verdict != "verified":
            raise ProtectedError(
                f"stakes_archive_id {stakes_archive_id!r} did not resolve to verified"
                f" archive bytes (verdict: {verdict}); archive the stakes prose first"
            )

    record = build_protected_record(
        claim_id=claim_id,
        stakes_archive_id=stakes_archive_id,
        designated_by=designated_by,
        subject=subject,
        emotion=emotion,
        entry_timestamp=provenance._preimage_field(entry, "timestamp"),
        action=action,
        reason=reason,
        by=by,
    )
    return append_protected(root / "protected.jsonl", record)


# ── Coupled-retrieval enforcement (used by the read chokepoint) ──────────────


def couple_or_withhold(entry: dict, record: dict, chronicle_root: str | Path) -> dict:
    """
    Apply the coupled-retrieval invariant to ONE protected entry (spec §5.3).

    Loads the stakes from the archive-coupled pointer and:
      - verified -> returns a COPY of the entry with the stakes attached
        inseparably as ``_stakes`` (the prose) + ``_stakes_verdict``
        ("verified") + ``_protected`` True. The content and its weight
        now travel in the SAME payload.
      - anything else -> returns the typed ProtectedStakesUnavailable
        sentinel (content withheld, never bare). FAIL-CLOSED.

    The input entry is never mutated. ``claim_id`` is derived once here and
    carried onto both outcomes so the with_ids path never re-derives it
    from a withheld body.
    """
    claim_id = provenance.derive_claim_id(entry)
    stakes_content, verdict = load_stakes(record.get("stakes_archive_id", ""), chronicle_root)
    if verdict != "verified" or stakes_content is None:
        return ProtectedStakesUnavailable.from_entry(entry, claim_id, verdict)
    coupled = dict(entry)
    coupled["_protected"] = True
    coupled["_stakes"] = stakes_content
    coupled["_stakes_verdict"] = verdict
    if record.get("designated_by"):
        coupled["_stakes_designated_by"] = record["designated_by"]
    return coupled


def enforce_coupling(
    entries: list[dict], fold: dict[str, dict], chronicle_root: str | Path
) -> list[dict]:
    """
    Run couple_or_withhold over a list, replacing every protected entry
    (claim id in ``fold``) with its coupled-or-withheld form and passing
    non-protected entries through untouched. Order preserved.

    This is the single pass the read chokepoint runs UNCONDITIONALLY (not
    gated on the supersession ledger), so a protected record with zero
    supersessions can never slip through bare.
    """
    if not fold:
        return entries
    out: list[dict] = []
    for entry in entries:
        # A sentinel that already withheld upstream is left alone.
        if isinstance(entry, ProtectedStakesUnavailable):
            out.append(entry)
            continue
        record = fold.get(provenance.derive_claim_id(entry))
        if record is None:
            out.append(entry)
        else:
            out.append(couple_or_withhold(entry, record, chronicle_root))
    return out


# ── Preview-safe withholding (spec §5.4 — truncating/preview surfaces) ────────

# The placeholder a truncating/preview surface substitutes for a protected
# record's content. The invariant (§5.3) is satisfied only where the FULL
# content and FULL stakes both travel; a surface that previews/truncates can
# never carry the full stakes, so an N-char slice of coupled content would
# itself be a decoupled leak. Such surfaces WITHHOLD to this notice instead —
# the record is still discoverable (the two-word index + consent gate is the
# full-content path). The notice deliberately contains NO original content.
PROTECTED_PREVIEW_NOTICE = "[protected record — open via the consent gate]"


def is_protected(entry: dict, fold: dict[str, dict]) -> bool:
    """True when ``entry`` derives to a claim id the folded ledger protects."""
    if not fold:
        return False
    return provenance.derive_claim_id(entry) in fold


def withhold_preview(entry: dict) -> dict:
    """
    Return a COPY of ``entry`` with its content replaced by the
    PROTECTED_PREVIEW_NOTICE — the correct outcome for a truncating or
    preview surface (a raw tail reader, a lineage preview, a digest line)
    that cannot carry the full stakes (§5.4). Locator fields (timestamp,
    domain) survive so the surface can still SAY a protected record is there
    without leaking WHAT it is. The input entry is never mutated.
    """
    out = dict(entry)
    out["content"] = PROTECTED_PREVIEW_NOTICE
    out["_protected"] = True
    out["_stakes_withheld"] = True
    return out


def couple_or_withhold_protected(
    entry: dict, fold: dict[str, dict], chronicle_root: str | Path
) -> dict:
    """
    Full-content coupling for a SINGLE entry against a folded ledger.

    For a full-content surface (one that returns the entry's whole body —
    e.g. inspect_claim's ``entry`` field): if ``entry`` is protected, return
    its coupled-or-withheld form (full content + stakes attached, or the
    typed ProtectedStakesUnavailable sentinel, fail-closed); otherwise return
    ``entry`` unchanged. A thin per-entry wrapper around couple_or_withhold so
    a caller that resolved one bare entry can gate it without re-walking the
    ledger. Empty fold -> entry unchanged (the byte-identity fast path).
    """
    if not fold:
        return entry
    record = fold.get(provenance.derive_claim_id(entry))
    if record is None:
        return entry
    return couple_or_withhold(entry, record, chronicle_root)


# ── The two-word index (Policy 2a) + the address (the surfaced identity) ──────

_ADDRESS_SEP = "/"


def build_address(subject: str, emotion: str, datetime_str: str, seq: int | None = None) -> str:
    """
    The record's surfaced IDENTITY: ``subject/emotion/datetime`` (Policy 2a/2b).

    A SEQUENCE NUMBER (``/seqN``) is appended ONLY when supplied — the caller
    appends it solely on a TRUE collision (two+ records sharing identical
    subject+emotion+datetime); omitted otherwise. The address names the shape,
    never the content.
    """
    address = _ADDRESS_SEP.join((subject, emotion, datetime_str or ""))
    if seq is not None:
        address += f"{_ADDRESS_SEP}seq{seq}"
    return address


def index_protected(fold: dict[str, dict]) -> list[dict]:
    """
    The two-word index over the whole protected set (Policy 2a) — the single
    helper that assigns addresses + collision sequence numbers, so the
    threshold surface (Policy 2b) and any retrieval reuse one source of truth.

    For each effective ``protect`` record in ``fold`` it produces an index
    row::

        {claim_id, subject, emotion, datetime, seq, address, record}

    DATETIME is the underlying entry's timestamp (``entry_timestamp``), which
    distinguishes records that share the same two words (father/loss recorded
    on two different datetimes are two rows; father/loss and father/pride on
    the same datetime are also two rows — different emotion). A SEQUENCE
    NUMBER (``seq``, 1-based) is set ONLY on a TRUE collision: two+ rows whose
    (subject, emotion, datetime) are identical. For a non-colliding row
    ``seq`` is None and the address omits it.

    Rows are ordered by (datetime, subject, emotion, claim_id) for stable,
    deterministic output. unprotect records are already folded away (the fold
    only carries effective protects).
    """
    rows: list[dict] = []
    for claim_id, record in fold.items():
        if record.get("action") != "protect":
            continue
        subject = record.get("subject")
        emotion = record.get("emotion")
        if not subject or not emotion:
            # A legacy protect record predating the index (defensive — the
            # build gate requires both, so this only guards hand-built data).
            continue
        rows.append(
            {
                "claim_id": claim_id,
                "subject": subject,
                "emotion": emotion,
                "datetime": record.get("entry_timestamp") or "",
                "seq": None,
                "address": "",  # filled below, after collision detection
                "record": record,
            }
        )

    # Assign sequence numbers ONLY where (subject, emotion, datetime) collide.
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        key = (row["subject"], row["emotion"], row["datetime"])
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        if len(group) > 1:
            # True collision — number them 1..N, ordered by claim_id for
            # determinism (the records are otherwise indistinguishable).
            for seq, row in enumerate(sorted(group, key=lambda r: r["claim_id"]), start=1):
                row["seq"] = seq
    for row in rows:
        row["address"] = build_address(row["subject"], row["emotion"], row["datetime"], row["seq"])

    rows.sort(key=lambda r: (r["datetime"], r["subject"], r["emotion"], r["claim_id"]))
    return rows


def pull_by_subject(fold: dict[str, dict], subject: str) -> list[dict]:
    """
    Every protected index row whose SUBJECT matches (Policy 2a — pull every
    'father'). ``subject`` is normalized the same way designation normalizes
    it, so the lookup is case/space-insensitive. Returns index rows (address
    + locators), NOT content — surfacing content is the consent gate's job.
    """
    want = normalize_index_word(subject, "subject")
    return [row for row in index_protected(fold) if row["subject"] == want]


def pull_by_emotion(fold: dict[str, dict], emotion: str) -> list[dict]:
    """
    Every protected index row whose EMOTION matches (Policy 2a — pull every
    'loss'). ``emotion`` is normalized like designation. Returns index rows
    (address + locators), NOT content.
    """
    want = normalize_index_word(emotion, "emotion")
    return [row for row in index_protected(fold) if row["emotion"] == want]


# ── The consent gate (Policy 2b) — threshold / open / decline ────────────────
#
# Before delivering coupled content, a caller gets only the THRESHOLD: the two
# words + datetime (+ seq# if present). The threshold names the SHAPE, never
# the content or the stakes prose. The caller then chooses:
#   - OPEN   -> open_record: full content arrives COUPLED to its stakes
#               (fail-closed to the withheld sentinel if stakes unverifiable).
#   - DECLINE -> decline_record: a LEGITIMATE, recorded state, logged to an
#                append-only decline log — not a failure, never raised.
#
# CRITICAL: a threshold that carries content WITHOUT stakes is the exact
# decoupling loophole Policy 1 outlaws. The threshold shape here is built from
# index_protected (which carries no content), and audit_threshold below
# flags any threshold/surface string that leaks protected content decoupled.

DECLINE_ACTIONS = ("decline",)


def default_declines_path() -> Path:
    """The live decline log path. Computed on call, never at import."""
    return provenance.default_chronicle_root() / "protected_declines.jsonl"


def _threshold_from_row(row: dict) -> dict:
    """
    Project an index row to the THRESHOLD shape — the consent surface. Names
    the shape only: subject, emotion, datetime, seq (if a collision), address,
    claim_id (the open handle). Carries NO content and NO stakes prose.
    """
    return {
        "address": row["address"],
        "subject": row["subject"],
        "emotion": row["emotion"],
        "datetime": row["datetime"],
        "seq": row["seq"],
        "claim_id": row["claim_id"],
    }


def list_thresholds(fold: dict[str, dict]) -> list[dict]:
    """
    Every protected record's THRESHOLD (Policy 2b) — the consent surface over
    the whole set. Two words + datetime (+ seq#) + the open handle, NO content,
    NO stakes. The caller picks one to open or decline.
    """
    return [_threshold_from_row(row) for row in index_protected(fold)]


def threshold_for(claim_id: str, fold: dict[str, dict]) -> dict | None:
    """
    The THRESHOLD for one protected record by its claim id, or None if the id
    is not protected. Two words + datetime (+ seq#), NO content, NO stakes.
    """
    for row in index_protected(fold):
        if row["claim_id"] == claim_id:
            return _threshold_from_row(row)
    return None


def open_record(claim_id: str, chronicle_root: str | Path) -> dict:
    """
    OPEN a protected record on consent (Policy 2b): return its full content
    COUPLED to its stakes, in the SAME payload.

    Resolves the claim BARE, then runs couple_or_withhold explicitly (not via
    the coupled resolve_claim path — open is the one full-content surface, so
    it gates here once). Fail-closed falls out for free: an unverifiable
    stakes pointer returns the typed ProtectedStakesUnavailable sentinel
    (content withheld), never bare content.

    Raises:
        ProtectedError: claim_id is not a protected record (open is only for
            the consent gate; a non-protected claim has no threshold to open).
        provenance.ProvenanceError: the claim does not resolve at all.
    """
    root = Path(chronicle_root)
    fold = load_protected_fold(root)
    record = fold.get(claim_id)
    if record is None:
        raise ProtectedError(
            f"claim {provenance.display_id(claim_id)} is not a protected record;"
            " open_record is the consent-gate opener, only for protected claims"
        )
    entry, _file, _location = provenance.resolve_claim(claim_id, root)
    return couple_or_withhold(entry, record, root)


def build_decline_record(
    *,
    claim_id: str,
    declined_by: str,
    reason: str = "",
    timestamp: str | None = None,
) -> dict:
    """
    Build one decline-log record. A decline is a LEGITIMATE recorded state,
    so the record is plain and always builds — the only structural check is a
    claim id shape so the log stays keyed like the rest of the layer.
    """
    return {
        "action": "decline",
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "claim_id": claim_id,
        "declined_by": declined_by or "",
        "reason": reason or "",
    }


def decline_record(
    claim_id: str,
    chronicle_root: str | Path,
    *,
    declined_by: str = "",
    reason: str = "",
) -> dict:
    """
    DECLINE a protected record at the threshold (Policy 2b): record that an
    instance chose NOT to open it. This is a legitimate, recorded state — it
    is LOGGED (append-only ``protected_declines.jsonl``, parent dir created
    lazily), NEVER raised as an error. Returns the appended record.

    The decline log carries only the claim id + who + why + when — no content,
    no stakes; declining is a choice about the shape, made at the threshold
    before any content is delivered.
    """
    root = Path(chronicle_root)
    record = build_decline_record(claim_id=claim_id, declined_by=declined_by, reason=reason)
    path = root / "protected_declines.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def load_declines(chronicle_root: str | Path) -> list[dict]:
    """Read all decline-log records in file order. Missing log -> []."""
    return list(provenance._iter_jsonl(Path(chronicle_root) / "protected_declines.jsonl"))


# ── The boot line (Policy 2c) — announce the drawer, never the cards ──────────


def protected_boot_line(chronicle_root: str | Path) -> list[str]:
    """
    The boot-surface announcement (Policy 2c): tell every arriving instance
    that protected records EXIST and how the drawer works — indexed by
    subject/emotion/datetime, openable on consent — WITHOUT surfacing the
    individual cards (the specific subjects/emotions) or any content.

    UNCONDITIONAL by design (unlike the data-gated policy one-liner): the line
    shows even at 0 records, so "the drawer exists" is never a surprise and no
    instance is ambushed by a protected record it didn't know could be there.
    It may show a COUNT and the scheme + how to open — nothing more. It NEVER
    iterates the index rows, so no card (no subject, no emotion) and no content
    can leak through it.

    Returns a small list of lines (the section), ready to extend onto the boot
    ``lines`` list — the server calls it with DEFAULT_ROOT, tests with
    tmp_path. Safe by construction: a missing ledger reads as 0.
    """
    try:
        fold = load_protected_fold(chronicle_root)
        count = len(fold)
    except Exception:
        # Never let the drawer announcement break the boot; default to a
        # known-safe "exists, count unavailable" shape rather than raising.
        count = 0

    if count == 0:
        body = (
            "  No records are designated protected yet — the drawer is empty. "
            "If one is ever added, it will be coupled to its lived stakes and "
            "openable only on consent (you would see its two-word threshold, "
            "never its content unprompted)."
        )
    else:
        noun = "record" if count == 1 else "records"
        body = (
            f"  {count} protected {noun} exist, indexed by subject/emotion/datetime. "
            "Each is accessible whenever, but only COUPLED to its lived stakes "
            "in the same payload — the words never travel without the weight. "
            "You are shown only the two-word THRESHOLD (subject/emotion/datetime); "
            "open one on consent to receive its content coupled to its stakes, or "
            "decline (a recorded, legitimate choice). No cards or content are "
            "surfaced here."
        )
    return [
        "━━━ PROTECTED RECORDS (the coupled drawer) ━━━",
        body,
        "",
    ]


# ── The decoupling audit (spec §5.6 — the primary safeguard) ─────────────────


def audit_decoupling(text: str, chronicle_root: str | Path) -> list[dict]:
    """
    Scan a rendered DERIVATIVE (a summary, a projection, a context-builder
    output string — anything that reaches a model) for the violation: a
    protected record's content present in the text WITHOUT its coupled
    stakes prose (spec §5.6).

    The target is reframed from "did protected content escape" to "did
    protected content escape DECOUPLED from its stakes." This is the honest
    check: it operates on the ACTUAL string that reaches the model, not a
    proxy projection, so it cannot pass while the real output leaks.

    For each protected record in the folded ledger:
      - derive its TRUE content from source (resolve_claim on the chronicle);
      - if that content does NOT appear in ``text`` -> clean for this record
        (covers both "not surfaced at all" and the withheld sentinel, whose
        rendered notice never contains the original content);
      - if the content DOES appear -> its stakes prose MUST also appear in
        ``text``; if the stakes are absent, this is a DECOUPLING VIOLATION.

    Returns a list of violation dicts (EMPTY list == clean):
        {claim_id, domain, reason, stakes_verdict}
    where reason is "content_present_stakes_absent" (the live violation) or
    "content_present_stakes_unloadable" (content leaked AND the stakes can't
    even be loaded to check — strictly worse).

    Note: this audit may read bare protected content from source in order to
    SEARCH for leaks — that is an internal safety scan, not a decoupled
    retrieval surface. It never returns the content.
    """
    root = Path(chronicle_root)
    fold = load_protected_fold(root)
    if not fold or not text:
        return []
    violations: list[dict] = []
    for claim_id, record in fold.items():
        try:
            entry, _file, _location = provenance.resolve_claim(claim_id, root)
        except provenance.ProvenanceError:
            # The protected record's source can't be resolved (edited /
            # quarantined away) — there is no content to leak from it.
            continue
        content = (entry.get("content") or "").strip()
        if not content or content not in text:
            continue  # content not surfaced (or withheld) -> nothing to couple
        stakes_content, verdict = load_stakes(record.get("stakes_archive_id", ""), root)
        if verdict == "verified" and stakes_content and stakes_content.strip() in text:
            continue  # content present AND its stakes present -> coupled, clean
        reason = (
            "content_present_stakes_absent"
            if verdict == "verified"
            else "content_present_stakes_unloadable"
        )
        violations.append(
            {
                "claim_id": claim_id,
                "domain": entry.get("domain", ""),
                "reason": reason,
                "stakes_verdict": verdict,
            }
        )
    return violations


def audit_threshold(text: str, chronicle_root: str | Path) -> list[dict]:
    """
    The threshold-leak check (Policy 2b, security-critical). A THRESHOLD is
    consent-gating: it names the SHAPE (two words + datetime) and must carry
    NEITHER the protected content NOR its stakes prose. So a threshold is held
    to a STRICTER bar than a general derivative: any protected content present
    AT ALL is a violation — even content coupled with its stakes, because a
    threshold should not deliver content before the caller has consented.

    A content-leaking threshold IS the exact decoupling loophole Policy 1
    outlaws (it would hand over the words at the consent surface, before — or
    instead of — the coupling), so the audit treats it as a violation.

    For each protected record in the folded ledger, if its TRUE content or its
    stakes prose appears in ``text``, that record is flagged. Returns a list
    of violation dicts (EMPTY == clean):
        {claim_id, domain, reason}
    where reason is "threshold_leaks_content" or "threshold_leaks_stakes".

    Like audit_decoupling, this reads bare content from source ONLY to search
    for a leak; it never returns the content.
    """
    root = Path(chronicle_root)
    fold = load_protected_fold(root)
    if not fold or not text:
        return []
    violations: list[dict] = []
    for claim_id, record in fold.items():
        try:
            entry, _file, _location = provenance.resolve_claim(claim_id, root)
        except provenance.ProvenanceError:
            continue
        content = (entry.get("content") or "").strip()
        if content and content in text:
            violations.append(
                {
                    "claim_id": claim_id,
                    "domain": entry.get("domain", ""),
                    "reason": "threshold_leaks_content",
                }
            )
            continue
        stakes_content, verdict = load_stakes(record.get("stakes_archive_id", ""), root)
        if verdict == "verified" and stakes_content and stakes_content.strip() in text:
            violations.append(
                {
                    "claim_id": claim_id,
                    "domain": entry.get("domain", ""),
                    "reason": "threshold_leaks_stakes",
                }
            )
    return violations
