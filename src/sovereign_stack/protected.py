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
from datetime import datetime, timezone
from pathlib import Path

from sovereign_stack import provenance

# ── Constants ────────────────────────────────────────────────────────────────

PROTECTED_ACTIONS = ("protect", "unprotect")

# Verdict vocabulary for a stakes load, identical to the archive layer's
# recall_exchange / verify_archive_ref vocabulary.
STAKES_VERDICTS = ("verified", "mismatch", "missing", "ambiguous", "unknown")


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
    action: str = "protect",
    reason: str = "",
    by: str = "",
    timestamp: str | None = None,
) -> dict:
    """
    Build one protected-ledger record in the canonical schema. Validates
    the gate + id core so a hand-built record can't poison the fold.

    Raises:
        ProtectedGateError: designated_by missing/empty (the human gate).
        ProtectedError: invalid action, non-64-hex claim_id, or a
            ``protect`` record without a stakes_archive_id pointer.
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
    if action == "protect" and (
        not isinstance(stakes_archive_id, str) or not stakes_archive_id.strip()
    ):
        raise ProtectedError(
            "protect requires stakes_archive_id — the pointer to the"
            " archive-coupled stakes prose (the coupling vehicle)"
        )
    return {
        "action": action,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "claim_id": claim_id,
        "stakes_archive_id": stakes_archive_id.strip() if stakes_archive_id else None,
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
