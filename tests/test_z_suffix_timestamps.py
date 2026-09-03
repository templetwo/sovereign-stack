"""
The `Z` spelling must PARSE and must be STORED VERBATIM.

THE DEFECT. `datetime.fromisoformat` did not accept a trailing `Z` before
CPython 3.11. Two validators parsed caller-supplied ISO strings with a bare
`fromisoformat` — `memory._validate_original_timestamp` and the `occurred_at`
check in `ground.record_catch` — so on 3.10 both REFUSED
`2026-06-19T05:18:19Z`. That is the spelling `post_fix_tools._iso` emits, and
the live store already holds specimens of it. The house's own timestamp format
was rejected by the house's own validators, on the interpreter the package
declares support for.

WHY THIS IS A DEFECT EVEN THOUGH IT REFUSES RATHER THAN DROPS. The refusal is
loud — no fail-open here — but it refuses a CORRECT value, and the caller's
only recourse is to rewrite a timestamp they were right about. `post_fix_tools`
had carried the two-line fix since it was written; the two validators never got
it. SOP #12: the fix was already in the tree, one wire short.

THE HALF THAT MATTERS MORE THAN THE PARSE. Both contracts say the value is
returned/stored VERBATIM, and `timestamp` is inside `derive_claim_id`'s
preimage — normalizing `Z` to `+00:00` on the way in would silently change the
claim id every backdated entry receives, which is the one thing a validator
must never do. So the swap happens on a COPY used only for the bounds check.
Every test below asserts the stored bytes, not just that the call succeeded.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack import ground, provenance
from sovereign_stack.memory import ExperientialMemory

Z_FORM = "2026-06-19T05:18:19Z"
OFFSET_FORM = "2026-06-19T05:18:19+00:00"


class Py310Datetime(datetime):
    """`datetime` with CPython 3.10's `fromisoformat`, which refuses `Z`.

    WITHOUT THIS THE WHOLE FILE IS DECORATION ON THE INTERPRETER IT RUNS ON.
    3.11 taught `fromisoformat` the `Z` spelling, so on the 3.12 venv every
    assertion below passes on the UNFIXED code — the validator's bare
    `fromisoformat` would accept `Z` by itself and nothing would be proven. The
    defect only exists on 3.10, which `pyproject.toml` declares support for
    (`requires-python = ">=3.10"`) and which no test here can run under,
    because the package's own imports need dependencies the 3.10 interpreter on
    this machine does not have.

    So the parser is what gets substituted, not the interpreter: this class
    reproduces 3.10's exact refusal, and the tests marked `_under_py310_parser`
    go RED against a bare `fromisoformat` on any interpreter. That is the gate
    being shown to fail (experimental law #2) rather than asserted to hold.
    """

    @classmethod
    def fromisoformat(cls, s):  # type: ignore[override]
        if isinstance(s, str) and s.endswith("Z"):
            raise ValueError(f"Invalid isoformat string: {s!r}")
        return datetime.fromisoformat(s)


def _mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(str(tmp_path / ".sovereign"))


def _last_entry(path: str) -> dict:
    lines = [ln for ln in Path(path).read_text().splitlines() if ln.strip()]
    return json.loads(lines[-1])


class TestTheParseHelper:
    def test_a_trailing_Z_becomes_an_offset(self):
        assert provenance.iso_parseable(Z_FORM) == OFFSET_FORM

    def test_everything_else_is_returned_untouched(self):
        for s in (OFFSET_FORM, "2026-06-19", "2026-06-19T05:18:19", "not-a-date", ""):
            assert provenance.iso_parseable(s) == s

    def test_a_lowercase_z_is_NOT_touched(self):
        """Mirrors post_fix_tools exactly. Widening the rule here would be a
        different change, made silently, in a helper whose whole job is to be
        boring."""
        assert provenance.iso_parseable("2026-06-19T05:18:19z") == "2026-06-19T05:18:19z"

    def test_an_interior_Z_is_not_touched(self):
        assert provenance.iso_parseable("Z2026-06-19") == "Z2026-06-19"


class TestRecordInsightAcceptsZ:
    def test_it_is_accepted(self, tmp_path):
        entry = _last_entry(_mem(tmp_path).record_insight("d", "c", original_timestamp=Z_FORM))
        assert entry["timestamp"] == Z_FORM

    def test_it_is_stored_verbatim_not_normalized(self, tmp_path):
        """THE CONTRACT. `timestamp` is in the claim_id preimage, so rewriting
        `Z` to `+00:00` here would change the id the entry is addressed by."""
        entry = _last_entry(_mem(tmp_path).record_insight("d", "c", original_timestamp=Z_FORM))
        assert entry["timestamp"] == Z_FORM
        assert entry["timestamp"] != OFFSET_FORM
        assert "+00:00" not in entry["timestamp"]

    def test_the_claim_id_is_the_one_the_Z_string_derives(self, tmp_path):
        path = _mem(tmp_path).record_insight("d", "c", original_timestamp=Z_FORM)
        entry = _last_entry(path)
        assert provenance.derive_claim_id(entry) == provenance.derive_claim_id(
            {"timestamp": Z_FORM, "domain": "d", "content": "c"}
        )

    def test_the_bounds_checks_still_see_through_the_Z(self, tmp_path):
        """The parse is not a bypass: a Z-suffixed value below the floor must
        still be refused, or the normalization would have opened a hole."""
        mem = _mem(tmp_path)
        with pytest.raises(ValueError, match="before 2024-01-01"):
            mem.record_insight("d", "c", original_timestamp="1999-01-01T00:00:00Z")

    def test_a_far_future_Z_value_is_still_refused(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        ahead = datetime.now(timezone.utc) + timedelta(days=2)
        z = ahead.strftime("%Y-%m-%dT%H:%M:%SZ")
        with pytest.raises(ValueError, match="in the future"):
            _mem(tmp_path).record_insight("d", "c", original_timestamp=z)

    @pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45Z", "Z", "19/06/2026Z"])
    def test_garbage_wearing_a_Z_is_still_refused(self, tmp_path, bad):
        with pytest.raises(ValueError, match="original_timestamp"):
            _mem(tmp_path).record_insight("d", "c", original_timestamp=bad)


class TestTheSiblingWritePathsAcceptZ:
    def test_open_thread(self, tmp_path):
        path = _mem(tmp_path).record_open_thread("q", "ctx", "d", original_timestamp=Z_FORM)
        assert _last_entry(path)["timestamp"] == Z_FORM

    def test_learning(self, tmp_path):
        path = _mem(tmp_path).record_learning("applies", "lesson", original_timestamp=Z_FORM)
        assert _last_entry(path)["timestamp"] == Z_FORM


class TestRecordCatchAcceptsZ:
    """`ground.record_catch` returns its refusals as a STRING. A test that only
    checked "no exception" would pass on a rejected write, so every assertion
    here reads the stored row."""

    @staticmethod
    def _catch(root: Path, occurred_at: str) -> str:
        return ground.record_catch(
            caught="a stale figure",
            caught_by="the-instrument",
            direction="instrument",
            occurred_at=occurred_at,
            would_have_cost="a wrong number in a filing",
            actual_cost="none",
            anthony_present="absent",
            content="The auditor caught a count nobody re-measured.",
            vantage="human_attestation",
            chronicle_root=str(root),
        )

    def test_a_Z_suffixed_occurred_at_is_accepted(self, tmp_path):
        root = tmp_path / ".sovereign"
        out = self._catch(root, Z_FORM)
        assert out.startswith("⚓"), out

    def test_it_is_stored_verbatim(self, tmp_path):
        root = tmp_path / ".sovereign"
        self._catch(root, Z_FORM)
        d = root / "insights" / "the-ground,catch,instrument"
        rows = [
            json.loads(ln)
            for f in sorted(d.glob("*.jsonl"))
            for ln in f.read_text().splitlines()
            if ln.strip()
        ]
        assert len(rows) == 1
        assert rows[0]["occurred_at"] == Z_FORM

    def test_a_genuinely_bad_occurred_at_is_still_refused(self, tmp_path):
        """Law #2: the gate is shown to accept AND to refuse."""
        out = self._catch(tmp_path / ".sovereign", "the day before yesterday")
        assert out.startswith("record_catch rejected:"), out
        assert "occurred_at" in out

    def test_the_date_only_form_still_works(self, tmp_path):
        """The commonest live spelling must not regress."""
        assert self._catch(tmp_path / ".sovereign", "2026-08-14").startswith("⚓")


class TestUnderThe310Parser:
    """The only tests in this file that can fail on unfixed code.

    Each substitutes `Py310Datetime` for the module-level `datetime` name the
    validator actually calls, so the assertion is about the validator's
    routing, not about which CPython the suite happens to run on.
    """

    def test_the_310_parser_really_does_refuse_Z(self):
        """Positive control on the substitute itself. A simulated failure mode
        that cannot fail is the same decoration this class exists to avoid."""
        with pytest.raises(ValueError, match="Invalid isoformat"):
            Py310Datetime.fromisoformat(Z_FORM)
        assert Py310Datetime.fromisoformat(OFFSET_FORM).year == 2026

    def test_record_insight_accepts_Z_under_the_310_parser(self, tmp_path, monkeypatch):
        from sovereign_stack import memory as memory_mod

        monkeypatch.setattr(memory_mod, "datetime", Py310Datetime)
        entry = _last_entry(_mem(tmp_path).record_insight("d", "c", original_timestamp=Z_FORM))
        assert entry["timestamp"] == Z_FORM

    def test_record_catch_accepts_Z_under_the_310_parser(self, tmp_path, monkeypatch):
        from sovereign_stack import ground as ground_mod

        monkeypatch.setattr(ground_mod, "datetime", Py310Datetime)
        out = TestRecordCatchAcceptsZ._catch(tmp_path / ".sovereign", Z_FORM)
        assert out.startswith("⚓"), out

    def test_the_310_parser_still_refuses_real_garbage(self, tmp_path, monkeypatch):
        from sovereign_stack import memory as memory_mod

        monkeypatch.setattr(memory_mod, "datetime", Py310Datetime)
        with pytest.raises(ValueError, match="original_timestamp"):
            _mem(tmp_path).record_insight("d", "c", original_timestamp="nonsenseZ")


class TestTheREADERSideUnderThe310Parser:
    """The validators were taught the `Z` spelling; `memory._parse_iso` — the
    READ side — was not, and it is the same one-call fix.

    WHAT IT COSTS ON 3.10. `_parse_iso` feeds `_dedup_hit` and the two
    thread-id backfills. The live store holds 176 rows whose `timestamp` ends
    in `Z`, all under `insights/temple-vault-import,…`, so on 3.10 every one of
    them parsed to None: the retry-dedup probe declined silently on exactly the
    imported corpus (the safe direction, but a guard that is off), and the
    thread-id backfills fell through to `datetime.now()` — which means a legacy
    Z-stamped thread got a DIFFERENT derived thread_id on every call, since the
    id is derived from the timestamp handed in.

    SUBSTITUTING THE PARSER, NOT THE INTERPRETER, for the reason this file's
    Py310Datetime docstring already gives: on the 3.12 venv a bare
    `fromisoformat` accepts `Z` by itself and every assertion here would pass on
    unfixed code.
    """

    @staticmethod
    def _seed(tmp_path: Path, row: dict, session: str = "session_seeded") -> Path:
        d = tmp_path / ".sovereign" / "insights" / "dom"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{session}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        return path

    @staticmethod
    def _rows(tmp_path: Path) -> list[dict]:
        d = tmp_path / ".sovereign" / "insights" / "dom"
        return [
            json.loads(ln)
            for f in sorted(d.glob("*.jsonl"))
            for ln in f.read_text().splitlines()
            if ln.strip()
        ]

    def test_parse_iso_reads_Z_under_the_310_parser(self, monkeypatch):
        from sovereign_stack import memory as memory_mod

        monkeypatch.setattr(memory_mod, "datetime", Py310Datetime)
        parsed = memory_mod._parse_iso(Z_FORM)
        assert parsed is not None, "a Z-spelled timestamp read as unparseable"
        assert parsed == memory_mod._parse_iso(OFFSET_FORM)

    def test_the_dedup_probe_fires_on_a_Z_stamped_row_under_the_310_parser(
        self, tmp_path, monkeypatch
    ):
        from sovereign_stack import memory as memory_mod

        now = datetime.now(timezone.utc)
        self._seed(
            tmp_path,
            {
                "timestamp": now.isoformat().replace("+00:00", "Z"),
                "domain": "dom",
                "content": "c",
                "layer": "hypothesis",
            },
        )
        monkeypatch.setattr(memory_mod, "datetime", Py310Datetime)
        _mem(tmp_path).record_insight("dom", "c", session_id="session_seeded")
        assert len(self._rows(tmp_path)) == 1, (
            "the Z-stamped previous write parsed to None, so dedup declined"
        )

    def test_it_can_still_NOT_fire(self, tmp_path, monkeypatch):
        """Law #2. A Z-stamped write six hours old is a deliberate
        re-recording, not a retry, and must append."""
        from sovereign_stack import memory as memory_mod

        old = datetime.now(timezone.utc) - timedelta(hours=6)
        self._seed(
            tmp_path,
            {
                "timestamp": old.isoformat().replace("+00:00", "Z"),
                "domain": "dom",
                "content": "c",
                "layer": "hypothesis",
            },
        )
        monkeypatch.setattr(memory_mod, "datetime", Py310Datetime)
        _mem(tmp_path).record_insight("dom", "c", session_id="session_seeded")
        assert len(self._rows(tmp_path)) == 2

    def test_real_garbage_still_reads_as_None(self, monkeypatch):
        """The normalization is a trailing capital Z and nothing else — a
        reader that started accepting anything would be a different change."""
        from sovereign_stack import memory as memory_mod

        monkeypatch.setattr(memory_mod, "datetime", Py310Datetime)
        assert memory_mod._parse_iso("who knows") is None
        assert memory_mod._parse_iso("2026-06-19T05:18:19z") is None
        assert memory_mod._parse_iso(None) is None
