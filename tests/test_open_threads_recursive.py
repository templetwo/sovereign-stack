"""
The open_threads store has NESTED shards, and three of the five walkers over it
were flat globs — so the same store had two different sizes depending on which
code asked.

LIVE SPECIMEN (2026-08-28, ~/.sovereign): chronicle/open_threads/ holds 149 flat
*.jsonl plus ONE nested shard,
``tech-debt,compaction,auto-detection/log.jsonl``. ``get_open_threads`` misses it
at ANY limit — not a cap, a walk — and the heartbeat aperture reported
``open_threads.on_disk = 255`` against 256 records on disk. An off-by-one that no
`limit=9999` can widen, because the file was never enumerated.

This is SOP #10's shape, not SOP #2's: the bytes were never lost, the ADDRESS was
unreachable. And it has an addressing half that a bare ``rglob`` does NOT fix —
the nested shard's domain is its DIRECTORY name
(``tech-debt,compaction,auto-detection``), while its file stem is ``log``. A
recursive walk that keeps deriving the domain from the stem makes the shard
countable but still unaddressable: ``get_open_threads(domain="tech-debt")``
would return nothing while the record sits right there. Both halves are tested.

Every test builds its own tmp root. Nothing reads or writes ~/.sovereign.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

NESTED_DOMAIN = "tech-debt,compaction,auto-detection"


def _thread_record(question: str, domain: str, *, resolved: bool = False) -> str:
    return json.dumps(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "thread_id": f"t_{abs(hash(question)) % 10**12:012d}",
            "question": question,
            "context": "fixture",
            "domain": domain,
            "session_id": "session_test",
            "layer": "open_thread",
            "resolved": resolved,
        }
    )


@pytest.fixture
def chronicle_with_nested_shard(tmp_sovereign_root: Path) -> Path:
    """A chronicle whose open_threads store has flat shards AND one nested one."""
    threads = tmp_sovereign_root / "chronicle" / "open_threads"
    threads.mkdir(parents=True, exist_ok=True)

    (threads / "architecture.jsonl").write_text(
        _thread_record("flat question one", "architecture") + "\n"
    )
    (threads / "provenance.jsonl").write_text(
        _thread_record("flat question two", "provenance") + "\n"
    )

    nested = threads / NESTED_DOMAIN
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "log.jsonl").write_text(
        _thread_record("the nested question nobody could reach", NESTED_DOMAIN) + "\n"
    )

    return tmp_sovereign_root


# ── get_open_threads (memory.py) ─────────────────────────────────────────────


def test_get_open_threads_enumerates_the_nested_shard(chronicle_with_nested_shard: Path):
    """The count half: a nested shard is part of the store, at any limit."""
    from sovereign_stack.memory import ExperientialMemory

    memory = ExperientialMemory(root=str(chronicle_with_nested_shard / "chronicle"))
    questions = {t["question"] for t in memory.get_open_threads(limit=9999)}

    assert "the nested question nobody could reach" in questions, (
        "nested open-threads shard was not enumerated — the walk is flat, so no limit can widen it"
    )
    assert len(questions) == 3


def test_get_open_threads_addresses_the_nested_shard_by_its_directory_domain(
    chronicle_with_nested_shard: Path,
):
    """The address half: the nested shard's domain is its DIRECTORY, not 'log'.

    A recursive walk alone leaves this broken — the shard becomes countable and
    stays unaddressable.
    """
    from sovereign_stack.memory import ExperientialMemory

    memory = ExperientialMemory(root=str(chronicle_with_nested_shard / "chronicle"))

    hit = memory.get_open_threads(domain="tech-debt", limit=9999)
    assert [t["question"] for t in hit] == ["the nested question nobody could reach"]

    # ...and the file stem must NOT become an addressable domain.
    assert memory.get_open_threads(domain="log", limit=9999) == []


def test_get_open_threads_domain_contains_reaches_nested_shard(
    chronicle_with_nested_shard: Path,
):
    from sovereign_stack.memory import ExperientialMemory

    memory = ExperientialMemory(root=str(chronicle_with_nested_shard / "chronicle"))
    hit = memory.get_open_threads(domain_contains="compaction", limit=9999)
    assert [t["question"] for t in hit] == ["the nested question nobody could reach"]


def test_flat_only_store_is_unchanged(tmp_sovereign_root: Path):
    """Byte-stability guard: with no nested shard, behaviour is exactly as before."""
    from sovereign_stack.memory import ExperientialMemory

    threads = tmp_sovereign_root / "chronicle" / "open_threads"
    threads.mkdir(parents=True, exist_ok=True)
    (threads / "architecture.jsonl").write_text(_thread_record("only flat", "architecture") + "\n")

    memory = ExperientialMemory(root=str(tmp_sovereign_root / "chronicle"))
    assert [t["question"] for t in memory.get_open_threads(limit=9999)] == ["only flat"]
    assert [t["question"] for t in memory.get_open_threads(domain="architecture")] == ["only flat"]


# ── aperture.py ──────────────────────────────────────────────────────────────


def test_aperture_counts_the_nested_shard(chronicle_with_nested_shard: Path):
    """The heartbeat aperture said 255 when the store held 256."""
    from sovereign_stack.aperture import measure_aperture

    ap = measure_aperture(datetime.now(timezone.utc), root=chronicle_with_nested_shard)
    threads = ap["surfaces"]["open_threads"]

    assert threads["on_disk"] == 3, (
        "aperture under-counted the store by exactly the nested shard — "
        "the surface built to stop a projection passing as the corpus was "
        "itself projecting"
    )
    assert threads["unresolved"] == 3


def test_aperture_resolved_count_sees_nested_resolved_records(tmp_sovereign_root: Path):
    """`not_reachable.resolved_open_threads` is derived from the same walk."""
    from sovereign_stack.aperture import measure_aperture

    threads = tmp_sovereign_root / "chronicle" / "open_threads"
    nested = threads / NESTED_DOMAIN
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "log.jsonl").write_text(
        _thread_record("resolved nested", NESTED_DOMAIN, resolved=True) + "\n"
    )
    (tmp_sovereign_root / "comms" / "letters").mkdir(parents=True, exist_ok=True)
    for bucket in ("to_arrival", "to_self", "breakthroughs"):
        (tmp_sovereign_root / "comms" / "letters" / bucket).mkdir(parents=True, exist_ok=True)

    ap = measure_aperture(datetime.now(timezone.utc), root=tmp_sovereign_root)
    assert ap["surfaces"]["open_threads"]["on_disk"] == 1
    assert ap["not_reachable"]["resolved_open_threads"]["count"] == 1


# ── metabolism.py ────────────────────────────────────────────────────────────


def test_metabolism_load_all_threads_sees_the_nested_shard(
    chronicle_with_nested_shard: Path, monkeypatch: pytest.MonkeyPatch
):
    """The fourth walker. `_load_all_threads` feeds resonance matching."""
    from sovereign_stack import metabolism

    monkeypatch.setattr(
        metabolism, "CHRONICLE_DIR", chronicle_with_nested_shard / "chronicle", raising=True
    )
    questions = {t["question"] for t in metabolism._load_all_threads()}
    assert "the nested question nobody could reach" in questions
    assert len(questions) == 3


# ── dashboard.py — already recursive; pinned so it cannot regress ────────────


def test_dashboard_latest_entry_can_be_the_nested_shard(tmp_sovereign_root: Path):
    """dashboard.collect_latest_entries already walks recursively.

    No change was needed here — this test exists so the agreement between the
    walkers is asserted rather than assumed, and so a future 'simplification'
    back to a flat glob fails loudly.
    """
    from sovereign_stack.dashboard import collect_latest_entries

    threads = tmp_sovereign_root / "chronicle" / "open_threads"
    nested = threads / NESTED_DOMAIN
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "log.jsonl").write_text(
        _thread_record("the nested question nobody could reach", NESTED_DOMAIN) + "\n"
    )

    latest = collect_latest_entries(tmp_sovereign_root)
    assert latest["open_thread"] is not None
    assert latest["open_thread"]["preview"] == "the nested question nobody could reach"


# ── The three walkers the first pass MISSED ──────────────────────────────────
#
# The first audit grepped for the literal `open_threads"` and found five call
# sites. That grep could not see `self.threads_dir.glob(...)` (the path is bound
# in __init__) or `memory.threads_dir.glob(...)` in another module. Three more
# flat walkers survived it — and the worst of them is a WRITE path, where the
# consequence is not an undercount but a silent no-op.
#
# The instrument that found the defect had the same blind spot as the defect.


def test_resolve_thread_by_id_can_resolve_a_thread_in_a_nested_shard(
    chronicle_with_nested_shard: Path,
):
    """THE WRITE-PATH ONE, and the sharpest of the set.

    `resolve_thread_by_id` walked flat, so a thread in a nested shard was never
    visited: the function fell through, `resolved_domain` stayed None, and it
    returned "" — indistinguishable from "no such thread". A thread that exists,
    is addressable by a stable id, and CANNOT BE RESOLVED, reporting not-found.
    """
    from sovereign_stack.memory import ExperientialMemory

    memory = ExperientialMemory(root=str(chronicle_with_nested_shard / "chronicle"))
    nested = next(
        t
        for t in memory.get_open_threads(limit=9999)
        if t["question"] == "the nested question nobody could reach"
    )

    result = memory.resolve_thread_by_id(nested["thread_id"], "resolved in the nested shard")

    assert result != "", (
        "resolve_thread_by_id returned '' for a thread that exists — a flat walk "
        "over a store with nested shards reports not-found instead of resolving"
    )
    remaining = [t["question"] for t in memory.get_open_threads(limit=9999)]
    assert "the nested question nobody could reach" not in remaining


def test_resolve_thread_by_id_records_the_directory_domain_not_the_stem(
    chronicle_with_nested_shard: Path,
):
    """The resolving ground_truth insight must land under the thread's real
    domain. The fallback was `jsonl_file.stem`, which is 'log' for a nested
    shard — a resolution filed under a domain nobody would look in."""
    from sovereign_stack.memory import ExperientialMemory

    memory = ExperientialMemory(root=str(chronicle_with_nested_shard / "chronicle"))
    nested = next(
        t
        for t in memory.get_open_threads(limit=9999)
        if t["question"] == "the nested question nobody could reach"
    )
    # Strip the stored domain so the fallback is what gets exercised.
    shard = chronicle_with_nested_shard / "chronicle" / "open_threads" / NESTED_DOMAIN / "log.jsonl"
    record = json.loads(shard.read_text().strip())
    record.pop("domain", None)
    shard.write_text(json.dumps(record) + "\n")

    result = memory.resolve_thread_by_id(nested["thread_id"], "resolved")

    assert result != ""
    assert "/log/" not in result
    assert NESTED_DOMAIN in result


def test_seasons_all_threads_sees_the_nested_shard(chronicle_with_nested_shard: Path):
    """`_all_threads` feeds link_threads — the thread-family WRITE path. A
    nested thread could not be linked into a family: it was never in the dict."""
    from sovereign_stack.memory import ExperientialMemory
    from sovereign_stack.seasons import _all_threads

    memory = ExperientialMemory(root=str(chronicle_with_nested_shard / "chronicle"))
    questions = {t.get("question") for t in _all_threads(memory).values()}
    assert "the nested question nobody could reach" in questions
    assert len(questions) == 3


def test_seasons_load_threads_readonly_sees_the_nested_shard(
    chronicle_with_nested_shard: Path,
):
    """`_load_threads_readonly`'s own docstring says it "Mirrors
    get_open_threads' glob." It stopped mirroring the moment get_open_threads
    went recursive — so season_review would digest a smaller store than the boot
    door reports, with no signal either was partial."""
    from sovereign_stack.seasons import _load_threads_readonly

    threads = _load_threads_readonly(chronicle_with_nested_shard / "chronicle")
    questions = {t.get("question") for t in threads}
    assert "the nested question nobody could reach" in questions
    assert len(questions) == 3


# ── The other half of "recursive": a hidden backup dir is NOT the store ──────
#
# Going recursive without a filter trades an under-read for a corrupting
# over-read. `pathlib.rglob` descends into DOTTED directories, and this house
# migrates in place and leaves the old copy beside the new one under a dot name
# — ~/.sovereign/comms/letters/ carries `.pre-md-backup-20260609/` and
# `.pre-md-backup-20260610/` today, made by exactly that convention, and
# open_threads/ already holds a `…jsonl.bak.20260502` file that only the
# `*.jsonl` pattern keeps out. The day someone backs a shard up as
# `open_threads/.bak-20260502/`, a bare rglob serves a RETIRED thread as live
# under the invented domain `.bak-20260502` — and resolve_thread_by_id WRITES
# INTO it, stamping `resolved: true` into a file nobody meant to keep live.


@pytest.fixture
def chronicle_with_hidden_backup(tmp_sovereign_root: Path) -> Path:
    """A chronicle whose open_threads store has a live shard, a live NESTED
    shard, and a hidden `.bak-…/` directory holding a retired copy."""
    threads = tmp_sovereign_root / "chronicle" / "open_threads"
    threads.mkdir(parents=True, exist_ok=True)

    (threads / "architecture.jsonl").write_text(
        _thread_record("flat question one", "architecture") + "\n"
    )
    nested = threads / NESTED_DOMAIN
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "log.jsonl").write_text(
        _thread_record("the nested question nobody could reach", NESTED_DOMAIN) + "\n"
    )

    retired = threads / ".bak-20260502"
    retired.mkdir(parents=True, exist_ok=True)
    (retired / "old.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-02T00:00:00+00:00",
                "thread_id": "t_retired",
                "question": "a retired thread from a pre-migration backup",
                "context": "fixture",
                "domain": "temple-wars,next-move",
                "session_id": "session_old",
                "layer": "open_thread",
                "resolved": False,
            }
        )
        + "\n"
    )
    return tmp_sovereign_root


def test_get_open_threads_skips_a_hidden_backup_directory(
    chronicle_with_hidden_backup: Path,
):
    from sovereign_stack.memory import ExperientialMemory

    memory = ExperientialMemory(root=str(chronicle_with_hidden_backup / "chronicle"))
    threads = memory.get_open_threads(limit=9999)
    questions = {t["question"] for t in threads}

    assert "a retired thread from a pre-migration backup" not in questions, (
        "a hidden backup directory was folded into the live corpus — rglob descends into dot-dirs"
    )
    assert questions == {"flat question one", "the nested question nobody could reach"}
    assert ".bak-20260502" not in {t.get("domain") for t in threads}


def test_resolve_thread_by_id_does_not_write_into_a_hidden_backup_directory(
    chronicle_with_hidden_backup: Path,
):
    """The write half, and the reason this is not cosmetic: the walk that
    ENUMERATES the store is the same walk that RESOLVES into it. A retired file
    reachable by the reader is a retired file the writer will stamp."""
    from sovereign_stack.memory import ExperientialMemory

    shard = (
        chronicle_with_hidden_backup / "chronicle" / "open_threads" / ".bak-20260502" / "old.jsonl"
    )
    before = shard.read_text()

    memory = ExperientialMemory(root=str(chronicle_with_hidden_backup / "chronicle"))
    result = memory.resolve_thread_by_id("t_retired", "resolved from the backup")

    assert result == "", "a thread inside a hidden backup dir must read as not-found"
    assert shard.read_text() == before, "a retired backup file was rewritten in place"
    assert json.loads(before)["resolved"] is False


def test_a_dotted_shard_FILE_is_excluded_on_the_same_rule(tmp_sovereign_root: Path):
    from sovereign_stack.memory import ExperientialMemory

    threads = tmp_sovereign_root / "chronicle" / "open_threads"
    threads.mkdir(parents=True, exist_ok=True)
    (threads / "live.jsonl").write_text(_thread_record("live question", "live") + "\n")
    (threads / ".hidden.jsonl").write_text(_thread_record("hidden question", "hidden") + "\n")

    memory = ExperientialMemory(root=str(tmp_sovereign_root / "chronicle"))
    questions = {t["question"] for t in memory.get_open_threads(limit=9999)}
    assert questions == {"live question"}


def test_every_walker_agrees_that_the_hidden_backup_is_not_the_store(
    chronicle_with_hidden_backup: Path,
):
    """All six walkers share ONE definition (memory.iter_thread_shards). A rule
    spelled six times is a rule that lands on five of six walkers — which is how
    the same store came to have two sizes in the first place."""
    from sovereign_stack import metabolism
    from sovereign_stack.aperture import measure_aperture
    from sovereign_stack.memory import ExperientialMemory, iter_thread_shards
    from sovereign_stack.seasons import _all_threads, _load_threads_readonly

    root = chronicle_with_hidden_backup
    chronicle = root / "chronicle"
    (chronicle / "insights").mkdir(parents=True, exist_ok=True)
    (root / "handoffs").mkdir(parents=True, exist_ok=True)
    (root / "comms" / "letters").mkdir(parents=True, exist_ok=True)

    memory = ExperientialMemory(root=str(chronicle))
    retired = "a retired thread from a pre-migration backup"

    assert len(iter_thread_shards(chronicle / "open_threads")) == 2
    assert retired not in {t["question"] for t in memory.get_open_threads(limit=9999)}
    assert retired not in {t.get("question") for t in _all_threads(memory).values()}
    assert retired not in {t.get("question") for t in _load_threads_readonly(chronicle)}

    ap = measure_aperture(datetime.now(timezone.utc), root=root)
    assert ap["surfaces"]["open_threads"]["on_disk"] == 2

    original = metabolism.CHRONICLE_DIR
    try:
        metabolism.CHRONICLE_DIR = chronicle
        assert retired not in {t.get("question") for t in metabolism._load_all_threads()}
    finally:
        metabolism.CHRONICLE_DIR = original


# ── resolve_thread: reporting success on a resolution that did not happen ────
#
# `resolve_thread(domain, fragment)` addresses the shard by EXACT PATH
# (`threads_dir/{domain}.jsonl`), guarded by `if jsonl_path.exists():`. When
# that misses — an absent domain, a nested shard, a fragment matching nothing —
# control fell straight through to `record_insight(...)` with
# `resolved_thread_id=None`, and the dispatch printed "Thread resolved →
# ground_truth insight: <path>" UNCONDITIONALLY. The chronicle gained a
# ground_truth record asserting a resolution the store does not carry, filed
# under a domain that need not exist, and the caller was handed a path.
#
# NOT fixed by making it recursive: the defect is the false success, not the
# reach. A nested thread is resolvable today through resolve_thread_by_id,
# which does walk. Widening this lookup would move resolution semantics under
# a bug fix.


def _dispatch(tool: str, args: dict) -> str:
    import asyncio

    from sovereign_stack import server

    return asyncio.run(server._dispatch_tool(tool, args))[0].text


def test_resolve_thread_does_not_claim_a_resolution_it_did_not_perform(
    chronicle_with_nested_shard: Path, monkeypatch
):
    from sovereign_stack import server
    from sovereign_stack.memory import ExperientialMemory

    chronicle = chronicle_with_nested_shard / "chronicle"
    memory = ExperientialMemory(root=str(chronicle))
    monkeypatch.setattr(server, "experiential", memory)

    insights_before = sorted((chronicle / "insights").rglob("*.jsonl"))

    text = _dispatch(
        "resolve_thread",
        {
            "domain": "no-such-domain-anywhere",
            "question_fragment": "anything",
            "resolution": "found it anyway",
        },
    )

    assert "Thread resolved" not in text, (
        "the dispatch announced a resolution for a domain that does not exist"
    )
    assert "No unresolved thread" in text
    assert sorted((chronicle / "insights").rglob("*.jsonl")) == insights_before, (
        "a ground_truth insight was written for a resolution that never happened"
    )
    assert not (chronicle / "insights" / "no-such-domain-anywhere").exists()


def test_resolve_thread_on_a_nested_shard_says_so_instead_of_inventing_a_record(
    chronicle_with_nested_shard: Path, monkeypatch
):
    """The nested shard is the live specimen. resolve_thread cannot reach it by
    exact path — that is a known, NAMED limit. What it must not do is answer as
    if it had."""
    from sovereign_stack import server
    from sovereign_stack.memory import ExperientialMemory

    chronicle = chronicle_with_nested_shard / "chronicle"
    memory = ExperientialMemory(root=str(chronicle))
    monkeypatch.setattr(server, "experiential", memory)

    text = _dispatch(
        "resolve_thread",
        {
            "domain": NESTED_DOMAIN,
            "question_fragment": "nested question",
            "resolution": "answered",
        },
    )

    assert "Thread resolved" not in text
    assert "resolve_thread_by_id" in text, "the refusal must name the door that DOES reach it"

    nested = memory.get_open_threads(domain="tech-debt", limit=9999)
    assert [t["resolved"] for t in nested] == [False]

    # And the door it names does reach it.
    thread_id = nested[0]["thread_id"]
    assert memory.resolve_thread_by_id(thread_id, "answered") != ""
    assert memory.get_open_threads(domain="tech-debt", limit=9999) == []
