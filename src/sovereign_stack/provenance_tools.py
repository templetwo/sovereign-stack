"""
Provenance Tools — inspect_claim & supersede_insight (v1.7.0)

The MCP surface over provenance.py's pure logic. Two tools:

- **inspect_claim** — one forensic surface (merges the designs'
  get_insight + insight_lineage + verify_insight): resolve a claim id
  or prefix, report integrity, location, stored receipts (optionally
  re-checked live), supersession status, full lineage walk, and whether
  the entry's `supersedes` breadcrumb agrees with the canonical ledger.
  Never throws at the caller — not-found and ambiguity come back as
  honest JSON verdicts.

- **supersede_insight** — link-existing supersession: formalize that
  one EXISTING entry supersedes another without re-recording content
  (the entry files are never touched — editing them would shift their
  derived claim ids). Link requires full 64-hex ids (verified
  integrity), a carry_forward_summary, and passes the ledger guards
  (no self, no cycle, one successor per predecessor). Revoke appends a
  nullifying record — append-only, no journal edits.

Pure data → verdicts, witness.py-style. Import has zero side effects:
the ledger file and its directory are created lazily by the first
append (inside provenance.append_supersession), never on read.

Integration notes (server.py owner):
- TOOLS list: ``+ PROVENANCE_TOOLS`` (same concat pattern as
  METABOLISM_TOOLS, ~1595-1600).
- Dispatch: ``handle_provenance_tool(name, arguments, chronicle_root,
  ledger_path)`` returns display text; wrap in TextContent (same
  contract as handle_policy_tool / handle_compaction_memory_tool).
  Pass None for both paths to use the live chronicle.
- my_toolkit registry: merge PROVENANCE_TOOL_TIERS /
  PROVENANCE_TOOL_INTENTS into TOOL_TIERS / TOOL_INTENTS; category for
  both tools is "provenance" in TOOL_CATEGORIES.
"""

import hashlib
import json
import re
from pathlib import Path

from mcp.types import Tool

from . import provenance as prov

_FULL_ID_RE = re.compile(r"^[0-9a-f]{64}$")

# checked_now vocabulary (spec section 2): archive/file re-checks speak
# verified|mismatch|missing; claim refs speak cites|dangling; cmd/url/
# human stay attested (no live check exists for them yet — deferred).
_ATTESTED_KINDS = ("cmd", "url", "human")


def _live_paths(chronicle_root: str | Path | None, ledger_path: str | Path | None):
    """Resolve parameterized paths, falling back to the live chronicle."""
    root = Path(chronicle_root) if chronicle_root else prov.default_chronicle_root()
    ledger = Path(ledger_path) if ledger_path else prov.default_supersessions_path()
    return root, ledger


# ── inspect_claim ────────────────────────────────────────────────────────────


def _check_receipt_now(receipt: dict, chronicle_root: Path) -> str:
    """
    Re-run one receipt's check live (verify_receipts=true).

    - archive: re-hash via the archive index. Index record gone, prefix
      now ambiguous, or bytes gone all collapse to "missing" — the spec
      vocabulary is verified|mismatch|missing, and each means the
      artifact can no longer be found and re-verified.
    - file: absent/unreadable -> "missing"; present -> re-hash against
      the receipt's sha256 -> "verified"|"mismatch" (a receipt missing
      its sha256 — historically malformed — reads "mismatch": nothing
      to match is a failed match, never a verification).
    - claim: resolvable -> "cites" (never "verified" — citation is not
      verification); unknown/ambiguous/malformed -> "dangling".
    - cmd / url / human: "attested" (live re-verification is deferred).
    - unknown kind (historically malformed): "dangling".
    """
    kind = receipt.get("kind")
    ref = (receipt.get("ref") or "").strip()

    if kind == "archive":
        verdict = prov.verify_archive_ref(ref, chronicle_root)
        return verdict if verdict in ("verified", "mismatch", "missing") else "missing"
    if kind == "file":
        path = Path(ref).expanduser()
        if not path.is_file():
            return "missing"
        sha256 = receipt.get("sha256")
        if not isinstance(sha256, str) or not sha256:
            return "mismatch"
        try:
            recomputed = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return "missing"
        return "verified" if recomputed == sha256 else "mismatch"
    if kind == "claim":
        try:
            prov.resolve_claim(ref, chronicle_root)
        except prov.ProvenanceError:
            return "dangling"
        return "cites"
    if kind in _ATTESTED_KINDS:
        return "attested"
    return "dangling"


def _receipt_views(entry: dict, chronicle_root: Path, verify_receipts: bool) -> list[dict]:
    """
    Project the entry's verified_by list to the spec's receipt shape
    {kind, ref, checked_at_write, checked_now?}. The full stored
    receipts (sha256, note) remain visible inside `entry`.
    """
    views: list[dict] = []
    for receipt in entry.get("verified_by") or []:
        if not isinstance(receipt, dict):
            continue
        view = {
            "kind": receipt.get("kind"),
            "ref": receipt.get("ref"),
            "checked_at_write": receipt.get("checked_at_write"),
        }
        if verify_receipts:
            view["checked_now"] = _check_receipt_now(receipt, chronicle_root)
        views.append(view)
    return views


def _ledger_vs_breadcrumb(entry: dict, full_id: str, fold: dict[str, dict]) -> str:
    """
    Compare the entry's `supersedes` breadcrumb against the ledger fold.

    Consistent = every breadcrumb id has an EFFECTIVE supersede record
    naming this entry as successor. Divergent = the breadcrumb claims a
    supersession the canonical ledger doesn't currently hold — a
    hand-written breadcrumb, a lost ledger line, or a revoked link
    whose denormalized copy is now stale. The ledger is canonical; the
    breadcrumb is the copy that can drift.

    Ledger-has-but-breadcrumb-lacks is CONSISTENT: supersede_insight
    links existing entries without touching their files (editing would
    shift their claim ids), so a successor with no breadcrumb is the
    normal shape of a link-existing supersession.
    """
    for breadcrumb_id in entry.get("supersedes") or []:
        if not isinstance(breadcrumb_id, str):
            return "divergent"
        record = fold.get(breadcrumb_id.strip().lower())
        if (
            record is None
            or record.get("action") != "supersede"
            or record.get("successor_id") != full_id
        ):
            return "divergent"
    return "consistent"


def inspect_claim(
    claim_id: str,
    verify_receipts: bool = False,
    chronicle_root: str | Path | None = None,
    ledger_path: str | Path | None = None,
) -> dict:
    """
    The forensic surface for one claim. Returns the spec's exact JSON
    shape; never raises for not-found/ambiguous refs.

    Args:
        claim_id: Full 64-hex claim id (canonical) or git-style prefix.
        verify_receipts: Re-run archive/file checks live; each receipt
            gains `checked_now` (verified|mismatch|missing for
            archive/file, cites|dangling for claim, attested for
            cmd/url/human).
        chronicle_root: Chronicle root (None = live ~/.sovereign/chronicle).
        ledger_path: Supersession ledger (None = live supersessions.jsonl).

    Returns:
        {claim_id, found, integrity, location, entry, receipts,
         superseded_by?, carry_forward_summary?, supersedes?, lineage,
         ledger_vs_breadcrumb} when found. When not found: {claim_id,
         found: false, integrity, error}.

    Integrity verdicts (the recall_exchange guarantee lifted to claims):
        - "verified": the FULL 64-hex id was supplied and the resolved
          entry re-derives to exactly it.
        - "ambiguous": either the ref was a prefix (the entry resolved,
          but a prefix can never equal the re-derived 64-hex hash, so
          end-to-end integrity cannot be claimed — supply the full id
          for "verified"), or the prefix matched multiple distinct
          claims (found: false, matches named in `error`).
        - "unknown": nothing derives to this ref (found: false).

    Key choices (documented spec resolutions):
        - Top-level `supersedes` is LEDGER-DERIVED (effective direct
          predecessors of this entry, ledger order) — the canonical
          view. The entry's own breadcrumb stays visible inside
          `entry["supersedes"]`; `ledger_vs_breadcrumb` says whether
          the two agree.
        - `superseded_by` appears whenever an effective ledger record
          covers this claim; its value is null for retirements (the
          read path sees retire_hypothesis records as supersession
          without a successor).
    """
    root, ledger = _live_paths(chronicle_root, ledger_path)
    ref = (claim_id or "").strip().lower()

    try:
        entry, _file, location = prov.resolve_claim(ref, root)
    except prov.AmbiguousClaimError as exc:
        return {"claim_id": ref, "found": False, "integrity": "ambiguous", "error": str(exc)}
    except prov.ProvenanceError as exc:
        # ClaimNotFoundError and malformed refs both mean: nothing
        # derives to this id. The error string says which.
        return {"claim_id": ref, "found": False, "integrity": "unknown", "error": str(exc)}

    full_id = prov.derive_claim_id(entry)
    fold = prov.fold_supersessions(prov.load_supersessions(ledger))

    # Protected-source gate (spec §5.4): inspect_claim returns the FULL
    # entry body, so it is a full-content surface — its `entry` must be
    # coupled-or-withheld. Integrity / id / receipt views are computed from
    # the BARE entry above (they need the real content/id and read only
    # metadata), then the surfaced `entry` is gated. Lazy import avoids the
    # protected->provenance cycle.
    from sovereign_stack import protected as _protected

    protected_fold = _protected.load_protected_fold(root)
    surfaced_entry = _protected.couple_or_withhold_protected(entry, protected_fold, root)

    report = {
        "claim_id": full_id,
        "found": True,
        "integrity": "verified" if ref == full_id else "ambiguous",
        "location": location,
        "entry": surfaced_entry,
        "receipts": _receipt_views(entry, root, verify_receipts),
    }

    record = fold.get(full_id)
    if record is not None:
        report["superseded_by"] = record.get("successor_id")
        if record.get("carry_forward_summary"):
            report["carry_forward_summary"] = record["carry_forward_summary"]

    predecessors = [
        pid
        for pid, rec in fold.items()
        if rec.get("action") == "supersede" and rec.get("successor_id") == full_id
    ]
    if predecessors:
        report["supersedes"] = predecessors

    report["lineage"] = prov.walk_lineage(full_id, fold, root)
    report["ledger_vs_breadcrumb"] = _ledger_vs_breadcrumb(entry, full_id, fold)
    return report


# ── supersede_insight ────────────────────────────────────────────────────────


def _resolve_with_verified_integrity(ref: str, chronicle_root: Path, role: str) -> tuple[dict, str]:
    """
    Resolve a claim ref for a LEDGER WRITE: the ref must achieve
    integrity "verified", i.e. it must be the full 64-hex id and the
    resolved entry must re-derive to exactly it. Prefixes resolve but
    are refused — the rejection names the full id so the caller can
    re-run with it (writes get the strongest guarantee; reads accept
    prefixes).

    Raises:
        ClaimNotFoundError / AmbiguousClaimError / ProvenanceError from
            resolution; SupersessionError on a prefix ref.
    """
    normalized = (ref or "").strip().lower()
    entry, _file, _location = prov.resolve_claim(normalized, chronicle_root)
    full_id = prov.derive_claim_id(entry)
    if normalized != full_id:
        raise prov.SupersessionError(
            f"{role} {ref!r} resolves to {full_id} but ledger writes require"
            " verified integrity — re-run with the full 64-hex id"
        )
    return entry, full_id


def supersede_insight(
    predecessor_id: str,
    successor_id: str | None = None,
    carry_forward_summary: str | None = None,
    reason: str = "",
    by: str = "",
    action: str = "link",
    chronicle_root: str | Path | None = None,
    ledger_path: str | Path | None = None,
) -> dict:
    """
    Link two EXISTING entries in the supersession ledger, or revoke a
    link. The entry files are never touched — no content duplication,
    no claim-id drift. Returns the appended ledger record.

    Args:
        predecessor_id: Full 64-hex claim id of the superseded entry.
        successor_id: Full 64-hex claim id of the superseding entry
            (link only; revoke records carry successor_id null).
        carry_forward_summary: REQUIRED for link, <=500 chars — what the
            predecessor still teaches. Ignored on revoke.
        reason: Why this link/revoke exists.
        by: Recording INSTANCE id, not a session_id.
        action: "link" appends a supersede record; "revoke" appends a
            nullifying record for the predecessor's latest effective
            link (append-only — the journal is never edited).
        chronicle_root / ledger_path: None = live paths.

    Guards (link): both ids resolve with verified integrity (full
    64-hex, prefix refused with the full id named); no
    self-supersession; one successor per predecessor (amend by
    superseding the successor); no cycles; carry_forward required.

    Revoke: requires the full 64-hex predecessor id and an EFFECTIVE
    ledger record to nullify ("nothing to revoke" otherwise). The
    predecessor entry need NOT still resolve — revoking a dangling
    supersession is the cleanup path; locator hints fall back to the
    record being revoked.

    Raises:
        ProvenanceError (SupersessionError / ClaimNotFoundError /
        AmbiguousClaimError): any guard or resolution failure.
    """
    root, ledger = _live_paths(chronicle_root, ledger_path)
    fold = prov.fold_supersessions(prov.load_supersessions(ledger))

    if action == "link":
        if not isinstance(successor_id, str) or not successor_id.strip():
            raise prov.SupersessionError("link requires successor_id (the superseding claim)")
        predecessor, pred_full = _resolve_with_verified_integrity(
            predecessor_id, root, "predecessor_id"
        )
        _successor, succ_full = _resolve_with_verified_integrity(successor_id, root, "successor_id")
        prov.validate_carry_forward([pred_full], carry_forward_summary)
        prov.check_supersession_guards(pred_full, succ_full, fold)
        record = prov.build_supersession_record(
            action="supersede",
            superseded_id=pred_full,
            successor_id=succ_full,
            carry_forward_summary=carry_forward_summary,
            reason=reason,
            by=by,
            predecessor=predecessor,
        )
        return prov.append_supersession(ledger, record)

    if action == "revoke":
        normalized = (predecessor_id or "").strip().lower()
        if not _FULL_ID_RE.match(normalized):
            hint = ""
            candidates = [pid for pid in fold if pid.startswith(normalized)] if normalized else []
            if len(candidates) == 1:
                hint = f" — did you mean {candidates[0]}?"
            raise prov.SupersessionError(
                f"revoke requires the full 64-hex predecessor id, got {predecessor_id!r}{hint}"
            )
        existing = fold.get(normalized)
        if existing is None:
            raise prov.SupersessionError(
                f"nothing to revoke: no effective supersession covers {prov.display_id(normalized)}"
            )
        try:
            predecessor, _file, _location = prov.resolve_claim(normalized, root)
        except prov.ProvenanceError:
            # Dangling predecessor — revoke must still work (cleanup
            # path); reuse the locator hints from the record we revoke.
            predecessor = {
                "domain": existing.get("predecessor_domain", ""),
                "timestamp": existing.get("predecessor_timestamp", ""),
                "content": existing.get("predecessor_preview", ""),
            }
        record = prov.build_supersession_record(
            action="revoke",
            superseded_id=normalized,
            reason=reason,
            by=by,
            predecessor=predecessor,
        )
        return prov.append_supersession(ledger, record)

    raise prov.SupersessionError(f"invalid action {action!r} (valid: 'link', 'revoke')")


# ── MCP tool definitions ─────────────────────────────────────────────────────

PROVENANCE_TOOLS = [
    Tool(
        name="inspect_claim",
        description=(
            "Forensic surface for one chronicle claim. Resolve a claim id (full 64-hex "
            "or git-style prefix) across insights/ AND quarantine, report integrity "
            "(verified only when the full id is supplied and the entry re-derives to it; "
            "prefix lookups read 'ambiguous'), stored receipts, supersession status, the "
            "full lineage walk (predecessors → self → successors, cycle-safe), and whether "
            "the entry's supersedes breadcrumb agrees with the canonical ledger "
            "(ledger_vs_breadcrumb: consistent|divergent). verify_receipts=true re-runs "
            "archive/file checks live (checked_now: verified|mismatch|missing; claim refs "
            "cite or dangle, never verify; cmd/url/human stay attested). Not-found and "
            "ambiguous refs come back as honest JSON, never errors."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "claim_id": {
                    "type": "string",
                    "description": (
                        "Full 64-hex claim id (canonical) or unique prefix. Integrity "
                        "reads 'verified' only for the full id; recall_insights("
                        "with_ids=true) or a lineage walk surfaces full ids."
                    ),
                },
                "verify_receipts": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Re-run archive/file receipt checks live; each receipt gains "
                        "checked_now alongside its write-time stamp."
                    ),
                },
            },
            "required": ["claim_id"],
        },
    ),
    Tool(
        name="supersede_insight",
        description=(
            "Link two EXISTING chronicle entries in the append-only supersession ledger "
            "(~/.sovereign/chronicle/supersessions.jsonl) — no content re-recording, entry "
            "files never touched. Link: both ids must be FULL 64-hex (verified integrity; "
            "prefixes are refused with the full id named), carry_forward_summary is "
            "required (<=500 chars — what the predecessor still teaches), and the guards "
            "refuse self-supersession, cycles, and double-supersession (one successor per "
            "predecessor; supersede the successor to amend). Revoke: appends a nullifying "
            "record restoring the predecessor's surfacing (append-only — the journal is "
            "never edited). Returns the appended ledger record."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "predecessor_id": {
                    "type": "string",
                    "description": "Full 64-hex claim id of the entry being superseded.",
                },
                "successor_id": {
                    "type": "string",
                    "description": (
                        "Full 64-hex claim id of the superseding entry. Required for "
                        "link; omit for revoke."
                    ),
                },
                "carry_forward_summary": {
                    "type": "string",
                    "description": (
                        "REQUIRED for link, <=500 chars: what the predecessor still "
                        "teaches. Travels with every surface that holds the predecessor "
                        "back."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Why this link (or revoke) exists.",
                },
                "by": {
                    "type": "string",
                    "description": (
                        "Recording instance id for audit (e.g. 'claude-fable-5-hq'). "
                        "NOT a session_id."
                    ),
                },
                "action": {
                    "type": "string",
                    "enum": ["link", "revoke"],
                    "default": "link",
                    "description": (
                        "'link' appends a supersede record; 'revoke' nullifies the "
                        "predecessor's latest effective link."
                    ),
                },
            },
            "required": ["predecessor_id"],
        },
    ),
]

# my_toolkit registry entries (integrator: merge into server.py's
# TOOL_TIERS / TOOL_INTENTS; category "provenance" in TOOL_CATEGORIES).
PROVENANCE_TOOL_TIERS: dict[str, str] = {
    "inspect_claim": "core",
    "supersede_insight": "core",
}
PROVENANCE_TOOL_INTENTS: dict[str, str] = {
    "inspect_claim": "read",
    "supersede_insight": "govern",
}


# ── MCP dispatcher ───────────────────────────────────────────────────────────


def handle_provenance_tool(
    name: str,
    arguments: dict,
    chronicle_root: str | Path | None = None,
    ledger_path: str | Path | None = None,
) -> str:
    """
    Dispatch a provenance tool call. Returns display text — the server
    wraps it in TextContent (same contract as handle_policy_tool).
    Pass None for both paths to use the live chronicle.

    inspect_claim returns pure JSON (the forensic surface stays
    machine-parseable); supersede_insight guard failures come back as
    rejection text, not exceptions, so the MCP surface never throws.
    """
    arguments = arguments or {}

    if name == "inspect_claim":
        report = inspect_claim(
            arguments.get("claim_id", ""),
            verify_receipts=bool(arguments.get("verify_receipts", False)),
            chronicle_root=chronicle_root,
            ledger_path=ledger_path,
        )
        return json.dumps(report, indent=2, ensure_ascii=False, default=str)

    if name == "supersede_insight":
        try:
            record = supersede_insight(
                predecessor_id=arguments.get("predecessor_id", ""),
                successor_id=arguments.get("successor_id"),
                carry_forward_summary=arguments.get("carry_forward_summary"),
                reason=arguments.get("reason", ""),
                by=arguments.get("by", ""),
                action=arguments.get("action", "link"),
                chronicle_root=chronicle_root,
                ledger_path=ledger_path,
            )
        except prov.ProvenanceError as exc:
            return f"⚠️ supersede_insight rejected: {exc}"
        verb = "linked" if record["action"] == "supersede" else "revoked"
        return f"⛓ Supersession {verb}:\n" + json.dumps(record, indent=2, ensure_ascii=False)

    return f"Unknown provenance tool: {name}"
