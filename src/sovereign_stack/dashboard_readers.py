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

    Naive strings are assumed UTC rather than local: the chronicle writes
    both shapes, and treating a naive stamp as local time on an
    Eastern-offset box silently ages every record by 4 hours — enough to
    expire every arrival request against a 900s window.
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
    """Truthiness is WRONG for this field and the failure is silent.

    Live open-thread records carry ``"resolved": "False"`` — the STRING.
    ``if rec.get("resolved")`` reads that as True and reports 0 open
    threads on a box with ~180. The inverse mistake (``not`` on the raw
    value) reads ``"True"`` as unresolved. Only an explicit string-aware
    comparison gets both directions right.
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


def reset_caches() -> None:
    """Drop every cached external probe. Called by tests on BOTH sides of a
    test so cache state can never make test order load-bearing."""
    with _cache_lock:
        _CACHE.clear()


def _cache_get(key: str, ttl: float):
    with _cache_lock:
        hit = _CACHE.get(key)
    if hit is None:
        return None
    stored_at, value = hit
    if (time.monotonic() - stored_at) > ttl:
        return None
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
    """
    directory = _sovereign_root() / "chronicle" / "open_threads"
    if not directory.is_dir():
        return None

    unresolved: list[dict] = []
    malformed = 0
    files = 0
    for path in sorted(directory.rglob("*.jsonl")):
        files += 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
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
        "age_seconds": newest_age,
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
                "rid": row["rid"],
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


def _letter_frontmatter(path: Path) -> dict:
    """Read ONLY the YAML frontmatter block — never a byte of the body.

    The body of a lineage letter is a private thing written by one instance
    to another; it does not belong on an ops dashboard. Enforced
    structurally by stopping the read at the closing ``---`` rather than
    slurping the file and slicing, so there is no moment at which the body
    is in memory next to the returned dict.
    """
    fields: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline().strip()
            if first != "---":
                return fields
            for line in handle:
                if line.strip() == "---":
                    break
                match = _FRONTMATTER_KEY.match(line.rstrip("\n"))
                if match:
                    fields[match.group(1)] = match.group(2).strip()
    except OSError:
        return {}
    return fields


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
    listener = subprocess.run(
        ["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"],
        capture_output=True,
        text=True,
        timeout=GUARDIAN_TIMEOUT_SECONDS,
        check=False,
    )
    services: dict[str, bool] = {}
    for name in ("ollama", "sovereign"):
        found = subprocess.run(
            ["pgrep", "-x", name],
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
