"""
Sovereign Stack auto-recovery monitor.

Replaces `scripts/monitor` (bash, ps-grep, knew only 2 of 5 services)
with a Python loop that uses the connectivity manager as truth.

Loop:
  1. check_all() every interval
  2. For each STATUS_DOWN endpoint, attempt restart with exponential
     backoff. A streak of failures is capped at max_restarts before
     giving up on that endpoint until the next baseline reset.
  3. STATUS_DEGRADED is logged but NOT auto-restarted — degraded means
     the service is up but the health probe failed, which can have
     transient causes (network blip, slow startup) that resolve without
     intervention. Restarting on degraded would amplify flakiness.
  4. STATUS_STALE on periodic services is informational; restarting a
     periodic service via launchctl kickstart is a no-op for the next
     scheduled tick.

Logging: append-only ~/.sovereign/monitor.log (one JSON line per event).

Public API:
  - MonitorConfig dataclass
  - RestartTracker (tracks per-endpoint restart counts + backoff)
  - run_loop(config) (async, the long-running entrypoint)
  - run_once(config) (sync, single tick — used by tests)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import connectivity

# ── Defaults ────────────────────────────────────────────────────────────────


DEFAULT_INTERVAL = 30        # seconds between full checks
DEFAULT_MAX_RESTARTS = 5     # per endpoint, before giving up
DEFAULT_BASELINE_RESET = 3600  # reset restart counters every hour
DEFAULT_BACKOFF_BASE = 2.0   # exponential base (seconds)
DEFAULT_BACKOFF_CAP = 300.0  # max backoff (5 min)


def _monitor_log_path() -> Path:
    return Path(os.environ.get(
        "SOVEREIGN_ROOT", Path.home() / ".sovereign",
    )) / "monitor.log"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Config ──────────────────────────────────────────────────────────────────


@dataclass
class MonitorConfig:
    interval: int = DEFAULT_INTERVAL
    max_restarts: int = DEFAULT_MAX_RESTARTS
    baseline_reset_seconds: int = DEFAULT_BASELINE_RESET
    backoff_base: float = DEFAULT_BACKOFF_BASE
    backoff_cap: float = DEFAULT_BACKOFF_CAP
    dry_run: bool = False
    log_path: Path | None = None
    # Names to skip (e.g. periodic services that shouldn't be restarted
    # on stale, or services Anthony intentionally has off).
    exclude: list[str] = field(default_factory=list)


# ── Restart tracker ─────────────────────────────────────────────────────────


@dataclass
class _RestartRecord:
    count: int = 0           # failed-restart streak
    last_attempt: float = 0.0
    # last_baseline is the wall-clock timestamp from which the
    # "long-healthy gap clears the streak" timer counts. Default is 0
    # (epoch) so the first should_attempt always sees the reset
    # condition true and snaps last_baseline to whatever `now` is — that
    # keeps the test-injected clock and the production clock consistent.
    last_baseline: float = 0.0


class RestartTracker:
    """
    Per-endpoint restart bookkeeping. Encodes the backoff schedule:

        next_attempt_at(now) = last_attempt + backoff_base ** count

    capped at backoff_cap. Returns False ("don't try yet") if not enough
    time has elapsed since the last attempt.

    Streak resets every baseline_reset_seconds — a service that's been
    healthy for an hour gets a fresh budget.
    """

    def __init__(self, config: MonitorConfig):
        self._cfg = config
        self._records: dict[str, _RestartRecord] = {}

    def _record(self, name: str) -> _RestartRecord:
        rec = self._records.get(name)
        if rec is None:
            # last_baseline left at default 0 — first should_attempt or
            # record_attempt that runs will snap it to the current `now`.
            rec = _RestartRecord()
            self._records[name] = rec
        return rec

    def should_attempt(self, name: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        rec = self._record(name)

        # Periodic baseline reset — a long-healthy gap clears the streak.
        if now - rec.last_baseline > self._cfg.baseline_reset_seconds:
            rec.count = 0
            rec.last_baseline = now

        if rec.count >= self._cfg.max_restarts:
            return False

        if rec.count == 0:
            return True

        backoff = min(
            self._cfg.backoff_cap,
            self._cfg.backoff_base ** rec.count,
        )
        return (now - rec.last_attempt) >= backoff

    def record_attempt(
        self,
        name: str,
        success: bool,
        now: float | None = None,
    ) -> None:
        now = now if now is not None else time.time()
        rec = self._record(name)
        rec.last_attempt = now
        # Snap last_baseline forward on first interaction so the reset
        # window timer is anchored at first contact (consistent with
        # caller's clock — real or test-injected).
        if rec.last_baseline == 0.0:
            rec.last_baseline = now
        if success:
            rec.count = 0
            rec.last_baseline = now
        else:
            rec.count += 1

    def state(self) -> dict[str, dict]:
        return {
            name: {"count": r.count, "last_attempt": r.last_attempt}
            for name, r in self._records.items()
        }


# ── Logging ─────────────────────────────────────────────────────────────────


def _log_event(config: MonitorConfig, event: dict) -> None:
    path = config.log_path or _monitor_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


# ── Single tick (sync, testable) ────────────────────────────────────────────


def run_once(
    config: MonitorConfig,
    tracker: RestartTracker | None = None,
    *,
    check_fn: Callable[[], list] | None = None,
    restart_fn: Callable[[connectivity.Endpoint], connectivity.ActionResult] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> dict:
    """
    Execute one monitoring tick. Returns the tick summary dict.

    Args:
        config: Monitor settings.
        tracker: RestartTracker to share across ticks. Created fresh if None.
        check_fn: Override for connectivity.check_all (tests).
        restart_fn: Override for connectivity.restart (tests).
        now_fn: Override for time.time (tests).
    """
    tracker = tracker or RestartTracker(config)
    check = check_fn or connectivity.check_all
    do_restart = restart_fn or connectivity.restart
    now = (now_fn or time.time)()

    statuses = check()
    actions: list[dict] = []

    for status in statuses:
        if status.name in config.exclude:
            continue
        if status.status != connectivity.STATUS_DOWN:
            # Only DOWN triggers auto-recovery. DEGRADED / STALE / UNKNOWN
            # are logged elsewhere and require human attention or
            # natural recovery.
            continue

        if not tracker.should_attempt(status.name, now=now):
            actions.append({
                "name": status.name,
                "action": "deferred",
                "reason": "backoff_or_max_reached",
                "tracker_state": tracker.state().get(status.name, {}),
            })
            continue

        endpoint = next(
            (e for e in connectivity.ENDPOINTS if e.name == status.name),
            None,
        )
        if endpoint is None:
            continue

        if config.dry_run:
            actions.append({
                "name": status.name,
                "action": "would_restart",
                "dry_run": True,
            })
            continue

        result = do_restart(endpoint)
        tracker.record_attempt(status.name, result.ok, now=now)
        actions.append({
            "name": status.name,
            "action": "restart",
            "ok": result.ok,
            "returncode": result.returncode,
            "stderr": result.stderr[:200] if result.stderr else "",
        })

    summary = {
        "timestamp": _now_iso(),
        "checked": [s.name for s in statuses],
        "down": [s.name for s in statuses
                 if s.status == connectivity.STATUS_DOWN],
        "degraded": [s.name for s in statuses
                     if s.status == connectivity.STATUS_DEGRADED],
        "actions": actions,
        "dry_run": config.dry_run,
    }
    _log_event(config, summary)
    return summary


# ── Async loop ──────────────────────────────────────────────────────────────


async def run_loop(config: MonitorConfig) -> None:
    """The long-running monitor. Cancellation-safe."""
    tracker = RestartTracker(config)
    _log_event(config, {
        "timestamp": _now_iso(),
        "event": "loop_start",
        "config": {
            "interval": config.interval,
            "max_restarts": config.max_restarts,
            "dry_run": config.dry_run,
        },
    })
    try:
        while True:
            run_once(config, tracker)
            await asyncio.sleep(config.interval)
    except asyncio.CancelledError:
        _log_event(config, {"timestamp": _now_iso(), "event": "loop_stop"})
        return
