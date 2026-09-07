"""
Tests for the OPEN-THREADS INDEX panel — the last key on /snapshot.json.

WHAT THIS PANEL IS FOR, because it decides what these tests defend. The
console's OPEN THREADS panel renders the six NEWEST unresolved threads. On
2026-09-06 there were 191 open and 176 had never been touched, some of them
138 days old. Six of 191 is not a sample, it is a lid: the old rows are
unreachable from that panel by construction, and no amount of watching
reveals them. This panel reads the catalog a separate index run writes and
renders the totals instead.

Three things these tests defend:

  1. ABSENCE IS RENDERED, NOT FAKED, AND IT SAYS WHY.
     Missing file, unreadable file, non-object, no `entries`, no parseable
     timestamp — each returns ``status="absent"`` with a reason and the age
     of the last index file that did exist. The invariant that matters is
     asserted directly: THE ABSENT ENVELOPE CONTAINS NO COUNT OF ANY KIND.
     That is what makes an explicit envelope safe where a plausible zero
     would not be, and it is why this reader is allowed to depart from the
     module's "return None" rule.

  2. THE COUNTS ARE THE FILING'S COUNTS.
     The fixture below is a hand-built miniature of the real
     2026-09-06 filing's shape, and the expected numbers are worked out by
     hand in each test rather than recomputed by the code under test. The
     near-duplicate rule in particular has two halves that both matter:
     group by the ``dup-NN`` label, and drop families with fewer than two
     OPEN members.

  3. A BROKEN CATALOG BREAKS ONLY THIS PANEL.
     Malformed JSON must not take /snapshot.json down; the other 18 keys
     keep rendering.

ISOLATION: every test sets ``SOVEREIGN_ROOT`` to a tmp_path and builds its
own fixture. Nothing here reads the operator's live ``~/.sovereign`` — this
house has twice shipped a suite that asserted against Anthony's real store
(the protected-drawer boot tests, and the gate-census tests two commits
before this one). The real filing was verified by hand, outside the suite.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack import connectivity as conn
from sovereign_stack import dashboard_readers as readers
from sovereign_stack import dashboard_web as web

STATIC_DIR = Path(web.__file__).parent / "dashboard_web_static"

# Any key whose name could be mistaken for a measurement. The absent
# envelope must contain NONE of these — that is the fail-open this whole
# panel is shaped around.
_COUNT_SHAPED_KEYS = (
    "open_count",
    "never_touched",
    "touched",
    "answered_elsewhere",
    "duplicate_clusters",
    "duplicate_cluster_members",
    "entries_total",
    "by_category",
    "by_month",
    "by_shape",
)


def _index_dir(root: Path) -> Path:
    directory = root / "filings" / "open-threads-index"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _entry(
    thread_id,
    *,
    status="open",
    opened="2026-05-02T04:19:20+00:00",
    category="governance-and-gates",
    shape="none",
    touches=0,
    answered=None,
    similar=None,
):
    return {
        "thread_id": thread_id,
        "opened": opened,
        "domain": "d",
        "question": "q",
        "status": status,
        "age_days": 1.0,
        "touches": {"count": touches, "last": None},
        "category": category,
        "tags": [],
        "shape": shape,
        "similar_to": similar or [],
        "answered_elsewhere": answered,
        "suggested_owner": {"seat": "hq", "reason": "r"},
    }


def _fixture_payload(generated_at=None, entries=None, declared=None):
    """A miniature of the real filing.

    Worked out by hand so the expected numbers below are independent of the
    code under test:

      8 entries, 6 open (t1..t6) and 2 resolved (r1, r2)
      touched: t1 only            -> never_touched 5, touched 1
      answered_elsewhere: t2 only -> 1
      categories: governance 3 (t1,t2,t3), entropy 2 (t4,t5), memory 1 (t6)
      shapes: none 3, boundary 2, delegation 1
      months: 2026-04 2, 2026-05 3, 2026-07 1
      dup-01 = {t3, t4} (2 open members)   -> counted
      dup-02 = {t5, r1}  (1 OPEN member)   -> dropped by the >=2-open rule
                                              => 1 cluster, 2 member ids
    """
    if entries is None:
        entries = [
            _entry("t1", opened="2026-04-01T00:00:00+00:00", touches=2),
            _entry(
                "t2",
                opened="2026-04-09T00:00:00+00:00",
                answered={"kind": "insight", "pointer": "p"},
            ),
            _entry(
                "t3",
                opened="2026-05-02T00:00:00+00:00",
                shape="boundary",
                similar=[{"thread_id": "t4", "reason": "dup-01 (same-question): split write."}],
            ),
            _entry(
                "t4",
                opened="2026-05-03T00:00:00+00:00",
                category="entropy-program",
                shape="boundary",
                similar=[{"thread_id": "t3", "reason": "dup-01 (same-question): split write."}],
            ),
            _entry(
                "t5",
                opened="2026-05-04T00:00:00+00:00",
                category="entropy-program",
                shape="delegation",
                similar=[{"thread_id": "r1", "reason": "dup-02 (exact-copy): recorded twice."}],
            ),
            _entry("t6", opened="2026-07-11T00:00:00+00:00", category="memory-and-surfacing"),
            _entry(
                "r1",
                status="resolved",
                opened="2026-05-04T00:00:00+00:00",
                similar=[{"thread_id": "t5", "reason": "dup-02 (exact-copy): recorded twice."}],
            ),
            _entry("r2", status="resolved", opened="2026-06-01T00:00:00+00:00"),
        ]
    opens = [e for e in entries if e.get("status") == "open"]
    return {
        "author": "test fixture",
        "coverage": {
            "generated_at_utc": generated_at or datetime.now(timezone.utc).isoformat(),
            "counts": {
                "open_including_nested": len(opens) if declared is None else declared,
                "open_top_level": len(opens),
            },
        },
        "entries": entries,
    }


def _write_index(root: Path, payload, name: str = "LATEST.json") -> Path:
    path = _index_dir(root) / name
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
    return path


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    return tmp_path


# ── Absence: four ways in, one shape out ────────────────────────────────────


class TestAbsentIsStatedNotFaked:
    def test_missing_file_is_absent_with_a_reason(self, root):
        out = readers.read_open_threads_index()
        assert out["status"] == "absent"
        assert "LATEST.json not found" in out["reason"]
        # Nothing has ever landed here, and the reader says exactly that
        # rather than implying a catalog went missing.
        assert "no index run has landed here" in out["reason"]
        assert out["last_seen_at"] is None
        assert out["last_seen_age_seconds"] is None

    def test_missing_pointer_beside_dated_files_is_a_different_reason(self, root):
        """An index that ran but did not update LATEST.json is a different
        fault from one that never ran, and the panel must not merge them."""
        dated = _write_index(root, _fixture_payload(), name="2026-09-06_open-threads-index.json")
        out = readers.read_open_threads_index()
        assert out["status"] == "absent"
        assert "the pointer is missing" in out["reason"]
        # "The age of whatever last existed" — mtime of the dated sibling.
        assert out["last_seen_source"] == str(dated)
        assert out["last_seen_age_seconds"] is not None
        assert out["last_seen_age_seconds"] >= 0

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("{not json at all", "not valid JSON"),
            ('["a", "list"]', "not a JSON object"),
            (
                '{"coverage": {"generated_at_utc": "2026-09-06T00:00:00+00:00"}}',
                "no `entries` list",
            ),
            ('{"entries": []}', "no parseable generation timestamp"),
            (
                '{"entries": [], "coverage": {"generated_at_utc": "not-a-date"}}',
                "no parseable generation timestamp",
            ),
        ],
    )
    def test_malformed_is_absent_and_names_what_is_wrong(self, root, content, expected):
        _write_index(root, content)
        out = readers.read_open_threads_index()
        assert out["status"] == "absent"
        assert expected in out["reason"], out["reason"]

    def test_the_absent_envelope_carries_no_count_of_any_kind(self, root):
        """THE load-bearing assertion of this file.

        An explicit absent envelope is only safe because it cannot be
        misread as a measurement. If any count-shaped key ever appears
        here, this reader has become the plausible-zero it was written to
        avoid, and the module's discipline #1 exemption stops being narrow.
        """
        _write_index(root, "{broken")
        out = readers.read_open_threads_index()
        for key in _COUNT_SHAPED_KEYS:
            assert key not in out, f"absent envelope leaked a count-shaped key: {key}"
        assert out["age_seconds"] is None

    def test_absent_uses_mtime_and_says_so_in_its_own_field(self, root):
        """Two ages, two instruments, never the same field.

        `age_seconds` is the record's own generation stamp; it is None when
        there is no record. `last_seen_age_seconds` is mtime-derived. This
        module already carries a test insisting a stamp and an mtime never
        get conflated, and this is that rule for this reader.
        """
        _write_index(root, "{broken")
        out = readers.read_open_threads_index()
        assert out["age_seconds"] is None
        assert out["last_seen_age_seconds"] is not None
        assert out["last_seen_source"].endswith("LATEST.json")


# ── Presence: the counts are the filing's counts ────────────────────────────


class TestPresentCounts:
    def test_exact_counts_from_the_fixture(self, root):
        _write_index(root, _fixture_payload())
        out = readers.read_open_threads_index()
        assert out["status"] == "present"
        assert out["entries_total"] == 8
        assert out["open_count"] == 6
        assert out["never_touched"] == 5
        assert out["touched"] == 1
        assert out["answered_elsewhere"] == 1

    def test_breakdowns_sum_to_the_open_count_and_only_the_open_count(self, root):
        """One denominator for every breakdown. The published .md mixes two
        (its category table is over all open, its month table over top-level
        only) and the two cannot be told apart from the JSON — so this panel
        picks one and every column adds up to it."""
        _write_index(root, _fixture_payload())
        out = readers.read_open_threads_index()
        for key in ("by_category", "by_month", "by_shape"):
            assert sum(row["count"] for row in out[key]) == out["open_count"], key

    def test_category_and_shape_tallies(self, root):
        _write_index(root, _fixture_payload())
        out = readers.read_open_threads_index()
        assert out["by_category"] == [
            {"name": "governance-and-gates", "count": 3},
            {"name": "entropy-program", "count": 2},
            {"name": "memory-and-surfacing", "count": 1},
        ]
        assert out["by_shape"] == [
            {"name": "none", "count": 3},
            {"name": "boundary", "count": 2},
            {"name": "delegation", "count": 1},
        ]

    def test_months_are_chronological_not_biggest_first(self, root):
        """A month axis sorted by size is unreadable as a trend, and the
        month list is the one breakdown whose order carries meaning."""
        _write_index(root, _fixture_payload())
        out = readers.read_open_threads_index()
        assert [row["name"] for row in out["by_month"]] == ["2026-04", "2026-05", "2026-07"]
        assert [row["count"] for row in out["by_month"]] == [2, 3, 1]

    def test_duplicate_clusters_need_two_OPEN_members(self, root):
        """Both halves of the rule at once.

        dup-01 has two open members and counts. dup-02 has one open member
        and one resolved one — a "cluster" of a single open thread is not a
        duplicate of anything still open, and counting it would inflate the
        number Anthony reads next to the filing.
        """
        _write_index(root, _fixture_payload())
        out = readers.read_open_threads_index()
        assert out["duplicate_clusters"] == 1
        assert out["duplicate_cluster_members"] == 2
        assert out["cluster_method"] == "dup-label"

    def test_labels_present_but_no_family_qualifies_stays_on_the_label_method(self, root):
        """The fallback must not fire while the label convention is alive.

        A catalog whose only dup-NN family has aged down to ONE open member
        has zero clusters BY THAT METHOD — it has not stopped using the
        method. Gating the fallback on "no qualifying family" instead of "no
        labels at all" would report a components-derived count under the
        name `dup-label`'s absence, i.e. a number tied to an algorithm that
        is not the one in force. `dup-05` in the real 2026-09-06 filing is
        already this shape at n=1; a catalog where every family reaches it
        is what fires this.
        """
        entries = [
            _entry("a", similar=[{"thread_id": "z", "reason": "dup-01 (exact-copy): twice."}]),
            _entry("z", status="resolved"),
            # An unlabelled open pair that the components fallback WOULD
            # find, so this test fails loudly if the gate is wrong.
            _entry("b", similar=[{"thread_id": "c", "reason": "no label here"}]),
            _entry("c", similar=[{"thread_id": "b", "reason": "no label here"}]),
        ]
        _write_index(root, _fixture_payload(entries=entries))
        out = readers.read_open_threads_index()
        assert out["cluster_method"] == "dup-label"
        assert out["duplicate_clusters"] == 0
        assert out["duplicate_cluster_members"] == 0

    def test_unlabelled_links_fall_back_to_components_and_say_so(self, root):
        """A future index run that stops writing `dup-NN` must not make the
        cluster count silently ZERO while `similar_to` links plainly exist.
        It degrades to connected components and NAMES the method, because a
        number that cannot be tied to the filing beside it is a rumour."""
        entries = [
            _entry("a", similar=[{"thread_id": "b", "reason": "same question, no label"}]),
            _entry("b", similar=[{"thread_id": "a", "reason": "same question, no label"}]),
            _entry("c"),
        ]
        _write_index(root, _fixture_payload(entries=entries))
        out = readers.read_open_threads_index()
        assert out["cluster_method"] == "similar-to-components"
        assert out["duplicate_clusters"] == 1
        assert out["duplicate_cluster_members"] == 2

    def test_a_link_to_a_resolved_thread_is_not_a_component(self, root):
        """The components fallback is restricted to OPEN entries; a link
        pointing at something already closed builds no cluster."""
        entries = [
            _entry("a", similar=[{"thread_id": "z", "reason": "unlabelled"}]),
            _entry("z", status="resolved"),
        ]
        _write_index(root, _fixture_payload(entries=entries))
        out = readers.read_open_threads_index()
        assert out["duplicate_clusters"] == 0
        assert out["duplicate_cluster_members"] == 0

    def test_an_honestly_empty_catalog_is_present_with_real_zeros(self, root):
        """ "Never a zero" means never FABRICATE one. A catalog that has read
        the tree and found nothing open is a measurement, and hiding it
        behind ABSENT would be the mirror-image lie."""
        _write_index(root, _fixture_payload(entries=[]))
        out = readers.read_open_threads_index()
        assert out["status"] == "present"
        assert out["open_count"] == 0
        assert out["never_touched"] == 0
        assert out["by_category"] == []
        assert out["duplicate_clusters"] == 0

    def test_age_comes_from_the_generation_stamp_not_the_file_mtime(self, root):
        """The file was written seconds ago; the catalog inside it is two
        days old. An rsync moves an mtime, it does not move a generation."""
        stamp = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        _write_index(root, _fixture_payload(generated_at=stamp))
        out = readers.read_open_threads_index()
        assert out["generated_at"] == stamp
        assert 172000 < out["age_seconds"] < 173500

    @pytest.mark.parametrize("payload_key", ["generated_at_utc", "generated_at"])
    def test_top_level_timestamp_fallbacks_are_honoured(self, root, payload_key):
        """The LATEST.json writer is a different program from the one whose
        output this was verified against. A cosmetic difference in WHERE it
        puts its own timestamp must not render the panel absent."""
        payload = _fixture_payload()
        stamp = payload["coverage"].pop("generated_at_utc")
        payload[payload_key] = stamp
        _write_index(root, payload)
        out = readers.read_open_threads_index()
        assert out["status"] == "present"
        assert out["generated_at"] == stamp

    def test_counts_reconcile_against_the_files_own_coverage_line(self, root):
        _write_index(root, _fixture_payload())
        assert readers.read_open_threads_index()["counts_reconcile"] is True

    def test_a_disagreement_with_the_coverage_line_is_reported_not_hidden(self, root):
        """If the file's own coverage says 99 open and the entries say 6,
        something is wrong with the index run and the panel must show it
        rather than quietly trusting one of the two."""
        _write_index(root, _fixture_payload(declared=99))
        out = readers.read_open_threads_index()
        assert out["open_count"] == 6
        assert out["declared_open_including_nested"] == 99
        assert out["counts_reconcile"] is False

    def test_missing_coverage_counts_leave_reconcile_unknown_not_false(self, root):
        """Unknown is not a failure. A file with no coverage line cannot be
        cross-checked, and saying "they disagree" would be an invention."""
        payload = _fixture_payload()
        payload["coverage"].pop("counts")
        _write_index(root, payload)
        out = readers.read_open_threads_index()
        assert out["counts_reconcile"] is None
        assert out["declared_open_including_nested"] is None

    def test_provenance_is_mandatory(self, root):
        _write_index(root, _fixture_payload())
        out = readers.read_open_threads_index()
        assert out["source"].endswith("filings/open-threads-index/LATEST.json")
        assert out["age_seconds"] is not None
        assert out["stale_after_days"] > 0

    def test_the_reader_honours_sovereign_root(self, monkeypatch, tmp_path):
        """Resolved on every call, never captured at import — the same rule
        every other reader in this module follows."""
        first = tmp_path / "a"
        second = tmp_path / "b"
        _write_index(first, _fixture_payload())
        monkeypatch.setenv("SOVEREIGN_ROOT", str(first))
        assert readers.read_open_threads_index()["status"] == "present"
        monkeypatch.setenv("SOVEREIGN_ROOT", str(second))
        assert readers.read_open_threads_index()["status"] == "absent"


# ── The snapshot contract ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_external_probes(monkeypatch):
    readers.reset_caches()
    monkeypatch.setattr(readers, "fetch_bridge_heartbeat", lambda: None)
    monkeypatch.setattr(readers, "read_guardian", lambda: None)
    yield
    readers.reset_caches()


@pytest.fixture
def isolated_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(conn, "_launchctl_print_text", lambda label: None)
    monkeypatch.setattr(
        conn,
        "_http_probe",
        lambda url, timeout=2.0: {"http_status": None, "body": "", "error": "mocked"},
    )
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    return tmp_path


class TestSnapshotIntegration:
    def test_the_index_key_is_appended_last(self, isolated_snapshot):
        """NINETEEN, measured, not eighteen.

        `build_snapshot`'s own docstring heading says "Console v2 keys
        11..17", and it is stale: `unacked_signals` was later inserted as
        the 6th key, so main builds 18 and this appends the 19th. Counting
        the dict is the only honest way to assert this — the heading has
        been wrong for longer than anyone noticed.
        """
        snapshot = web.build_snapshot()
        assert list(snapshot.keys())[-1] == "open_threads_index"
        assert len(snapshot) == 19

    def test_bare_root_yields_the_absent_envelope_not_none(self, isolated_snapshot):
        """The one key that answers absence with content. Fails on unfixed
        code, where the key does not exist at all."""
        section = web.build_snapshot()["open_threads_index"]
        assert section is not None
        assert section["status"] == "absent"

    def test_a_malformed_catalog_does_not_take_the_snapshot_down(self, isolated_snapshot):
        _write_index(isolated_snapshot, "{ this is not json")
        snapshot = web.build_snapshot()
        assert snapshot["open_threads_index"]["status"] == "absent"
        # The other 17 keep rendering — that is the actual requirement.
        assert len(snapshot) == 19
        assert isinstance(snapshot["feed"], list)
        assert isinstance(snapshot["unacked_honks"], int)

    def test_a_raising_reader_yields_null_for_this_key_alone(self, monkeypatch, isolated_snapshot):
        """`_safe_section` still wraps this reader. The envelope is the
        FORESEEN absence; an unforeseen raise is still null, which is why
        the renderer has a null branch as well as an absent branch."""

        def boom():
            raise RuntimeError("index reader exploded")

        monkeypatch.setattr(readers, "read_open_threads_index", boom)
        snapshot = web.build_snapshot()
        assert snapshot["open_threads_index"] is None
        assert len(snapshot) == 19

    def test_populated_root_reaches_the_snapshot(self, isolated_snapshot):
        _write_index(isolated_snapshot, _fixture_payload())
        section = web.build_snapshot()["open_threads_index"]
        assert section["status"] == "present"
        assert section["open_count"] == 6
        assert section["never_touched"] == 5


# ── The panel itself ────────────────────────────────────────────────────────


class TestPanelAssets:
    @pytest.fixture
    def js(self):
        return (STATIC_DIR / "app.js").read_text()

    def test_panel_shell_exists(self):
        html = (STATIC_DIR / "index.html").read_text()
        assert 'id="otindex-body"' in html
        assert 'id="otindex-note"' in html
        assert "OPEN-THREADS INDEX" in html

    def test_renderer_is_wired_into_the_poll(self, js):
        assert "function renderOpenThreadsIndex" in js
        assert "renderOpenThreadsIndex(snapshot);" in js

    def test_the_renderer_branches_on_all_three_shapes(self, js):
        """null, absent, present. Two branches would leave one failure mode
        rendering as the other."""
        body = js.split("function renderOpenThreadsIndex", 1)[1].split("\nfunction ", 1)[0]
        assert "panelUnavailable(" in body, "missing the null branch"
        assert "!== 'present'" in body, "missing the absent branch"
        assert "section.reason" in body, "the absent branch must show the server's reason"
        assert "last_seen_age_seconds" in body, "the absent branch must show the last-seen age"

    def test_truncated_lists_state_their_denominator(self, js):
        """15 categories do not fit the column. A top-5 that does not say
        "5 of 15" reads as the whole taxonomy — the read-side fail-open."""
        body = js.split("function otiBreakdown", 1)[1].split("\nfunction ", 1)[0]
        assert "of ${total}" in body

    def test_the_panel_names_the_cluster_method(self, js):
        body = js.split("function renderOpenThreadsIndex", 1)[1].split("\nfunction ", 1)[0]
        assert "cluster_method" in body

    def test_no_hardcoded_counts_in_the_panel(self, js):
        """No demo data, ever. Every number the panel prints must come from
        the snapshot — the v2 prototype's demo-seeded panels are the reason
        this whole console has a no-simulated-data rule."""
        body = js.split("function renderOpenThreadsIndex", 1)[1].split("\nfunction ", 1)[0]
        for seeded in ("191", "176", "177"):
            assert seeded not in body, f"panel carries a hardcoded value: {seeded}"
        # And the display path must render a MISSING value as an em dash,
        # never coerce it to 0. `|| 0` is fine for a bar width; in a printed
        # number it is the fabricated zero this panel exists to avoid.
        assert "== null ? '—'" in body

    def test_panel_styles_exist(self):
        css = (STATIC_DIR / "style.css").read_text()
        for cls in (".otindex-body", ".oti-stat-num", ".oti-absent-why", ".oti-group-count"):
            assert cls in css, f"missing style: {cls}"
