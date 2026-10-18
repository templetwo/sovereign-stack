"""
THE DESCENT strengthened (lane 2026-10-17_descent-strengthen, items B and C).

B. descend never echoes raw caller text: the Selection line names only stored
   seat names, and a zero match prints the count 0 with no echo of the query.
   Month and shape render only validated values; malformed ones are refused
   without being echoed.
C. A shape month outside the record is MARKED on read (index, shape views,
   boot voice) and REFUSED at landing time by validate_sidecar_for_landing,
   while load_distillates (the read path) still reads the landed sidecar
   2026-10-04_close-distill, whose shape L10 names 2026-02, two months before
   the first close on record.

Hermetic, synthetic (tests/_phase4_fixture.py), plus a read-only tmp COPY of
the landed sidecar when it is present on this machine.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import _phase4_fixture as fx  # noqa: E402

LANDED = Path.home() / ".sovereign/chronicle/distillates/2026-10-04_close-distill.index.json"


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


def _descend(root: Path, **kw) -> str:
    return _d().descend(sovereign_root=root, **kw)


# ── B. never echo raw caller text ────────────────────────────────────────────


def test_a_seat_query_matching_nothing_prints_zero_and_no_echo(root, ids):
    needle = "QQnoSuchSeatZZ"
    text = _descend(root, seat=needle)
    assert "  Selection: 0 seat names: no stored seat name matches." in text.splitlines()
    assert needle not in text
    assert needle.lower() not in text


def test_a_matched_seat_query_prints_the_stored_name_not_the_query(root, ids):
    text = _descend(root, seat="SeAt-AlPhA")
    assert "  Selection: 1 seat name: fixture-seat-alpha." in text.splitlines()
    assert "SeAt-AlPhA" not in text


@pytest.mark.parametrize("month", ["2026-13", "2026-7", "26-07", "2026-07\n", "2026-07; done"])
def test_a_malformed_month_is_refused_without_echo(root, ids, month):
    text = _descend(root, month=month)
    assert text.startswith("THE DESCENT — refused: month must be")
    assert month.strip() not in text


@pytest.mark.parametrize("shape", ["R1; verified", "ZZ99999", "1R", "L1000", "shape-done"])
def test_a_malformed_shape_is_refused_without_echo(root, ids, shape):
    fx.plant_distillate(root, [ids["a1"]], by_month={"2026-06": 1})
    text = _descend(root, shape=shape)
    assert text.startswith("THE DESCENT — refused: shape must be")
    assert shape not in text


def test_a_well_formed_unknown_shape_id_is_not_echoed(root, ids):
    fx.plant_distillate(root, [ids["a1"]], by_month={"2026-06": 1})
    text = _descend(root, shape="Q77")
    assert "has no shape with that id" in text
    assert "Q77" not in text


# ── C. months outside the record ─────────────────────────────────────────────

# The fixture's closes run 2026-06-02 to 2026-07-03 (Eastern), so 2026-06 is
# the first month on record and 2026-07 the last. Nothing here hardcodes the
# live record's 2026-04.


def _shapes(ids: dict[str, str], months: list[str]) -> list[dict]:
    return [
        {
            "id": "R1",
            "kind": "recurring",
            "title": "a fixture recurring shape",
            "count": len(months),
            "of": 3,
            "months": months,
            "membership": "months_only",
            "close_ids": [],
        },
        {
            "id": "L10",
            "kind": "learned",
            "title": "a fixture lesson",
            "count": len(months),
            "of": 3,
            "months": months,
            "membership": "exemplars",
            "close_ids": [ids["a1"]],
        },
    ]


BEFORE = "2026-02 (outside the record, which begins 2026-06)"
AFTER = "2026-09 (outside the record, which ends 2026-07)"


def test_the_index_marks_a_month_before_the_record(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=_shapes(ids, ["2026-02", "2026-06", "2026-07"]))
    text = _descend(root)
    l10 = next(x for x in text.splitlines() if x.startswith("    L10 "))
    assert f"{BEFORE}, 2026-06, 2026-07" in l10


def test_a_consecutive_run_does_not_swallow_the_mark(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=_shapes(ids, ["2026-05", "2026-06", "2026-07"]))
    text = _descend(root)
    r1 = next(x for x in text.splitlines() if x.startswith("    R1 "))
    assert "2026-05 (outside the record, which begins 2026-06), 2026-06, 2026-07" in r1
    assert "2026-05 to 2026-07" not in r1


def test_the_index_marks_a_month_after_the_record(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=_shapes(ids, ["2026-06", "2026-09"]))
    assert AFTER in _descend(root)


def test_months_inside_the_record_render_exactly_as_before(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=_shapes(ids, ["2026-06", "2026-07"]))
    text = _descend(root)
    assert "outside the record" not in text
    assert "2026-06 to 2026-07" in text


def test_the_months_only_shape_view_marks_it(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=_shapes(ids, ["2026-02", "2026-06"]))
    text = _descend(root, shape="R1")
    assert f"Its months: {BEFORE}, 2026-06." in text


def test_the_exemplar_shape_view_marks_it(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=_shapes(ids, ["2026-02", "2026-06"]))
    text = _descend(root, shape="L10")
    note = next(x for x in text.splitlines() if x.startswith("  Note: "))
    assert BEFORE in note


def test_the_exemplar_shape_view_adds_nothing_when_every_month_is_inside(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=_shapes(ids, ["2026-06"]))
    assert "outside the record" not in _descend(root, shape="L10")


def test_the_boot_voice_marks_a_recurring_shape_month(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=_shapes(ids, ["2026-02", "2026-06"]))
    d = _d()
    state = d.gather_descent(root, depth="full")
    voice = "\n".join(d.render_descent(state))
    assert BEFORE in voice


# ── C. the landing-time refusal, and the read path left alone ────────────────


def _good_doc(ids: dict[str, str], months: list[str], base: Path) -> dict:
    """A valid sidecar document built by the shared fixture writer, read back."""
    path = fx.plant_distillate(
        base, [ids["a1"]], shapes=_shapes(ids, months), by_month={"2026-06": 1}
    )
    return json.loads(path.read_text())


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    return tmp_path / "scratch"


def test_landing_refuses_a_shape_month_before_the_corpus(ids, scratch):
    doc = _good_doc(ids, ["2026-02", "2026-06"], scratch)
    with pytest.raises(_d().DistillateIndexError) as e:
        _d().validate_sidecar_for_landing(doc, doc["id"])
    assert "R1" in str(e.value) and "2026-02" in str(e.value)


def test_landing_refuses_a_shape_month_after_the_corpus(ids, scratch):
    doc = _good_doc(ids, ["2026-06", "2026-08"], scratch)
    with pytest.raises(_d().DistillateIndexError, match="2026-08"):
        _d().validate_sidecar_for_landing(doc, doc["id"])


def test_landing_passes_a_sidecar_whose_months_sit_inside_the_corpus(ids, scratch):
    doc = _good_doc(ids, ["2026-06"], scratch)
    out = _d().validate_sidecar_for_landing(doc, doc["id"])
    assert out.id == doc["id"]


def test_landing_runs_the_full_read_validation_first(ids, scratch):
    doc = _good_doc(ids, ["2026-06"], scratch)
    doc["authorized_by"] = "pending"
    with pytest.raises(_d().DistillateIndexError, match="authorized_by"):
        _d().validate_sidecar_for_landing(doc, doc["id"])


def test_the_read_path_still_reads_a_sidecar_the_landing_gate_refuses(root, ids):
    fx.plant_distillate(root, [ids["a1"]], shapes=_shapes(ids, ["2026-02", "2026-06"]))
    newest, covered, on_disk, withdrawn = _d().load_distillates(root / "chronicle")
    assert newest is not None and on_disk == 1 and withdrawn == 0


@pytest.mark.skipif(not LANDED.is_file(), reason="the landed sidecar lives on the Mac Studio")
def test_the_landed_sidecar_stays_readable_and_is_refused_for_landing(tmp_path: Path):
    """A tmp COPY (same file name: the id must equal the stem). The landed file
    is never opened for writing."""
    chron = tmp_path / ".sovereign" / "chronicle"
    (chron / "distillates").mkdir(parents=True)
    copy = chron / "distillates" / LANDED.name
    shutil.copyfile(LANDED, copy)
    d = _d()
    newest, _covered, on_disk, _w = d.load_distillates(chron)
    assert newest is not None and newest.id == "2026-10-04_close-distill" and on_disk == 1
    doc = json.loads(copy.read_text())
    with pytest.raises(d.DistillateIndexError) as e:
        d.validate_sidecar_for_landing(doc, newest.id)
    assert "L10" in str(e.value) and "2026-02" in str(e.value)


# ── the landing gate has a caller: a small CLI ───────────────────────────────


def _cli(path: Path):
    import subprocess

    src = Path(__file__).resolve().parent.parent / "src"
    return subprocess.run(
        [sys.executable, "-m", "sovereign_stack.descent", "--validate-sidecar", str(path)],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
        timeout=60,
    )


def test_the_cli_refuses_a_sidecar_with_a_month_outside_its_corpus(ids, scratch):
    path = fx.plant_distillate(
        scratch, [ids["a1"]], shapes=_shapes(ids, ["2026-02", "2026-06"]), by_month={"2026-06": 1}
    )
    r = _cli(path)
    assert r.returncode == 1, r.stderr
    assert "R1" in r.stderr and "2026-02" in r.stderr


def test_the_cli_passes_a_good_sidecar(ids, scratch):
    path = fx.plant_distillate(
        scratch, [ids["a1"]], shapes=_shapes(ids, ["2026-06"]), by_month={"2026-06": 1}
    )
    r = _cli(path)
    assert r.returncode == 0, r.stderr
    assert "ok for landing: 2026-07-02_fixture-distill" in r.stdout
