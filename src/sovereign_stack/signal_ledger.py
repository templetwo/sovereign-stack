"""Signal ledger — watch-seat instrument (mesh-20260905).

Append-only JSONL at <sovereign root>/signals/ledger.jsonl.
Latest row per signal_id wins. Never deletes. Fail-closed: an unreadable
ledger is an ERROR on the heartbeat field, never a zero count.

Do not enable ntfy from this module; SIGNAL_LEDGER_NTFY defaults off.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp.types import Tool

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


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else default_sovereign_root()


def ledger_path(root: Path | None = None) -> Path:
    return _root(root) / "signals" / "ledger.jsonl"


def scan_marker_path(root: Path | None = None) -> Path:
    return _root(root) / "signals" / "last_scan.json"


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
    return None


def _read_scan_marker(root: Path | None = None) -> tuple[dict | None, str | None]:
    """(marker, error). A marker that fails validation is NOT a marker."""
    path = scan_marker_path(root)
    if not path.exists():
        return None, None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
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


def _write_scan_marker(counts: dict, source_status: dict, root: Path | None = None) -> dict:
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
    rec = {
        "scanned_at": _now(),
        "counts": counts,
        "source_status": source_status,
        "ledger_bytes": ledger_bytes,
        "ledger_rows": ledger_rows,
        "ledger_sha256": ledger_sha,
        "max_age_seconds": marker_max_age_seconds(),
    }
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
) -> dict:
    """One ledger row.

    ``kind`` and ``concern`` are the WHAT, added 2026-09-06 for the honk fold
    (review F3). The reviewer's exact objection: the only surviving signal
    reader returned aggregates with no ids and no bodies, so the replacement
    for the retired honk reader could not supply the identifier its own ack
    tool needs, let alone tell a watch seat what it was acking. A queue you
    cannot read is not a queue.

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
    with _append_lock(root) as fh:
        if sid in load_latest(root):
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
    prev = load_latest(root).get(signal_id)
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
            kind=_text_or_none(prev.get("kind")),
            concern=_text_or_none(prev.get("concern")),
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
    return {
        "total": None if blind else total,
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


def _blind_field(error: str, ingestion: str, scanned_at: str | None = None) -> dict:
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
        "measured": False,
        "total": None,
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
            unscanned = _blind_field("not_scanned", "never")
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
        if stale_error:
            # STALE IS AN ERROR STATE, NOT A FOOTNOTE. A six-year-old marker
            # returned ingestion:"ok" on the reviewed tip.
            return _blind_field(stale_error, "stale", marker.get("scanned_at"))
        summary = summarize(root)
        error = None
        ingestion = "ok"
        claimed = sum((marker.get("counts") or {}).values())
        # Lower-bound reconciliation, KEPT as a second, independent check.
        # counts are "opened by THIS scan", and the ledger accumulates across
        # scans, so the ledger can only ever hold MORE distinct ids than one
        # scan claims to have opened. Holding fewer is arithmetic proof that
        # rows are gone. Weak on its own (see MARKER_REQUIRED) — but it costs
        # nothing and catches a case the byte-prefix check cannot: rows lost
        # from a ledger that was then re-grown to the same length.
        distinct = len(state.latest) + state.corrupt_count
        if claimed > distinct:
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
        return {
            "error": error,
            "ingestion": ingestion,
            "scanned_at": marker.get("scanned_at"),
            "measured": True,
            # THE NULLS COME STRAIGHT FROM summarize(). Nothing here recomputes
            # a count from rows it happens to hold — that recomputation is what
            # the reviewer caught the tool surface doing.
            "total": summary["total"],
            "stale_24h": summary["stale_24h"],
            "stale_7d": summary["stale_7d"],
            "by_source": {k: v["open"] for k, v in summary["by_source"].items()},
            # THE NESTED VIEW, CARRIED ON THE SAME FOLD. `signals_summary`
            # needs stale windows and oldest_unacked per source; the reviewer
            # caught it calling summarize() a second time to get them and
            # publishing that second fold's raw numbers, which threw away every
            # null computed here. One fold, both shapes, no way to diverge.
            "by_source_detail": summary["by_source"],
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
    """

    __slots__ = ("opened", "status", "skipped")

    def __init__(self, opened: int, status: str, skipped: int = 0) -> None:
        self.opened = opened
        self.status = status
        self.skipped = skipped


def _degrade(status: str, skipped: int) -> str:
    if skipped and status == "ok":
        return f"degraded:{skipped} unusable ids"
    if skipped:
        return f"{status}; {skipped} unusable ids"
    return status


def scan_honks(root: Path, owner: str = "watch-2/3") -> ScanResult:
    honks = _read_source_jsonl(root / "nape" / "honks.jsonl")
    acks = _read_source_jsonl(root / "nape" / "acks.jsonl")
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
        ):
            n += 1
        if hid in ack_ids:
            sid = signal_id_for("honk", hid)
            latest = load_latest(root).get(sid)
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
    return ScanResult(n, _degrade(status, skipped), skipped)


def scan_watchman(root: Path, owner: str = "watch-2/3") -> ScanResult:
    spool = _read_source_jsonl(root / "watchman" / "spool.jsonl")
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
        ):
            n += 1
    status = spool.status if spool.status != "absent" else "ok"
    return ScanResult(n, _degrade(status, skipped), skipped)


def scan_proposals(root: Path, owner: str = "watch-2/3") -> ScanResult:
    n = 0
    skipped = 0
    notes: list[str] = []
    for substrate in ("grok_bridge", "openai_bridge", "antigravity_connector"):
        d = root / substrate / "pending_writes"
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
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
            ):
                n += 1
            sid = signal_id_for("proposal", key)
            latest = load_latest(root).get(sid)
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
    return ScanResult(n, _degrade("ok", skipped), skipped)


def scan_halts(root: Path, owner: str = "watch-2/3") -> ScanResult:
    n = 0
    d = root / "daemons" / "halts"
    if not d.is_dir():
        return ScanResult(0, "ok")
    for f in sorted(d.glob("*.md")):
        if open_signal(
            source="halt",
            native_id=f.name,
            produced_at=_iso_from_mtime(f),
            owner=owner,
            root=root,
            kind="halt",
            concern=_text_or_none(f.name),
        ):
            n += 1
    return ScanResult(n, "ok")


def scan_decisions(root: Path, owner: str = "watch-2/3") -> ScanResult:
    n = 0
    d = root / "decisions"
    if not d.is_dir():
        return ScanResult(0, "ok")
    for f in sorted(d.glob("metabolize_*.md")):
        if open_signal(
            source="decision",
            native_id=f.name,
            produced_at=_iso_from_mtime(f),
            owner=owner,
            root=root,
            kind="metabolize",
            concern=_text_or_none(f.name),
        ):
            n += 1
    return ScanResult(n, "ok")


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


def scan_guardian(root: Path, owner: str = "watch-2/3", provider=None) -> ScanResult:
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
        ):
            n += 1
    return ScanResult(n, _degrade("ok", skipped), skipped)


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


def scan_threads(root: Path, owner: str = "watch-2/3") -> ScanResult:
    """One signal per thread id ACROSS every shard. Latest timestamp wins.

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
    degraded = 0
    unreadable: list[str] = []
    # PASS 1 — decide, mutating nothing.
    latest_by_id: dict[str, tuple[dict, Path, datetime | None]] = {}
    for f in iter_thread_shards(d):
        read = _read_source_jsonl(f)
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
        ):
            n += 1
        sid = signal_id_for("thread", native)
        resolved = rec.get("resolved") is True or rec.get("status") == "resolved"
        latest = load_latest(root).get(sid)
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
        elif not resolved and latest.get("state") != "open":
            # THE HALF THE OLD CODE HAD NO WORD FOR. It could only ever close a
            # signal, so once an obsolete terminal state landed there was no
            # path back. A thread whose latest record across all shards is OPEN
            # must not sit in the ledger as acted; re-open it, and say why in
            # the row's own reason so the reversal is legible in the audit
            # trail rather than looking like a duplicate.
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
                ),
                root,
            )
    status = "ok"
    if unreadable:
        status = "unreadable:" + "; ".join(sorted(unreadable)[:5])
    elif degraded:
        status = f"degraded:{degraded} unparseable lines"
    return ScanResult(n, status, degraded)


def scan_all(root: Path | None = None, owner: str = "watch-2/3", guardian_provider=None) -> dict:
    """Owned ingestion path. Writes signals/last_scan.json. Does not install a worker.

    EVERY SOURCE IS ISOLATED. The branch as reviewed ran the seven scanners
    as values in one dict literal, so the first one to raise took every later
    source with it and the marker was never written — leaving a stale marker
    certifying a scan that had actually aborted (reviewer finding 6). A
    source that raises is now recorded as ``failed:<Exc>`` and the scan
    continues; the marker carries that status to every reader.
    """
    r = _root(root)
    scanners = (
        ("honk", lambda: scan_honks(r, owner)),
        ("watchman", lambda: scan_watchman(r, owner)),
        ("proposal", lambda: scan_proposals(r, owner)),
        ("halt", lambda: scan_halts(r, owner)),
        ("decision", lambda: scan_decisions(r, owner)),
        ("guardian", lambda: scan_guardian(r, owner, provider=guardian_provider)),
        ("thread", lambda: scan_threads(r, owner)),
    )
    counts: dict[str, int] = {}
    source_status: dict[str, str] = {}
    for name, fn in scanners:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 — one bad source must not blind the rest
            counts[name] = 0
            source_status[name] = f"failed:{exc.__class__.__name__}: {exc}"
            continue
        counts[name] = result.opened
        source_status[name] = result.status
    _write_scan_marker(counts, source_status, r)
    return {"counts": counts, "source_status": source_status}


def ensure_scanned(
    root: Path | None = None,
    owner: str = "watch-2/3",
    guardian_provider=None,
    *,
    now: datetime | None = None,
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
    its own freshness bound, so a burst of reads costs one sweep, not N.

    A FAILURE IS RETURNED, NEVER SWALLOWED. ``heartbeat_field`` turns it into
    an explicit error rather than serving the previous marker's counts, which
    would be "stale but ok" — the exact shape the review named.
    """
    try:
        marker, marker_error = _read_scan_marker(root)
        if (
            marker is not None
            and not marker_error
            and _marker_staleness(marker, now) is None
            and _check_marker_integrity(marker, root) is None
        ):
            return None
        scan_all(root, owner, guardian_provider)
        return None
    except Exception as exc:  # noqa: BLE001 — reported, not raised; see docstring
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
            "from the caller identity the DISPATCH resolved — actor_seat is filled "
            "in by the bridge from the verified seat identity, not composed by the "
            "caller, and a call with no resolvable actor is refused rather than "
            "stamped 'seat:None'. The producer of a source cannot close its own "
            "signal. Get the signal_id from signals_summary(mode='list')."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "signal_id": {"type": "string"},
                "state": {"type": "string", "enum": list(CLOSE_STATES)},
                "reason": {"type": "string"},
                "actor_seat": {
                    "type": "string",
                    "description": (
                        "THE BRIDGE FILLS THIS IN from the verified seat identity "
                        "(same convention as source_instance on open_thread). A "
                        "seat calling natively may omit it and the server resolves "
                        "its own; a value here never overrides a dispatch-resolved "
                        "identity, and an empty one is a refusal, not a default."
                    ),
                },
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
            }
        )
    rows.sort(key=lambda r: (r.get("opened_at") or "", r.get("signal_id") or ""))
    bounded = max(1, min(int(limit or LIST_DEFAULT_LIMIT), LIST_MAX_LIMIT))
    return rows[:bounded]


def handle_signal_tool(
    name: str,
    arguments: dict | None,
    root: Path | None = None,
    actor: str | None = None,
) -> str:
    """Tool entry point.

    ``actor`` is supplied by the DISPATCH LAYER from identity the server
    resolved for itself — never read out of ``arguments``. A call that
    arrives with no resolvable actor is REFUSED, not defaulted: the whole
    point of producer separation is that "who closed this" cannot be a value
    the closer supplies.
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
                return json.dumps(
                    {
                        "ok": True,
                        "mode": "list",
                        # PARTIAL READS SAY SO. A non-null error beside ok:true
                        # means "these rows are real, and something else could
                        # not be measured" — never "all clear".
                        "error": field.get("error"),
                        "ingestion": field.get("ingestion"),
                        "scanned_at": field.get("scanned_at"),
                        "source": source_filter,
                        "state": row_state,
                        "count": len(rows),
                        "corrupt_rows": field.get("corrupt_rows"),
                        "source_status": field.get("source_status"),
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
                "total": field.get("total"),
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
            return json.dumps({"ok": True, "row": row})
        return json.dumps({"ok": False, "error": f"unknown tool {name}"})
    except (ValueError, KeyError, PermissionError, LedgerUnreadable) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
