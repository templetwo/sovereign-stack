"""
Supersession Candidate Detector — prose-only markers, human-review-only.

Some chronicle shards assert supersession in PROSE ONLY (text like
"SUPERSEDES", "CORRECTED", "DEFINITIVE" in the entry body) with no
matching record in supersessions.jsonl — invisible to fold_supersessions,
to the read path, to every cold reader. This module is a detector, not
an actuator: it emits (marker, best_overlap_predecessor, jaccard_score)
triples for a HUMAN to review and land through the existing guarded
supersede_insight path with a written carry_forward_summary. It never
writes to the chronicle or the ledger, and it has no chronicle_root
default of its own (see run_against_chronicle) — nothing in this module
can reach ~/.sovereign unless a caller explicitly hands it that path.

ZERO AUTO-PROMOTION. The marker regex (and Jaccard token overlap) cannot
distinguish an entry that SUPERSEDES a prior claim from one that QUOTES
OR DISCUSSES supersession — an entry describing the ledger, the marker
mechanism, or a correction someone else made reads, lexically, exactly
like one performing a correction. Expected precision is moderate by
construction. Every hit needs a human's eyes before anything is landed;
this module cannot land anything even if asked to.

Reuses (never duplicates or edits) the pure primitives in provenance.py:
iter_chronicle_entries, derive_claim_id, display_id, token_overlap,
load_supersessions. It does NOT reuse has_legacy_marker — see
PROSE_MARKER_RE below for why — and it does not touch provenance.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sovereign_stack.provenance import (
    derive_claim_id,
    display_id,
    iter_chronicle_entries,
    load_supersessions,
    token_overlap,
)

# Case-insensitive by deliberate departure from provenance.LEGACY_MARKER_RE.
#
# LEGACY_MARKER_RE = re.compile(r"CORRECTED|DEFINITIVE|supersedes") is
# case-SENSITIVE: it matches literal "CORRECTED", "DEFINITIVE" (upper
# case only) and "supersedes" (lower case only). It does NOT match
# "SUPERSEDES" — the exact spelling this project's own brief uses, and
# the spelling a real fraction of chronicle entries use ("THIS
# SUPERSEDES the ...", "SUPERSEDES the stray 'test' entry ..."). A
# case-sensitive scan against this chronicle finds 62 marker entries;
# the case-insensitive version below finds 223 — a >3x recall gap
# caused entirely by casing, not by any judgment about what counts as
# a marker. Widening LEGACY_MARKER_RE in place would change
# season_review's own section-1 behavior, which is out of this
# detector's delta — so the wider predicate lives here instead, named
# distinctly, so nobody mistakes the two for interchangeable.
PROSE_MARKER_RE = re.compile(r"CORRECTED|DEFINITIVE|SUPERSEDES", re.IGNORECASE)

# Calibrated against this chronicle's real score distribution (see the
# detector's report), not chosen a priori: at 223 prose-only-candidate
# markers, best-overlap-predecessor scores run median ~0.24, p75 ~0.29,
# p90 ~0.34, max ~0.92. season_review's own SUPERSESSION_OVERLAP (0.5)
# is the right bar for its cross-domain-pair sweep but would leave this
# population's candidate file all but empty (2-4 hits) — not because
# the population lacks real pairs, but because most chronicle prose
# doesn't restate its predecessor's exact wording. 0.3 keeps the queue
# a moderate-precision, moderate-recall human-review list rather than
# either an empty file or a flood.
DEFAULT_MIN_SCORE = 0.3


def _field(entry: dict, key: str) -> str:
    """Same absent/None-tolerant accessor convention as provenance._preimage_field."""
    value = entry.get(key, "")
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def is_prose_marker(entry: dict) -> bool:
    """True when entry's domain or content carries a case-insensitive
    CORRECTED / DEFINITIVE / SUPERSEDES marker."""
    return bool(
        PROSE_MARKER_RE.search(_field(entry, "domain"))
        or PROSE_MARKER_RE.search(_field(entry, "content"))
    )


def find_prose_only_markers(entries: list[dict], sup_records: list[dict]) -> list[dict]:
    """
    Entries that assert supersession in prose (is_prose_marker) and are
    NOT already landed as a formal ledger successor.

    "Already landed" means: this exact entry's claim id already appears
    as a successor_id in supersessions.jsonl — i.e. someone already ran
    supersede_insight naming it as the corrective claim, so its prose
    assertion is already machine-readable and out of scope for this
    detector. Checked against the raw record list, not the folded
    state: a later "revoke" record would drop the predecessor's fold
    entry, but the successor_id was still genuinely spent by a real
    supersede action once, so it should not re-enter the candidate
    pool on that account.
    """
    successor_ids_ever = {r.get("successor_id") for r in sup_records if r.get("successor_id")}
    return [
        e for e in entries if is_prose_marker(e) and derive_claim_id(e) not in successor_ids_ever
    ]


def best_overlap_predecessor(
    marker: dict,
    pool: list[dict],
    *,
    require_older: bool = True,
) -> tuple[dict | None, float]:
    """
    Highest-token-Jaccard entry in `pool` against `marker`, excluding
    marker itself by identity (two entries with byte-identical content
    share a claim_id and are still legitimately distinct pool members,
    so identity, not equality, is what's excluded).

    require_older=True (default) additionally requires the candidate's
    timestamp to sort strictly before the marker's — a "predecessor"
    cannot come chronologically after the entry naming it. Timestamps
    compare as ISO-8601 strings (lexical order == chronological order
    for this chronicle's uniform format); a missing/empty timestamp on
    either side never satisfies the ordering, so that pairing is
    skipped rather than guessed at.

    Returns (None, 0.0) when nothing in the pool scores above zero.
    """
    best_entry: dict | None = None
    best_score = 0.0
    marker_ts = _field(marker, "timestamp")
    marker_content = marker.get("content", "")
    for other in pool:
        if other is marker:
            continue
        if require_older:
            other_ts = _field(other, "timestamp")
            if not (other_ts and marker_ts and other_ts < marker_ts):
                continue
        score = token_overlap(marker_content, other.get("content", ""))
        if score > best_score:
            best_score = score
            best_entry = other
    return best_entry, best_score


@dataclass(frozen=True)
class Candidate:
    """One (marker, best_overlap_predecessor, jaccard_score) triple."""

    marker_id: str
    marker_domain: str
    marker_timestamp: str
    predecessor_id: str
    predecessor_domain: str
    predecessor_timestamp: str
    jaccard_score: float


def detect_candidates(
    entries: list[dict],
    sup_records: list[dict],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    require_older: bool = True,
) -> tuple[list[Candidate], list[dict]]:
    """
    The detector's one entry point. Pure function: no I/O, no writes,
    no chronicle_root, touches no filesystem. Callers load entries
    read-only (e.g. via iter_chronicle_entries) and decide what a human
    does with the output — this function cannot supersede, retire, or
    revoke anything; it has no access to append_supersession.

    Returns (candidates, unrecoverable):
      - candidates: one Candidate per prose-only marker whose best
        overlap predecessor scores >= min_score, sorted by
        jaccard_score descending.
      - unrecoverable: the prose-only marker entries themselves (not
        Candidates) whose best score fell below min_score — "no
        locatable predecessor". These stay prose; this function does
        not guess a pairing just to have one, and for some of them
        absence IS the honest, correct output.
    """
    markers = find_prose_only_markers(entries, sup_records)
    candidates: list[Candidate] = []
    unrecoverable: list[dict] = []
    for marker in markers:
        pred, score = best_overlap_predecessor(marker, entries, require_older=require_older)
        if pred is None or score < min_score:
            unrecoverable.append(marker)
            continue
        candidates.append(
            Candidate(
                marker_id=display_id(derive_claim_id(marker)),
                marker_domain=_field(marker, "domain"),
                marker_timestamp=_field(marker, "timestamp"),
                predecessor_id=display_id(derive_claim_id(pred)),
                predecessor_domain=_field(pred, "domain"),
                predecessor_timestamp=_field(pred, "timestamp"),
                jaccard_score=score,
            )
        )
    candidates.sort(key=lambda c: c.jaccard_score, reverse=True)
    return candidates, unrecoverable


def render_candidates_markdown(
    candidates: list[Candidate],
    unrecoverable: list[dict],
    *,
    min_score: float,
    total_markers: int,
) -> str:
    """
    Human-readable review queue. Structural fields ONLY — claim id,
    domain, timestamp, score — never a content preview, by deliberate
    choice beyond the brief's "short structural previews only"
    allowance: several markers in this population sit in family and
    personal domains, and a reviewer with legitimate chronicle access
    already has inspect_claim(claim_id) to read the full text in
    context. Omitting content entirely removes any chance this file
    repeats personal content out of context.
    """
    lines = [
        "# Supersession candidate detector — human review queue",
        "",
        "ZERO AUTO-PROMOTION. Nothing below has been superseded, retired,",
        "or revoked — this file is a list of guesses, not a ledger.",
        "Read both entries with inspect_claim(<id>) and judge with your",
        "own eyes before doing anything. A wrong retirement is worse",
        "than a missing one.",
        "",
        f"Prose-only markers scanned: {total_markers}",
        f"Candidates emitted (score >= {min_score}): {len(candidates)}",
        f"Stayed prose (no locatable predecessor >= {min_score}): {len(unrecoverable)}",
        "",
        "## Candidates, sorted by confidence (highest first)",
        "",
    ]
    if not candidates:
        lines.append("(none)")
    for c in candidates:
        lines.append(
            f"- score {c.jaccard_score:.3f} — marker `{c.marker_id}` "
            f"[{c.marker_domain}] @ {c.marker_timestamp}"
        )
        lines.append(
            f"    predecessor `{c.predecessor_id}` [{c.predecessor_domain}] "
            f"@ {c.predecessor_timestamp}"
        )
        lines.append(
            f'    review: inspect_claim("{c.marker_id}"), inspect_claim("{c.predecessor_id}")'
        )
        lines.append(
            f'    if confirmed: supersede_insight(predecessor_id="{c.predecessor_id}", '
            f'successor_id="{c.marker_id}", '
            'carry_forward_summary="<what the predecessor still teaches>")'
        )
        lines.append("")

    lines += [
        "## Stayed prose — no locatable predecessor (id / domain / timestamp only)",
        "",
    ]
    if not unrecoverable:
        lines.append("(none)")
    for m in unrecoverable:
        lines.append(
            f"- `{display_id(derive_claim_id(m))}` [{_field(m, 'domain')}] "
            f"@ {_field(m, 'timestamp')}"
        )
    return "\n".join(lines) + "\n"


def run_against_chronicle(
    chronicle_root: str | Path,
    output_path: str | Path,
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    require_older: bool = True,
) -> tuple[list[Candidate], list[dict]]:
    """
    Read-only end to end: scan `chronicle_root` (never writes there —
    only iter_chronicle_entries and load_supersessions touch it, both
    pure reads) and write the human-review file to `output_path`.

    This function does not itself refuse a chronicle_root under
    ~/.sovereign or an output_path under it — callers driving this
    against the live store must pass a worktree/tmp output_path
    themselves. It is read-only by construction (no write call touches
    chronicle_root), which is the invariant that actually matters here.

    Protected-source withholding (spec section 5.4): a designated
    protected record is excluded from the scan entirely, mirroring
    season_review's own content-based-scan filter (seasons.py). This
    detector's structural render never surfaces content, but
    detect_candidates hands back full entry dicts (Candidate is built
    from them, and the unrecoverable list IS the raw entries) to
    whatever calls it in-process — a protected record has no business
    entering that pool at all, so it is dropped here before detection
    runs rather than trusted to a downstream renderer to withhold.
    Import is lazy (protected.py imports provenance at module level;
    a top-level import here would risk a cycle as the codebase already
    works around elsewhere).
    """
    from sovereign_stack.protected import load_protected_fold

    root = Path(chronicle_root)
    scanned = list(iter_chronicle_entries(root))
    entries = [e for e, _f, loc in scanned if loc == "insights"]
    protected_fold = load_protected_fold(root)
    if protected_fold:
        entries = [e for e in entries if derive_claim_id(e) not in protected_fold]
    sup_records = load_supersessions(root / "supersessions.jsonl")
    candidates, unrecoverable = detect_candidates(
        entries, sup_records, min_score=min_score, require_older=require_older
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_candidates_markdown(
            candidates,
            unrecoverable,
            min_score=min_score,
            total_markers=len(find_prose_only_markers(entries, sup_records)),
        ),
        encoding="utf-8",
    )
    return candidates, unrecoverable
