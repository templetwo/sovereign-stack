"""
grounded_extract tests — Step 2 of the v1.3.2 reflection-daemons lineage.

The load-bearing check is layer-awareness: hypothesis-only chronicle files
must NOT ground a claim, because reflection-on-hypothesis produces
reinforced hypothesis (the Huang et al. ICLR 2024 / Jain et al. MIT 2026
contamination failure mode). These tests exercise every path through
_classify_chronicle_path plus the REASON_* code surface that daemons will
branch on.
"""

import json
import os
import shutil
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sovereign_stack.grounding import (
    GroundingResult,
    grounded_extract,
    # Claim-level reason codes
    REASON_OK,
    REASON_NO_EVIDENCE,
    REASON_NO_GROUND_TRUTH,
    REASON_INSUFFICIENT_EVIDENCE,
    # Per-path reject codes
    PATH_MISSING,
    PATH_HYPOTHESIS_ONLY,
    PATH_OPEN_THREAD_ONLY,
    PATH_UNREADABLE,
    # Layer names
    GROUND_TRUTH_LAYER,
    HYPOTHESIS_LAYER,
    OPEN_THREAD_LAYER,
)


@pytest.fixture
def chronicle_root():
    """Sandboxed chronicle root so tests don't touch ~/.sovereign."""
    tmp = tempfile.mkdtemp()
    root = Path(tmp) / "chronicle"
    (root / "insights").mkdir(parents=True)
    yield root
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def outside_path():
    """An existing file outside the chronicle root — structural evidence."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    )
    tmp.write("# real source file\n")
    tmp.close()
    yield Path(tmp.name)
    os.unlink(tmp.name)


def _write_jsonl(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _chronicle_file(root: Path, domain: str, layers: list) -> Path:
    """
    Write a chronicle JSONL under root/insights/<domain>/test.jsonl with one
    record per layer in `layers`. Returns the file path.
    """
    target = root / "insights" / domain / "test.jsonl"
    records = [
        {"timestamp": "2026-01-01T00:00:00", "domain": domain,
         "content": f"r{i}", "layer": layer}
        for i, layer in enumerate(layers)
    ]
    _write_jsonl(target, records)
    return target


# ── Case 1: empty evidence ───────────────────────────────────────────────────

def test_empty_evidence_rejected(chronicle_root):
    r = grounded_extract("c", [], chronicle_root=chronicle_root)
    assert not r
    assert r.reason is REASON_NO_EVIDENCE
    assert r.matched_paths == []
    assert r.rejected_paths == []


# ── Case 2: missing path ─────────────────────────────────────────────────────

def test_missing_path_rejected_as_missing(chronicle_root):
    r = grounded_extract(
        "c",
        ["/definitely/not/a/real/path.txt"],
        chronicle_root=chronicle_root,
    )
    assert not r
    assert r.reason is REASON_NO_GROUND_TRUTH
    assert len(r.rejected_paths) == 1
    assert r.rejected_paths[0]["reason"] is PATH_MISSING


# ── Case 3: non-chronicle existing file = structural evidence ────────────────

def test_non_chronicle_existing_file_is_structural_evidence(
    chronicle_root, outside_path
):
    r = grounded_extract("c", [str(outside_path)], chronicle_root=chronicle_root)
    assert r.accepted
    assert r.reason is REASON_OK
    assert str(outside_path) in r.matched_paths
    assert r.rejected_paths == []


# ── Case 4: chronicle ground_truth only = accepted ───────────────────────────

def test_chronicle_ground_truth_only_accepted(chronicle_root):
    p = _chronicle_file(chronicle_root, "x", [GROUND_TRUTH_LAYER])
    r = grounded_extract("c", [str(p)], chronicle_root=chronicle_root)
    assert r.accepted
    assert r.reason is REASON_OK


# ── Case 5: chronicle hypothesis only = rejected (LOAD-BEARING) ─────────────

def test_chronicle_hypothesis_only_rejected(chronicle_root):
    """The load-bearing test: a hypothesis-only chronicle file must NOT
    ground a claim. This is the contamination guardrail."""
    p = _chronicle_file(chronicle_root, "x", [HYPOTHESIS_LAYER])
    r = grounded_extract("c", [str(p)], chronicle_root=chronicle_root)
    assert not r
    assert r.reason is REASON_NO_GROUND_TRUTH
    assert len(r.rejected_paths) == 1
    assert r.rejected_paths[0]["reason"] is PATH_HYPOTHESIS_ONLY


# ── Case 6: chronicle open_thread only = rejected ───────────────────────────

def test_chronicle_open_thread_only_rejected(chronicle_root):
    p = _chronicle_file(chronicle_root, "x", [OPEN_THREAD_LAYER])
    r = grounded_extract("c", [str(p)], chronicle_root=chronicle_root)
    assert not r
    assert r.rejected_paths[0]["reason"] is PATH_OPEN_THREAD_ONLY


# ── Case 7: mixed chronicle file (ground_truth + hypothesis) = accepted ─────

def test_chronicle_mixed_layers_accepted_if_any_ground_truth(chronicle_root):
    """A JSONL file containing both hypothesis AND ground_truth records is
    accepted — the presence of any ground_truth record grounds the file."""
    p = _chronicle_file(
        chronicle_root, "x",
        [HYPOTHESIS_LAYER, GROUND_TRUTH_LAYER, HYPOTHESIS_LAYER],
    )
    r = grounded_extract("c", [str(p)], chronicle_root=chronicle_root)
    assert r.accepted
    assert r.reason is REASON_OK


# ── Case 8: min_evidence_paths threshold ─────────────────────────────────────

def test_min_evidence_paths_insufficient(chronicle_root, outside_path):
    """One grounding path, min=2 → insufficient_evidence (not no_ground_truth,
    since something did ground but not enough)."""
    r = grounded_extract(
        "c",
        [str(outside_path)],
        chronicle_root=chronicle_root,
        min_evidence_paths=2,
    )
    assert not r
    assert r.reason is REASON_INSUFFICIENT_EVIDENCE
    assert len(r.matched_paths) == 1


def test_min_evidence_paths_met(chronicle_root, outside_path):
    """Two grounding paths, min=2 → accepted."""
    p = _chronicle_file(chronicle_root, "x", [GROUND_TRUTH_LAYER])
    r = grounded_extract(
        "c",
        [str(outside_path), str(p)],
        chronicle_root=chronicle_root,
        min_evidence_paths=2,
    )
    assert r.accepted
    assert len(r.matched_paths) == 2


# ── Case 9: __bool__ dunder ──────────────────────────────────────────────────

def test_bool_dunder_returns_accepted(chronicle_root, outside_path):
    accepted = grounded_extract(
        "c", [str(outside_path)], chronicle_root=chronicle_root,
    )
    assert bool(accepted) is True

    rejected = grounded_extract("c", [], chronicle_root=chronicle_root)
    assert bool(rejected) is False


def test_daemon_idiom_if_not_result(chronicle_root):
    """The documented daemon-side idiom: `if not result: return`."""
    result = grounded_extract("c", [], chronicle_root=chronicle_root)
    branched = False
    if not result:
        branched = True
    assert branched


# ── Case 10: reason code stability ───────────────────────────────────────────

def test_reason_codes_are_stable_strings():
    """Reason codes must be stable importable constants so daemons can
    branch on identity. Catches accidental renames."""
    assert REASON_OK == "ok"
    assert REASON_NO_EVIDENCE == "no_evidence"
    assert REASON_NO_GROUND_TRUTH == "no_ground_truth"
    assert REASON_INSUFFICIENT_EVIDENCE == "insufficient_evidence"
    assert PATH_MISSING == "missing"
    assert PATH_HYPOTHESIS_ONLY == "hypothesis_only"
    assert PATH_OPEN_THREAD_ONLY == "open_thread_only"
    assert PATH_UNREADABLE == "unreadable"


# ── Case 11: unreadable / malformed JSONL ────────────────────────────────────

def test_unreadable_chronicle_file_rejected(chronicle_root):
    """A chronicle path that's a binary file or otherwise unparseable is
    rejected with PATH_UNREADABLE — not silently accepted."""
    target = chronicle_root / "insights" / "x" / "binary.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00\x01\xff\xfe not json at all")
    r = grounded_extract("c", [str(target)], chronicle_root=chronicle_root)
    # Not strictly unreadable (bytes decoded might succeed), but the records
    # won't parse as JSON, so layers={} → hypothesis_only rejection.
    assert not r
    # Accept either classification — both mean "not grounding".
    assert r.rejected_paths[0]["reason"] in (PATH_UNREADABLE, PATH_HYPOTHESIS_ONLY)


def test_malformed_json_lines_ignored(chronicle_root):
    """Lines that fail to parse as JSON are skipped. If even one valid
    ground_truth record remains, the file still grounds."""
    target = chronicle_root / "insights" / "x" / "mixed.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as f:
        f.write("not json at all\n")
        f.write(json.dumps({"layer": GROUND_TRUTH_LAYER, "content": "ok"}) + "\n")
        f.write("{broken\n")
    r = grounded_extract("c", [str(target)], chronicle_root=chronicle_root)
    assert r.accepted


def test_empty_chronicle_file_rejected(chronicle_root):
    """An empty chronicle file has no ground_truth records → rejected."""
    target = chronicle_root / "insights" / "x" / "empty.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")
    r = grounded_extract("c", [str(target)], chronicle_root=chronicle_root)
    assert not r
    assert r.rejected_paths[0]["reason"] is PATH_HYPOTHESIS_ONLY


# ── Case 12: claim preserved ─────────────────────────────────────────────────

def test_claim_preserved_on_result(chronicle_root):
    claim = "v1.3.2 adds prior_for_turn"
    r = grounded_extract(claim, [], chronicle_root=chronicle_root)
    assert r.claim == claim


# ── Case 13: accounting — every path lands somewhere ────────────────────────

def test_every_path_accounted_for(chronicle_root, outside_path):
    """matched_paths ∪ rejected_paths must equal the input, no silent drops."""
    gt = _chronicle_file(chronicle_root, "a", [GROUND_TRUTH_LAYER])
    hyp = _chronicle_file(chronicle_root, "b", [HYPOTHESIS_LAYER])
    missing = "/nonexistent/xyz.txt"

    inputs = [str(outside_path), str(gt), str(hyp), missing]
    r = grounded_extract("c", inputs, chronicle_root=chronicle_root)

    covered = set(r.matched_paths) | {rp["path"] for rp in r.rejected_paths}
    assert covered == set(inputs)


# ── Case 14: frozen dataclass ────────────────────────────────────────────────

def test_grounding_result_is_frozen(chronicle_root):
    """Result objects must be immutable so daemons can't mutate them
    post-return and confuse downstream consumers."""
    r = grounded_extract("c", [], chronicle_root=chronicle_root)
    with pytest.raises(FrozenInstanceError):
        r.accepted = True  # type: ignore[misc]


# ── Case 15: no LLM leakage — the function is pure ──────────────────────────

def test_function_is_pure_no_filesystem_side_effects(chronicle_root, outside_path):
    """grounded_extract must not write anything. Snapshot the tree before
    and after; they must match."""
    def snapshot(root: Path) -> set:
        return {p for p in root.rglob("*") if p.is_file()}

    before = snapshot(chronicle_root)
    for _ in range(5):
        grounded_extract(
            "c", [str(outside_path)], chronicle_root=chronicle_root,
        )
    after = snapshot(chronicle_root)
    assert before == after


# ── Case 16: default chronicle root resolves to ~/.sovereign/chronicle ──────

def test_default_chronicle_root_does_not_crash():
    """Without chronicle_root override, the function falls back to the
    real ~/.sovereign/chronicle. Must not crash even if the user has no
    chronicle — a missing chronicle_root just means no path can be
    classified as inside it, so structural-evidence path still works."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    tmp.write(b"hello")
    tmp.close()
    try:
        r = grounded_extract("c", [tmp.name])
        assert r.accepted
    finally:
        os.unlink(tmp.name)
