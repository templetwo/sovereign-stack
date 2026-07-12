"""
Provenance Module — Claim Identity, Receipts, Supersession (v1.7.0 keystone)

The chronicle's truth layer. Three primitives, all pure logic:

1. **Claim identity** — every insight has a `claim_id` derived on read
   (sha256 over timestamp + domain + content, unit-separated), never
   stored. Edit a historical line and every pointer to it visibly
   dangles: the orphaning IS the tamper-evidence. Full 64-hex is
   canonical; 16-hex is display-only.

2. **Receipts** — `verified_by` grammar for record_insight. A receipt
   pointing at nothing is unrecordable (fail-closed on dangling /
   ambiguous / malformed); a receipt pointing at changed bytes is
   recordable but permanently stamped `mismatch`, so an entry can never
   wear a verification it didn't earn. `claim:` refs stamp `cites`,
   never `verified` — no citation laundering.

3. **Supersession ledger** — append-only supersessions.jsonl is the
   canonical source; the entry's `supersedes` field is a denormalized
   breadcrumb. Fold = latest action per predecessor wins, revokes
   nullify. One successor per predecessor; amend by superseding the
   successor.

No MCP coupling, no directory creation at import — witness.py-style
pure data in, verdicts out. All paths parameterized (defaults point at
~/.sovereign/chronicle). Integration (memory.py / server.py wiring)
lives elsewhere.
"""

import contextlib
import errno
import fcntl
import functools
import hashlib
import json
import os
import re
import stat
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

# ASCII unit separator: cannot appear in JSON-decoded chronicle text by
# accident, so ("ab","c") and ("a","bc") can never collide in the preimage.
_FIELD_SEP = "\x1f"

RECEIPT_KINDS = ("archive", "file", "claim", "cmd", "url", "human")
_RECEIPT_ALLOWED_KEYS = {"kind", "ref", "sha256", "note"}

SUPERSESSION_ACTIONS = ("supersede", "revoke", "retire")

CARRY_FORWARD_MAX_CHARS = 500
_PREVIEW_CHARS = 120

# Lived-ground-truth (v1.7.2): vantage values that mark an entry as a
# human-authored lived/attested account rather than a technical claim.
# These are the ONLY vantages exempt from the unreceipted-ground-truth nag
# (nape_daemon + season_review hygiene): the exemption is justified solely by
# HUMAN AUTHORSHIP of an experience that cannot carry an external receipt.
# A model's own read is deliberately NOT here — it belongs in layer=hypothesis,
# not exempt ground_truth. Absent/seat-tag/external vantages are never exempt,
# so nothing dodges a receipt by accident, only by being honestly marked lived.
LIVED_VANTAGES = frozenset({"human_observation", "human_attestation", "witnessed_account"})

_FULL_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_CLAIM_REF_RE = re.compile(r"^[0-9a-f]{1,64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Legacy text markers from the pre-ledger era ("CORRECTED:", "DEFINITIVE",
# "supersedes ...") — season_review uses these to find supersession
# candidates that were declared in prose before the ledger existed.
LEGACY_MARKER_RE = re.compile(r"CORRECTED|DEFINITIVE|supersedes")


def default_sovereign_root() -> Path:
    """The live sovereign root. Computed on call, never at import."""
    return Path(os.environ.get("SOVEREIGN_ROOT") or (Path.home() / ".sovereign"))


def default_chronicle_root() -> Path:
    """
    The live chronicle root. Computed on call, never at import.

    Honors SOVEREIGN_CHRONICLE, then SOVEREIGN_ROOT, then ~/.sovereign —
    the same precedence server.py resolves its roots by. Unset, this is
    ~/.sovereign/chronicle exactly as before; set, a test (or a second
    stack) never touches Anthony's lived records.
    """
    override = os.environ.get("SOVEREIGN_CHRONICLE")
    if override:
        return Path(override)
    return default_sovereign_root() / "chronicle"


def default_supersessions_path() -> Path:
    """The live supersession ledger path. Computed on call, never at import."""
    return default_chronicle_root() / "supersessions.jsonl"


# ── The chronicle write lock ─────────────────────────────────────────────────
#
# Serialization has to live in the SYNC layer, held by the code that actually
# touches the bytes. An asyncio.Lock in the dispatcher cannot do this job: it
# unwinds on CancelledError (a client disconnect would release it mid-write),
# and every inline caller — close_session, resolve_thread, a daemon, a CLI —
# bypasses it entirely.
#
# What it protects: chronicle mutators do read-modify-write. record_insight
# reads for dedup then appends; metabolism reads a whole JSONL, mutates, and
# REWRITES it. Once record_insight runs in a worker thread, an append landing
# inside a rewrite's read→write window is silently clobbered — the insight is
# gone. Every mutator takes this lock across its full read-modify-write span,
# so no dispatch path can slip between another's read and its write.
#
# RLock, not Lock: record_insight → append_supersession and resolve_thread →
# record_insight both nest, and a plain Lock would deadlock on itself.
#
# The threading lock alone is NOT enough, and the gap is measured: a writer
# process appending while an archiver process rewrites the same insights file
# lost 11 of 60 entries, silently. The same collision inside one process loses
# zero — the RLock closes that half. But a threading lock lives in ONE process,
# and the live topology has several: the SSE server, four launchd daemons, a
# Desktop stdio server, and CLI entry points, all against the same chronicle.
#
# So an advisory fcntl.flock on a lockfile is taken INSIDE the RLock. Anthony
# ratified this shape by voice on 2026-07-12 ("advisory file lock now, every
# writer, every process"); the append-only-ledger remodel is the better long
# term answer and is deferred.
#
# Order matters. RLock (outer) → flock (inner):
#   - flock() locks attach to the open file DESCRIPTION, so two threads sharing
#     one fd do not block each other. flock alone would NOT close the in-process
#     race the RLock already closes. Both are load-bearing.
#   - The RLock serializes threads before any of them touches the fd, so the
#     depth counter below needs no lock of its own — the RLock is holding it.
#
# The receipt hash stays OUTSIDE this lock (hoisted in memory.record_insight).
# It is the only unbounded caller-supplied read in the write path; hashing a
# file on a wedged disk while holding a CROSS-PROCESS lock would stall every
# process on the machine — the freeze we just fixed, made distributed.
_CHRONICLE_WRITE_LOCK = threading.RLock()

# Per-path RLocks, not one global. A single RLock held across the flock's
# spin-wait means a foreign process holding the REFLECTIONS lock stalls every
# CHRONICLE writer, even though the chronicle lock is free — measured at 2.81s.
# Now that reflections is a second lockfile, the trees must make independent
# progress. _RLOCKS_GUARD only protects the dict, never a spin-wait.
_RLOCKS: dict[str, threading.RLock] = {}
_RLOCKS_GUARD = threading.Lock()


def _rlock_for(key: str) -> threading.RLock:
    if key == str(chronicle_lock_path()):
        # the default chronicle keeps the named lock, which tests probe directly
        return _CHRONICLE_WRITE_LOCK
    with _RLOCKS_GUARD:
        return _RLOCKS.setdefault(key, threading.RLock())


_FLOCK_FDS: dict[str, int] = {}
# Per-path, not a single int. A global counter makes a nested lock on a
# DIFFERENT root look like a re-entry, so the inner root is never flocked at
# all — silently unprotected. One chronicle today, but the reflections tree is
# a second lockfile as of this change, and they nest.
_FLOCK_DEPTH: dict[str, int] = {}

LOCK_ACQUIRE_TIMEOUT_SECONDS = 30.0
LOCK_POLL_SECONDS = 0.01


class ChronicleLockError(RuntimeError):
    """The cross-process write lock could not be acquired."""


def chronicle_lock_path(chronicle_root: Path | None = None) -> Path:
    root = Path(chronicle_root) if chronicle_root else default_chronicle_root()
    return root / ".write.lock"


def reflections_lock_path(reflections_dir: Path | None = None) -> Path:
    """The reflections tree is a SIBLING of chronicle/, not under it, and it has
    its own cross-process race: ack_reflection rewrites the whole day-file that
    synthesis_daemon (a separate launchd process) appends to. Both sides must
    take THIS path — a lock on the chronicle root would serialize against
    nothing."""
    root = Path(reflections_dir) if reflections_dir else default_sovereign_root() / "reflections"
    return root / ".write.lock"


def _acquire_flock(path: Path) -> None:
    """Take LOCK_EX, blocking up to the timeout. Never proceeds unlocked."""
    key = str(path)
    fd = _FLOCK_FDS.get(key)
    if fd is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        _FLOCK_FDS[key] = fd

    deadline = time.monotonic() + LOCK_ACQUIRE_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise ChronicleLockError(
                    f"could not acquire the chronicle write lock at {path} within "
                    f"{LOCK_ACQUIRE_TIMEOUT_SECONDS}s — another process is holding it. "
                    "Refusing to write unlocked: an unserialized write can be silently "
                    "clobbered by a concurrent whole-file rewrite."
                ) from None
            time.sleep(LOCK_POLL_SECONDS)


@contextlib.contextmanager
def scoped_write_lock(lock_path: Path):
    """
    Serialize writers on one tree: in-process (RLock) + cross-process (flock).

    Re-entrant PER PATH. record_insight → append_supersession nests on the same
    lockfile; a chronicle write nested inside a reflections write does not, and
    each must hold its own flock.

    NEVER CALL THIS FROM THE EVENT LOOP. Acquisition spin-waits up to
    LOCK_ACQUIRE_TIMEOUT_SECONDS, so a foreign process holding the lock would
    freeze every seat on the stack — the 2026-07-10 wedge, made worse because
    another process could trigger it. Every async caller offloads via
    asyncio.to_thread; that is load-bearing, not stylistic.
    """
    key = str(lock_path)
    with _rlock_for(key):
        depth = _FLOCK_DEPTH.get(key, 0)
        if depth == 0:
            _acquire_flock(lock_path)
        _FLOCK_DEPTH[key] = depth + 1
        try:
            yield
        finally:
            _FLOCK_DEPTH[key] -= 1
            if _FLOCK_DEPTH[key] == 0:
                del _FLOCK_DEPTH[key]
                fd = _FLOCK_FDS.get(key)
                if fd is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)


def chronicle_write_lock(chronicle_root: Path | None = None):
    """Serialize chronicle mutators. `with chronicle_write_lock(root):`"""
    return scoped_write_lock(chronicle_lock_path(chronicle_root))


def reflections_write_lock(reflections_dir: Path | None = None):
    """Serialize reflections mutators. `with reflections_write_lock(dir):`"""
    return scoped_write_lock(reflections_lock_path(reflections_dir))


def under_chronicle_write_lock(fn: Callable) -> Callable:
    """
    Run a whole mutator under the chronicle write lock.

    For the guard-then-append shapes (supersede_insight, link_threads) whose
    critical section is the entire function: they resolve a fold, check it,
    then append against it. Held here, no dispatch path can bypass it.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # Lock the chronicle the function is actually writing, not the default.
        # Locking the default while writing a different root protects the wrong
        # file — and made a test suite flock Anthony's live chronicle.
        root = kwargs.get("chronicle_root")
        with chronicle_write_lock(root):
            return fn(*args, **kwargs)

    return wrapper


# ── Exceptions ───────────────────────────────────────────────────────────────


class ProvenanceError(ValueError):
    """Base for provenance failures (malformed ids, refs, params)."""


class ClaimNotFoundError(ProvenanceError):
    """No chronicle entry derives to the requested claim id / prefix."""


class AmbiguousClaimError(ProvenanceError):
    """A claim-id prefix matches more than one distinct claim."""


class ReceiptError(ProvenanceError):
    """A receipt is malformed, dangling, or ambiguous — the write must be rejected."""


class SupersessionError(ProvenanceError):
    """A supersession guard fired (self, cycle, double) or a record is invalid."""


# ── Claim identity ───────────────────────────────────────────────────────────


def _preimage_field(entry: dict, key: str) -> str:
    """
    Field accessor for the claim preimage: absent -> "", None -> "",
    non-string survivors coerced via str() so a malformed historical
    line still derives an id instead of crashing the read path.
    """
    value = entry.get(key, "")
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def derive_claim_id(entry: dict) -> str:
    """
    Derive the canonical claim id for a chronicle entry.

    sha256 over timestamp + US + domain + US + content (US = "\\x1f").
    Tolerant of absent fields (missing -> empty string). Derived on
    read, NEVER stored — annotation fields (layer, retired_by,
    intensity, ...) are outside the preimage, so in-place layer rewrites
    never shift ids, while edits to the identity triple visibly orphan
    every pointer.

    Returns the full 64-hex digest (canonical). Use display_id() for
    the 16-hex human form.
    """
    preimage = (
        _preimage_field(entry, "timestamp")
        + _FIELD_SEP
        + _preimage_field(entry, "domain")
        + _FIELD_SEP
        + _preimage_field(entry, "content")
    )
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def display_id(claim_id: str) -> str:
    """16-hex display truncation. The full 64-hex remains canonical."""
    return claim_id[:16]


# ── Chronicle scanning & prefix resolution ───────────────────────────────────


def _iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield parsed objects from a JSONL file, skipping blank/corrupt lines."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def iter_chronicle_entries(chronicle_root: Path) -> Iterator[tuple[dict, Path, str]]:
    """
    Yield (entry, file, location) for every insight line under the
    chronicle root: insights/**/*.jsonl (location "insights") PLUS
    _quarantine_*/**/*.jsonl (location "quarantine") — quarantine-aware
    so claim pointers survive the one sanctioned file-move convention.
    """
    root = Path(chronicle_root)
    scans = [
        ("insights", sorted(root.glob("insights/**/*.jsonl"))),
        ("quarantine", sorted(root.glob("_quarantine_*/**/*.jsonl"))),
    ]
    for location, files in scans:
        for jsonl_file in files:
            for entry in _iter_jsonl(jsonl_file):
                yield entry, jsonl_file, location


def resolve_claim(
    claim_ref: str, chronicle_root: Path, *, couple: bool = False
) -> tuple[dict, Path, str]:
    """
    Resolve a full claim id or unique prefix (git-style) to its entry.

    Scans insights/ and _quarantine_*/ under the chronicle root, deriving
    ids on read. Byte-identical duplicate lines share one claim id and
    resolve to the first occurrence — ambiguity means multiple DISTINCT
    ids match the prefix.

    Args:
        claim_ref: full claim id or unique prefix.
        chronicle_root: the chronicle root.
        couple: protected-source gate (spec §5.4). Default False — the BARE
            read internal machinery relies on (the decoupling audit's own
            content scan, designation, supersedes re-derivation all need the
            real content/id). Set True ONLY for a FULL-CONTENT model-facing
            surface (e.g. inspect_claim's ``entry``): when the resolved entry
            is protected, the returned entry is its coupled-or-withheld form
            (full content + stakes attached, or the fail-closed
            ProtectedStakesUnavailable sentinel). A coupled return must NEVER
            be re-truncated by the caller — a preview of coupled content
            re-decouples it; preview surfaces withhold instead (see
            protected.withhold_preview). protected is imported lazily here to
            avoid the protected->provenance import cycle.

    Returns:
        (entry, file, location) — location is "insights" or "quarantine".
        With couple=True on a protected record, ``entry`` is the coupled /
        withheld form; ``file`` / ``location`` still point at the source.

    Raises:
        ProvenanceError: malformed ref (empty / non-hex / over 64 chars).
        ClaimNotFoundError: nothing derives to this id or prefix.
        AmbiguousClaimError: prefix matches more than one distinct claim.
    """
    ref = (claim_ref or "").strip().lower()
    if not _CLAIM_REF_RE.match(ref):
        raise ProvenanceError(
            f"malformed claim id {claim_ref!r}: expected 1-64 lowercase hex characters"
        )

    matches: dict[str, tuple[dict, Path, str]] = {}
    for entry, jsonl_file, location in iter_chronicle_entries(chronicle_root):
        cid = derive_claim_id(entry)
        if cid.startswith(ref) and cid not in matches:
            matches[cid] = (entry, jsonl_file, location)

    if not matches:
        raise ClaimNotFoundError(f"no chronicle entry matches claim id {claim_ref!r}")
    if len(matches) > 1:
        shown = ", ".join(display_id(cid) for cid in sorted(matches))
        raise AmbiguousClaimError(
            f"claim id prefix {claim_ref!r} matches {len(matches)} claims"
            f" ({shown}); supply more characters"
        )
    entry, jsonl_file, location = next(iter(matches.values()))
    if couple:
        # Lazy import — protected imports provenance at module level, so a
        # top-level import here would cycle. The codebase uses lazy imports
        # for exactly this (cf. server.py boot-surface imports).
        from sovereign_stack import protected as _protected

        fold = _protected.load_protected_fold(chronicle_root)
        entry = _protected.couple_or_withhold_protected(entry, fold, chronicle_root)
    return entry, jsonl_file, location


# ── Bounded filesystem reads (the 2026-07-10 freeze) ─────────────────────────
#
# verify_receipt_at_write used to re-hash a caller-supplied path with an
# unbounded, synchronous read_bytes(). The path was an iCloud dataless stub:
# stat() reported an ordinary regular file, the read blocked forever on
# materialization, and because record_insight is called synchronously from the
# async dispatch on a single-worker event loop, the whole stack wedged behind
# it — heartbeats included. Every hash of bytes that a caller or an index
# points us at now goes through here: classified by stat before anything is
# opened, read in chunks, and bounded by a wall clock at both layers.

STAT_TIMEOUT_SECONDS = 1.0
READ_TIMEOUT_SECONDS = 5.0
_READ_CHUNK_BYTES = 1024 * 1024
# There is deliberately no size cap. The wall clock ALREADY bounds the read —
# a size limit adds no hang protection, it only turns a legitimate receipt into
# a hard write refusal. The entropy program ships raw NDJSON as its primary
# record (house law #7); a 300 MB run file hashes in ~0.1s here, well inside
# READ_TIMEOUT_SECONDS, and must stamp "verified" like any other file. A file
# too slow to hash in the budget degrades to "attested" with a reason — bound
# by TIME, never by size, and never by rejecting the write.

# macOS sets this on a file-provider (iCloud) placeholder whose bytes are not
# on this machine. Opening one triggers a materialization download that can
# block indefinitely. stat() is honest about the flag, so refuse before opening.
_SF_DATALESS = 0x40000000
_ICLOUD_STUB_SUFFIX = ".icloud"


class BoundedIOError(Exception):
    """A bounded filesystem probe or read did not complete inside its budget."""


def _run_bounded(fn: Callable[[], object], timeout: float) -> object:
    """
    Run a blocking filesystem call in a daemon thread; abandon it if it
    outlives `timeout`.

    A wedged open()/read() cannot be cancelled from Python. The thread is a
    daemon so an abandoned one never holds up interpreter exit, and it frees
    itself if the kernel ever answers. What matters is that the CALLER returns.

    Raises:
        BoundedIOError: the call outlived its budget.
        Exception: whatever fn raised, re-raised in the caller's thread.
    """
    box: dict = {}

    def _runner() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised in the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise BoundedIOError(f"did not complete within {timeout:g}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def classify_file_target(path: Path) -> str | None:
    """
    Stat-only hazard classification for a path we are about to hash.

    Returns None when the path is a plain, local, in-budget regular file;
    otherwise a short reason naming the hazard class. Nothing is opened —
    stat answers for FIFOs, devices, directories, symlink loops and dataless
    placeholders without triggering the block that opening them would cause.

    Raises:
        BoundedIOError: the stat itself never answered (wedged mount).
    """
    if path.name.endswith(_ICLOUD_STUB_SUFFIX):
        return "dangling — iCloud placeholder stub, not the file itself"

    def _probe() -> tuple[os.stat_result | None, str | None]:
        try:
            return os.stat(path), None
        except FileNotFoundError:
            return None, "dangling — file does not exist"
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return None, "dangling — symlink loop"
            return None, f"dangling — file is unstattable ({exc.strerror or exc})"

    st, reason = _run_bounded(_probe, STAT_TIMEOUT_SECONDS)  # type: ignore[misc]
    if reason is not None:
        return reason
    if not stat.S_ISREG(st.st_mode):
        return "dangling — not a regular file (directory, fifo, socket or device)"
    # st_flags is absent on Linux and None on a stat_result built without the
    # platform extras — neither is a dataless flag, both must not blow up here.
    if (getattr(st, "st_flags", 0) or 0) & _SF_DATALESS:
        return "dangling — dataless placeholder; the bytes are not on this machine"
    return None


def hash_file_bounded(path: Path) -> str:
    """
    sha256 a regular file in chunks under a wall clock.

    Two layers, because they catch different failures: the inner deadline
    aborts a slow-but-progressing read cooperatively (the thread exits); the
    outer bound catches a read that never progresses at all — an open() that
    blocks on materialization returns nothing to check a deadline against.

    Assumes classify_file_target has already cleared the path.

    Raises:
        BoundedIOError: the hash did not complete inside the budget.
        OSError: the file could not be opened or read.
    """

    def _hash() -> str:
        digest = hashlib.sha256()
        deadline = time.monotonic() + READ_TIMEOUT_SECONDS
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                if time.monotonic() > deadline:
                    raise BoundedIOError(f"read did not complete within {READ_TIMEOUT_SECONDS:g}s")
        return digest.hexdigest()

    return _run_bounded(_hash, READ_TIMEOUT_SECONDS + 0.5)  # type: ignore[return-value]


def read_blob_bounded(path: Path) -> tuple[str | None, str | None]:
    """
    Read an archive-indexed blob as utf-8 under the same bounds.

    (content, None) when the bytes came back; (None, verdict) when they did
    not, where verdict is "missing" (the blob is gone or unopenable) or
    "unreadable" (a hazard class, or the read never finished — a dataless or
    wedged ~/.sovereign blocks the loop exactly like a caller's bad path did).

    The archive layer's paths come from its own index rather than from a
    caller, which makes them trustworthy but not fast: same disks, same
    hazard, same bound. Never raises, never blocks past the budget.
    """
    try:
        hazard = classify_file_target(path)
    except BoundedIOError:
        return None, "unreadable"
    if hazard is not None:
        return None, "missing" if "does not exist" in hazard else "unreadable"

    def _read() -> str:
        return path.read_text(encoding="utf-8")

    try:
        return _run_bounded(_read, READ_TIMEOUT_SECONDS), None  # type: ignore[return-value]
    except BoundedIOError:
        return None, "unreadable"
    except OSError:
        return None, "missing"


def preflight_file_receipt(receipt: dict, position: int | None = None) -> None:
    """
    Reject hazard-class file receipts BEFORE the write does any work.

    Runs beside validate_receipt_shape in record_insight, so a receipt
    pointing at a fifo, a device, a directory or a dataless placeholder never
    reaches the dedup check, the ledger, or the hash. Size is NOT a hazard —
    a big file that hashes fast is a legitimate receipt.

    A stat that never answers is NOT rejected here — a wedged mount is not
    the caller's error, and verify_receipt_at_write degrades it honestly to
    "attested". Only definitive hazards raise.

    Raises:
        ReceiptError: naming the offending receipt.
    """
    if not isinstance(receipt, dict) or receipt.get("kind") != "file":
        return
    ref = receipt.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        return
    try:
        hazard = classify_file_target(Path(ref.strip()).expanduser())
    except BoundedIOError:
        return
    if hazard is not None:
        raise ReceiptError(f"{_receipt_name(receipt, position)}: {hazard}")


# ── Receipt grammar ──────────────────────────────────────────────────────────


def _receipt_name(receipt: object, position: int | None = None) -> str:
    """Human-legible handle for the offending receipt in error messages."""
    pos = f"receipt #{position} " if position is not None else "receipt "
    if isinstance(receipt, dict) and receipt.get("ref"):
        return f"{pos}{receipt.get('kind', '?')}:{receipt['ref']}"
    return f"{pos}{json.dumps(receipt, default=str)[:120]}"


def validate_receipt_shape(receipt: object, position: int | None = None) -> dict:
    """
    Validate a receipt against the grammar {kind, ref, sha256?, note?}.

    Fail-closed: unknown kinds, missing/empty ref, missing sha256 on
    file-kind, and ANY key outside the grammar are rejected — in
    particular a caller-supplied "checked_at_write" is a forged stamp
    and is unrecordable by construction.

    Returns the receipt (unchanged) for chaining.

    Raises:
        ReceiptError: naming the offending receipt.
    """
    name = _receipt_name(receipt, position)
    if not isinstance(receipt, dict):
        raise ReceiptError(f"{name}: receipt must be a dict {{kind, ref, sha256?, note?}}")
    extra = set(receipt) - _RECEIPT_ALLOWED_KEYS
    if extra:
        raise ReceiptError(
            f"{name}: unknown receipt key(s) {sorted(extra)} — stamps are write-time only"
        )
    kind = receipt.get("kind")
    if kind not in RECEIPT_KINDS:
        raise ReceiptError(f"{name}: unknown receipt kind {kind!r} (valid: {RECEIPT_KINDS})")
    ref = receipt.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ReceiptError(f"{name}: receipt ref must be a non-empty string")
    sha256 = receipt.get("sha256")
    if kind == "file" and sha256 is None:
        raise ReceiptError(f"{name}: file receipts require sha256 (64-hex of the file bytes)")
    if sha256 is not None and (not isinstance(sha256, str) or not _SHA256_RE.match(sha256)):
        raise ReceiptError(f"{name}: sha256 must be 64 lowercase hex characters")
    note = receipt.get("note")
    if note is not None and not isinstance(note, str):
        raise ReceiptError(f"{name}: note must be a string")
    return receipt


def _read_archive_index(chronicle_root: Path) -> list[dict]:
    """Read archives/index.jsonl provenance records (file order)."""
    return list(_iter_jsonl(Path(chronicle_root) / "archives" / "index.jsonl"))


def verify_archive_ref(ref: str, chronicle_root: Path) -> str:
    """
    Re-hash an archived exchange against its index record.

    Replicates the archive layer's recall_exchange integrity check
    (memory.py): resolve full id or unique prefix in archives/index.jsonl,
    read the bytes at the recorded path, recompute sha256 over the
    utf-8 text, compare to the indexed hash.

    Returns one of "verified" | "mismatch" | "missing" | "unreadable" |
    "ambiguous" | "unknown" — same vocabulary as recall_exchange.
    "unreadable" means the bytes could not be read inside the wall clock
    (see read_blob_bounded): never treat it as a verification.
    """
    records = _read_archive_index(chronicle_root)
    matches = [r for r in records if r.get("archive_id", "").startswith(ref)]
    if not matches:
        return "unknown"
    exact = [r for r in matches if r.get("archive_id") == ref]
    if exact:
        record = exact[-1]
    elif len({r.get("archive_id") for r in matches}) > 1:
        return "ambiguous"
    else:
        record = matches[-1]

    content, verdict = read_blob_bounded(Path(record.get("path", "")))
    if content is None:
        return verdict
    recomputed = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return "verified" if recomputed == record.get("sha256") else "mismatch"


def verify_receipt_at_write(
    receipt: dict, chronicle_root: Path, position: int | None = None
) -> dict:
    """
    Validate one receipt and stamp its write-time verdict.

    Semantics (spec section 2 — reject dangling, record mismatch):
      - archive: re-hash via the archive index. unknown/ambiguous/missing
        bytes -> REJECTED (a receipt pointing at nothing is unrecordable);
        resolvable -> stamped "verified" | "mismatch".
      - file: requires sha256; missing file / hazard class (fifo, device,
        directory, dataless placeholder) -> REJECTED; present -> re-hashed
        under a wall clock (chunked) -> "verified" | "mismatch". Size is not
        a hazard: a 300 MB run file that hashes inside the budget verifies.
      - claim: resolved against insights/ + quarantine; dangling or
        ambiguous -> REJECTED; resolvable -> stamped "cites", NEVER
        "verified" (a citation is not a verification).
      - cmd / url / human: stamped "attested" (no live check at write).

    Degradation (the 2026-07-10 freeze): when the bytes exist but cannot be
    read inside the wall clock — a wedged mount, a stat that never answers —
    the receipt is stamped "attested" with an "unverified_reason", NOT
    "verified", and the write proceeds. Three ways to get this wrong, all
    worse: hanging (froze the stack), rejecting the whole write (a slow disk
    is not the author's error), and stamping "verified" without hashing
    (would corrupt provenance itself — the one thing this module exists to
    prevent). "attested" already means "nobody re-checked this", which is
    exactly what is true.

    Returns a COPY of the receipt with "checked_at_write" added (and
    "unverified_reason" when degraded); the input dict is never mutated.

    Raises:
        ReceiptError: naming the offending receipt.
    """
    validate_receipt_shape(receipt, position)
    name = _receipt_name(receipt, position)
    kind = receipt["kind"]
    ref = receipt["ref"].strip()

    unverified_reason: str | None = None

    if kind == "archive":
        verdict = verify_archive_ref(ref, chronicle_root)
        if verdict == "unknown":
            raise ReceiptError(f"{name}: dangling — no archive record matches this id")
        if verdict == "ambiguous":
            raise ReceiptError(f"{name}: ambiguous archive id prefix; supply more characters")
        if verdict == "missing":
            raise ReceiptError(
                f"{name}: dangling — index record exists but the bytes are gone from disk"
            )
        if verdict == "unreadable":
            stamp = "attested"
            unverified_reason = "archive bytes could not be read within the wall clock"
        else:
            stamp = verdict  # "verified" | "mismatch"
    elif kind == "file":
        path = Path(ref).expanduser()
        try:
            hazard = classify_file_target(path)
        except BoundedIOError as exc:
            stamp = "attested"
            unverified_reason = f"file could not be stat'd ({exc})"
        else:
            if hazard is not None:
                raise ReceiptError(f"{name}: {hazard}")
            try:
                recomputed = hash_file_bounded(path)
            except BoundedIOError as exc:
                stamp = "attested"
                unverified_reason = f"file could not be hashed ({exc})"
            except OSError as exc:
                raise ReceiptError(f"{name}: dangling — file is unreadable ({exc})") from exc
            else:
                stamp = "verified" if recomputed == receipt["sha256"] else "mismatch"
    elif kind == "claim":
        try:
            resolve_claim(ref, chronicle_root)
        except ClaimNotFoundError as exc:
            raise ReceiptError(f"{name}: dangling — {exc}") from exc
        except AmbiguousClaimError as exc:
            raise ReceiptError(f"{name}: ambiguous — {exc}") from exc
        except ProvenanceError as exc:
            raise ReceiptError(f"{name}: malformed claim ref — {exc}") from exc
        stamp = "cites"
    else:  # cmd | url | human
        stamp = "attested"

    stamped = dict(receipt)
    stamped["checked_at_write"] = stamp
    if unverified_reason is not None:
        stamped["unverified_reason"] = unverified_reason
    return stamped


def verify_receipts_at_write(receipts: list[dict], chronicle_root: Path) -> list[dict]:
    """
    Verify a verified_by list for record_insight. All-or-nothing: the
    first malformed/dangling/ambiguous receipt rejects the whole call
    (ReceiptError names it, 1-based position included). Returns stamped
    copies; inputs are never mutated.
    """
    if not isinstance(receipts, list):
        raise ReceiptError("verified_by must be a list of receipt dicts")
    return [
        verify_receipt_at_write(receipt, chronicle_root, position=i)
        for i, receipt in enumerate(receipts, start=1)
    ]


def receipt_stamp_counts(receipts: list[dict]) -> dict[str, int]:
    """
    Count checked_at_write stamps for surface rendering ("N verified,
    M attested"). Keys: verified, mismatch, cites, attested. Surfaces
    count ONLY "verified" as verification — mismatch/cites never
    upgrade.
    """
    counts = {"verified": 0, "mismatch": 0, "cites": 0, "attested": 0}
    for receipt in receipts or []:
        stamp = receipt.get("checked_at_write") if isinstance(receipt, dict) else None
        if stamp in counts:
            counts[stamp] += 1
    return counts


# ── Supersedes params (record_insight write semantics) ───────────────────────


def validate_carry_forward(supersedes: list[str] | None, carry_forward_summary: str | None) -> None:
    """
    Enforce the supersedes/carry_forward pairing for record_insight:
    carry_forward_summary is REQUIRED when supersedes is present, and
    <= 500 chars whenever supplied.

    Raises:
        ProvenanceError: on a missing or oversized summary.
    """
    if supersedes and (
        not isinstance(carry_forward_summary, str) or not carry_forward_summary.strip()
    ):
        raise ProvenanceError(
            "carry_forward_summary is required when supersedes is present"
            " — say what the predecessor still teaches"
        )
    if carry_forward_summary is not None and len(carry_forward_summary) > CARRY_FORWARD_MAX_CHARS:
        raise ProvenanceError(
            f"carry_forward_summary exceeds {CARRY_FORWARD_MAX_CHARS} chars"
            f" ({len(carry_forward_summary)})"
        )


def resolve_supersedes(supersedes: list[str], chronicle_root: Path) -> list[tuple[str, dict]]:
    """
    Resolve each supersedes ref (full id or unique prefix) to
    (full_64hex_id, entry). Errors propagate per ref — unknown or
    ambiguous ids reject the whole call. Duplicate refs collapse to one
    predecessor. Order preserved.
    """
    resolved: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for ref in supersedes or []:
        entry, _file, _location = resolve_claim(ref, chronicle_root)
        full_id = derive_claim_id(entry)
        if full_id not in seen:
            seen.add(full_id)
            resolved.append((full_id, entry))
    return resolved


# ── Supersession ledger ──────────────────────────────────────────────────────


def load_supersessions(ledger_path: Path) -> list[dict]:
    """
    Read all ledger records in file order. Missing ledger -> [] (the
    file is created lazily on first append; reading never creates it).
    Corrupt lines are skipped, matching the chronicle read convention.
    """
    return list(_iter_jsonl(Path(ledger_path)))


def build_supersession_record(
    *,
    action: str,
    superseded_id: str,
    successor_id: str | None = None,
    carry_forward_summary: str | None = None,
    reason: str = "",
    by: str = "",
    vantage: str | None = None,
    predecessor: dict | None = None,
    timestamp: str | None = None,
) -> dict:
    """
    Build one ledger record in the exact spec schema.

    `by` is the recording INSTANCE, not a session_id (attribution-mush
    fix). `predecessor` (the resolved entry) fills the locator hints —
    domain, timestamp, first-120-chars preview — that keep the ledger
    human-legible even if the predecessor file is later lost or
    quarantined.

    Raises:
        SupersessionError: invalid action, non-64-hex ids, missing
            successor/carry_forward on "supersede", or a successor on
            "revoke"/"retire".
    """
    if action not in SUPERSESSION_ACTIONS:
        raise SupersessionError(
            f"invalid supersession action {action!r} (valid: {SUPERSESSION_ACTIONS})"
        )
    if not isinstance(superseded_id, str) or not _FULL_ID_RE.match(superseded_id):
        raise SupersessionError(
            f"superseded_id must be a full 64-hex claim id, got {superseded_id!r}"
        )
    if action == "supersede":
        if not isinstance(successor_id, str) or not _FULL_ID_RE.match(successor_id):
            raise SupersessionError(
                f"supersede requires a full 64-hex successor_id, got {successor_id!r}"
            )
        validate_carry_forward([superseded_id], carry_forward_summary)
    else:
        if successor_id is not None:
            raise SupersessionError(f"{action} records must carry successor_id null")
        validate_carry_forward(None, carry_forward_summary)

    predecessor = predecessor or {}
    return {
        "action": action,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "superseded_id": superseded_id,
        "successor_id": successor_id,
        "carry_forward_summary": carry_forward_summary,
        "reason": reason,
        "by": by,
        "vantage": vantage,
        "predecessor_domain": _preimage_field(predecessor, "domain"),
        "predecessor_timestamp": _preimage_field(predecessor, "timestamp"),
        "predecessor_preview": _preimage_field(predecessor, "content")[:_PREVIEW_CHARS],
    }


def append_supersession(ledger_path: Path, record: dict) -> dict:
    """
    Append one record to the ledger (parent directory created lazily —
    first write, never import). Validates the action/id core so a
    hand-built record can't poison the fold. Returns the record.
    """
    if record.get("action") not in SUPERSESSION_ACTIONS:
        raise SupersessionError(f"invalid supersession action {record.get('action')!r}")
    sid = record.get("superseded_id")
    if not isinstance(sid, str) or not _FULL_ID_RE.match(sid):
        raise SupersessionError(f"superseded_id must be a full 64-hex claim id, got {sid!r}")
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with chronicle_write_lock(ledger_path.parent), open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def fold_supersessions(records: list[dict]) -> dict[str, dict]:
    """
    Fold the append-only ledger into effective state: a map of
    predecessor claim_id -> its latest effective record (latest action
    per predecessor wins; "revoke" nullifies, restoring surfacing).

    The full record is kept (not a bare successor id) so annotation and
    lineage get carry_forward_summary and locator hints for free; the
    successor map is `record["successor_id"]` (null for "retire").
    """
    fold: dict[str, dict] = {}
    for record in records:
        action = record.get("action")
        sid = record.get("superseded_id")
        if action not in SUPERSESSION_ACTIONS or not isinstance(sid, str):
            continue
        if action == "revoke":
            fold.pop(sid, None)
        else:
            fold[sid] = record
    return fold


def check_supersession_guards(superseded_id: str, successor_id: str, fold: dict[str, dict]) -> None:
    """
    Guards for a new supersede link (both ids already resolved to full
    64-hex):

      - self-supersession refused;
      - double-supersession refused — one successor per predecessor; the
        only coherent amendment is to supersede the successor (a retired
        predecessor MAY still gain a successor: latest action wins);
      - cycles refused — walking the successor chain from the proposed
        successor must never reach the predecessor.

    Raises:
        SupersessionError: naming the colliding claim ids.
    """
    if superseded_id == successor_id:
        raise SupersessionError(
            f"refusing self-supersession: {display_id(superseded_id)} cannot supersede itself"
        )
    existing = fold.get(superseded_id)
    if existing and existing.get("action") == "supersede":
        raise SupersessionError(
            f"claim {display_id(superseded_id)} is already superseded by"
            f" {display_id(existing.get('successor_id') or '')};"
            " supersede the successor to amend"
        )
    visited: set[str] = set()
    cursor: str | None = successor_id
    while cursor:
        if cursor == superseded_id:
            raise SupersessionError(
                f"refusing supersession cycle: {display_id(successor_id)} already"
                f" descends from {display_id(superseded_id)}"
            )
        if cursor in visited:
            break
        visited.add(cursor)
        record = fold.get(cursor)
        cursor = (
            record.get("successor_id") if record and record.get("action") == "supersede" else None
        )


# ── Read-path helpers (partition / annotate) ─────────────────────────────────


def partition_superseded(
    entries: list[dict], fold: dict[str, dict]
) -> tuple[list[dict], list[dict]]:
    """
    Split entries into (live, superseded) by derived claim id. Folded
    "retire" records count as superseded — the read path sees
    retirements (spec's retire_hypothesis reconciliation). Entries are
    returned as-is, order preserved within each partition.
    """
    live: list[dict] = []
    superseded: list[dict] = []
    for entry in entries:
        (superseded if derive_claim_id(entry) in fold else live).append(entry)
    return live, superseded


def annotate_superseded(entries: list[dict], fold: dict[str, dict]) -> list[dict]:
    """
    Annotate-not-drop for the raw query tool: every entry is returned
    (as a copy), superseded ones gain `_superseded_by` (full 64-hex;
    null for retirements) and `_carry_forward_summary` when the ledger
    carries one. Underscore = derived at read, never persisted.
    """
    annotated: list[dict] = []
    for entry in entries:
        record = fold.get(derive_claim_id(entry))
        if record is None:
            annotated.append(entry)
            continue
        copy = dict(entry)
        copy["_superseded_by"] = record.get("successor_id")
        summary = record.get("carry_forward_summary")
        if summary:
            copy["_carry_forward_summary"] = summary
        annotated.append(copy)
    return annotated


def annotate_claim_ids(entries: list[dict]) -> list[dict]:
    """
    with_ids=True support: copies with `claim_id` (full 64-hex) derived
    onto each entry. Never persisted.

    Type- and id-preserving for entries that ALREADY carry a `claim_id`
    (e.g. the protected-source fail-closed sentinel, whose body is withheld
    so its id must NOT be re-derived from the replacement content). Such
    entries are returned UNCHANGED — same object, same class, the true id
    intact. Ordinary entries get a plain-dict copy with the derived id, as
    before.
    """
    annotated = []
    for entry in entries:
        if entry.get("claim_id"):
            # Already carries its true id (and may be a typed dict subclass
            # whose content is withheld) — never copy-flatten or re-derive.
            annotated.append(entry)
            continue
        copy = dict(entry)
        copy["claim_id"] = derive_claim_id(entry)
        annotated.append(copy)
    return annotated


# ── Lineage ──────────────────────────────────────────────────────────────────


def _lineage_row(claim_id: str, role: str, fold: dict[str, dict], chronicle_root: Path) -> dict:
    """
    One lineage record: {claim_id, role, timestamp, domain,
    content_preview, carry_forward_summary?}. Resolves the entry for
    its fields; a dangling predecessor falls back to the ledger's
    locator hints (that is what they are for); a dangling successor
    gets nulls — inspect_claim surfaces the dangle, it doesn't hide it.
    """
    timestamp: str | None = None
    domain: str | None = None
    preview: str | None = None
    try:
        entry, _file, _location = resolve_claim(claim_id, chronicle_root)
        timestamp = _preimage_field(entry, "timestamp")
        domain = _preimage_field(entry, "domain")
        # Protected-source gate (spec §5.4): a lineage row is a 120-char
        # PREVIEW surface — it can never carry the full stakes, so a slice of
        # coupled content would re-decouple. A protected entry withholds its
        # preview to the placeholder (locator fields still surface, so the
        # lineage shape is intact and the row says a protected record is
        # there). Lazy import avoids the protected->provenance cycle.
        from sovereign_stack import protected as _protected

        if _protected.is_protected(entry, _protected.load_protected_fold(chronicle_root)):
            preview = _protected.PROTECTED_PREVIEW_NOTICE
        else:
            preview = _preimage_field(entry, "content")[:_PREVIEW_CHARS]
    except ProvenanceError:
        record = fold.get(claim_id)
        if record:
            timestamp = record.get("predecessor_timestamp") or None
            domain = record.get("predecessor_domain") or None
            preview = record.get("predecessor_preview") or None
    row = {
        "claim_id": claim_id,
        "role": role,
        "timestamp": timestamp,
        "domain": domain,
        "content_preview": preview,
    }
    record = fold.get(claim_id)
    if record and record.get("carry_forward_summary"):
        row["carry_forward_summary"] = record["carry_forward_summary"]
    return row


def walk_lineage(claim_id: str, fold: dict[str, dict], chronicle_root: Path) -> list[dict]:
    """
    Walk the supersession graph both directions from a claim, cycle-safe.

    Backward: every transitive predecessor (N-to-1 consolidation means
    one successor can have many). Forward: the successor chain. Returns
    lineage rows ordered predecessors (by timestamp) -> self ->
    successors (chain order). `claim_id` must be the full 64-hex id
    (resolve prefixes first).
    """
    predecessors_of: dict[str, list[str]] = {}
    for pid, record in fold.items():
        successor = record.get("successor_id")
        if record.get("action") == "supersede" and successor:
            predecessors_of.setdefault(successor, []).append(pid)

    visited: set[str] = {claim_id}
    backward: list[str] = []
    queue = list(predecessors_of.get(claim_id, []))
    while queue:
        pid = queue.pop(0)
        if pid in visited:
            continue
        visited.add(pid)
        backward.append(pid)
        queue.extend(predecessors_of.get(pid, []))

    forward: list[str] = []
    cursor = claim_id
    while True:
        record = fold.get(cursor)
        successor = (
            record.get("successor_id") if record and record.get("action") == "supersede" else None
        )
        if not successor or successor in visited:
            break
        visited.add(successor)
        forward.append(successor)
        cursor = successor

    rows = [_lineage_row(pid, "predecessor", fold, chronicle_root) for pid in backward]
    rows.sort(key=lambda r: r.get("timestamp") or "")
    rows.append(_lineage_row(claim_id, "self", fold, chronicle_root))
    rows.extend(_lineage_row(sid, "successor", fold, chronicle_root) for sid in forward)
    return rows


# ── Similarity & legacy markers (season_review inputs) ───────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def token_overlap(a: str, b: str) -> float:
    """
    Jaccard similarity over lowercase alphanumeric token sets. Both
    empty -> 0.0 (no evidence of overlap is not overlap). Used by
    season_review's supersession / thread-family candidate detection.
    """
    tokens_a = set(_TOKEN_RE.findall((a or "").lower()))
    tokens_b = set(_TOKEN_RE.findall((b or "").lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def has_legacy_marker(entry: dict) -> bool:
    """
    True when the pre-ledger supersession markers (CORRECTED |
    DEFINITIVE | supersedes) appear in the entry's domain OR content —
    the regex scans both fields, a hit in either marks the entry.
    """
    return bool(
        LEGACY_MARKER_RE.search(_preimage_field(entry, "domain"))
        or LEGACY_MARKER_RE.search(_preimage_field(entry, "content"))
    )
