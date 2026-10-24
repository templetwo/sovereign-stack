"""Design A2, decision 6: a dark HQ is seen in the console.

On every tick the push poller reads the panel transcript (hq_push.seat_state) and the
console says what it read. The seat is DARK when the newest assistant record is a
login-expired API-error turn. Any other API error is a STALL, and batches keep
flowing. The seat is UNKNOWN when the transcript cannot be read, parses to nothing,
or has gone quiet for more than 60 s before the last hand. A dark seat is handed no
batch until a real turn lands. The 24-hour notice rides every batch until a close
lands. panel_sessions.jsonl records start, stop, dark and close.

The control is the replay. tests/fixtures/panel_error_turns_20261020.jsonl holds the
six authentication_failed records of the 2026-10-20/21 dark window, copied from the
panel transcript with nine fields kept and nothing else, plus six records written by
hand (each says "synthetic" where it carries text).

Nothing here reads ~/.claude or writes ~/.sovereign. Transcripts live under a tmp
HQ_PUSH_PROJECTS_DIR, registries and stores under tmp_path, and the one live-root
check runs against a stand-in live root.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sovereign_stack import hq_panel, hq_push, live_store_guard
from sovereign_stack.live_store_guard import LiveStoreWriteRefused
from tests.test_hq_push import (
    PROC_START,
    Clock,
    FakeInbox,
    FakeSession,
    rows,
    spool_write,
    write_registry,
)

FIXTURE = Path(__file__).parent / "fixtures" / "panel_error_turns_20261020.jsonl"
#: The panel session the six error turns came from, and the panel's cwd.
SESSION_ID = "34a38121-aa63-40b9-8a02-767ee7defc08"
PANEL_CWD = "/Users/tony_studio"
#: The fixture's synthetic continued-in target.
CONTINUED_ID = "00000000-0000-4000-8000-000000000006"
CLOSE_TOOL = "mcp__sovereign-stack__close_session"

LAST_ANSWER = "2026-10-21T02:24:04.000Z"
FIRST_ERROR = "2026-10-21T03:20:43.285Z"
ERROR_STAMPS = (
    FIRST_ERROR,
    "2026-10-21T03:35:48.895Z",
    "2026-10-21T03:50:54.519Z",
    "2026-10-21T04:06:00.182Z",
    "2026-10-21T11:11:41.648Z",
    "2026-10-22T02:08:08.997Z",
)
SIXTH_ERROR = ERROR_STAMPS[-1]
ENQUEUE = "2026-10-22T02:15:00.000Z"
RATE_LIMIT = "2026-10-22T02:20:00.000Z"
REAL_AFTER = "2026-10-22T02:30:00.000Z"
CLOSE = "2026-10-22T02:35:00.000Z"

SEAT_KEYS = {
    "transcript",
    "dark",
    "dark_reason",
    "dark_since",
    "stalled",
    "last_answered_at",
    "unanswered_batches",
    "closed_at",
}
UNKNOWN_FIELDS = {
    "dark": False,
    "dark_reason": None,
    "dark_since": None,
    "stalled": None,
    "last_answered_at": None,
    "unanswered_batches": 0,
    "closed_at": None,
}
ACCEPT_REASON = "push link on (inbound accept, set in the console's environment)"


def epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def stamp_at(seconds: float) -> str:
    """A transcript-shaped stamp (UTC, milliseconds, Z) for an epoch second."""
    text = datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="milliseconds")
    return text.replace("+00:00", "Z")


def upto(records: list[dict], stamp: str) -> list[dict]:
    """The transcript as it stood when `stamp` was its newest record."""
    return [r for r in records if epoch(r["timestamp"]) <= epoch(stamp)]


def turn(stamp: str, *, error: str | None = None, tool: str | None = None) -> dict:
    """A hand-written assistant record (synthetic)."""
    if tool:
        block = {"type": "tool_use", "id": "toolu_synthetic", "name": tool, "input": {}}
    else:
        block = {"type": "text", "text": "synthetic"}
    rec = {
        "type": "assistant",
        "timestamp": stamp,
        "uuid": "00000000-0000-4000-8000-0000000000ff",
        "sessionId": SESSION_ID,
        "message": {"role": "assistant", "content": [block]},
    }
    if error:
        rec.update(isApiErrorMessage=True, error=error)
    return rec


def enqueue(stamp: str) -> dict:
    """What a handed peer message leaves in the transcript (synthetic)."""
    return {
        "type": "queue-operation",
        "operation": "enqueue",
        "timestamp": stamp,
        "sessionId": SESSION_ID,
        "content": "synthetic",
    }


def write_transcript(records, session_id: str = SESSION_ID, cwd: str = PANEL_CWD) -> Path:
    path = hq_push.transcript_path(session_id, cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def append_transcript(*records) -> None:
    with hq_push.transcript_path(SESSION_ID, PANEL_CWD).open("a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in records))


def panel_rows(root: Path) -> list[dict]:
    path = root / "hq" / "cockpit" / "panel_sessions.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def seat_link(root: Path, clock: Clock, session=None):
    """A link whose target names the panel transcript, with a recording send."""
    sent: list[str] = []

    def resolve(pid, spawned_at):
        return {
            "socket": "/tmp/x.sock",
            "token": "ab" * 32,
            "session_id": SESSION_ID,
            "cwd": PANEL_CWD,
        }

    link = hq_push.PushLink(
        session or FakeSession(spawned_at=clock.t),
        root=root,
        send=lambda sock, token, text: sent.append(text),
        resolve=resolve,
        clock=clock,
    )
    return link, sent


def socket_link(tmp_path: Path, fake_inbox, clock: Clock, *, over_24h: bool = False):
    """A link that resolves a real registry record and writes to a real socket."""
    sessions = tmp_path / "sessions"
    write_registry(
        sessions,
        31,
        fake_inbox.path,
        started_ms=int((clock.t + 1) * 1000),
        rec_extra={"sessionId": SESSION_ID, "cwd": PANEL_CWD},
    )
    return hq_push.PushLink(
        FakeSession(pid=31, spawned_at=clock.t, over_24h=over_24h),
        root=tmp_path / "root",
        clock=clock,
        resolve=lambda pid, spawned_at: hq_push.resolve_target(pid, spawned_at, sessions),
    )


def inbox_batches(fake_inbox, n: int, timeout: float = 3.0) -> list[str]:
    """The batch texts on the socket, once at least n have landed."""
    deadline = time.time() + timeout
    while time.time() < deadline and len(fake_inbox.lines) < 2 * n:
        time.sleep(0.02)
    frames = [json.loads(line) for line in fake_inbox.lines]
    return [f["message"]["content"] for f in frames if f.get("type") == "user"]


@pytest.fixture(autouse=True)
def _contained(monkeypatch, tmp_path):
    # No ps for a fake pid, no read of the real ~/.claude/projects, the shipped
    # interval and inbound.
    monkeypatch.setattr(hq_push, "ps_proc_start", lambda pid: PROC_START)
    monkeypatch.setenv("HQ_PUSH_PROJECTS_DIR", str(tmp_path / "projects"))
    for name in ("HQ_PUSH_MIN_INTERVAL_S", "HQ_PANEL_INBOUND", "HQ_PANEL_COMMAND"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fixture() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def fake_inbox():
    ib = FakeInbox()
    yield ib
    ib.close()


# ── where the transcript is ─────────────────────────────────────────────────


def test_transcript_path_folds_every_non_alphanumeric(tmp_path):
    assert (
        hq_push.transcript_path("x", "/Users/tony_studio", tmp_path)
        == tmp_path / "-Users-tony-studio" / "x.jsonl"
    )
    # The worktree key measured under ~/.claude/projects: the dot folds too.
    folded = hq_push.transcript_path("x", "/Users/tony_studio/.cache/wt-descend-stack", tmp_path)
    assert folded.parent.name == "-Users-tony-studio--cache-wt-descend-stack"
    # The default is HQ_PUSH_PROJECTS_DIR (the autouse fixture points it at tmp).
    assert hq_push.transcript_path("x", "/a") == tmp_path / "projects" / "-a" / "x.jsonl"


def test_resolve_target_carries_session_and_cwd(tmp_path, fake_inbox):
    def resolved(**rec):
        write_registry(tmp_path, 7, fake_inbox.path, started_ms=1_000_500, rec_extra=rec)
        return hq_push.resolve_target(7, 1000.0, tmp_path)

    t = resolved(sessionId=SESSION_ID, cwd=PANEL_CWD)
    assert t["session_id"] == SESSION_ID and t["cwd"] == PANEL_CWD
    assert t["socket"] == fake_inbox.path and t["token"] == "ab" * 32
    # A value that cannot name a transcript safely is None, and the target still
    # resolves: the seat reads unknown, the link keeps working.
    for bad in ("../x", "a/b", "", ".hidden", "x" * 200, 5, None):
        t = resolved(sessionId=bad, cwd=PANEL_CWD)
        assert t["session_id"] is None and t["socket"] == fake_inbox.path, bad
    for bad in ("relative/dir", "", 7, None):
        t = resolved(sessionId=SESSION_ID, cwd=bad)
        assert t["cwd"] is None and t["socket"] == fake_inbox.path, bad


def test_read_transcript_tail_drops_only_a_partial_first_line(tmp_path):
    p = tmp_path / "t.jsonl"
    recs = [{"type": "user", "timestamp": f"2026-10-22T00:00:0{i}.000Z", "n": i} for i in range(5)]
    lines = [json.dumps(r) + "\n" for r in recs]
    p.write_text("".join(lines))
    size = p.stat().st_size
    # Read from offset 0: the first line is whole and kept.
    assert [r["n"] for r in hq_push.read_transcript_tail(p)] == [0, 1, 2, 3, 4]
    # A window starting inside record 1: that partial line is dropped.
    cut = len(lines[0]) + 5
    assert [r["n"] for r in hq_push.read_transcript_tail(p, max_bytes=size - cut)] == [2, 3, 4]
    # A window starting exactly on record 2's first byte keeps record 2.
    on = len(lines[0]) + len(lines[1])
    assert [r["n"] for r in hq_push.read_transcript_tail(p, max_bytes=size - on)] == [2, 3, 4]
    # Unparseable lines and a torn last line are dropped.
    with p.open("a") as f:
        f.write('not json\n{"type": "user", "timestamp"')
    assert [r["n"] for r in hq_push.read_transcript_tail(p)] == [0, 1, 2, 3, 4]
    # A window holding part of one record parses to [] (the caller: unreadable).
    assert hq_push.read_transcript_tail(p, max_bytes=3) == []


# ── the replay: the six error turns of 2026-10-20/21 ────────────────────────


def test_replay_six_error_turns_marks_dark(fixture):
    # The control: before the first error the seat answers.
    before = hq_push.seat_state(upto(fixture, LAST_ANSWER), [])
    assert before["transcript"] == "ok" and before["dark"] is False
    assert before["last_answered_at"] == LAST_ANSWER
    # Dark from the first error turn on, and the episode keeps its first stamp.
    for stamp in ERROR_STAMPS:
        seat = hq_push.seat_state(upto(fixture, stamp), [])
        assert seat["dark"] is True and seat["dark_since"] == FIRST_ERROR, stamp
    assert hq_push.seat_state(upto(fixture, SIXTH_ERROR), []) == {
        "transcript": "ok",
        "dark": True,
        "dark_reason": "login expired",
        "dark_since": FIRST_ERROR,
        "stalled": None,
        "last_answered_at": LAST_ANSWER,
        "unanswered_batches": 0,
        "closed_at": None,
    }


def test_rate_limit_turn_is_a_stall_not_dark(fixture, tmp_path):
    """Review Focus 6: a rate limit is a stall; dark keys on authentication_failed alone."""
    seat = hq_push.seat_state(upto(fixture, RATE_LIMIT), [])
    assert seat["transcript"] == "ok" and seat["dark"] is False
    assert seat["stalled"] == "rate_limit"
    assert seat["dark_since"] is None and seat["dark_reason"] is None
    assert seat["last_answered_at"] == LAST_ANSWER
    # Batches still flow, and the status names the stall.
    write_transcript(upto(fixture, RATE_LIMIT))
    root = tmp_path / "root"
    clock = Clock(epoch("2026-10-22T02:21:00.000Z"))
    link, sent = seat_link(root, clock)
    spool_write(root, rows(1))
    link.tick()
    spool_write(root, rows(2))
    clock.t += 900
    link.tick()
    assert len(sent) == 1 and "newest rowid 2" in sent[0]
    st = link.status()
    assert st["reason"] == ACCEPT_REASON + "; HQ stalled (rate_limit)"
    assert st["seat"]["stalled"] == "rate_limit" and st["seat"]["dark"] is False
    assert panel_rows(root) == []  # a stall is not a dark episode


def test_a_real_turn_after_dark_clears_it(fixture):
    seat = hq_push.seat_state(upto(fixture, REAL_AFTER), [])
    assert seat["dark"] is False and seat["stalled"] is None
    assert seat["dark_since"] is None and seat["last_answered_at"] == REAL_AFTER


def test_unanswered_batches_counts_handed_after_last_answer(fixture):
    # The console's own hand times that night (batches 6 to 10): batch 6 went
    # out before the last answer, the four after it were never answered.
    handed = [
        epoch(s)
        for s in (
            "2026-10-21T02:23:51.371Z",
            "2026-10-21T03:20:42.875Z",
            "2026-10-21T03:35:48.519Z",
            "2026-10-21T03:50:54.129Z",
            "2026-10-21T04:05:59.810Z",
        )
    ]
    assert hq_push.seat_state(upto(fixture, SIXTH_ERROR), handed)["unanswered_batches"] == 4
    # The real turn after the window answers all of them.
    assert hq_push.seat_state(upto(fixture, REAL_AFTER), handed)["unanswered_batches"] == 0


def test_close_is_a_close_session_tool_use(fixture):
    assert hq_push.seat_state(fixture, [])["closed_at"] == CLOSE
    assert hq_push.seat_state(upto(fixture, REAL_AFTER), [])["closed_at"] is None
    # Another tool is not a close, and a close_session block outside an
    # assistant record is not one either.
    other = turn("2026-10-22T02:36:00.000Z", tool="mcp__sovereign-stack__record_insight")
    assert hq_push.seat_state(upto(fixture, REAL_AFTER) + [other], [])["closed_at"] is None
    not_assistant = dict(turn("2026-10-22T02:36:00.000Z", tool=CLOSE_TOOL), type="user")
    assert hq_push.seat_state(upto(fixture, REAL_AFTER) + [not_assistant], [])["closed_at"] is None


# ── unknown is never dark (Review Focus 2 and 7) ────────────────────────────


def test_unreadable_transcript_is_unknown_not_dark(fixture, tmp_path, monkeypatch):
    """Review Focus 2: no dark mark, no "answered" claim, batches handed as before,
    and the status says the transcript is unreadable."""
    assert hq_push.seat_state(None, [epoch(SIXTH_ERROR)]) == {
        "transcript": "unreadable",
        **UNKNOWN_FIELDS,
    }
    assert hq_push.read_transcript_tail(tmp_path / "missing.jsonl") is None
    root = tmp_path / "root"
    clock = Clock(epoch("2026-10-22T03:00:00.000Z"))
    link, sent = seat_link(root, clock)
    spool_write(root, rows(1))

    def handed_with(cause: str) -> None:
        n = len(sent)
        spool_write(root, rows(10 + n))
        clock.t += 900
        link.tick()
        st = link.status()
        assert len(sent) == n + 1, cause
        assert st["seat"] == {"transcript": "unreadable", **UNKNOWN_FIELDS}, cause
        assert st["reason"] == ACCEPT_REASON and st["listening"] is True, cause

    link.tick()
    handed_with("no transcript file at all")
    # A transcript under another cwd's project key is not this seat's.
    write_transcript(upto(fixture, SIXTH_ERROR), cwd="/Users/tony_studio/elsewhere")
    handed_with("a cwd whose project key differs")
    path = hq_push.transcript_path(SESSION_ID, PANEL_CWD)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    handed_with("a directory where the transcript should be")
    path.rmdir()
    os.mkfifo(path)
    handed_with("a FIFO (never blocks the poller)")
    path.unlink()
    if os.geteuid() != 0:
        write_transcript(upto(fixture, SIXTH_ERROR))
        os.chmod(path, 0)
        try:
            handed_with("a permission error")
        finally:
            os.chmod(path, 0o600)
    write_transcript(upto(fixture, SIXTH_ERROR))

    def boom(*a, **k):
        raise RuntimeError("reader exploded")

    monkeypatch.setattr(hq_push, "read_transcript_tail", boom)
    handed_with("a reader that raises (never a poller error)")


def test_empty_tail_is_unknown(fixture, tmp_path):
    """Review Focus 2: a tail that parses to nothing is unknown, never "ok" with
    nothing answered."""
    assert hq_push.seat_state([], [epoch(SIXTH_ERROR)]) == {
        "transcript": "unreadable",
        **UNKNOWN_FIELDS,
    }
    # Records with no stamp say nothing about time: unknown as well.
    untimed = [{"type": "last-prompt", "sessionId": SESSION_ID}, {"type": "mode"}]
    assert hq_push.seat_state(untimed, [])["transcript"] == "unreadable"
    # Through the link: a transcript of unparseable lines reads unreadable and
    # the batch is still handed.
    path = hq_push.transcript_path(SESSION_ID, PANEL_CWD)
    path.parent.mkdir(parents=True)
    path.write_text("not json\nnor this\n")
    assert hq_push.read_transcript_tail(path) == []
    root = tmp_path / "root"
    clock = Clock(epoch("2026-10-22T03:00:00.000Z"))
    link, sent = seat_link(root, clock)
    spool_write(root, rows(1))
    link.tick()
    spool_write(root, rows(2))
    clock.t += 900
    link.tick()
    assert len(sent) == 1
    assert link.status()["seat"] == {"transcript": "unreadable", **UNKNOWN_FIELDS}


def test_queue_operation_record_counts_as_newest(fixture):
    hand = [epoch("2026-10-22T02:14:00.000Z")]
    # The enqueue at 02:15 is newer than the hand: ok, with no newer assistant record.
    seat = hq_push.seat_state(upto(fixture, ENQUEUE), hand)
    assert seat["transcript"] == "ok" and seat["dark"] is True
    # Without it the newest record (the 02:08 error) is 351 s older than the hand.
    assert hq_push.seat_state(upto(fixture, SIXTH_ERROR), hand)["transcript"] == "stale"


def test_transcript_older_than_last_hand_reads_unknown(fixture):
    """Review Focus 7: a transcript the panel no longer writes is never counted
    against and never dark."""
    records = upto(fixture, SIXTH_ERROR)
    newest = epoch(SIXTH_ERROR)
    handed = [epoch(FIRST_ERROR) - 1, newest + 61]
    assert hq_push.seat_state(records, handed) == {"transcript": "stale", **UNKNOWN_FIELDS}
    # More than 60 s, not 60 s: at exactly 60 s the transcript still counts.
    at_bound = hq_push.seat_state(records, [epoch(FIRST_ERROR) - 1, newest + 60])
    assert at_bound["transcript"] == "ok" and at_bound["dark"] is True
    assert at_bound["unanswered_batches"] == 2


def test_continued_in_is_followed(fixture, tmp_path):
    """Review Focus 7: a /clear's continued-in record is followed to the new file."""
    path = write_transcript(fixture)
    sibling = path.parent / f"{CONTINUED_ID}.jsonl"
    assert hq_push.follow_continuation(fixture, path) == sibling
    assert hq_push.follow_continuation(upto(fixture, CLOSE), path) is None
    # The sibling carries its own continued-in: it is followed ONCE, never twice.
    third = "00000000-0000-4000-8000-000000000007"
    sibling.write_text(
        json.dumps(turn("2026-10-22T02:41:00.000Z"))
        + "\n"
        + json.dumps(
            {
                "type": "continued-in",
                "continuedInSessionId": third,
                "sessionId": CONTINUED_ID,
                "timestamp": "2026-10-22T02:42:00.000Z",
            }
        )
        + "\n"
    )
    (path.parent / f"{third}.jsonl").write_text(
        json.dumps(turn("2026-10-22T02:43:00.000Z", error="authentication_failed")) + "\n"
    )
    root = tmp_path / "root"
    clock = Clock(epoch("2026-10-22T02:42:30.000Z"))
    link, _ = seat_link(root, clock)
    link.tick()
    seat = link.status()["seat"]
    assert seat["transcript"] == "ok" and seat["dark"] is False
    assert seat["last_answered_at"] == "2026-10-22T02:41:00.000Z"  # the sibling's, not 02:35
    # A continued-in whose file is not there yet: followed, unreadable, never
    # judged from the file the seat has left.
    sibling.unlink()
    clock.t += 30
    link.tick()
    assert link.status()["seat"] == {"transcript": "unreadable", **UNKNOWN_FIELDS}


# ── the link: a dark seat holds batches; the 24-hour line repeats ───────────


def test_dark_seat_holds_batches(fixture, tmp_path, fake_inbox):
    root = tmp_path / "root"
    clock = Clock(epoch("2026-10-21T03:00:00.000Z"))
    write_transcript(upto(fixture, LAST_ANSWER))
    link = socket_link(tmp_path, fake_inbox, clock)
    spool_write(root, rows(1))
    link.tick()  # baseline
    spool_write(root, rows(2))
    clock.t += 900  # 03:15
    link.tick()
    assert link.status()["batches_handed"] == 1 and len(inbox_batches(fake_inbox, 1)) == 1
    # HQ's turn on that batch failed: login expired.
    first_error = next(r for r in fixture if r["timestamp"] == FIRST_ERROR)
    append_transcript(enqueue("2026-10-21T03:15:01.000Z"), first_error)
    spool_write(root, rows(3))
    clock.t = epoch("2026-10-21T03:31:00.000Z")  # due again
    link.tick()
    st = link.status()
    assert st["batches_handed"] == 1  # held
    since = hq_push._hm(epoch(FIRST_ERROR))
    assert st["reason"] == f"seat dark since {since} (login expired): batches held, 1 unanswered"
    assert st["seat"]["dark"] is True and st["seat"]["dark_since"] == FIRST_ERROR
    assert st["seat"]["unanswered_batches"] == 1 and st["events_waiting"] == 1
    clock.t += 30
    link.tick()
    assert link.status()["batches_handed"] == 1
    # One dark row per episode, however many ticks see it.
    assert [(r["event"], r["pid"]) for r in panel_rows(root)] == [("dark", 31)]
    dark_row = panel_rows(root)[0]
    assert dark_row["since"] == FIRST_ERROR and dark_row["reason"] == "login expired"
    assert dark_row["unanswered"] == 1
    # A real turn newer than dark_since: the held batch goes out.
    append_transcript(turn("2026-10-21T03:40:00.000Z"))
    clock.t = epoch("2026-10-21T03:40:30.000Z")
    link.tick()
    st = link.status()
    assert st["batches_handed"] == 2 and st["seat"]["dark"] is False
    assert st["reason"] == ACCEPT_REASON
    batches = inbox_batches(fake_inbox, 2)
    assert len(batches) == 2 and "newest rowid 3" in batches[1]


def test_24h_line_repeats_until_close(tmp_path, fake_inbox):
    root = tmp_path / "root"
    t0 = epoch("2026-10-22T02:00:00.000Z")
    clock = Clock(t0)
    write_transcript([turn(stamp_at(t0 - 60))])
    link = socket_link(tmp_path, fake_inbox, clock, over_24h=True)
    spool_write(root, rows(1))
    link.tick()  # baseline; the crossing is seen here
    clock.t = t0 + 900
    link.tick()  # the crossing alone fires batch 1
    append_transcript(enqueue(stamp_at(t0 + 901)), turn(stamp_at(t0 + 905)))
    spool_write(root, rows(2))
    clock.t = t0 + 1800
    link.tick()  # batch 2: a text, and the line again
    closed = stamp_at(t0 + 1860)
    append_transcript(
        enqueue(stamp_at(t0 + 1801)), turn(closed, tool=CLOSE_TOOL), turn(stamp_at(t0 + 1865))
    )
    spool_write(root, rows(3))
    clock.t = t0 + 2700
    link.tick()  # batch 3: a text, and no line
    batches = inbox_batches(fake_inbox, 3)
    assert len(batches) == 3
    line = f"passed 24 hours open at {hq_push._hm(t0)}"
    assert line in batches[0] and line in batches[1] and "newest rowid 2" in batches[1]
    assert "24 hours" not in batches[2] and "newest rowid 3" in batches[2]
    assert link.status()["events_waiting"] == 0  # the line rides batches, it is no event
    clock.t += 30
    link.tick()
    assert [(r["event"], r.get("closed_at")) for r in panel_rows(root)] == [("close", closed)]


# ── panel_sessions.jsonl ────────────────────────────────────────────────────


def test_panel_sessions_rows(tmp_path, monkeypatch):
    root = tmp_path / "root"
    assert not (root / "hq" / "cockpit").exists()
    hq_panel.record_panel_event(root, "start", pid=11)
    hq_panel.record_panel_event(root, "stop", pid=11, last_exit=0)
    hq_panel.record_panel_event(
        root, "dark", pid=12, since=FIRST_ERROR, reason="login expired", unanswered=4
    )
    hq_panel.record_panel_event(root, "close", pid=12, closed_at=CLOSE)
    got = panel_rows(root)  # the directory was created
    assert [(r["event"], r["pid"]) for r in got] == [
        ("start", 11),
        ("stop", 11),
        ("dark", 12),
        ("close", 12),
    ]
    for r in got:
        at = datetime.fromisoformat(r["at"])
        assert at.tzinfo is not None and at.utcoffset().total_seconds() == 0
    assert got[1]["last_exit"] == 0 and got[3]["closed_at"] == CLOSE
    assert (got[2]["since"], got[2]["reason"], got[2]["unanswered"]) == (
        FIRST_ERROR,
        "login expired",
        4,
    )
    # Exactly the design's four events, and every row names the panel child.
    with pytest.raises(ValueError):
        hq_panel.record_panel_event(root, "resume", pid=11)
    with pytest.raises(ValueError):
        hq_panel.record_panel_event(root, "start")
    # A live-root path is refused under pytest, before the directory is made.
    live = tmp_path / "live"
    monkeypatch.setattr(live_store_guard, "live_root", lambda: live.resolve())
    with pytest.raises(LiveStoreWriteRefused) as e:
        hq_panel.record_panel_event(live, "start", pid=1)
    assert e.value.store == "hq/cockpit/panel_sessions"
    assert not live.exists()


def test_the_panel_writes_its_own_start_and_stop_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("HQ_PANEL_COMMAND", json.dumps(["/bin/sh", "-c", "exit 3"]))
    monkeypatch.setenv("HQ_PANEL_CWD", str(tmp_path))
    root = tmp_path / "root"
    monkeypatch.setenv("SOVEREIGN_ROOT", str(root))
    s = hq_panel.HQSession(seat_finder=lambda: None)
    try:
        assert s.start(80, 24)[0]
        pid = s.last_started_pid
        s._reader.join(timeout=3)
    finally:
        s._terminate_for_tests()
    got = panel_rows(root)
    assert [(r["event"], r["pid"]) for r in got] == [("start", pid), ("stop", pid)]
    assert got[1]["last_exit"] == 3
    # A store the guard refuses never stops the panel, and neither row lands.
    live = tmp_path / "live"
    monkeypatch.setattr(live_store_guard, "live_root", lambda: live.resolve())
    monkeypatch.setenv("SOVEREIGN_ROOT", str(live))
    s2 = hq_panel.HQSession(seat_finder=lambda: None)
    try:
        assert s2.start(80, 24)[0]
        s2._reader.join(timeout=3)
    finally:
        s2._terminate_for_tests()
    assert not live.exists()


# ── the page ────────────────────────────────────────────────────────────────


def test_app_js_renders_each_seat_word_ahead_of_the_link_word():
    js = (Path(hq_push.__file__).parent / "dashboard_web_static" / "app.js").read_text()
    seat_fn = js[js.index("function hqSeatWord(") :]
    seat_fn = seat_fn[: seat_fn.index("\n}\n")]
    for word in (
        "last answered ",
        "batches unanswered",
        "login expired",
        "panel transcript unreadable",
        "panel transcript stale",
        "HQ stalled (",
    ):
        assert word in seat_fn, word
    # The transcript's UTC stamps go through Date, never sliced as local digits.
    assert "Date.parse(" in seat_fn
    link_fn = js[js.index("function hqLinkWord(") :]
    link_fn = link_fn[: link_fn.index("\n}\n")]
    assert link_fn.index("hqSeatWord(") < link_fn.index("if (hp.listening)")
    assert "hp.running" in link_fn[: link_fn.index("if (hp.listening)")]
