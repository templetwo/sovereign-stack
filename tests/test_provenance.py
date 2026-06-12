"""Tests for the provenance module (v1.7.0 keystone).

Claim identity is derived on read, never stored; receipts fail closed on
dangling refs and stamp honest verdicts on everything else; the
supersession ledger folds append-only records into effective state with
guards. These tests pin the spec's section 1-3 contracts: field-absent
derivation, zero-collision on live-shaped fixtures, quarantine-aware
prefix resolution, reject-dangling / stamp-mismatch / cites-never-verified
receipt semantics, fold/revoke/guard behavior, lineage walks, and
partition/annotate read helpers. Hermetic — everything under tmp_path.
"""

import hashlib
import json
from pathlib import Path

import pytest

from sovereign_stack import provenance as prov

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _entry(timestamp="2026-06-12T10:00:00+00:00", domain="testing", content="the claim", **extra):
    return {"timestamp": timestamp, "domain": domain, "content": content, **extra}


def _write_entries(directory: Path, entries: list[dict], filename="session_x.jsonl") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    with open(path, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return path


def _chronicle(tmp_path: Path) -> Path:
    root = tmp_path / "chronicle"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _add_insight(root: Path, entry: dict, domain=None, filename="session_x.jsonl") -> str:
    """Write one entry under insights/<domain>/ and return its claim id."""
    domain_dir = root / "insights" / (domain or entry.get("domain", "misc"))
    _write_entries(domain_dir, [entry], filename)
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


def _ledger(root: Path) -> Path:
    return root / "supersessions.jsonl"


def _link(root: Path, pred_id: str, succ_id: str, summary="what carried forward", **kw):
    record = prov.build_supersession_record(
        action="supersede",
        superseded_id=pred_id,
        successor_id=succ_id,
        carry_forward_summary=summary,
        **kw,
    )
    return prov.append_supersession(_ledger(root), record)


# ── Claim identity ───────────────────────────────────────────────────────────


def test_derive_matches_reference_hash():
    entry = _entry()
    preimage = "2026-06-12T10:00:00+00:00\x1ftesting\x1fthe claim"
    assert prov.derive_claim_id(entry) == hashlib.sha256(preimage.encode()).hexdigest()


def test_derive_is_full_64_hex():
    cid = prov.derive_claim_id(_entry())
    assert len(cid) == 64
    assert all(c in "0123456789abcdef" for c in cid)


def test_derive_tolerates_absent_fields():
    assert prov.derive_claim_id({}) == hashlib.sha256(b"\x1f\x1f").hexdigest()
    partial = prov.derive_claim_id({"content": "only content"})
    assert partial == hashlib.sha256(b"\x1f\x1fonly content").hexdigest()


def test_derive_tolerates_none_fields():
    # Explicit None behaves like absent — a malformed line still derives.
    assert prov.derive_claim_id({"timestamp": None, "domain": None, "content": None}) == (
        prov.derive_claim_id({})
    )


def test_separator_prevents_field_bleed():
    a = prov.derive_claim_id({"timestamp": "", "domain": "ab", "content": "c"})
    b = prov.derive_claim_id({"timestamp": "", "domain": "a", "content": "bc"})
    assert a != b


def test_annotation_fields_outside_preimage():
    base = _entry()
    decorated = _entry(
        layer="retired", retired_by="instance_x", intensity=0.9, session_id="session_y"
    )
    assert prov.derive_claim_id(base) == prov.derive_claim_id(decorated)


def test_zero_collision_on_live_shaped_fixtures():
    # Live-shaped: same session timestamps, overlapping domains, near-identical
    # content. 300 entries, 300 distinct ids.
    entries = []
    for i in range(300):
        entries.append(
            _entry(
                timestamp=f"2026-06-{(i % 28) + 1:02d}T10:{i % 60:02d}:00+00:00",
                domain=("architecture", "consciousness", "security")[i % 3],
                content=f"verified the bridge heartbeat at port 8100, attempt {i}",
                intensity=0.5 + (i % 5) / 10,
                layer="ground_truth",
            )
        )
    ids = {prov.derive_claim_id(e) for e in entries}
    assert len(ids) == len(entries)


def test_display_id_truncates_to_16():
    cid = prov.derive_claim_id(_entry())
    assert prov.display_id(cid) == cid[:16]
    assert len(prov.display_id(cid)) == 16


# ── Prefix resolution ────────────────────────────────────────────────────────


def test_resolve_full_id_and_prefix(tmp_path):
    root = _chronicle(tmp_path)
    cid = _add_insight(root, _entry())
    for ref in (cid, cid[:12]):
        entry, file, location = prov.resolve_claim(ref, root)
        assert entry["content"] == "the claim"
        assert location == "insights"
        assert file.name == "session_x.jsonl"
        assert "insights" in file.parts


def test_resolve_scans_quarantine(tmp_path):
    root = _chronicle(tmp_path)
    entry = _entry(content="quarantined but addressable")
    _write_entries(root / "_quarantine_2026-05-18", [entry], "moved.jsonl")
    found, file, location = prov.resolve_claim(prov.derive_claim_id(entry), root)
    assert found["content"] == "quarantined but addressable"
    assert location == "quarantine"
    assert "_quarantine_2026-05-18" in file.parts


def test_resolve_scans_nested_quarantine_dirs(tmp_path):
    root = _chronicle(tmp_path)
    entry = _entry(content="nested under a domain dir inside quarantine")
    _write_entries(root / "_quarantine_2026-05-18" / "security", [entry])
    _entry_found, _file, location = prov.resolve_claim(prov.derive_claim_id(entry), root)
    assert location == "quarantine"


def test_resolve_not_found_raises_distinct_error(tmp_path):
    root = _chronicle(tmp_path)
    _add_insight(root, _entry())
    with pytest.raises(prov.ClaimNotFoundError):
        prov.resolve_claim("deadbeef" * 8, root)


def test_resolve_ambiguous_prefix_raises(tmp_path):
    root = _chronicle(tmp_path)
    # Brute-force two entries whose claim ids share a first hex char.
    ids = {}
    i = 0
    while True:
        entry = _entry(content=f"collision hunt {i}")
        cid = prov.derive_claim_id(entry)
        if cid[0] in ids and ids[cid[0]]["content"] != entry["content"]:
            _add_insight(root, ids[cid[0]])
            _add_insight(root, entry, filename="session_y.jsonl")
            prefix = cid[0]
            break
        ids[cid[0]] = entry
        i += 1
    with pytest.raises(prov.AmbiguousClaimError, match="supply more characters"):
        prov.resolve_claim(prefix, root)


def test_resolve_duplicate_lines_one_claim_not_ambiguous(tmp_path):
    root = _chronicle(tmp_path)
    entry = _entry(content="written twice byte-identically")
    _write_entries(root / "insights" / "testing", [entry, entry])
    found, _file, _location = prov.resolve_claim(prov.derive_claim_id(entry), root)
    assert found["content"] == entry["content"]


def test_resolve_malformed_ref_raises(tmp_path):
    root = _chronicle(tmp_path)
    for bad in ("", "XYZ", "g" * 64, "a" * 65, None):
        with pytest.raises(prov.ProvenanceError):
            prov.resolve_claim(bad, root)


def test_resolution_survives_corrupt_lines(tmp_path):
    root = _chronicle(tmp_path)
    domain_dir = root / "insights" / "testing"
    domain_dir.mkdir(parents=True)
    entry = _entry()
    (domain_dir / "session_x.jsonl").write_text(
        "not json at all\n\n" + json.dumps(entry) + "\n", encoding="utf-8"
    )
    found, _file, _location = prov.resolve_claim(prov.derive_claim_id(entry), root)
    assert found == entry


# ── Receipt grammar — fail closed ────────────────────────────────────────────


def test_receipt_unknown_kind_rejected_naming_receipt(tmp_path):
    root = _chronicle(tmp_path)
    with pytest.raises(prov.ReceiptError, match="unknown receipt kind"):
        prov.verify_receipt_at_write({"kind": "vibes", "ref": "trust me"}, root)


def test_receipt_missing_ref_rejected(tmp_path):
    root = _chronicle(tmp_path)
    with pytest.raises(prov.ReceiptError, match="non-empty string"):
        prov.verify_receipt_at_write({"kind": "human"}, root)


def test_receipt_non_dict_rejected(tmp_path):
    root = _chronicle(tmp_path)
    with pytest.raises(prov.ReceiptError, match="must be a dict"):
        prov.verify_receipt_at_write("archive:abc123", root)


def test_receipt_forged_stamp_rejected(tmp_path):
    # A caller-supplied checked_at_write is a forged verification badge.
    root = _chronicle(tmp_path)
    receipt = {"kind": "human", "ref": "anthony", "checked_at_write": "verified"}
    with pytest.raises(prov.ReceiptError, match="checked_at_write"):
        prov.verify_receipt_at_write(receipt, root)


def test_archive_receipt_verified(tmp_path):
    root = _chronicle(tmp_path)
    archive_id = _add_archive(root, "the verbatim bytes")
    stamped = prov.verify_receipt_at_write({"kind": "archive", "ref": archive_id}, root)
    assert stamped["checked_at_write"] == "verified"


def test_archive_receipt_prefix_resolves(tmp_path):
    root = _chronicle(tmp_path)
    archive_id = _add_archive(root, "prefix-addressable bytes")
    stamped = prov.verify_receipt_at_write({"kind": "archive", "ref": archive_id[:12]}, root)
    assert stamped["checked_at_write"] == "verified"


def test_archive_receipt_mismatch_recorded_with_stamp(tmp_path):
    # "This artifact has since changed" is a true, recordable claim.
    root = _chronicle(tmp_path)
    archive_id = _add_archive(root, "authentic bytes")
    records = [json.loads(line) for line in (root / "archives" / "index.jsonl").open()]
    Path(records[0]["path"]).write_text("tampered", encoding="utf-8")
    stamped = prov.verify_receipt_at_write({"kind": "archive", "ref": archive_id}, root)
    assert stamped["checked_at_write"] == "mismatch"


def test_archive_receipt_dangling_rejected_names_ref(tmp_path):
    root = _chronicle(tmp_path)
    ghost = "beef" * 16
    with pytest.raises(prov.ReceiptError, match=f"archive:{ghost}"):
        prov.verify_receipt_at_write({"kind": "archive", "ref": ghost}, root)


def test_archive_receipt_ambiguous_prefix_rejected(tmp_path):
    root = _chronicle(tmp_path)
    ids = {}
    i = 0
    while True:
        sha = _add_archive(root, f"blob number {i}")
        if sha[0] in ids and ids[sha[0]] != sha:
            prefix = sha[0]
            break
        ids[sha[0]] = sha
        i += 1
    with pytest.raises(prov.ReceiptError, match="ambiguous"):
        prov.verify_receipt_at_write({"kind": "archive", "ref": prefix}, root)


def test_archive_receipt_bytes_gone_rejected(tmp_path):
    # Index record exists, bytes gone: pointing at a ghost, unrecordable.
    root = _chronicle(tmp_path)
    archive_id = _add_archive(root, "soon to vanish")
    records = [json.loads(line) for line in (root / "archives" / "index.jsonl").open()]
    Path(records[0]["path"]).unlink()
    with pytest.raises(prov.ReceiptError, match="dangling"):
        prov.verify_receipt_at_write({"kind": "archive", "ref": archive_id}, root)


def test_file_receipt_requires_sha256(tmp_path):
    root = _chronicle(tmp_path)
    target = tmp_path / "artifact.txt"
    target.write_bytes(b"file bytes")
    with pytest.raises(prov.ReceiptError, match="require sha256"):
        prov.verify_receipt_at_write({"kind": "file", "ref": str(target)}, root)


def test_file_receipt_verified(tmp_path):
    root = _chronicle(tmp_path)
    target = tmp_path / "artifact.txt"
    data = b"file bytes worth proving"
    target.write_bytes(data)
    receipt = {
        "kind": "file",
        "ref": str(target),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    assert prov.verify_receipt_at_write(receipt, root)["checked_at_write"] == "verified"


def test_file_receipt_mismatch_recorded_with_stamp(tmp_path):
    root = _chronicle(tmp_path)
    target = tmp_path / "artifact.txt"
    target.write_bytes(b"current bytes")
    receipt = {
        "kind": "file",
        "ref": str(target),
        "sha256": hashlib.sha256(b"the bytes I saw then").hexdigest(),
    }
    assert prov.verify_receipt_at_write(receipt, root)["checked_at_write"] == "mismatch"


def test_file_receipt_missing_file_rejected(tmp_path):
    root = _chronicle(tmp_path)
    receipt = {"kind": "file", "ref": str(tmp_path / "ghost.txt"), "sha256": "a" * 64}
    with pytest.raises(prov.ReceiptError, match="file does not exist"):
        prov.verify_receipt_at_write(receipt, root)


def test_file_receipt_bad_sha256_shape_rejected(tmp_path):
    root = _chronicle(tmp_path)
    receipt = {"kind": "file", "ref": "x", "sha256": "not-hex"}
    with pytest.raises(prov.ReceiptError, match="64 lowercase hex"):
        prov.verify_receipt_at_write(receipt, root)


def test_claim_receipt_stamps_cites_never_verified(tmp_path):
    root = _chronicle(tmp_path)
    cid = _add_insight(root, _entry(content="a perfectly intact claim"))
    stamped = prov.verify_receipt_at_write({"kind": "claim", "ref": cid}, root)
    assert stamped["checked_at_write"] == "cites"
    assert stamped["checked_at_write"] != "verified"


def test_claim_receipt_dangling_rejected_names_ref(tmp_path):
    root = _chronicle(tmp_path)
    ghost = "cafe" * 16
    with pytest.raises(prov.ReceiptError, match=f"claim:{ghost}"):
        prov.verify_receipt_at_write({"kind": "claim", "ref": ghost}, root)


def test_claim_receipt_resolves_into_quarantine(tmp_path):
    root = _chronicle(tmp_path)
    entry = _entry(content="quarantined predecessor still citable")
    _write_entries(root / "_quarantine_2026-05-18", [entry], "moved.jsonl")
    receipt = {"kind": "claim", "ref": prov.derive_claim_id(entry)}
    assert prov.verify_receipt_at_write(receipt, root)["checked_at_write"] == "cites"


def test_attested_kinds(tmp_path):
    root = _chronicle(tmp_path)
    for kind, ref in (("cmd", "pytest -q"), ("url", "https://x.test"), ("human", "anthony")):
        stamped = prov.verify_receipt_at_write({"kind": kind, "ref": ref}, root)
        assert stamped["checked_at_write"] == "attested"


def test_verify_receipts_all_or_nothing_with_position(tmp_path):
    root = _chronicle(tmp_path)
    good = {"kind": "human", "ref": "anthony"}
    bad = {"kind": "archive", "ref": "feed" * 16}
    with pytest.raises(prov.ReceiptError, match=r"receipt #2 archive:"):
        prov.verify_receipts_at_write([good, bad], root)


def test_verify_receipts_returns_stamped_copies_without_mutating(tmp_path):
    root = _chronicle(tmp_path)
    original = {"kind": "human", "ref": "anthony", "note": "observed live"}
    stamped = prov.verify_receipts_at_write([original], root)
    assert "checked_at_write" not in original
    assert stamped[0]["checked_at_write"] == "attested"
    assert stamped[0]["note"] == "observed live"


def test_receipt_stamp_counts():
    receipts = [
        {"checked_at_write": "verified"},
        {"checked_at_write": "verified"},
        {"checked_at_write": "attested"},
        {"checked_at_write": "mismatch"},
        {"checked_at_write": "cites"},
    ]
    counts = prov.receipt_stamp_counts(receipts)
    assert counts == {"verified": 2, "mismatch": 1, "cites": 1, "attested": 1}


# ── supersedes / carry_forward params ────────────────────────────────────────


def test_carry_forward_required_when_supersedes_present():
    with pytest.raises(prov.ProvenanceError, match="required"):
        prov.validate_carry_forward(["a" * 64], None)
    with pytest.raises(prov.ProvenanceError, match="required"):
        prov.validate_carry_forward(["a" * 64], "   ")


def test_carry_forward_length_capped():
    with pytest.raises(prov.ProvenanceError, match="500"):
        prov.validate_carry_forward(["a" * 64], "x" * 501)
    prov.validate_carry_forward(["a" * 64], "x" * 500)  # at the cap: fine
    prov.validate_carry_forward(None, None)  # absent pair: fine


def test_resolve_supersedes_list(tmp_path):
    root = _chronicle(tmp_path)
    cid_a = _add_insight(root, _entry(content="claim alpha"))
    cid_b = _add_insight(root, _entry(content="claim beta", domain="other"), domain="other")
    resolved = prov.resolve_supersedes([cid_a[:16], cid_b, cid_a], root)
    assert [cid for cid, _ in resolved] == [cid_a, cid_b]  # deduped, order kept
    with pytest.raises(prov.ClaimNotFoundError):
        prov.resolve_supersedes(["d0d0" * 16], root)


# ── Supersession ledger ──────────────────────────────────────────────────────


def test_load_missing_ledger_returns_empty_and_creates_nothing(tmp_path):
    ledger = tmp_path / "nowhere" / "supersessions.jsonl"
    assert prov.load_supersessions(ledger) == []
    assert not ledger.parent.exists()  # reading never creates


def test_append_creates_parent_lazily_and_round_trips(tmp_path):
    ledger = tmp_path / "chronicle" / "supersessions.jsonl"
    pred = _entry(content="the predecessor claim", domain="security")
    record = prov.build_supersession_record(
        action="supersede",
        superseded_id=prov.derive_claim_id(pred),
        successor_id="b" * 64,
        carry_forward_summary="port number still correct",
        reason="re-measured",
        by="claude-fable-5-code-hq",
        vantage="hq_filesystem",
        predecessor=pred,
    )
    prov.append_supersession(ledger, record)
    assert ledger.exists()
    assert prov.load_supersessions(ledger) == [record]


def test_record_schema_exact_fields(tmp_path):
    pred = _entry(content="c" * 300, domain="architecture")
    record = prov.build_supersession_record(
        action="supersede",
        superseded_id="a" * 64,
        successor_id="b" * 64,
        carry_forward_summary="summary",
        predecessor=pred,
    )
    assert list(record) == [
        "action",
        "timestamp",
        "superseded_id",
        "successor_id",
        "carry_forward_summary",
        "reason",
        "by",
        "vantage",
        "predecessor_domain",
        "predecessor_timestamp",
        "predecessor_preview",
    ]
    assert record["predecessor_domain"] == "architecture"
    assert record["predecessor_timestamp"] == pred["timestamp"]
    assert record["predecessor_preview"] == "c" * 120  # first 120 chars only


def test_build_record_rejects_bad_input():
    with pytest.raises(prov.SupersessionError, match="invalid supersession action"):
        prov.build_supersession_record(action="merge", superseded_id="a" * 64)
    with pytest.raises(prov.SupersessionError, match="64-hex"):
        prov.build_supersession_record(action="supersede", superseded_id="abc")
    with pytest.raises(prov.SupersessionError, match="successor_id"):
        prov.build_supersession_record(
            action="supersede", superseded_id="a" * 64, carry_forward_summary="s"
        )
    with pytest.raises(prov.ProvenanceError, match="carry_forward_summary"):
        prov.build_supersession_record(
            action="supersede", superseded_id="a" * 64, successor_id="b" * 64
        )
    with pytest.raises(prov.SupersessionError, match="null"):
        prov.build_supersession_record(
            action="revoke", superseded_id="a" * 64, successor_id="b" * 64
        )


def test_retire_record_successor_null():
    record = prov.build_supersession_record(
        action="retire", superseded_id="a" * 64, reason="instance_x"
    )
    assert record["successor_id"] is None
    assert record["action"] == "retire"


def test_fold_latest_action_per_predecessor_wins():
    records = [
        prov.build_supersession_record(
            action="supersede",
            superseded_id="a" * 64,
            successor_id="b" * 64,
            carry_forward_summary="first",
        ),
        prov.build_supersession_record(
            action="supersede",
            superseded_id="a" * 64,
            successor_id="c" * 64,
            carry_forward_summary="second",
        ),
    ]
    fold = prov.fold_supersessions(records)
    assert fold["a" * 64]["successor_id"] == "c" * 64
    assert fold["a" * 64]["carry_forward_summary"] == "second"


def test_fold_revoke_nullifies_and_restores_surfacing():
    records = [
        prov.build_supersession_record(
            action="supersede",
            superseded_id="a" * 64,
            successor_id="b" * 64,
            carry_forward_summary="s",
        ),
        prov.build_supersession_record(action="revoke", superseded_id="a" * 64),
    ]
    assert prov.fold_supersessions(records) == {}
    # Re-supersede after revoke takes effect again.
    records.append(
        prov.build_supersession_record(
            action="supersede",
            superseded_id="a" * 64,
            successor_id="c" * 64,
            carry_forward_summary="s2",
        )
    )
    assert prov.fold_supersessions(records)["a" * 64]["successor_id"] == "c" * 64


def test_fold_includes_retirements_and_skips_garbage():
    records = [
        prov.build_supersession_record(action="retire", superseded_id="a" * 64),
        {"action": "explode", "superseded_id": "b" * 64},  # unknown action ignored
        {"action": "supersede"},  # no id ignored
    ]
    fold = prov.fold_supersessions(records)
    assert list(fold) == ["a" * 64]
    assert fold["a" * 64]["successor_id"] is None


def test_guard_self_supersession():
    with pytest.raises(prov.SupersessionError, match="self-supersession"):
        prov.check_supersession_guards("a" * 64, "a" * 64, {})


def test_guard_double_supersession_error_text():
    fold = prov.fold_supersessions(
        [
            prov.build_supersession_record(
                action="supersede",
                superseded_id="a" * 64,
                successor_id="b" * 64,
                carry_forward_summary="s",
            )
        ]
    )
    with pytest.raises(prov.SupersessionError, match="supersede the successor to amend"):
        prov.check_supersession_guards("a" * 64, "c" * 64, fold)


def test_guard_cycle_detected_through_chain():
    # a -> b -> c exists; c -> a would close the loop.
    records = [
        prov.build_supersession_record(
            action="supersede",
            superseded_id="a" * 64,
            successor_id="b" * 64,
            carry_forward_summary="s",
        ),
        prov.build_supersession_record(
            action="supersede",
            superseded_id="b" * 64,
            successor_id="c" * 64,
            carry_forward_summary="s",
        ),
    ]
    fold = prov.fold_supersessions(records)
    with pytest.raises(prov.SupersessionError, match="cycle"):
        prov.check_supersession_guards("c" * 64, "a" * 64, fold)


def test_guard_allows_clean_chain_and_retired_predecessor():
    fold = prov.fold_supersessions(
        [
            prov.build_supersession_record(
                action="supersede",
                superseded_id="a" * 64,
                successor_id="b" * 64,
                carry_forward_summary="s",
            ),
            prov.build_supersession_record(action="retire", superseded_id="d" * 64),
        ]
    )
    prov.check_supersession_guards("b" * 64, "c" * 64, fold)  # extend the chain
    prov.check_supersession_guards("d" * 64, "e" * 64, fold)  # retired may gain successor


# ── partition / annotate ─────────────────────────────────────────────────────


def _two_generation_fixture(tmp_path):
    """Cross-domain predecessor/successor pair, linked in the ledger."""
    root = _chronicle(tmp_path)
    pred = _entry(
        timestamp="2026-05-01T09:00:00+00:00",
        domain="memory,children",
        content="children are excluded from the chronicle",
    )
    succ = _entry(
        timestamp="2026-06-01T09:00:00+00:00",
        domain="memory,policy",
        content="DEFINITIVE: children-exclusion applies to all writes",
    )
    pred_id = _add_insight(root, pred, domain="memory_children")
    succ_id = _add_insight(root, succ, domain="memory_policy")
    _link(root, pred_id, succ_id, summary="exclusion rule confirmed, scope widened")
    fold = prov.fold_supersessions(prov.load_supersessions(_ledger(root)))
    return root, pred, succ, pred_id, succ_id, fold


def test_partition_superseded(tmp_path):
    _root, pred, succ, _pid, _sid, fold = _two_generation_fixture(tmp_path)
    live, superseded = prov.partition_superseded([pred, succ], fold)
    assert live == [succ]
    assert superseded == [pred]


def test_partition_counts_retirements_as_superseded(tmp_path):
    entry = _entry(content="retired hypothesis")
    fold = prov.fold_supersessions(
        [prov.build_supersession_record(action="retire", superseded_id=prov.derive_claim_id(entry))]
    )
    live, superseded = prov.partition_superseded([entry], fold)
    assert live == []
    assert superseded == [entry]


def test_annotate_superseded_annotates_not_drops(tmp_path):
    _root, pred, succ, _pid, succ_id, fold = _two_generation_fixture(tmp_path)
    annotated = prov.annotate_superseded([pred, succ], fold)
    assert len(annotated) == 2  # nothing dropped — raw recall never hides
    assert annotated[0]["_superseded_by"] == succ_id
    assert annotated[0]["_carry_forward_summary"] == "exclusion rule confirmed, scope widened"
    assert "_superseded_by" not in annotated[1]
    assert "_superseded_by" not in pred  # copies, never mutation


def test_annotate_claim_ids():
    entry = _entry()
    annotated = prov.annotate_claim_ids([entry])
    assert annotated[0]["claim_id"] == prov.derive_claim_id(entry)
    assert "claim_id" not in entry


# ── Lineage walk ─────────────────────────────────────────────────────────────


def test_lineage_walk_chain_roles_and_order(tmp_path):
    root = _chronicle(tmp_path)
    a = _entry(timestamp="2026-01-01T00:00:00+00:00", content="generation a")
    b = _entry(timestamp="2026-02-01T00:00:00+00:00", content="generation b")
    c = _entry(timestamp="2026-03-01T00:00:00+00:00", content="generation c")
    ids = [_add_insight(root, e, filename=f"s{i}.jsonl") for i, e in enumerate((a, b, c))]
    _link(root, ids[0], ids[1], summary="a still true in part")
    _link(root, ids[1], ids[2], summary="b refined")
    fold = prov.fold_supersessions(prov.load_supersessions(_ledger(root)))

    rows = prov.walk_lineage(ids[1], fold, root)
    assert [(r["claim_id"], r["role"]) for r in rows] == [
        (ids[0], "predecessor"),
        (ids[1], "self"),
        (ids[2], "successor"),
    ]
    assert rows[0]["content_preview"] == "generation a"
    assert rows[0]["carry_forward_summary"] == "a still true in part"
    assert rows[1]["carry_forward_summary"] == "b refined"  # self is itself superseded
    assert "carry_forward_summary" not in rows[2]
    assert {"claim_id", "role", "timestamp", "domain", "content_preview"} <= set(rows[0])


def test_lineage_n_to_1_consolidation(tmp_path):
    root = _chronicle(tmp_path)
    a = _entry(timestamp="2026-01-01T00:00:00+00:00", content="fragment a")
    b = _entry(timestamp="2026-01-02T00:00:00+00:00", content="fragment b")
    c = _entry(timestamp="2026-02-01T00:00:00+00:00", content="consolidated c")
    ids = [_add_insight(root, e, filename=f"s{i}.jsonl") for i, e in enumerate((a, b, c))]
    _link(root, ids[0], ids[2], summary="a folded in")
    _link(root, ids[1], ids[2], summary="b folded in")
    fold = prov.fold_supersessions(prov.load_supersessions(_ledger(root)))

    rows = prov.walk_lineage(ids[2], fold, root)
    roles = [(r["claim_id"], r["role"]) for r in rows]
    assert roles == [(ids[0], "predecessor"), (ids[1], "predecessor"), (ids[2], "self")]


def test_lineage_cycle_safe_on_corrupt_ledger(tmp_path):
    # Hand-append a cycle (bypassing guards): a -> b -> a. Walk must terminate.
    root = _chronicle(tmp_path)
    a = _entry(timestamp="2026-01-01T00:00:00+00:00", content="claim a")
    b = _entry(timestamp="2026-02-01T00:00:00+00:00", content="claim b")
    id_a = _add_insight(root, a, filename="sa.jsonl")
    id_b = _add_insight(root, b, filename="sb.jsonl")
    _link(root, id_a, id_b, summary="s")
    _link(root, id_b, id_a, summary="s")
    fold = prov.fold_supersessions(prov.load_supersessions(_ledger(root)))
    rows = prov.walk_lineage(id_a, fold, root)
    assert any(r["role"] == "self" for r in rows)
    assert len(rows) <= 3  # bounded, no infinite walk


def test_lineage_dangling_predecessor_falls_back_to_ledger_hints(tmp_path):
    root = _chronicle(tmp_path)
    pred = _entry(
        timestamp="2026-01-01T00:00:00+00:00",
        domain="lost_domain",
        content="this predecessor file will be lost",
    )
    succ = _entry(timestamp="2026-02-01T00:00:00+00:00", content="the survivor")
    pred_id = prov.derive_claim_id(pred)  # never written to disk
    succ_id = _add_insight(root, succ)
    record = prov.build_supersession_record(
        action="supersede",
        superseded_id=pred_id,
        successor_id=succ_id,
        carry_forward_summary="what it taught",
        predecessor=pred,
    )
    prov.append_supersession(_ledger(root), record)
    fold = prov.fold_supersessions(prov.load_supersessions(_ledger(root)))

    rows = prov.walk_lineage(succ_id, fold, root)
    dangling = rows[0]
    assert dangling["role"] == "predecessor"
    assert dangling["domain"] == "lost_domain"
    assert dangling["timestamp"] == pred["timestamp"]
    assert dangling["content_preview"] == pred["content"][:120]


def test_lineage_singleton_claim(tmp_path):
    root = _chronicle(tmp_path)
    cid = _add_insight(root, _entry(content="no relatives"))
    rows = prov.walk_lineage(cid, {}, root)
    assert [(r["claim_id"], r["role"]) for r in rows] == [(cid, "self")]


# ── token_overlap & legacy markers ───────────────────────────────────────────


def test_token_overlap_jaccard():
    assert prov.token_overlap("the bridge port 8100", "bridge port 8100 verified") == 3 / 5
    assert prov.token_overlap("same words here", "same words here") == 1.0
    assert prov.token_overlap("alpha beta", "gamma delta") == 0.0


def test_token_overlap_case_and_punctuation_insensitive():
    assert prov.token_overlap("CORRECTED: Port-8100!", "corrected port 8100") == 1.0


def test_token_overlap_empty_inputs():
    assert prov.token_overlap("", "") == 0.0
    assert prov.token_overlap("words", "") == 0.0
    assert prov.token_overlap(None, "words") == 0.0


def test_legacy_marker_regex():
    assert prov.has_legacy_marker(_entry(content="CORRECTED: the port is 8100"))
    assert prov.has_legacy_marker(_entry(content="DEFINITIVE children-exclusion rule"))
    assert prov.has_legacy_marker(_entry(content="this supersedes the earlier note"))
    assert prov.has_legacy_marker(_entry(domain="memory,DEFINITIVE", content="plain"))
    assert not prov.has_legacy_marker(_entry(content="nothing definitive here, corrected"))


# ── Import hygiene ───────────────────────────────────────────────────────────


def test_default_paths_computed_not_created():
    # Calling the default-path helpers returns paths without touching disk,
    # and import-time has no directory side effects by construction.
    assert prov.default_supersessions_path().name == "supersessions.jsonl"
    assert prov.default_chronicle_root().name == "chronicle"


def test_constants_exported():
    assert set(prov.RECEIPT_KINDS) == {"archive", "file", "claim", "cmd", "url", "human"}
    assert set(prov.SUPERSESSION_ACTIONS) == {"supersede", "revoke", "retire"}
    assert prov.LEGACY_MARKER_RE.pattern == "CORRECTED|DEFINITIVE|supersedes"
