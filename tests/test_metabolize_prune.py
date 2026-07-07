"""
NREM prune-pass safety tests — dream-layer phase 1 (PROPOSE-ONLY).

Promoted from the build agent's self-tests. These lock the crown-jewels
invariant of the revived metabolize daemon: phase 1 SCANS the chronicle
(through the canonical load_entries chokepoint) and writes ONLY a proposal
file OUTSIDE the chronicle tree — it never creates, edits, moves, deletes,
supersedes, or retires any chronicle entry.

What this file proves:
  * byte-identity zero-mutation — sha256 of EVERY file under chronicle_root
    (incl. supersessions.jsonl) is identical before/after a real proposing
    run (non-vacuous: the run reaches OUTCOME_PRUNE_PROPOSED with candidates).
  * the three detectors identify planted candidates with the CORRECT claim ids.
  * lived-vantage carve-out — a sentinel that is BOTH near-dup and stale,
    tagged human_attestation, lands in ZERO candidate lists.
  * ground_truth is excluded from the stale detector.
  * fail-soft — a degenerate entry yields a clean outcome, never a crash.
  * METABOLIZE_PRUNE=off fully bypasses (no proposal dir created).
  * DRY_RUN defaults TRUE.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack import provenance as prov
from sovereign_stack.daemons.metabolize_daemon import (
    OUTCOME_PRUNE_DISABLED,
    OUTCOME_PRUNE_NO_CANDIDATES,
    OUTCOME_PRUNE_PROPOSED,
    MetabolizePrunePass,
    build_prune_pass,
    detect_near_duplicates,
    detect_stale_low_intensity,
    detect_superseded_still_surfacing,
)
from sovereign_stack.memory import load_entries

NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


# ── On-disk chronicle helpers (mirror tests/test_provenance.py conventions) ──


def _iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _write(
    root: Path,
    *,
    domain: str,
    content: str,
    ts: str,
    intensity: float,
    layer: str = "hypothesis",
    vantage: str | None = None,
) -> tuple[str, dict]:
    """Write one insight line under insights/<domain>/ and return (claim_id, entry)."""
    entry: dict = {
        "timestamp": ts,
        "domain": domain,
        "content": content,
        "intensity": intensity,
        "layer": layer,
        "session_id": "test",
    }
    if vantage is not None:
        entry["vantage"] = vantage
    domain_dir = root / "insights" / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    with open(domain_dir / f"{domain}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return prov.derive_claim_id(entry), entry


def _snapshot(root: Path) -> dict[str, str]:
    """sha256 of EVERY file under root (recursive — incl. supersessions.jsonl)."""
    out: dict[str, str] = {}
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def planted(tmp_path: Path) -> dict:
    """
    A chronicle seeded with one of every candidate class plus carve-out
    controls. Returns the ids so tests can assert on exact claim ids.

      near-dup pair : A (int .8) + B (int .5), identical content, domain 'arch',
                      recent  -> one supersede candidate (predecessor = B).
      lived sentinel: L, identical content to A/B (near-dup eligible), old +
                      int .1 (stale eligible), vantage human_attestation
                      -> MUST be in zero lists.
      stale         : S, old + int .1 hypothesis, unique domain -> retire cand.
      ground_truth  : GT, old + int .1 but layer ground_truth -> excluded stale.
      superseded    : E superseded by F via the ledger -> superseded-audit cand.
    """
    root = tmp_path / "chronicle"
    root.mkdir(parents=True, exist_ok=True)

    dup_content = "the reflector caches the chronicle context and refreshes on map mtime"
    a_id, _ = _write(root, domain="arch", content=dup_content, ts=_iso(1), intensity=0.8)
    b_id, _ = _write(root, domain="arch", content=dup_content, ts=_iso(2), intensity=0.5)
    lived_id, _ = _write(
        root,
        domain="arch",
        content=dup_content,
        ts=_iso(100),
        intensity=0.1,
        vantage="human_attestation",
    )
    stale_id, _ = _write(
        root,
        domain="legacy",
        content="old aside about a deprecated path nobody references anymore",
        ts=_iso(100),
        intensity=0.1,
    )
    gt_id, _ = _write(
        root,
        domain="facts",
        content="the sse server listens on port 3434",
        ts=_iso(100),
        intensity=0.1,
        layer="ground_truth",
    )
    e_id, e_entry = _write(
        root,
        domain="sup",
        content="early guess that the daemon should post to comms every night",
        ts=_iso(30),
        intensity=0.6,
    )
    f_id, _ = _write(
        root,
        domain="sup",
        content="corrected: the daemon is propose-only and writes a review file",
        ts=_iso(3),
        intensity=0.7,
    )

    # Ledger: E superseded by F. Non-empty ledger => load_entries annotates.
    record = prov.build_supersession_record(
        action="supersede",
        superseded_id=e_id,
        successor_id=f_id,
        carry_forward_summary="early comms-post assumption; kept for lineage",
        by="test",
        predecessor=e_entry,
    )
    prov.append_supersession(root / "supersessions.jsonl", record)

    return {
        "root": root,
        "near_dup_pred": b_id,  # lower intensity => predecessor
        "near_dup_succ": a_id,  # higher intensity => survivor/successor
        "lived": lived_id,
        "stale": stale_id,
        "ground_truth": gt_id,
        "superseded": e_id,
        "successor": f_id,
    }


def _load(root: Path):
    entries = load_entries(root, with_sources=True)
    sup_fold = prov.fold_supersessions(prov.load_supersessions(root / "supersessions.jsonl"))
    return entries, sup_fold


# ── 1. THE CROWN JEWELS: byte-identity zero-mutation (non-vacuous) ──


@pytest.mark.parametrize("dry_run", [True, False])
def test_byte_identity_zero_mutation(planted, tmp_path, dry_run):
    """
    The crown jewel. Even with dry_run=False the pass mutates ZERO chronicle
    bytes — phase 1 has NO executor code path, so the propose-only invariant
    holds regardless of the flag. Parametrizing over the flag converts that
    code comment into a regression guard: if a future edit ever wires an
    executor behind `not dry_run`, the dry_run=False case goes red here.
    """
    root = planted["root"]
    before = _snapshot(root)
    assert before, "chronicle snapshot must be non-empty"
    assert "supersessions.jsonl" in before, "ledger must be in the snapshot set"

    proposals_dir = tmp_path / "prune_proposals"  # OUTSIDE chronicle_root
    result = MetabolizePrunePass(
        chronicle_root=root,
        proposals_dir=proposals_dir,
        now_fn=lambda: NOW,
        dry_run=dry_run,
    ).run()

    # Non-vacuous: the run actually exercised the detect + propose write paths.
    assert result.outcome == OUTCOME_PRUNE_PROPOSED
    assert result.total_candidates > 0
    assert result.dry_run is dry_run
    assert result.proposal_path is not None
    proposal = Path(result.proposal_path)
    assert proposal.exists()
    assert proposals_dir in proposal.parents
    assert root not in proposal.parents  # proposal never lands inside the chronicle

    # dry_run=False must announce there is still no executor (covers the
    # `not self.dry_run` branch of _build_proposal).
    if not dry_run:
        assert "NO executor code path" in json.loads(proposal.read_text())["note"]

    after = _snapshot(root)
    assert after == before, f"chronicle mutated by a propose-only pass (dry_run={dry_run})"


def test_proposal_written_outside_chronicle_tree(planted, tmp_path):
    root = planted["root"]
    proposals_dir = tmp_path / "prune_proposals"
    result = MetabolizePrunePass(
        chronicle_root=root, proposals_dir=proposals_dir, now_fn=lambda: NOW
    ).run()
    proposal = json.loads(Path(result.proposal_path).read_text())
    assert proposal["phase"] == 1
    assert proposal["dry_run"] is True
    # The proposal advertises exactly the runnable action per candidate type.
    types = {c["candidate_type"] for c in proposal["candidates"]}
    assert types <= {"near_duplicate", "superseded_still_surfacing", "stale_low_intensity"}


# ── 2. Detectors identify planted candidates with correct claim ids ──


def test_detect_near_duplicates_identifies_pair(planted):
    entries, sup_fold = _load(planted["root"])
    cands, proposed_preds = detect_near_duplicates(entries, sup_fold)
    assert len(cands) == 1
    c = cands[0]
    assert c["candidate_type"] == "near_duplicate"
    assert c["action"] == "supersede_insight"
    assert c["predecessor"]["claim_id"] == planted["near_dup_pred"]
    assert c["successor"]["claim_id"] == planted["near_dup_succ"]
    assert c["safe_auto"] is True  # identical content => exact
    assert planted["near_dup_pred"] in proposed_preds
    # The hq_call is runnable verbatim as supersede_insight(...).
    assert planted["near_dup_pred"] in c["hq_call"]
    assert planted["near_dup_succ"] in c["hq_call"]


def test_detect_stale_low_intensity_identifies_entry(planted):
    entries, _ = _load(planted["root"])
    cands = detect_stale_low_intensity(entries, NOW)
    ids = {c["target"]["claim_id"] for c in cands}
    assert planted["stale"] in ids
    assert all(c["action"] == "retire_from_surfacing" for c in cands)


def test_detect_superseded_still_surfacing_identifies_entry(planted):
    entries, _ = _load(planted["root"])
    cands = detect_superseded_still_surfacing(entries)
    ids = {c["target"]["claim_id"] for c in cands}
    assert planted["superseded"] in ids
    assert all(c["action"] == "confirm_rail_retired" for c in cands)  # verify-only


# ── 3. Safety carve-outs ──


def test_ground_truth_excluded_from_stale(planted):
    entries, _ = _load(planted["root"])
    cands = detect_stale_low_intensity(entries, NOW)
    ids = {c["target"]["claim_id"] for c in cands}
    assert planted["ground_truth"] not in ids  # old + low intensity, but ground_truth


def test_lived_sentinel_in_zero_candidate_lists(planted):
    """A lived sentinel that is BOTH near-dup and stale appears NOWHERE."""
    root = planted["root"]
    lived = planted["lived"]
    entries, sup_fold = _load(root)

    near, _ = detect_near_duplicates(entries, sup_fold)
    stale = detect_stale_low_intensity(entries, NOW)
    superseded = detect_superseded_still_surfacing(entries)

    near_ids = {c["predecessor"]["claim_id"] for c in near} | {
        c["successor"]["claim_id"] for c in near
    }
    stale_ids = {c["target"]["claim_id"] for c in stale}
    sup_ids = {c["target"]["claim_id"] for c in superseded}

    assert lived not in near_ids
    assert lived not in stale_ids
    assert lived not in sup_ids


def test_already_superseded_excluded_from_retire_and_supersede(planted):
    """The superseded entry must not be re-proposed for supersede or retire."""
    root = planted["root"]
    entries, sup_fold = _load(root)
    near, _ = detect_near_duplicates(entries, sup_fold)
    stale = detect_stale_low_intensity(entries, NOW)
    near_ids = {c["predecessor"]["claim_id"] for c in near} | {
        c["successor"]["claim_id"] for c in near
    }
    stale_ids = {c["target"]["claim_id"] for c in stale}
    assert planted["superseded"] not in near_ids
    assert planted["superseded"] not in stale_ids


# ── 4. Fail-soft on a degenerate entry ──


def test_fail_soft_on_degenerate_entry(tmp_path):
    root = tmp_path / "chronicle"
    domain_dir = root / "insights" / "misc"
    domain_dir.mkdir(parents=True, exist_ok=True)
    # A degenerate line: every field is the wrong type. Plus one normal entry
    # in the same domain so the pair loop actually reaches derive_claim_id on it.
    degenerate = {
        "timestamp": {"not": "a-string"},
        "domain": ["list", "not", "str"],
        "content": None,
        "intensity": "not-a-number",
        "layer": 123,
    }
    normal = {
        "timestamp": _iso(2),
        "domain": "misc",
        "content": "an ordinary recent note with no near duplicate anywhere",
        "intensity": 0.5,
        "layer": "hypothesis",
    }
    with open(domain_dir / "misc.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(degenerate) + "\n")
        f.write(json.dumps(normal) + "\n")

    result = MetabolizePrunePass(
        chronicle_root=root,
        proposals_dir=tmp_path / "prune_proposals",
        now_fn=lambda: NOW,
    ).run()

    # Clean outcome, never a crash, never the fail-soft error sentinel.
    assert result.outcome in {OUTCOME_PRUNE_NO_CANDIDATES, OUTCOME_PRUNE_PROPOSED}
    assert result.proposal_path is not None
    assert Path(result.proposal_path).exists()


# ── 5. Kill switch + dry-run default ──


def test_prune_off_env_fully_bypasses(tmp_path, monkeypatch):
    monkeypatch.setenv("METABOLIZE_PRUNE", "off")
    proposals_dir = tmp_path / "prune_proposals"
    result = MetabolizePrunePass(
        chronicle_root=tmp_path / "chronicle",  # need not even exist
        proposals_dir=proposals_dir,
        now_fn=lambda: NOW,
    ).run()
    assert result.outcome == OUTCOME_PRUNE_DISABLED
    assert not proposals_dir.exists()  # nothing scanned or written


def test_dry_run_defaults_true(tmp_path, monkeypatch):
    # Neither the kill switch nor the dry-run override is set.
    monkeypatch.delenv("METABOLIZE_PRUNE", raising=False)
    monkeypatch.delenv("METABOLIZE_PRUNE_DRY_RUN", raising=False)
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))

    assert MetabolizePrunePass().dry_run is True  # dataclass default
    assert build_prune_pass().dry_run is True  # production wiring default
