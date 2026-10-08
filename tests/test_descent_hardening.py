"""
THE DESCENT — validation takes the WHOLE string, and corpus counts are never
negative (builder hardening on top of the reference build, 2026-10-08).

In Python `$` also matches before one trailing newline, so re.match(r"^...$")
accepted "2026-07\n" as a month and "claim:<64 hex>\n" as an authorization.
On descend that let a caller's newline through validation, into a store read
and back out into the Selection line. On a sidecar it let a close id carrying
a newline pass, which never equals a live id: coverage would silently
miscount. Build spec section 6 also says the corpus counts are all
non-negative ints; a negative month or seat count that still sums to
`closes` used to pass.

Hermetic, synthetic only (tests/_phase4_fixture.py); the module is imported
inside each test so a tree without the feature fails per test, not at
collection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import _phase4_fixture as fx  # noqa: E402

DISTILLATE = "2026-07-02_fixture-distill"


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
        "shapes": [
            {
                "id": "L1",
                "kind": "learned",
                "title": "a fixture lesson about stale counts",
                "count": 1,
                "of": 3,
                "months": ["2026-06"],
                "membership": "exemplars",
                "close_ids": [ids["a1"]],
            }
        ],
    }


def _newline_close_id(d: dict) -> None:
    nl = d["close_ids"][0] + "\n"
    d["close_ids"] = [nl]
    d["shapes"][0]["close_ids"] = [nl]


MUTATIONS = {
    "authorized_by with a trailing newline": lambda d: d.update(
        authorized_by=d["authorized_by"] + "\n"
    ),
    "close id with a trailing newline": _newline_close_id,
    "shape id with a trailing newline": lambda d: d["shapes"][0].update(id="L1\n"),
    "shape month with a trailing newline": lambda d: d["shapes"][0].update(months=["2026-06\n"]),
    "corpus month with a trailing newline": lambda d: d["corpus"].update(by_month={"2026-06\n": 1}),
    "negative month count that still sums": lambda d: d["corpus"].update(
        by_month={"2026-05": -1, "2026-06": 2}
    ),
    "negative seat count that still sums": lambda d: d["corpus"].update(
        by_seat={"fixture-seat-alpha": 2, "fixture-seat-beta": -1}
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_a_sidecar_value_must_be_the_whole_valid_string(root, ids, name):
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


def test_descend_refuses_a_month_with_a_trailing_newline(root, ids):
    text = _d().descend(sovereign_root=root, month="2026-07\n")
    assert text.startswith("THE DESCENT — refused: month must be")
    assert text.endswith("Nothing was read.")
    assert "\n" not in text  # nothing of the caller's input split the output
    # control: the same month without the newline reads the two July closes
    ok = _d().descend(sovereign_root=root, month="2026-07")
    assert (
        ok.splitlines()[0] == "THE DESCENT — 2 closes in this selection; showing 1-2, newest first"
    )
