"""
The boot door must carry the aperture, because the heartbeat cannot reach the
seats that need it.

FAILURE SPECIMEN, 2026-08-28, found by the ChatGPT seat from the OpenAI bridge
within an hour of aperture-v1 shipping — the outside door HQ structurally
cannot occupy:

    "Discovery for heartbeat exposed no heartbeat tool either, so aperture-v1
     may be live at the public heartbeat while not directly callable as a
     first-class tool from this OpenAI connector."

Verified: a `heartbeat` tool exists in the registry but is NOT in
CANONICAL_RING_1, and none of the boot tools a bridge seat CAN call carried the
aperture. The surface built to stop arriving seats mistaking a projection for
the corpus was reachable only by seats that already had a shell — which is
every seat except the ones whose misattribution earned it.

`where_did_i_leave_off` is the one boot door every arriving seat calls,
including across bridges. Putting the aperture there needs no permission change
and lands it where arrival actually happens.
"""

from __future__ import annotations

import asyncio

from sovereign_stack import server


def _boot_text() -> str:
    out = asyncio.run(
        server._dispatch_tool("where_did_i_leave_off", {"source_instance": "test-aperture-probe"})
    )
    return out[0].text


from sovereign_stack import arrival_state as ast_mod  # noqa: E402


def _aperture_text() -> str:
    """The block itself. Read-only against the live store — measuring never writes."""
    return "\n".join(ast_mod._render_aperture())


def _boot_text(tmp_path) -> str:
    """The full door via the house fixture, with consume=False so no live write occurs."""
    from tests import _phase4_fixture as fx

    fx.build_fixture(tmp_path)
    return fx.run_door(
        tmp_path,
        "where_did_i_leave_off",
        {"consume": False, "source_instance": "aperture-probe", "full_content": False},
    )


class TestTheApertureBlock:
    def test_states_what_is_withheld(self):
        assert "APERTURE" in _aperture_text().upper()

    def test_names_the_policy_version(self):
        """No neutral projection — viewing conditions must be versioned."""
        assert "aperture-v1" in _aperture_text()

    def test_gives_totals_not_just_what_it_showed(self):
        """The specimen: a seat saw 5 letters and could not learn 13 existed."""
        t = _aperture_text()
        assert "on disk" in t.lower()
        assert "lineage_to_arrival" in t

    def test_names_what_no_parameter_can_reach(self):
        assert "NOT REACHABLE BY ANY PARAMETER" in _aperture_text()

    def test_warns_that_the_default_order_is_recency(self):
        """RULE ZERO's finding, delivered where an arriving seat will see it."""
        assert "relevance" in _aperture_text().lower()


class TestItReachesTheDoor:
    def test_the_full_boot_door_carries_it(self, tmp_path):
        """
        The whole point of moving it out of the heartbeat: the seats that need
        it call this door, not GET /api/heartbeat.
        """
        assert "APERTURE" in _boot_text(tmp_path).upper()


class TestFailsClosed:
    """These must be able to FAIL. A gate never shown to reject is decoration."""

    def test_unmeasurable_aperture_says_so(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("simulated: store unreadable")

        monkeypatch.setattr(ast_mod, "measure_aperture", boom)
        t = _aperture_text().lower()
        assert "unmeasured" in t
        assert "not zero counts" in t

    def test_unmeasurable_aperture_emits_no_counts(self, monkeypatch):
        """An absence manufactured by the instrument must never read as zero."""

        def boom(*a, **k):
            raise OSError("simulated")

        monkeypatch.setattr(ast_mod, "measure_aperture", boom)
        assert "on disk" not in _aperture_text().lower()
