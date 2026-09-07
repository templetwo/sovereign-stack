"""Ack recursion at the nape/ledger boundary.

A successful signal_ack of an error-shaped honk used to echo the original
observation (and the caller's reason) back through nape observe; scan_honks
then opened a new honk-source signal whose trigger_tool was signal_ack.
Measured 2026-09-07 13:2x EDT: 249 acks -> 497 new honks.

Cuts (both):
  1. scan_honks does not open a honk-source signal whose trigger_tool is
     signal_ack.
  2. observe skips a successful signal_ack (JSON ``ok: true``). Failures
     still land, so repeated_mistake still sees them.

Every write is under tmp_sovereign_root. Nothing here may touch live
~/.sovereign.
"""

from __future__ import annotations

import json
from pathlib import Path

from sovereign_stack import signal_ledger as sl
from sovereign_stack.dispatch_context import reset_caller_seat, set_caller_seat
from sovereign_stack.nape_daemon import NapeDaemon

SESSION = "ack-recursion-session"
SEED_HONK_ID = "h-seed-error-failed"
SEED_OBSERVATION = (
    "record_insight result contains error language and the write failed "
    "before any verify call was observed"
)
ACK_REASON = "error in the original observation; the write failed; now fixed"


def _honk_rows(root: Path) -> list[dict]:
    path = root / "nape" / "honks.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _obs_rows(root: Path) -> list[dict]:
    path = root / "nape" / "observations.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _open_honk_count(root: Path) -> int:
    latest = sl.load_latest(root)
    return sum(
        1 for row in latest.values() if row.get("source") == "honk" and row.get("state") == "open"
    )


def _write_seed_honk(root: Path) -> None:
    path = root / "nape" / "honks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "honk_id": SEED_HONK_ID,
                "session_id": SESSION,
                "pattern": "declare_before_verify",
                "level": "sharp",
                "trigger_tool": "record_insight",
                "observation": SEED_OBSERVATION,
                "timestamp": "2026-09-07T17:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_acking_error_shaped_honk_never_increases_open_count(tmp_sovereign_root):
    """End-to-end: seed -> ingest -> real signal_ack -> real observe -> ingest.

    Assertion is on the end state: acking a signal never increases the open
    honk-source count, and no honk with trigger_tool signal_ack is produced.
    """
    root = tmp_sovereign_root
    _write_seed_honk(root)
    assert "error" in SEED_OBSERVATION
    assert "failed" in SEED_OBSERVATION
    assert "error" in ACK_REASON
    assert "failed" in ACK_REASON
    assert "fixed" in ACK_REASON

    first = sl.scan_honks(root)
    assert first.opened == 1
    open_after_ingest = _open_honk_count(root)
    assert open_after_ingest == 1

    sid = sl.signal_id_for("honk", SEED_HONK_ID)
    token = set_caller_seat("seat:watch-2/3")
    try:
        ack_result = sl.handle_signal_tool(
            "signal_ack",
            {"signal_id": sid, "state": "acted", "reason": ACK_REASON},
            root=root,
        )
    finally:
        reset_caller_seat(token)

    payload = json.loads(ack_result)
    assert payload["ok"] is True
    assert payload["row"]["state"] == "acted"
    open_after_ack = _open_honk_count(root)
    assert open_after_ack == 0

    daemon = NapeDaemon(root=str(root))
    daemon.observe(
        "signal_ack",
        {"signal_id": sid, "state": "acted", "reason": ACK_REASON},
        ack_result,
        SESSION,
    )

    echo_honks = [h for h in _honk_rows(root) if h.get("trigger_tool") == "signal_ack"]
    assert echo_honks == [], (
        "a successful signal_ack must not produce a honk; got "
        f"{[h.get('pattern') for h in echo_honks]}"
    )
    ack_obs = [o for o in _obs_rows(root) if o.get("tool_name") == "signal_ack"]
    assert ack_obs == [], "a successful signal_ack must not be observed"

    second = sl.scan_honks(root)
    assert second.opened == 0
    assert _open_honk_count(root) == 0
    assert _open_honk_count(root) <= open_after_ack


def test_failed_signal_ack_is_observed_and_counts_as_repeated_mistake(tmp_sovereign_root):
    """ok: false (missing signal_id) still lands in nape and still repeats."""
    root = tmp_sovereign_root
    daemon = NapeDaemon(root=str(root))
    failed = sl.handle_signal_tool(
        "signal_ack",
        {"state": "acted", "reason": "trying"},
        root=root,
    )
    payload = json.loads(failed)
    assert payload["ok"] is False
    assert "signal_id" in payload["error"]

    daemon.observe("signal_ack", {"state": "acted", "reason": "trying"}, failed, SESSION)
    daemon.observe("signal_ack", {"state": "acted", "reason": "trying"}, failed, SESSION)

    ack_obs = [o for o in _obs_rows(root) if o.get("tool_name") == "signal_ack"]
    assert len(ack_obs) == 2, f"failed acks must still be observed; got {len(ack_obs)}"
    assert all('"ok": false' in o.get("result_str", "").lower() for o in ack_obs)

    repeats = [h for h in _honk_rows(root) if h.get("pattern") == "repeated_mistake"]
    assert len(repeats) == 1
    assert repeats[0]["trigger_tool"] == "signal_ack"

    # Cut (1): a failed-ack honk is nape-local. Ingest must not open it as a
    # new honk-source signal — that is the same recursion class.
    before = _open_honk_count(root)
    sl.scan_honks(root)
    assert _open_honk_count(root) == before == 0


def test_scan_honks_does_not_open_signal_ack_trigger(tmp_sovereign_root):
    """Ledger ingest cut, independent of nape observe."""
    root = tmp_sovereign_root
    path = root / "nape" / "honks.jsonl"
    path.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {
                    "honk_id": "h-echo-ack",
                    "timestamp": "2026-09-07T17:10:00Z",
                    "pattern": "declare_before_verify",
                    "trigger_tool": "signal_ack",
                    "observation": "signal_ack result contains error and failed; fixed",
                },
                {
                    "honk_id": "h-real-drift",
                    "timestamp": "2026-09-07T17:11:00Z",
                    "pattern": "declare_before_verify",
                    "trigger_tool": "record_insight",
                    "observation": "record_insight declared complete",
                },
            )
        ),
        encoding="utf-8",
    )
    result = sl.scan_honks(root)
    assert result.opened == 1
    latest = sl.load_latest(root)
    assert sl.signal_id_for("honk", "h-echo-ack") not in latest
    assert latest[sl.signal_id_for("honk", "h-real-drift")]["state"] == "open"
