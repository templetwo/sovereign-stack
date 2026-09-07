"""
Sovereign Console v2 — server-side, token-free data readers.

Every panel added by the v2 reskin is fed from here. The organizing
constraint, and the reason this module exists at all: **the console needs
no bridge credential.** Twelve of the fourteen v2 surfaces are filesystem,
in-package, or no-auth reads; the two that would need the master token
(`GET /api/admin/tokens`, `GET /api/comms/read`) are not read here and
never will be — a KeepAlive daemon holding Anthony's master token is worse
than the design that put it in browser localStorage.

Four disciplines run through every function below.

  1. **FAIL SOFT, NEVER FAIL OPEN.** A missing file, an absent directory,
     malformed JSON, a db that isn't there — all return ``None``. They do
     NOT return ``{"count": 0}``. A plausible zero is indistinguishable
     from a real zero at the panel, and the page has no way to say "this
     source is missing" if the server already answered "it's empty."

     ONE READER DEPARTS FROM THE ``None`` HALF OF THIS, NARROWLY, AND SAYS
     SO: ``read_open_threads_index`` returns an explicit ``{"status":
     "absent", "reason": ..., "last_seen_age_seconds": ...}`` envelope,
     because its panel was specified to render WHY the catalog is
     unusable and HOW OLD the last usable one was, and ``None`` carries
     neither. The prohibition this rule actually enforces is on plausible
     ZEROS, and that envelope contains no count of any kind. See
     ``_index_absent``.

  2. **PROVENANCE IS PART OF THE PAYLOAD.** Every returned dict carries a
     ``source`` (what was read) and an ``age_seconds`` (how old the data
     is). A panel cannot render staleness the server never told it about,
     and three of these sources are dormant instruments: self_model.json
     was last written 2026-05-25, and the retired comms board was 24 days
     silent when this was built.

  3. **EVERYTHING HANGS OFF ``SOVEREIGN_ROOT``.** Resolved fresh on every
     call, never captured at import — the same rule
     ``dashboard.service_log_map()`` follows, so a test's
     ``monkeypatch.setenv("SOVEREIGN_ROOT", tmp_path)`` is honored.

  4. **THE TWO EXTERNAL READERS ARE CACHED AND RESETTABLE.**
     ``fetch_bridge_heartbeat()`` (HTTP) and ``read_guardian()`` (``lsof``
     + ``pgrep``) leave the process. Both cache with a TTL, and both are
     dropped by ``reset_caches()``. That reset is not a convenience: a
     module-level TTL cache plus pytest's arbitrary ordering is precisely
     how a green suite hides a live-system read — a real ``lsof`` result
     cached before a monkeypatch lands would leak into a later test.

Deliberately NOT here: ``metabolize``. It writes to
``metabolism_log.jsonl`` on every ``detect`` call (236 records total, 47
days silent when measured); polling it would add ~3,600/day. Two of the
six metabolism cells it is supposed to feed — "learnings" and "decisions"
— do not exist in its output at all, which is how the prototype's demo
values 23 and 9 survived into its LIVE mode forever. The heartbeat's
``aperture.surfaces`` already carries the same counts, computed, for free.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import guardian_tools

# ── Tunables (all named, none magic) ────────────────────────────────────────

#: The bridge expires a pending arrival after this long. `_expire_stale`
#: only ever runs on the BRIDGE's own connection, so a naive read of the
#: table shows a long-dead request as live. We apply the cutoff ourselves.
PENDING_WINDOW_SECONDS = 900

#: Past this, the SELF-MODEL MIRROR degrades visually. It still renders its
#: content — a 3-month-old truth is not nothing, it is old.
SELF_MODEL_STALE_DAYS = 14

#: Heartbeat is 9,251 bytes and ~115-154ms, and its `attribution.scan`
#: walks 400 shard files per call. At the page's 3s poll that would be a
#: 400-file walk every 3 seconds forever. 10s floor; 20s in practice.
HEARTBEAT_TTL_SECONDS = 20.0
HEARTBEAT_TIMEOUT_SECONDS = 3.0

#: Guardian shells out to `lsof -iTCP -sTCP:LISTEN` plus two `pgrep`s.
#: Three subprocesses per call is the cost we are caching away.
GUARDIAN_TTL_SECONDS = 45.0
GUARDIAN_TIMEOUT_SECONDS = 5.0

DEFAULT_BRIDGE_URL = "http://127.0.0.1:8100"

_LETTER_BUCKETS = ("to_arrival", "breakthroughs", "to_self")
_SELF_MODEL_CATEGORIES = ("strength", "drift", "blind_spot", "tendency")


# ── Root + small helpers ────────────────────────────────────────────────────


def _sovereign_root() -> Path:
    """Resolved on every call so SOVEREIGN_ROOT overrides are honored."""
    return Path(os.environ.get("SOVEREIGN_ROOT", Path.home() / ".sovereign"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value) -> datetime | None:
    """Parse an ISO-8601 stamp to an AWARE datetime, or None.

    Naive strings are assumed UTC rather than local. The chronicle writes
    both shapes, and the failure direction is a FAIL-OPEN, not a fail-closed:
    on a UTC-4 box, reading a naive stamp as local resolves it 4 hours into
    the FUTURE, so `age` goes negative and a request that is 901s stale
    reads PENDING instead of expired against the 900s window. The arrival
    gate would then show a dead request as live. (An earlier draft of this
    docstring stated the opposite direction — corrected by measurement, see
    test_naive_stamp_is_read_as_utc_not_local.)
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_seconds(value) -> float | None:
    dt = _parse_ts(value)
    if dt is None:
        return None
    return max(0.0, (_now() - dt).total_seconds())


def _mtime_age(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _read_json(path: Path):
    """Parse a JSON file, or None on absent/unreadable/malformed."""
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _is_resolved(value) -> bool:
    """Truthiness is WRONG for this field and the failure would be silent.

    The chronicle's open-thread records are written by several producers
    and the field is not schema-enforced. A string ``"False"`` read by
    ``if rec.get("resolved")`` is True — that reads a box with ~180 open
    threads as 0. The inverse mistake (``not`` on the raw value) reads
    ``"True"`` as unresolved. Only an explicit string-aware comparison
    gets both directions right.

    MEASURED SCOPE, so nobody re-derives it: on this machine's live tree on
    2026-08-30 every record carried a JSON boolean and naive truthiness
    happened to agree. The predicate still earns its place — ``aperture.py``
    uses ``not rec.get("resolved", False)``, which DIVERGES from this one on
    exactly the string case, and this console now renders both counts in
    adjacent panels. Do not delete it on the strength of one clean corpus.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


# ── Cache plumbing for the two external readers ─────────────────────────────

_CACHE: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()
# One lock PER CACHE KEY, held across the miss path so N concurrent
# snapshots run ONE probe rather than N. See _single_flight.
_probe_locks: dict[str, threading.Lock] = {}

# Set to a truthy value to make both external readers return None without
# probing. The containment for these two seams belongs AT the seam, not in
# one test file's fixture: SOVEREIGN_ROOT redirects neither probe, so any
# other caller of build_snapshot() would otherwise GET the operator's real
# bridge and shell out lsof/pgrep against their real machine.
NO_EXTERNAL_PROBES_ENV = "SOVEREIGN_DASHBOARD_NO_EXTERNAL_PROBES"


class ExternalProbesDisabled(RuntimeError):
    """Raised at the seam when NO_EXTERNAL_PROBES_ENV is set."""


def _refuse_external_probe(what: str) -> None:
    """Fail CLOSED at the exact function that leaves the process.

    Guarding the two READERS instead would be guarding the wrong line: a
    test that stubs `_guardian_probe` or `_http_get_json` with an in-process
    fake is legitimately exercising the real reader, and a reader-level
    guard would silently turn those into `None` assertions. Guarded here,
    a stub REPLACES the guard (correct — nothing leaves the process) while
    an unstubbed caller is refused (also correct).

    Both readers already catch every exception and fail soft to None, so a
    refusal reads as "could not measure", never as a fabricated zero.
    """
    if os.environ.get(NO_EXTERNAL_PROBES_ENV, "").strip():
        raise ExternalProbesDisabled(f"{NO_EXTERNAL_PROBES_ENV} is set; refusing {what}")


def reset_caches() -> None:
    """Drop every cached external probe. Called by tests on BOTH sides of a
    test so cache state can never make test order load-bearing."""
    with _cache_lock:
        _CACHE.clear()


def _single_flight(key: str) -> threading.Lock:
    """The per-key lock for `key`, created once.

    Acquired OUTSIDE `_cache_lock` by the caller: `_cache_lock` guards only
    the dict, and holding it across a 5s subprocess probe would serialize
    every reader in the process, not just the one that missed.
    """
    with _cache_lock:
        lock = _probe_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _probe_locks[key] = lock
        return lock


def _cache_get(key: str, ttl: float):
    """Return `(value,)` on a hit, None on a miss.

    A cached dict gets its `age_seconds` REWRITTEN to the real age of the
    stored value on the way out. Stamping 0.0 at fetch time and then
    re-serving that object for the whole TTL is the "green LIVE badge over
    stale data" shape this module exists to refuse: the GUARDIAN panel read
    "0s old" permanently, because every cache hit re-served the same 0.0.
    The returned dict is a COPY, so the cached object is never mutated and
    successive hits do not accumulate edits.
    """
    with _cache_lock:
        hit = _CACHE.get(key)
    if hit is None:
        return None
    stored_at, value = hit
    age = time.monotonic() - stored_at
    if age > ttl:
        return None
    if isinstance(value, dict) and "age_seconds" in value:
        value = {**value, "age_seconds": round(age, 3)}
    return (value,)  # tuple-wrapped so a cached None is still a hit


def _cache_put(key: str, value) -> None:
    with _cache_lock:
        _CACHE[key] = (time.monotonic(), value)


# ── SPIRAL ──────────────────────────────────────────────────────────────────


def read_spiral_state() -> dict | None:
    """``~/.sovereign/spiral_state.json`` — phase, depth, tool-call count.

    ``tool_call_count`` is what the ACTIVITY panel's synthesized TOOLS lane
    differences against; the raw counter is monotonic, the delta is the
    event. Missing sub-fields stay ``None`` rather than defaulting to 0 —
    "no phase recorded" and "phase 0" are different facts.
    """
    path = _sovereign_root() / "spiral_state.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        return None

    history = data.get("phase_history")
    started = data.get("started")
    return {
        "source": str(path),
        "session_id": data.get("session_id"),
        "current_phase": data.get("current_phase"),
        "reflection_depth": data.get("reflection_depth"),
        "tool_call_count": data.get("tool_call_count"),
        "phase_history_count": len(history) if isinstance(history, list) else None,
        "started": started,
        "session_age_seconds": _age_seconds(started),
        # Age of the FILE — the counter has no self-timestamp, so mtime is
        # the only honest freshness signal for this one.
        "age_seconds": _mtime_age(path),
    }


# ── SELF-MODEL MIRROR ───────────────────────────────────────────────────────


def read_self_model() -> dict | None:
    """``~/.sovereign/self_model.json`` — four categories, newest entry each.

    Age comes from the newest RECORD timestamp, never the file mtime: a
    backup, an rsync, or a dedup pass touches mtime and would report a
    three-month-old self-model as fresh. This panel is one of three fed by
    a dormant instrument, so its age is the most load-bearing field it has.
    """
    path = _sovereign_root() / "self_model.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        return None

    entries: list[dict] = []
    for category in _SELF_MODEL_CATEGORIES:
        raw = data.get(category)
        if not isinstance(raw, list) or not raw:
            continue
        dated = [r for r in raw if isinstance(r, dict)]
        if not dated:
            continue
        newest = max(
            dated,
            key=lambda r: _parse_ts(r.get("timestamp"))
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        entries.append(
            {
                "category": category,
                "observation": newest.get("observation"),
                "timestamp": newest.get("timestamp"),
                "age_seconds": _age_seconds(newest.get("timestamp")),
                "entry_count": len(raw),
            }
        )

    if not entries:
        return None

    ages = [e["age_seconds"] for e in entries if e["age_seconds"] is not None]
    newest_age = min(ages) if ages else None
    entries.sort(key=lambda e: (e["age_seconds"] is None, e["age_seconds"]))

    return {
        "source": str(path),
        "entries": entries,
        "age_seconds": newest_age,
        "stale": bool(newest_age is not None and newest_age > SELF_MODEL_STALE_DAYS * 86400),
        "stale_after_days": SELF_MODEL_STALE_DAYS,
    }


# ── OPEN THREADS ────────────────────────────────────────────────────────────


def read_open_threads(limit: int = 6) -> dict | None:
    """Unresolved threads from ``chronicle/open_threads/**/*.jsonl``.

    RECURSIVE — ``dashboard.py`` reads this tree with ``recursive=True``
    and a flat glob silently misses nested shards. Malformed lines are
    counted, not swallowed: ``malformed_skipped`` is the coverage signal
    that keeps a partial read from reading as a complete one.

    ``unreadable_files`` is the second half of that signal. A file that
    raises on read used to be ``continue``d after ``files_scanned`` had
    already been incremented — so a shard contributing ZERO records read as
    fully scanned, in the one field advertised as proof of coverage. Both
    counters are non-zero-means-partial; neither is decorative.
    """
    directory = _sovereign_root() / "chronicle" / "open_threads"
    if not directory.is_dir():
        return None

    unresolved: list[dict] = []
    malformed = 0
    unreadable = 0
    files = 0
    for path in sorted(directory.rglob("*.jsonl")):
        files += 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            unreadable += 1
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(record, dict):
                malformed += 1
                continue
            if _is_resolved(record.get("resolved")):
                continue
            unresolved.append(
                {
                    "thread_id": record.get("thread_id"),
                    "domain": record.get("domain") or "",
                    "question": record.get("question") or "",
                    "timestamp": record.get("timestamp"),
                    "age_seconds": _age_seconds(record.get("timestamp")),
                }
            )

    unresolved.sort(
        key=lambda t: _parse_ts(t["timestamp"]) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    newest_age = unresolved[0]["age_seconds"] if unresolved else None

    return {
        "source": str(directory),
        "unresolved_count": len(unresolved),
        "threads": unresolved[: max(0, limit)],
        "files_scanned": files,
        "malformed_skipped": malformed,
        "unreadable_files": unreadable,
        "age_seconds": newest_age,
    }


# ── OPEN-THREADS INDEX ──────────────────────────────────────────────────────
#
# The pile the OPEN THREADS panel structurally cannot show. That panel
# renders the 6 NEWEST unresolved threads; on 2026-09-06 there were 191
# open and 176 of them had never been touched. Six of one hundred ninety
# one is not a sample, it is a lid — the newest six are by construction the
# only ones a reader ever sees, so the 138-day-old April rows are invisible
# from the console no matter how long anyone watches it.
#
# This reader does NOT re-derive that catalog. It reads the filing the
# index run writes, and the division of labour is the point: the index
# script walks 163 shard files, classifies, clusters, and assigns owners;
# the dashboard reads ONE json and renders totals. A console that
# recomputed the catalog on a 3-second poll would walk that tree ~28,800
# times a day to answer a question whose answer changes when someone runs
# the index.

#: Where the index run lands its newest output. LATEST.json is the pointer;
#: the dated siblings (``2026-09-06_open-threads-index.json``) are the
#: history and are what `_last_seen` falls back to when the pointer is gone.
OPEN_THREADS_INDEX_DIRNAME = "open-threads-index"
OPEN_THREADS_INDEX_FILENAME = "LATEST.json"

#: Past this the panel degrades visually. The index is a hand-run
#: instrument, not a daemon: a 3-day-old catalog is old, not broken.
OPEN_THREADS_INDEX_STALE_DAYS = 3

#: `similar_to[].reason` opens with this label when the index run grouped a
#: near-duplicate family. See `_duplicate_clusters` for why a free-text
#: prefix is the PRIMARY key here and what happens when it is absent.
_DUP_LABEL_RE = re.compile(r"\bdup-(\d+)")


def _index_absent(path: Path, reason: str, last_seen: Path | None = None) -> dict:
    """The ABSENT envelope: a reason, an age, and NOT ONE COUNT.

    This reader is the module's one deliberate departure from "a missing
    source returns None" (discipline #1 in the module docstring), and the
    departure is narrow: None can carry neither WHY the file is unusable
    nor HOW OLD the last usable one was, and the panel was specified to
    render both. Discipline #1 forbids a PLAUSIBLE ZERO — a shape a panel
    cannot distinguish from a real measurement. There is no count of any
    kind below, so this envelope cannot be misread as one: the panel
    branches on `status` before it reads anything else.

    `last_seen_at` is MTIME-derived and named so. It is a different
    instrument from the `generated_at` a present file carries (that is the
    record's own stamp), and this module already has a test insisting the
    two never get conflated — an rsync moves an mtime, it does not move a
    generation.
    """
    stat_path = last_seen if last_seen is not None else path
    age = _mtime_age(stat_path)
    seen_at = None
    if age is not None:
        with contextlib.suppress(OSError, OverflowError, ValueError):
            seen_at = datetime.fromtimestamp(stat_path.stat().st_mtime, tz=timezone.utc).isoformat()
    return {
        "source": str(path),
        "status": "absent",
        "reason": reason,
        "last_seen_source": str(stat_path) if age is not None else None,
        "last_seen_at": seen_at,
        "last_seen_age_seconds": age,
        "age_seconds": None,
    }


def _newest_index_sibling(directory: Path, exclude: Path) -> Path | None:
    """Newest dated index file, for "the age of whatever last existed".

    A vanished LATEST.json does not mean the catalog never existed; the
    dated files are still there and their mtime is the honest answer to
    "how long since anyone ran this." Returns None when the directory
    itself is gone, which is the case where the true answer is "never".
    """
    try:
        candidates = [
            p for p in directory.glob("*.json") if p.is_file() and p.resolve() != exclude.resolve()
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _duplicate_clusters(open_entries: list[dict]) -> tuple[int, int, str]:
    """(clusters, members, method) over the near-duplicate families.

    TWO ALGORITHMS, and which one ran is part of the payload, because a
    number nobody can tie to the filing beside it is a rumour.

    PRIMARY — group by the ``dup-NN`` label the index run writes into each
    ``similar_to[].reason``, and count only families with **two or more
    OPEN members**. Verified against the 2026-09-06 filing: 14 clusters
    covering 40 open ids, exactly what its section (a) reports. Both halves
    of the rule are load-bearing — 15 labels appear, and `dup-05` is
    dropped because three of its four members are already resolved, so a
    "cluster" of one open thread is not a duplicate of anything.

    FALLBACK — connected components over ``similar_to[].thread_id``,
    restricted to open entries. Structural, using only schema fields, and
    it exists so that a future index run which stops writing the free-text
    label degrades to a defensible number instead of to ZERO. Zero clusters
    while `similar_to` links plainly exist is the fabricated-zero this
    house forbids, and a regex over prose is exactly the kind of coupling
    that breaks quietly. Named in the payload as `similar-to-components`
    because it does NOT reproduce the filing's number (16/47 vs 14/40 on
    the 09-06 data): the label groups a semicolon-split family that the
    link graph splits in two, and it ignores links carrying no label.
    """
    labelled: dict[str, set[str]] = {}
    adjacency: dict[str, set[str]] = {}
    open_ids = {e.get("thread_id") for e in open_entries if e.get("thread_id")}

    for entry in open_entries:
        this_id = entry.get("thread_id")
        similar = entry.get("similar_to")
        if not this_id or not isinstance(similar, list):
            continue
        for link in similar:
            if not isinstance(link, dict):
                continue
            match = _DUP_LABEL_RE.search(str(link.get("reason") or ""))
            if match:
                labelled.setdefault(f"dup-{match.group(1)}", set()).add(this_id)
            other = link.get("thread_id")
            if isinstance(other, str) and other in open_ids and other != this_id:
                adjacency.setdefault(this_id, set()).add(other)
                adjacency.setdefault(other, set()).add(this_id)

    # GATED ON `labelled`, NOT ON `families`, and the difference is a real
    # fault. If a catalog carries dup-NN labels but every family has aged
    # down to a single open member, `families` is empty while the label
    # convention is very much alive — gating on it would fall through to
    # components and report a number under a method name that is not the
    # one in force. Zero clusters by dup-label is the honest answer there,
    # and `dup-05` in the 2026-09-06 filing is exactly that shape already.
    if labelled:
        families = [ids for ids in labelled.values() if len(ids) >= 2]
        return len(families), sum(len(ids) for ids in families), "dup-label"

    seen: set[str] = set()
    components = 0
    members = 0
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack = [start]
        component: set[str] = set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            seen.add(node)
            stack.extend(adjacency.get(node, set()) - component)
        if len(component) >= 2:
            components += 1
            members += len(component)
    return components, members, "similar-to-components"


def _tally(pairs, *, chronological: bool = False) -> list[dict]:
    """Counted breakdown as an ORDERED list, not a dict.

    A list because the order is a decision the server makes and JSON object
    key order is not a contract the page should have to trust. Categories
    and gate-shapes sort biggest-first (ties by name, so the panel does not
    reshuffle between polls on equal counts); months sort chronologically,
    because "2026-04" before "2026-05" is information and "72 before 52" is
    not — a month axis that jumps around by size is unreadable as a trend.
    """
    counts: dict[str, int] = {}
    for value in pairs:
        counts[value] = counts.get(value, 0) + 1
    if chronological:
        ordered = sorted(counts.items())
    else:
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": name, "count": count} for name, count in ordered]


def read_open_threads_index() -> dict:
    """``~/.sovereign/filings/open-threads-index/LATEST.json`` — the catalog.

    ALWAYS returns a dict, never None: see `_index_absent` for why this one
    reader carries an explicit absent envelope instead.

    THE DENOMINATOR IS DERIVED, NOT COPIED, AND IT IS ONE NUMBER. The
    filing's `coverage.counts` carries no by-category, by-month, by-shape,
    answered-elsewhere or cluster totals, so every breakdown here is
    computed from `entries` with `status == "open"`. That gives 191 on the
    09-06 file, which equals `coverage.counts.open_including_nested`, and
    the reader cross-checks exactly that: `counts_reconcile` is False when
    the derivation and the file's own coverage line disagree, which is a
    coverage signal, not a crash.

    Be careful reading this against the filing's prose. THE .MD MIXES TWO
    DENOMINATORS: its by-category and by-gate-shape tables sum to 191 (all
    open), while its by-month table and its "176 never touched" are scoped
    to the 190 TOP-LEVEL rows and silently drop the one nested entry from
    `tech-debt,compaction,auto-detection/log.jsonl`. Nothing in an entry
    marks it nested, so the 190 view is not reconstructible from the JSON.
    This panel uses ONE denominator for everything and states it, so its
    April count reads 17 where the filing's table reads 16, and its
    never-touched reads 177 where the filing reads 176. Both are right
    about different sets; only one of them can be a panel.

    MALFORMED IS A THREE-PART BAR, and it is deliberately not stricter: a
    dict, an `entries` list, and a parseable generation stamp. The stamp is
    looked for at `coverage.generated_at_utc`, then top-level
    `generated_at_utc`, then top-level `generated_at` — a fallback chain
    rather than one key, because the LATEST.json writer is a separate
    program from the one that produced the file this was verified against,
    and a cosmetic difference in where it puts its own timestamp must not
    render the panel ABSENT. An `entries` list that is EMPTY is a real
    zero and renders present: a catalog honestly reporting nothing open is
    a measurement, and refusing to show it would be the mirror-image lie.
    """
    directory = _sovereign_root() / "filings" / OPEN_THREADS_INDEX_DIRNAME
    path = directory / OPEN_THREADS_INDEX_FILENAME

    if not path.is_file():
        # Two different absences, and telling them apart is the whole
        # value of the line: a missing pointer beside dated files means an
        # index run happened and did not update LATEST.json, which is a
        # different problem from an index that has never been run here.
        sibling = _newest_index_sibling(directory, path)
        reason = (
            "LATEST.json not found — dated index files exist, so the pointer is missing"
            if sibling is not None
            else "LATEST.json not found — no index run has landed here"
        )
        return _index_absent(path, reason, last_seen=sibling)

    payload = _read_json(path)
    if payload is None:
        return _index_absent(path, "LATEST.json is unreadable or not valid JSON")
    if not isinstance(payload, dict):
        return _index_absent(path, "LATEST.json is not a JSON object")

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return _index_absent(path, "LATEST.json carries no `entries` list")

    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    generated_at = None
    for candidate in (
        coverage.get("generated_at_utc"),
        payload.get("generated_at_utc"),
        payload.get("generated_at"),
    ):
        if _parse_ts(candidate) is not None:
            generated_at = candidate
            break
    if generated_at is None:
        return _index_absent(path, "LATEST.json carries no parseable generation timestamp")

    open_entries = [
        e for e in entries if isinstance(e, dict) and str(e.get("status") or "") == "open"
    ]

    def _untouched(entry: dict) -> bool:
        touches = entry.get("touches")
        if isinstance(touches, dict):
            return not touches.get("count")
        return not touches

    never_touched = sum(1 for e in open_entries if _untouched(e))
    answered = sum(1 for e in open_entries if e.get("answered_elsewhere") not in (None, "", [], {}))
    clusters, cluster_members, cluster_method = _duplicate_clusters(open_entries)

    counts = coverage.get("counts") if isinstance(coverage.get("counts"), dict) else {}
    declared = counts.get("open_including_nested")
    reconcile = None if not isinstance(declared, int) else declared == len(open_entries)

    return {
        "source": str(path),
        "status": "present",
        "author": payload.get("author") if isinstance(payload.get("author"), str) else None,
        "generated_at": generated_at,
        # The record's own stamp, NOT the file's mtime. `last_seen_age_seconds`
        # on the absent envelope is the mtime one; they are never the same field.
        "age_seconds": _age_seconds(generated_at),
        "stale_after_days": OPEN_THREADS_INDEX_STALE_DAYS,
        "entries_total": len(entries),
        "open_count": len(open_entries),
        "never_touched": never_touched,
        "touched": len(open_entries) - never_touched,
        "answered_elsewhere": answered,
        "duplicate_clusters": clusters,
        "duplicate_cluster_members": cluster_members,
        "cluster_method": cluster_method,
        "by_category": _tally(str(e.get("category") or "(uncategorized)") for e in open_entries),
        "by_month": _tally(
            ((str(e.get("opened") or "")[:7] or "(undated)") for e in open_entries),
            chronological=True,
        ),
        "by_shape": _tally(str(e.get("shape") or "none") for e in open_entries),
        "declared_open_including_nested": declared if isinstance(declared, int) else None,
        "counts_reconcile": reconcile,
    }


# ── ARRIVAL GATE ────────────────────────────────────────────────────────────


def read_arrival_gate() -> dict | None:
    """Pending arrivals from the bridge's ``session_tokens.db``, READ-ONLY.

    Three things this deliberately does:

    * Opens with a ``mode=ro`` URI so a missing db raises rather than being
      CREATED. A dashboard that brings the bridge's token store into
      existence is a write nobody authorized.
    * Does NOT use ``immutable=1``. The bridge writes this file live;
      immutable reads can return torn pages.
    * Applies ``PENDING_WINDOW_SECONDS`` itself (see the constant).

    The TOKENS half of the design's "ARRIVAL GATE · TOKENS" card is
    omitted, not silently emptied. ``GET /api/admin/tokens`` is master-token
    only; the README's "degrade silently on 401/403" renders an empty list,
    which reads as "no session tokens exist" — a false statement produced
    by a permissions failure. We state the unavailability instead.
    """
    path = _sovereign_root() / "bridge" / "session_tokens.db"
    conn = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT rid, code, source_instance, seat_description, requested_scope, "
            "status, created_at FROM arrival_requests"
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    now = _now()
    pending: list[dict] = []
    expired_by_cutoff = 0
    for row in rows:
        if (row["status"] or "").strip().lower() != "pending":
            continue
        created = _parse_ts(row["created_at"])
        age = (now - created).total_seconds() if created else None
        if age is not None and age > PENDING_WINDOW_SECONDS:
            expired_by_cutoff += 1
            continue
        pending.append(
            {
                # `rid` is DELIBERATELY ABSENT. It is a 192-bit capability
                # token (`arq_` + secrets.token_urlsafe(24)) and it is the
                # SOLE input GET /api/arrival/poll/{rid} requires — that
                # route takes no bearer, no CSRF, no signature, and the
                # first caller to poll an approved rid consumes the one
                # session-token mint. /snapshot.json is served
                # unauthenticated with Access-Control-Allow-Origin: *, so
                # publishing the rid there hands any origin (or any local
                # process) both token theft and, by polling faster than the
                # gate's discipline threshold, denial of arrival. Nothing
                # on the page ever read it. Do not re-add it, under this
                # name or another. See test_rid_never_reaches_the_snapshot.
                "code": row["code"],
                "source_instance": row["source_instance"],
                "seat_description": row["seat_description"],
                "requested_scope": row["requested_scope"],
                "created_at": row["created_at"],
                "age_seconds": age,
            }
        )

    pending.sort(key=lambda p: (p["age_seconds"] is None, p["age_seconds"]))

    return {
        "source": str(path),
        "status": "asked" if pending else "quiet",
        "pending": pending,
        "pending_count": len(pending),
        "expired_by_cutoff": expired_by_cutoff,
        "pending_window_seconds": PENDING_WINDOW_SECONDS,
        "total_requests": len(rows),
        # Explicit unavailability — never an empty list. See the docstring.
        "tokens_available": False,
        "tokens_note": "session-token list not available without the master token",
        "age_seconds": _mtime_age(path),
    }


# ── LINEAGE LETTERS (replaces the retired COMMS board) ──────────────────────


_FRONTMATTER_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
_FILENAME_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})[-_]?(.*)$")
# A frontmatter block is a handful of lines. Past this, the file is
# malformed and scanning further is walking the body. See _letter_frontmatter.
_MAX_FRONTMATTER_LINES = 50


def _letter_frontmatter(path: Path) -> dict:
    """Read ONLY the YAML frontmatter block — never a byte of the body.

    The body of a lineage letter is a private thing written by one instance
    to another; it does not belong on an ops dashboard. Enforced
    structurally by REQUIRING the closing ``---`` — not merely by stopping
    at it. An earlier version read "until the next ``---`` or EOF", which
    meant a letter with an opening delimiter and no closing one had its
    entire body scanned with the frontmatter regex; a body line reading
    ``from: <something private>`` was published onto an unauthenticated
    route. A malformed letter now degrades to no metadata instead.

    Two bounds, both load-bearing:

    * ``_MAX_FRONTMATTER_LINES`` caps the scan. A frontmatter block is a
      handful of lines; anything longer is malformed, and without a cap a
      single unterminated 20MB letter walks in full on every 3s poll of
      every open tab (measured: 0.4ms -> 73.2ms, and this reader has no
      TTL cache to absorb it).
    * Fields are only returned once the terminator is seen. Partial
      results from a truncated scan are discarded.
    """
    fields: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline().strip()
            if first != "---":
                return {}
            terminated = False
            for index, line in enumerate(handle):
                if index >= _MAX_FRONTMATTER_LINES:
                    break
                if line.strip() == "---":
                    terminated = True
                    break
                match = _FRONTMATTER_KEY.match(line.rstrip("\n"))
                if match:
                    fields[match.group(1)] = match.group(2).strip()
    except OSError:
        return {}
    return fields if terminated else {}


def read_lineage_letters(limit: int = 6) -> dict | None:
    """Newest lineage letters by mtime — TITLE AND DATE ONLY.

    Replaces the design's COMMS panel. The comms board was RETIRED in
    2026-06; its newest message was 24 days old when this was built and the
    freshest traffic on it was automated hq-pulse alerts, not
    correspondence. Wiring a dead transport to a live-looking panel is the
    exact "green LIVE badge over stale data" failure the v2 prototype ships.
    Lineage letters are where correspondence actually moved.
    """
    base = _sovereign_root() / "comms" / "letters"
    if not base.is_dir():
        return None

    letters: list[dict] = []
    counts: dict[str, int] = {}
    for bucket in _LETTER_BUCKETS:
        directory = base / bucket
        if not directory.is_dir():
            counts[bucket] = 0
            continue
        paths = [p for p in directory.glob("*.md") if p.is_file()]
        counts[bucket] = len(paths)
        for path in paths:
            meta = _letter_frontmatter(path)
            stem_match = _FILENAME_DATE.match(path.stem)
            date = meta.get("written_at") or meta.get("event_date")
            if not date and stem_match:
                date = stem_match.group(1)
            title = meta.get("title")
            if not title:
                slug = stem_match.group(2) if stem_match else path.stem
                title = slug.replace("-", " ").replace("_", " ").strip().title() or path.stem
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            letters.append(
                {
                    "bucket": bucket,
                    "title": title,
                    "date": date,
                    "from": meta.get("from") or meta.get("written_by"),
                    "age_seconds": _age_seconds(date),
                    "_mtime": mtime,
                }
            )

    if not counts:
        return None

    letters.sort(key=lambda letter: letter["_mtime"], reverse=True)
    for letter in letters:
        letter.pop("_mtime", None)

    ages = [letter["age_seconds"] for letter in letters if letter["age_seconds"] is not None]
    return {
        "source": str(base),
        "letters": letters[: max(0, limit)],
        "counts": counts,
        "total": sum(counts.values()),
        "age_seconds": min(ages) if ages else None,
    }


# ── GUARDIAN ────────────────────────────────────────────────────────────────


# System binaries the guardian probe shells out to. Resolved by ABSOLUTE PATH,
# never by bare name: `lsof` lives in /usr/sbin, which launchd's plist PATH for
# this daemon does not contain (venv/bin:/opt/homebrew/bin:/usr/local/bin:
# /usr/bin:/bin). A bare ["lsof", ...] therefore raises FileNotFoundError inside
# the daemon while working perfectly from an interactive shell whose PATH has
# /usr/sbin -- the panel went dark for exactly this reason on 2026-08-30 and the
# fail-soft None hid the cause. Same class as the /usr/bin/log-vs-zsh-builtin
# trap this house already carries: for a system tool, resolve the path, do not
# inherit it.
_PROBE_SEARCH_DIRS = ("/usr/sbin", "/usr/bin", "/bin", "/sbin", "/opt/homebrew/bin")


def _resolve_tool(name: str) -> str:
    """Absolute path for a system binary, PATH first then known sbin dirs.

    Returns the bare name if nothing resolves, so the caller still raises the
    same FileNotFoundError it always did rather than silently doing nothing.
    """
    found = shutil.which(name)
    if found:
        return found
    for d in _PROBE_SEARCH_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return name


def _guardian_probe() -> tuple[list[str], dict[str, bool]]:
    """Collect guardian's inputs synchronously.

    Deliberately NOT ``asyncio.run(guardian_tools._status_async())``: this
    runs inside a ThreadingHTTPServer worker thread, and spinning a fresh
    event loop per snapshot to drive three subprocesses is more machinery
    for the same three subprocesses. The SCORING is still
    ``guardian_tools._evaluate_status`` — the pure function is the part
    worth sharing, and re-implementing it here would let the dashboard's
    idea of "healthy" drift from the guardian's.
    """
    _refuse_external_probe("lsof/pgrep guardian probe")
    listener = subprocess.run(
        [_resolve_tool("lsof"), "-iTCP", "-sTCP:LISTEN", "-n", "-P"],
        capture_output=True,
        text=True,
        timeout=GUARDIAN_TIMEOUT_SECONDS,
        check=False,
    )
    services: dict[str, bool] = {}
    for name in ("ollama", "sovereign"):
        found = subprocess.run(
            [_resolve_tool("pgrep"), "-x", name],
            capture_output=True,
            text=True,
            timeout=GUARDIAN_TIMEOUT_SECONDS,
            check=False,
        )
        services[name] = bool(found.stdout.strip())
    return (listener.stdout or "").splitlines(), services


def read_guardian() -> dict | None:
    """Security posture via ``guardian_tools._evaluate_status``. Cached.

    Fails soft to ``None`` on any probe failure — a guardian panel that
    invents a 100 because ``lsof`` is missing is worse than one that says
    it could not measure.
    """
    hit = _cache_get("guardian", GUARDIAN_TTL_SECONDS)
    if hit is not None:
        return hit[0]

    lock = _single_flight("guardian")
    with lock:
        # Re-check under the lock: the thread that held it may have just
        # filled the cache, and the point of the lock is that its waiters
        # do NOT then each run their own three subprocesses.
        hit = _cache_get("guardian", GUARDIAN_TTL_SECONDS)
        if hit is not None:
            return hit[0]
        return _read_guardian_uncached()


def _read_guardian_uncached() -> dict | None:
    try:
        listener_lines, services = _guardian_probe()
        status = guardian_tools._evaluate_status(listener_lines, services)
    except Exception:
        _cache_put("guardian", None)
        return None

    issues = status.get("issues") or []
    # guardian_tools returns the literal ["No issues detected"] for a clean
    # box — that is prose, not a finding, and rendering it as a red issue
    # row would be a false alarm on a healthy machine.
    real_issues = [i for i in issues if i != "No issues detected"]
    result = {
        "source": "guardian_tools._evaluate_status",
        "health_score": status.get("health_score"),
        "listeners": status.get("listeners"),
        "ollama_localhost_only": status.get("ollama_localhost_only"),
        "issues": real_issues,
        "issue_count": len(real_issues),
        "services": status.get("services"),
        "timestamp": status.get("timestamp"),
        "age_seconds": 0.0,
        "cache_ttl_seconds": GUARDIAN_TTL_SECONDS,
    }
    _cache_put("guardian", result)
    return result


# ── BRIDGE HEARTBEAT ────────────────────────────────────────────────────────


def _http_get_json(url: str, timeout: float):
    _refuse_external_probe(f"HTTP GET {url}")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def fetch_bridge_heartbeat() -> dict | None:
    """``GET /api/heartbeat`` on the local bridge — NO AUTHENTICATION.

    That is the point: the heartbeat is the one bridge door that needs no
    credential, and it already carries everything v2 wanted the master
    token for. ``aperture.surfaces`` replaces ``metabolize`` outright —
    insight/thread/handoff counts, precomputed, no write, no chronicle
    walk on our side.

    Two field traps, both live-verified: the key is ``tools``, not
    ``tool_count`` (the prototype's fallback is backwards), and ``version``
    is resolved once at bridge import so it goes stale —
    ``source_commit``/``bridge_commit`` are the fields that cannot.
    """
    hit = _cache_get("heartbeat", HEARTBEAT_TTL_SECONDS)
    if hit is not None:
        return hit[0]

    lock = _single_flight("heartbeat")
    with lock:
        hit = _cache_get("heartbeat", HEARTBEAT_TTL_SECONDS)
        if hit is not None:
            return hit[0]
        return _fetch_bridge_heartbeat_uncached()


def _fetch_bridge_heartbeat_uncached() -> dict | None:
    base = os.environ.get("SOVEREIGN_BRIDGE_URL", DEFAULT_BRIDGE_URL).rstrip("/")
    try:
        payload = _http_get_json(f"{base}/api/heartbeat", HEARTBEAT_TIMEOUT_SECONDS)
    except Exception:
        _cache_put("heartbeat", None)
        return None

    if not isinstance(payload, dict):
        _cache_put("heartbeat", None)
        return None

    aperture = payload.get("aperture") if isinstance(payload.get("aperture"), dict) else {}
    gate = payload.get("gate") if isinstance(payload.get("gate"), dict) else {}
    service_start = payload.get("service_start_time")

    result = {
        "source": f"{base}/api/heartbeat",
        "version": payload.get("version"),
        # `tools`, not `tool_count` — the latter does not exist.
        "tools": payload.get("tools"),
        "source_commit": payload.get("source_commit"),
        "bridge_commit": payload.get("bridge_commit"),
        "service_start_time": service_start,
        "service_uptime_seconds": _age_seconds(service_start),
        "aperture_surfaces": aperture.get("surfaces"),
        "gate_total_pending_all_substrates": gate.get("total_pending_all_substrates"),
        "gate_total_pending_claim_bearing": gate.get("total_pending_claim_bearing"),
        "arrival_gate": payload.get("arrival_gate"),
        "age_seconds": 0.0,
        "cache_ttl_seconds": HEARTBEAT_TTL_SECONDS,
    }
    _cache_put("heartbeat", result)
    return result
