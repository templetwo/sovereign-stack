#!/usr/bin/env python3
"""
One-shot migration: split bundled "(1) foo (2) bar" open threads into atomic
threads, each with a thread_id.

Idempotent. Safe to re-run: only migrates rows that contain the bundled pattern
AND do not yet carry a thread_id. Writes a .bak beside every modified file.

Also, when --resolve-token is passed, marks the historical token-revocation
atom resolved using the April 9 ground_truth insight as the attestation source.
This is the canonical continuity fix for the stale URGENT claim.
"""
import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from sovereign_stack.memory import (
    _split_bundled_question,
    _generate_thread_id,
    _parse_iso,
    ExperientialMemory,
)


TOKEN_RESOLUTION = (
    "GitHub token gho_UHJv5Zztt revoked by Tony on April 6, 2026. "
    "Source: insight recorded 2026-04-09T23:27:30 in spiral_20260406_235628 "
    "(domain=security, layer=ground_truth, resolved_from='Guardian Phase 0')."
)


def migrate_file(path: Path, dry_run: bool = False) -> dict:
    """Split any bundled entries in one JSONL file. Return a summary dict."""
    stats = {"read": 0, "atomized_from": 0, "atomized_into": 0, "skipped": 0, "unchanged": 0}
    new_lines = []

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            stats["read"] += 1
            try:
                thread = json.loads(line)
            except json.JSONDecodeError:
                stats["skipped"] += 1
                new_lines.append(line)
                continue

            # Skip resolved threads — don't rewrite history beyond adding ids.
            if thread.get("thread_id"):
                stats["unchanged"] += 1
                new_lines.append(json.dumps(thread))
                continue

            question = thread.get("question", "")
            sub_questions = _split_bundled_question(question)
            ts = _parse_iso(thread.get("timestamp")) or datetime.now()

            if len(sub_questions) == 1:
                # Not a bundle — just backfill thread_id.
                thread["thread_id"] = _generate_thread_id(question, ts)
                new_lines.append(json.dumps(thread))
                stats["unchanged"] += 1
            else:
                stats["atomized_from"] += 1
                for sub in sub_questions:
                    atom = {
                        **thread,
                        "question": sub,
                        "thread_id": _generate_thread_id(sub, ts),
                        "migrated_from_bundle": True,
                        "migration_timestamp": datetime.now().isoformat(),
                    }
                    new_lines.append(json.dumps(atom))
                    stats["atomized_into"] += 1

    if stats["atomized_from"] == 0 and stats["unchanged"] == stats["read"]:
        # Nothing to do.
        return stats

    if dry_run:
        return stats

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    with open(path, "w") as f:
        f.write("\n".join(new_lines) + "\n")
    stats["backup"] = str(backup)
    return stats


def resolve_token_thread(chronicle_root: Path, session_id: str) -> str:
    """Find the atomized token-revocation thread and mark it resolved."""
    chronicle = ExperientialMemory(root=str(chronicle_root))
    threads = chronicle.get_open_threads(domain="security,guardian,action-items", limit=50)
    target = None
    for t in threads:
        q = t.get("question", "").lower()
        if "gho_uhjv5zztt" in q or "revoke github token" in q:
            target = t
            break
    if not target:
        return ""
    return chronicle.resolve_thread_by_id(
        thread_id=target["thread_id"],
        resolution=TOKEN_RESOLUTION,
        session_id=session_id,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chronicle-root", default=str(Path.home() / ".sovereign" / "chronicle"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resolve-token", action="store_true",
                        help="After migration, mark the token-revocation atom resolved.")
    parser.add_argument("--session-id", default="migration_bundled_threads")
    args = parser.parse_args()

    root = Path(args.chronicle_root)
    threads_dir = root / "open_threads"
    if not threads_dir.exists():
        print(f"No threads directory at {threads_dir}")
        return

    totals = {"files": 0, "read": 0, "atomized_from": 0, "atomized_into": 0, "unchanged": 0, "skipped": 0}
    for jsonl in sorted(threads_dir.glob("*.jsonl")):
        stats = migrate_file(jsonl, dry_run=args.dry_run)
        totals["files"] += 1
        for k in ("read", "atomized_from", "atomized_into", "unchanged", "skipped"):
            totals[k] += stats.get(k, 0)
        if stats.get("atomized_from") or stats.get("backup"):
            print(f"  {jsonl.name}: bundled={stats['atomized_from']} → atoms={stats['atomized_into']}, unchanged={stats['unchanged']}")

    print(f"\nSummary: files={totals['files']} read={totals['read']} "
          f"bundled→atoms={totals['atomized_from']}→{totals['atomized_into']} "
          f"unchanged={totals['unchanged']} skipped={totals['skipped']}")

    if args.resolve_token and not args.dry_run:
        path = resolve_token_thread(root, args.session_id)
        if path:
            print(f"\nToken thread resolved → {path}")
        else:
            print("\nNo open token-revocation thread found to resolve.")


if __name__ == "__main__":
    main()
