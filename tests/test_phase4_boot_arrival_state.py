"""
Phase 4 — boot-internals swap (ONE DOORWAY, MANY DEPTHS) regression suite.

Three families of guarantees:

  1. GOLDEN EQUIVALENCE (non-breaking, the make-or-break). The goldens under
     tests/goldens/phase4/ were captured by running the SAME fixture harness
     against the UNTOUCHED main-HEAD code, before the refactor (see
     tests/_phase4_fixture.py for the provenance note). Here the refactored
     doors run against the identical fixture; after stripping the intended
     ━━━ AS OF ━━━ block and masking run-volatile tokens, the output must be
     byte-identical to the pristine golden — i.e. "before == after modulo the
     as-of additions".

  2. PROJECTION INVARIANTS (contract §3.2). generated_at always present; the
     as-of receipt on every door; freshness never "current" when a newer
     in-scope entry exists; the watermark is a true upper bound; a degraded
     gather yields freshness="incomplete" + partial_reasons.

  3. SINGLE DOORWAY. All three doors route through build_arrival_state — no
     door computes its own separate state.

Plus wrapper-identity tests proving each witness collect/render split is
byte-identical to the pre-split format_* function.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import _phase4_fixture as fx  # noqa: E402

GOLDEN_DIR = Path(__file__).parent / "goldens" / "phase4"
DOORS = ("full", "foyer", "gentle")


# ── 1. Golden equivalence ────────────────────────────────────────────────────


class TestDoorEquivalence:
    def test_all_doors_byte_identical_modulo_as_of(self, tmp_path: Path):
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        rendered = fx.run_all_doors(root)
        for name in DOORS:
            got = fx.normalize(rendered[name], root)
            golden = (GOLDEN_DIR / f"{name}.txt").read_text()
            assert got == golden, (
                f"{name} door diverged from the pristine golden "
                f"(non-breaking violated). Run a diff of the normalized outputs."
            )


# ── 2. Projection invariants ─────────────────────────────────────────────────


def _engines(root: Path):
    from sovereign_stack.handoff import HandoffEngine
    from sovereign_stack.memory import ExperientialMemory
    from sovereign_stack.reflexive import ReflexiveSurface

    return (
        ExperientialMemory(root=str(root / "chronicle")),
        HandoffEngine(root=str(root)),
        ReflexiveSurface(sovereign_root=root),
    )


def _build(root: Path, profile: str, **kw):
    from sovereign_stack.arrival_state import build_arrival_state

    exp, ho, rx = _engines(root)
    return build_arrival_state(
        root,
        reader="phase4-reader",
        profile=profile,
        experiential=exp,
        handoff_engine=ho,
        reflexive_surface=rx,
        spiral_summary={
            "session_id": "s",
            "current_phase": "INITIALIZATION",
            "tool_call_count": 1,
            "reflection_depth": 0,
            "session_duration_seconds": 0.0,
        },
        **kw,
    )


class TestProjectionInvariants:
    def test_as_of_block_and_generated_at_present_on_every_door(self, tmp_path: Path):
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        rendered = fx.run_all_doors(root)
        for name in DOORS:
            text = rendered[name]
            assert "━━━ AS OF ━━━" in text, f"{name} missing as-of block"
            assert "  Generated: " in text, f"{name} missing generated_at"
            assert "  Source high-water mark: " in text, f"{name} missing watermark"

    def test_generated_at_is_iso_z(self, tmp_path: Path):
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        state = _build(root, "full")
        assert state.generated_at.endswith("Z")
        # Parseable back to a datetime.
        datetime.fromisoformat(state.generated_at.replace("Z", "+00:00"))

    def test_injected_clock_flows_into_generated_at(self, tmp_path: Path):
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        frozen = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        state = _build(root, "full", now_fn=lambda: frozen)
        assert state.generated_at == "2030-01-02T03:04:05Z"

    def test_normal_fixture_is_current(self, tmp_path: Path):
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        state = _build(root, "full")
        assert state.freshness == "current"
        assert not state.partial_reasons

    def test_watermark_is_true_upper_bound(self, tmp_path: Path):
        # No in-scope gathered entry may postdate the watermark (contract §3.2).
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        state = _build(root, "full")
        from sovereign_stack.arrival_state import _parse_ts

        wm = _parse_ts(state.source_high_watermark)
        assert wm is not None
        for lst in (state.handoffs, state.open_threads, state.sentinel_pool, state.recent_activity):
            for rec in lst or []:
                ts = _parse_ts(rec.get("timestamp"))
                if ts is not None:
                    assert ts <= wm, (
                        f"entry {rec.get('timestamp')} postdates watermark {state.source_high_watermark}"
                    )

    def test_freshness_not_current_on_true_rendered_layer_lag(self, tmp_path: Path):
        # freshness="stale" must remain REACHABLE (law #2 — a gate must be
        # demonstrably able to fail in selftest) — but on a TRUE lag: a
        # rendered-layer entry (insight/open_thread) newer than everything the
        # doors gathered. A routine record_learning is NOT a rendered layer and
        # must never trigger this (see test_record_learning_alone_does_not_
        # flip_freshness_to_stale below — the false-positive regression this
        # fix closes). Simulate the true-lag case synthetically by mocking the
        # store-head probe, since injecting a real insight/open_thread newer
        # than the gathered max would just get pulled INTO the gathered set by
        # the doors themselves (they render insights/threads at wide limits).
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        newer = "2030-01-01T00:00:00+00:00"
        with mock.patch(
            "sovereign_stack.arrival_state._store_head_timestamp",
            return_value=(newer, datetime(2030, 1, 1, tzinfo=timezone.utc)),
        ):
            state = _build(root, "full")
        assert state.freshness != "current"
        assert state.freshness == "stale"
        from sovereign_stack.arrival_state import _parse_ts

        assert _parse_ts(state.source_high_watermark) >= _parse_ts(newer)

    def test_stale_line_is_never_bare(self, tmp_path: Path):
        # A bare "Freshness: stale" reads as broken on the surface every instance
        # reads first — the render must carry a reason clause. Proven on the
        # same synthetic true-lag as above, not by riding a routine learning.
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        with mock.patch(
            "sovereign_stack.arrival_state._store_head_timestamp",
            return_value=(
                "2030-01-01T00:00:00+00:00",
                datetime(2030, 1, 1, tzinfo=timezone.utc),
            ),
        ):
            state = _build(root, "full")
        from sovereign_stack.arrival_state import render_full

        text = render_full(state)
        assert "Freshness: stale — " in text
        assert "Freshness: stale\n" not in text

    def test_record_learning_alone_does_not_flip_freshness_to_stale(self, tmp_path: Path):
        # REGRESSION (the bug this fix closes): a `learning` newer than every
        # gathered insight/open_thread/handoff must NOT flip freshness to
        # "stale". No door renders the learnings layer, so it is excluded from
        # the store-head probe's scope — counting it made record_learning (a
        # routine, frequent write) the only practical trigger of "stale",
        # crying wolf on the first line every instance reads. Cover both doors
        # that show chronicle recency (full + foyer).
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        newer = "2030-01-01T00:00:00+00:00"
        learn_dir = root / "chronicle" / "learnings" / "general"
        learn_dir.mkdir(parents=True, exist_ok=True)
        (learn_dir / "s.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": newer,
                    "applies_to": "general",
                    "what_happened": "a thing",
                    "what_learned": "a lesson newer than all rendered entries",
                }
            )
            + "\n"
        )
        for profile in ("full", "foyer"):
            state = _build(root, profile)
            assert state.freshness == "current", (
                f"{profile} boot went stale on a bare record_learning write — "
                "the false positive is back"
            )
            assert not state.partial_reasons

    def test_consuming_newest_handoff_does_not_flip_to_stale(self, tmp_path: Path):
        # The fixture's newest entry is the handoff. The default consume=True
        # marks it consumed; the NEXT boot must not read "stale" just because a
        # consumed handoff file still exists (handoffs are excluded from the
        # store-head probe for exactly this reason).
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        from sovereign_stack.handoff import HandoffEngine

        ho = HandoffEngine(root=str(root))
        pending = ho.unconsumed(limit=20)
        assert pending
        ho.mark_consumed([r["_path"] for r in pending], consumed_by="phase4-reader")
        state = _build(root, "full")
        assert state.freshness == "current"

    def test_degraded_gather_yields_incomplete(self, tmp_path: Path):
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        # Force the open-threads gather to raise.
        exp, ho, rx = _engines(root)
        from sovereign_stack.arrival_state import build_arrival_state

        with mock.patch.object(exp, "get_open_threads", side_effect=RuntimeError("boom")):
            state = build_arrival_state(
                root,
                reader="phase4-reader",
                profile="full",
                experiential=exp,
                handoff_engine=ho,
                reflexive_surface=rx,
                spiral_summary={
                    "session_id": "s",
                    "current_phase": "INITIALIZATION",
                    "tool_call_count": 1,
                    "reflection_depth": 0,
                    "session_duration_seconds": 0.0,
                },
            )
        assert state.freshness == "incomplete"
        assert any("open_threads" in r for r in state.partial_reasons)
        # The incompleteness surfaces in the rendered as-of block.
        from sovereign_stack.arrival_state import render_full

        text = render_full(state)
        assert "Freshness: incomplete" in text

    def test_degrade_does_not_raise_through_render(self, tmp_path: Path):
        # boot-degrades-not-raises: a degraded section must never raise out of
        # build/render. The lineage collector raising is caught and rendered as
        # the exact legacy degrade line.
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        with mock.patch(
            "sovereign_stack.witness.collect_lineage", side_effect=RuntimeError("nope")
        ):
            state = _build(root, "full")
        from sovereign_stack.arrival_state import render_full

        text = render_full(state)  # must not raise
        assert "(lineage layer unavailable: nope)" in text


# ── 3. Single doorway ────────────────────────────────────────────────────────


class TestSingleDoorway:
    def test_all_three_doors_route_through_build_arrival_state(self, tmp_path: Path):
        import sovereign_stack.server as server

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)

        seen: list[str] = []
        orig = server.build_arrival_state

        def _spy(*args, **kwargs):
            seen.append(kwargs.get("profile"))
            return orig(*args, **kwargs)

        with mock.patch.object(server, "build_arrival_state", _spy):
            fx.run_all_doors(root)

        assert seen == ["full", "foyer", "gentle"]


# ── Render-detail coverage the golden (consume=False) does not exercise ──────


class TestRenderDetails:
    def test_consumed_handoff_line_renders_with_count(self, tmp_path: Path):
        # The default consume=True path prints a "(N handoff(s) marked consumed …)"
        # line the golden (consume=False) never sees. Render it directly.
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        state = _build(root, "full")
        from sovereign_stack.arrival_state import render_full

        with_consume = render_full(state, consumed_count=2)
        assert "(2 handoff(s) marked consumed" in with_consume
        # None (not consumed) omits the line entirely.
        without = render_full(state, consumed_count=None)
        assert "marked consumed" not in without

    def test_full_content_and_compact_assemble(self, tmp_path: Path):
        # Light smoke on the other flag combos: they must assemble without error
        # and keep the closing + as-of receipt.
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        state = _build(root, "full", compact=True)
        from sovereign_stack.arrival_state import render_full

        compact_text = render_full(state, compact=True)
        assert "BEFORE YOU BEGIN" not in compact_text  # preamble skipped
        assert "━━━ AS OF ━━━" in compact_text
        full_text = render_full(state, full_content=True)
        assert "BOOTSTRAP CONTEXT" in full_text
        assert "Content above truncated" not in full_text  # gated on full_content


# ── Wrapper-identity: each witness collect/render split is byte-identical ─────


class TestWrapperIdentity:
    def test_self_model(self, tmp_path: Path):
        from sovereign_stack import witness

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        for cap in (180, None):
            assert witness.format_self_model(root, max_obs_len=cap) == witness.render_self_model(
                witness.collect_self_model(root), max_obs_len=cap
            )

    def test_uncertainties(self, tmp_path: Path):
        from sovereign_stack import witness

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        for cap in (160, None):
            assert witness.format_unresolved_uncertainties(
                root, max_text_len=cap
            ) == witness.render_uncertainties(witness.collect_uncertainties(root), max_text_len=cap)

    def test_lineage(self, tmp_path: Path):
        from sovereign_stack import witness

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        for fc in (False, True):
            assert witness.format_lineage_layer(
                root, "phase4-reader", 5, full_content=fc
            ) == witness.render_lineage(
                witness.collect_lineage(root, "phase4-reader", 5), full_content=fc
            )

    def test_the_ground(self, tmp_path: Path):
        from sovereign_stack import witness
        from sovereign_stack.ground import load_ground_entries

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        for calm in (False, True):
            assert witness.format_the_ground(root, calm=calm) == witness.render_the_ground(
                load_ground_entries(root / "chronicle"), calm=calm
            )

    def test_protected(self, tmp_path: Path):
        from sovereign_stack.protected import (
            collect_protected_drawer,
            protected_boot_line,
            render_protected_boot_line,
        )

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        chron = root / "chronicle"
        assert protected_boot_line(chron) == render_protected_boot_line(
            collect_protected_drawer(chron)
        )
