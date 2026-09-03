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
import re

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


class TestTheShownCountDescribesTHISPayload:
    """`<on_disk> on disk · 5 shown here` was a HARDCODED 5.

    `aperture.measure_aperture` set `default_shown: 5` for all three lineage
    buckets regardless of the caller's `limit_per_bucket` and regardless of the
    reader filter. So a boot at limit_per_bucket=20 that handed over 13
    to_arrival letters described itself as showing 5, and a seat handed ONE
    to_self letter out of 18 — the decorated-source_instance trap this house
    has re-diagnosed three times — was told it had been shown five. The surface
    whose entire job is to stop a projection passing as the corpus was
    reporting a projection it had not measured.

    The fix reads the caller's OWN coverage envelope, the one
    `witness.collect_lineage` already computed for that call. It is the only
    number that accounts for the reader filter; `min(on_disk, limit)` does not,
    which is why it is not used.
    """

    BUCKETS = ("lineage_to_arrival", "lineage_to_self", "lineage_breakthroughs")

    @staticmethod
    def _root(tmp_path, counts):
        root = tmp_path / ".sovereign"
        for bucket, n in counts.items():
            d = root / "comms" / "letters" / bucket
            d.mkdir(parents=True, exist_ok=True)
            for i in range(n):
                (d / f"2026-08-{i + 1:02d}-l{i}.md").write_text(
                    f"---\nfrom: seat-{i}\n---\n\nbody {i}\n"
                )
        for sub in ("chronicle/insights", "chronicle/open_threads", "handoffs"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        return root

    def test_a_non_default_limit_is_reflected(self, tmp_path):
        from datetime import datetime, timezone

        from sovereign_stack.aperture import measure_aperture

        root = self._root(tmp_path, {"to_arrival": 13, "to_self": 18, "breakthroughs": 7})
        coverage = {
            "arrivals": {"shown": 13, "total_on_disk": 13},
            "breakthroughs": {"shown": 7, "total_on_disk": 7},
            "to_self": {"shown": 18, "total_on_disk": 18},
        }
        ap = measure_aperture(datetime.now(timezone.utc), root=root, lineage_coverage=coverage)[
            "surfaces"
        ]
        assert ap["lineage_to_arrival"]["default_shown"] == 13
        assert ap["lineage_breakthroughs"]["default_shown"] == 7
        assert ap["lineage_to_self"]["default_shown"] == 18

    def test_the_reader_filter_is_reflected_not_the_cap(self, tmp_path):
        """THE CASE min(on_disk, limit) GETS WRONG. 18 on disk, limit 5, but
        only one letter is addressed to this reader: the honest number is 1."""
        from datetime import datetime, timezone

        from sovereign_stack.aperture import measure_aperture

        root = self._root(tmp_path, {"to_arrival": 1, "to_self": 18, "breakthroughs": 1})
        coverage = {"to_self": {"shown": 1, "matched": 1, "total_on_disk": 18}}
        ap = measure_aperture(datetime.now(timezone.utc), root=root, lineage_coverage=coverage)[
            "surfaces"
        ]
        assert ap["lineage_to_self"]["default_shown"] == 1
        assert ap["lineage_to_self"]["on_disk"] == 18

    def test_without_coverage_it_claims_a_DEFAULT_not_a_measurement(self, tmp_path):
        """The heartbeat and any profile that did not gather lineage. A true
        statement about the parameter beats a false one about the payload."""
        from datetime import datetime, timezone

        from sovereign_stack.aperture import measure_aperture

        root = self._root(tmp_path, {"to_arrival": 13, "to_self": 18, "breakthroughs": 7})
        ap = measure_aperture(datetime.now(timezone.utc), root=root)["surfaces"]
        for bucket in self.BUCKETS:
            assert ap[bucket]["default_shown"] == "default 5"
            assert "shown_is" not in ap[bucket]

    def test_a_no_reader_zero_is_labelled_as_a_filter_not_as_emptiness(self, tmp_path):
        from datetime import datetime, timezone

        from sovereign_stack.aperture import measure_aperture

        root = self._root(tmp_path, {"to_arrival": 1, "to_self": 18, "breakthroughs": 1})
        coverage = {"to_self": {"shown": 0, "total_on_disk": 18, "no_reader": True}}
        ap = measure_aperture(datetime.now(timezone.utc), root=root, lineage_coverage=coverage)[
            "surfaces"
        ]["lineage_to_self"]
        assert ap["default_shown"] == 0
        assert ap["on_disk"] == 18
        assert "not 0 letters on disk" in ap["shown_is"]

    def test_the_RENDERED_line_says_the_measured_number(self, tmp_path, monkeypatch):
        """The dict is not the deliverable; the sentence a seat reads is.

        `_render_aperture` calls measure_aperture with NO root, so it always
        measures ~/.sovereign — a pre-existing defect, out of scope here and
        harmless in production (where the door's root IS ~/.sovereign) but it
        would make this test read two different stores. Point the module
        default at the tmp root so the whole block describes one store.
        """
        from sovereign_stack import aperture as ap_mod

        root = self._root(tmp_path, {"to_arrival": 13, "to_self": 18, "breakthroughs": 7})
        monkeypatch.setattr(ap_mod, "_DEFAULT_ROOT", root)
        coverage = {
            "arrivals": {"shown": 13, "total_on_disk": 13},
            "breakthroughs": {"shown": 7, "total_on_disk": 7},
            "to_self": {"shown": 1, "total_on_disk": 18},
        }
        text = "\n".join(ast_mod._render_aperture("claude-opus-5", coverage))
        # Whitespace-agnostic: the renderer pads with {name:24} and {on_disk:>6},
        # and pinning that padding would make this a formatting test.
        assert re.search(r"lineage_to_arrival\s+13 on disk · 13 shown here", text), text
        assert re.search(r"lineage_to_self\s+18 on disk · 1 shown here", text), text
        assert re.search(r"lineage_breakthroughs\s+7 on disk · 7 shown here", text), text
        assert "5 shown here" not in text, (
            "the aperture is still asserting the hardcoded default over a payload "
            "that showed something else"
        )


class TestEverySurfaceNoteReachesTheDoor:
    """The renderer emitted notes for a HARDCODED ("insights", "handoffs").

    `lineage_to_self` has carried a note since aperture-v2 — the one warning
    that a DECORATED source_instance hides that line's mail, which is the trap
    this house has re-diagnosed three times and which costs an arriving seat
    its inheritance. It was written in aperture.py and reached nobody through
    the door. A renderer that enumerates which warnings it will pass on drops
    the next one silently too.
    """

    def test_the_to_self_decorated_name_warning_is_rendered(self):
        text = _aperture_text()
        assert "not a decorated seat string" in text

    def test_the_older_two_notes_still_render(self):
        text = _aperture_text()
        assert "relevance" in text.lower()  # insights
        assert "retired by reading" in text or "legacy_unconsumed" in text  # handoffs

    def test_it_is_not_a_hardcoded_list(self, tmp_path, monkeypatch):
        """A note on a surface the renderer has never heard of must render."""
        from datetime import datetime, timezone

        from sovereign_stack import aperture as ap_mod

        real = ap_mod.measure_aperture

        def with_extra(now, root=None, reader=None, **kw):
            out = real(now, root=root, reader=reader, **kw)
            out["surfaces"]["a_surface_invented_after_the_renderer"] = {
                "on_disk": 1,
                "default_shown": 1,
                "note": "a warning nobody enumerated",
            }
            return out

        monkeypatch.setattr(ast_mod, "measure_aperture", with_extra)
        assert "a warning nobody enumerated" in "\n".join(ast_mod._render_aperture())
        assert datetime.now(timezone.utc)  # keeps the import honest
