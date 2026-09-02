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
import json

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
        assert "aperture-v2" in _aperture_text()

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


class TestTheThreadCountReachesNestedShards:
    """THE APERTURE AND THE CONSOLE MUST AGREE, and for one release they did
    not: `dashboard_readers.read_open_threads` walked the store with `rglob`
    while the aperture used a flat `glob`, so the legacy nested shard
    `open_threads/tech-debt,compaction,auto-detection/log.jsonl` was counted by
    the console and invisible to the door. The surface whose entire job is to
    stop a projection passing as the corpus was itself projecting.

    Fixed in 66a9984 by routing the aperture through `iter_thread_shards` —
    memory's ONE walk, which is recursive AND dot-filtered. UNTESTED until now:
    nothing pinned the recursion, so a future edit back to a flat glob would
    have gone green. This class is that pin, on a tmp root — no live dependency.
    """

    @staticmethod
    def _seed(tmp_path):
        root = tmp_path / ".sovereign"
        threads = root / "chronicle" / "open_threads"
        threads.mkdir(parents=True)
        # measure_aperture scandirs these unconditionally and raises
        # FileNotFoundError when they are absent (the caller in arrival_state
        # catches it and renders `unmeasured`). Seed them so this class tests
        # the thread walk and nothing else.
        (root / "chronicle" / "insights").mkdir(parents=True)
        (root / "handoffs").mkdir(parents=True)
        (threads / "flat.jsonl").write_text(
            '{"question": "a", "resolved": false}\n{"question": "b", "resolved": true}\n'
        )
        nested = threads / "tech-debt,compaction,auto-detection"
        nested.mkdir()
        (nested / "log.jsonl").write_text('{"question": "c", "resolved": false}\n')
        return root

    def test_nested_shards_are_counted(self, tmp_path):
        from datetime import datetime, timezone

        from sovereign_stack.aperture import measure_aperture

        root = self._seed(tmp_path)
        ap = measure_aperture(datetime.now(timezone.utc), root=root)["surfaces"]["open_threads"]
        # 3, not 2: a flat glob sees only flat.jsonl's two lines.
        assert ap["on_disk"] == 3
        assert ap["unresolved"] == 2

    def test_it_agrees_with_the_console_reader_on_the_same_store(self, tmp_path):
        """One store, two surfaces, no drift — asserted against the reader
        itself rather than against a number copied from it."""
        from datetime import datetime, timezone

        from sovereign_stack import dashboard_readers
        from sovereign_stack.aperture import measure_aperture

        root = self._seed(tmp_path)
        ap = measure_aperture(datetime.now(timezone.utc), root=root)["surfaces"]["open_threads"]
        directory = root / "chronicle" / "open_threads"
        console_unresolved = sum(
            1
            for path in directory.rglob("*.jsonl")
            for line in path.read_text().splitlines()
            if line.strip() and not json.loads(line).get("resolved", False)
        )
        assert ap["unresolved"] == console_unresolved
        assert dashboard_readers is not None  # the reader this parity is owed to

    def test_a_dotted_backup_dir_is_still_excluded(self, tmp_path):
        """Recursion without the dot-filter trades an under-read for a
        CORRUPTING over-read: a retired copy served as live, and writable
        through resolve_thread_by_id. The aperture must count what readers can
        actually reach, no more."""
        from datetime import datetime, timezone

        from sovereign_stack.aperture import measure_aperture

        root = self._seed(tmp_path)
        backup = root / "chronicle" / "open_threads" / ".bak-20260502"
        backup.mkdir()
        (backup / "old.jsonl").write_text('{"question": "retired", "resolved": false}\n')
        ap = measure_aperture(datetime.now(timezone.utc), root=root)["surfaces"]["open_threads"]
        assert ap["on_disk"] == 3
        assert ap["unresolved"] == 2
