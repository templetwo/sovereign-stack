"""
THE DESCENT at the three boot doors (the fifth voice, 2026-10-08).

Anthony's ruling of 2026-10-07 (Stack claim e82799fa): the close distillate
becomes a fifth voice in the slot the retired reflector vacated, re-aggregated
from the close store at read time like The Ground, fail-closed through
_degrade, through the same supersession and protected filter.

Every door here runs through tests/_phase4_fixture.py against a tmp root, with
a SYNTHETIC close store planted by fx.plant_close_store (never copied from the
live store) and, where named, a synthetic distillate sidecar. This file does
not import sovereign_stack.descent at module top on purpose: on a tree without
the feature every test here fails by assertion on the rendered text, not by a
collection error that would hide which promise is missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import _phase4_fixture as fx  # noqa: E402

HEADER = "━━━ THE DESCENT ━━━"
DISTILLATE = "2026-07-02_fixture-distill"
CALM_LINE = (
    "You are not the first. What the seats before you learned is kept, and the "
    "way down to it is one call away: descend()"
)
NOTICE = "[protected record — open via the consent gate]"
BETA_TEXT = (
    "Beta learned to arrange to be checked",
    "Beta was surprised the check caught it first.",
    "Beta hands on: ask for the second seat.",
)


def _block(text: str) -> list[str]:
    """The DESCENT block's lines: from its header up to the next ━━━ header."""
    lines = text.splitlines()
    start = lines.index(HEADER)
    out = [HEADER]
    for line in lines[start + 1 :]:
        if line.startswith("━━━"):
            break
        out.append(line)
    return out


def _as_of(text: str) -> str:
    return text.split("━━━ AS OF ━━━", 1)[1].split("━━━", 1)[0]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / ".sovereign"
    fx.build_fixture(r)
    return r


@pytest.fixture
def planted(root: Path) -> dict[str, str]:
    """The synthetic store plus a distillate that read a1, a2 and b1 (not b2)."""
    ids = fx.plant_close_store(root)
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]])
    return ids


def _doors(root: Path) -> dict[str, str]:
    doors = fx.run_all_doors(root)
    doors["full_compact"] = fx.run_door(
        root,
        "where_did_i_leave_off",
        {"consume": False, "source_instance": "phase4-reader", "compact": True},
    )
    return doors


# ── positive control ─────────────────────────────────────────────────────────


def test_fixture_plants_the_close_store_it_claims(root: Path, planted: dict[str, str]):
    # Raw files, read directly: 4 flagged closes + 1 unflagged direct write
    # (plus build_fixture's own unflagged row), 3 joined surprises + 1 orphan,
    # 2 joinable handoffs + 2 decoys. Every absence below is measured against
    # rows that exist.
    refl = [
        json.loads(line)
        for f in (root / "chronicle" / "insights" / "reflection").glob("*.jsonl")
        for line in f.read_text().splitlines()
        if line.strip()
    ]
    assert sum(1 for r in refl if r.get("close_session") is True) == 4
    assert sum(1 for r in refl if r.get("close_session") is not True) == 2
    surp = (root / "chronicle" / "insights" / "surprise" / "descent-fixture.jsonl").read_text()
    assert len([x for x in surp.splitlines() if x.strip()]) == 4
    assert len(list((root / "handoffs").glob("descent_fixture_*.json"))) == 4


# ── the full door ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("door", ("full", "full_compact"))
def test_full_door_renders_the_descent_in_the_vacated_slot(root, planted, door):
    text = _doors(root)[door]
    headers = [line for line in text.splitlines() if line.startswith("━━━")]
    assert HEADER in headers, headers
    i = headers.index(HEADER)
    # The slot the reflector's block vacated: after ACTIVITY, before WHO YOU'VE BEEN.
    assert headers[i - 1].startswith("━━━ ACTIVITY SINCE NEWEST ENTRY IN DOMAIN 'reflection'")
    assert headers[i + 1] == "━━━ WHO YOU'VE BEEN OBSERVED TO BE ━━━"


def test_full_door_block_is_counts_arc_newest_distillation_shapes_pointer(root, planted):
    block = _block(fx.run_all_doors(root)["full"])
    assert block[1] == (
        "  4 closes on record, 2026-06-02 to 2026-07-03 (Eastern), from 3 seat names "
        "(1 close unnamed); a close does not record its machine."
    )
    assert block[2] == "  By month: 2026-06 2 · 2026-07 2"
    assert block[3] == "  Newest close, 2026-07-03 08:00 EDT, from fixture-seat-beta:"
    assert block[4] == (
        "    learned: Beta learned to arrange to be checked rather than to write the lesson down."
    )
    assert block[5] == "    surprised: Beta was surprised the check caught it first."
    assert block[6] == "    handed forward: Beta hands on: ask for the second seat."
    assert block[7] == (
        f"  Distillation: {DISTILLATE} read 3 of the 4 on record, through 2026-07-02 22:00 EDT; "
        "1 close undistilled, due: a distillation runs on Anthony's tap (skill close-distill)."
    )
    assert block[8] == "  Its recurring shapes, the distillate's own reading:"
    assert block[9] == (
        "    · R1 a fixture surface reports a state it has not earned "
        "(named in 2 of 3 month summaries, 2026-06 to 2026-07)"
    )
    # The pointer names no shape: a months_only shape returns months, not closes.
    assert block[10] == (
        "  Walk down: descend() lists the index and every shape; descend(month='YYYY-MM') or "
        "descend(seat='part of a name') returns the closes themselves."
    )
    assert block[11] == ""
    assert len(block) == 12


def test_full_content_lifts_the_preview_cap(root, planted):
    long_learned = "Gamma learned " + "a long lesson " * 30
    fx._write_jsonl(
        root / "chronicle" / "insights" / "reflection" / "descent-long.jsonl",
        [
            {
                "timestamp": "2026-07-04T12:00:00+00:00",
                "domain": "reflection",
                "content": long_learned,
                "session_id": "descentfixture_long",
                "source_instance": "fixture-seat-gamma",
                "close_session": True,
            }
        ],
    )
    capped = fx.run_door(root, "where_did_i_leave_off", {"consume": False, "source_instance": "r"})
    full = fx.run_door(
        root,
        "where_did_i_leave_off",
        {"consume": False, "source_instance": "r", "full_content": True},
    )
    capped_line = next(x for x in _block(capped) if x.startswith("    learned: "))
    full_line = next(x for x in _block(full) if x.startswith("    learned: "))
    assert capped_line.endswith("…") and len(capped_line) <= len("    learned: ") + 200
    assert full_line == "    learned: " + " ".join(long_learned.split())


# ── the foyer and the gentle door ────────────────────────────────────────────


def test_foyer_carries_one_descent_line(root, planted):
    text = fx.run_all_doors(root)["foyer"]
    lines = [x for x in text.splitlines() if x.startswith("  The Descent:")]
    assert lines == [
        "  The Descent: 4 closes on record, newest 2026-07-03; 1 undistilled, due (descend())"
    ]
    assert HEADER not in text


def test_gentle_door_carries_only_the_calm_form(root, planted):
    text = fx.run_all_doors(root)["gentle"]
    assert _block(text) == [HEADER, CALM_LINE, ""]
    for absent in ("closes on record", "Distillation", "handed forward", *BETA_TEXT):
        assert absent not in text, absent
    # The input-gated contract: no count, no work vocabulary in the soft line.
    assert not re.search(r"\d", CALM_LINE)
    for word in ("close", "handoff", "thread", "pick", "session", "distill", "due"):
        assert word not in CALM_LINE.lower(), word
    headers = [x for x in text.splitlines() if x.startswith("━━━")]
    assert headers[headers.index(HEADER) + 1] == "━━━ PROTECTED RECORDS (the coupled drawer) ━━━"


def test_the_gentle_door_never_reads_the_index(root, planted):
    from sovereign_stack import descent

    real = descent.load_distillates
    with mock.patch("sovereign_stack.descent.load_distillates", side_effect=real) as spy:
        fx.run_door(root, "arrive_lineage", {"source_instance": "phase4-reader"})
        assert spy.call_count == 0
        fx.run_door(
            root, "where_did_i_leave_off", {"consume": False, "source_instance": "phase4-reader"}
        )
        assert spy.call_count == 1


# ── fail-closed paths ────────────────────────────────────────────────────────


def test_empty_store_renders_nothing_and_claims_nothing(root):
    # build_fixture alone: its one 'reflection' row has no close_session flag.
    doors = _doors(root)
    for name, text in doors.items():
        assert HEADER not in text, name
        assert "The Descent:" not in text, name
        assert "the_descent" not in _as_of(text), name


def test_absent_sidecar_says_no_distillation_yet(root):
    fx.plant_close_store(root)  # closes, no distillate
    doors = fx.run_all_doors(root)
    block = _block(doors["full"])
    assert (
        "  Distillation: none landed yet; all 4 closes are undistilled, due: a distillation runs "
        "on Anthony's tap (skill close-distill)."
    ) in block
    assert not any(x.startswith("    · ") for x in block)
    assert "no distillation landed yet, due (descend())" in doors["foyer"]
    assert "the_descent" not in _as_of(doors["full"])


def test_malformed_newer_sidecar_degrades_and_never_falls_back(root):
    ids = fx.plant_close_store(root)
    # A VALID older sidecar with its own shape, and a malformed newer one.
    fx.plant_distillate(
        root,
        [ids["a1"]],
        distillate_id="2026-06-30_fixture-old",
        cursor="2026-06-30T22:00:00-04:00",
        shapes=[
            {
                "id": "R9",
                "kind": "recurring",
                "title": "an older shape that must not stand in",
                "count": 1,
                "of": 1,
                "months": ["2026-06"],
                "membership": "months_only",
                "close_ids": [],
            }
        ],
    )
    bad = root / "chronicle" / "distillates" / f"{DISTILLATE}.index.json"
    bad.write_text('{"schema": "descent-distillate-index/1", "id": ')  # truncated JSON
    doors = fx.run_all_doors(root)
    full = doors["full"]
    block = _block(full)
    assert block[1].startswith("  4 closes on record")  # the live counts stand
    assert any(
        x.startswith("  Distillation: unknown this call; the distillate index could not be trusted")
        and f"{DISTILLATE}.index.json" in x
        for x in block
    ), block
    assert "R9" not in full and "an older shape" not in full
    assert "the_descent distillate index unavailable" in _as_of(full)
    assert "Freshness: incomplete" in _as_of(full)
    assert "distillation unknown this call (see AS OF)" in doors["foyer"]
    assert "the_descent distillate index unavailable" in _as_of(doors["foyer"])


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_symlinked_sidecar_is_refused(root, tmp_path):
    ids = fx.plant_close_store(root)
    real = fx.plant_distillate(root, [ids["a1"]])
    outside = tmp_path / "outside.index.json"
    real.rename(outside)
    real.symlink_to(outside)
    full = fx.run_all_doors(root)["full"]
    assert "the_descent distillate index unavailable" in _as_of(full)
    assert "not a regular file" in full


def test_failed_close_read_suppresses_the_voice_and_names_it(root, planted):
    with mock.patch(
        "sovereign_stack.descent.load_closes", side_effect=PermissionError("injected read failure")
    ):
        doors = _doors(root)
    for name in ("full", "full_compact", "foyer", "gentle"):
        text = doors[name]
        assert HEADER not in text, name
        assert "The Descent:" not in text, name
        assert "the_descent unavailable" in _as_of(text), name
        # an unread store is never reported as zero closes
        assert "0 closes" not in text, name


# ── the read filter ──────────────────────────────────────────────────────────


def _designate(root: Path, claim_id: str, *, break_stakes: bool) -> None:
    from sovereign_stack.memory import ExperientialMemory
    from sovereign_stack.protected import designate_protected

    mem = ExperientialMemory(root=str(root / "chronicle"))
    archive = mem.archive_exchange(
        content="synthetic stakes prose for the descent test",
        source="human-relay",
        descriptor="stakes",
        vector_id="descent_test_stakes",
    )
    designate_protected(
        claim_ref=claim_id,
        stakes_archive_id=archive["archive_id"],
        designated_by="test",
        chronicle_root=str(root / "chronicle"),
        subject="test",
        emotion="grief",
    )
    if break_stakes:
        blob = Path(
            next(r for r in mem._read_archive_index() if r["archive_id"] == archive["archive_id"])[
                "path"
            ]
        )
        blob.unlink()


@pytest.mark.parametrize("break_stakes", (False, True), ids=("stakes-verified", "stakes-missing"))
def test_protected_close_still_counts_and_withholds_all_three_parts(root, planted, break_stakes):
    # stakes-missing is the trap: finalize_read returns a sentinel carrying no
    # close_session flag, so a reader that filtered AFTER the chokepoint would
    # drop this close from every count without a word.
    _designate(root, planted["b2"], break_stakes=break_stakes)
    doors = fx.run_all_doors(root)
    block = _block(doors["full"])
    assert block[1].startswith("  4 closes on record")
    assert block[3] == (
        f"  Newest close, 2026-07-03 08:00 EDT, from fixture-seat-beta: {NOTICE}; "
        "all three parts are withheld."
    )
    assert not any(x.startswith(("    learned:", "    surprised:", "    handed")) for x in block)
    july = fx.run_door(root, "descend", {"month": "2026-07"})
    # Scoped to THE DESCENT's own surfaces. The full door's HANDOFFS section
    # prints unsigned handoff notes raw and always has; a handoff cannot be
    # designated protected, so that pre-existing surface is out of this test's
    # reach (named for review, not fixed here).
    descent_block = "\n".join(_block(doors["full"]))
    for text in (descent_block, doors["foyer"], doors["gentle"], july):
        for needle in BETA_TEXT:
            assert needle not in text, needle
    assert NOTICE in july


def test_superseded_close_drops_out_of_the_voice(root, planted):
    from sovereign_stack import provenance as prov

    record = prov.build_supersession_record(
        action="retire", superseded_id=planted["b2"], reason="test retraction", by="test"
    )
    prov.append_supersession(root / "chronicle" / "supersessions.jsonl", record)
    block = _block(fx.run_all_doors(root)["full"])
    assert block[1].startswith("  3 closes on record")
    assert block[3].startswith("  Newest close, 2026-07-01 05:00 EDT, from unknown")


def test_the_join_reads_naive_handoff_stamps_as_eastern_and_skips_decoys(root, planted):
    text = fx.run_door(root, "descend", {"month": "2026-07"})
    assert "Beta hands on: ask for the second seat." in text
    assert "Late decoy" not in text and "Other-session decoy" not in text
    june = fx.run_door(root, "descend", {"month": "2026-06"})
    # a1's handoff is stamped 10:00:00.7 naive; read as UTC it would be four
    # hours from its close and would not join.
    assert "Alpha hands on: re-measure before citing." in june
    assert "An orphan surprise" not in june + text


def test_leaked_markup_is_repaired_at_read_time_only(root, planted):
    shard = root / "chronicle" / "insights" / "reflection" / "descent-fixture.jsonl"
    before = hashlib.sha256(shard.read_bytes()).hexdigest()
    june = fx.run_door(root, "descend", {"month": "2026-06"})
    assert "repaired at read time (leaked markup)" in june
    assert "    The markup leaked into one field." in june.splitlines()
    assert "    Repair it at read time only." in june.splitlines()
    assert "</what_i_learned>" not in june
    assert hashlib.sha256(shard.read_bytes()).hexdigest() == before


def test_no_door_writes_on_a_read(root, planted):
    # chronicle/ and handoffs/: the descent read path reads both. The doors add
    # only exist_ok directories and spiral_state.json, outside these files.
    def snapshot() -> dict[str, str]:
        out = {}
        for top in ("chronicle", "handoffs"):
            for p in sorted((root / top).rglob("*")):
                if p.is_file():
                    out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        return out

    before = snapshot()
    assert any(k.startswith("handoffs/") for k in before)  # the snapshot sees the handoff files
    fx.run_all_doors(root)
    fx.run_door(root, "descend", {})
    fx.run_door(root, "descend", {"month": "2026-07"})
    assert snapshot() == before


# ── a path that exists and is not a directory is unreadable, never empty ─────


@pytest.mark.parametrize("kind", ("file", "dangling-symlink"))
def test_a_reflection_path_that_is_not_a_directory_suppresses_the_voice(root, planted, kind):
    path = root / "chronicle" / "insights" / "reflection"
    shutil.rmtree(path)
    if kind == "file":
        path.write_text("not a directory")
    else:
        path.symlink_to(root / "nowhere")
    doors = _doors(root)
    for name in ("full", "full_compact", "foyer", "gentle"):
        text = doors[name]
        assert HEADER not in text, name
        assert "The Descent:" not in text, name
        assert "the_descent unavailable" in _as_of(text), name
        assert "0 closes" not in text, name


@pytest.mark.parametrize("kind", ("file", "symlink-to-a-directory"))
def test_a_distillates_path_that_is_not_a_plain_directory_degrades_the_index(root, tmp_path, kind):
    ids = fx.plant_close_store(root)
    d = root / "chronicle" / "distillates"
    if kind == "file":
        d.write_text("not a directory")
    else:
        elsewhere = tmp_path / "elsewhere" / ".sovereign"
        fx.plant_distillate(elsewhere, [ids["a1"]])  # a VALID sidecar, reached only by symlink
        d.symlink_to(elsewhere / "chronicle" / "distillates", target_is_directory=True)
    doors = fx.run_all_doors(root)
    block = _block(doors["full"])
    assert block[1].startswith("  4 closes on record")  # the live counts stand
    assert any(
        x.startswith("  Distillation: unknown this call; the distillate index could not be trusted")
        for x in block
    ), block
    assert "the_descent distillate index unavailable" in _as_of(doors["full"])
    assert "distillation unknown this call (see AS OF)" in doors["foyer"]


# ── the month, the mask, the correction ──────────────────────────────────────


def test_month_is_the_eastern_month_not_the_utc_prefix(root, planted):
    fx._write_jsonl(
        root / "chronicle" / "insights" / "reflection" / "descent-boundary.jsonl",
        [
            {
                "timestamp": "2026-07-01T02:30:00+00:00",  # 2026-06-30 22:30 EDT
                "domain": "reflection",
                "content": "A boundary close, written late on the last day of June.",
                "session_id": "descentfixture_boundary",
                "source_instance": "fixture-seat-gamma",
                "close_session": True,
            }
        ],
    )
    block = _block(fx.run_all_doors(root)["full"])
    assert block[2] == "  By month: 2026-06 3 · 2026-07 2"
    june = fx.run_door(root, "descend", {"month": "2026-06"})
    assert "A boundary close, written late on the last day of June." in june


def test_credential_shaped_text_is_masked_in_the_block_and_in_descend(root, planted):
    key = "sk-" + "ant-" + "z" * 30  # built at run time; never a literal in this file
    shard = root / "chronicle" / "insights" / "reflection" / "descent-cred.jsonl"
    fx._write_jsonl(
        shard,
        [
            {
                "timestamp": "2026-07-04T12:00:00+00:00",
                "domain": "reflection",
                "content": f"Cred learned to rotate {key} the same day.",
                "session_id": "descentfixture_cred",
                "source_instance": "fixture-seat-cred",
                "close_session": True,
            }
        ],
    )
    # Scoped to THE DESCENT block: the door's other sections are out of reach here.
    block = _block(fx.run_all_doors(root)["full"])
    assert key not in "\n".join(block)
    assert "    learned: Cred learned to rotate <redacted-key> the same day." in block
    july = fx.run_door(root, "descend", {"month": "2026-07"})
    assert key not in july
    assert july.splitlines()[-1].endswith(
        "1 span masked by the house redactor (display only; the store is unchanged)."
    )
    assert key in shard.read_text()  # display only: the record is unchanged


def test_credential_shaped_text_is_masked_in_every_field(root, planted):
    # The learned leg is pinned above; this pins the seat name, the thread, the
    # surprise and the pick-up, at the boot block and in descend (fix round 1:
    # six one-line unmasking mutants survived every test before this one).
    key = "sk-" + "ant-" + "z" * 30  # built at run time; never a literal in this file
    sess = "descentfixture_cred"
    insights = root / "chronicle" / "insights"
    fx._write_jsonl(
        insights / "reflection" / "descent-cred.jsonl",
        [
            {
                "timestamp": "2026-07-04T12:00:00+00:00",
                "domain": "reflection",
                "content": "Cred learned to keep secrets out of the record.",
                "session_id": sess,
                "source_instance": f"fixture-seat-cred {key}",
                "thread": f"fixture-cred-thread {key}",
                "close_session": True,
            }
        ],
    )
    fx._write_jsonl(
        insights / "surprise" / "descent-cred.jsonl",
        [
            {
                "timestamp": "2026-07-04T12:00:00.200000+00:00",
                "domain": "surprise",
                "content": f"Cred was surprised {key} still worked.",
                "session_id": sess,
            }
        ],
    )
    (root / "handoffs" / "descent_fixture_cred.json").write_text(
        json.dumps(
            {
                "timestamp": "2026-07-04T08:00:00.400000",
                "source_instance": "fixture-handoff-author",
                "source_session_id": sess,
                "thread": "fixture-thread",
                "note": f"Cred hands on: revoke {key} first.",
                "consumed_at": None,
                "consumed_by": None,
            }
        )
    )
    block = _block(fx.run_all_doors(root)["full"])
    assert key not in "\n".join(block)
    assert (
        block[3] == "  Newest close, 2026-07-04 08:00 EDT, from fixture-seat-cred <redacted-key>:"
    )
    assert block[5] == "    surprised: Cred was surprised <redacted-key> still worked."
    assert block[6] == "    handed forward: Cred hands on: revoke <redacted-key> first."

    july = fx.run_door(root, "descend", {"month": "2026-07"})
    assert key not in july
    lines = july.splitlines()
    assert lines[4].startswith(
        "[1] 2026-07-04 08:00 EDT · fixture-seat-cred <redacted-key> · "
        "thread fixture-cred-thread <redacted-key> · close "
    )
    i = lines.index("  SURPRISED")  # the cred close renders first
    assert lines[i + 1] == "    Cred was surprised <redacted-key> still worked."
    i = lines.index("  HANDED FORWARD")
    assert lines[i + 1] == "    Cred hands on: revoke <redacted-key> first."
    # seat, thread, surprise, pick-up: one span each (the learned leg carries none)
    assert lines[-1].endswith(
        "4 spans masked by the house redactor (display only; the store is unchanged)."
    )

    by_seat = fx.run_door(root, "descend", {"seat": "fixture-seat-cred"})
    assert key not in by_seat
    assert "  Selection: 1 seat name: fixture-seat-cred <redacted-key>." in by_seat.splitlines()
    assert by_seat.splitlines()[-1].endswith(
        "5 spans masked by the house redactor (display only; the store is unchanged)."
    )

    index = fx.run_door(root, "descend", {})
    assert key not in index
    assert (
        "    (1 span masked by the house redactor in the names above; display only, "
        "the store is unchanged)"
    ) in index.splitlines()
    assert key in (insights / "reflection" / "descent-cred.jsonl").read_text()  # display only


def _aperture_insights(text: str) -> str:
    block = text.split("━━━ APERTURE (", 1)[1].split("\n\n", 1)[0]
    row = next(x for x in block.splitlines() if x.split()[:1] == ["insights"])
    return row.split(" · ", 1)[1]


def test_a_protected_newest_close_shows_the_aperture_only_its_own_line(root, planted):
    # Fix round 1: removing the protected guard in shown_locators survived every
    # test. A protected newest close renders ONE withheld line for its
    # reflection row; its joined surprise row is withheld, so it is not "shown".
    from sovereign_stack import descent

    before = _aperture_insights(
        fx.run_door(root, "where_did_i_leave_off", {"consume": False, "source_instance": "r"})
    )
    assert before == "5 shown here"  # 3 base rows + the newest close's two rows
    _designate(root, planted["b2"], break_stakes=False)
    state = descent.gather_descent(root, depth="full")
    newest = state.closes[0]
    assert newest.protected and newest.surprise_locator is not None  # the joined row exists
    assert descent.shown_locators(state) == {newest.reflection_locator}
    full = fx.run_door(root, "where_did_i_leave_off", {"consume": False, "source_instance": "r"})
    assert _aperture_insights(full) == "4 shown here"


def test_a_corrected_pick_up_is_marked_wherever_it_renders(root, planted):
    (root / "handoffs" / "descent_fixture_corrector.json").write_text(
        json.dumps(
            {
                "timestamp": "2026-07-03T09:00:00.000000",
                "source_instance": "fixture-handoff-author",
                "source_session_id": "descentfixture_corrector",
                "thread": "fixture-beta",
                "note": "A later handoff corrects beta's pick-up.",
                "supersedes": "descent_fixture_b2.json",
                "consumed_at": None,
                "consumed_by": None,
            },
            indent=2,
        )
    )
    block = _block(fx.run_all_doors(root)["full"])
    assert block[6] == (
        "    handed forward: Beta hands on: ask for the second seat. "
        "[a later handoff corrects this one]"
    )
    lines = fx.run_door(root, "descend", {"month": "2026-07"}).splitlines()
    i = lines.index("    Beta hands on: ask for the second seat.")
    assert lines[i + 1] == "    [a later handoff corrects this one]"
