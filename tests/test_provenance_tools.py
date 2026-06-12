"""Tests for provenance_tools (v1.7.0 — inspect_claim & supersede_insight).

The MCP surface over the provenance keystone. These tests pin the spec's
section 2 contracts: link-existing supersession over a cross-domain
children-exclusion-shaped fixture with zero content duplication; lineage
walk rendering; ledger-vs-breadcrumb divergence detection; revoke
restoring surfacing; integrity verdicts (verified only on full ids,
ambiguous on prefixes); and live receipt re-verification surfacing
dangling refs via checked_now. Hermetic — everything under tmp_path.
"""

import hashlib
import json
from pathlib import Path

import pytest

from sovereign_stack import provenance as prov
from sovereign_stack import provenance_tools as pt

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _entry(timestamp="2026-06-12T10:00:00+00:00", domain="testing", content="the claim", **extra):
    return {"timestamp": timestamp, "domain": domain, "content": content, **extra}


def _chronicle(tmp_path: Path) -> Path:
    root = tmp_path / "chronicle"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ledger(root: Path) -> Path:
    return root / "supersessions.jsonl"


def _add_insight(root: Path, entry: dict, domain=None, filename="session_x.jsonl") -> str:
    """Write one entry under insights/<domain>/ and return its claim id."""
    domain_dir = root / "insights" / (domain or entry.get("domain", "misc"))
    domain_dir.mkdir(parents=True, exist_ok=True)
    with open(domain_dir / filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return prov.derive_claim_id(entry)


def _add_archive(root: Path, content: str) -> str:
    """Replicate the archive layer's on-disk convention; return archive_id."""
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    blob = root / "archives" / "_unfiled" / f"2026-06-12_test_fixture__{sha[:12]}.txt"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text(content, encoding="utf-8")
    record = {"archive_id": sha, "sha256": sha, "path": str(blob)}
    with open(root / "archives" / "index.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return sha


def _insight_bytes(root: Path) -> dict[Path, bytes]:
    """Snapshot every insight file's bytes — the no-duplication witness."""
    return {p: p.read_bytes() for p in sorted(root.glob("insights/**/*.jsonl"))}


def _children_exclusion_pair(root: Path) -> tuple[str, str]:
    """
    The children-exclusion-SHAPED fixture: a predecessor and its
    DEFINITIVE successor living in DIFFERENT domain dirs (the live pair
    the spec verified spans domains).
    """
    predecessor = _entry(
        timestamp="2026-05-01T09:00:00+00:00",
        domain="memory-architecture",
        content="children are excluded from the inheritable context by the layer filter",
    )
    successor = _entry(
        timestamp="2026-06-01T09:00:00+00:00",
        domain="boot-ritual,memory-architecture",
        content=(
            "DEFINITIVE: children-exclusion happens at the partition step, "
            "not the layer filter — the filter never sees them"
        ),
    )
    pred_id = _add_insight(root, predecessor, domain="memory-architecture")
    succ_id = _add_insight(root, successor, domain="boot-ritual")
    return pred_id, succ_id


def _link(root: Path, pred_id: str, succ_id: str, summary="partition, not filter", **kw):
    return pt.supersede_insight(
        pred_id,
        succ_id,
        carry_forward_summary=summary,
        chronicle_root=root,
        ledger_path=_ledger(root),
        **kw,
    )


def _inspect(root: Path, ref: str, **kw) -> dict:
    return pt.inspect_claim(ref, chronicle_root=root, ledger_path=_ledger(root), **kw)


# ── supersede_insight: link-existing, no duplication ─────────────────────────


def test_link_existing_cross_domain_pair_appends_ledger_record(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)

    record = _link(root, pred_id, succ_id, reason="formalizing the live pair", by="instance_x")

    assert record["action"] == "supersede"
    assert record["superseded_id"] == pred_id
    assert record["successor_id"] == succ_id
    assert record["carry_forward_summary"] == "partition, not filter"
    assert record["by"] == "instance_x"
    assert record["predecessor_domain"] == "memory-architecture"
    assert "layer filter" in record["predecessor_preview"]
    ledger_lines = _ledger(root).read_text().strip().splitlines()
    assert len(ledger_lines) == 1
    assert json.loads(ledger_lines[0]) == record


def test_link_existing_never_duplicates_content(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    before = _insight_bytes(root)

    _link(root, pred_id, succ_id)

    # Entry files byte-unchanged: the link lives ONLY in the ledger.
    assert _insight_bytes(root) == before
    entries = [e for e, _f, _loc in prov.iter_chronicle_entries(root)]
    assert len(entries) == 2
    # Derived ids unshifted — the pair still resolves.
    assert prov.derive_claim_id(entries[0]) in (pred_id, succ_id)


def test_link_requires_carry_forward(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    with pytest.raises(prov.ProvenanceError, match="carry_forward_summary is required"):
        pt.supersede_insight(pred_id, succ_id, chronicle_root=root, ledger_path=_ledger(root))


def test_link_requires_successor_id(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, _ = _children_exclusion_pair(root)
    with pytest.raises(prov.SupersessionError, match="requires successor_id"):
        pt.supersede_insight(
            pred_id, carry_forward_summary="x", chronicle_root=root, ledger_path=_ledger(root)
        )


def test_link_refuses_prefix_ids_naming_the_full_id(tmp_path):
    # Ledger writes require verified integrity: full 64-hex only. The
    # rejection hands back the full id so the caller can re-run.
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    with pytest.raises(prov.SupersessionError, match=pred_id):
        _link(root, pred_id[:16], succ_id)


def test_link_rejects_unknown_and_self_and_double(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)

    with pytest.raises(prov.ClaimNotFoundError):
        _link(root, "f" * 64, succ_id)
    with pytest.raises(prov.SupersessionError, match="self-supersession"):
        _link(root, pred_id, pred_id)

    _link(root, pred_id, succ_id)
    third_id = _add_insight(root, _entry(content="a third claim"))
    with pytest.raises(prov.SupersessionError, match="already superseded"):
        _link(root, pred_id, third_id)


def test_link_rejects_cycles(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    _link(root, pred_id, succ_id)
    with pytest.raises(prov.SupersessionError, match="cycle"):
        _link(root, succ_id, pred_id)


def test_invalid_action_rejected(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    with pytest.raises(prov.SupersessionError, match="invalid action"):
        _link(root, pred_id, succ_id, action="merge")


# ── supersede_insight: revoke ────────────────────────────────────────────────


def test_revoke_restores_surfacing(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    _link(root, pred_id, succ_id)

    record = pt.supersede_insight(
        pred_id,
        action="revoke",
        reason="linked wrong pair",
        chronicle_root=root,
        ledger_path=_ledger(root),
    )

    assert record["action"] == "revoke"
    assert record["superseded_id"] == pred_id
    assert record["successor_id"] is None
    # Append-only: both records remain; the fold nullifies.
    assert len(_ledger(root).read_text().strip().splitlines()) == 2
    fold = prov.fold_supersessions(prov.load_supersessions(_ledger(root)))
    assert fold == {}
    report = _inspect(root, pred_id)
    assert "superseded_by" not in report
    # And the predecessor may be linked again — the guard is cleared.
    _link(root, pred_id, succ_id)


def test_revoke_requires_full_id_with_hint(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    _link(root, pred_id, succ_id)
    with pytest.raises(prov.SupersessionError, match=f"did you mean {pred_id}"):
        pt.supersede_insight(
            pred_id[:16], action="revoke", chronicle_root=root, ledger_path=_ledger(root)
        )


def test_revoke_with_nothing_to_revoke_rejected(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, _ = _children_exclusion_pair(root)
    with pytest.raises(prov.SupersessionError, match="nothing to revoke"):
        pt.supersede_insight(
            pred_id, action="revoke", chronicle_root=root, ledger_path=_ledger(root)
        )


def test_revoke_works_on_dangling_predecessor(tmp_path):
    # The cleanup path: the ledger names a predecessor whose entry no
    # longer resolves. Revoke must still work, locator hints falling
    # back to the record being revoked.
    root = _chronicle(tmp_path)
    ghost = _entry(domain="lost-domain", content="this entry was never written to disk")
    ghost_id = prov.derive_claim_id(ghost)
    record = prov.build_supersession_record(
        action="supersede",
        superseded_id=ghost_id,
        successor_id="a" * 64,
        carry_forward_summary="x",
        predecessor=ghost,
    )
    prov.append_supersession(_ledger(root), record)

    revoke = pt.supersede_insight(
        ghost_id, action="revoke", chronicle_root=root, ledger_path=_ledger(root)
    )
    assert revoke["action"] == "revoke"
    assert revoke["predecessor_domain"] == "lost-domain"
    assert "never written" in revoke["predecessor_preview"]


# ── inspect_claim: integrity verdicts ────────────────────────────────────────


def test_integrity_verified_on_full_id(tmp_path):
    root = _chronicle(tmp_path)
    cid = _add_insight(root, _entry())
    report = _inspect(root, cid)
    assert report["found"] is True
    assert report["claim_id"] == cid
    assert report["integrity"] == "verified"
    assert report["location"] == "insights"
    assert report["entry"]["content"] == "the claim"


def test_integrity_ambiguous_on_short_prefix(tmp_path):
    # A prefix resolves the entry, but a prefix can never equal the
    # re-derived 64-hex hash — end-to-end integrity stays unclaimed.
    root = _chronicle(tmp_path)
    cid = _add_insight(root, _entry())
    report = _inspect(root, cid[:16])
    assert report["found"] is True
    assert report["claim_id"] == cid  # full id reported for the re-run
    assert report["integrity"] == "ambiguous"


def test_integrity_unknown_when_not_found(tmp_path):
    root = _chronicle(tmp_path)
    report = _inspect(root, "f" * 64)
    assert report["found"] is False
    assert report["integrity"] == "unknown"
    assert "no chronicle entry matches" in report["error"]


def test_integrity_ambiguous_on_multi_match_prefix(tmp_path):
    root = _chronicle(tmp_path)
    ids = []
    for i in range(40):
        ids.append(_add_insight(root, _entry(content=f"claim number {i}")))
    first, collision = next((a, b) for a in ids for b in ids if a != b and a[0] == b[0])
    report = _inspect(root, first[0])
    assert report["found"] is False
    assert report["integrity"] == "ambiguous"
    assert "matches" in report["error"]
    assert collision  # two distinct claims shared the prefix


def test_malformed_ref_reported_not_raised(tmp_path):
    root = _chronicle(tmp_path)
    report = _inspect(root, "not-hex!")
    assert report["found"] is False
    assert report["integrity"] == "unknown"
    assert "malformed" in report["error"]


def test_inspect_finds_quarantined_entries(tmp_path):
    root = _chronicle(tmp_path)
    entry = _entry(domain="quarantined-domain", content="moved but not lost")
    qdir = root / "_quarantine_2026-06-01" / "quarantined-domain"
    qdir.mkdir(parents=True, exist_ok=True)
    with open(qdir / "session_q.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    report = _inspect(root, prov.derive_claim_id(entry))
    assert report["found"] is True
    assert report["location"] == "quarantine"


# ── inspect_claim: lineage rendering ─────────────────────────────────────────


def test_lineage_walk_renders_chain(tmp_path):
    root = _chronicle(tmp_path)
    a = _entry(timestamp="2026-04-01T00:00:00+00:00", domain="alpha", content="first take")
    b = _entry(timestamp="2026-05-01T00:00:00+00:00", domain="beta", content="second take")
    c = _entry(timestamp="2026-06-01T00:00:00+00:00", domain="gamma", content="third take")
    a_id, b_id, c_id = (_add_insight(root, e) for e in (a, b, c))
    _link(root, a_id, b_id, summary="first still teaches the question")
    _link(root, b_id, c_id, summary="second still teaches the method")

    report = _inspect(root, b_id)

    roles = [(row["role"], row["claim_id"]) for row in report["lineage"]]
    assert roles == [("predecessor", a_id), ("self", b_id), ("successor", c_id)]
    by_id = {row["claim_id"]: row for row in report["lineage"]}
    assert by_id[a_id]["content_preview"] == "first take"
    assert by_id[a_id]["domain"] == "alpha"
    assert by_id[a_id]["carry_forward_summary"] == "first still teaches the question"
    assert by_id[b_id]["carry_forward_summary"] == "second still teaches the method"
    assert by_id[c_id]["timestamp"] == "2026-06-01T00:00:00+00:00"
    # Supersession status block: B is superseded by C and supersedes A.
    assert report["superseded_by"] == c_id
    assert report["carry_forward_summary"] == "second still teaches the method"
    assert report["supersedes"] == [a_id]


def test_lineage_trivial_when_unlinked(tmp_path):
    root = _chronicle(tmp_path)
    cid = _add_insight(root, _entry())
    report = _inspect(root, cid)
    assert [row["role"] for row in report["lineage"]] == ["self"]
    assert "superseded_by" not in report
    assert "supersedes" not in report


# ── inspect_claim: ledger vs breadcrumb ──────────────────────────────────────


def test_divergence_detected_on_breadcrumb_the_ledger_lacks(tmp_path):
    # Hand-write a breadcrumb (as if the entry line was edited, or the
    # ledger line lost) — the canonical ledger denies it.
    root = _chronicle(tmp_path)
    phantom_id = "b" * 64
    successor = _entry(content="claims a supersession the ledger lacks", supersedes=[phantom_id])
    succ_id = _add_insight(root, successor)
    report = _inspect(root, succ_id)
    assert report["ledger_vs_breadcrumb"] == "divergent"
    assert report["entry"]["supersedes"] == [phantom_id]  # raw breadcrumb stays visible
    assert "supersedes" not in report  # canonical view: ledger holds nothing


def test_consistent_when_ledger_backs_the_breadcrumb(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, _ = _children_exclusion_pair(root)
    successor = _entry(content="recorded with a proper supersedes param", supersedes=[pred_id])
    succ_id = _add_insight(root, successor)
    record = prov.build_supersession_record(
        action="supersede",
        superseded_id=pred_id,
        successor_id=succ_id,
        carry_forward_summary="x",
    )
    prov.append_supersession(_ledger(root), record)
    report = _inspect(root, succ_id)
    assert report["ledger_vs_breadcrumb"] == "consistent"
    assert report["supersedes"] == [pred_id]


def test_divergent_when_breadcrumb_names_someone_elses_predecessor(tmp_path):
    # The breadcrumb claims THIS entry supersedes X, but the ledger says
    # X's successor is a different claim — divergent, not consistent.
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    _link(root, pred_id, succ_id)
    impostor = _entry(content="also claims the predecessor", supersedes=[pred_id])
    impostor_id = _add_insight(root, impostor)
    assert _inspect(root, impostor_id)["ledger_vs_breadcrumb"] == "divergent"


def test_revoked_link_makes_stale_breadcrumb_divergent(tmp_path):
    # After revoke the denormalized breadcrumb is stale relative to the
    # canonical ledger — surfaced, never papered over.
    root = _chronicle(tmp_path)
    pred_id, _ = _children_exclusion_pair(root)
    successor = _entry(content="successor whose link gets revoked", supersedes=[pred_id])
    succ_id = _add_insight(root, successor)
    record = prov.build_supersession_record(
        action="supersede",
        superseded_id=pred_id,
        successor_id=succ_id,
        carry_forward_summary="x",
    )
    prov.append_supersession(_ledger(root), record)
    assert _inspect(root, succ_id)["ledger_vs_breadcrumb"] == "consistent"

    pt.supersede_insight(pred_id, action="revoke", chronicle_root=root, ledger_path=_ledger(root))
    assert _inspect(root, succ_id)["ledger_vs_breadcrumb"] == "divergent"


def test_empty_breadcrumb_is_consistent(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    _link(root, pred_id, succ_id)
    # Link-existing leaves the successor breadcrumb-less — the normal
    # shape of a formalized pair, NOT a divergence.
    assert _inspect(root, succ_id)["ledger_vs_breadcrumb"] == "consistent"


# ── inspect_claim: receipts & checked_now ────────────────────────────────────


def test_receipts_projected_without_live_check_by_default(tmp_path):
    root = _chronicle(tmp_path)
    entry = _entry(
        content="receipted claim",
        verified_by=[{"kind": "cmd", "ref": "pytest -q", "checked_at_write": "attested"}],
    )
    cid = _add_insight(root, entry)
    report = _inspect(root, cid)
    assert report["receipts"] == [
        {"kind": "cmd", "ref": "pytest -q", "checked_at_write": "attested"}
    ]


def test_dangling_claim_receipt_surfaced_via_checked_now(tmp_path):
    # Hand-written receipt (the write path would have rejected it):
    # its claim target resolves to nothing NOW — inspect says so.
    root = _chronicle(tmp_path)
    entry = _entry(
        content="cites a claim that no longer derives",
        verified_by=[{"kind": "claim", "ref": "d" * 64, "checked_at_write": "cites"}],
    )
    cid = _add_insight(root, entry)
    report = _inspect(root, cid, verify_receipts=True)
    assert report["receipts"][0]["checked_now"] == "dangling"
    assert report["receipts"][0]["checked_at_write"] == "cites"


def test_resolvable_claim_receipt_checked_now_cites_never_verified(tmp_path):
    root = _chronicle(tmp_path)
    cited_id = _add_insight(root, _entry(content="the cited claim"))
    entry = _entry(
        content="cites a live claim",
        verified_by=[{"kind": "claim", "ref": cited_id, "checked_at_write": "cites"}],
    )
    cid = _add_insight(root, entry)
    report = _inspect(root, cid, verify_receipts=True)
    assert report["receipts"][0]["checked_now"] == "cites"


def test_archive_receipt_checked_now_verified_then_missing(tmp_path):
    root = _chronicle(tmp_path)
    archive_id = _add_archive(root, "the archived exchange text")
    entry = _entry(
        content="receipted against an archive",
        verified_by=[{"kind": "archive", "ref": archive_id, "checked_at_write": "verified"}],
    )
    cid = _add_insight(root, entry)
    assert _inspect(root, cid, verify_receipts=True)["receipts"][0]["checked_now"] == "verified"

    # Delete the blob: the receipt now dangles, surfaced as missing.
    next(root.glob("archives/_unfiled/*.txt")).unlink()
    assert _inspect(root, cid, verify_receipts=True)["receipts"][0]["checked_now"] == "missing"


def test_file_receipt_checked_now_tracks_the_bytes(tmp_path):
    root = _chronicle(tmp_path)
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("original bytes", encoding="utf-8")
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    entry = _entry(
        content="receipted against a file",
        verified_by=[
            {"kind": "file", "ref": str(artifact), "sha256": sha, "checked_at_write": "verified"}
        ],
    )
    cid = _add_insight(root, entry)
    assert _inspect(root, cid, verify_receipts=True)["receipts"][0]["checked_now"] == "verified"

    artifact.write_text("tampered bytes", encoding="utf-8")
    assert _inspect(root, cid, verify_receipts=True)["receipts"][0]["checked_now"] == "mismatch"

    artifact.unlink()
    assert _inspect(root, cid, verify_receipts=True)["receipts"][0]["checked_now"] == "missing"


def test_attested_kinds_stay_attested_now(tmp_path):
    root = _chronicle(tmp_path)
    entry = _entry(
        content="human-attested claim",
        verified_by=[{"kind": "human", "ref": "anthony", "checked_at_write": "attested"}],
    )
    cid = _add_insight(root, entry)
    assert _inspect(root, cid, verify_receipts=True)["receipts"][0]["checked_now"] == "attested"


# ── inspect_claim: retire records on the read path ───────────────────────────


def test_retired_entry_shows_null_superseded_by(tmp_path):
    root = _chronicle(tmp_path)
    cid = _add_insight(root, _entry(content="retired hypothesis"))
    record = prov.build_supersession_record(action="retire", superseded_id=cid, reason="instance_x")
    prov.append_supersession(_ledger(root), record)
    report = _inspect(root, cid)
    assert "superseded_by" in report
    assert report["superseded_by"] is None


# ── handler dispatch ─────────────────────────────────────────────────────────


def test_handle_inspect_claim_returns_json_text(tmp_path):
    root = _chronicle(tmp_path)
    cid = _add_insight(root, _entry())
    text = pt.handle_provenance_tool(
        "inspect_claim", {"claim_id": cid}, chronicle_root=root, ledger_path=_ledger(root)
    )
    parsed = json.loads(text)
    assert parsed["claim_id"] == cid
    assert parsed["integrity"] == "verified"
    assert parsed["ledger_vs_breadcrumb"] == "consistent"


def test_handle_inspect_claim_not_found_is_json_not_error(tmp_path):
    root = _chronicle(tmp_path)
    text = pt.handle_provenance_tool(
        "inspect_claim", {"claim_id": "e" * 64}, chronicle_root=root, ledger_path=_ledger(root)
    )
    parsed = json.loads(text)
    assert parsed["found"] is False
    assert parsed["integrity"] == "unknown"


def test_handle_supersede_insight_links_and_reports(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    text = pt.handle_provenance_tool(
        "supersede_insight",
        {
            "predecessor_id": pred_id,
            "successor_id": succ_id,
            "carry_forward_summary": "partition, not filter",
            "by": "instance_x",
        },
        chronicle_root=root,
        ledger_path=_ledger(root),
    )
    assert text.startswith("⛓ Supersession linked:")
    record = json.loads(text.split(":", 1)[1])
    assert record["superseded_id"] == pred_id
    assert record["successor_id"] == succ_id


def test_handle_supersede_insight_guard_failure_is_rejection_text(tmp_path):
    root = _chronicle(tmp_path)
    pred_id, succ_id = _children_exclusion_pair(root)
    text = pt.handle_provenance_tool(
        "supersede_insight",
        {"predecessor_id": pred_id, "successor_id": succ_id},  # no carry_forward
        chronicle_root=root,
        ledger_path=_ledger(root),
    )
    assert text.startswith("⚠️ supersede_insight rejected:")
    assert "carry_forward_summary" in text
    assert not _ledger(root).exists()  # rejected calls never touch the ledger


def test_handle_unknown_tool(tmp_path):
    assert pt.handle_provenance_tool("not_a_tool", {}) == "Unknown provenance tool: not_a_tool"


# ── tool schemas ─────────────────────────────────────────────────────────────


def test_tool_schemas_shape():
    names = [t.name for t in pt.PROVENANCE_TOOLS]
    assert names == ["inspect_claim", "supersede_insight"]
    inspect_tool, supersede_tool = pt.PROVENANCE_TOOLS
    assert inspect_tool.inputSchema["required"] == ["claim_id"]
    assert supersede_tool.inputSchema["required"] == ["predecessor_id"]
    assert supersede_tool.inputSchema["properties"]["action"]["enum"] == ["link", "revoke"]
    assert set(pt.PROVENANCE_TOOL_TIERS) == set(names)
    assert set(pt.PROVENANCE_TOOL_INTENTS) == set(names)
    assert all(tier == "core" for tier in pt.PROVENANCE_TOOL_TIERS.values())


def test_import_has_no_side_effects(tmp_path, monkeypatch):
    # Lazy creation: nothing under a fake HOME until the first append.
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib

    import sovereign_stack.provenance_tools as module

    importlib.reload(module)
    assert list(tmp_path.iterdir()) == []
