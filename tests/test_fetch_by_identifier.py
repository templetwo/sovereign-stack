"""
Fetch-by-identifier (Phase 1, feat/chronicle-content-class, FIX-3).

Locks the return_claim_id guarantees on record_insight:

  - return_claim_id=True surfaces the derived claim_id of the entry just
    written, even when it is buried under many same-word siblings (the
    motivating case: you can't find it by text search, so you address it by id);
  - the surfaced id is a 64-hex string equal to derive_claim_id of the stored
    entry, and inspect_claim resolves it with integrity=="verified";
  - the default (return_claim_id omitted / False) returns a PLAIN str with no
    claim_id attribute — byte-identical to today.
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from sovereign_stack import provenance
from sovereign_stack import provenance_tools as pt
from sovereign_stack.memory import ExperientialMemory

SESSION = "fetch-by-id"
DOMAIN = "burial-ground"


@pytest.fixture()
def mem():
    tmp = Path(tempfile.mkdtemp(prefix="fetch-id-"))
    yield ExperientialMemory(root=str(tmp / "chronicle"))
    shutil.rmtree(tmp, ignore_errors=True)


def _stored_entry_by_content(mem, content):
    path = mem.insights_dir / DOMAIN / f"{SESSION}.jsonl"
    for line in path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("content") == content:
                return rec
    raise AssertionError("target entry not found on disk")


def test_return_claim_id_surfaces_verifiable_id(mem):
    # Bury the target under ~30 entries that all share the same words, so text
    # recall cannot single it out — id is the only reliable handle.
    for i in range(30):
        mem.record_insight(DOMAIN, f"buried needle in the haystack number {i}", 0.5, SESSION)
    target_content = "buried needle in the haystack THE ONE"
    path = mem.record_insight(DOMAIN, target_content, 0.5, SESSION, return_claim_id=True)

    # 64-hex id equal to the stored entry's derived id.
    assert hasattr(path, "claim_id")
    assert len(path.claim_id) == 64
    assert all(c in "0123456789abcdef" for c in path.claim_id)
    stored = _stored_entry_by_content(mem, target_content)
    assert path.claim_id == provenance.derive_claim_id(stored)

    # inspect_claim resolves it end-to-end with verified integrity.
    report = pt.inspect_claim(
        path.claim_id,
        chronicle_root=mem.root,
        ledger_path=mem.supersessions_path,
    )
    assert report["integrity"] == "verified"


def test_default_returns_plain_str_without_claim_id(mem):
    path = mem.record_insight(DOMAIN, "no id requested", 0.5, SESSION)
    assert type(path) is str
    assert not hasattr(path, "claim_id")


def test_explicit_false_returns_plain_str(mem):
    path = mem.record_insight(DOMAIN, "explicit false", 0.5, SESSION, return_claim_id=False)
    assert type(path) is str
    assert not hasattr(path, "claim_id")
