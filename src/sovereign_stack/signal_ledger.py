"""Signal ledger — watch-seat instrument (mesh-20260905).

Append-only JSONL at <sovereign root>/signals/ledger.jsonl.
Latest row per signal_id wins. Never deletes. Fail-closed: an unreadable
ledger is an ERROR on the heartbeat field, never a zero count.

Do not enable ntfy from this module; SIGNAL_LEDGER_NTFY defaults off.

THE FRESHNESS INTERVAL IS 3600 SECONDS, and it is the ingestion cadence of
this whole subsystem — state it when you describe the design, because a
reader who does not know it will assume every read sweeps every source.
``ensure_scanned`` runs a source sweep only when the marker on disk is older
than ``DEFAULT_MARKER_MAX_AGE_SECONDS`` (override
``SIGNAL_LEDGER_MARKER_MAX_AGE``), so a burst of reads costs one sweep and a
15-minute poller does NOT rescan every 15 minutes — under defaults the sweep
is about hourly, with exact boundary timing set by the read schedule. The
bound each scan was judged against is written into the marker itself, so a
reader judges a scan by the contract its own writer declared.

CONCURRENT READERS DO ONE SCAN, not N. The refresh takes an exclusive flock
on the marker path and re-checks under it. Lock order is one-directional and
must stay that way: MARKER LOCK -> ledger appends (``_append_lock``) ->
marker write. Never take the marker lock while holding a ledger append.

THE SWEEP IS INCREMENTAL, BUDGETED, AND OFF THE CALLER'S THREAD (2026-09-06).
The deployed sweep re-read every source shard and re-folded the whole ledger
once per source record: 265 s at full CPU, measured, inside the process that
answers every stack tool call. Three changes, and only the first one is the
one people expect. (1) ``_ScanIndex`` — the sweep carries ONE fold of the
ledger and tails the file for what was appended, instead of re-folding 6 MB
per record; this alone took the same corpus from 265 s to 0.34 s. (2) PER-SHARD
WATERMARKS in the certificate — size, mtime_ns and row count per shard, so a
rescan reads only what moved and unchanged shards carry their certified
entries forward. (3) A HARD TIME BUDGET and a background worker: a sweep that
overruns writes a PARTIAL certificate that names what it did not reach, and a
read that finds a sweep running answers from the previous certificate marked
``refreshing`` rather than waiting. None of it weakens the integrity rules
below: a partial or refreshing read still publishes NULL, never a short count.

AND A DAMAGED LEDGER IS NEVER REPAIRED BY RESCANNING IT (review N2). A refresh
writes a new marker, and a new marker certifies whatever the file now holds —
so a refresh over a truncated ledger destroys the only evidence that anything
was lost. Integrity is therefore checked BEFORE any refresh, and a failure
returns an error and leaves the marker exactly where it is, until a human
moves the damaged ledger aside.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import contextvars
import fcntl
import hashlib
import json
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp.types import Tool

from .dispatch_context import REFUSED_IDENTITY_ARGUMENTS, caller_seat
from .memory import iter_thread_shards
from .provenance import default_sovereign_root


class LedgerUnreadable(Exception):
    """Ledger exists but cannot be trusted. Heartbeat must not report zero."""


STATES = ("open", "acknowledged", "acted", "dismissed")
REQUIRED_ROW_FIELDS = ("signal_id", "source", "produced_at", "owner", "state")
CLOSE_STATES = ("acknowledged", "acted", "dismissed")
SOURCES = (
    "honk",
    "watchman",
    "proposal",
    "halt",
    "decision",
    "guardian",
    "thread",
)

# Producer identity per source. closed_by must not equal this.
SOURCE_PRODUCER = {
    "honk": "nape",
    "watchman": "watchman",
    "proposal": "bridge",
    "halt": "daemon",
    "decision": "metabolize",
    "guardian": "guardian",
    "thread": "chronicle",
}

NTFY_ENV = "SIGNAL_LEDGER_NTFY"

# ── THE FRESHNESS BOUND ─────────────────────────────────────────────────────
#
# A marker is a claim that a scan HAPPENED. It says nothing about when, unless
# a reader is willing to call an old claim stale — and the branch as reviewed
# was not: a legitimate marker stamped `scanned_at="2020-01-01T00:00:00Z"`
# returned ingestion:"ok" with a straight face (review F1). "The last scan was
# six years ago" and "the last scan was a minute ago" were the same health.
#
# The bound is WRITTEN INTO THE MARKER, not only read from here, so the reader
# judges a scan by the contract its own writer declared rather than by whatever
# this constant happens to be when the reader is deployed. Env override exists
# so a slower scan cadence can declare itself without a code change.
MARKER_MAX_AGE_ENV = "SIGNAL_LEDGER_MARKER_MAX_AGE"
DEFAULT_MARKER_MAX_AGE_SECONDS = 3600


def marker_max_age_seconds() -> int:
    raw = os.environ.get(MARKER_MAX_AGE_ENV, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_MARKER_MAX_AGE_SECONDS
        if value > 0:
            return value
    return DEFAULT_MARKER_MAX_AGE_SECONDS


# ── THE SWEEP'S OWN BOUNDS (2026-09-06, the incremental release) ────────────
#
# THREE NUMBERS, AND EACH ONE EXISTS BECAUSE A MEASUREMENT SAID SO. The
# deployed sweep took 265 s over the live corpus, at full CPU, inside the
# process that answers every stack tool call. A reader waiting on that is not
# waiting on a health check, it is waiting on a batch job.
#
#   * SCAN_BUDGET   — how long one sweep may run before it stops and says it
#     stopped. Exceeding it produces a PARTIAL certificate, never a partial
#     count wearing a total's name.
#   * REFRESH_WAIT  — how long the read that STARTED a sweep waits for it
#     before handing back the previous certificate's numbers marked
#     "refreshing". A read that finds a sweep ALREADY running never waits at
#     all.
#   * WATERMARK bounds — how much per-shard evidence one certificate may
#     carry before it starts eliding entries, and it says when it does.
SCAN_BUDGET_ENV = "SIGNAL_LEDGER_SCAN_BUDGET"
DEFAULT_SCAN_BUDGET_SECONDS = 120.0
REFRESH_WAIT_ENV = "SIGNAL_LEDGER_REFRESH_WAIT"
DEFAULT_REFRESH_WAIT_SECONDS = 5.0
WATERMARK_MAX_SHARDS_ENV = "SIGNAL_LEDGER_WATERMARK_MAX_SHARDS"
DEFAULT_WATERMARK_MAX_SHARDS = 2000
WATERMARK_WINDOW_DAYS_ENV = "SIGNAL_LEDGER_WATERMARK_WINDOW_DAYS"
DEFAULT_WATERMARK_WINDOW_DAYS = 30


def _positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return default
        if value > 0:
            return value
    return default


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return default
        if value > 0:
            return value
    return default


def scan_budget_seconds() -> float:
    return _positive_float_env(SCAN_BUDGET_ENV, DEFAULT_SCAN_BUDGET_SECONDS)


def refresh_wait_seconds() -> float:
    return _positive_float_env(REFRESH_WAIT_ENV, DEFAULT_REFRESH_WAIT_SECONDS)


def watermark_max_shards() -> int:
    return _positive_int_env(WATERMARK_MAX_SHARDS_ENV, DEFAULT_WATERMARK_MAX_SHARDS)


def watermark_window_days() -> int:
    return _positive_int_env(WATERMARK_WINDOW_DAYS_ENV, DEFAULT_WATERMARK_WINDOW_DAYS)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else default_sovereign_root()


def ledger_path(root: Path | None = None) -> Path:
    return _root(root) / "signals" / "ledger.jsonl"


def scan_marker_path(root: Path | None = None) -> Path:
    return _root(root) / "signals" / "last_scan.json"


def scan_lock_path(root: Path | None = None) -> Path:
    """The refresh lock's own file (R4).

    SEPARATE FROM THE CERTIFICATE, and the separation is the whole point.
    Round 3 flocked the certificate itself, so opening it for the lock CREATED
    it on a fresh root — which forced "empty certificate" to be read as "no
    certificate", which made a DESTROYED certificate indistinguishable from a
    store that had never been scanned. A lock is an artifact of coordination;
    a certificate is a claim about a scan. One file cannot be both without one
    of them lying.
    """
    return _root(root) / "signals" / "last_scan.lock"


def signal_id_for(source: str, native_id: str) -> str:
    raw = f"{source}:{native_id}".encode()
    return hashlib.sha256(raw).hexdigest()


class SourceRead:
    """What a *source* file gave us, and whether it gave us all of it.

    The old ``_parse_jsonl`` returned a bare list, so a file of pure garbage
    and an empty file were the same value. The reviewer's finding 3: a source
    containing ``{broken}`` became "honk count zero, status ok" — a
    fail-open, because the count was reported as measured when nothing was
    measured. ``status`` is what the scan marker and the heartbeat carry
    forward so a degraded source can never render as a healthy zero.
    """

    __slots__ = ("rows", "bad_lines", "status")

    def __init__(self, rows: list[dict], bad_lines: int, status: str) -> None:
        self.rows = rows
        self.bad_lines = bad_lines
        self.status = status


def _read_source_jsonl(path: Path) -> SourceRead:
    """Best-effort parse for *source* files. Reports what it could not read.

    Not for the ledger — the ledger is strict (``_parse_ledger_rows``).
    """
    if not path.exists():
        return SourceRead([], 0, "absent")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return SourceRead([], 0, f"unreadable:{exc.__class__.__name__}")
    rows: list[dict] = []
    bad = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(rec, dict):
            rows.append(rec)
        else:
            bad += 1
    return SourceRead(rows, bad, "ok" if bad == 0 else f"degraded:{bad} unparseable lines")


def _parse_jsonl(path: Path) -> list[dict]:
    """Rows only. Kept for callers that do not carry a status; prefer
    ``_read_source_jsonl`` so a partial read cannot report as complete."""
    return _read_source_jsonl(path).rows


def _native_id_text(value: Any) -> str | None:
    """A native id must be a scalar. ``["halt"]`` and ``{"a":1}`` are not.

    Returning None instead of stringifying is deliberate: ``str(["halt"])``
    would mint the signal id ``"['halt']"``, which is a stable-looking hash
    of a bug. The reviewer's finding 6 — a list/dict id raised TypeError
    *after* its signal had already been appended, so a rescan raised again
    forever while the heartbeat reported a healthy partial count.
    """
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _text_or_none(value: Any, limit: int = 400) -> str | None:
    """A short display string, or None. Never a repr of a non-string."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text:
        return None
    return text[:limit]


def _origin(
    source: str, native_id: str, *, claim_id: str | None = None, path: str | None = None
) -> dict:
    """Where this signal came from, carried on the row itself (review N1).

    ``claim_id`` is the load-bearing one: it is the key the protected
    designation index is folded by, so it is what lets a display boundary ask
    "may I show this?" at read time. ``path`` is the source file, relative to
    the sovereign root, so a human can find the record a row was minted from
    even when it has no claim id.

    Keys with no value are OMITTED rather than stored as null: a row that
    never had a claim id and a row whose claim id we lost should not look the
    same, and `"claim_id": null` reads as the second.
    """
    origin: dict[str, str] = {"source": source, "native_id": native_id}
    text_claim = _text_or_none(claim_id, limit=200)
    if text_claim:
        origin["claim_id"] = text_claim
    text_path = _text_or_none(path, limit=400)
    if text_path:
        origin["path"] = text_path
    return origin


def _validate_row(rec: Any) -> str | None:
    """None if the row is a usable ledger row, else why it is not.

    Reviewer finding 1: the old check was ``isinstance(rec, dict) and
    rec.get("signal_id")``. A complete JSON object holding only
    ``{"signal_id": "x"}`` therefore *overwrote* a valid open row and the
    signal vanished from the count with ``error=null``. Validating the row
    is what makes "healthy zero" mean measured-zero.
    """
    if not isinstance(rec, dict):
        return "not an object"
    for field in REQUIRED_ROW_FIELDS:
        if field not in rec:
            return f"missing {field}"
    sid = rec.get("signal_id")
    if not isinstance(sid, str) or not sid.strip():
        return "signal_id must be a non-empty string"
    source = rec.get("source")
    if not isinstance(source, str) or source not in SOURCES:
        return f"source must be one of {SOURCES}"
    owner = rec.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        return "owner must be a non-empty string"
    state = rec.get("state")
    if not isinstance(state, str) or state not in STATES:
        return f"state must be one of {STATES}"
    if _parse_dt(rec.get("produced_at")) is None:
        # An unparseable produced_at is not cosmetic: it keeps the signal
        # permanently outside BOTH stale counts, so the oldest unacked thing
        # in the house is the one thing the staleness gauge cannot see.
        return "produced_at is not an ISO-8601 timestamp"
    for optional in ("kind", "concern"):
        if optional in rec and rec[optional] is not None and not isinstance(rec[optional], str):
            return f"{optional} must be a string when present"
    # ORIGIN IS OPTIONAL FOR THE SAME REASON kind/concern ARE. Every row
    # written before 2026-09-06 lacks it, and a required field added late
    # turns the whole existing ledger corrupt on the next read.
    if "origin" in rec and rec["origin"] is not None:
        origin = rec["origin"]
        if not isinstance(origin, dict):
            return "origin must be an object when present"
        for key, value in origin.items():
            if not isinstance(key, str) or (value is not None and not isinstance(value, str)):
                return f"origin[{key!r}] must be a string or null"
    if state in CLOSE_STATES:
        reason = rec.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return f"{state} row needs a non-blank string reason"
        closed_by = rec.get("closed_by")
        if not isinstance(closed_by, str) or not closed_by.strip():
            return f"{state} row needs a non-blank closed_by"
        if _parse_dt(rec.get("closed_at")) is None:
            return f"{state} row needs an ISO-8601 closed_at"
    return None


def _read_ledger_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise LedgerUnreadable(f"encoding:{exc.__class__.__name__}") from exc
    except OSError as exc:
        raise LedgerUnreadable(f"unreadable:{exc.__class__.__name__}") from exc


def _parse_ledger_rows(text: str) -> list[Any]:
    """Strict on SYNTAX: one malformed or truncated line fails the whole ledger.

    Row *schema* is judged separately, by ``_validate_row`` during the fold —
    a schema-invalid row must not erase the valid history around it, so it is
    counted as corrupt rather than raised on. Syntax is different: a JSON
    parse failure means the file itself is not what it claims to be, and no
    part of it can be trusted to be complete.
    """
    rows: list[Any] = []
    for i, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LedgerUnreadable(f"malformed:line {i}") from exc
        rows.append(rec)
    return rows


class LedgerState:
    """The fold, plus everything the fold had to throw away.

    ``corrupt`` is the whole point. The reviewer's finding 1 named the exact
    fail-open: a structurally invalid row silently replaced a valid open
    signal and both the heartbeat and ``signals_summary`` reported a healthy
    zero. Corrupt rows now (a) never overwrite a valid prior row, (b) are
    counted, and (c) force a non-null ``error`` on every consumer.
    """

    __slots__ = ("latest", "corrupt", "rows_read")

    def __init__(self, latest: dict[str, dict], corrupt: list[dict], rows_read: int) -> None:
        self.latest = latest
        self.corrupt = corrupt
        self.rows_read = rows_read

    @property
    def corrupt_count(self) -> int:
        return len(self.corrupt)

    def error(self) -> str | None:
        if not self.corrupt:
            return None
        first = self.corrupt[0]
        return (
            f"corrupt_rows:{len(self.corrupt)} (first at line {first['line']}: {first['reason']})"
        )


def load_state(root: Path | None = None) -> LedgerState:
    """Fold the ledger, keeping corrupt rows visible instead of authoritative.

    Ordering is file order — the ledger is append-only, so the last VALID row
    for an id wins. A corrupt row is skipped for the fold and recorded; it
    can neither become the winner nor unseat the row that already is.
    """
    path = ledger_path(root)
    if not path.exists():
        return LedgerState({}, [], 0)
    latest: dict[str, dict] = {}
    corrupt: list[dict] = []
    rows = _parse_ledger_rows(_read_ledger_text(path))
    for i, rec in enumerate(rows, start=1):
        reason = _validate_row(rec)
        if reason is not None:
            corrupt.append(
                {
                    "line": i,
                    "reason": reason,
                    "signal_id": rec.get("signal_id") if isinstance(rec, dict) else None,
                }
            )
            continue
        latest[str(rec["signal_id"])] = rec
    return LedgerState(latest, corrupt, len(rows))


def load_latest(root: Path | None = None) -> dict[str, dict]:
    """signal_id -> latest VALID row. Raises LedgerUnreadable on bad syntax."""
    return load_state(root).latest


# ── THE FOLD A SWEEP CARRIES WITH IT, INSTEAD OF REBUILDING PER ROW ────────
#
# THIS IS WHERE THE 265 SECONDS WENT, and no amount of shard watermarking
# would have found it. `open_signal` re-read and re-folded the WHOLE ledger
# inside its append lock, once per source record, to answer one question:
# "have I seen this id?". Measured on the live corpus 2026-09-06: 8,765 rows
# / 6.09 MB is ~66 ms to fold, and `nape/honks.jsonl` holds 3,975 rows, so the
# honk source alone spent ~4,000 x 66 ms = ~260 s answering a dictionary
# lookup. `scan_honks`, `scan_proposals` and `scan_threads` each called
# `load_latest` a SECOND time per record on top of that.
#
# It is O(rows x records) and it is invisible in a profile of any one call,
# which is why it survived four reviews: every individual read is fast and
# correct.
#
# THE INDEX IS REFRESHED BY TAILING THE FILE, NOT BY BOOKKEEPING ITS OWN
# WRITES. An index that updated itself in memory on append would be a second
# model of the ledger that can silently disagree with the ledger; this one
# re-reads whatever bytes appeared since it last looked, so its only source of
# truth is still the file. It is refreshed INSIDE `open_signal`'s append lock,
# so a foreign writer's append is picked up on exactly the same schedule the
# old full re-read picked it up on — the check-and-append atomicity that
# reviewer finding 8 closed is untouched.
#
# ANY SURPRISE IS A FULL RELOAD. Shrinkage, a decode failure, a malformed
# line: every one of them falls back to `load_state`, so the errors a reader
# sees are byte-identical to the ones the unindexed path raised. The index is
# an accelerator, never a second implementation of the fold.
class _ScanIndex:
    """The ledger fold for the life of ONE sweep. Never crosses a sweep."""

    __slots__ = ("key", "root", "latest", "corrupt_count", "consumed", "loaded")

    def __init__(self, root: Path | None) -> None:
        self.root = _root(root)
        self.key = str(self.root)
        self.latest: dict[str, dict] = {}
        self.corrupt_count = 0
        self.consumed = 0
        self.loaded = False

    def matches(self, root: Path | None) -> bool:
        return str(_root(root)) == self.key

    def _fold(self, rows: list) -> None:
        for rec in rows:
            if _validate_row(rec) is not None:
                self.corrupt_count += 1
                continue
            self.latest[str(rec["signal_id"])] = rec

    def _full_reload(self) -> None:
        path = ledger_path(self.root)
        self.latest = {}
        self.corrupt_count = 0
        self.consumed = 0
        self.loaded = True
        if not path.exists():
            return
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LedgerUnreadable(f"encoding:{exc.__class__.__name__}") from exc
        self._fold(_parse_ledger_rows(text))
        self.consumed = len(data)

    def refresh(self) -> dict[str, dict]:
        """The current fold. Cheap when nothing was appended since last call."""
        path = ledger_path(self.root)
        if not self.loaded or not path.exists():
            self._full_reload()
            return self.latest
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise LedgerUnreadable(f"unreadable:{exc.__class__.__name__}") from exc
        if size == self.consumed:
            return self.latest
        if size < self.consumed:
            # THE FILE SHRANK UNDER US. Append-only says this cannot happen, so
            # trust the file and not the assumption: reload from scratch.
            self._full_reload()
            return self.latest
        try:
            with path.open("rb") as fh:
                fh.seek(self.consumed)
                chunk = fh.read(size - self.consumed)
        except OSError as exc:
            raise LedgerUnreadable(f"unreadable:{exc.__class__.__name__}") from exc
        cut = chunk.rfind(b"\n")
        if cut == -1:
            # A partial final line. Consume nothing; the next refresh sees it
            # whole. (Appends are one fsync'd line under flock, so this is the
            # foreign-writer-mid-write case, not our own.)
            return self.latest
        usable = chunk[: cut + 1]
        try:
            text = usable.decode("utf-8")
            rows = _parse_ledger_rows(text)
        except (UnicodeDecodeError, LedgerUnreadable):
            self._full_reload()
            return self.latest
        self._fold(rows)
        self.consumed += len(usable)
        return self.latest


_SCAN_INDEX: contextvars.ContextVar[_ScanIndex | None] = contextvars.ContextVar(
    "signal_ledger_scan_index", default=None
)


@contextlib.contextmanager
def _scan_index(root: Path | None):
    """Install a fold for the duration of one sweep.

    A ContextVar rather than a parameter threaded through nine functions:
    `open_signal` and `ack_signal` are public and are called directly by
    callers that have no sweep, and a new keyword on each of them would be a
    second way to get the fold wrong. It is reset in a finally, so no sweep
    can leak its index into the next one, and a worker thread starts with its
    own context so two sweeps cannot share one.
    """
    index = _ScanIndex(root)
    token = _SCAN_INDEX.set(index)
    try:
        yield index
    finally:
        _SCAN_INDEX.reset(token)


def _latest_view(root: Path | None = None) -> dict[str, dict]:
    """`load_latest`, served from the sweep's index when one is installed."""
    index = _SCAN_INDEX.get()
    if index is not None and index.matches(root):
        return index.refresh()
    return load_latest(root)


# ── scan marker ─────────────────────────────────────────────────────────────
#
# A marker is a CLAIM about a scan, not evidence of one. Reviewer finding 2:
# ``{"bogus": true}`` certified an empty ledger, and deleting the ledger after
# a scan moved the total from 1 to 0 with ``error=null, ingestion="ok"``. So
# the marker is validated like any other persisted row, and it is reconciled
# against the ledger rather than trusted over it.

MARKER_REQUIRED = (
    "scanned_at",
    "counts",
    "source_status",
    # ── CUMULATIVE INTEGRITY EVIDENCE, added 2026-09-06 (review F1) ─────────
    #
    # The reviewed marker carried only per-scan deltas: `counts` is "opened by
    # THIS scan". So an ordinary zero-delta rescan — the common case, nothing
    # new — wrote counts of all zeros, and the reconciliation that was supposed
    # to detect loss ("did the ledger shrink below what the scan claims?")
    # became `0 > distinct`, which is false for every possible ledger. Truncate
    # the ledger to zero bytes after that rescan and the heartbeat answered
    # error:null, ingestion:"ok", total:0 for a ledger whose contents were gone.
    # A detector whose sensitivity decays to zero on the ordinary path is not a
    # detector.
    #
    # These three are about the WHOLE ledger as it stood when the scan
    # finished, so they do not decay: they are re-verifiable against the file
    # at any later read, by any reader, with no scan history.
    "ledger_bytes",
    "ledger_rows",
    "ledger_sha256",
    "max_age_seconds",
)


def _validate_marker(rec: Any) -> str | None:
    if not isinstance(rec, dict):
        return "not an object"
    for field in MARKER_REQUIRED:
        if field not in rec:
            return f"missing {field}"
    if _parse_dt(rec.get("scanned_at")) is None:
        return "scanned_at is not an ISO-8601 timestamp"
    counts = rec.get("counts")
    if not isinstance(counts, dict):
        return "counts must be an object"
    for key, value in counts.items():
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int):
            return f"counts[{key!r}] must be an integer"
        if value < 0:
            return f"counts[{key!r}] must not be negative"
    status = rec.get("source_status")
    if not isinstance(status, dict):
        return "source_status must be an object"
    for key, value in status.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            return f"source_status[{key!r}] must be a non-empty string"
    # EVERY SOURCE, OR IT IS NOT A COMPLETED SCAN. Review F1: `_validate_marker`
    # accepted empty `counts` / `source_status` maps, and an empty map plus an
    # empty ledger produces zero counts and no error — a marker that certifies
    # a scan of nothing as a healthy scan of everything. `scan_all` always
    # writes all seven sources, so a marker missing one did not come from a
    # completed scan and must not be read as one.
    missing_counts = [srcname for srcname in SOURCES if srcname not in counts]
    if missing_counts:
        return f"counts is not a completed scan: missing {missing_counts}"
    missing_status = [srcname for srcname in SOURCES if srcname not in status]
    if missing_status:
        return f"source_status is not a completed scan: missing {missing_status}"
    for field in ("ledger_bytes", "ledger_rows"):
        value = rec.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return f"{field} must be a non-negative integer"
    sha = rec.get("ledger_sha256")
    if not isinstance(sha, str) or len(sha) != 64:
        return "ledger_sha256 must be a 64-character hex digest"
    max_age = rec.get("max_age_seconds")
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
        return "max_age_seconds must be a positive integer"
    # ── THE 2026-09-06 FIELDS ARE OPTIONAL ON READ, AND THAT IS A DEPLOY
    # ── REQUIREMENT, NOT A STYLE CHOICE ────────────────────────────────────
    # The certificate on the live box right now carries exactly the eight keys
    # above. Adding `watermarks` or `partial` to MARKER_REQUIRED would make
    # that file `marker_invalid` the moment this release lands, and
    # `_refresh_decision` answers marker_invalid with REFUSE plus a quarantine
    # instruction — i.e. the release would hand Anthony a recovery procedure
    # for an undamaged ledger. Same rule, same reason, as `origin` on a ledger
    # row: a required field added late turns the existing store corrupt on the
    # next read.
    #
    # Absent watermarks simply mean "nothing is known to be unchanged", which
    # degrades to the full sweep this release is replacing. Present-and-broken
    # is a different fact and is refused, because a watermark we cannot parse
    # is a claim about what was read that we cannot check.
    watermarks = rec.get("watermarks")
    if watermarks is not None:
        if not isinstance(watermarks, dict):
            return "watermarks must be an object when present"
        for src_name, block in watermarks.items():
            if src_name not in SOURCES:
                return f"watermarks[{src_name!r}] is not a source"
            if not isinstance(block, dict):
                return f"watermarks[{src_name!r}] must be an object"
            digest = block.get("digest")
            if not isinstance(digest, str) or len(digest) != 64:
                return f"watermarks[{src_name!r}].digest must be a 64-character hex digest"
            shards = block.get("shards")
            if not isinstance(shards, list):
                return f"watermarks[{src_name!r}].shards must be a list"
            for entry in shards:
                if not isinstance(entry, dict):
                    return f"watermarks[{src_name!r}] has a shard entry that is not an object"
                if not isinstance(entry.get("path"), str) or not entry["path"].strip():
                    return f"watermarks[{src_name!r}] has a shard with no path"
                for numeric in ("size", "mtime_ns", "rows"):
                    value = entry.get(numeric)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        return (
                            f"watermarks[{src_name!r}] shard {entry['path']!r} has a bad {numeric}"
                        )
    partial = rec.get("partial")
    if partial is not None and not isinstance(partial, bool):
        return "partial must be a boolean when present"
    unreached = rec.get("unreached")
    if unreached is not None and (
        not isinstance(unreached, list) or any(not isinstance(u, str) for u in unreached)
    ):
        return "unreached must be a list of strings when present"
    if partial and not unreached:
        # A PARTIAL CERTIFICATE THAT CANNOT NAME WHAT IT MISSED is worse than
        # no certificate: it publishes the flag that nulls every aggregate
        # while leaving the reader no way to know what is still owed.
        return "a partial certificate must name what it did not reach"
    return None


def _read_scan_marker(root: Path | None = None) -> tuple[dict | None, str | None]:
    """(marker, error). A marker that fails validation is NOT a marker."""
    path = scan_marker_path(root)
    if not path.exists():
        return None, None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"marker_unreadable:{exc.__class__.__name__}"
    if not raw.strip():
        # A ZERO-BYTE CERTIFICATE IS INVALID, NOT ABSENT (R4). Round 3 read it
        # as absent, and had a reason: the refresh lock flocked this same path,
        # so opening it for the lock created an empty file on a fresh root, and
        # calling that invalid would have made the first ever read an error.
        # The reviewer showed what it cost — blank the certificate of a scanned
        # store and the next read RECERTIFIES it silently; blank the ledger too
        # and the answer is `error:null, ingestion:"ok", total:0` for a store
        # whose signal is gone.
        #
        # The lock now lives on its own sidecar path, so the two facts are no
        # longer entangled: NO FILE AT ALL is a fresh root, and a file that
        # exists and claims nothing is a certificate that was destroyed.
        return None, "marker_invalid:the certificate exists but is empty"
    try:
        rec = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"marker_unreadable:{exc.__class__.__name__}"
    reason = _validate_marker(rec)
    if reason is not None:
        return None, f"marker_invalid:{reason}"
    return rec, None


def _ledger_integrity(root: Path | None = None) -> tuple[int, int, str]:
    """(bytes, non-blank rows, sha256 of exactly those bytes) for the ledger.

    THE HASH IS OVER A PREFIX, AND THAT IS THE WHOLE DESIGN. The ledger is
    append-only and a watch seat legitimately appends an ack a second after a
    scan, so a whole-file digest recorded at scan time would mismatch on the
    very next honest write and cry loss on every acknowledgement. What the scan
    can certify is the file AS IT STOOD: the first ``ledger_bytes`` bytes
    hashing to ``ledger_sha256``. A later read re-hashes exactly that prefix.
    Appends are invisible to the check, which is correct; truncation and any
    rewrite of history are not, which is the point.

    A tail-only digest — the sha of the last row — was the tempting cheaper
    version and does not work: it survives a file whose entire head was cut
    away, which is the loss shape this exists to catch.
    """
    path = ledger_path(root)
    if not path.exists():
        return 0, 0, hashlib.sha256(b"").hexdigest()
    data = path.read_bytes()
    rows = sum(1 for line in data.decode("utf-8", "replace").splitlines() if line.strip())
    return len(data), rows, hashlib.sha256(data).hexdigest()


def _check_marker_integrity(marker: dict, root: Path | None = None) -> str | None:
    """Re-verify the WHOLE ledger against a completed scan's snapshot.

    Called on every read, not only after a scan that opened something — review
    F1's reproduction is precisely a *zero-delta* rescan followed by a
    truncation, i.e. the path where the old per-scan reconciliation had nothing
    to say.
    """
    claimed_bytes = marker.get("ledger_bytes")
    claimed_sha = marker.get("ledger_sha256")
    claimed_rows = marker.get("ledger_rows")
    path = ledger_path(root)
    if not path.exists():
        return "ledger_missing"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"unreadable:{exc.__class__.__name__}"
    if len(data) < claimed_bytes:
        return f"ledger_truncated:marker certified {claimed_bytes} bytes, file holds {len(data)}"
    prefix_sha = hashlib.sha256(data[:claimed_bytes]).hexdigest()
    if prefix_sha != claimed_sha:
        return (
            "ledger_rewritten:the bytes the last scan certified no longer hash "
            f"to {claimed_sha[:12]}…"
        )
    # ── ledger_rows IS PART OF THE CONTRACT, SO IT GETS CHECKED (review N6) ─
    # `_validate_marker` only asked that it be a non-negative integer, so a
    # marker claiming 21 rows over a one-row ledger stayed healthy through all
    # three readers. A field advertised as cumulative evidence and never
    # reconciled is worse than an absent one: it reads as a second check and
    # is not one. Either check it or drop it from MARKER_REQUIRED; this checks
    # it.
    #
    # STRICTLY LESS-THAN, NEVER EQUALITY. The ledger is append-only and a
    # watch seat legitimately acks a second after a scan, so `actual > claimed`
    # is the ordinary honest case. Only a SHRINK is loss.
    if isinstance(claimed_rows, int) and not isinstance(claimed_rows, bool):
        # WITHIN THE CERTIFIED PREFIX, AND EXACTLY (R5). Round 3 counted the
        # WHOLE current file and only refused a shortfall, which broke both
        # ways: an understated count was accepted outright, and a legitimate
        # ack appended after the scan could push the whole-file count up past
        # an overstated claim and mask it. The prefix is the only region the
        # certificate speaks about — `ledger_bytes` and `ledger_sha256`
        # already pin it — and inside it the row count is a fact with exactly
        # one correct value. Rows appended beyond the prefix stay legitimate
        # and are not counted here; that is what makes an append-only ledger
        # checkable at all.
        prefix_rows = sum(
            1
            for line in data[:claimed_bytes].decode("utf-8", "replace").splitlines()
            if line.strip()
        )
        if prefix_rows != claimed_rows:
            return (
                f"ledger_rows_mismatch:marker certified {claimed_rows} rows in its "
                f"first {claimed_bytes} bytes, those bytes hold {prefix_rows}"
            )
    return None


def _marker_staleness(marker: dict, now: datetime | None = None) -> str | None:
    """None if the scan is inside its own declared freshness bound."""
    scanned = _parse_dt(marker.get("scanned_at"))
    if scanned is None:
        return "scan_marker_unparseable"
    bound = marker.get("max_age_seconds") or marker_max_age_seconds()
    age = (now or datetime.now(timezone.utc)) - scanned
    if age.total_seconds() > bound:
        return (
            f"scan_stale:last scan {int(age.total_seconds())}s ago exceeds the "
            f"marker's own {bound}s freshness bound"
        )
    return None


def _write_scan_marker(
    counts: dict,
    source_status: dict,
    root: Path | None = None,
    *,
    watermarks: dict | None = None,
    partial: bool = False,
    unreached: list[str] | None = None,
    budget_seconds: float | None = None,
    scan_seconds: float | None = None,
) -> dict:
    """ONE WRITER FOR BOTH THE COMPLETE AND THE PARTIAL CERTIFICATE.

    A second writer for partial scans is how `ledger_bytes` / `ledger_rows` /
    `ledger_sha256` come to be computed two ways and disagree — and those three
    are the only cumulative loss evidence there is. A partial certificate is
    the same certificate with `partial` set and the unreached sources named.
    """
    path = scan_marker_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # MATERIALISE THE LEDGER BEFORE THE MARKER EXISTS. Without this, a scan
    # that legitimately opened nothing left no ledger file, so "scanned and
    # empty" and "scanned, then the ledger was lost" were the same two facts
    # on disk: a marker and no ledger. They are now distinguishable, which is
    # what lets an absent-ledger-after-a-scan be an unconditional error
    # instead of a judgement call the reader has to get right.
    lpath = ledger_path(root)
    lpath.parent.mkdir(parents=True, exist_ok=True)
    if not lpath.exists():
        lpath.touch()
    # MEASURE AFTER MATERIALISING, WRITE THE MARKER LAST. A scan that opened
    # nothing legitimately leaves a zero-byte ledger, whose integrity evidence
    # is (0, 0, sha256 of empty) — a real snapshot of a real empty file, not a
    # special case. That is what keeps "scanned and empty" distinguishable from
    # "scanned, then the ledger was lost": the latter has no file at all, and
    # _check_marker_integrity calls it ledger_missing unconditionally.
    ledger_bytes, ledger_rows, ledger_sha = _ledger_integrity(root)
    rec: dict[str, Any] = {
        "scanned_at": _now(),
        "counts": counts,
        "source_status": source_status,
        "ledger_bytes": ledger_bytes,
        "ledger_rows": ledger_rows,
        "ledger_sha256": ledger_sha,
        "max_age_seconds": marker_max_age_seconds(),
        "watermarks": watermarks or {},
        "partial": bool(partial),
        "unreached": list(unreached or []),
    }
    if budget_seconds is not None:
        rec["scan_budget_seconds"] = float(budget_seconds)
    if scan_seconds is not None:
        rec["scan_seconds"] = round(float(scan_seconds), 3)
    path.write_text(json.dumps(rec, sort_keys=True) + "\n", encoding="utf-8")
    return rec


def _append(row: dict, root: Path | None = None) -> dict:
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(row, sort_keys=True) + "\n"
    with path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    return row


@contextlib.contextmanager
def _append_lock(root: Path | None = None):
    """Hold the ledger's append lock across a read-then-write decision.

    Reviewer finding 8: ``open_signal`` checked existence and only THEN
    called ``_append``, which took the lock. Two processes crossing between
    those two steps each saw "absent" and each appended, so one signal got
    two open events. Check-and-append now happen inside one lock hold.
    """
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield fh
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _append_locked(fh, row: dict) -> dict:
    fh.write(json.dumps(row, sort_keys=True) + "\n")
    fh.flush()
    os.fsync(fh.fileno())
    return row


def _row(
    *,
    signal_id: str,
    source: str,
    produced_at: str,
    owner: str,
    state: str,
    reason: str | None,
    closed_by: str | None,
    closed_at: str | None,
    updated_at: str,
    kind: str | None = None,
    concern: str | None = None,
    origin: dict | None = None,
) -> dict:
    """One ledger row.

    ``kind`` and ``concern`` are the WHAT, added 2026-09-06 for the honk fold
    (review F3). The reviewer's exact objection: the only surviving signal
    reader returned aggregates with no ids and no bodies, so the replacement
    for the retired honk reader could not supply the identifier its own ack
    tool needs, let alone tell a watch seat what it was acking. A queue you
    cannot read is not a queue.

    ``origin`` is the WHERE (review N1), added the same day: the source, the
    native id, and the claim id or shard path the row was minted from. The
    scanner used to discard the input record's ``claim_id`` the moment it had
    copied the observation text, so a honk quoting a DESIGNATED PROTECTED
    RECORD landed in the ledger as bare prose with nothing left to check it
    against — and list mode published the body. Provenance is what lets the
    designation be applied at READ time, against the index as it stands then,
    rather than only at scan time against the index as it stood once.

    They are DELIBERATELY OPTIONAL and outside ``REQUIRED_ROW_FIELDS``: every
    row written before today lacks them, and a required field added late turns
    the entire existing ledger corrupt on the next read — which is the failure
    this module exists to prevent, self-inflicted.
    """
    row = {
        "signal_id": signal_id,
        "source": source,
        "produced_at": produced_at,
        "owner": owner,
        "state": state,
        "reason": reason,
        "closed_by": closed_by,
        "closed_at": closed_at,
        "updated_at": updated_at,
    }
    if kind is not None:
        row["kind"] = kind
    if concern is not None:
        row["concern"] = concern
    if origin is not None:
        row["origin"] = origin
    return row


def open_signal(
    *,
    source: str,
    native_id: str,
    produced_at: str,
    owner: str = "watch-2/3",
    root: Path | None = None,
    kind: str | None = None,
    concern: str | None = None,
    origin_claim_id: str | None = None,
    origin_path: str | None = None,
) -> dict | None:
    """Idempotent open: skip if a row already exists for this id.

    ``owner`` here is ASSIGNMENT — whose queue this lands in. It is not, and
    must never become, the identity of whoever later closes it; see
    ``ack_signal``.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    native = _native_id_text(native_id)
    if native is None:
        raise ValueError(f"native_id must be a non-empty scalar, got {type(native_id).__name__}")
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("owner must be a non-empty string")
    sid = signal_id_for(source, native)
    now = _now()
    if _parse_dt(produced_at) is None:
        # Never persist an unparseable produced_at: _validate_row would
        # rightly call the resulting row corrupt, and the corruption would
        # have been minted by our own writer.
        produced_at = now
    origin = _origin(source, native, claim_id=origin_claim_id, path=origin_path)
    with _append_lock(root) as fh:
        # THE FOLD, NOT A RE-FOLD. `_latest_view` is `load_latest` outside a
        # sweep and the sweep's tailed index inside one; either way it is read
        # here, under the append lock, exactly as before.
        existing = _latest_view(root).get(sid)
        if existing is not None:
            # ── PROVENANCE BACKFILL (R1) ────────────────────────────────────
            # INGESTION IS IDEMPOTENT ON STATE, NOT ON PROVENANCE. A signal
            # opened before origin existed — every row written before this
            # release — never gained one, because the only thing that wrote
            # origin was the branch that opens a NEW signal. So a rescan of a
            # honk that still carries its claim id left the row unprovenanced
            # forever, and R1 then withholds its concern forever: the fix for
            # the exposure would have permanently blinded the queue instead.
            #
            # Narrow on purpose. It fires only when the source can supply a
            # claim id the stored row lacks, i.e. only when it changes the
            # provenance verdict. State, reason, closer and close time are
            # copied through untouched — this is a provenance write, never a
            # lifecycle one, and an append that quietly reopened an acked
            # signal would be N7 all over again.
            if origin.get("claim_id") and not _row_origin_claim(existing):
                merged = dict(existing.get("origin") or {})
                merged.update(origin)
                _append_locked(fh, dict(existing, origin=merged, updated_at=now))
            return None
        return _append_locked(
            fh,
            _row(
                signal_id=sid,
                source=source,
                produced_at=produced_at,
                owner=owner.strip(),
                state="open",
                reason=None,
                closed_by=None,
                closed_at=None,
                updated_at=now,
                kind=_text_or_none(kind),
                concern=_text_or_none(concern),
                origin=origin,
            ),
        )


def _normalise_actor(actor: Any) -> str:
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor must be a non-empty string")
    text = actor.strip()
    # A NAMESPACE WITH NOTHING AFTER IT IS NOT AN IDENTITY. The reviewed
    # dispatch built `f"seat:{spiral_state.session_id}"` unconditionally, so a
    # missing session produced the literal closers "seat:None" and "seat:" and
    # both were accepted as authorship (review F5). Refused here as well as at
    # the dispatch, because this is the layer that writes the row.
    if text.endswith(":") or text.casefold() in ("seat:none", "seat:null", "none", "null"):
        raise ValueError(f"actor is not an identity: {actor!r}")
    return text


def _actor_identity(actor: str) -> str:
    """The identity inside a namespaced actor label. ``seat:daemon`` -> ``daemon``.

    THIS IS WHAT MAKES PRODUCER SEPARATION COMPARE LIKE WITH LIKE (review F5).
    Producer labels are bare (``daemon``, ``nape``, ``watchman``); seat labels
    are namespaced. Comparing the two whole strings meant `seat:daemon` never
    equalled `daemon`, so the check could not refuse the one case it exists
    for — the reviewer closed a halt as `seat:daemon` through the real
    dispatcher with `ok:true`. Stripping one leading namespace before the
    comparison is what makes the two comparable at all.
    """
    head, sep, tail = actor.partition(":")
    if sep and tail.strip():
        return tail.strip()
    return actor


def ack_signal(
    signal_id: str,
    actor: str,
    state: str,
    reason: str | None,
    root: Path | None = None,
) -> dict:
    """Close or acknowledge one signal.

    ``actor`` IS THE CALLER IDENTITY THE SERVER HOLDS, never a string the
    caller chose. Reviewer finding 4: producer separation used to compare the
    fixed producer name against the caller-supplied ``owner`` and then copy
    that same input into ``closed_by``, so ``owner="daemon"`` was refused
    while ``owner="watch-2/3"`` or ``owner="Daemon"`` sailed through — a
    caller with tool access could claim to be a different closer, and the
    refusal was a spelling check rather than an authorisation. The tool
    surface no longer accepts a closer at all (see ``handle_signal_tool``);
    the dispatch layer supplies the identity it resolved.

    ``owner`` (assignment) is carried forward from the row being closed. The
    two were one field and are now two facts.
    """
    if state not in CLOSE_STATES:
        raise ValueError(f"state must be one of {CLOSE_STATES}, got {state!r}")
    actor = _normalise_actor(actor)
    # A reason must be a non-blank STRING. str()-coercion used to land
    # `{"reason": ""}` as the literal text "{'reason': ''}" — an audit trail
    # made of Python reprs (reviewer finding 9).
    if reason is not None and not isinstance(reason, str):
        raise ValueError("reason must be a string")
    if state in ("acted", "dismissed") and not (reason and reason.strip()):
        raise ValueError(f"{state} requires a reason")
    if state == "acknowledged" and not (reason and reason.strip()):
        reason = "acknowledged"
    prev = _latest_view(root).get(signal_id)
    if not prev:
        raise KeyError(f"unknown signal_id {signal_id}")
    producer = SOURCE_PRODUCER.get(prev.get("source", ""), "")
    if producer:
        candidates = {actor.casefold(), _actor_identity(actor).casefold()}
        if producer.casefold() in candidates:
            raise PermissionError(
                f"producer cannot close its own signal: {actor!r} is the "
                f"{prev.get('source')!r} producer {producer!r}"
            )
    now = _now()
    return _append(
        _row(
            signal_id=signal_id,
            source=prev["source"],
            produced_at=prev.get("produced_at") or now,
            owner=prev.get("owner") or "watch-2/3",
            state=state,
            reason=reason.strip(),
            closed_by=actor,
            closed_at=now,
            updated_at=now,
            # CARRIED FORWARD, not re-derived. The ledger is append-only and
            # the latest row wins, so a close that dropped `kind`/`concern`
            # would erase what the signal was ABOUT at the moment it was
            # acted on — leaving an audit trail of ids with no bodies.
            #
            # `origin` travels with them for a sharper reason: it is what the
            # display boundary consults to decide whether the concern may be
            # shown at all. A close that dropped it would turn an acked
            # protected-derived signal into an unprovenanced one, i.e. the
            # withholding would silently stop applying on exactly the rows a
            # human has already looked at (review N1).
            kind=_text_or_none(prev.get("kind")),
            concern=_text_or_none(prev.get("concern")),
            origin=prev.get("origin") if isinstance(prev.get("origin"), dict) else None,
        ),
        root,
    )


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


# ── CONFIGURATION AFFIRMS APPLICABILITY; ABSENCE NEVER DOES ────────────────
#
# Review judgment (2): "explicitly not configured should be distinguishable
# from configured but unreadable... Configuration must affirm applicability;
# missing files, None, exceptions, or permission failures must never be used
# to infer 'not configured'."
#
# THAT SENTENCE IS THE WHOLE DESIGN, AND IT RUNS IN ONE DIRECTION ONLY. A
# source is applicable unless a human has WRITTEN DOWN that it is not. An
# absent config file means every source is in scope. An unreadable one means
# we do not know what was declared — which excuses nothing and is reported as
# an error, because the alternative is a corrupt file quietly shrinking the
# denominator.
#
# Shape of <root>/signals/sources.json:
#     {"sources": {"guardian": "not_configured", "proposal": "configured"}}

SOURCE_CONFIG_FILENAME = "sources.json"
NOT_CONFIGURED = "not_configured"


def source_config_path(root: Path | None = None) -> Path:
    return _root(root) / "signals" / SOURCE_CONFIG_FILENAME


def _declared_not_configured(root: Path | None = None) -> tuple[set[str], str | None]:
    """(sources a human declared inapplicable, error). Absent file -> empty set."""
    path = source_config_path(root)
    if not path.exists():
        return set(), None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return set(), f"source_config_unreadable:{exc.__class__.__name__}"
    if not isinstance(rec, dict) or not isinstance(rec.get("sources"), dict):
        return set(), "source_config_malformed:expected {'sources': {name: status}}"
    declared = set()
    for name, value in rec["sources"].items():
        if name not in SOURCES:
            return set(), f"source_config_malformed:{name!r} is not a source"
        if value == NOT_CONFIGURED:
            declared.add(name)
        elif value != "configured":
            return set(), (
                f"source_config_malformed:{name!r} must be 'configured' or "
                f"{NOT_CONFIGURED!r}, got {value!r}"
            )
    return declared, None


def _source_status(root: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """(status per source, sources that are NOT reporting a measured count).

    An unavailable source renders as ``None``, never ``0``. The distinction
    is the whole of reviewer finding 3: an absent guardian became a healthy
    guardian zero on the heartbeat, so the panel that exists to notice a
    problem was quietest exactly when it could not see.

    ``unknown`` — no marker at all, or a marker predating a source — is
    UNMEASURED, not ok. It used to be waved through by the heartbeat's
    ``not in ("ok", "unknown")`` test, which is how "nobody has ever scanned"
    rendered as seven healthy zeros.
    """
    marker, _err = _read_scan_marker(root)
    declared = (marker or {}).get("source_status") or {}
    status = {s: str(declared.get(s, "unknown")) for s in SOURCES}
    degraded = [s for s, v in status.items() if v != "ok"]
    return status, degraded


def summarize(root: Path | None = None, *, now: datetime | None = None) -> dict:
    """Fold the ledger into counts — with NULLS where nothing was measured.

    Review F2: this function returned numbers built only from the rows it
    could read, and every consumer then published those numbers as the
    answer. A source that could not be read, a source nothing had ever
    scanned, and a source with genuinely nothing open all produced ``0``.
    Anthony's contract for this field is "never report zero on error", and a
    zero that means three different things cannot honour it.

    So the nulls are produced HERE, at the fold, rather than patched on by
    each caller. The reviewer found the tool result rebuilding its own numbers
    from this function and discarding the heartbeat's nulls on the way out;
    that is only possible while the honest shape lives downstream of the fold
    instead of inside it.
    """
    now = now or datetime.now(timezone.utc)
    state = load_state(root)
    latest = state.latest
    source_status, degraded = _source_status(root)
    by_source: dict[str, dict[str, Any]] = {
        s: {"open": 0, "stale_24h": 0, "stale_7d": 0, "oldest_unacked": None} for s in SOURCES
    }
    total = stale_24h = stale_7d = 0
    for rec in latest.values():
        if rec.get("state") != "open":
            continue
        src = rec.get("source")
        if src not in by_source:
            by_source[src] = {"open": 0, "stale_24h": 0, "stale_7d": 0, "oldest_unacked": None}
        total += 1
        by_source[src]["open"] += 1
        dt = _parse_dt(rec.get("produced_at"))
        if dt:
            age = now - dt
            oldest = by_source[src]["oldest_unacked"]
            iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            if oldest is None or iso < oldest:
                by_source[src]["oldest_unacked"] = iso
            if age >= timedelta(days=7):
                stale_7d += 1
                by_source[src]["stale_7d"] += 1
            if age >= timedelta(days=1):
                stale_24h += 1
                by_source[src]["stale_24h"] += 1

    # ── WHAT WAS NOT MEASURED, AND THEREFORE CANNOT BE COUNTED ─────────────
    corrupt = state.corrupt_count
    unmeasured = sorted(degraded)
    # WHAT IS IN SCOPE AT ALL, which is a different question (judgment 2).
    # `not_configured` is still UNMEASURED — it contributes an unknown number
    # of open signals to `total`, not zero of them — so `total` stays null.
    # `total_configured` answers the narrower question and says, in
    # `total_configured_scope`, exactly which sources it covers.
    not_configured = sorted(s for s in SOURCES if source_status.get(s) == NOT_CONFIGURED)
    _declared, config_error = _declared_not_configured(root)
    configured = [s for s in SOURCES if source_status.get(s) != NOT_CONFIGURED]
    configured_blind = bool(corrupt) or any(source_status.get(s) != "ok" for s in configured)
    for src, facts in by_source.items():
        # A source whose read failed contributes an unknown number of open
        # signals, not zero of them.
        if source_status.get(src, "unknown") != "ok" or corrupt:
            facts["open"] = None
            facts["stale_24h"] = None
            facts["stale_7d"] = None
    # CORRUPT ROWS BLIND EVERY SOURCE, not just their own. A row rejected for
    # `source must be one of ...` has no trustworthy source field to attribute
    # the loss to — deciding which bucket it would have fallen in requires
    # believing the row this validator just refused to believe.
    blind = bool(unmeasured) or bool(corrupt)
    total_configured = None
    if not configured_blind:
        total_configured = sum(
            rec.get("state") == "open" and rec.get("source") in configured
            for rec in latest.values()
        )
    return {
        "total": None if blind else total,
        # THE CONFIGURED-SCOPE AGGREGATE, WITH ITS SCOPE ATTACHED. A number
        # whose denominator is invisible is the fail-open one level up: it
        # looks like `total` and means something narrower. A configured source
        # that FAILED still nulls it — not-configured and could-not-read are
        # different facts and only the first one shrinks the scope.
        "total_configured": None if config_error else total_configured,
        "total_configured_scope": configured,
        "not_configured": not_configured,
        "config_error": config_error,
        "stale_24h": None if blind else stale_24h,
        "stale_7d": None if blind else stale_7d,
        # THE FLOOR, NAMED AS A FLOOR. `total` is the honest answer to "how
        # many open signals are there" and is null whenever that cannot be
        # answered. These three are the answer to a different, narrower
        # question — "how many did the rows I could read account for" — and
        # they are only safe because their name says so. Publishing a floor
        # under the name `total` is precisely the fail-open being closed here.
        "open_measured": total,
        "stale_24h_measured": stale_24h,
        "stale_7d_measured": stale_7d,
        "by_source": by_source,
        "corrupt_rows": corrupt,
        "corrupt": state.corrupt[:10],
        "source_status": source_status,
        "sources_degraded": degraded,
        "unmeasured": unmeasured,
    }


def _blind_field(
    error: str,
    ingestion: str,
    scanned_at: str | None = None,
    refresh_started_at: str | None = None,
) -> dict:
    """The shape for "we could not measure ANYTHING". Every count is None.

    ``measured: False`` is the flag that separates this from a PARTIAL read.
    They are different facts and the tool surface has to answer them
    differently: a ledger that cannot be read at all is a refusal, while a
    ledger that was read with one source unavailable still has rows a watch
    seat must be able to list and ack — it just has no honest TOTAL. Collapsing
    the two into one boolean is how "the guardian probe is down" would come to
    mean "you may not see your honks".
    """
    return {
        "error": error,
        "ingestion": ingestion,
        "scanned_at": scanned_at,
        # WHEN THE ANSWER IS ALREADY BEING REPLACED. Null on every read that
        # has no sweep behind it, so a reader can tell "there is no number and
        # nobody is working on it" from "there is no number YET".
        "refresh_started_at": refresh_started_at,
        "partial": False,
        "unreached": None,
        "measured": False,
        "total": None,
        "total_configured": None,
        "total_configured_scope": None,
        "not_configured": None,
        "by_source_detail": None,
        "stale_24h": None,
        "stale_7d": None,
        "by_source": None,
        "corrupt_rows": None,
        "source_status": None,
        "sources_degraded": None,
    }


def heartbeat_field(root: Path | None = None, *, scan: bool = False) -> dict:
    """Heartbeat/dashboard payload. Unreadable, unscanned, stale, or partially
    corrupt is NEVER a healthy zero.

    ``scan=True`` runs ingestion first (review F4: "ingestion must run on
    read"). It is OPT-IN rather than the default because
    ``dashboard_web.build_snapshot`` calls this on a 3-second console poll,
    and a full source sweep on that clock would be a write amplification, not
    a health check. The two callers the review named — the native heartbeat
    tool and ``signals_summary`` — pass it.

    This function MUST NOT RAISE. ``dashboard_web.build_snapshot`` calls it
    with no section guard, so an exception here takes the whole console down
    — the reviewer reproduced exactly that with a ledger row whose ``source``
    was a list. The broad ``except Exception`` at the tail is deliberate and
    is the reason the row validator can afford to be strict.
    """
    try:
        if scan:
            scan_error = ensure_scanned(root)
            if scan_error:
                # FIRST, BEFORE EVERY OTHER JUDGEMENT. The marker on disk may
                # still be fresh and intact from the previous sweep, and
                # serving its counts would publish the last successful scan's
                # health as this one's — "stale but ok", the shape review F4
                # names. Nothing downstream is trustworthy once ingestion has
                # failed, so nothing downstream gets to speak.
                marker_now, _ = _read_scan_marker(root)
                return _blind_field(
                    f"scan_failed:{scan_error}", "error", (marker_now or {}).get("scanned_at")
                )
        path = ledger_path(root)
        marker, marker_error = _read_scan_marker(root)
        if marker_error:
            # A marker we cannot trust is not a marker; it certifies nothing.
            return _blind_field(marker_error, "marker_error")
        if not path.exists():
            if marker:
                # Marker present, ledger gone. Since _write_scan_marker
                # materialises the ledger, "scanned and empty" always leaves a
                # zero-byte file behind. An absent one after a scan therefore
                # means the ledger was LOST, unconditionally — no count.
                return _blind_field("ledger_missing", "error", marker.get("scanned_at"))
            return _blind_field("not_scanned", "never")
        # LOAD BEFORE JUDGING THE MARKER. A malformed ledger is an ERROR
        # whether or not a scan ever ran, and answering "not_scanned" for a
        # file full of broken JSON tells the reader the wrong thing to go fix.
        state = load_state(root)
        refreshing = refresh_in_flight(root)
        if marker is None:
            # A ledger with no completed scan behind it. The rows are real, but
            # nothing certifies that the SOURCES were read, so no count here is
            # a measurement of the house — only of this file.
            #
            # corrupt_rows SURVIVES the blind return. It is a measured fact
            # about the file we just parsed, and blanking it would hide a real
            # defect behind a different one — the reader would see
            # "not_scanned" and go run a scan, never learning that the ledger
            # it was about to certify has unreadable rows in it.
            unscanned = _blind_field(
                "not_scanned",
                "refreshing" if refreshing else "never",
                None,
                (refreshing or {}).get("started_at"),
            )
            unscanned["corrupt_rows"] = state.corrupt_count
            corrupt_error = state.error()
            if corrupt_error:
                unscanned["error"] = f"not_scanned; {corrupt_error}"
            return unscanned

        # ── THE MARKER IS RE-VERIFIED AGAINST THE WHOLE LEDGER, EVERY READ ──
        # Not against the last scan's deltas. This is review F1: a zero-delta
        # rescan used to disarm the only loss check there was.
        integrity_error = _check_marker_integrity(marker, root)
        if integrity_error:
            return _blind_field(integrity_error, "error", marker.get("scanned_at"))
        stale_error = _marker_staleness(marker)
        if stale_error and not refreshing:
            # STALE IS AN ERROR STATE, NOT A FOOTNOTE. A six-year-old marker
            # returned ingestion:"ok" on the reviewed tip.
            #
            # WITH A SWEEP IN FLIGHT IT IS NOT THE ANSWER EITHER. Blanking the
            # counts because the certificate aged out, while the replacement
            # is already being computed, hands the watch seat nothing at the
            # one moment it has a perfectly good previous answer. So the
            # numbers stand, `ingestion` says `refreshing` rather than `ok`,
            # and `scanned_at` still shows how old they are. What is refused is
            # calling them fresh — "stale but ok" stays impossible.
            return _blind_field(stale_error, "stale", marker.get("scanned_at"))
        summary = summarize(root)
        error = None
        ingestion = "ok"
        claimed = sum(v for v in (marker.get("counts") or {}).values() if isinstance(v, int))
        # Lower-bound reconciliation, KEPT as a second, independent check.
        # counts are "opened by THIS scan", and the ledger accumulates across
        # scans, so the ledger can only ever hold MORE distinct ids than one
        # scan claims to have opened. Holding fewer is arithmetic proof that
        # rows are gone. Weak on its own (see MARKER_REQUIRED) — but it costs
        # nothing and catches a case the byte-prefix check cannot: rows lost
        # from a ledger that was then re-grown to the same length.
        distinct = len(state.latest) + state.corrupt_count
        shrank = claimed > distinct
        if shrank:
            error = f"ledger_shrank:marker claims {claimed} opened, ledger holds {distinct}"
            ingestion = "error"
        elif summary["unmeasured"]:
            ingestion = "degraded"
        corrupt_error = state.error()
        if corrupt_error:
            error = f"{error}; {corrupt_error}" if error else corrupt_error
            ingestion = "error" if ingestion != "error" else ingestion
        if summary["unmeasured"] and not error:
            error = "unmeasured_sources:" + ",".join(summary["unmeasured"])
        config_error = summary.get("config_error")
        if config_error:
            # R9. Round 3 put the configuration failure into `error` and left
            # `ingestion: "ok"` with a numeric total beside it — a declaration
            # this reader could not interpret, published under a healthy
            # status. The scope of the count is exactly what the unreadable
            # file was going to define, so the count cannot stand either.
            error = f"{error}; {config_error}" if error else config_error
            ingestion = "config_error"
        if stale_error:
            # ONLY REACHABLE WITH A SWEEP IN FLIGHT — the branch above returns
            # otherwise. Appended last so it can never displace the shrank,
            # corrupt, unmeasured or config messages, which are all about the
            # data rather than about its age.
            error = f"{error}; {stale_error}" if error else stale_error
        # ── A PARTIAL SWEEP NEVER PUBLISHES A TOTAL ────────────────────────
        # The `partial:` statuses `scan_all` writes already make every
        # unreached source unmeasured, so `summarize` nulls the aggregates by
        # the rule that was already there. This adds the NAME: `ingestion`
        # says `partial` and the error lists what was not reached, so a reader
        # can tell a partial sweep from a broken source without diffing
        # statuses. INGESTION PRECEDENCE, most severe first: error,
        # config_error, partial, refreshing, degraded, ok.
        partial = bool(marker.get("partial"))
        unreached = marker.get("unreached") or []
        if partial:
            partial_error = "partial_scan:" + ",".join(str(u) for u in unreached)
            error = f"{error}; {partial_error}" if error else partial_error
            if ingestion in ("ok", "degraded"):
                ingestion = "partial"
        if refreshing and ingestion in ("ok", "degraded"):
            ingestion = "refreshing"
        # ── N5: THE ledger_shrank BRANCH RETURNS NULL LIKE EVERY OTHER ONE ──
        # It used to set `error` and then copy the numeric total out of the
        # fold unchanged, so the one branch that has ARITHMETIC PROOF that
        # rows are missing was also the one that still published a count. A
        # marker claiming more opened signals than the ledger holds makes
        # every aggregate over that ledger untrustworthy, not just the total —
        # the missing rows have no source to subtract them from either.
        by_source_open = {k: v["open"] for k, v in summary["by_source"].items()}
        by_source_detail = summary["by_source"]
        blind_aggregates = shrank or bool(config_error) or partial
        if blind_aggregates:
            by_source_open = dict.fromkeys(by_source_open)
            by_source_detail = {k: dict.fromkeys(v) for k, v in summary["by_source"].items()}
        return {
            "error": error,
            "ingestion": ingestion,
            "scanned_at": marker.get("scanned_at"),
            "refresh_started_at": (refreshing or {}).get("started_at"),
            "partial": partial,
            "unreached": list(unreached),
            "measured": True,
            # THE NULLS COME STRAIGHT FROM summarize(). Nothing here recomputes
            # a count from rows it happens to hold — that recomputation is what
            # the reviewer caught the tool surface doing.
            "total": None if blind_aggregates else summary["total"],
            "total_configured": None if blind_aggregates else summary["total_configured"],
            "total_configured_scope": summary["total_configured_scope"],
            "not_configured": summary["not_configured"],
            "stale_24h": None if blind_aggregates else summary["stale_24h"],
            "stale_7d": None if blind_aggregates else summary["stale_7d"],
            "by_source": by_source_open,
            # THE NESTED VIEW, CARRIED ON THE SAME FOLD. `signals_summary`
            # needs stale windows and oldest_unacked per source; the reviewer
            # caught it calling summarize() a second time to get them and
            # publishing that second fold's raw numbers, which threw away every
            # null computed here. One fold, both shapes, no way to diverge.
            "by_source_detail": by_source_detail,
            "corrupt_rows": summary["corrupt_rows"],
            "source_status": summary["source_status"],
            "sources_degraded": summary["sources_degraded"],
        }
    except LedgerUnreadable as exc:
        return _blind_field(str(exc), "error")
    except OSError as exc:
        return _blind_field(f"unreadable:{exc.__class__.__name__}", "error")
    except Exception as exc:  # noqa: BLE001 — see the docstring: must not raise
        return _blind_field(f"internal:{exc.__class__.__name__}", "error")


# ── adapters (read existing surfaces; do not mutate them) ───────────────────


def _iso_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ScanResult:
    """Per-source outcome: what opened, and whether the source was readable.

    A count without a status is the fail-open the reviewer named twice: a
    source that could not be parsed, and a source that is simply not there,
    both used to return ``0`` and be published as "ok".

    ``shards`` and ``unreached`` are the 2026-09-06 additions and they are the
    same idea one layer down. ``shards`` is the per-file evidence a later
    rescan judges "has this changed?" against — size, mtime_ns and row count
    for every shard the source is made of, INCLUDING the ones this scan
    skipped because they had not changed, because a watermark list that
    silently drops what it did not re-read certifies a smaller store than the
    one on disk. ``unreached`` names the shards a time budget stopped us
    before; it is what makes a partial scan say so instead of looking complete.
    """

    __slots__ = ("opened", "status", "skipped", "shards", "unreached")

    def __init__(
        self,
        opened: int,
        status: str,
        skipped: int = 0,
        shards: list[dict] | None = None,
        unreached: list[str] | None = None,
    ) -> None:
        self.opened = opened
        self.status = status
        self.skipped = skipped
        self.shards = shards or []
        self.unreached = unreached or []


# ── PER-SHARD WATERMARKS ───────────────────────────────────────────────────
#
# A certificate used to say only "these seven sources were read". It could not
# say WHAT was read, so every rescan re-read everything: the deployed sweep
# folded 3,975 honks, 240 spool rows, 295 proposal files and 161 thread shards
# on the hour, every hour, to discover that almost none of them had moved.
#
# A watermark is the smallest honest answer to "did this file change?": its
# size and its nanosecond mtime at the moment we read it, plus the row count we
# got. A rescan re-stats each shard; equal size AND equal mtime_ns AND still
# readable means the bytes we folded last time are the bytes on disk now, so
# its rows are already in the ledger and re-folding them can only reproduce
# what is there.
#
# THE READABILITY PROBE IS NOT DECORATION. `chmod 000` changes neither size nor
# mtime, so a permission loss is invisible to a stat-only comparison and the
# source would carry its old "ok" forward while being unopenable. One
# `os.access` per skipped shard closes that, and a shard that fails it is
# treated as CHANGED — read it, and let the real status come back.
#
# ABSENCE OF A WATERMARK MEANS SCAN IT. Every path here degrades toward the
# full sweep: no certificate, a certificate from a release that had no
# watermarks, an elided entry, a shard we have never seen. Being wrong in that
# direction costs time; being wrong in the other direction loses signals.


def _shard_entry(rel: str, path: Path, rows: int) -> dict | None:
    """This shard as the certificate will record it. None if it vanished."""
    try:
        st = path.stat()
    except OSError:
        return None
    return {"path": rel, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "rows": int(rows)}


def _prev_shards(prev: dict | None) -> dict[str, dict]:
    """path -> previous watermark entry, for the entries the certificate kept."""
    if not isinstance(prev, dict):
        return {}
    shards = prev.get("shards")
    if not isinstance(shards, list):
        return {}
    out: dict[str, dict] = {}
    for entry in shards:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            out[entry["path"]] = entry
    return out


def _shard_unchanged(entry: dict | None, path: Path) -> bool:
    """True only when the bytes we folded last time are the bytes on disk now."""
    if not isinstance(entry, dict):
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_size != entry.get("size") or st.st_mtime_ns != entry.get("mtime_ns"):
        return False
    return os.access(path, os.R_OK)


def _status_carryable(status: Any) -> bool:
    """May an unchanged shard keep the status the last scan gave it?

    NOT FOR "unknown" AND NOT FOR "partial:". Those two are the absence of a
    measurement, and carrying an absence forward on the strength of "nothing
    changed" would make a source that has never been read look permanently
    settled — the sweep would skip it forever on evidence it never gathered.
    """
    if not isinstance(status, str) or not status.strip():
        return False
    return status != "unknown" and not status.startswith("partial:")


def _canonical_shards(shards: list[dict]) -> list[dict]:
    return sorted(
        (
            {
                "path": s.get("path"),
                "size": s.get("size"),
                "mtime_ns": s.get("mtime_ns"),
                "rows": s.get("rows"),
            }
            for s in shards
            if isinstance(s, dict)
        ),
        key=lambda s: str(s.get("path")),
    )


def _watermark_block(shards: list[dict], *, now: datetime | None = None) -> dict:
    """One source's watermark evidence, BOUNDED, and it says when it bounded.

    The bound exists because a certificate is read on every refresh decision
    and a source with tens of thousands of shards would turn that read into its
    own cost. Over ``watermark_max_shards()`` entries, only shards touched
    within ``watermark_window_days()`` are kept (newest first, still capped),
    and the block records how many were dropped plus a DIGEST OF THE WHOLE
    LIST — so a reader can still tell that the shard set changed even where the
    per-shard entry is gone. An elided shard simply has no watermark, which
    means the next sweep reads it: the bound costs time, never correctness.

    Measured on the live store 2026-09-06: the largest source is `thread` at
    161 shards, three orders of magnitude under the 2000 default, so nothing is
    elided today. The path is exercised by lowering the bound in a test.
    """
    canonical = _canonical_shards(shards)
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    block: dict[str, Any] = {
        "shard_count": len(canonical),
        "digest": digest,
        "elided": 0,
        "shards": canonical,
    }
    cap = watermark_max_shards()
    if len(canonical) <= cap:
        return block
    window_days = watermark_window_days()
    cutoff_ns = int(
        ((now or datetime.now(timezone.utc)) - timedelta(days=window_days)).timestamp() * 1e9
    )
    recent = [
        s for s in canonical if isinstance(s.get("mtime_ns"), int) and s["mtime_ns"] >= cutoff_ns
    ]
    recent.sort(key=lambda s: s.get("mtime_ns") or 0, reverse=True)
    kept = _canonical_shards(recent[:cap])
    block["shards"] = kept
    block["elided"] = len(canonical) - len(kept)
    block["window_days"] = window_days
    block["note"] = (
        f"{len(canonical)} shards exceeds the {cap}-entry watermark bound; per-shard "
        f"entries are kept only for shards touched in the last {window_days} days "
        f"({len(kept)} kept, {len(canonical) - len(kept)} elided). An elided shard has "
        "no watermark and is therefore RE-READ by the next sweep; `digest` still "
        "covers the whole list."
    )
    return block


def _budget_exceeded(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _degrade(status: str, skipped: int) -> str:
    if skipped and status == "ok":
        return f"degraded:{skipped} unusable ids"
    if skipped:
        return f"{status}; {skipped} unusable ids"
    return status


def scan_honks(
    root: Path,
    owner: str = "watch-2/3",
    prev: dict | None = None,
    prev_status: str | None = None,
    deadline: float | None = None,
) -> ScanResult:
    """honks.jsonl and acks.jsonl SKIP AS A PAIR, deliberately.

    An ack is matched against a honk, so reading one file without the other
    decides an acknowledgement from half its evidence. Either both are
    unchanged and the source is skipped whole, or both are read.

    On the live box this source is the one that keeps moving — nape appends to
    honks.jsonl continuously — so the watermark will USUALLY miss here and the
    cost of a re-read is what the sweep's ledger index (see `_ScanIndex`) is
    there to make affordable. The two fixes are not alternatives.
    """
    honk_path = root / "nape" / "honks.jsonl"
    ack_path = root / "nape" / "acks.jsonl"
    pairs = [("nape/honks.jsonl", honk_path), ("nape/acks.jsonl", ack_path)]
    present = [(rel, p) for rel, p in pairs if p.exists()]
    prev_map = _prev_shards(prev)
    if (
        prev_map
        and _status_carryable(prev_status)
        and set(prev_map) == {rel for rel, _ in present}
        and all(_shard_unchanged(prev_map.get(rel), p) for rel, p in present)
    ):
        return ScanResult(0, str(prev_status), 0, shards=list(prev_map.values()))
    honks = _read_source_jsonl(honk_path)
    acks = _read_source_jsonl(ack_path)
    # NORMALISE BOTH SIDES. The old code stringified the ack id and then
    # compared the RAW honk id against that set, so integer id 7 was written
    # as "7" on one side and matched as 7 on the other and never matched at
    # all — an acknowledged honk stayed open forever (reviewer finding 6).
    ack_ids = {t for t in (_native_id_text(r.get("honk_id")) for r in acks.rows) if t}
    n = 0
    skipped = 0
    for rec in honks.rows:
        hid = _native_id_text(rec.get("honk_id"))
        if hid is None:
            skipped += 1
            continue
        produced = rec.get("timestamp") or _now()
        if open_signal(
            source="honk",
            native_id=hid,
            produced_at=str(produced),
            owner=owner,
            root=root,
            # THE HONK FOLD'S PAYLOAD (review F3). `pattern` is what kind of
            # drift Nape saw; `observation` is the concern in words. Without
            # these two the ledger holds an id and a count, and the surviving
            # reader cannot tell a watch seat what it is being asked to ack.
            kind=_text_or_none(rec.get("pattern")),
            concern=_text_or_none(rec.get("observation")),
            # THE CLAIM REFERENCE THE SCANNER USED TO THROW AWAY (review N1).
            # A honk about a chronicle claim carries that claim's id; without
            # it the observation reaches the ledger as anonymous prose and the
            # protected designation has nothing to bind to.
            origin_claim_id=_native_id_text(rec.get("claim_id")),
            origin_path="nape/honks.jsonl",
        ):
            n += 1
        if hid in ack_ids:
            sid = signal_id_for("honk", hid)
            latest = _latest_view(root).get(sid)
            if latest and latest.get("state") == "open":
                with contextlib.suppress(PermissionError):
                    ack_signal(
                        sid,
                        actor="nape-ack",
                        state="acknowledged",
                        reason="nape acks.jsonl",
                        root=root,
                    )
    status = honks.status if honks.status != "absent" else "ok"
    if acks.status not in ("ok", "absent"):
        status = f"{status}; acks {acks.status}"
    shards = [
        e
        for e in (
            _shard_entry("nape/honks.jsonl", honk_path, len(honks.rows) + honks.bad_lines),
            _shard_entry("nape/acks.jsonl", ack_path, len(acks.rows) + acks.bad_lines),
        )
        if e is not None
    ]
    return ScanResult(n, _degrade(status, skipped), skipped, shards=shards)


def scan_watchman(
    root: Path,
    owner: str = "watch-2/3",
    prev: dict | None = None,
    prev_status: str | None = None,
    deadline: float | None = None,
) -> ScanResult:
    spool_path = root / "watchman" / "spool.jsonl"
    prev_map = _prev_shards(prev)
    if (
        prev_map
        and _status_carryable(prev_status)
        and set(prev_map) == ({"watchman/spool.jsonl"} if spool_path.exists() else set())
        and _shard_unchanged(prev_map.get("watchman/spool.jsonl"), spool_path)
    ):
        return ScanResult(0, str(prev_status), 0, shards=list(prev_map.values()))
    spool = _read_source_jsonl(spool_path)
    n = 0
    skipped = 0
    for rec in spool.rows:
        sid = _native_id_text(rec.get("sweep_id"))
        if sid is None:
            skipped += 1
            continue
        produced = rec.get("started_at") or rec.get("spooled_at") or _now()
        if open_signal(
            source="watchman",
            native_id=sid,
            produced_at=str(produced),
            owner=owner,
            root=root,
            kind="sweep",
            concern=_text_or_none(rec.get("summary") or rec.get("note")),
            origin_claim_id=_native_id_text(rec.get("claim_id")),
            origin_path="watchman/spool.jsonl",
        ):
            n += 1
    status = spool.status if spool.status != "absent" else "ok"
    entry = _shard_entry("watchman/spool.jsonl", spool_path, len(spool.rows) + spool.bad_lines)
    return ScanResult(n, _degrade(status, skipped), skipped, shards=[entry] if entry else [])


def scan_proposals(
    root: Path,
    owner: str = "watch-2/3",
    prev: dict | None = None,
    prev_status: str | None = None,
    deadline: float | None = None,
) -> ScanResult:
    """One pending-write file is one shard, so skipping is per file.

    A proposal's whole lifecycle lives in its own file — the `status` field is
    what drives the committed/rejected transitions — so an unchanged file has
    nothing new to say and skipping it decides nothing.

    RESIDUAL, NAMED RATHER THAN ENGINEERED AROUND: `native_id` comes from
    `proposal_id` INSIDE the file, with the filename only as a fallback. Two
    files in one substrate carrying the same `proposal_id` are therefore one
    signal, and which of them drives its transitions is already walk-order
    arbitrary on a full sweep; skipping one can change which. Same ambiguity,
    same blast radius, not made worse here.
    """
    n = 0
    skipped = 0
    notes: list[str] = []
    prev_map = _prev_shards(prev)
    shards: list[dict] = []
    unreached: list[str] = []
    stopped = False
    for substrate in ("grok_bridge", "openai_bridge", "antigravity_connector"):
        d = root / substrate / "pending_writes"
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            rel = f"{substrate}/pending_writes/{f.name}"
            if stopped:
                unreached.append(rel)
                continue
            carried = prev_map.get(rel)
            if _status_carryable(prev_status) and _shard_unchanged(carried, f):
                shards.append(carried)
                continue
            if _budget_exceeded(deadline):
                stopped = True
                unreached.append(rel)
                continue
            entry = _shard_entry(rel, f, 1)
            if entry is not None:
                shards.append(entry)
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                skipped += 1
                continue
            if not isinstance(rec, dict):
                # `[]`, `null` and a bare string each used to reach
                # `.get` and raise AttributeError mid-scan, aborting every
                # later source invisibly (reviewer finding 6).
                skipped += 1
                continue
            native = _native_id_text(rec.get("proposal_id")) or f.name
            produced = rec.get("timestamp") or _iso_from_mtime(f)
            status = rec.get("status") or "pending"
            key = f"{substrate}:{native}"
            if open_signal(
                source="proposal",
                native_id=key,
                produced_at=str(produced),
                owner=owner,
                root=root,
                kind=_text_or_none(status) or "pending",
                concern=_text_or_none(rec.get("tool") or rec.get("summary")),
                origin_claim_id=_native_id_text(rec.get("claim_id")),
                origin_path=f"{substrate}/pending_writes/{f.name}",
            ):
                n += 1
            sid = signal_id_for("proposal", key)
            latest = _latest_view(root).get(sid)
            if not latest or latest.get("state") != "open":
                continue
            if status == "committed":
                ack_signal(
                    sid, actor="drain", state="acted", reason=f"{substrate} committed", root=root
                )
            elif status == "rejected":
                ack_signal(
                    sid,
                    actor="drain",
                    state="dismissed",
                    reason=f"{substrate} rejected",
                    root=root,
                )
    if skipped:
        notes.append(f"{skipped} unreadable proposal files")
    return ScanResult(n, _degrade("ok", skipped), skipped, shards=shards, unreached=unreached)


def scan_halts(
    root: Path,
    owner: str = "watch-2/3",
    prev: dict | None = None,
    prev_status: str | None = None,
    deadline: float | None = None,
) -> ScanResult:
    return _scan_file_source(
        root,
        owner,
        prev,
        prev_status,
        deadline,
        directory=root / "daemons" / "halts",
        pattern="*.md",
        rel_prefix="daemons/halts",
        source="halt",
        kind="halt",
    )


def scan_decisions(
    root: Path,
    owner: str = "watch-2/3",
    prev: dict | None = None,
    prev_status: str | None = None,
    deadline: float | None = None,
) -> ScanResult:
    return _scan_file_source(
        root,
        owner,
        prev,
        prev_status,
        deadline,
        directory=root / "decisions",
        pattern="metabolize_*.md",
        rel_prefix="decisions",
        source="decision",
        kind="metabolize",
    )


def _scan_file_source(
    root: Path,
    owner: str,
    prev: dict | None,
    prev_status: str | None,
    deadline: float | None,
    *,
    directory: Path,
    pattern: str,
    rel_prefix: str,
    source: str,
    kind: str,
) -> ScanResult:
    """halts and metabolize decisions: one FILE is one signal and one shard.

    ONE FUNCTION FOR BOTH, because they were the same eleven lines twice and a
    watermark change landing on one of them and not the other is exactly the
    drift `iter_thread_shards` was extracted to end.
    """
    n = 0
    if not directory.is_dir():
        return ScanResult(0, "ok")
    prev_map = _prev_shards(prev)
    shards: list[dict] = []
    unreached: list[str] = []
    stopped = False
    for f in sorted(directory.glob(pattern)):
        rel = f"{rel_prefix}/{f.name}"
        if stopped:
            unreached.append(rel)
            continue
        carried = prev_map.get(rel)
        if _status_carryable(prev_status) and _shard_unchanged(carried, f):
            shards.append(carried)
            continue
        if _budget_exceeded(deadline):
            stopped = True
            unreached.append(rel)
            continue
        entry = _shard_entry(rel, f, 1)
        if entry is not None:
            shards.append(entry)
        if open_signal(
            source=source,
            native_id=f.name,
            produced_at=_iso_from_mtime(f),
            owner=owner,
            root=root,
            kind=kind,
            concern=_text_or_none(f.name),
            origin_path=rel,
        ):
            n += 1
    return ScanResult(n, "ok", 0, shards=shards, unreached=unreached)


def default_guardian_provider():
    """THE GUARDIAN READER THIS HOUSE ACTUALLY HAS.

    The branch as reviewed read ``guardian/status.json`` and
    ``guardian/issues.json`` — two files nothing in the tree writes, so the
    guardian source was wired to a shape that does not exist and every scan
    recorded a guardian that was silently unavailable. The real reader is
    ``dashboard_readers.read_guardian`` (dashboard_readers.py:727), which
    computes the posture via ``guardian_tools._evaluate_status``, caches it,
    and returns ``None`` on probe failure — exactly the contract this seam
    wants: a dict with ``issues``, or None meaning "could not measure".

    Imported inside the function, not at module import: ``dashboard_web``
    imports this module, so a module-level import of a dashboard module
    would put a cycle in the console's import path.
    """
    from . import dashboard_readers

    return dashboard_readers.read_guardian()


def _guardian_payload(root: Path, provider=None) -> dict | None:
    """Dashboard-reader shape: {issues: [str], ...}. None = source unavailable.

    ``provider`` defaults to the live reader above. Tests inject their own
    (or a fixture file under ``guardian/``, still honoured so an offline
    fixture run does not have to probe the box).
    """
    if provider is None:
        status_path = root / "guardian" / "status.json"
        issues_path = root / "guardian" / "issues.json"
        if status_path.exists():
            try:
                payload = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return None
            return payload if isinstance(payload, dict) else None
        if issues_path.exists():
            try:
                issues = json.loads(issues_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return None
            if isinstance(issues, list):
                return {"issues": issues, "issue_count": len(issues), "source": "issues.json"}
            return None
        # THE LIVE READER PROBES THE MACHINE, NOT THIS ROOT. Guardian posture
        # is a property of the box (listening ports, services), so it is only
        # meaningful for the real sovereign root. A scan of a synthetic root
        # with no fixture reports guardian UNAVAILABLE rather than shelling
        # out — which keeps the house rule (a test never probes the live box)
        # and is the honest answer besides: there is no guardian data here.
        try:
            if Path(root).resolve() != default_sovereign_root().resolve():
                return None
        except OSError:
            return None
        provider = default_guardian_provider
    try:
        payload = provider()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def scan_guardian(
    root: Path,
    owner: str = "watch-2/3",
    provider=None,
    prev: dict | None = None,
    prev_status: str | None = None,
    deadline: float | None = None,
) -> ScanResult:
    """NO WATERMARKS, AND THAT IS NOT AN OVERSIGHT. Guardian posture is a live
    probe of the box, not a file, so there is nothing whose mtime could stand
    in for it. It is re-measured on every sweep — which is also what keeps a
    guardian that went unavailable from carrying a stale "ok" forward."""
    payload = _guardian_payload(root, provider=provider)
    if payload is None:
        # UNAVAILABLE, NOT ZERO. This status is what stops the heartbeat
        # rendering a guardian it could not read as a guardian with no issues.
        return ScanResult(0, "unavailable")
    issues = payload.get("issues") or []
    if not isinstance(issues, list):
        return ScanResult(0, "unavailable:issues is not a list")
    n = 0
    skipped = 0
    for issue in issues:
        if issue == "No issues detected":
            continue
        native = _native_id_text(issue)
        if native is None:
            skipped += 1
            continue
        if open_signal(
            source="guardian",
            native_id=native,
            produced_at=_now(),
            owner=owner,
            root=root,
            kind="issue",
            concern=_text_or_none(native),
            # No file: the guardian posture is a live probe of the box, so the
            # honest provenance is the reader that produced it, not a path.
            origin_path="guardian:dashboard_readers.read_guardian",
        ):
            n += 1
    return ScanResult(n, _degrade("ok", skipped), skipped)


def _shard_rel(shard: Path, root: Path) -> str:
    """The shard's path relative to <root>/chronicle/open_threads.

    Relative, not absolute: an absolute path baked into a ledger row is the
    `~/` ambiguity one layer down — it resolves only on the machine that wrote
    it, and this ledger is meant to be readable wherever the store is.
    """
    try:
        return str(shard.relative_to(Path(root) / "chronicle" / "open_threads"))
    except ValueError:
        return shard.name


def _thread_native_id(rec: dict, shard: Path, index: int, rel: str | None = None) -> str:
    tid = rec.get("thread_id")
    if tid:
        text = _native_id_text(tid)
        if text:
            return text
    # THE SHARD'S RELATIVE PATH, NOT ITS BASENAME. Hashing `shard.name`
    # collapsed `domain_a/log.jsonl` and `domain_b/log.jsonl` into ONE
    # anonymous signal whenever the question, timestamp and index matched —
    # a permanent undercount that a rescan could never repair, because the
    # second scan was idempotent on the collided id (reviewer finding 7).
    key = rel if rel is not None else shard.name
    raw = f"{key}|{rec.get('question', '')}|{rec.get('timestamp', '')}|{index}"
    return f"anon:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


# ── WHO CLOSED IT DECIDES WHETHER A SCAN MAY REOPEN IT (review N7) ─────────
#
# The F7 repair taught `scan_threads` to reopen a signal whose source record
# is unresolved. It then reopened EVERY non-open signal on every rescan of an
# unchanged source — including one a human had just acknowledged, blanking
# reason/closed_by/closed_at on the latest row. "The thread is still open" and
# "nobody has looked at this" are different facts, and the second one is the
# only thing an acknowledgement ever claimed.
#
# These are the closers THIS MODULE writes itself, from source state. A close
# by one of them is a restatement of the source, so a later source record may
# freely restate it back. Anything else is somebody's acknowledgement, and
# reversing it needs an ACTUAL later source transition — a source timestamp
# newer than the moment of the ack.
#
# RESIDUAL, NAMED: a caller reaching `ack_signal` directly with the literal
# string "watch-2/3" would have their ack treated as source-derived. The tool
# surface cannot produce it — dispatch identities are namespaced `seat:` —
# but a direct library call could.
SOURCE_DERIVED_CLOSERS = frozenset({"watch-2/3", "drain", "nape-ack"})


def _may_reopen(latest: dict, source_produced_at: str) -> bool:
    """True when a scan is allowed to reopen this terminal row."""
    closer = latest.get("closed_by")
    if isinstance(closer, str) and closer.strip() in SOURCE_DERIVED_CLOSERS:
        return True
    # A HUMAN (or a seat) CLOSED THIS. Reopen only on a real later transition.
    # NOT "the source is newer than the row" — the scanner's own close stamps
    # closed_at=now while a legitimate source record is dated in the past, so
    # that test would forbid every honest cross-shard repair F7 exists for.
    # The question is narrower: did the SOURCE change after the ack?
    closed_at = _parse_dt(latest.get("closed_at"))
    when = _parse_dt(source_produced_at)
    if closed_at is None or when is None:
        # Cannot establish a later transition. Leave the acknowledgement
        # standing: a reversal we cannot justify is not a reversal.
        return False
    return when > closed_at


def scan_threads(
    root: Path,
    owner: str = "watch-2/3",
    prev: dict | None = None,
    prev_status: str | None = None,
    deadline: float | None = None,
) -> ScanResult:
    """One signal per thread id ACROSS every shard. Latest timestamp wins.

    THIS SOURCE SKIPS ALL-OR-NOTHING, AND THAT IS A CORRECTNESS RULE, NOT A
    SIMPLIFICATION. Per-shard skipping is sound only where a signal's identity
    is confined to one shard. `thread` is the one source with a CROSS-SHARD
    fold — F7 exists because "latest record wins" used to hold only within a
    shard — so reading a subset of shards decides a thread's state from a
    subset of its evidence. Concretely: a thread resolved in `a.jsonl` on
    2026-08-01 and reopened in `b.jsonl` on 2026-09-01, with `a` unchanged and
    `b` touched, would be judged from `b` alone.

    A GUARD ON produced_at DOES NOT RESCUE THAT, which was the tempting cheap
    fix: `open_signal` stamps `produced_at` at FIRST OPEN and returns early on
    every rescan without updating it, so the stored value is first-seen, not
    latest-across-shards, and comparing against it is a no-op on exactly the
    case it would exist for.

    So: every shard unchanged -> skip the source; anything moved -> read all of
    them. Measured on the live store 2026-09-06 that is 161 shards / 772 KB /
    267 records, i.e. milliseconds. If this store ever grows to where a full
    thread pass is expensive, the answer is a per-thread index, not per-shard
    skipping.

    THE BUDGET ABANDONS THIS SOURCE RATHER THAN HALF-FOLDING IT, for the same
    reason: a pass-1 fold cut short is a decision made from part of the
    evidence. Hitting the deadline mid-walk reports every shard unreached and
    mutates nothing.

    TWO DEFECTS FIXED HERE, and they are different animals.

    F7 (cross-shard state). ``latest_by_id`` used to be re-created inside the
    per-shard loop, so "latest record wins" held only WITHIN a shard: a thread
    resolved in ``a.jsonl`` on August 1 and re-opened in ``b.jsonl`` on
    September 1 was acted on by whichever shard the directory walk reached
    last, and in the reviewer's fixture the obsolete August terminal state won
    permanently — a rescan is idempotent, so nothing ever repaired it. THE
    POLICY, stated once and applied before any ledger mutation: across all
    shards, the record with the LATEST parseable ``timestamp`` is the thread's
    state; ties fall back to walk order, which is stable under ``sorted``.
    That is why this is now two passes. Deciding state while mutating is what
    made the old bug expressible at all.

    F2 (status propagation). The read status of each shard was dropped on the
    floor — only ``bad_lines`` was accumulated — so a chmod-000 shard scanned
    as ``thread:"ok"`` and the heartbeat reported a healthy zero for a source
    it could not open. Every non-ok status is now carried into the marker.
    """
    d = root / "chronicle" / "open_threads"
    if not d.is_dir():
        return ScanResult(0, "ok")
    all_shards = iter_thread_shards(d)

    def _rel(path: Path) -> str:
        try:
            return f"chronicle/open_threads/{path.relative_to(d)}"
        except ValueError:
            return f"chronicle/open_threads/{path.name}"

    prev_map = _prev_shards(prev)
    if (
        prev_map
        and _status_carryable(prev_status)
        and set(prev_map) == {_rel(f) for f in all_shards}
        and all(_shard_unchanged(prev_map.get(_rel(f)), f) for f in all_shards)
    ):
        return ScanResult(0, str(prev_status), 0, shards=list(prev_map.values()))
    degraded = 0
    unreadable: list[str] = []
    shards: list[dict] = []
    # PASS 1 — decide, mutating nothing.
    latest_by_id: dict[str, tuple[dict, Path, datetime | None]] = {}
    for f in all_shards:
        if _budget_exceeded(deadline):
            return ScanResult(
                0,
                "ok",
                0,
                shards=[],
                unreached=[_rel(s) for s in all_shards],
            )
        read = _read_source_jsonl(f)
        entry = _shard_entry(_rel(f), f, len(read.rows) + read.bad_lines)
        if entry is not None:
            shards.append(entry)
        degraded += read.bad_lines
        if read.status not in ("ok", "absent"):
            try:
                label = str(f.relative_to(d))
            except ValueError:
                label = str(f)
            unreadable.append(f"{label}:{read.status}")
        try:
            rel = str(f.relative_to(d))
        except ValueError:
            rel = str(f)
        for i, rec in enumerate(read.rows):
            native = _thread_native_id(rec, f, i, rel)
            when = _parse_dt(rec.get("timestamp"))
            prev = latest_by_id.get(native)
            if prev is None:
                latest_by_id[native] = (rec, f, when)
                continue
            _prev_rec, _prev_shard, prev_when = prev
            # An unparseable timestamp never displaces a dated record; a dated
            # record always displaces an undated one. Two undated records fall
            # back to walk order (last wins), which is the old behaviour and
            # the only ordering available.
            if when is None and prev_when is not None:
                continue
            if when is not None and prev_when is not None and when < prev_when:
                continue
            latest_by_id[native] = (rec, f, when)

    # PASS 2 — act on the decided state.
    n = 0
    for native, (rec, shard, _when) in latest_by_id.items():
        produced = rec.get("timestamp") or _iso_from_mtime(shard)
        if open_signal(
            source="thread",
            native_id=native,
            produced_at=str(produced),
            owner=owner,
            root=root,
            kind="open_thread",
            concern=_text_or_none(rec.get("question")),
            origin_claim_id=_native_id_text(rec.get("claim_id")),
            origin_path=f"chronicle/open_threads/{_shard_rel(shard, root)}",
        ):
            n += 1
        sid = signal_id_for("thread", native)
        resolved = rec.get("resolved") is True or rec.get("status") == "resolved"
        latest = _latest_view(root).get(sid)
        if not latest:
            continue
        if resolved and latest.get("state") == "open":
            ack_signal(
                sid,
                actor="watch-2/3",
                state="acted",
                reason="thread resolved",
                root=root,
            )
        elif not resolved and latest.get("state") != "open" and _may_reopen(latest, produced):
            # THE HALF THE OLD CODE HAD NO WORD FOR. It could only ever close a
            # signal, so once an obsolete terminal state landed there was no
            # path back. A thread whose latest record across all shards is OPEN
            # must not sit in the ledger as acted; re-open it, and say why in
            # the row's own reason so the reversal is legible in the audit
            # trail rather than looking like a duplicate.
            #
            # `_may_reopen` is review N7: the F7 repair reversed HUMAN
            # acknowledgements too, on an unchanged source.
            _append(
                _row(
                    signal_id=sid,
                    source="thread",
                    produced_at=str(produced),
                    owner=latest.get("owner") or owner,
                    state="open",
                    reason=None,
                    closed_by=None,
                    closed_at=None,
                    updated_at=_now(),
                    kind="open_thread",
                    concern=_text_or_none(rec.get("question")),
                    origin=_origin(
                        "thread",
                        native,
                        claim_id=_native_id_text(rec.get("claim_id")),
                        path=f"chronicle/open_threads/{_shard_rel(shard, root)}",
                    ),
                ),
                root,
            )
    status = "ok"
    if unreadable:
        status = "unreadable:" + "; ".join(sorted(unreadable)[:5])
    elif degraded:
        status = f"degraded:{degraded} unparseable lines"
    return ScanResult(n, status, degraded, shards=shards)


# ── SCANNER DIAGNOSTICS (review N9) ────────────────────────────────────────
#
# "signal_ledger.py:1481 converts per-source exceptions to strings in the
# marker; :1533 does the same for whole-scanner exceptions in the response.
# Neither logs the traceback. Exception text is useful partial diagnosis; it
# is not a traceback."
#
# The public error STAYS A STRING and stays exactly as it was. `heartbeat_field`
# must not raise — `dashboard_web.build_snapshot` calls it with no section
# guard — so a genuine scanner bug can only surface there as text, and a
# reader of that text has the class and the message and no idea which line
# produced them. The traceback goes to a local, bounded file instead, which
# costs the public surface nothing.

DIAGNOSTICS_MAX_BYTES = 1024 * 1024
# R7. The file bound was checked BEFORE appending an unbounded render, so one
# exception carrying a 2 MiB message produced a 2,097,902-byte file under a
# 1,048,576-byte cap. A bound you check before writing something unbounded is
# not a bound. The entry is capped first, and small enough relative to the file
# cap that the file cap holds after any single append.
DIAGNOSTICS_ENTRY_MAX_BYTES = 64 * 1024


def diagnostics_path(root: Path | None = None) -> Path:
    return _root(root) / "signals" / "diagnostics.log"


def _log_diagnostic(root: Path | None, label: str, exc: BaseException) -> None:
    """Append one traceback, truncating the file at DIAGNOSTICS_MAX_BYTES.

    SWALLOWS ITS OWN FAILURES, deliberately and narrowly: this runs on the
    READ path, inside the handler for something that already went wrong. A
    diagnostic that turns a degraded read into a broken one is worse than a
    missing diagnostic. The public error is unaffected either way.

    Truncate-and-restart rather than rotate: the bound exists so an exception
    firing on every 3-second console poll cannot fill a disk, and a rotation
    scheme is a second thing to get wrong for a file nothing reads on a
    schedule. The truncation stamps a line saying it happened, so a reader
    never mistakes a fresh file for a quiet one.
    """
    try:
        path = diagnostics_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        rendered = f"--- {_now()} {label}\n" + "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        raw = rendered.encode("utf-8", "replace")
        if len(raw) > DIAGNOSTICS_ENTRY_MAX_BYTES:
            # TRUNCATE THE ENTRY, AND SAY SO IN THE ENTRY. A silently clipped
            # traceback reads as a complete one that simply ended early, which
            # is the shape of every fail-open in this file.
            notice = (
                f"\n[entry truncated at {DIAGNOSTICS_ENTRY_MAX_BYTES} bytes; "
                f"{len(raw)} bytes rendered]\n"
            )
            keep = DIAGNOSTICS_ENTRY_MAX_BYTES - len(notice.encode("utf-8"))
            rendered = raw[: max(0, keep)].decode("utf-8", "replace") + notice
            raw = rendered.encode("utf-8", "replace")
        existing = path.stat().st_size if path.exists() else 0
        if existing + len(raw) > DIAGNOSTICS_MAX_BYTES:
            path.write_text(
                f"--- {_now()} truncated at {DIAGNOSTICS_MAX_BYTES} bytes; "
                "earlier diagnostics discarded\n",
                encoding="utf-8",
            )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(rendered)
    except Exception:  # noqa: BLE001 — see the docstring
        return


def scan_all(
    root: Path | None = None,
    owner: str = "watch-2/3",
    guardian_provider=None,
    *,
    budget_seconds: float | None = None,
) -> dict:
    """Owned ingestion path. Writes signals/last_scan.json. Does not install a worker.

    EVERY SOURCE IS ISOLATED. The branch as reviewed ran the seven scanners
    as values in one dict literal, so the first one to raise took every later
    source with it and the marker was never written — leaving a stale marker
    certifying a scan that had actually aborted (reviewer finding 6). A
    source that raises is now recorded as ``failed:<Exc>`` and the scan
    continues; the marker carries that status to every reader.

    IT IS INCREMENTAL AGAINST THE PREVIOUS CERTIFICATE (2026-09-06). Each
    scanner is handed the watermark block and the status the last completed
    scan recorded for its source, and skips the shards whose size and mtime
    are unchanged and which are still readable. A skipped shard carries its
    previous watermark entry forward verbatim, so the certificate still
    describes the WHOLE source and not just the part this sweep touched.

    IT HAS A HARD TIME BUDGET, and exceeding it produces a PARTIAL certificate
    rather than a short answer wearing a total's name. A source not reached is
    stamped `partial:` in `source_status`, which every downstream reader
    already treats as unmeasured — so `total` is null by the existing rule, not
    by a new one. Its previous watermarks are carried forward untouched so the
    next sweep resumes where this one stopped instead of starting over.
    """
    r = _root(root)
    budget = scan_budget_seconds() if budget_seconds is None else float(budget_seconds)
    started = time.monotonic()
    deadline = started + budget
    prev_marker, _prev_marker_error = _read_scan_marker(r)
    if prev_marker is not None and _check_marker_integrity(prev_marker, r) is not None:
        # ── A WATERMARK CERTIFIES "THOSE ROWS ARE ALREADY IN *THIS* LEDGER" ──
        # and it stops meaning that the moment the ledger is no longer the one
        # the certificate was written over. Skipping an unchanged shard is only
        # sound because re-reading it could not change the ledger; if the
        # ledger itself was rewritten under us, that is exactly what re-reading
        # it might repair.
        #
        # THE CASE THAT PROVED IT (R1): strip `origin` from every ledger row and
        # rescan. The sources have not moved, so a stat-only skip skips them —
        # and the provenance backfill, which is the whole R1 remedy, never runs,
        # so every concern in the queue stays withheld forever. `ensure_scanned`
        # would REFUSE this state outright (it is a rewritten ledger), but
        # `scan_all` is public and reachable directly, and a repair path that
        # only works through one door is not a repair path.
        #
        # Costs one sha256 of the ledger prefix per sweep: ~10 ms on the live
        # 6.09 MB store.
        prev_marker = None
    prev_watermarks = (prev_marker or {}).get("watermarks")
    if not isinstance(prev_watermarks, dict):
        prev_watermarks = {}
    prev_statuses = (prev_marker or {}).get("source_status")
    if not isinstance(prev_statuses, dict):
        prev_statuses = {}

    def _prev(name: str) -> tuple[dict | None, str | None]:
        block = prev_watermarks.get(name)
        status = prev_statuses.get(name)
        return (block if isinstance(block, dict) else None), (
            status if isinstance(status, str) else None
        )

    scanners = (
        ("honk", lambda: scan_honks(r, owner, *_prev("honk"), deadline)),
        ("watchman", lambda: scan_watchman(r, owner, *_prev("watchman"), deadline)),
        ("proposal", lambda: scan_proposals(r, owner, *_prev("proposal"), deadline)),
        ("halt", lambda: scan_halts(r, owner, *_prev("halt"), deadline)),
        ("decision", lambda: scan_decisions(r, owner, *_prev("decision"), deadline)),
        (
            "guardian",
            lambda: scan_guardian(r, owner, guardian_provider, *_prev("guardian"), deadline),
        ),
        ("thread", lambda: scan_threads(r, owner, *_prev("thread"), deadline)),
    )
    counts: dict[str, int] = {}
    source_status: dict[str, str] = {}
    watermarks: dict[str, dict] = {}
    unreached: list[str] = []
    # DECLARED INAPPLICABLE, NOT INFERRED (judgment 2). Only an explicit
    # human-written declaration in signals/sources.json takes a source out of
    # scope. A missing file, a probe returning None, or a scanner raising
    # never lands here — those are `unavailable` and `failed:`, which are
    # measurement failures and must stay loud.
    declared_off, config_error = _declared_not_configured(r)
    budget_note = f"not reached before the {budget:g}s scan budget"
    with _scan_index(r):
        for name, fn in scanners:
            if name in declared_off:
                counts[name] = 0
                source_status[name] = NOT_CONFIGURED
                continue
            if _budget_exceeded(deadline):
                # NEVER STARTED. Carry its watermarks forward so the next sweep
                # still knows what it does not have to re-read, and stamp a
                # status that no reader can mistake for a measurement.
                counts[name] = 0
                source_status[name] = f"partial:{budget_note}"
                block, _ = _prev(name)
                if block is not None:
                    watermarks[name] = block
                unreached.append(name)
                continue
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 — one bad source must not blind the rest
                counts[name] = 0
                source_status[name] = f"failed:{exc.__class__.__name__}: {exc}"
                _log_diagnostic(r, f"source {name!r} raised during scan_all", exc)
                continue
            counts[name] = result.opened
            if result.unreached:
                source_status[name] = f"partial:{len(result.unreached)} shard(s) {budget_note}"
                unreached.append(name)
            else:
                source_status[name] = result.status
            watermarks[name] = _watermark_block(result.shards)
    # An unreadable declaration EXCUSES NOTHING: `_declared_not_configured`
    # returned an empty set, so every source stayed in scope and was scanned.
    # The error itself is surfaced at READ time by `summarize`, against the
    # file as it stands then, rather than frozen into this marker.
    del config_error
    marker = _write_scan_marker(
        counts,
        source_status,
        r,
        watermarks=watermarks,
        partial=bool(unreached),
        unreached=sorted(unreached),
        budget_seconds=budget,
        scan_seconds=time.monotonic() - started,
    )
    return {
        "counts": counts,
        "source_status": source_status,
        "watermarks": watermarks,
        "partial": bool(unreached),
        "unreached": sorted(unreached),
        "scan_seconds": marker.get("scan_seconds"),
    }


@contextlib.contextmanager
def _refresh_lock(root: Path | None = None):
    """Exclusive flock on the MARKER path, held across the whole refresh.

    Review judgment (1): "use a refresh lock if multiple callers may arrive
    together." Without it, two readers whose marker has just gone stale each
    decide to scan, each sweeps seven sources, and each writes a marker — the
    second certifying a ledger the first was still appending to.

    LOCK ORDER IS ONE-DIRECTIONAL AND MUST STAY THAT WAY: this lock, then the
    ledger appends `scan_all` makes through `_append_lock`, then the marker
    write. Never take this lock while holding a ledger append; that inversion
    is a deadlock between two scanners.

    IT IS ITS OWN FILE (R4). Locking the certificate created the certificate,
    which forced "empty certificate" to mean "no certificate" and hid a
    destroyed one. The sidecar carries no claims and is safe to create.
    """
    path = scan_lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield fh
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def quarantine_dir(root: Path | None = None, stamp: str | None = None) -> Path:
    """Where a human puts the damaged evidence so ingestion can start again."""
    return _root(root) / "signals" / "quarantine" / (stamp or "<timestamp>")


def damaged_ledger_remedy(root: Path | None = None) -> str:
    """The recovery instruction, and it has to actually reach a clean start (R8).

    Round 3 said "move the damaged ledger aside". The reviewer followed that
    exact instruction on a temporary root and landed in a NEW refusal: the
    certificate survived the move, so the next read saw a valid certificate
    over an absent ledger, answered `ledger_missing`, and told the operator to
    move a file that was no longer there. Containment held and the store was
    unrecoverable — an instruction that does not reach a recoverable state is
    a wedge with good manners.

    BOTH ARTIFACTS AND THE LOCK MOVE, TOGETHER, INTO ONE TIMESTAMPED
    DIRECTORY. The certificate is the thing that makes an absent ledger an
    error, so it has to go with it; the lock sidecar goes too so nothing
    stale is left holding coordination state. NOTHING IS DELETED — the
    evidence is the receipt for whatever happened, and a recovery that
    destroys it trades one silent loss for another.
    """
    q = quarantine_dir(root)
    return (
        "a rescan would overwrite the certificate and erase the evidence, so "
        "ingestion is refused until a human quarantines the damaged pair. "
        f"Nothing is deleted: mkdir -p {q} && mv {ledger_path(root)} "
        f"{scan_marker_path(root)} {scan_lock_path(root)} {q}/ "
        "(the lock may not exist; that is fine). The next read then starts a "
        "clean scan and the quarantined files remain as the receipt."
    )


def _refresh_decision(root: Path | None, *, now: datetime | None = None) -> tuple[str, str | None]:
    """(decision, error). Decision is 'skip', 'initialize', 'refresh' or 'refuse'.

    THE FOUR CASES THE REVIEWED CODE COLLAPSED INTO ONE `else: scan_all`
    (review N2), and they are four different facts:

      * **skip** — a valid marker, inside its freshness bound, whose ledger
        still matches what it certified. Nothing to do.
      * **initialize** — no marker at all. Nothing is being overwritten
        because nothing has been claimed; scanning is how this root starts.
      * **refresh** — a valid marker whose ledger still matches, gone stale.
        The ordinary path. A refresh here overwrites a marker that agrees
        with the file, so it destroys no evidence.
      * **refuse** — the certified ledger no longer matches the certificate,
        or the certificate itself will not parse. A refresh here is the
        evidence-erasure the reviewer reproduced: heartbeat and summary both
        returned `error:null, ingestion:"ok", total:0` for a ledger whose
        contents had been truncated away, because the read had already
        rewritten the marker before anything could report the loss.
    """
    marker, marker_error = _read_scan_marker(root)
    if marker_error:
        # A CERTIFICATE WE CANNOT READ IS NOT A LICENCE TO ISSUE A NEW ONE.
        # Rescanning would replace it, and the replacement would certify
        # whatever the ledger now holds — the same erasure one door over.
        return "refuse", (f"{marker_error}; " + damaged_ledger_remedy(root))
    if marker is None:
        return "initialize", None
    integrity_error = _check_marker_integrity(marker, root)
    if integrity_error:
        return "refuse", (f"{integrity_error}; " + damaged_ledger_remedy(root))
    if _marker_staleness(marker, now) is None:
        return "skip", None
    return "refresh", None


# ── THE SWEEP RUNS OFF THE CALLER'S THREAD ─────────────────────────────────
#
# `heartbeat_field(scan=True)` is reached from the native `heartbeat` tool and
# from `signals_summary`, both of which the SSE server dispatches through
# `asyncio.to_thread`. That already keeps the sweep off the event loop's own
# thread — and it did NOT keep the box responsive, because a 265 s CPU-bound
# Python sweep holds the GIL for essentially all of that wall time, so every
# other tool call crawls behind it. The measured symptom was exactly that.
#
# So the fix is not "put it in a thread", it is "STOP WAITING FOR IT". One
# executor thread owns the sweep; the read that started it waits a bounded
# `refresh_wait_seconds()` and then hands back the previous certificate's
# numbers marked `refreshing`; a read that finds a sweep ALREADY running never
# waits at all.
#
# BE HONEST ABOUT WHAT THIS BUYS. With the sweep's ledger index in place the
# ordinary sweep on the live corpus finishes in well under a second, so in
# practice the triggering read completes inside the wait and returns FRESH
# numbers rather than `refreshing`. The `refreshing` path is what covers a
# first sweep on a cold store and whatever this corpus grows into. Claiming
# "the loop is never blocked" would overstate it; the loop is never blocked for
# longer than the wait.
_REFRESH_GUARD = threading.Lock()
_REFRESH_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None
_REFRESH_INFLIGHT: dict[str, dict] = {}


def _refresh_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _REFRESH_EXECUTOR
    if _REFRESH_EXECUTOR is None:
        _REFRESH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="signal-scan"
        )
    return _REFRESH_EXECUTOR


def _refresh_worker(
    root: Path | None, owner: str, guardian_provider, now: datetime | None
) -> str | None:
    """The sweep, under the flock. RETURNS its failure; never raises.

    An exception escaping into the executor thread would be swallowed into a
    Future nobody inspects — and on a tmp root pytest has already deleted, that
    is a phantom error with no reader. Everything is caught, logged with its
    traceback, and returned as the same string `ensure_scanned` has always
    returned.
    """
    try:
        with _refresh_lock(root):
            # RE-DECIDE UNDER THE LOCK. Another reader may have completed the
            # very scan we queued behind; scanning again would be the second
            # sweep this lock exists to prevent. It also re-runs the integrity
            # check, so a ledger damaged while we waited is still refused.
            decision, error = _refresh_decision(root, now=now)
            if decision == "skip":
                return None
            if decision == "refuse":
                return error
            scan_all(root, owner, guardian_provider)
        return None
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        _log_diagnostic(root, "ingestion raised during ensure_scanned's background refresh", exc)
        return f"{exc.__class__.__name__}: {exc}"


def _start_refresh(
    root: Path | None, owner: str, guardian_provider, now: datetime | None
) -> tuple[concurrent.futures.Future, bool, str]:
    """(future, started_by_this_caller, started_at). One sweep per root."""
    key = str(_root(root))
    with _REFRESH_GUARD:
        entry = _REFRESH_INFLIGHT.get(key)
        if entry is not None and not entry["future"].done():
            return entry["future"], False, entry["started_at"]
        started_at = _now()
        future = _refresh_executor().submit(_refresh_worker, root, owner, guardian_provider, now)
        _REFRESH_INFLIGHT[key] = {"future": future, "started_at": started_at}
        return future, True, started_at


def refresh_in_flight(root: Path | None = None) -> dict | None:
    """``{"started_at": iso}`` while a sweep for this root is still running.

    This is what lets a read say `refreshing` instead of `stale`. It is a
    SEPARATE function rather than a richer return from `ensure_scanned`
    because that function's contract — a string is a failure, None is not —
    is asserted directly by a dozen tests and is worth keeping exactly.
    """
    key = str(_root(root))
    with _REFRESH_GUARD:
        entry = _REFRESH_INFLIGHT.get(key)
        if entry is None:
            return None
        if entry["future"].done():
            _REFRESH_INFLIGHT.pop(key, None)
            return None
        return {"started_at": entry["started_at"]}


def ensure_scanned(
    root: Path | None = None,
    owner: str = "watch-2/3",
    guardian_provider=None,
    *,
    now: datetime | None = None,
    wait: float | None = None,
) -> str | None:
    """INGESTION ON READ. Returns None on success, an error string on failure.

    Review F4: ``scan_all`` was labelled an "Owned ingestion path" in its own
    docstring and was never called by anything — a library function nobody
    invoked, so the counts every reader trusted were only ever as current as
    the last time a human ran a scan by hand. Searches of src/, clients/,
    scripts/, the bridge and LaunchAgents found no caller at all.

    THE FIX IS NOT A DAEMON, DELIBERATELY. Installing a launchd worker is a
    system change and Anthony's gate. What a release CAN own is that the two
    surfaces a seat actually reads through — the native heartbeat and
    ``signals_summary`` — refresh before they answer. Ingestion is then
    guaranteed to be exactly as fresh as the last read, which for a watch
    surface is the property that matters.

    IT IS INCREMENTAL BY CONSTRUCTION, not by a flag: ``open_signal`` is
    idempotent on the signal id, so a rescan of unchanged sources appends
    nothing. The scan is skipped entirely while the existing marker is inside
    its own freshness bound (3600 s by default — see the module docstring), so
    a burst of reads costs one sweep, not N.

    INTEGRITY IS JUDGED BEFORE ANYTHING IS REWRITTEN (review N2). A damaged
    ledger is REFUSED, not refreshed, and the refusal survives every
    subsequent read until a human intervenes — because the only way for this
    function to "fix" that state is to overwrite the evidence of it.

    A FAILURE IS RETURNED, NEVER SWALLOWED. ``heartbeat_field`` turns it into
    an explicit error rather than serving the previous marker's counts, which
    would be "stale but ok" — the exact shape the review named.
    """
    try:
        # THE DECISION IS MADE ON THIS THREAD, BEFORE ANY WORKER EXISTS.
        # A refusal must never be reachable only by waiting on a future: the
        # integrity-before-refresh rule (review N2) is what stops a rescan
        # erasing the evidence of a damaged ledger, and it has to answer the
        # caller immediately and identically whether or not a sweep is queued.
        decision, error = _refresh_decision(root, now=now)
        if decision == "skip":
            return None
        if decision == "refuse":
            return error
        future, started_here, _started_at = _start_refresh(root, owner, guardian_provider, now)
        if not started_here:
            # SOMEONE ELSE'S SWEEP IS ALREADY RUNNING. Waiting on it would be
            # the blocking this release exists to remove; the reader gets the
            # previous certificate marked `refreshing` instead.
            return None
        try:
            return future.result(timeout=refresh_wait_seconds() if wait is None else wait)
        except concurrent.futures.TimeoutError:
            # Still going. Not an error — `refresh_in_flight` is what the
            # reader consults, and a failure will surface on a later read
            # because the certificate it would have written is still not there.
            return None
    except Exception as exc:  # noqa: BLE001 — reported, not raised; see docstring
        _log_diagnostic(root, "ingestion raised during ensure_scanned", exc)
        return f"{exc.__class__.__name__}: {exc}"


# ── escalation (text only; send defaults OFF) ───────────────────────────────

TRIGGERS = (
    "new_halt",
    "arrival_left_quiet",
    "hq_reviewed_proposal_needs_tap",
    "tried_close_still_open_7d",
)


def render_escalation(trigger: str, detail: str) -> str:
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}")
    titles = {
        "new_halt": "NEW HALT",
        "arrival_left_quiet": "ARRIVAL GATE LEFT QUIET",
        "hq_reviewed_proposal_needs_tap": "PROPOSAL NEEDS YOUR TAP",
        "tried_close_still_open_7d": "STILL OPEN AFTER A WEEK",
    }
    return f"WATCH 2/3 — {titles[trigger]}\n{detail.strip()}\nOne line. Not a queue."


def ntfy_enabled() -> bool:
    v = os.environ.get(NTFY_ENV, "").strip().lower()
    return v in ("1", "true", "on", "yes")


def send_escalation(trigger: str, detail: str, *, publish=None) -> dict:
    """Render the phone line. Sends only if SIGNAL_LEDGER_NTFY is on AND publish given."""
    text = render_escalation(trigger, detail)
    sent = False
    if ntfy_enabled() and publish is not None:
        publish(text)
        sent = True
    return {"text": text, "sent": sent, "enabled": ntfy_enabled()}


# ── Stack tools ─────────────────────────────────────────────────────────────

SIGNAL_TOOLS = [
    Tool(
        name="signals_summary",
        description=(
            "The watch seat's queue. mode='summary' (default) returns unacked "
            "total, stale_24h, stale_7d, per-source open counts and per-source "
            "availability; mode='list' returns the SIGNALS THEMSELVES — "
            "signal_id, source, kind, opened_at, owner, state and the concern "
            "text — which is what you pass to signal_ack. Optional source "
            "filter. Ingestion runs before the read, so the counts are current. "
            "An unreadable ledger, an unscanned or stale one, or a source that "
            "could not be measured returns error / null, never a zero. "
            "signals_summary(mode='list', source='honk') replaces nape_honks "
            "and nape_honks_with_history (retired 2026-09-06)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["summary", "list"],
                    "default": "summary",
                    "description": (
                        "summary = counts. list = the individual open signals, "
                        "with their ids and concern text."
                    ),
                },
                "source": {
                    "type": "string",
                    "enum": list(SOURCES),
                    "description": "Restrict to one signal source.",
                },
                "state": {
                    "type": "string",
                    "enum": list(STATES),
                    "description": "mode='list' only. Defaults to open signals.",
                },
                "limit": {
                    "type": "integer",
                    "description": "mode='list' only. Rows to return (default 50, max 500).",
                },
            },
        },
    ),
    Tool(
        name="signal_ack",
        description=(
            "Close or acknowledge one signal. state is acknowledged|acted|dismissed. "
            "acted and dismissed require a non-blank reason. closed_by is stamped "
            "from the DISPATCH CONTEXT — the native server sets it from its own "
            "spiral session, the bridge sets it in-process from the seat identity "
            "its kernel verified, and there is no argument that can name a closer. "
            "Sending actor, actor_seat, owner, closed_by or source_seat is REFUSED, "
            "not ignored; a call with no resolvable identity is refused rather than "
            "stamped 'seat:None'. The producer of a source cannot close its own "
            "signal. Get the signal_id from signals_summary(mode='list')."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "signal_id": {"type": "string"},
                "state": {"type": "string", "enum": list(CLOSE_STATES)},
                "reason": {"type": "string"},
                # NO CLOSER PARAMETER, DELIBERATELY (review N3). `actor_seat` was
                # published here so "the bridge could fill it in" — and the
                # review then closed a signal as an arbitrary seat through the
                # real MCP handler by simply sending that field. A parameter the
                # server reads is reachable by every caller of the server; there
                # is no such thing as a bridge-only argument. Identity now
                # travels in `dispatch_context.CALLER_SEAT`, which no argument
                # dict can name.
            },
            "required": ["signal_id", "state"],
        },
    ),
]

SIGNAL_TOOL_TIERS = {"signals_summary": "core", "signal_ack": "core"}
# INTENT: signal_ack is "write", NOT "govern" (HQ's ruling, 2026-09-06,
# review F4). Acknowledging a signal is the watch seat's ordinary operational
# act — the thing the seat exists to do. Anthony's governance list is laws,
# policies, seat permissions, ring placement and deletes; a honk being marked
# read is none of those. Classifying it govern was what left the designated
# watch seat with no closure path at all: the canonical rings do not admit a
# govern-intent tool and the bridge denies it to Studio seats.
SIGNAL_TOOL_INTENTS = {"signals_summary": "read", "signal_ack": "write"}

LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 500


# ── THE DISPLAY BOUNDARY FOR PROTECTED MATERIAL (review N1) ────────────────
#
# A honk about a DESIGNATED PROTECTED RECORD quoted that record's body into
# its observation; the scanner copied the observation into the ledger as
# `concern`; list mode returned it verbatim, with no stakes and no notice.
# The reviewer built the whole chain from a synthetic record and read the body
# straight out of `signals_summary(mode='list')`.
#
# THE WITHHOLDING IS AT READ TIME, NOT SCAN TIME, AND THAT IS THE DESIGN.
# Designation happens whenever the human says so — usually AFTER the material
# was written. A scan-time filter would honour only the designations that
# existed the moment a row was minted, which is precisely the window this
# house's protected layer exists to close. So every read re-folds the index
# and re-judges every row it is about to show.

WITHHELD_CONCERN = "[withheld: protected]"
# R1. A row whose provenance is absent or could not be evaluated is withheld
# under its own marker: it is NOT known to be protected, and saying so would
# be a claim we cannot make either. The two counts in the envelope keep the
# two populations distinguishable.
WITHHELD_UNEVALUATED = "[withheld: provenance not evaluated]"

# BOUNDED READ, with the bound named. The index is consulted on every list
# read, so an unbounded read here is a read-amplification the display boundary
# cannot afford — and a file that has grown past this is a fact worth failing
# on rather than truncating through. The live index is 2,275 bytes / 4 records
# (measured 2026-09-06), so this is roughly three orders of magnitude of head
# room.
PROTECTED_INDEX_MAX_BYTES = 4 * 1024 * 1024


def protected_index_path(root: Path | None = None) -> Path:
    return _root(root) / "chronicle" / "protected.jsonl"


def _protected_fold(root: Path | None = None) -> tuple[dict[str, dict], str | None]:
    """(designation fold, error). THREE STATES, kept distinct on purpose.

      * **absent** — nothing has ever been designated under this root. Empty
        fold, no error, nothing withheld. This is the ordinary case and it
        must stay cheap, or the boundary gets removed for being expensive.
      * **unparseable / over the bound / unreadable** — we cannot say what is
        designated, so we cannot say anything is safe to show. Returns an
        error, and the caller withholds EVERY concern.
      * **readable** — fold it and judge each row against it.

    THIS READER IS STRICTER THAN ``protected.load_protected``, deliberately.
    That one skips corrupt lines (the chronicle read convention, right for a
    recall surface). Here a skipped line could be the very designation that
    should have withheld the row we are about to print, so a malformed line
    fails the whole read instead.
    """
    path = protected_index_path(root)
    if not path.exists():
        return {}, None
    try:
        size = path.stat().st_size
        if size > PROTECTED_INDEX_MAX_BYTES:
            return {}, (
                f"protected_index_unbounded:{size} bytes exceeds the "
                f"{PROTECTED_INDEX_MAX_BYTES}-byte read bound"
            )
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"protected_index_unreadable:{exc.__class__.__name__}"
    from . import protected as protected_module

    records: list[dict] = []
    for i, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            return {}, f"protected_index_malformed:line {i}"
        if not isinstance(rec, dict):
            return {}, f"protected_index_malformed:line {i} is not an object"
        # ── R2: STRUCTURE, NOT JUST SYNTAX ──────────────────────────────────
        # `fold_protected` SILENTLY SKIPS a row whose action is missing or
        # whose claim id is not a string — the right convention for a recall
        # surface, and a fail-open here. The reviewer replaced a real
        # designation with the valid JSON `{"claim_id": "<the same id>"}` and
        # the fold came back EMPTY AND HEALTHY: no error, zero withheld, the
        # designated body printed. A row that is valid JSON and uninterpretable
        # as a designation is exactly as disqualifying as a parse failure —
        # in both cases we cannot say what is designated.
        action = rec.get("action")
        if action not in protected_module.PROTECTED_ACTIONS:
            return {}, (
                f"protected_index_malformed:line {i} has no usable action "
                f"(expected one of {list(protected_module.PROTECTED_ACTIONS)}, got {action!r})"
            )
        cid = rec.get("claim_id")
        if not isinstance(cid, str) or not cid.strip():
            return {}, (
                f"protected_index_malformed:line {i} has no usable claim_id "
                f"(expected a non-empty string, got {type(cid).__name__})"
            )
        records.append(rec)

    return protected_module.fold_protected(records), None


def _row_origin_claim(rec: dict) -> str | None:
    origin = rec.get("origin")
    if not isinstance(origin, dict):
        return None
    claim = origin.get("claim_id")
    return claim if isinstance(claim, str) and claim.strip() else None


def withhold_protected_concerns(
    rows: list[dict], root: Path | None = None
) -> tuple[list[dict], int, int, str | None]:
    """(rows, withheld, unprovenanced, error). Never mutates its input rows.

    A CONCERN IS SHOWN ONLY WHEN ITS PROVENANCE WAS EVALUATED AND CAME BACK
    CLEAN. Everything else is withheld: no ``origin.claim_id``, or an index
    this reader could not read. Round 3 published the unprovenanced ones with
    a count beside them, on the reasoning that a blanket withhold would blank
    the queue. The reviewer rejected that in one sentence — "counting
    uncertainty does not contain the text already returned" — and demonstrated
    it by putting a designated record's body in a honk that simply omitted the
    claim id. The count was accurate and the body was still on the wire.

    THE QUEUE STAYS ADDRESSABLE. Only ``concern`` is replaced; signal_id,
    source, kind, opened_at, owner, state, reason, closed_by and origin all
    survive, so a watch seat can still see that something is waiting and still
    ack it. What it cannot do is read a body nobody has cleared.

    The counts remain SEPARATE because the two populations are different
    facts: ``withheld`` is "the index says this is designated", ``unprovenanced``
    is "nothing here can say either way".
    """
    fold, error = _protected_fold(root)
    if error:
        # CANNOT READ THE INDEX -> CANNOT SHOW ANY BODY. Fail closed.
        return (
            [
                dict(r, concern=WITHHELD_CONCERN if r.get("concern") is not None else None)
                for r in rows
            ],
            sum(1 for r in rows if r.get("concern") is not None),
            0,
            error,
        )
    out: list[dict] = []
    withheld = 0
    unprovenanced = 0
    for rec in rows:
        claim = _row_origin_claim(rec)
        if claim is None:
            if rec.get("concern") is None:
                out.append(rec)
                continue
            unprovenanced += 1
            out.append(dict(rec, concern=WITHHELD_UNEVALUATED))
            continue
        if claim in fold:
            withheld += 1
            out.append(dict(rec, concern=WITHHELD_CONCERN))
            continue
        out.append(rec)
    return out, withheld, unprovenanced, None


def list_signals(
    root: Path | None = None,
    *,
    source: str | None = None,
    state: str = "open",
    limit: int = LIST_DEFAULT_LIMIT,
) -> list[dict]:
    """The individual signals, oldest first. What a watch seat acks from.

    Review F3: the honk fold pointed callers at a reader that returned
    aggregates only — "no ids, no bodies" — so the replacement could not
    supply the identifier that ``signal_ack`` (or the surviving ``nape_ack``)
    requires, and a seat could see that three honks existed without being able
    to see or close one. Counts are a dashboard; this is the queue.

    Corrupt rows are NOT listed: a row that failed validation has no
    trustworthy id to hand back. Their count travels in the envelope instead,
    so a partial list can never read as a complete one.
    """
    ledger = load_state(root)
    rows = []
    for rec in ledger.latest.values():
        if state and rec.get("state") != state:
            continue
        if source and rec.get("source") != source:
            continue
        rows.append(
            {
                "signal_id": rec.get("signal_id"),
                "source": rec.get("source"),
                "kind": rec.get("kind"),
                "opened_at": rec.get("produced_at"),
                "owner": rec.get("owner"),
                "state": rec.get("state"),
                "concern": rec.get("concern"),
                "reason": rec.get("reason"),
                "closed_by": rec.get("closed_by"),
                # PROVENANCE TRAVELS WITH THE ROW (review N1). It is what the
                # display boundary judges the concern against, and it is what
                # a human follows back to the record a row was minted from.
                "origin": rec.get("origin"),
            }
        )
    rows.sort(key=lambda r: (r.get("opened_at") or "", r.get("signal_id") or ""))
    bounded = max(1, min(int(limit or LIST_DEFAULT_LIMIT), LIST_MAX_LIMIT))
    return rows[:bounded]


def handle_signal_tool(
    name: str,
    arguments: dict | None,
    root: Path | None = None,
) -> str:
    """Tool entry point.

    THE ACTOR COMES FROM ``dispatch_context.CALLER_SEAT`` AND FROM NOWHERE
    ELSE (review N3). It used to arrive as a parameter this function trusted,
    filled by the dispatch from ``arguments["actor_seat"]`` — which meant the
    "the bridge fills this in" convention was, in fact, "any caller fills this
    in", and the reviewer closed a signal as ``seat:fixture-different-seat``
    through the real MCP handler to prove it.

    A parameter would still be a second source of identity, so there is no
    parameter. A context variable is the only shape that (a) crosses the
    ``asyncio.to_thread`` hop the dispatch makes, (b) is per-request rather
    than global, and (c) cannot be named by anything in ``arguments``.

    An unset or blank context is a REFUSAL, not a default.
    """
    arguments = arguments or {}
    try:
        if name == "signals_summary":
            # INGESTION RUNS ON READ (F4). Cheap and idempotent; skipped
            # entirely while the marker is inside its freshness bound.
            field = heartbeat_field(root, scan=True)
            source_filter = arguments.get("source")
            if source_filter and source_filter not in SOURCES:
                return json.dumps({"ok": False, "error": f"source must be one of {list(SOURCES)}"})
            if not field.get("measured"):
                # FULLY BLIND: no ledger, no scan, a stale or rewritten one.
                # Fail closed and say why; there is nothing honest to list.
                return json.dumps(
                    {
                        "ok": False,
                        "error": field["error"],
                        "ingestion": field.get("ingestion"),
                        "scanned_at": field.get("scanned_at"),
                        "refresh_started_at": field.get("refresh_started_at"),
                    }
                )
            mode = str(arguments.get("mode") or "summary")
            if mode not in ("summary", "list"):
                return json.dumps({"ok": False, "error": "mode must be 'summary' or 'list'"})
            if mode == "list":
                row_state = str(arguments.get("state") or "open")
                if row_state not in STATES:
                    return json.dumps(
                        {"ok": False, "error": f"state must be one of {list(STATES)}"}
                    )
                rows = list_signals(
                    root,
                    source=source_filter,
                    state=row_state,
                    limit=arguments.get("limit") or LIST_DEFAULT_LIMIT,
                )
                # THE DISPLAY BOUNDARY (review N1). Judged here, at the moment
                # of showing, against the designation index as it stands NOW.
                rows, withheld, unprovenanced, protected_error = withhold_protected_concerns(
                    rows, root
                )
                list_error = field.get("error")
                if protected_error:
                    list_error = (
                        f"{list_error}; {protected_error}" if list_error else protected_error
                    )
                return json.dumps(
                    {
                        "ok": True,
                        "mode": "list",
                        # PARTIAL READS SAY SO. A non-null error beside ok:true
                        # means "these rows are real, and something else could
                        # not be measured" — never "all clear".
                        "error": list_error,
                        "ingestion": field.get("ingestion"),
                        "scanned_at": field.get("scanned_at"),
                        "refresh_started_at": field.get("refresh_started_at"),
                        "partial": field.get("partial"),
                        "unreached": field.get("unreached"),
                        "source": source_filter,
                        "state": row_state,
                        "count": len(rows),
                        "corrupt_rows": field.get("corrupt_rows"),
                        "source_status": field.get("source_status"),
                        # HOW MUCH OF WHAT YOU ARE LOOKING AT IS NOT SHOWN.
                        # `withheld_protected` is the count the designation
                        # index withheld; `unprovenanced_concerns` is the
                        # count this gate could not judge either way, named so
                        # it cannot be read as "checked and clean".
                        "withheld_protected": withheld,
                        "unprovenanced_concerns": unprovenanced,
                        "signals": rows,
                    }
                )
            # SUMMARY IS THE HEARTBEAT FIELD ITSELF, not a re-fold. The reviewer
            # caught this branch calling summarize() again and publishing its
            # raw numbers, which threw away every null the heartbeat had just
            # computed: an unavailable guardian and a malformed honk source
            # came back ok:true with source open:0.
            payload = {
                "ok": True,
                "mode": "summary",
                "error": field.get("error"),
                "ingestion": field.get("ingestion"),
                "scanned_at": field.get("scanned_at"),
                # WHAT IS BEING DONE ABOUT IT. `ingestion` names the state;
                # these two name the work in flight and what a partial sweep
                # still owes, so a watch seat can tell "come back in a moment"
                # from "this is the answer".
                "refresh_started_at": field.get("refresh_started_at"),
                "partial": field.get("partial"),
                "unreached": field.get("unreached"),
                "total": field.get("total"),
                # SCOPE TRAVELS WITH THE NARROWER NUMBER, always. Publishing
                # `total_configured` without saying what it covers is how a
                # partial count comes to be read as a whole one.
                "total_configured": field.get("total_configured"),
                "total_configured_scope": field.get("total_configured_scope"),
                "not_configured": field.get("not_configured"),
                "stale_24h": field.get("stale_24h"),
                "stale_7d": field.get("stale_7d"),
                "by_source": field.get("by_source_detail"),
                "corrupt_rows": field.get("corrupt_rows"),
                "source_status": field.get("source_status"),
                "sources_degraded": field.get("sources_degraded"),
            }
            if source_filter:
                payload["by_source"] = {
                    source_filter: (field.get("by_source_detail") or {}).get(source_filter)
                }
                payload["source_status"] = {
                    source_filter: (field.get("source_status") or {}).get(source_filter, "unknown")
                }
            return json.dumps(payload)
        if name == "signal_ack":
            # ── REFUSED, NEVER IGNORED ──────────────────────────────────────
            # An ignored argument looks, from the caller's side, exactly like
            # an honoured one: the reviewed build carried a comment saying
            # native `actor_seat` was ignored while the dispatch read it. So
            # the identity-shaped argument names fail the call and the error
            # names the one that was sent.
            for forbidden in REFUSED_IDENTITY_ARGUMENTS:
                if forbidden in arguments:
                    return json.dumps(
                        {
                            "ok": False,
                            "error": (
                                f"{forbidden!r} is not an accepted argument: closed_by is "
                                "stamped from the identity the dispatch resolved, never "
                                "from the call. Remove it and retry."
                            ),
                        }
                    )
            actor = caller_seat()
            sid = str(arguments.get("signal_id") or "").strip()
            state = str(arguments.get("state") or "").strip()
            reason = arguments.get("reason")
            if not sid or not state:
                return json.dumps({"ok": False, "error": "signal_id and state are required"})
            if not (isinstance(actor, str) and actor.strip()):
                return json.dumps(
                    {
                        "ok": False,
                        "error": (
                            "no caller identity: the server could not resolve who is "
                            "closing this signal, and closed_by is never taken from "
                            "the call"
                        ),
                    }
                )
            row = ack_signal(sid, actor, state, reason, root=root)
            # THE RETURNED COPY IS GATED; THE LEDGER ROW IS NOT. The record
            # keeps the true concern — it is the record — but this response is
            # a display surface like any other, and review N1's exposure is a
            # body reaching a reader, not a body reaching the disk.
            shown, withheld, unprovenanced, protected_error = withhold_protected_concerns(
                [row], root
            )
            # BOTH COUNTS, ALWAYS, ON BOTH SURFACES (R1). Round 3 carried
            # `withheld_protected` here only when it was non-zero and dropped
            # the unprovenanced count entirely, so the one response that
            # returns a body carried the least information about whether that
            # body had been cleared. A count that appears only when it is
            # interesting is a count a reader learns to read as zero.
            payload: dict[str, Any] = {
                "ok": True,
                "row": shown[0],
                "withheld_protected": withheld,
                "unprovenanced_concerns": unprovenanced,
            }
            if protected_error:
                payload["error"] = protected_error
            return json.dumps(payload)
        return json.dumps({"ok": False, "error": f"unknown tool {name}"})
    except (ValueError, KeyError, PermissionError, LedgerUnreadable) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
