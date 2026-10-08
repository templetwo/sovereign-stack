"""
THE DESCENT — the module: the distillate index reader, the due rule, and the
`descend` read tool.

Hermetic: every store is a tmp root built by tests/_phase4_fixture.py with a
SYNTHETIC close store (fx.plant_close_store) and synthetic sidecars
(fx.plant_distillate). The module is imported INSIDE each test (_d()), so on a
tree without the feature each test fails on its own, by name, rather than the
whole file failing at collection.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import _phase4_fixture as fx  # noqa: E402

DISTILLATE = "2026-07-02_fixture-distill"
CALLER_TEXT = "ZZQX-declared-done-verified"  # a caller string that must never be echoed


def _d():
    from sovereign_stack import descent

    return descent


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / ".sovereign"
    fx.build_fixture(r)
    return r


@pytest.fixture
def ids(root: Path) -> dict[str, str]:
    return fx.plant_close_store(root)


def _shapes(ids: dict[str, str]) -> list[dict]:
    return [
        {
            "id": "R1",
            "kind": "recurring",
            "title": "a fixture surface reports a state it has not earned",
            "count": 2,
            "of": 3,
            "months": ["2026-06", "2026-07"],
            "membership": "months_only",
            "close_ids": [],
        },
        {
            "id": "L1",
            "kind": "learned",
            "title": "a fixture lesson about stale counts",
            "count": 1,
            "of": 3,
            "months": ["2026-06"],
            "membership": "exemplars",
            "close_ids": [ids["a1"]],
        },
    ]


def _descend(root: Path, **kw) -> str:
    return _d().descend(sovereign_root=root, **kw)


# ── the index reader is fail-closed ──────────────────────────────────────────


def _valid_doc(ids: dict[str, str]) -> dict:
    return {
        "schema": "descent-distillate-index/1",
        "kind": "distillate",
        "id": DISTILLATE,
        "created": "2026-07-02T22:00:00-04:00",
        "cursor": "2026-07-02T22:00:00-04:00",
        "authorized_by": fx.FIXTURE_AUTH,
        "withdraws": [],
        "sources": {
            "manifest": {"path": "fixture:m", "sha256": "0" * 64, "bytes": 1},
            "body": {"path": "fixture:b", "sha256": "0" * 64, "bytes": 1},
            "sections": [{"key": "2026-06", "sha256": "0" * 64}],
        },
        "corpus": {
            "records": 1,
            "closes": 1,
            "other_reflection_rows": 0,
            "journal_reviews": 0,
            "sections": 1,
            "by_month": {"2026-06": 1},
            "by_seat": {"fixture-seat-alpha": 1},
        },
        "close_ids": [ids["a1"]],
        "shapes": [_shapes(ids)[1]],
    }


MUTATIONS = {
    "extra key": lambda d: d.update(note="x"),
    "wrong schema": lambda d: d.update(schema="descent-distillate-index/2"),
    "id differs from file name": lambda d: d.update(id="2026-07-03_other"),
    "cursor without offset": lambda d: d.update(cursor="2026-07-02T22:00:00"),
    "duplicate close id": lambda d: d.update(close_ids=d["close_ids"] * 2),
    "corpus disagrees with close_ids": lambda d: d["corpus"].update(closes=2),
    "shape names an unread close": lambda d: d["shapes"][0].update(close_ids=["f" * 64]),
    "two-line title": lambda d: d["shapes"][0].update(title="one\ntwo"),
    "unknown kind": lambda d: d["shapes"][0].update(kind="verdict"),
    "unsorted months": lambda d: d["shapes"][0].update(months=["2026-07", "2026-06"]),
    "missing document kind": lambda d: d.pop("kind"),
    "unknown document kind": lambda d: d.update(kind="verdict"),
    "authorization pending": lambda d: d.update(authorized_by="pending"),
    "authorization not a full claim id": lambda d: d.update(authorized_by="claim:e82799fa"),
    "unknown membership": lambda d: d["shapes"][0].update(membership="most"),
    "months_only shape lists a close": lambda d: d["shapes"][0].update(membership="months_only"),
    "members shape lists no close": lambda d: d["shapes"][0].update(
        membership="members", close_ids=[]
    ),
    "title the redactor masks": lambda d: d["shapes"][0].update(
        title="rotate " + "sk-" + "ant-" + "z" * 30
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_every_malformed_sidecar_is_refused_by_name(root, ids, name):
    doc = _valid_doc(ids)
    path = root / "chronicle" / "distillates" / f"{DISTILLATE}.index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))
    _d().load_distillates(root / "chronicle")  # the unmutated doc is valid (control)
    MUTATIONS[name](doc)
    path.write_text(json.dumps(doc))
    with pytest.raises(_d().DistillateIndexError) as err:
        _d().load_distillates(root / "chronicle")
    assert f"{DISTILLATE}.index.json" in str(err.value)


def test_oversize_sidecar_is_refused(root, ids):
    path = fx.plant_distillate(root, [ids["a1"]], shapes=_shapes(ids))
    path.write_text(path.read_text() + " " * (_d().INDEX_MAX_BYTES + 1))
    with pytest.raises(_d().DistillateIndexError, match="larger than"):
        _d().load_distillates(root / "chronicle")


def test_newest_is_the_greatest_cursor_never_the_newest_file(root, ids):
    old = fx.plant_distillate(
        root,
        [ids["a1"]],
        distillate_id="2026-06-30_fixture-old",
        cursor="2026-06-30T22:00:00-04:00",
    )
    fx.plant_distillate(root, [ids["a1"], ids["a2"]], shapes=_shapes(ids))
    os.utime(old, (2_000_000_000, 2_000_000_000))  # the OLDER sidecar is the newest file
    newest, covered, on_disk, withdrawn = _d().load_distillates(root / "chronicle")
    assert newest.id == DISTILLATE
    assert covered == {ids["a1"], ids["a2"]}
    assert (on_disk, withdrawn) == (2, 0)


def test_a_withdrawn_sidecar_neither_wins_nor_covers(root, ids):
    fx.plant_distillate(root, [ids["a1"], ids["a2"]], shapes=_shapes(ids))
    fx.plant_distillate(
        root,
        [ids["a1"]],
        distillate_id="2026-07-05_fixture-erratum",
        cursor="2026-06-01T22:00:00-04:00",
        withdraws=[DISTILLATE],
        shapes=[],
    )
    newest, covered, on_disk, withdrawn = _d().load_distillates(root / "chronicle")
    assert newest.id == "2026-07-05_fixture-erratum"
    assert covered == {ids["a1"]}
    assert (on_disk, withdrawn) == (2, 1)


# ── the due rule ─────────────────────────────────────────────────────────────


def _state(root: Path):
    return _d().gather_descent(root, depth="full")


def test_not_yet_due_inside_the_month_under_ten(root, ids):
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]], shapes=_shapes(ids))
    lines = _d().render_descent(_state(root), generated_at="2026-07-20T12:00:00Z")
    line = next(x for x in lines if x.startswith("  Distillation:"))
    assert "1 close undistilled, not yet due (due at 10 undistilled" in line


def test_due_once_the_month_holding_an_undistilled_close_has_ended(root, ids):
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]], shapes=_shapes(ids))
    lines = _d().render_descent(_state(root), generated_at="2026-08-01T04:30:00Z")
    line = next(x for x in lines if x.startswith("  Distillation:"))
    assert "1 close undistilled, due: a distillation runs on Anthony's tap" in line


def test_due_at_ten_undistilled_inside_the_month(root, ids):
    fx._write_jsonl(
        root / "chronicle" / "insights" / "reflection" / "descent-ten.jsonl",
        [
            {
                "timestamp": f"2026-07-0{i % 9 + 1}T15:00:{i:02d}+00:00",
                "domain": "reflection",
                "content": f"synthetic close {i}",
                "session_id": "descentfixture_ten",
                "source_instance": "fixture-seat-ten",
                "close_session": True,
            }
            for i in range(9)
        ],
    )
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]], shapes=_shapes(ids))
    lines = _d().render_descent(_state(root), generated_at="2026-07-20T12:00:00Z")
    line = next(x for x in lines if x.startswith("  Distillation:"))
    assert "10 closes undistilled, due:" in line


def test_with_no_distillation_the_same_due_rule_applies(root):
    # Two closes, both inside the render month, no sidecar: under ten and no
    # month has ended, so "none yet" is not yet due.
    fx._write_jsonl(
        root / "chronicle" / "insights" / "reflection" / "descent-two.jsonl",
        [
            {
                "timestamp": f"2026-07-0{i}T15:00:00+00:00",
                "domain": "reflection",
                "content": f"synthetic close {i}",
                "session_id": "descentfixture_two",
                "source_instance": "fixture-seat-two",
                "close_session": True,
            }
            for i in (1, 2)
        ],
    )
    lines = _d().render_descent(_state(root), generated_at="2026-07-20T12:00:00Z")
    line = next(x for x in lines if x.startswith("  Distillation:"))
    assert line.startswith(
        "  Distillation: none landed yet; all 2 closes are undistilled, not yet due"
    )
    foyer = _d().render_descent_foyer_line(
        _d().gather_descent(root, depth="foyer"), generated_at="2026-07-20T12:00:00Z"
    )
    assert foyer.endswith("no distillation landed yet, not yet due (descend())")


# ── descend ──────────────────────────────────────────────────────────────────


def test_descend_by_month(root, ids):
    text = _descend(root, month="2026-07")
    assert text.splitlines()[0] == (
        "THE DESCENT — 2 closes in this selection; showing 1-2, newest first"
    )
    assert "  Selection: month 2026-07 (Eastern)." in text.splitlines()
    assert text.splitlines()[-1] == (
        "COVERAGE: 2 of 2 shown (offset 0, limit 10). 0 unreadable rows skipped; "
        "0 handoff files unreadable."
    )


def test_descend_by_seat_is_case_insensitive_and_reaches_the_unnamed(root, ids):
    text = _descend(root, seat="ALPHA")
    assert "THE DESCENT — 2 closes in this selection" in text
    assert "  Selection: 1 seat name: fixture-seat-alpha." in text.splitlines()
    unnamed = _descend(root, seat="unknown")
    assert "THE DESCENT — 1 close in this selection" in unnamed


def test_descend_by_shape_returns_exemplars_and_says_so(root, ids):
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]], shapes=_shapes(ids))
    text = _descend(root, shape="l1")
    assert "THE DESCENT — 1 close in this selection" in text
    assert "the distillate recorded exemplars, not membership" in text
    assert "Alpha learned that a count read from a stale file is not a count." in text


def test_descend_by_a_month_only_shape_returns_its_months_not_closes(root, ids):
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]], shapes=_shapes(ids))
    text = _descend(root, shape="R1")
    assert "recorded this shape by month only" in text
    assert "Its months: 2026-06, 2026-07." in text
    assert "LEARNED" not in text


def test_descend_coverage_states_partiality_and_the_next_offset(root, ids):
    first = _descend(root, month="2026-07", limit=1)
    assert first.splitlines()[-1].startswith(
        "COVERAGE: 1 of 2 shown (offset 0, limit 1); 1 more: descend(..., offset=1)."
    )
    second = _descend(root, month="2026-07", limit=1, offset=1)
    assert second.splitlines()[-1].startswith("COVERAGE: 1 of 2 shown (offset 1, limit 1).")


def test_descend_omitted_limit_equals_its_schema_default(root, ids):
    """The contract walker's default-equivalence case for descend.limit is
    vacuous on its empty sandbox; this one is not: 12 closes in one month."""
    fx._write_jsonl(
        root / "chronicle" / "insights" / "reflection" / "descent-twelve.jsonl",
        [
            {
                "timestamp": f"2026-05-{i + 1:02d}T15:00:00+00:00",
                "domain": "reflection",
                "content": f"synthetic close {i}",
                "session_id": "descentfixture_twelve",
                "source_instance": "fixture-seat-twelve",
                "close_session": True,
            }
            for i in range(12)
        ],
    )
    from sovereign_stack import server

    schema = next(t for t in asyncio.run(server.list_tools()) if t.name == "descend")
    default = schema.inputSchema["properties"]["limit"]["default"]
    omitted = _d().handle_descent_tool("descend", {"month": "2026-05"}, root)
    explicit = _d().handle_descent_tool("descend", {"month": "2026-05", "limit": default}, root)
    assert omitted == explicit
    assert omitted.splitlines()[-1].startswith(f"COVERAGE: {default} of 12 shown")


@pytest.mark.parametrize(
    "kw",
    (
        {"month": CALLER_TEXT},
        {"shape": CALLER_TEXT},
        {"seat": CALLER_TEXT * 10},
        {"seat": "   "},
        {"month": "2026-07", "limit": 0},
        {"month": "2026-07", "limit": 21},
        {"month": "2026-07", "limit": True},
        {"month": "2026-07", "offset": -1},
    ),
)
def test_descend_refuses_bad_input_without_echoing_it(root, ids, kw):
    text = _descend(root, **kw)
    assert text.startswith("THE DESCENT — refused: ")
    assert text.endswith("Nothing was read.")
    assert CALLER_TEXT not in text


def test_descend_never_echoes_the_seat_filter(root, ids):
    text = _descend(root, seat=CALLER_TEXT)
    assert CALLER_TEXT not in text
    assert "no seat name matches" in text
    assert "THE DESCENT — 0 closes in this selection; none shown" in text


def test_descend_on_an_unreadable_store_is_an_error_not_an_empty_result(root, ids):
    with (
        mock.patch.object(_d(), "_leg_rows", side_effect=PermissionError("injected")),
        pytest.raises(ValueError, match="the close store could not be read"),
    ):
        _descend(root, month="2026-07")


def test_descend_through_the_server_is_an_error_on_an_unreadable_store(root, ids):
    from sovereign_stack import server

    with (
        mock.patch.object(_d(), "_leg_rows", side_effect=PermissionError("injected")),
        pytest.raises(ValueError, match="could not be read"),
    ):
        fx.run_door(root, "descend", {"month": "2026-07"})
    assert server.TOOL_CATEGORIES["descend"] == "witness"


def test_descend_shape_on_an_unreadable_index_is_an_error(root, ids):
    (root / "chronicle" / "distillates").mkdir(parents=True)
    (root / "chronicle" / "distillates" / f"{DISTILLATE}.index.json").write_text("{")
    with pytest.raises(ValueError, match="the distillate index could not be trusted"):
        _descend(root, shape="R1")
    # month and seat do not need the index: they still read, and say so per close
    assert "distillation unknown" in _descend(root, month="2026-07")


def test_descend_index_on_an_empty_store(root):
    text = _descend(root)
    assert text.splitlines()[0] == "THE DESCENT — index"
    assert "No close is on record in this store" in text


def test_descend_index_lists_months_seats_and_every_shape(root, ids):
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]], shapes=_shapes(ids))
    text = _descend(root, now=datetime(2026, 10, 8, tzinfo=timezone.utc))
    assert "    2026-06 2 · 2026-07 2" in text.splitlines()
    assert (
        "  Seat names (3; 1 close unnamed) → descend(seat='part of a name'):" in text.splitlines()
    )
    assert any(x.startswith("    R1   recurring") for x in text.splitlines())
    assert any(
        x.startswith("    L1   learned") and x.endswith("1 exemplar close")
        for x in text.splitlines()
    )


def test_descend_is_registered_core_read_witness():
    from sovereign_stack import server

    names = {t.name for t in asyncio.run(server.list_tools())}
    assert "descend" in names
    assert server.TOOL_TIERS["descend"] == "core"
    assert server.TOOL_INTENTS["descend"] == "read"


# ── withdrawals: a veto filed beside a distillate, never a deletion ──────────


def test_a_withdrawal_withdraws_without_standing_in(root, ids):
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]], shapes=_shapes(ids))
    fx.plant_withdrawal(root, [DISTILLATE])
    assert _d().load_distillates(root / "chronicle") == (None, frozenset(), 1, 1)
    lines = _d().render_descent(_state(root), generated_at="2026-07-20T12:00:00Z")
    assert (
        "  Distillation: none in force (1 withdrawn); all 4 closes are undistilled, due: a "
        "distillation runs on Anthony's tap (skill close-distill)."
    ) in lines
    assert not any(x.startswith("  Its recurring shapes") for x in lines)
    foyer = _d().render_descent_foyer_line(
        _d().gather_descent(root, depth="foyer"), generated_at="2026-07-20T12:00:00Z"
    )
    assert foyer.endswith("no distillation in force (1 withdrawn), due (descend())")


def test_a_dangling_withdrawal_is_refused(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=[])
    fx.plant_withdrawal(root, ["2026-07-01_not-on-disk"])
    with pytest.raises(_d().DistillateIndexError, match="which is not a distillate on disk"):
        _d().load_distillates(root / "chronicle")


def test_a_withdrawal_cannot_withdraw_a_withdrawal(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=[])
    fx.plant_withdrawal(root, [DISTILLATE])
    fx.plant_withdrawal(
        root, ["2026-07-06_fixture-withdrawal"], withdrawal_id="2026-07-07_fixture-unveto"
    )
    with pytest.raises(_d().DistillateIndexError, match="which is not a distillate on disk"):
        _d().load_distillates(root / "chronicle")


def test_more_sidecars_than_the_cap_is_refused(root, ids):
    d = root / "chronicle" / "distillates"
    d.mkdir(parents=True)
    for i in range(_d().INDEX_MAX_FILES + 1):
        (d / f"2026-01-01_cap-{i}.index.json").write_text("{}")
    with pytest.raises(_d().DistillateIndexError, match="more than 200 sidecars"):
        _d().load_distillates(root / "chronicle")


# ── a leg path that exists and is not a directory raises ─────────────────────


@pytest.mark.parametrize("surface", ("chronicle/insights/surprise", "handoffs"))
def test_a_leg_path_that_is_not_a_directory_is_unreadable_not_empty(root, ids, surface):
    path = root / surface
    shutil.rmtree(path)
    path.write_text("not a directory")
    with pytest.raises(NotADirectoryError, match="is not a directory"):
        _d().gather_descent(root, depth="full")
    with pytest.raises(ValueError, match="the close store could not be read"):
        _descend(root, month="2026-07")
    # the foyer depth reads the reflection leg only, so its count still stands
    assert len(_d().gather_descent(root, depth="foyer").closes) == 4


# ── membership drives what descend(shape=...) says ───────────────────────────


def test_descend_by_a_members_shape_says_members(root, ids):
    shapes = [
        {
            "id": "M1",
            "kind": "recurring",
            "title": "a fixture shape with recorded members",
            "count": 1,
            "of": 3,
            "months": ["2026-06"],
            "membership": "members",
            "close_ids": [ids["a1"], ids["a2"]],
        }
    ]
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]], shapes=shapes)
    text = _descend(root, shape="M1")
    assert "THE DESCENT — 2 closes in this selection" in text
    assert "  Note: these are the closes the distillate recorded as members of M1." in (
        text.splitlines()
    )
    assert "exemplars" not in text


def test_descend_by_an_exemplar_shape_with_no_close_says_so(root, ids):
    shapes = [
        {
            "id": "L9",
            "kind": "learned",
            "title": "a fixture theme whose quotes are not closes",
            "count": 1,
            "of": 3,
            "months": ["2026-06"],
            "membership": "exemplars",
            "close_ids": [],
        }
    ]
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]], shapes=shapes)
    text = _descend(root, shape="L9")
    assert "The distillate quoted no close for this shape" in text
    assert "Its months: 2026-06." in text
    assert "LEARNED" not in text


# ── the tool reads the dispatch root, and stays visible to Nape ──────────────


def test_descend_reads_the_dispatch_root_not_home(root, ids, tmp_path, monkeypatch):
    decoy = tmp_path / "decoyhome" / ".sovereign"
    fx.build_fixture(decoy)
    fx._write_jsonl(
        decoy / "chronicle" / "insights" / "reflection" / "decoy.jsonl",
        [
            {
                "timestamp": "2026-07-02T12:00:00+00:00",
                "domain": "reflection",
                "content": "A decoy close outside the dispatch root.",
                "session_id": "descentfixture_decoy",
                "source_instance": "fixture-seat-decoy",
                "close_session": True,
            }
        ],
    )
    monkeypatch.setenv("HOME", str(decoy.parent))
    monkeypatch.setenv("SOVEREIGN_ROOT", str(decoy))
    monkeypatch.setenv("SOVEREIGN_CHRONICLE", str(decoy / "chronicle"))
    text = fx.run_door(root, "descend", {"month": "2026-07"})
    assert "decoy close" not in text
    assert "THE DESCENT — 2 closes in this selection" in text


def test_a_count_of_one_month_summary_is_singular(root, ids):
    # Build spec 2(e): a count of 1 is singular. "of 1" printed "1 of 1 month
    # summaries" in the voice and the index (surface review of 379c27f).
    shapes = [
        {
            "id": "R1",
            "kind": "recurring",
            "title": "a single-month fixture shape",
            "count": 1,
            "of": 1,
            "months": ["2026-06"],
            "membership": "months_only",
            "close_ids": [],
        }
    ]
    fx.plant_distillate(root, [ids["a1"]], shapes=shapes)
    state = _d().gather_descent(root, depth="full")
    block = _d().render_descent(state, generated_at="2026-07-20T12:00:00Z")
    assert "    · R1 a single-month fixture shape (named in 1 of 1 month summary, 2026-06)" in block
    assert (
        "    R1   recurring a single-month fixture shape — named in 1 of 1 month summary, "
        "2026-06; recorded by month only"
    ) in _descend(root).splitlines()


def test_a_cursor_later_than_its_own_created_makes_the_index_unknown(root, ids):
    # A distillation cannot read past its own making. A 2099 cursor validated,
    # won "newest in force" and was shown at boot as the read-through time
    # (adversarial review of 379c27f).
    path = fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]])
    doc = json.loads(path.read_text())
    doc["cursor"] = "2099-01-01T00:00:00-05:00"  # created stays 2026-07-02T22:00-04:00
    path.write_text(json.dumps(doc))
    state = _d().gather_descent(root, depth="full")
    assert state.index_error == f"{DISTILLATE}.index.json: cursor is later than created"
    assert len(state.closes) == 4
    block = "\n".join(_d().render_descent(state, generated_at="2026-07-20T12:00:00Z"))
    assert "2099" not in block
    assert "Distillation: unknown this call" in block


def test_descend_names_the_newest_distillate_in_force():
    # Spec 4.6: "an id from the newest distillate in force". The description
    # said "newest landed", which a withdrawal makes false (surface review).
    tool = next(t for t in _d().DESCENT_TOOLS if t.name == "descend")
    shape = tool.inputSchema["properties"]["shape"]["description"]
    assert "newest landed" not in tool.description + shape
    assert "the shapes of the newest distillate in force" in tool.description
    assert "A shape id from the newest distillate in force" in shape


def test_descend_stays_out_of_nape_readonly_tools():
    """Green on main by design: it pins a decision. descend FAILS the call on an
    unreadable store, so clause 3 of Nape's membership test does not hold; it
    stays visible to the honk detectors, and stored close text may honk."""
    from sovereign_stack.nape_daemon import READONLY_TOOL_NAMES

    assert "descend" not in READONLY_TOOL_NAMES
