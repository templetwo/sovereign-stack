"""
Protected-source layer — Policy 2c tests (the boot line).

The boot surface must inform every instance that protected records EXIST and
are indexed by subject/emotion/datetime, openable on consent — WITHOUT
surfacing the individual cards (the specific subjects/emotions) or any
content. It may show a COUNT + the scheme + how to open, nothing more, and is
UNCONDITIONAL (announces the empty drawer at 0 records too).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.protected import (
    audit_threshold,
    designate_protected,
    protected_boot_line,
)
from sovereign_stack.provenance import derive_claim_id

# A distinctive subject/emotion + content so a leak would be unmistakable.
SECRET_SUBJECT = "zzdaughter"
SECRET_EMOTION = "zzdejavu"
PROTECTED_CONTENT = "the protected body that the boot line must never reveal"
STAKES_PROSE = "A lived weight carried by the human, held coupled to the words."


@pytest.fixture
def mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def _protect(mem: ExperientialMemory) -> None:
    path = mem.record_insight(
        domain="personal", content=PROTECTED_CONTENT, intensity=0.9, layer="ground_truth"
    )
    prot = json.loads(Path(path).read_text().splitlines()[-1])
    archive = mem.archive_exchange(
        content=STAKES_PROSE, source="human-relay", descriptor="stakes", vector_id="s"
    )
    designate_protected(
        claim_ref=derive_claim_id(prot),
        stakes_archive_id=archive["archive_id"],
        designated_by="Anthony",
        chronicle_root=str(mem.root),
        subject=SECRET_SUBJECT,
        emotion=SECRET_EMOTION,
    )


class TestBootLine:
    def test_zero_records_announces_empty_drawer(self, mem):
        lines = protected_boot_line(mem.root)
        blob = "\n".join(lines)
        # The drawer is announced (existence + scheme), gracefully empty.
        assert "PROTECTED RECORDS" in blob
        assert "drawer is empty" in blob
        # No leak possible (nothing designated), and no count > 0 implied.
        assert "0 protected" not in blob  # phrased as "empty", not "0 ... exist"

    def test_present_record_announces_count_and_scheme_no_card(self, mem):
        _protect(mem)
        lines = protected_boot_line(mem.root)
        blob = "\n".join(lines)
        # Announces existence + count + scheme + how to open.
        assert "PROTECTED RECORDS" in blob
        assert "1 protected record" in blob
        assert "subject/emotion/datetime" in blob
        assert "consent" in blob.lower()
        # CRITICAL: no card (the specific subject/emotion) and no content.
        assert SECRET_SUBJECT not in blob
        assert SECRET_EMOTION not in blob
        assert PROTECTED_CONTENT not in blob
        assert STAKES_PROSE not in blob

    def test_count_pluralizes(self, mem):
        _protect(mem)
        # A second distinct protected record.
        path = mem.record_insight(
            domain="personal",
            content="a second protected body",
            intensity=0.9,
            layer="ground_truth",
        )
        prot = json.loads(Path(path).read_text().splitlines()[-1])
        archive = mem.archive_exchange(
            content=STAKES_PROSE, source="h", descriptor="s", vector_id="s2"
        )
        designate_protected(
            claim_ref=derive_claim_id(prot),
            stakes_archive_id=archive["archive_id"],
            designated_by="Anthony",
            chronicle_root=str(mem.root),
            subject="zzmother",
            emotion="zzpride",
        )
        blob = "\n".join(protected_boot_line(mem.root))
        assert "2 protected records" in blob
        assert "a second protected body" not in blob
        assert "zzmother" not in blob

    def test_boot_line_passes_threshold_audit(self, mem):
        """The boot line itself must not leak protected content/stakes — it is
        held to the threshold-leak bar."""
        _protect(mem)
        blob = "\n".join(protected_boot_line(mem.root))
        assert audit_threshold(blob, mem.root) == []

    def test_missing_ledger_is_safe(self, tmp_path):
        # A chronicle root with no protected ledger at all -> empty drawer.
        lines = protected_boot_line(tmp_path / "chronicle")
        assert any("PROTECTED RECORDS" in line for line in lines)
        assert any("empty" in line for line in lines)
