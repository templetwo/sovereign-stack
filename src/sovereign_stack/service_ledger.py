"""
Service disable/enable ledger — reason-receipts for "disabled" state.

connectivity.py derives ok/down/stale/degraded purely from launchctl and
HTTP probes. Nothing stores WHO took a service offline, WHEN, WHY, or what
would bring it back — "disabled" is inferred, never recorded. The closest
thing to a record today is prose buried in a plist XML comment, unqueryable
by anything but a human reading that one file.

This ledger makes "disabled" a first-class, queryable state. Append-only,
same shape as provenance.py's supersession ledger (append_supersession /
fold_supersessions): latest action per service wins, corrections append a
new record rather than editing history.

Public API:
  - SERVICE_ACTIONS: ("disable", "enable")
  - default_services_path() -> Path
  - build_service_record(...) -> dict
  - append_service_record(ledger_path, record) -> dict
  - load_service_records(ledger_path) -> list[dict]
  - fold_service_state(records) -> dict[service_label -> latest record]
  - extract_python_module_arg(plist_text) -> str | None
  - check_module_importable(module_name) -> dict
  - check_service_module(record) -> dict | None
  - seed_known_disabled_services(*, timestamp=None) -> list[dict]

No MCP coupling, no directory creation at import — pure data in, verdicts
out, matching provenance.py's own house style. Seeding the LIVE ledger
(writing to ~/.sovereign/services.jsonl) is a deploy action, not something
any function here does as a side effect of import or of building records.
"""

from __future__ import annotations

import importlib.util
import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

SERVICE_ACTIONS = ("disable", "enable")


class ServiceLedgerError(ValueError):
    """A service ledger record is malformed or invalid."""


def default_services_path() -> Path:
    """The live service ledger path. Computed on call, never at import."""
    return Path.home() / ".sovereign" / "services.jsonl"


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


# ── Ledger records ───────────────────────────────────────────────────────────


def build_service_record(
    *,
    action: str,
    service: str,
    reason: str = "",
    by: str = "",
    re_enable_condition: str | None = None,
    plist_path: str | None = None,
    timestamp: str | None = None,
) -> dict:
    """
    Build one ledger record.

    `service` is the launchctl Label — the same identifier connectivity.py's
    Endpoint.label carries, so reconciliation can key off it directly. A
    service with no registered Endpoint (e.g. the dream daemon, invisible
    to ENDPOINTS by design) is still addressable here by label.

    `re_enable_condition` is optional: some disables are conditional
    ("HQ reads N nights by hand"), others are permanent supersessions with
    no path back (a renamed plist) — None means "no re-enable condition
    was recorded," not "unknown."

    `plist_path` is optional metadata: when the disabled service's plist
    is known, it lets check_service_module() check whether the program it
    invokes actually imports, without this module hardcoding any path.

    Raises:
        ServiceLedgerError: invalid action, empty service, or a disable
            record with no reason — an unreasoned disable is exactly the
            gap this ledger exists to close.
    """
    if action not in SERVICE_ACTIONS:
        raise ServiceLedgerError(f"invalid service action {action!r} (valid: {SERVICE_ACTIONS})")
    if not isinstance(service, str) or not service.strip():
        raise ServiceLedgerError("service must be a non-empty string (the launchctl label)")
    if action == "disable" and not (reason or "").strip():
        raise ServiceLedgerError(f"disable record for {service!r} requires a reason")
    return {
        "action": action,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "service": service,
        "reason": reason,
        "by": by,
        "re_enable_condition": re_enable_condition,
        "plist_path": plist_path,
    }


def append_service_record(ledger_path: Path, record: dict) -> dict:
    """
    Append one record to the ledger (parent directory created lazily —
    first write, never import). Validates the action/service core so a
    hand-built record can't poison the fold.
    """
    if record.get("action") not in SERVICE_ACTIONS:
        raise ServiceLedgerError(f"invalid service action {record.get('action')!r}")
    service = record.get("service")
    if not isinstance(service, str) or not service.strip():
        raise ServiceLedgerError(f"service must be a non-empty string, got {service!r}")
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def load_service_records(ledger_path: Path) -> list[dict]:
    """Read all ledger records in file order. Missing ledger -> []."""
    return list(_iter_jsonl(Path(ledger_path)))


def fold_service_state(records: list[dict]) -> dict[str, dict]:
    """
    Fold the append-only ledger into effective state: service label ->
    its latest record, annotated with state="disabled"|"enabled" (latest
    action per service wins — mirrors provenance.fold_supersessions).
    Malformed entries (bad action, non-string service) are skipped rather
    than raising, matching the chronicle read convention: a corrupt line
    degrades the fold, it doesn't crash it.
    """
    fold: dict[str, dict] = {}
    for record in records:
        action = record.get("action")
        service = record.get("service")
        if action not in SERVICE_ACTIONS or not isinstance(service, str):
            continue
        effective = dict(record)
        effective["state"] = "disabled" if action == "disable" else "enabled"
        fold[service] = effective
    return fold


# ── Unrunnable surfacing: plist references a module that does not import ────

_MODULE_ARG_RE = re.compile(r"<string>-m</string>\s*<string>([\w.]+)</string>")


def extract_python_module_arg(plist_text: str) -> str | None:
    """
    Pull the `-m <module>` argument out of a launchd plist's
    ProgramArguments array, if present. Regex against the raw XML text
    (adjacent <string> tags) rather than a full plist parse — forgiving of
    surrounding structure, avoids pulling in plistlib for two strings.
    """
    match = _MODULE_ARG_RE.search(plist_text)
    return match.group(1) if match else None


def check_module_importable(module_name: str) -> dict:
    """
    Check whether `module_name` resolves via Python's import machinery,
    WITHOUT executing it — importlib.util.find_spec stops at spec
    resolution, it never runs the module body.

    Surfaces the "not merely paused, actually unrunnable" class of
    problem: a plist can be well-formed and its label legitimately
    stored-disabled, while the module it would invoke does not exist at
    all on the current checkout (e.g. code that lives only on an unmerged
    branch). find_spec DOES import parent packages to resolve a dotted
    submodule path (e.g. `sovereign_stack.daemons` to resolve
    `...dream_daemon`) — that's inherent to how the import system locates
    submodules and is benign; the leaf module itself is never imported.

    Returns {"module": str, "importable": bool, "error": str | None}.
    """
    try:
        spec = importlib.util.find_spec(module_name)
    except ModuleNotFoundError as e:
        return {"module": module_name, "importable": False, "error": str(e)}
    except (ImportError, ValueError) as e:
        return {"module": module_name, "importable": False, "error": str(e)}
    if spec is None:
        return {"module": module_name, "importable": False, "error": "module not found"}
    return {"module": module_name, "importable": True, "error": None}


def check_service_module(record: dict) -> dict | None:
    """
    If a ledger record carries plist_path, parse it for a `-m module`
    invocation and check importability. Returns None when there's no
    plist_path to check (nothing to say) or the plist has no -m
    invocation (nothing to check — not every service is `python -m ...`).
    """
    plist_path = record.get("plist_path")
    if not plist_path:
        return None
    try:
        text = Path(plist_path).read_text(encoding="utf-8")
    except OSError as e:
        return {"module": None, "importable": None, "error": f"could not read plist: {e}"}
    module = extract_python_module_arg(text)
    if module is None:
        return None
    return check_module_importable(module)


# ── Known seed data ──────────────────────────────────────────────────────────
#
# Pure data. Building these records writes nothing — call
# append_service_record(default_services_path(), record) per record to
# actually seed the live ledger; that write is a deploy action.


def seed_known_disabled_services(*, timestamp: str | None = None) -> list[dict]:
    """
    The two out-of-band disabled services mapped 2026-07-12 while STATUS_DISABLED
    sat dead in connectivity.py:

      - the dream daemon (com.templetwo.sovereign.dream): never launchctl-loaded,
        reason quoted verbatim from the plist's own XML comment.
      - the legacy tunnel (com.templetwo.sovereign-tunnel): superseded by
        com.templetwo.cloudflared-tunnel — the plist carries the .disabled
        filename suffix, the replacement plist is the one ENDPOINTS registers.

    `timestamp`, if given, is applied to every seed record uniformly (a
    single seeding event) — mirrors build_service_record's own convention
    of "timestamp = when this record was written," not a backdated guess
    at when the underlying event happened. Evidence for the historical
    event lives in the `reason` text, not a fabricated timestamp.
    """
    dream_plist = str(
        Path.home() / "Library" / "LaunchAgents" / "com.templetwo.sovereign.dream.plist"
    )
    return [
        build_service_record(
            action="disable",
            service="com.templetwo.sovereign.dream",
            reason=(
                "DO NOT launchctl load THIS YET. Phase 1 eval runs are manual — "
                "HQ wants to read a few nights of real dream output by hand "
                "before this fires unattended. (quoted verbatim from the plist's "
                "own XML comment, header dated 2026-07-06)"
            ),
            by="HQ",
            re_enable_condition=(
                "HQ reads several nights of real dream output by hand, then "
                "runs: launchctl load ~/Library/LaunchAgents/com.templetwo.sovereign.dream.plist"
            ),
            plist_path=dream_plist,
            timestamp=timestamp,
        ),
        build_service_record(
            action="disable",
            service="com.templetwo.sovereign-tunnel",
            reason=(
                "Legacy label, superseded by com.templetwo.cloudflared-tunnel "
                "(same cloudflared binary, renamed plist — ENDPOINTS registers "
                "the current label, not this one). Filename carries the "
                "conventional .disabled suffix. Evidence: "
                "com.templetwo.sovereign-tunnel.plist.disabled mtime 2026-02-17 "
                "(inferred from filesystem, not a recorded event date)."
            ),
            by="HQ",
            re_enable_condition=None,
            plist_path=None,
            timestamp=timestamp,
        ),
    ]
