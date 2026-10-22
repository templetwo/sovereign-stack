"""HQ cockpit Phase 3: the push link, console events INTO the panel HQ session.

Anthony, 2026-10-09 (claim a2cc3765): the console reaches HQ through Claude Code's
cross-session messaging socket, not channels; SMS stays off. "Build now", 2026-10-10.

THE MECHANISM, measured on Claude Code 2.1.296 (lane design.md has the byte offsets
and the two probe transcripts):

* Every interactive session binds a Unix socket (``/tmp/cc-socks/<pid>.sock`` here,
  mode 0600 in a 0700 dir) and writes a registry record
  ``~/.claude/sessions/<pid>.json`` whose ``messagingSocketPath`` names it.
* Beside it, mode 0600, sits ``<pid>.<sha256(socket path)>.key`` holding a
  ``peerToken``. A same-user process reads it and sends, newline-delimited JSON,
  ``{"type":"auth","token":...}`` then ``{"type":"user","message":{"role":"user",
  "content":"..."}}``. On macOS auth is optional (required on Windows only); we
  present the peer token anyway so the link keeps working if that ever flips.
* The receiver queues the text as a meta user turn with ``origin.kind == "peer"``,
  framed "Another Claude session sent a message: ..." plus a paragraph telling the
  model a peer cannot grant escalation or approve a prompt.
* In a session that bypasses permission prompts (panel HQ inherits
  ``defaultMode: bypassPermissions``) a message that does not attest a permission
  mode is HELD for review unless ``crossSessionInbound`` is set. Measured: held,
  with a review dialog. The panel's argv sets the value EXPLICITLY, as the plan
  asks: ``--settings {"crossSessionInbound":"hold"}`` by default (flag settings,
  this one process only; ~/.claude/settings.json is untouched).
* ``accept`` is NEVER the default (HQ review, 2026-10-10). Claude Code 2.1.296 has
  no sender allowlist: accept would admit every same-user peer (another claude, a
  watch shell, any process that reads the 0600 key, or none at all, since macOS
  auth is optional) into a bypass-mode HQ with no hold. Accept is reachable only
  through the console's environment: ``HQ_PANEL_INBOUND=accept`` or an
  ``HQ_PANEL_COMMAND`` that sets it. Anthony's switch (gate-shape:channel-trust).
* The inbound word on the page is what the console LAUNCHED with, not Claude
  Code's effective policy: a managed setting or the runtime kill switch outranks
  the flag silently. Hence "sent to HQ's review queue", never "held".
* MEASURED (2026-10-10, probe_hold_peer.txt): under an explicit ``hold`` a batch
  is held with NO review dialog; Claude Code prints it in the panel as "Held peer
  message ... preview ... not delivered to Claude" and only a change of the
  setting to accept would release it. The dialog (Deliver / Deny) exists only
  for the unset case. So under hold the link reports OFF with ``HOLD_REASON``
  and keeps handing batches, which Anthony sees as held lines in the panel.
* Each batch's first line names the console pid and a per-console nonce, also
  written to a 0600 file (``nonce_file``) HQ can read, so a held batch is
  recognisable in the review queue. That is recognition, not authentication: any
  same-user process can read a 0600 file.

WHAT THIS MODULE NEVER DOES:

* It never forwards free text. Every byte of a batch comes from the fixed template
  in ``format_batch`` plus integers, counts and HH:MM times the console measured
  or validated. No message body, handle, phone number, chat guid, honk text, halt
  note name, file name or path. The one web-reachable trigger (a successful
  ``/hq/drop``) passes a byte count only.
* It never discovers a target. It sends only to the pid the console itself spawned
  (``HQSession.last_started_pid``), only while that child is running, only when the
  argv the console spawned carries the inbound setting, and only after checking
  the registry record, the socket and the key file belong to this user.
* It never claims delivery. The socket answers nothing to a sender with no reply
  address, so a successful write reads "handed to HQ's inbox at HH:MM".
* It never drops a batch silently. A failed or impossible send keeps the pending
  events (they coalesce, so the buffer is bounded) and the panel shows the link
  off with the reason.
* It never sends ``priority: "now"`` (that skips HQ's queue); it sends "next".
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

# ── configuration ──────────────────────────────────────────────────────────

#: The approved plan (2026-10-09): "at most one message every 15 minutes".
DEFAULT_MIN_INTERVAL_S = 900
#: The env override may shorten the batch interval for a live test, never below this.
MIN_INTERVAL_FLOOR_S = 60
#: How often the poller reads its sources.
DEFAULT_POLL_S = 30
#: A failed send is retried no more often than this.
RETRY_S = 60
CONNECT_TIMEOUT_S = 2.0
#: macOS AF_UNIX sun_path limit (Claude Code itself uses 103).
SOCK_PATH_MAX = 103
#: The value the console attests as its sender label (origin.from on the receiver).
SENDER = "hq-console"
#: The only registry peerProtocol this sender speaks (measured on 2.1.296).
PEER_PROTOCOL = 1
#: A poller that has not ticked for this many polls reads off.
STALE_POLLS = 3

INBOUND_ENV = "HQ_PANEL_INBOUND"
INBOUND_VALUES = ("accept", "hold", "refuse")
DEFAULT_INBOUND = "hold"  # never "accept" by default: see the module docstring

#: Bounded tail of the spool's inbound.jsonl (records are a few hundred bytes).
SPOOL_TAIL_BYTES = 256 * 1024

_RE_HEX_TOKEN = re.compile(r"^[0-9a-f]{16,256}$")
# [0-9], never \d: \d matches every Unicode digit (HQ review C4).
_RE_EASTERN_HM = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T]([0-9]{2}):([0-9]{2})")


def inbound_value(env: dict | None = None) -> str:
    """The crossSessionInbound value the panel is launched with. Anything other
    than the three Claude Code values falls back to the default, never to a typo
    Claude Code would read as invalid (which it treats as hold)."""
    raw = ((env if env is not None else os.environ).get(INBOUND_ENV) or "").strip().lower()
    return raw if raw in INBOUND_VALUES else DEFAULT_INBOUND


def inbound_settings_arg(env: dict | None = None) -> list[str]:
    """The two argv words that set crossSessionInbound for the panel child alone
    (Claude Code reads --settings as flagSettings, above userSettings and below
    managed policy). Compact JSON so the argv has no spaces."""
    value = inbound_value(env)
    return ["--settings", json.dumps({"crossSessionInbound": value}, separators=(",", ":"))]


def command_inbound(argv: list[str] | None) -> str | None:
    """The crossSessionInbound value an argv sets via --settings JSON, or None
    when it sets none (then the push link is off: unset means HELD in a
    bypass-mode session, measured)."""
    if not argv:
        return None
    for i, word in enumerate(argv[:-1]):
        if word != "--settings":
            continue
        try:
            obj = json.loads(argv[i + 1])
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and obj.get("crossSessionInbound") in INBOUND_VALUES:
            return obj["crossSessionInbound"]
    return None


def _min_interval() -> int:
    try:
        v = int(os.environ.get("HQ_PUSH_MIN_INTERVAL_S", DEFAULT_MIN_INTERVAL_S))
    except ValueError:
        return DEFAULT_MIN_INTERVAL_S
    return max(MIN_INTERVAL_FLOOR_S, v)


def _sessions_dir() -> Path:
    return Path(os.environ.get("HQ_PUSH_SESSIONS_DIR") or Path.home() / ".claude" / "sessions")


def _sovereign_root() -> Path:
    return Path(os.environ.get("SOVEREIGN_ROOT") or Path.home() / ".sovereign")


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ── target resolution (fail closed at every step) ──────────────────────────


class TargetError(Exception):
    """A short, fixed-vocabulary reason the target cannot be used. Never a path."""


def _private_owned(st: os.stat_result) -> bool:
    return st.st_uid == os.getuid() and (st.st_mode & 0o077) == 0


def ps_proc_start(pid: int) -> str | None:
    """`ps -o lstart=` for `pid` in UTC with the C locale, whitespace-normalised:
    the same text Claude Code writes as the registry's procStart (measured:
    80949 -> "Sun Oct 11 00:14:44 2026"). None when ps fails or the pid is gone."""
    try:
        out = subprocess.run(
            ["/bin/ps", "-o", "lstart=", "-p", str(int(pid))],
            capture_output=True,
            text=True,
            timeout=5,
            env={"TZ": "UTC", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        ).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    norm = " ".join(out.split())
    return norm or None


def resolve_target(
    pid: int,
    spawned_at: float | None,
    sessions_dir: Path | None = None,
    proc_start=None,
) -> dict:
    """The socket path and peer token for the panel child `pid`. Raises
    TargetError with a fixed reason on any doubt. `spawned_at` is the console's
    own clock at spawn: a registry record older than the spawn belongs to an
    earlier process that held the same pid, never to this child."""
    d = sessions_dir or _sessions_dir()
    rec_path = d / f"{int(pid)}.json"
    try:
        st = os.lstat(rec_path)
    except OSError:
        raise TargetError("HQ has not registered its inbox yet") from None
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size > 65536:
        raise TargetError("session record is not a regular file of this user")
    try:
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise TargetError("session record unreadable") from None
    if not isinstance(rec, dict) or rec.get("pid") != pid:
        raise TargetError("session record names another process")
    if rec.get("peerProtocol") != PEER_PROTOCOL:
        raise TargetError("HQ's inbox speaks an unknown peer protocol")
    # Bind to the process, not just the pid (HQ review C7): the record's
    # procStart must equal what ps says for this pid right now.
    rec_start = rec.get("procStart")
    live_start = (proc_start or ps_proc_start)(pid)
    if (
        not isinstance(rec_start, str)
        or live_start is None
        or " ".join(rec_start.split()) != live_start
    ):
        raise TargetError("session record does not match the running process")
    started_ms = rec.get("startedAt")
    if spawned_at is not None and (not _is_int(started_ms) or started_ms / 1000.0 < spawned_at - 5):
        raise TargetError("session record predates the panel child")
    sock = rec.get("messagingSocketPath")
    if not isinstance(sock, str) or not sock.startswith("/") or ".." in sock.split("/"):
        raise TargetError("HQ has no messaging socket (cross-session messaging off there)")
    if len(sock.encode()) > SOCK_PATH_MAX:
        raise TargetError("socket path too long")
    try:
        sst = os.lstat(sock)
        dst = os.lstat(os.path.dirname(sock))
    except OSError:
        raise TargetError("HQ's socket is gone") from None
    if not stat.S_ISSOCK(sst.st_mode) or not _private_owned(sst):
        raise TargetError("socket is not a private socket of this user")
    if not stat.S_ISDIR(dst.st_mode) or not _private_owned(dst):
        raise TargetError("socket directory is not private to this user")
    # Claude Code names the key file after the sha256 of the socket path string
    # it bound (measured: /tmp/cc-socks/18109.sock -> e14f56af31233cbc...).
    key_path = d / f"{int(pid)}.{hashlib.sha256(sock.encode()).hexdigest()}.key"
    try:
        kst = os.lstat(key_path)
    except OSError:
        raise TargetError("HQ's inbox key is missing") from None
    if not stat.S_ISREG(kst.st_mode) or not _private_owned(kst) or kst.st_size > 4096:
        raise TargetError("inbox key is not a private file of this user")
    try:
        token = json.loads(key_path.read_text(encoding="utf-8")).get("peerToken")
    except (OSError, ValueError, AttributeError):
        raise TargetError("inbox key unreadable") from None
    if not isinstance(token, str) or not _RE_HEX_TOKEN.match(token):
        raise TargetError("inbox key malformed")
    return {"socket": sock, "token": token}


def send_frame(
    sock_path: str, token: str, content: str, timeout: float = CONNECT_TIMEOUT_S
) -> None:
    """Write one auth line and one user line, then close. Raises OSError on any
    socket failure. The token never leaves this function except onto the socket."""
    frame = {
        "type": "user",
        "from": SENDER,
        "uuid": str(uuid.uuid4()),
        "priority": "next",
        "message": {"role": "user", "content": content},
    }
    payload = (
        json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps(frame) + "\n"
    ).encode("utf-8")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(sock_path)
        s.sendall(payload)
        with contextlib.suppress(OSError):
            s.shutdown(socket.SHUT_WR)
    finally:
        s.close()


# ── sources (metadata only) ────────────────────────────────────────────────


def read_inbound_rows(path: Path) -> list[tuple[int, str | None]] | None:
    """(rowid, "HH:MM" Eastern or None) for every is_from_me == 0 record in the
    spool's bounded tail. Reads only `rowid`, `is_from_me` and `date_iso_eastern`;
    the clock is sliced to digits by regex, never passed through. None when the
    file cannot be read (unknown, not "no texts")."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - SPOOL_TAIL_BYTES))
            tail = f.read()
    except OSError:
        return None
    rows: list[tuple[int, str | None]] = []
    for line in tail.splitlines():
        try:
            rec = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(rec, dict) or rec.get("is_from_me") != 0:
            continue
        rowid = rec.get("rowid")
        if not _is_int(rowid) or rowid <= 0:
            continue
        hm = None
        stamp = rec.get("date_iso_eastern")
        if isinstance(stamp, str):
            m = _RE_EASTERN_HM.match(stamp)
            if m:
                hm = f"{m.group(1)}:{m.group(2)}"
        rows.append((rowid, hm))
    return rows


def read_counters(root: Path) -> dict:
    """The two console counters the push link watches: all-time unacked nape honks
    and halt notes on disk. Integers or None (unreadable), never text.

    The honk count is ``dashboard.count_unacked_honks``, which since 2026-10-21
    counts only honks that want an ack: an informational honk (level satisfied)
    no longer raises it, so it no longer hands HQ a batch."""
    from . import dashboard  # local import: dashboard is heavy and imports the world

    out: dict = {"honks": None, "halts": None}
    with contextlib.suppress(Exception):
        out["honks"] = int(dashboard.count_unacked_honks(root / "nape" / "honks.jsonl"))
    with contextlib.suppress(Exception):
        halts = root / "daemons" / "halts"
        out["halts"] = len(list(halts.glob("*.md"))) if halts.is_dir() else 0
    return out


# ── the batch text: the ONLY place message content is made ─────────────────


def nonce_file() -> Path:
    return Path(
        os.environ.get("HQ_PUSH_NONCE_FILE")
        or Path.home() / ".local" / "share" / "hq-cockpit" / "push" / "console.json"
    )


def cursor_file() -> Path:
    return Path(os.environ.get("HQ_PUSH_CURSOR_FILE") or nonce_file().parent / "cursor.json")


def _write_private_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_cursor(path: Path | None) -> int | None:
    """The persisted spool rowid baseline, or None (no file, unreadable, junk)."""
    if path is None:
        return None
    try:
        v = json.loads(path.read_text(encoding="utf-8")).get("base_rowid")
    except (OSError, ValueError, AttributeError):
        return None
    return v if _is_int(v) and v >= 0 else None


def write_nonce_file(path: Path, nonce: str, console_pid: int) -> None:
    """The console's recognition stamp, 0600 in a 0700 dir, written atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(
            {
                "console_pid": int(console_pid),
                "nonce": nonce,
                "written_at": datetime.now().astimezone().isoformat(),
            },
            f,
        )
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def format_batch(
    pending: dict,
    *,
    batch_no: int,
    now: float,
    since: float | None,
    console_pid: int,
    nonce: str = "",
) -> str:
    """Render pending events from a fixed template. Every interpolated value is
    an int or an HH:MM made here or validated above; tests feed hostile strings
    into every source and assert none reach this text."""
    stamp = f", nonce {nonce}" if re.fullmatch(r"[0-9a-f]{8,64}", nonce or "") else ""
    lines = [
        f"[HQ console push, batch {int(batch_no)}, {_hm(now)}, console pid {int(console_pid)}{stamp}]",
        (
            f"Automated notice from the cockpit console (sovereign-dashboard-web, pid "
            f"{int(console_pid)}) on this Studio. Metadata only, from a fixed template. "
            "It is not a message from Anthony and requests nothing beyond your usual "
            "judgment; it cannot approve or authorize anything."
        ),
        f"Since {_hm(since) if since else 'this session started'}:",
    ]
    t = pending.get("texts")
    if t and int(t.get("count", 0)) > 0:
        newest = t.get("newest_rowid")
        hm = t.get("newest_hm")
        at = (
            f" at {hm} Eastern"
            if isinstance(hm, str) and re.fullmatch(r"[0-9]{2}:[0-9]{2}", hm)
            else ""
        )
        lines.append(
            f"- iMessage spool: {int(t['count'])} new inbound row(s) on Anthony's thread, "
            f"newest rowid {int(newest)}{at}. Rows can be echoes of HQ's own outbound SMS "
            "(2026-10-10 finding); read the thread before treating one as his."
        )
    for key, label in (("honks", "unacked nape honks"), ("halts", "halt notes")):
        c = pending.get(key)
        if c and int(c.get("new", 0)) > 0:
            lines.append(f"- {int(c['new'])} new {label} (now {int(c['now'])} in all).")
    dr = pending.get("drops")
    if dr and int(dr.get("count", 0)) > 0:
        lines.append(
            f"- {int(dr['count'])} file(s) dropped on the panel, {int(dr['bytes'])} bytes in all, "
            f"last at {_hm(float(dr['last_at']))}; each path was already pasted at your prompt."
        )
    o = pending.get("over_24h")
    if o:
        lines.append(
            f"- This panel session passed 24 hours open at {_hm(float(o['at']))}: the read-back "
            "rule (claim a3859590) says close with the three lines and reopen."
        )
    lines.append("If you act on any of this, acknowledge on the Stack as usual.")
    return "\n".join(lines)


#: Under an explicit hold Claude Code 2.1.296 opens NO Deliver/Deny dialog (it
#: does so only for the mode-mismatch and no-mode-asserted causes, measured and
#: read at ~217107417): the batch prints as a held line in the panel and never
#: reaches HQ's turn unless the setting becomes accept. So hold reads OFF.
HOLD_REASON = (
    "on hold as launched: each batch prints in the panel as a held line and does "
    "not reach HQ (inbound hold)"
)


def _on_reason(inbound: str | None) -> str:
    return (
        HOLD_REASON
        if inbound == "hold"
        else "push link on (inbound accept, set in the console's environment)"
    )


def _link_on(inbound: str | None) -> bool:
    """True only when a handed batch can reach HQ's turn: inbound accept."""
    return inbound == "accept"


def _has_events(p: dict) -> bool:
    return bool(
        (p.get("texts") or {}).get("count")
        or (p.get("honks") or {}).get("new")
        or (p.get("halts") or {}).get("new")
        or (p.get("drops") or {}).get("count")
        or p.get("over_24h")
    )


# ── the link ───────────────────────────────────────────────────────────────


class PushLink:
    """One poller per console. Thread-safe. `session` is the HQSession (it must
    expose status(), last_started_pid, spawned_at and last_command); `send`,
    `resolve` and `clock` are injectable for tests.

    Baselines live as long as the console: the first observation after the
    console starts sets them and reports nothing, so a restart never announces
    the whole backlog. Sources are observed on every tick, panel running or not,
    so a text that lands between two panel sessions is handed to the next one."""

    def __init__(
        self,
        session,
        *,
        root: Path | None = None,
        send=None,
        resolve=None,
        clock=None,
        cursor_path: Path | None = None,
        poll_s: float = DEFAULT_POLL_S,
    ):
        self._session = session
        self._cursor_path = cursor_path
        self.poll_s = float(poll_s)
        self.nonce_file_ok: bool | None = None
        self._root = root
        self._send = send or send_frame
        self._resolve = resolve or resolve_target
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self.nonce = secrets.token_hex(8)
        self._observed = False
        self._base_rowid: int | None = None
        self._last: dict = {}
        self._pending: dict = {}
        self._pending_since: float | None = None
        # Per panel session:
        self._pid: int | None = None
        self._last_sent_at: float | None = None
        self._last_attempt_at: float | None = None
        self._batches = 0
        self._over_24h_sent = False
        # Shown on /hq/status:
        self._listening = False
        self._reason = "push poller not started"
        self._send_failing = False  # a send failed and none has succeeded since
        self._last_tick_at: float | None = None
        self._sources: dict = {"texts": "unknown", "honks": "unknown", "halts": "unknown"}
        self._last_error: str | None = None
        self._last_handed_at: float | None = None
        self._handed_total = 0

    def root(self) -> Path:
        return self._root or _sovereign_root()

    def _mark_pending(self) -> None:
        if self._pending_since is None:
            self._pending_since = self._clock()

    # ── events from the console's own handlers ──
    def note_drop(self, nbytes: int) -> None:
        """A successful /hq/drop. Byte count only: the name stays on the server.
        Counted only while a panel session is the drop's target."""
        if not _is_int(nbytes) or nbytes < 0:
            return
        with self._lock:
            if self._pid is None:
                return
            d = self._pending.setdefault("drops", {"count": 0, "bytes": 0, "last_at": 0.0})
            d["count"] += 1
            d["bytes"] += nbytes
            d["last_at"] = self._clock()
            self._mark_pending()

    # ── the poll ──
    def _new_session(self, pid: int) -> None:
        self._pid = pid
        self._last_sent_at = getattr(self._session, "spawned_at", None) or self._clock()
        self._last_attempt_at = None
        self._batches = 0
        self._over_24h_sent = False
        self._last_error = None
        self._send_failing = False
        # Drops and the 24 h notice belonged to the session that ended.
        self._pending.pop("drops", None)
        self._pending.pop("over_24h", None)
        if not _has_events(self._pending):
            self._pending_since = None

    def _read_sources(self) -> dict:
        """The file reads, OUTSIDE the lock (a snapshot poll must not wait on them)."""
        root = self.root()
        spool = root / "imessage" / "inbound.jsonl"
        rows = read_inbound_rows(spool)
        return {"rows": rows, "spool_exists": spool.exists(), "counters": read_counters(root)}

    def _set_base(self, rowid: int) -> None:
        self._base_rowid = rowid
        if self._cursor_path is not None:
            with contextlib.suppress(OSError):
                _write_private_json(self._cursor_path, {"base_rowid": int(rowid)})

    def _fold(self, src: dict) -> None:
        """Fold one read of the sources into pending (under the lock)."""
        rows, counters = src["rows"], src["counters"]
        if rows is not None:
            self._sources["texts"] = "ok"
        else:
            self._sources["texts"] = "unreadable" if src["spool_exists"] else "absent"
        if rows is None and self._base_rowid is None and not src["spool_exists"]:
            # No spool file yet means no texts yet: baseline at zero, so the
            # first rows written later all count. An unreadable file that does
            # exist sets no baseline (unknown, not zero).
            self._set_base(0)
        first = not self._observed
        self._observed = True
        if rows is not None:
            newest = max((r for r, _ in rows), default=None)
            if self._base_rowid is None:
                # A cursor persisted by the previous console process carries the
                # texts that landed during a restart (HQ review A5); never past
                # the newest row on disk.
                saved = read_cursor(self._cursor_path)
                top = newest if newest is not None else 0
                self._set_base(min(saved, top) if saved is not None else top)
            if newest is not None:
                fresh = [(r, hm) for r, hm in rows if r > self._base_rowid]
                if fresh:
                    r, hm = max(fresh)
                    t = self._pending.setdefault("texts", {"count": 0})
                    t["count"] += len(fresh)
                    t["newest_rowid"] = r
                    t["newest_hm"] = hm
                    self._set_base(r)
                    self._mark_pending()
        for key in ("honks", "halts"):
            now_v = counters.get(key)
            if now_v is None:
                self._sources[key] = "unreadable"
                continue
            self._sources[key] = "ok"
            prev = self._last.get(key)
            if not first and prev is not None and now_v > prev:
                c = self._pending.setdefault(key, {"new": 0, "now": now_v})
                c["new"] += now_v - prev
                c["now"] = now_v
                self._mark_pending()
            elif key in self._pending:
                self._pending[key]["now"] = now_v
            # Level-triggered: a drop (an ack, a removed note) lowers the baseline
            # silently, and an ack plus a new honk inside one poll nets to zero.
            self._last[key] = now_v

    def tick(self) -> None:
        """One poll: observe, then hand over a batch when one is due. Never raises
        an Exception; a thread killed some other way shows as "push poller
        stopped" once last_tick_at is STALE_POLLS polls old."""
        try:
            self._tick()
        except Exception as e:  # a poller that dies silently is the failure to avoid
            with self._lock:
                self._listening = False
                self._reason = "push poller error"
                self._last_error = type(e).__name__
        finally:
            with self._lock:
                self._last_tick_at = self._clock()

    def _tick(self) -> None:
        session = self._session
        st = session.status()
        running = bool(st.get("running"))
        pid = st.get("pid") if running else None
        src = self._read_sources()
        with self._lock:
            self._fold(src)
            if not running or not _is_int(pid):
                self._listening = False
                self._reason = "no panel session"
                if self._pid is not None:
                    self._pid = None
                return
            if pid != self._pid:
                self._new_session(pid)
            if st.get("over_24h") and not self._over_24h_sent:
                self._pending["over_24h"] = {"at": self._clock()}
                self._over_24h_sent = True
                self._mark_pending()
            inbound = command_inbound(getattr(session, "last_command", None))
            if inbound not in ("accept", "hold"):
                self._listening = False
                self._reason = (
                    "push link off: the panel was started without the inbound setting"
                    if inbound is None
                    else "push link off: inbound set to refuse"
                )
                return
            spawned_at = getattr(session, "spawned_at", None)
        try:
            target = self._resolve(pid, spawned_at)
        except TargetError as e:
            with self._lock:
                self._listening = False
                self._reason = str(e)
            return
        now = self._clock()
        with self._lock:
            if self._pid != pid:  # the session changed while we resolved
                return
            if self._send_failing:
                # HQ review R1: the target resolving is not the link working.
                # Until a send succeeds the link stays off with the failure
                # reason, between retries too.
                self._listening = False
            else:
                self._listening = _link_on(inbound)
                self._reason = _on_reason(inbound)
            due = (
                _has_events(self._pending)
                and (self._last_sent_at is None or now - self._last_sent_at >= _min_interval())
                and (self._last_attempt_at is None or now - self._last_attempt_at >= RETRY_S)
            )
            if not due:
                return
            pending = json.loads(json.dumps(self._pending))
            batch_no = self._batches + 1
            since = self._pending_since
            self._last_attempt_at = now
        text = format_batch(
            pending,
            batch_no=batch_no,
            now=now,
            since=since,
            console_pid=os.getpid(),
            nonce=self.nonce,
        )
        try:
            self._send(target["socket"], target["token"], text)
        except OSError as e:
            with self._lock:
                self._listening = False
                self._send_failing = True
                self._reason = "HQ's inbox refused the connection; events kept"
                self._last_error = type(e).__name__
            return
        with self._lock:
            self._clear_rendered(pending)
            if not _has_events(self._pending):
                self._pending_since = None
            self._last_sent_at = now
            self._last_handed_at = now
            self._batches = batch_no
            self._handed_total += 1
            self._last_error = None
            self._send_failing = False
            self._listening = _link_on(inbound)
            self._reason = _on_reason(inbound)

    def _clear_rendered(self, rendered: dict) -> None:
        """Subtract exactly what was rendered; anything that arrived mid-send stays."""
        for key, field in (
            ("texts", "count"),
            ("honks", "new"),
            ("halts", "new"),
            ("drops", "count"),
        ):
            r = rendered.get(key)
            cur = self._pending.get(key)
            if not r or not cur:
                continue
            cur[field] -= r.get(field, 0)
            if key == "drops":
                cur["bytes"] -= r.get("bytes", 0)
            if cur[field] <= 0:
                self._pending.pop(key, None)
        if rendered.get("over_24h"):
            self._pending.pop("over_24h", None)

    # ── status ──
    def _stale_locked(self) -> bool:
        if self._last_tick_at is None:
            return True
        return self._clock() - self._last_tick_at > STALE_POLLS * self.poll_s

    def listening(self) -> bool:
        with self._lock:
            return self._listening and not self._stale_locked()

    def status(self) -> dict:
        """For the Studio-only GET /hq/status. Fixed vocabulary, no paths, no token."""
        with self._lock:
            p = self._pending
            waiting = int((p.get("texts") or {}).get("count", 0))
            waiting += int((p.get("honks") or {}).get("new", 0))
            waiting += int((p.get("halts") or {}).get("new", 0))
            waiting += int((p.get("drops") or {}).get("count", 0))
            waiting += 1 if p.get("over_24h") else 0
            if self._last_tick_at is None:
                reason = "push poller not started"
            elif self._stale_locked():
                reason = "push poller stopped"
            else:
                reason = self._reason
            blind = sorted(k for k, v in self._sources.items() if v not in ("ok", "unknown"))
            return {
                "listening": self._listening and not self._stale_locked(),
                "reason": reason,
                "last_tick_at": (
                    datetime.fromtimestamp(self._last_tick_at).strftime("%H:%M:%S")
                    if self._last_tick_at
                    else None
                ),
                "sources": dict(self._sources),
                "blind": blind,
                "nonce_file_ok": self.nonce_file_ok,
                "inbound": command_inbound(getattr(self._session, "last_command", None)),
                "batches_handed": self._handed_total,
                "last_handed_at": _hm(self._last_handed_at) if self._last_handed_at else None,
                "events_waiting": waiting,
                "min_interval_s": _min_interval(),
                "last_error": self._last_error,
            }


_LINK: PushLink | None = None
_LINK_LOCK = threading.Lock()


def get_link(session=None) -> PushLink:
    global _LINK
    with _LINK_LOCK:
        if _LINK is None:
            if session is None:
                from . import hq_panel

                session = hq_panel.get_session()
            _LINK = PushLink(session, cursor_path=cursor_file())
        return _LINK


def start_poller(link: PushLink, interval_s: float = DEFAULT_POLL_S) -> threading.Thread:
    link.poll_s = float(interval_s)
    try:
        write_nonce_file(nonce_file(), link.nonce, os.getpid())
        link.nonce_file_ok = True
    except OSError:
        link.nonce_file_ok = False  # shown on /hq/status: HQ cannot cross-check the nonce

    def loop():
        while True:
            link.tick()
            time.sleep(interval_s)

    t = threading.Thread(target=loop, name="hq-push-poller", daemon=True)
    t.start()
    return t
