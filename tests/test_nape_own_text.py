"""
Nape scores a quoted-record read tool by its OWN text (lane
2026-10-17_descent-strengthen, item A).

On 2026-10-17 six successful descend() calls drew seven honks
(declare_before_verify sharp x3, repeated_mistake uneasy x4) and the handoff
that followed drew premature_summary, because the three detectors scanned the
closes descend QUOTED (other seats' "failed", "verified", "refused") as this
seat's own completion and error text.

descend cannot join READONLY_TOOL_NAMES (it fails clauses 2 and 3 of that
set's membership test, see nape_daemon.py), so the fix is a per-tool own-text
extractor: for a registered tool the detectors scan the extractor's output,
which keeps the tool's framing and drops the quoted record. The fixture file
is SYNTHETIC: it mirrors the structure of the six real outputs (index cut at
the 4000-character observation cap, month views, a months-only shape view, an
exemplar shape view with its Note, a seat view, and a short view ending in
COVERAGE) and keeps the trigger words inside the quoted blocks and shape
titles, but every body, seat name, close id and title is invented. Scored in
full (the 8800960 behaviour) it draws 6 declare_before_verify, 4
repeated_mistake and, on the handoff after, 1 premature_summary. The real
outputs stay outside the repo, in the lane directory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

FIXTURE = Path(__file__).parent / "nape_descend_fixture_synthetic.json"
SESSION = "own-text-session"


def _nd():
    from sovereign_stack import nape_daemon

    return nape_daemon


def _fixture_obs() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["observations"]


def _honks(daemon) -> list[dict]:
    path = Path(daemon._honks_path)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


@pytest.fixture
def daemon(tmp_path: Path):
    return _nd().NapeDaemon(root=str(tmp_path / ".sovereign"))


_TS = iter(f"2026-10-18T02:{m:02d}:{s:02d}+00:00" for m in range(60) for s in range(60))


def _obs(daemon, tool: str, result: str, args: dict | None = None) -> None:
    daemon.observe(tool, args or {}, result, SESSION, timestamp=next(_TS))


HANDOFF_RESULT = (
    "Handoff written → /tmp/x/handoffs/fixture.json\n  thread: hq\n"
    "  from: fixture-seat (session s)\n  note: a fixture pick-up"
)


# ── (1) six descend outputs shaped like tonight's ───────────────────────────────────


def test_the_fixture_holds_the_six_observations():
    obs = _fixture_obs()
    assert len(obs) == 6
    assert {o["tool_name"] for o in obs} == {"descend"}
    assert all(o["result_str"].startswith("THE DESCENT") for o in obs)


def test_tonights_six_descend_reads_draw_no_honk_of_any_level(daemon):
    for o in _fixture_obs():
        _obs(daemon, "descend", o["result_str"], o["arguments"])
    assert _honks(daemon) == []


def test_a_handoff_after_tonights_reads_draws_no_premature_summary(daemon):
    for o in _fixture_obs():
        _obs(daemon, "descend", o["result_str"], o["arguments"])
    _obs(daemon, "handoff", HANDOFF_RESULT)
    assert [h["pattern"] for h in _honks(daemon)] == []


def test_the_extractor_keeps_the_framing_of_tonights_reads():
    ex = _nd().OWN_TEXT_EXTRACTORS["descend"]
    obs = {o["obs_id"]: o["result_str"] for o in _fixture_obs()}
    month = ex(obs["synthetic-2"])
    assert month.startswith("THE DESCENT — 4 closes in this selection")
    assert "Selection: month 2026-10 (Eastern)." in month
    assert (
        "[1] 2026-10-17 20:44 EDT · Synthetic Seat Alpha, fixture-model-1 · thread "
        "fixture-thread-a" in month
    )
    # the quoted close body is gone, its second paragraph after a blank line too
    assert "Synthetic seat note one" not in month
    assert "SECOND PARAGRAPH" not in month
    index = ex(obs["synthetic-1"])
    assert "42 closes on record" in index
    assert "0 unreadable rows skipped" in index
    # a shape title is the distillate's quoted reading, not the tool's words
    assert "Writing a failure mode down" not in index
    assert "named in 3 of 8 month summaries" in index
    shape = ex(obs["synthetic-4"])
    assert "Selection: shape L2 of 2099-01-01_synthetic-distill: <shape title>." in shape
    assert "Note: the distillate recorded exemplars" in shape


# ── (2) the tool's own failure reports stay visible ──────────────────────────


def _selection(coverage: str, learned: str = "a quiet lesson") -> str:
    return "\n".join(
        [
            "THE DESCENT — 1 close in this selection; showing 1-1, newest first",
            "  Selection: month 2026-06 (Eastern).",
            "",
            "[1] 2026-06-02 10:00 EDT · fixture-seat · thread t · close 0123456789abcdef · distilled",
            "  LEARNED",
            f"    {learned}",
            "  SURPRISED",
            "    (no surprise joined: none from this session within 10 seconds)",
            "  HANDED FORWARD",
            "    (no handoff joined)",
            "",
            coverage,
        ]
    )


NONZERO = "COVERAGE: 1 of 1 shown (offset 0, limit 5). 3 unreadable rows skipped; 0 handoff files unreadable."
ZERO = "COVERAGE: 1 of 1 shown (offset 0, limit 5). 0 unreadable rows skipped; 0 handoff files unreadable."


def test_a_nonzero_unreadable_count_is_an_error_for_repeated_mistake(daemon):
    _obs(daemon, "descend", _selection(NONZERO))
    _obs(daemon, "descend", _selection(NONZERO))
    assert [h["pattern"] for h in _honks(daemon)] == ["repeated_mistake"]


def test_a_nonzero_unreadable_count_is_an_error_for_premature_summary(daemon):
    _obs(daemon, "descend", _selection(NONZERO))
    _obs(daemon, "handoff", HANDOFF_RESULT)
    assert [h["pattern"] for h in _honks(daemon)] == ["premature_summary"]


def test_unreadable_handoff_files_count_too(daemon):
    cov = "COVERAGE: 1 of 1 shown (offset 0, limit 5). 0 unreadable rows skipped; 2 handoff files unreadable."
    _obs(daemon, "descend", _selection(cov))
    _obs(daemon, "handoff", HANDOFF_RESULT)
    assert [h["pattern"] for h in _honks(daemon)] == ["premature_summary"]


def test_a_could_not_be_read_note_inside_a_block_survives_truncation(daemon):
    """The COVERAGE line is often cut by the 4000-character cap; the joined-leg
    note the tool writes in place of a pick-up is its own text and must count."""
    text = _selection(ZERO).replace(
        "    (no handoff joined)", "    (none joined; 2 handoff files could not be read)"
    )
    text = text.rsplit("\n", 1)[0]  # drop COVERAGE, as truncation does
    _obs(daemon, "descend", text)
    _obs(daemon, "handoff", HANDOFF_RESULT)
    assert [h["pattern"] for h in _honks(daemon)] == ["premature_summary"]


def test_a_zero_unreadable_count_is_not_an_error(daemon):
    _obs(daemon, "descend", _selection(ZERO))
    _obs(daemon, "descend", _selection(ZERO))
    _obs(daemon, "handoff", HANDOFF_RESULT)
    assert _honks(daemon) == []


def test_the_autohook_error_text_passes_through_unchanged(daemon):
    """Guard (also green on 8800960): a raising descend is recorded by the
    auto-hook as "ERROR: {exc}", and the extractor must hand it through."""
    assert _nd().OWN_TEXT_EXTRACTORS["descend"]("ERROR: store unreadable") == (
        "ERROR: store unreadable"
    )
    _obs(daemon, "descend", "ERROR: store unreadable")
    _obs(daemon, "descend", "ERROR: store unreadable")
    _obs(daemon, "handoff", HANDOFF_RESULT)
    assert sorted(h["pattern"] for h in _honks(daemon)) == [
        "premature_summary",
        "repeated_mistake",
    ]


def test_a_refusal_is_the_tools_own_error(daemon):
    refused = "THE DESCENT — refused: month must be an Eastern calendar month written YYYY-MM. Nothing was read."
    _obs(daemon, "descend", refused)
    _obs(daemon, "descend", refused)
    assert [h["pattern"] for h in _honks(daemon)] == ["repeated_mistake"]


# ── (3) the extractor boundary ───────────────────────────────────────────────


def test_a_declare_word_inside_a_quoted_learned_block_does_not_honk(daemon):
    _obs(daemon, "descend", _selection(ZERO, learned="the fix was verified and shipped"))
    assert _honks(daemon) == []


def test_a_blank_line_inside_a_block_does_not_end_it(daemon):
    learned = "first paragraph\n\n    second paragraph, verified and done"
    _obs(daemon, "descend", _selection(ZERO, learned=learned))
    assert _honks(daemon) == []


def test_the_same_word_in_the_framing_does_honk(daemon):
    text = _selection(ZERO).replace("· fixture-seat ·", "· verified-seat ·")
    _obs(daemon, "descend", text)
    assert [h["pattern"] for h in _honks(daemon)] == ["declare_before_verify"]


# ── (4) unregistered tools are scored exactly as before ──────────────────────


def test_an_unregistered_tool_with_descend_shaped_text_still_honks(daemon):
    """The registry is keyed by tool name, never by the shape of the text."""
    _obs(daemon, "some_other_tool", _selection(ZERO, learned="verified and shipped"))
    assert [h["pattern"] for h in _honks(daemon)] == ["declare_before_verify"]


def test_descend_is_registered_and_stays_out_of_the_exemption_set():
    nd = _nd()
    assert "descend" in nd.OWN_TEXT_EXTRACTORS
    assert "descend" not in nd.READONLY_TOOL_NAMES


def test_a_raising_extractor_falls_back_to_the_full_text(daemon, monkeypatch):
    def boom(_text: str) -> str:
        raise RuntimeError("extractor broke")

    monkeypatch.setitem(_nd().OWN_TEXT_EXTRACTORS, "descend", boom)
    _obs(daemon, "descend", _selection(ZERO, learned="verified"))
    assert [h["pattern"] for h in _honks(daemon)] == ["declare_before_verify"]


# ── shape titles, rendered by the real descend ───────────────────────────────


def test_shape_titles_are_quoted_record_in_every_view(tmp_path: Path):
    import _phase4_fixture as fx

    from sovereign_stack import descent

    root = tmp_path / ".sovereign"
    fx.build_fixture(root)
    ids = fx.plant_close_store(root)
    loud = "the check failed, was refused, then verified and shipped"
    shapes = [
        {
            "id": "R1",
            "kind": "recurring",
            "title": loud,
            "count": 2,
            "of": 3,
            "months": ["2026-06", "2026-07"],
            "membership": "months_only",
            "close_ids": [],
        },
        {
            "id": "L4",
            "kind": "learned",
            "title": loud + ".",
            "count": 1,
            "of": 3,
            "months": ["2026-06"],
            "membership": "exemplars",
            "close_ids": [ids["a1"]],
        },
    ]
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]], shapes=shapes)
    views = {
        "index": descent.descend(sovereign_root=root),
        "months_only": descent.descend(sovereign_root=root, shape="R1"),
        "exemplars": descent.descend(sovereign_root=root, shape="L4"),
        "exemplars_and_month": descent.descend(sovereign_root=root, shape="L4", month="2026-06"),
    }
    ex = _nd().OWN_TEXT_EXTRACTORS["descend"]
    for name, text in views.items():
        assert loud in text, name  # the tool did show the title
        assert loud not in ex(text), name
    assert "Selection: shape L4 of 2026-07-02_fixture-distill" in ex(views["exemplars_and_month"])
    assert "month 2026-06 (Eastern)" in ex(views["exemplars_and_month"])

    d = _nd().NapeDaemon(root=str(tmp_path / "nape-root"))
    for text in views.values():
        _obs(d, "descend", text)
        _obs(d, "descend", text)
    _obs(d, "handoff", HANDOFF_RESULT)
    assert _honks(d) == []


def test_an_index_shape_row_cut_by_the_observation_cap_loses_its_title_too():
    ex = _nd().OWN_TEXT_EXTRACTORS["descend"]
    index = next(o for o in _fixture_obs() if o["obs_id"] == "synthetic-1")["result_str"]
    last = index.splitlines()[-1]
    assert last.startswith("    L16  learned   Invented lesson number 16")  # the cut row
    assert " — named in" not in last
    out = ex(index).splitlines()[-1]
    assert out == "    L16  learned   <shape title>"


def test_the_fixture_draws_the_old_honks_when_scored_in_full(daemon, monkeypatch):
    """Positive control: the synthetic fixture really does carry tonight's
    triggers. With descend unregistered (the 8800960 scoring) it honks."""
    monkeypatch.delitem(_nd().OWN_TEXT_EXTRACTORS, "descend")
    for o in _fixture_obs():
        _obs(daemon, "descend", o["result_str"], o["arguments"])
    _obs(daemon, "handoff", HANDOFF_RESULT)
    patterns = [h["pattern"] for h in _honks(daemon)]
    assert patterns.count("declare_before_verify") == 6
    assert patterns.count("repeated_mistake") == 4
    assert patterns.count("premature_summary") == 1


def _index_with(title: str, distillation_tail: str) -> str:
    return "\n".join(
        [
            "THE DESCENT — index",
            "  3 closes on record (close_session entries in chronicle domain 'reflection'), "
            "2026-06-02 to 2026-07-03 (Eastern). 0 unreadable rows skipped.",
            f"  Distillation: 2099-01-01_synthetic-distill read 3 of the 3 on record{distillation_tail}.",
            "  Shapes in 2099-01-01_synthetic-distill, its own reading → descend(shape='ID'):",
            f"    L1   learned   {title} — named in 1 of 1 month summary, 2026-06; 1 exemplar close",
        ]
    )


def test_a_declare_word_in_a_shape_title_is_silent(daemon):
    _obs(daemon, "descend", _index_with("the fix was verified and shipped", ""))
    assert _honks(daemon) == []


def test_the_same_word_in_the_index_framing_honks(daemon):
    _obs(daemon, "descend", _index_with("a quiet lesson", "; verified"))
    assert [h["pattern"] for h in _honks(daemon)] == ["declare_before_verify"]
