"""
SAYING "supersedes" IS NOT SUPERSEDING.

Two entries in the 2026-08-27 window state a supersession in their body prose
and carry NO ``supersedes`` field. The ledger therefore has no record, the
predecessor keeps surfacing as live, and ``inspect_claim`` on either id shows an
unbroken claim — while a human reading the chronicle sees the word and believes
the chain moved. The entry looks like governance and is decoration.

This is the write-side twin of every fail-open in this house: the surface
accepted the call and reported success, and the failure was legible only to a
reader who already knew to doubt it.

THE FIX IS A REFUSAL, NOT AN AUTOFILL. Deriving the field from the prose would
be the stack guessing at a governance act — and a wrong guess writes a
supersession ledger record nobody authored. ``carry_forward_summary`` (what the
predecessor still teaches) cannot be inferred at all; that is the whole point of
requiring it. So the write is REFUSED, the id is named, and the caller is told
the two arguments to pass. Same shape as validate_carry_forward.

Bounds that keep the guard from becoming a nuisance — all pinned below:
  * the word alone, with no claim id, is ordinary prose and is accepted;
  * "supersedes" as a DOMAIN label is untouched;
  * a body that names an id AND passes the field is accepted (the field is the
    governance act; the prose is then just narration).

Everything runs against a tmp chronicle root. Nothing writes ~/.sovereign.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.provenance import ProvenanceError

CLAIM_ID = "a3f19c47b2e85d06f1c4a9e37b0d5628ff4a1c93e7b26d80a5f3c19e4b7d0286"
OTHER_ID = "0b1c2d3e4f5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"


@pytest.fixture
def memory(tmp_sovereign_root: Path) -> ExperientialMemory:
    return ExperientialMemory(root=str(tmp_sovereign_root / "chronicle"))


# ── Refused ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        f"SUPERSEDES claim {CLAIM_ID} — the earlier reading was measured wrong.",
        f"This entry supersedes {CLAIM_ID}.",
        f"supersedes: {CLAIM_ID}",
        f"Superseding note.\n\nSupersedes claim {CLAIM_ID}\n\nCarry forward: the "
        "method still holds.",
        f"supersedes claim  {CLAIM_ID.upper()}",
    ],
)
def test_prose_supersession_without_the_field_is_refused(memory: ExperientialMemory, body: str):
    with pytest.raises(ProvenanceError) as exc:
        memory.record_insight(domain="governance", content=body)

    msg = str(exc.value)
    assert CLAIM_ID in msg.lower(), "the refusal must name the id it found"
    assert "supersedes=" in msg
    assert "carry_forward_summary" in msg


def test_the_refusal_writes_nothing(memory: ExperientialMemory):
    """Fail CLOSED: a refused write leaves no file, no ledger, no partial entry."""
    with pytest.raises(ProvenanceError):
        memory.record_insight(domain="governance", content=f"supersedes claim {CLAIM_ID}")

    assert list((memory.insights_dir).rglob("*.jsonl")) == []
    assert not memory.supersessions_path.exists()


# ── Accepted ─────────────────────────────────────────────────────────────────


def test_the_same_body_with_the_field_and_a_summary_is_accepted(
    memory: ExperientialMemory,
):
    predecessor = memory.record_insight(
        domain="governance", content="the original reading", return_claim_id=True
    )

    path = memory.record_insight(
        domain="governance",
        content=f"SUPERSEDES claim {predecessor.claim_id} — the earlier reading was wrong.",
        supersedes=[predecessor.claim_id],
        carry_forward_summary="the measurement method still holds; only the value moved",
    )
    # The return string is decorated (' ⊃ supersedes N') when supersedes was used.
    assert Path(str(path).split(" ⊃")[0]).exists()
    assert memory.supersessions_path.exists()


def test_the_word_without_an_id_is_ordinary_prose(memory: ExperientialMemory):
    path = memory.record_insight(
        domain="governance",
        content=(
            "Nothing here supersedes the standing policy — this is a note about "
            "how supersession is supposed to work, with no claim id in it."
        ),
    )
    assert Path(path).exists()


def test_supersedes_as_a_domain_label_is_untouched(memory: ExperientialMemory):
    path = memory.record_insight(domain="supersedes", content="a domain can be called anything")
    assert Path(path).exists()
    assert "supersedes" in str(path)


def test_a_bare_claim_id_with_no_supersession_language_is_accepted(
    memory: ExperientialMemory,
):
    """The guard keys on the WORD plus an id nearby, not on the presence of a
    hex string — chronicle entries quote claim ids constantly."""
    path = memory.record_insight(
        domain="governance",
        content=f"See claim {CLAIM_ID} for the original measurement.",
    )
    assert Path(path).exists()


def test_an_id_far_from_the_word_is_not_treated_as_a_supersession(
    memory: ExperientialMemory,
):
    filler = "x" * 400
    path = memory.record_insight(
        domain="governance",
        content=f"A note on how supersedes works.\n{filler}\nUnrelated: claim {OTHER_ID}.",
    )
    assert Path(path).exists()


def test_a_short_hex_string_is_not_a_claim_id(memory: ExperientialMemory):
    path = memory.record_insight(domain="governance", content=f"supersedes claim {CLAIM_ID[:40]}")
    assert Path(path).exists()


def test_a_longer_hex_run_is_not_a_claim_id(memory: ExperientialMemory):
    """A 64-char window inside a 90-char hex blob must not read as an id."""
    path = memory.record_insight(domain="governance", content=f"supersedes blob {'ab' * 45}")
    assert Path(path).exists()


# ── Calibrated against the LIVE corpus, not against invented examples ────────
#
# The first draft of this guard allowed 120 characters of anything between the
# word and the id. Run over all 3,445 chronicle entries on 2026-08-30 it matched
# FOUR, of which one was the defect and three were entropy-program registration
# entries where the word governs a *run* and the matched hex is an unrelated
# artifact digest a hundred characters downstream. 25% precision on the store it
# protects is a tax, not a guard — and the author it taxes learns to route
# around it. The shapes below are transcribed from those real entries.
#
# After tightening to adjacency: 2 of 3,445 match, and both genuinely state a
# supersession with the id as the object.


def test_the_entropy_registration_shape_is_accepted(memory: ExperientialMemory):
    """Real body, entropy-v4.4 2026-07-25. `supersedes` governs a RUN; the hex is
    the driver script's sha256 in the next sentence. Must not be refused."""
    path = memory.record_insight(
        domain="entropy-v4.4",
        content=(
            "PRE-DECLARATION -- clean CRN calibration re-run, filed BEFORE execution, "
            "per the void ruling onepct-calibration-void-ruling-20260724. New namespace, "
            "supersedes the ~19:31 void run.  DRIVER (hash-locked before any seed): "
            f"crn_calib_v2.py sha256 {CLAIM_ID}. Its CRN coupling functions are frozen."
        ),
    )
    assert Path(path).exists()


def test_the_prereg_parenthetical_shape_is_accepted(memory: ExperientialMemory):
    """Real body, mesh-20260713. `(supersedes b7c5aeb6..., noted inside)` — a
    TRUNCATED id, followed much later by an unrelated certification hash."""
    path = memory.record_insight(
        domain="mesh-20260713",
        content=(
            "THE v4.4 PACKAGE IS PINNED. prereg_v44.json v2 c970ad40b8880ed7 "
            "(supersedes b7c5aeb6..., noted inside) | config_hash ba7c854ebc825476 | "
            f"certification NDJSON {CLAIM_ID} (byte-reproducible, two runs)"
        ),
    )
    assert Path(path).exists()


def test_the_retraction_shape_is_refused(memory: ExperientialMemory):
    """Real body, 2026-08-27 — one of the two entries this guard exists for.
    `Supersedes the claim in claim <64-hex>`, no field."""
    with pytest.raises(ProvenanceError) as exc:
        memory.record_insight(
            domain="sovereign-stack,retraction",
            content=(
                "RETRACTION - THE FOUNDING IMAGE WAS SEALED, AND HQ'S 'NEVER SEALED' "
                f"CLAIM WAS A FILTER THAT COULD NOT MATCH. Supersedes the claim in claim "
                f"{CLAIM_ID} and in the dig filing."
            ),
        )
    assert CLAIM_ID in str(exc.value).lower()
