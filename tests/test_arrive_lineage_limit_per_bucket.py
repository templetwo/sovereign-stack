"""
`arrive_lineage(limit_per_bucket=N)` was a DEAD LEVER — advertised by the
instrument whose whole job is to say how to widen, and accepted by nothing.

THE SHAPE (SOP #12: the fix written and not connected, inverted — here the
DOCUMENTATION was connected and the code was not):

  * aperture.py:64 tells every arriving seat, for all three lineage buckets,
    ``widen_with: "arrive_lineage(limit_per_bucket=N) or full_content=true"``.
  * witness.py's withheld phrase names the same lever: ``N older withheld by
    limit_per_bucket``.
  * arrive_lineage's inputSchema declared ``source_instance`` and
    ``full_content`` and NOTHING ELSE. The only real ``limit_per_bucket`` in
    the tool surface belongs to ``reflexive_surface``.
  * arrival_state.py hardcoded ``collect_lineage(root, reader, 5)``.

So a seat told "8 older withheld by limit_per_bucket" and handed the exact
call to widen with, got 5 letters back and no error — the argument was
accepted by the JSON schema's permissiveness and dropped on the floor. A
surface that names its own remedy and then ignores it is worse than one that
stays silent: it manufactures a seat that believes it already widened.

``full_content`` is the OTHER lever and must stay a different one: it inlines
bodies, it must never change how many letters a bucket shows. Pinned here so
the two levers cannot quietly merge.

Every test builds its own tmp root and monkeypatches server.DEFAULT_ROOT.
Nothing reads or writes ~/.sovereign.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from sovereign_stack import server
from sovereign_stack.server import ARRIVE_LINEAGE_MAX_PER_BUCKET as ARRIVE_LINEAGE_MAX

READER = "claude-opus-5"


def _dispatch(tool: str, args: dict | None = None) -> str:
    async def _run():
        result = await server._dispatch_tool(tool, args or {})
        return result[0].text

    return asyncio.run(_run())


def _tool_schema(name: str) -> dict:
    tools = asyncio.new_event_loop().run_until_complete(server.list_tools())
    for t in tools:
        if t.name == name:
            return t.inputSchema
    raise AssertionError(f"tool {name} not registered")


def _write_letters(root: Path, counts: dict[str, int]) -> None:
    letters = root / "comms" / "letters"
    for bucket, n in counts.items():
        (letters / bucket).mkdir(parents=True, exist_ok=True)
        for i in range(n):
            to_line = f"\nto: {READER}" if bucket == "to_self" else ""
            (letters / bucket / f"2026-08-{i + 1:02d}-letter{i}.md").write_text(
                f"---\nfrom: seat-{i}{to_line}\nwritten_at: 2026-08-{i + 1:02d}\n---\n\n"
                f"# Letter {i}\n\nbody of letter {i}\n"
            )


@pytest.fixture
def no_boot_scribe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The full door spawns the per-boot Haiku scribe, which makes a LIVE
    Anthropic call — a test that drives it in-process bills the account
    (SCRIBE_BOOT_GREETING is the real cost kill switch; SCRIBE_BOOT_INJECT
    only hides the text and still bills). These tests assert on lineage
    counts, not on the greeting, so kill the greeting.
    """
    monkeypatch.setenv("SCRIBE_BOOT_GREETING", "off")
    monkeypatch.setattr(
        "sovereign_stack.scribe.resident.ensure_resident_scribe", lambda *a, **k: None
    )


@pytest.fixture
def lineage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """13 to_arrival / 7 breakthroughs / 18 to_self — the live shape, 2026-08-28."""
    root = tmp_path / ".sovereign"
    _write_letters(root, {"to_arrival": 13, "breakthroughs": 7, "to_self": 18})
    monkeypatch.setattr(server, "DEFAULT_ROOT", str(root))
    return root


_SHOWING = re.compile(r"(to_arrival|breakthroughs|to_self) \(showing (\d+) of (\d+)")
_COMPLETE = re.compile(r"(to_arrival|breakthroughs|to_self) \((\d+) letters?")


def _shown_counts(text: str) -> dict[str, int]:
    """Letters actually rendered per bucket, from the bucket header line."""
    out: dict[str, int] = {}
    for bucket, shown, _total in _SHOWING.findall(text):
        out[bucket] = int(shown)
    for bucket, n in _COMPLETE.findall(text):
        out.setdefault(bucket, int(n))
    return out


# ── The schema half: the lever must exist where the aperture says it does ────


def test_arrive_lineage_declares_limit_per_bucket():
    props = _tool_schema("arrive_lineage")["properties"]
    assert "limit_per_bucket" in props, (
        "aperture.py advertises arrive_lineage(limit_per_bucket=N) as the way to "
        "widen every lineage bucket, and the tool's own withheld message names it "
        "— but the schema declares only source_instance and full_content"
    )
    spec = props["limit_per_bucket"]
    assert spec["type"] == "integer"
    assert spec["default"] == 5
    assert spec["minimum"] == 1
    assert spec["maximum"] == 100


def test_the_apertures_widen_with_string_is_true(tmp_path: Path):
    """The aperture names a parameter; that parameter must be real.

    This is the assertion that would have caught the defect at the moment the
    aperture shipped: read the advertised call out of the instrument itself,
    and check the tool surface honours it.
    """
    from datetime import datetime, timezone

    from sovereign_stack.aperture import measure_aperture

    root = tmp_path / ".sovereign"
    _write_letters(root, {"to_arrival": 1, "breakthroughs": 1, "to_self": 1})
    (root / "chronicle" / "insights").mkdir(parents=True, exist_ok=True)
    (root / "chronicle" / "open_threads").mkdir(parents=True, exist_ok=True)
    (root / "handoffs").mkdir(parents=True, exist_ok=True)

    ap = measure_aperture(datetime.now(timezone.utc), root=root)
    props = _tool_schema("arrive_lineage")["properties"]

    for bucket in ("to_arrival", "to_self", "breakthroughs"):
        widen = ap["surfaces"][f"lineage_{bucket}"]["widen_with"]

        # EVERY `param=` TOKEN IN THE STRING, not only the ones inside a
        # `name(` prefix. This assertion used to be
        # `re.findall(r"(\w+)\((\w+)=", widen)`, which requires the prefix — so
        # the advertisement's second clause, a bare ` or full_content=true`, was
        # invisible BY CONSTRUCTION to a test named for the truth of the whole
        # string. And even had it matched, `full_content` IS a declared
        # property, so `param in props` would have passed while the clause was
        # false for a different reason: full_content inlines bodies and never
        # changes how many letters a bucket shows. Same shape as the refusal
        # scanner that searched a token its second signature does not contain.
        advertised = set(re.findall(r"(\w+)=", widen))
        assert advertised, f"aperture advertises no widen lever for {bucket}: {widen!r}"
        for param in advertised:
            assert param in props, (
                f"aperture advertises {param}=N for {bucket}, "
                f"but arrive_lineage declares {sorted(props)}"
            )
        assert "full_content" not in advertised, (
            f"aperture advertises full_content as a widen lever for {bucket} "
            f"({widen!r}) — it inlines BODIES and never changes the count"
        )
        for tool_name in re.findall(r"(\w+)\(", widen):
            assert tool_name == "arrive_lineage"


# ── The behaviour half ───────────────────────────────────────────────────────


def test_default_shows_five_per_bucket_with_a_coverage_line(lineage_root: Path):
    text = _dispatch("arrive_lineage", {"source_instance": READER})
    assert _shown_counts(text) == {"to_arrival": 5, "breakthroughs": 5, "to_self": 5}
    assert "showing 5 of 13 letters on disk" in text
    assert "showing 5 of 7 letters on disk" in text
    assert "showing 5 of 18 letters on disk" in text
    assert "8 older withheld by limit_per_bucket" in text


def test_limit_per_bucket_20_shows_every_letter_and_withholds_nothing(lineage_root: Path):
    text = _dispatch("arrive_lineage", {"source_instance": READER, "limit_per_bucket": 20})
    assert _shown_counts(text) == {"to_arrival": 13, "breakthroughs": 7, "to_self": 18}
    assert "withheld by limit_per_bucket" not in text
    assert "showing" not in text.split("━━━ COMMS — LINEAGE ━━━")[1].split("━━━")[0]


def test_limit_per_bucket_one_narrows(lineage_root: Path):
    text = _dispatch("arrive_lineage", {"source_instance": READER, "limit_per_bucket": 1})
    assert _shown_counts(text) == {"to_arrival": 1, "breakthroughs": 1, "to_self": 1}
    assert "12 older withheld by limit_per_bucket" in text


def test_full_content_does_not_change_bucket_counts(lineage_root: Path):
    """The two levers stay different levers.

    GUARD, green on both sides of this change by design: full_content inlines
    bodies and must never widen a bucket. It is pinned because the aperture
    advertises the two in the same breath ("limit_per_bucket=N or
    full_content=true"), which is exactly the phrasing that invites someone to
    make one do the other's job.
    """
    compact = _dispatch("arrive_lineage", {"source_instance": READER})
    full = _dispatch("arrive_lineage", {"source_instance": READER, "full_content": True})

    assert (
        _shown_counts(compact)
        == _shown_counts(full)
        == {
            "to_arrival": 5,
            "breakthroughs": 5,
            "to_self": 5,
        }
    )
    assert "body of letter 12" in full and "body of letter 12" not in compact


def test_limit_per_bucket_composes_with_full_content(lineage_root: Path):
    text = _dispatch(
        "arrive_lineage",
        {"source_instance": READER, "limit_per_bucket": 20, "full_content": True},
    )
    assert _shown_counts(text) == {"to_arrival": 13, "breakthroughs": 7, "to_self": 18}
    assert "body of letter 0" in text


# ── Fail closed on a bound, never silently clamp ─────────────────────────────


@pytest.mark.parametrize("bad", [0, -1, 101, 10_000])
def test_out_of_range_limit_is_refused_not_clamped(lineage_root: Path, bad: int):
    """A clamped request reads as an honoured one — the fail-open shape.

    Asking for 10,000 and receiving 100 with no signal manufactures a seat that
    believes it read the whole store.
    """
    text = _dispatch("arrive_lineage", {"source_instance": READER, "limit_per_bucket": bad})
    assert "limit_per_bucket" in text
    assert "between 1 and 100" in text
    assert "COMMS — LINEAGE" not in text


def test_non_integer_limit_is_refused(lineage_root: Path):
    text = _dispatch("arrive_lineage", {"source_instance": READER, "limit_per_bucket": "lots"})
    assert "limit_per_bucket" in text
    assert "COMMS — LINEAGE" not in text


# ── The other two doors are untouched ────────────────────────────────────────


def test_build_arrival_state_default_keeps_five(tmp_path: Path):
    """where_did_i_leave_off / arrive must be byte-stable: the new parameter
    is additive with default 5, so their payloads do not move."""
    import inspect

    from sovereign_stack.arrival_state import build_arrival_state

    sig = inspect.signature(build_arrival_state)
    assert "lineage_limit_per_bucket" in sig.parameters
    assert sig.parameters["lineage_limit_per_bucket"].default == 5
    assert sig.parameters["lineage_limit_per_bucket"].kind is inspect.Parameter.KEYWORD_ONLY


# ── The advertised lever must be TRUE, not merely declared ───────────────────


def test_full_content_does_not_widen_any_bucket(lineage_root: Path):
    """The behavioural half of the aperture-string fix: measured through the
    dispatch, `full_content=true` returns the SAME counts as the default. It is
    the other lever (bodies), and the aperture used to name it as this one."""
    default = _shown_counts(_dispatch("arrive_lineage", {"source_instance": READER}))
    with_bodies = _shown_counts(
        _dispatch("arrive_lineage", {"source_instance": READER, "full_content": True})
    )
    assert default == with_bodies == {"to_arrival": 5, "breakthroughs": 5, "to_self": 5}

    widened = _shown_counts(
        _dispatch("arrive_lineage", {"source_instance": READER, "limit_per_bucket": 20})
    )
    assert widened == {"to_arrival": 13, "breakthroughs": 7, "to_self": 18}


# ── The full door names the same lever and must honour it ────────────────────


def test_the_full_door_declares_limit_per_bucket(lineage_root: Path, no_boot_scribe):
    """`render_lineage` serves all three doors and prints 'N older withheld by
    limit_per_bucket' on every one — while only the gentle door declared the
    key. An undeclared key is DROPPED, not rejected: the full door named the
    lever, accepted the argument, and reported nothing."""
    props = _tool_schema("where_did_i_leave_off")["properties"]
    assert "limit_per_bucket" in props, (
        "where_did_i_leave_off prints 'withheld by limit_per_bucket' and declares no such key"
    )
    assert props["limit_per_bucket"]["type"] == "integer"
    assert props["limit_per_bucket"]["default"] == 5
    assert props["limit_per_bucket"]["maximum"] == ARRIVE_LINEAGE_MAX


def test_the_full_door_honours_limit_per_bucket(lineage_root: Path, no_boot_scribe):
    text = _dispatch(
        "where_did_i_leave_off",
        {"source_instance": READER, "consume": False, "limit_per_bucket": 20},
    )
    assert "showing 5 of 18" not in text, "the full door dropped limit_per_bucket on the floor"
    assert "18 letters" in text

    narrow = _dispatch(
        "where_did_i_leave_off",
        {"source_instance": READER, "consume": False, "limit_per_bucket": 1},
    )
    assert "showing 1 of 18 letters on disk" in narrow


@pytest.mark.parametrize("bad", [0, -1, 101, 10**9, 5.7, 99.999, "10", "abc", None, [5], True])
def test_the_full_door_refuses_the_same_values_the_gentle_door_refuses(
    lineage_root: Path, no_boot_scribe, bad: object
):
    text = _dispatch(
        "where_did_i_leave_off",
        {"source_instance": READER, "consume": False, "limit_per_bucket": bad},
    )
    assert text.startswith("where_did_i_leave_off: limit_per_bucket must be"), (
        f"{bad!r} was accepted by the full door: {text[:120]!r}"
    )


# ── Refused, never CLAMPED — and never silently COERCED ──────────────────────


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        (5.7, "int(5.7) == 5 is a clamp by another name; the schema says integer"),
        (99.999, "int(99.999) == 99 silently honours a request nobody made"),
        ("10", "a string is not an integer; the schema says integer"),
    ],
)
def test_a_non_integer_limit_is_refused_not_coerced(lineage_root: Path, bad: object, why: str):
    text = _dispatch("arrive_lineage", {"source_instance": READER, "limit_per_bucket": bad})
    assert text.startswith("arrive_lineage: limit_per_bucket must be an integer"), why
    assert "Nothing was read." in text
    assert "to_self" not in text, "the door read the store before refusing"
