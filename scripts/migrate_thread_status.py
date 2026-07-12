#!/usr/bin/env python3
"""
One-shot migration: make the lifecycle `status` explicit on disk.

NOT RUN as part of the lifecycle-status patch, and it does not need to be.
The read path derives status for pre-enum records (resolved=False -> active,
resolved=True -> answered), so the ~128 live domain files keep reading correctly
forever, unmigrated. This script only makes the derived value explicit — it buys
greppability, not correctness. Run it deliberately or not at all.

Conservative by construction:
  - Derives ONLY from the existing `resolved` boolean. It never invents `held`,
    `superseded`, or `merged` — those are judgments, not data, and no migration
    is entitled to make them.
  - Rows that already carry a valid `status` are left alone (idempotent).
  - Unparseable lines are preserved verbatim.
  - Writes a .bak beside every modified file, and rewrites atomically.

Usage:
    python3 scripts/migrate_thread_status.py --dry-run    # always start here
    python3 scripts/migrate_thread_status.py
"""

import argparse
import json
import shutil
from pathlib import Path

from sovereign_stack.memory import (
    THREAD_STATUSES,
    _rewrite_jsonl_atomic,
    apply_thread_status,
    thread_status,
)

DEFAULT_THREADS_DIR = Path.home() / ".sovereign" / "chronicle" / "open_threads"


def migrate_file(path: Path, dry_run: bool = False) -> dict:
    stats = {"file": path.name, "rows": 0, "stamped": 0, "already": 0, "unparseable": 0}
    records: list = []

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        stats["rows"] += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            stats["unparseable"] += 1
            records.append(line)
            continue
        if record.get("status") in THREAD_STATUSES:
            stats["already"] += 1
        else:
            apply_thread_status(record, thread_status(record))
            stats["stamped"] += 1
        records.append(record)

    if stats["stamped"] and not dry_run:
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        _rewrite_jsonl_atomic(path, records)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads-dir", type=Path, default=DEFAULT_THREADS_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.threads_dir.is_dir():
        raise SystemExit(f"no such threads dir: {args.threads_dir}")

    totals = {"files": 0, "rows": 0, "stamped": 0, "already": 0, "unparseable": 0}
    for jsonl in sorted(args.threads_dir.glob("*.jsonl")):
        stats = migrate_file(jsonl, dry_run=args.dry_run)
        totals["files"] += 1
        for key in ("rows", "stamped", "already", "unparseable"):
            totals[key] += stats[key]
        if stats["stamped"]:
            print(f"  {stats['file']}: {stats['stamped']} stamped / {stats['rows']} rows")

    mode = "DRY RUN — nothing written" if args.dry_run else "migrated"
    print(
        f"\n{mode}: {totals['files']} files, {totals['rows']} rows, "
        f"{totals['stamped']} stamped, {totals['already']} already had status, "
        f"{totals['unparseable']} unparseable (preserved verbatim)"
    )


if __name__ == "__main__":
    main()
