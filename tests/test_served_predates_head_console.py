"""Design A4 on the console: the bridge heartbeat's ``served_predates_head``
reaches the page, and the page colours it (next-phase plan 1, Task 10).

The bridge derives the field (sovereign-bridge
``tests/test_served_predates_head.py`` pins the derivation); this side has two
jobs, and each one fails on its own:

1. ``dashboard_readers`` copies a FIXED allow-list of heartbeat keys into
   ``snapshot.bridge_heartbeat``, so a field the projection does not name never
   reaches ``app.js``, whatever ``app.js`` says. Both keys pass through
   verbatim, ``null`` stays ``None`` (never ``False``), and a bridge too old to
   send the field reads as absent, not as false.
2. ``app.js`` colours the VERSION stat: true in the degraded colour, null or
   absent in an unknown style of its own, false plain; the reason rides the
   title, as text. These are source pins, the convention every ``app.js`` test
   in this suite follows: the page has no JS runner here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sovereign_stack import dashboard_readers as readers
from sovereign_stack import dashboard_web as web

STATIC = Path(web.__file__).parent / "dashboard_web_static"

TRUE_REASON = (
    "service started 2026-10-17T02:26:00+00:00 before HEAD a12b51f "
    "committed at 2026-10-23T12:00:00+00:00"
)
FALSE_REASON = (
    "service started 2026-10-23T13:00:00+00:00 at or after HEAD a12b51f "
    "committed at 2026-10-23T12:00:00+00:00; HEAD is the commit the service loaded; "
    "working tree clean at service start and now"
)
NULL_REASON = (
    "git metadata unreadable: git log failed: fatal: not a git repository: '~/sovereign-stack/.git'"
)


def _payload(**fields) -> dict:
    payload = {
        "version": "1.21.0",
        "tools": 53,
        "source_commit": "a12b51f",
        "bridge_commit": "b79d113",
        "service_start_time": "2026-10-17T02:26:00+00:00",
        "aperture": {"surfaces": {}},
        "gate": {},
    }
    payload.update(fields)
    return payload


@pytest.fixture
def projected(monkeypatch):
    """The real reader over a planted heartbeat payload: only the HTTP GET is
    replaced, so the projection under test is the one the console runs."""

    def _project(payload: dict) -> dict:
        monkeypatch.setattr(readers, "_http_get_json", lambda url, timeout: payload)
        readers.reset_caches()
        try:
            out = readers.fetch_bridge_heartbeat()
        finally:
            readers.reset_caches()
        assert out is not None
        return out

    return _project


# ── 1. the projection ───────────────────────────────────────────────────────


def test_true_and_its_reason_reach_the_snapshot_verbatim(projected):
    out = projected(_payload(served_predates_head=True, served_predates_head_reason=TRUE_REASON))
    assert out["served_predates_head"] is True
    assert out["served_predates_head_reason"] == TRUE_REASON


def test_false_and_its_reason_reach_the_snapshot_verbatim(projected):
    out = projected(_payload(served_predates_head=False, served_predates_head_reason=FALSE_REASON))
    assert out["served_predates_head"] is False
    assert out["served_predates_head_reason"] == FALSE_REASON


def test_null_stays_null_never_false(projected):
    """Plan 1 Review Focus 4, carried through the console: the bridge's null
    is "could not decide", and the projection must not turn it into false."""
    out = projected(_payload(served_predates_head=None, served_predates_head_reason=NULL_REASON))
    assert out["served_predates_head"] is None
    assert out["served_predates_head_reason"] == NULL_REASON


def test_a_bridge_without_the_field_reads_as_absent_not_false(projected):
    """A bridge deployed before A4 sends neither key. The snapshot still names
    both, as None, so the page can say "this bridge does not report it"."""
    out = projected(_payload())
    assert "served_predates_head" in out and out["served_predates_head"] is None
    assert "served_predates_head_reason" in out and out["served_predates_head_reason"] is None


# ── 2. the colour rule in app.js ────────────────────────────────────────────


def _app_js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


def _function_source(js: str, name: str) -> str:
    start = js.index(f"function {name}(")
    return js[start : js.index("\n}\n", start) + 2]


def test_the_state_comes_from_the_field_by_strict_comparison():
    """`=== true` and `=== false`, and nothing that tests truthiness: `!value`
    or `value ? ... : ...` would fold null into false, the one confusion the
    field exists to prevent."""
    fn = _function_source(_app_js(), "servedHeadState")
    assert "hb.served_predates_head" in fn
    assert "value === true" in fn and "value === false" in fn
    assert "!value" not in fn and "value ?" not in fn and "value ||" not in fn
    # The fall-through is the unknown style, and it carries the bridge's reason,
    # or says the bridge does not report the field at all.
    assert fn.rstrip().endswith("}")
    tail = fn[fn.index("value === false") :]
    assert "'is-head-unknown'" in tail
    assert "this bridge does not report it" in tail
    assert "served_predates_head_reason" in fn


def test_the_version_stat_wears_the_state_and_the_reason_as_text():
    render = _function_source(_app_js(), "renderHeaderStats")
    assert "servedHeadState(hb)" in render
    assert "vEl.classList.toggle('is-behind-head'" in render
    assert "vEl.classList.toggle('is-head-unknown'" in render
    assert "vEl.title =" in render
    assert "innerHTML" not in render
    # Bridge unreachable: neither style and no reason may outlive the data.
    unreachable = render[render.index("// Bridge unreachable") :]
    assert "vEl.classList.remove('is-behind-head', 'is-head-unknown')" in unreachable
    assert "vEl.title = ''" in unreachable


def test_both_styles_exist_and_differ_from_false():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".stat-value.is-behind-head  { color: var(--degraded); }" in css
    assert ".stat-value.is-head-unknown { color: var(--text-dim); font-style: italic; }" in css
