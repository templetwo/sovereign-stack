"""
Tests for the supersession candidate detector (supersession_candidates.py).

This module is a DETECTOR, not an actuator: it emits
(marker, best_overlap_predecessor, jaccard_score) triples for human
review and never writes to the chronicle or the supersession ledger.
These tests pin that invariant (zero auto-promotion, read-only against
the chronicle tree) alongside the detection logic itself: the
case-insensitive marker gap has_legacy_marker misses, the
already-ledgered exclusion, best-overlap-predecessor selection with and
without the require_older ordering guard, threshold behavior (the
"stays prose" bucket), and — the one the brief insists on — a
constructed case where the detector DOES produce a false positive, so
the precision claim in the accompanying report is tested, not assumed.

Hermetic — everything lives under tmp_path; ~/.sovereign is never
touched, and no test depends on real chronicle content.
"""

import json
from pathlib import Path

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.protected import designate_protected
from sovereign_stack.provenance import (
    build_supersession_record,
    derive_claim_id,
    display_id,
    has_legacy_marker,
)
from sovereign_stack.supersession_candidates import (
    best_overlap_predecessor,
    detect_candidates,
    find_prose_only_markers,
    is_prose_marker,
    render_candidates_markdown,
    run_against_chronicle,
)

# ── Fixture helpers (mirrors tests/test_provenance.py's convention) ──────────


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
    domain_dir = root / "insights" / (domain or entry.get("domain", "misc"))
    _write_entries(domain_dir, [entry], filename)
    return derive_claim_id(entry)


def _hash_tree(root: Path) -> dict[str, str]:
    import hashlib

    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ── The case-insensitivity gap this module exists to close ──────────────────


class TestMarkerPredicate:
    def test_all_caps_supersedes_is_a_prose_marker(self):
        e = _entry(content="THIS SUPERSEDES the earlier reading of the release ledger.")
        assert is_prose_marker(e)

    def test_all_caps_supersedes_is_invisible_to_legacy_marker_re(self):
        # The exact gap: provenance.has_legacy_marker's regex is
        # case-SENSITIVE and only matches lowercase "supersedes" — the
        # capitalization the brief itself uses, and several real
        # chronicle entries use, is invisible to it. This test pins the
        # documented reason supersession_candidates does not reuse
        # has_legacy_marker unmodified.
        e = _entry(content="THIS SUPERSEDES the earlier reading of the release ledger.")
        assert not has_legacy_marker(e)
        assert is_prose_marker(e)

    def test_lowercase_and_mixed_case_all_match(self):
        for word in ("corrected", "Corrected", "CORRECTED", "definitive", "DEFINITIVE"):
            assert is_prose_marker(_entry(content=f"the prior note was {word} today"))

    def test_domain_side_marker_also_matches(self):
        e = _entry(domain="policy-DEFINITIVE-revision", content="plain content, no marker word")
        assert is_prose_marker(e)

    def test_no_marker_word_does_not_match(self):
        e = _entry(content="a perfectly ordinary observation with no marker language")
        assert not is_prose_marker(e)


# ── Already-ledgered exclusion ────────────────────────────────────────────────


class TestFindProseOnlyMarkers:
    def test_marker_not_yet_ledgered_is_returned(self):
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            domain="d",
            content="SUPERSEDES the earlier note entirely",
        )
        other = _entry(timestamp="2026-06-11T10:00:00+00:00", domain="d", content="unrelated")
        found = find_prose_only_markers([marker, other], sup_records=[])
        assert found == [marker]

    def test_marker_already_landed_as_successor_is_excluded(self):
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            domain="d",
            content="SUPERSEDES the earlier note entirely",
        )
        pred = _entry(timestamp="2026-06-11T10:00:00+00:00", domain="d", content="earlier note")
        record = build_supersession_record(
            action="supersede",
            superseded_id=derive_claim_id(pred),
            successor_id=derive_claim_id(marker),
            carry_forward_summary="carried",
            predecessor=pred,
        )
        found = find_prose_only_markers([marker, pred], sup_records=[record])
        assert found == []

    def test_revoked_link_does_not_reopen_the_marker(self):
        # A revoke record nullifies the PREDECESSOR's fold entry (it can
        # be superseded again), but the successor_id was still spent by
        # a real action once — find_prose_only_markers checks the raw
        # record list, not the fold, precisely so a revoke doesn't
        # quietly reintroduce this marker as an unlandeded candidate.
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00", domain="d", content="SUPERSEDES the note"
        )
        pred = _entry(timestamp="2026-06-11T10:00:00+00:00", domain="d", content="earlier note")
        supersede = build_supersession_record(
            action="supersede",
            superseded_id=derive_claim_id(pred),
            successor_id=derive_claim_id(marker),
            carry_forward_summary="carried",
            predecessor=pred,
        )
        revoke = build_supersession_record(action="revoke", superseded_id=derive_claim_id(pred))
        found = find_prose_only_markers([marker, pred], sup_records=[supersede, revoke])
        assert found == []


# ── best_overlap_predecessor ─────────────────────────────────────────────────


class TestBestOverlapPredecessor:
    def test_finds_the_high_overlap_older_entry(self):
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            content="the jetson swap file lives on nvme at slot two and is verified daily",
        )
        good_pred = _entry(
            timestamp="2026-06-11T10:00:00+00:00",
            content="the jetson swap file lives on nvme at slot two",
        )
        noise = _entry(timestamp="2026-06-10T10:00:00+00:00", content="completely unrelated text")
        pred, score = best_overlap_predecessor(marker, [marker, good_pred, noise])
        assert pred is good_pred
        assert score > 0.5

    def test_excludes_self_by_identity_not_equality(self):
        # A byte-identical duplicate of the marker is a DISTINCT pool
        # member and must still be excluded only by object identity —
        # excluding by equality would also (wrongly) exclude the
        # duplicate as a legitimate predecessor candidate.
        content = "the exact same words twice, once older once as the marker"
        marker = _entry(timestamp="2026-06-12T10:00:00+00:00", content=content)
        duplicate_older = _entry(timestamp="2026-06-11T10:00:00+00:00", content=content)
        pred, score = best_overlap_predecessor(marker, [marker, duplicate_older])
        assert pred is duplicate_older
        assert score == 1.0

    def test_require_older_excludes_a_newer_high_overlap_entry(self):
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            content="alpha bravo charlie delta echo foxtrot",
        )
        newer_twin = _entry(
            timestamp="2026-06-13T10:00:00+00:00",  # AFTER marker
            content="alpha bravo charlie delta echo foxtrot",
        )
        older_partial = _entry(
            timestamp="2026-06-11T10:00:00+00:00", content="alpha bravo charlie only"
        )
        pred, score = best_overlap_predecessor(
            marker, [marker, newer_twin, older_partial], require_older=True
        )
        assert pred is older_partial
        assert score < 1.0

    def test_require_older_false_allows_the_newer_match_through(self):
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            content="alpha bravo charlie delta echo foxtrot",
        )
        newer_twin = _entry(
            timestamp="2026-06-13T10:00:00+00:00", content="alpha bravo charlie delta echo foxtrot"
        )
        pred, score = best_overlap_predecessor(marker, [marker, newer_twin], require_older=False)
        assert pred is newer_twin
        assert score == 1.0

    def test_no_candidates_returns_none_and_zero(self):
        marker = _entry(timestamp="2026-06-12T10:00:00+00:00", content="alpha bravo")
        pred, score = best_overlap_predecessor(marker, [marker])
        assert pred is None
        assert score == 0.0

    def test_missing_timestamp_never_satisfies_ordering(self):
        marker = _entry(timestamp="", content="alpha bravo charlie")
        other = _entry(timestamp="2026-06-01T00:00:00+00:00", content="alpha bravo charlie")
        pred, score = best_overlap_predecessor(marker, [marker, other], require_older=True)
        assert pred is None
        assert score == 0.0


# ── detect_candidates: threshold, sorting, and the "stays prose" bucket ──────


class TestDetectCandidates:
    def test_above_threshold_becomes_a_candidate(self):
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            domain="edge",
            content="SUPERSEDES: the orin swap file lives on nvme at slot two, relocated",
        )
        pred = _entry(
            timestamp="2026-06-11T10:00:00+00:00",
            domain="edge",
            content="the orin swap file lives on nvme at slot two",
        )
        candidates, unrecoverable = detect_candidates([marker, pred], sup_records=[])
        assert len(candidates) == 1
        assert candidates[0].marker_id == display_id(derive_claim_id(marker))
        assert candidates[0].predecessor_id == display_id(derive_claim_id(pred))
        assert unrecoverable == []

    def test_below_threshold_stays_prose(self):
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            content="CORRECTED: an entirely freeform reflection sharing almost no words",
        )
        noise = _entry(timestamp="2026-06-11T10:00:00+00:00", content="zzz yyy xxx www vvv uuu")
        candidates, unrecoverable = detect_candidates([marker, noise], sup_records=[])
        assert candidates == []
        assert unrecoverable == [marker]

    def test_sorted_by_confidence_descending(self):
        low_marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            domain="a",
            content="DEFINITIVE alpha bravo charlie delta zzz yyy xxx",
        )
        low_pred = _entry(
            timestamp="2026-06-11T10:00:00+00:00",
            domain="a",
            content="alpha bravo charlie www vvv",
        )
        high_marker = _entry(
            timestamp="2026-06-12T11:00:00+00:00",
            domain="b",
            content="DEFINITIVE the swap file lives on nvme at slot two exactly",
        )
        high_pred = _entry(
            timestamp="2026-06-11T11:00:00+00:00",
            domain="b",
            content="the swap file lives on nvme at slot two",
        )
        candidates, _ = detect_candidates(
            [low_marker, low_pred, high_marker, high_pred], sup_records=[]
        )
        assert len(candidates) == 2
        assert candidates[0].jaccard_score >= candidates[1].jaccard_score
        assert candidates[0].marker_id == display_id(derive_claim_id(high_marker))

    def test_already_ledgered_marker_produces_no_candidate(self):
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            domain="d",
            content="SUPERSEDES the earlier note entirely and completely",
        )
        pred = _entry(timestamp="2026-06-11T10:00:00+00:00", domain="d", content="earlier note")
        record = build_supersession_record(
            action="supersede",
            superseded_id=derive_claim_id(pred),
            successor_id=derive_claim_id(marker),
            carry_forward_summary="carried",
            predecessor=pred,
        )
        candidates, unrecoverable = detect_candidates([marker, pred], sup_records=[record])
        assert candidates == []
        assert unrecoverable == []  # not a candidate AND not "stays prose" — already landed


# ── The precision drill: the detector MUST be able to produce a false positive ──


class TestFalsePositiveCapability:
    def test_quote_only_entry_that_shares_the_predecessors_own_words_is_flagged(self):
        """
        This is the constructed failure the brief demands proof of: an
        entry DISCUSSING supersession (quoting a real predecessor's
        wording while explaining the mechanism, not retiring anything)
        scores high enough on token-Jaccard to be emitted as a
        candidate. The marker regex and the overlap score both fire
        for the same reason a genuine performing entry would: shared
        vocabulary. This is not a bug this test papers over — it is
        the exact shape of error the brief says every hit needs a
        human's eyes for, reproduced deterministically so the
        precision measurement in the report is backed by a real,
        inspectable case rather than an assertion that trusts itself.
        """
        predecessor = _entry(
            timestamp="2026-06-11T10:00:00+00:00",
            domain="release-notes",
            content="the orin swap file lives on nvme at slot two, verified daily by the doctor",
        )
        quote_only_marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            domain="mechanism-recon",
            content=(
                "RECON NOTE (discussing, not performing): season_review's SUPERSEDES marker "
                "sweep would flag an entry like 'the orin swap file lives on nvme at slot two, "
                "verified daily by the doctor' as a predecessor purely on shared wording -- this "
                "note is explaining that mechanism, not correcting the swap-file record itself."
            ),
        )
        candidates, _ = detect_candidates(
            [quote_only_marker, predecessor], sup_records=[], min_score=0.3
        )
        assert len(candidates) == 1, (
            "expected the quote-only entry to be misflagged as a candidate here -- if this "
            "assertion fails because the score dropped, the detector has gotten MORE precise "
            "on this constructed case, but the report's false-positive claim must be "
            "re-verified against a fresh constructed case rather than silently trusted"
        )
        c = candidates[0]
        assert c.marker_id == display_id(derive_claim_id(quote_only_marker))
        assert c.predecessor_id == display_id(derive_claim_id(predecessor))
        # The whole point: this pairing is WRONG. quote_only_marker never
        # intended to retire `predecessor` — it was explaining a mechanism.
        # A human reviewing this row via inspect_claim() would reject it.


# ── Rendering: structural fields only, never a content preview ──────────────


class TestRenderCandidatesMarkdown:
    def test_content_text_never_appears_in_rendered_output(self):
        secret_marker_phrase = "the grief around frank stays private and unquoted here"
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            domain="personal-domain",
            content=f"SUPERSEDES the earlier note. {secret_marker_phrase}",
        )
        pred = _entry(
            timestamp="2026-06-11T10:00:00+00:00",
            domain="personal-domain",
            content=f"earlier note. {secret_marker_phrase}",
        )
        candidates, unrecoverable = detect_candidates([marker, pred], sup_records=[])
        rendered = render_candidates_markdown(
            candidates, unrecoverable, min_score=0.3, total_markers=1
        )
        assert secret_marker_phrase not in rendered
        # Structural fields (ids, domain, timestamp, score) DO belong.
        assert display_id(derive_claim_id(marker)) in rendered
        assert "personal-domain" in rendered
        assert "score" in rendered

    def test_stayed_prose_section_lists_ids_only(self):
        marker = _entry(
            timestamp="2026-06-12T10:00:00+00:00",
            domain="d",
            content="CORRECTED but shares no real vocabulary with anything else at all",
        )
        candidates, unrecoverable = detect_candidates([marker], sup_records=[])
        rendered = render_candidates_markdown(
            candidates, unrecoverable, min_score=0.3, total_markers=1
        )
        assert "Stayed prose" in rendered
        assert display_id(derive_claim_id(marker)) in rendered


# ── Zero auto-promotion & read-only invariants ───────────────────────────────


class TestZeroAutoPromotion:
    def test_module_has_no_write_capable_import(self):
        import sovereign_stack.supersession_candidates as sc

        assert not hasattr(sc, "append_supersession")
        assert not hasattr(sc, "supersede_insight")
        assert not hasattr(sc, "build_supersession_record")

    def test_run_against_chronicle_never_writes_under_chronicle_root(self, tmp_path):
        root = _chronicle(tmp_path)
        _add_insight(
            root,
            _entry(
                timestamp="2026-06-12T10:00:00+00:00",
                domain="edge",
                content="SUPERSEDES the orin swap file note entirely",
            ),
            domain="edge",
        )
        _add_insight(
            root,
            _entry(
                timestamp="2026-06-11T10:00:00+00:00",
                domain="edge",
                content="the orin swap file lives on nvme",
            ),
            domain="edge",
        )
        before = _hash_tree(root)
        assert not (root / "supersessions.jsonl").exists()

        output_path = tmp_path / "artifacts" / "candidates.md"
        candidates, _ = run_against_chronicle(root, output_path)

        after = _hash_tree(root)
        assert before == after, "run_against_chronicle must not modify the chronicle tree"
        assert not (root / "supersessions.jsonl").exists(), (
            "the ledger must not spring into existence from a detector run"
        )
        assert output_path.exists()
        assert output_path.is_relative_to(tmp_path)  # never under chronicle_root's own tree

    def test_designated_protected_record_never_enters_the_scan(self, tmp_path):
        """
        Mirrors season_review's own protected-fold exclusion (seasons.py):
        run_against_chronicle must drop a designated protected record
        BEFORE detection runs, not trust a downstream renderer to
        withhold it. Built via the real designate_protected path (not a
        hand-rolled protected.jsonl line) so this exercises the actual
        gate, same as tests/test_protected_source.py.
        """
        mem = ExperientialMemory(root=str(tmp_path / "chronicle"))
        protected_path = mem.record_insight(
            domain="personal",
            content="SUPERSEDES the earlier private family note entirely",
            intensity=0.9,
            layer="ground_truth",
        )
        protected_entry = json.loads(Path(protected_path).read_text().splitlines()[-1])
        mem.record_insight(
            domain="personal",
            content="the earlier private family note",
            intensity=0.9,
            layer="ground_truth",
        )
        # designate_protected needs a resolvable stakes archive.
        archive = mem.archive_exchange(
            content="the weight this carries for the human, held as lived experience",
            source="human-relay",
            descriptor="test stakes",
            vector_id="test_stakes",
        )
        designate_protected(
            claim_ref=derive_claim_id(protected_entry),
            stakes_archive_id=archive["archive_id"],
            designated_by="Anthony",
            chronicle_root=str(mem.root),
            subject="test-subject",
            emotion="test-emotion",
            reason="unit test designation",
        )

        output_path = tmp_path / "candidates.md"
        candidates, unrecoverable = run_against_chronicle(mem.root, output_path)

        protected_id = display_id(derive_claim_id(protected_entry))
        assert protected_id not in {c.marker_id for c in candidates}
        assert protected_id not in {c.predecessor_id for c in candidates}
        assert protected_id not in {display_id(derive_claim_id(m)) for m in unrecoverable}
        rendered = output_path.read_text(encoding="utf-8")
        assert protected_id not in rendered
        assert "SUPERSEDES the earlier private family note" not in rendered

    def test_output_file_is_written_and_readable(self, tmp_path):
        root = _chronicle(tmp_path)
        _add_insight(
            root,
            _entry(
                timestamp="2026-06-12T10:00:00+00:00",
                domain="edge",
                content="SUPERSEDES the orin swap file note entirely",
            ),
            domain="edge",
        )
        _add_insight(
            root,
            _entry(
                timestamp="2026-06-11T10:00:00+00:00",
                domain="edge",
                content="the orin swap file lives on nvme",
            ),
            domain="edge",
        )
        output_path = tmp_path / "candidates.md"
        candidates, unrecoverable = run_against_chronicle(root, output_path)
        text = output_path.read_text(encoding="utf-8")
        assert "Supersession candidate detector" in text
        assert "ZERO AUTO-PROMOTION" in text
        assert len(candidates) == 1
