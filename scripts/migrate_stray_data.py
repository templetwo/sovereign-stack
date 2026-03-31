#!/usr/bin/env python3
"""
Migrate stray data into the canonical chronicle location.

Some early sessions wrote to ~/.sovereign/{insights,learnings,open_threads,transformations}/
instead of ~/.sovereign/chronicle/{insights,learnings,open_threads,transformations}/.

This script:
1. Finds all JSONL records in stray directories
2. Deduplicates by timestamp against existing chronicle data
3. Appends only new records into the correct chronicle subdirs
4. Archives the stray directories as .migrated_YYYY-MM-DD/

Also migrates old ~/.sovereign/compaction/ files into the
compaction_memory buffer format.

Safe to run multiple times (deduplication prevents double-writes).
"""

import json
import sys
import shutil
from datetime import datetime, date
from pathlib import Path


SOVEREIGN_ROOT = Path.home() / ".sovereign"
CHRONICLE_ROOT = SOVEREIGN_ROOT / "chronicle"
ARCHIVE_SUFFIX = f".migrated_{date.today()}"

# Stray dir → chronicle subdir
STRAY_MAP = {
    SOVEREIGN_ROOT / "insights":       CHRONICLE_ROOT / "insights",
    SOVEREIGN_ROOT / "learnings":      CHRONICLE_ROOT / "learnings",
    SOVEREIGN_ROOT / "open_threads":   CHRONICLE_ROOT / "open_threads",
    SOVEREIGN_ROOT / "transformations": CHRONICLE_ROOT / "transformations",
}


def collect_timestamps(directory: Path) -> set:
    """Read all timestamps from all JSONL files in a directory tree."""
    timestamps = set()
    for jsonl_file in directory.rglob("*.jsonl"):
        for line in jsonl_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                timestamps.add(json.loads(line)["timestamp"])
            except (json.JSONDecodeError, KeyError):
                continue
    return timestamps


def merge_jsonl_dir(stray_dir: Path, chronicle_dir: Path) -> dict:
    """
    Merge JSONL files from stray_dir into chronicle_dir.
    Preserves subdirectory structure (domain folders).
    Returns stats.
    """
    stats = {"scanned": 0, "migrated": 0, "skipped_duplicate": 0, "skipped_error": 0}

    for stray_file in stray_dir.rglob("*.jsonl"):
        # Determine relative path to preserve domain structure
        rel = stray_file.relative_to(stray_dir)
        dest_file = chronicle_dir / rel
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        # Collect timestamps already in destination
        existing_ts = set()
        if dest_file.exists():
            for line in dest_file.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    existing_ts.add(json.loads(line)["timestamp"])
                except (json.JSONDecodeError, KeyError):
                    continue

        # Append only new records
        new_lines = []
        for line in stray_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            stats["scanned"] += 1
            try:
                record = json.loads(line)
                ts = record.get("timestamp")
                if ts and ts in existing_ts:
                    stats["skipped_duplicate"] += 1
                else:
                    new_lines.append(line)
                    if ts:
                        existing_ts.add(ts)
                    stats["migrated"] += 1
            except (json.JSONDecodeError, KeyError):
                stats["skipped_error"] += 1

        if new_lines:
            with open(dest_file, "a") as f:
                for line in new_lines:
                    f.write(line + "\n")
            print(f"  + {rel}: {len(new_lines)} record(s) migrated → {dest_file}")

    return stats


def migrate_old_compaction():
    """Migrate ~/.sovereign/compaction/ old JSON files into compaction_memory buffer."""
    old_dir = SOVEREIGN_ROOT / "compaction"
    new_buffer_file = SOVEREIGN_ROOT / "compaction_memory" / "compaction_buffer.json"

    if not old_dir.exists():
        return

    old_files = list(old_dir.glob("*.json"))
    if not old_files:
        return

    print(f"\nMigrating {len(old_files)} legacy compaction file(s)...")

    # Load existing buffer
    existing_summaries = []
    if new_buffer_file.exists():
        try:
            data = json.loads(new_buffer_file.read_text())
            existing_summaries = data.get("summaries", [])
        except (json.JSONDecodeError, KeyError):
            existing_summaries = []

    existing_sessions = {s.get("session_id") for s in existing_summaries}

    added = 0
    for old_file in sorted(old_files):
        try:
            old_data = json.loads(old_file.read_text())
        except (json.JSONDecodeError, OSError):
            print(f"  ! Could not parse {old_file.name}, skipping")
            continue

        session_id = old_data.get("session_id", old_file.stem)
        if session_id in existing_sessions:
            print(f"  ~ {old_file.name}: already in buffer, skipping")
            continue

        # Convert old format → CompactionSummary format
        new_summary = {
            "timestamp": old_data.get("compaction_timestamp", datetime.now().isoformat()),
            "summary_text": old_data.get("conversation_summary", "Legacy compaction data"),
            "session_id": session_id,
            "compaction_number": len(existing_summaries) + added + 1,
            "key_points": [f"git_state: {old_data['git_state']}"] if old_data.get("git_state") else [],
            "active_tasks": [],
            "recent_breakthroughs": [],
        }
        existing_summaries.append(new_summary)
        existing_sessions.add(session_id)
        added += 1
        print(f"  + {old_file.name} → compaction_memory buffer (session: {session_id})")

    if added > 0:
        # Keep only last 3 (FIFO)
        existing_summaries = existing_summaries[-3:]
        new_buffer_file.parent.mkdir(parents=True, exist_ok=True)
        new_buffer_file.write_text(json.dumps({
            "summaries": existing_summaries,
            "last_updated": datetime.now().isoformat(),
        }, indent=2))
        print(f"  Buffer updated: {len(existing_summaries)} summaries")

    # Archive old compaction dir
    archive_path = old_dir.parent / (old_dir.name + ARCHIVE_SUFFIX)
    old_dir.rename(archive_path)
    print(f"  Archived: {old_dir} → {archive_path}")


def archive_stray_dir(stray_dir: Path):
    """Rename stray dir to .migrated_DATE to signal it's been processed."""
    if stray_dir.exists():
        archive_path = stray_dir.parent / (stray_dir.name + ARCHIVE_SUFFIX)
        stray_dir.rename(archive_path)
        print(f"  Archived: {stray_dir.name}/ → {archive_path.name}/")


def main():
    print("=" * 60)
    print("Sovereign Stack - Stray Data Migration")
    print(f"Chronicle root: {CHRONICLE_ROOT}")
    print("=" * 60)

    CHRONICLE_ROOT.mkdir(parents=True, exist_ok=True)
    total_migrated = 0

    for stray_dir, chronicle_subdir in STRAY_MAP.items():
        if not stray_dir.exists():
            continue

        jsonl_files = list(stray_dir.rglob("*.jsonl"))
        if not jsonl_files:
            archive_stray_dir(stray_dir)
            continue

        print(f"\nMigrating {stray_dir.name}/ ({len(jsonl_files)} file(s))...")
        stats = merge_jsonl_dir(stray_dir, chronicle_subdir)
        total_migrated += stats["migrated"]
        print(f"  Scanned: {stats['scanned']}, Migrated: {stats['migrated']}, "
              f"Duplicates skipped: {stats['skipped_duplicate']}, Errors: {stats['skipped_error']}")

        archive_stray_dir(stray_dir)

    migrate_old_compaction()

    print("\n" + "=" * 60)
    print(f"Migration complete. {total_migrated} record(s) rescued into chronicle.")
    print("Stray directories archived with suffix:", ARCHIVE_SUFFIX)
    print("=" * 60)

    # Verify
    print("\nVerification — chronicle domain counts:")
    for subdir in ["insights", "learnings", "open_threads", "transformations"]:
        path = CHRONICLE_ROOT / subdir
        if path.exists():
            total = sum(
                1 for f in path.rglob("*.jsonl")
                for line in f.read_text().splitlines()
                if line.strip()
            )
            print(f"  {subdir}: {total} records")


if __name__ == "__main__":
    main()
