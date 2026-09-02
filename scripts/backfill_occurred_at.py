#!/usr/bin/env python3
"""
Backfill the AUTHORSHIP TIME of chronicle entries written by a bridge drain.

THE DEFECT. A Ring-2 proposal is written at one moment and drained at another,
routinely days apart: the "does the membrane hold?" proposals are from
2026-05-25/27, and the Grok / GPT-5.6 pause acknowledgements sat a full day in
their queues before anyone drained them. Until the commit paths learned to
forward `original_timestamp`, every one of those landed in the chronicle under
the DRAIN OPERATOR's clock. The proposer's identity was taught to travel on
2026-08-03 (grok) and 2026-08-30 (openai). Their MOMENT was not.

THE DESIGN IS ANTHONY'S, 2026-06-19, and it rejects the obvious alternative BY
NAME: an occurred_at-only fix "lets any un-taught reader silently miss them",
because recency-ordered recall, the date-bounded readers, the boot surfaces and
the dashboards all key on `timestamp`. So for each matched entry the plan is:

    timestamp       <- the proposal's FILING time      (backdate in place)
    occurred_at     <- the entry's OLD timestamp       (the commit instant)
    timestamp_source<- "bridge_backfill_20260902"      (say it out loud)

Both axes survive; no reader has to be taught anything.

────────────────────────────────────────────────────────────────────────────
THE COST, AND THE REASON THIS TOOL DEFAULTS TO A DRY RUN AND REPORTS
CITATIONS. `provenance.derive_claim_id` hashes timestamp + US + domain + US +
content. `timestamp` is IN THE PREIMAGE. Backdating an entry therefore CHANGES
ITS CLAIM ID — which is fine for a new write (no id existed) and is the entire
risk of a backfill, because a claim id is the address other records point at:
supersession ledger rows, `verified_by` receipts of kind `claim`, open threads,
handoffs, letters and filings. Rewriting a cited entry silently orphans every
pointer at it.

So this script computes the OLD and NEW claim id for every planned change and
greps the whole of ~/.sovereign for the old id (16-hex display prefix, which is
the form entries actually cite), then prints how many planned entries are cited
elsewhere and by what. That number is the decision, and it is HQ's to take to
Anthony — not this script's to take.

MATCHING IS EXACT AND NEVER FUZZY. A proposal maps to a chronicle entry only on
byte-equal `content` AND byte-equal normalized `domain`. Zero matches or more
than one: SKIPPED and listed. Nothing is invented, nothing is guessed, and an
ambiguous case is reported rather than resolved — this rewrites the primary
record, and a wrong match here is indistinguishable from a correct one
afterwards.

USAGE
    python3 scripts/backfill_occurred_at.py                 # DRY RUN (default)
    python3 scripts/backfill_occurred_at.py --verbose       # + per-entry detail
    python3 scripts/backfill_occurred_at.py --root PATH     # a different store
    python3 scripts/backfill_occurred_at.py --apply         # writes. See below.

--apply, in one pass, per touched shard:
    * copies the shard to ~/.sovereign/backups/occurred_at_backfill_<ts>/
      (mirroring its path under the store) BEFORE any rewrite;
    * rewrites only the matched lines, leaving every other byte untouched;
    * appends one row per rewritten entry to
      ~/.sovereign/chronicle/claim_aliases.jsonl
      {old_claim_id, new_claim_id, reason, ts} — so a reader holding a stale
      id can still resolve it. THE ALIAS FILE IS A PROPOSAL, NOT A DECISION:
      nothing in the running tree reads it yet, and whether the house wants an
      alias layer at all is Anthony's call. Written so that if --apply is ever
      run, the mapping exists rather than being reconstructible only from
      backups;
    * writes a human-readable change log beside the backups.

--apply is NOT to be run without Anthony's explicit go on the citation report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TIMESTAMP_SOURCE = "bridge_backfill_20260902"
QUEUES = ("grok_bridge", "openai_bridge")
_FIELD_SEP = "\x1f"

# Only these commit targets are addressable by (content, domain) in the
# insights store. propose_learning lands in learnings/ keyed by applies_to and
# carries no `content` field at all; handoff / close_session / comms_acknowledge
# are not chronicle insights. They are counted as NOT APPLICABLE and named,
# never silently dropped from the denominator.
INSIGHT_TARGETS = frozenset({"record_insight"})


def derive_claim_id(entry: dict) -> str:
    """Local copy of provenance.derive_claim_id, deliberately.

    This script must be runnable against a store with no venv and no import of
    the package it is about to rewrite. The formula is pinned by a test that
    imports the real one and asserts they agree, so a drift shows up as a red
    test rather than as a wrong id in a backup log.
    """
    preimage = (
        str(entry.get("timestamp") or "")
        + _FIELD_SEP
        + str(entry.get("domain") or "")
        + _FIELD_SEP
        + str(entry.get("content") or "")
    )
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def normalize_domain(domain: str) -> str:
    """Same rule as memory._normalize_domain: strip space around commas."""
    if not domain:
        return domain or ""
    return ",".join(part.strip() for part in str(domain).split(","))


def load_committed_proposals(root: Path) -> tuple[list[dict], list[str]]:
    """Every status=committed proposal across both queues, plus parse errors.

    Parse failures are RETURNED, not swallowed: a proposal this script could
    not read is a hole in the denominator, and a report that hides its holes is
    the fail-open shape the whole house is built against.
    """
    proposals: list[dict] = []
    problems: list[str] = []
    for queue in QUEUES:
        qdir = root / queue / "pending_writes"
        if not qdir.is_dir():
            continue
        for path in sorted(qdir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"{path}: {type(exc).__name__}: {exc}")
                continue
            if not isinstance(data, dict) or data.get("status") != "committed":
                continue
            data["_queue"] = queue
            data["_path"] = str(path)
            proposals.append(data)
    return proposals, problems


def load_insight_index(root: Path) -> tuple[dict, list[str]]:
    """Index every insight entry by (content, normalized domain).

    Values are lists, because collisions are the ambiguous case this script
    refuses to resolve. Parsed with json, one object per line — never grep,
    which counts lines on a store whose shards are line-delimited JSON.
    """
    index: dict[tuple[str, str], list[dict]] = {}
    problems: list[str] = []
    insights = root / "chronicle" / "insights"
    if not insights.is_dir():
        return index, [f"{insights} does not exist"]
    for shard in sorted(insights.rglob("*.jsonl")):
        if any(part.startswith(".") for part in shard.relative_to(insights).parts):
            continue  # dotted = a retired copy; never live corpus
        try:
            lines = shard.read_text(errors="replace").splitlines()
        except OSError as exc:
            problems.append(f"{shard}: {exc}")
            continue
        for lineno, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"{shard}:{lineno}: unparseable line")
                continue
            if not isinstance(entry, dict):
                continue
            key = (str(entry.get("content") or ""), normalize_domain(entry.get("domain") or ""))
            index.setdefault(key, []).append(
                {"entry": entry, "shard": shard, "lineno": lineno}
            )
    return index, problems


def build_plan(root: Path, min_gap_days: float = 0.0) -> dict:
    """Everything the dry run reports. Pure: reads, never writes."""
    proposals, proposal_problems = load_committed_proposals(root)
    index, index_problems = load_insight_index(root)

    matched: list[dict] = []
    ambiguous: list[dict] = []
    unmatched: list[dict] = []
    not_applicable: list[dict] = []
    already_stamped: list[dict] = []
    already_aligned: list[dict] = []

    for prop in proposals:
        target = prop.get("commit_target") or prop.get("tool")
        args = prop.get("arguments") or {}
        short = str(prop.get("proposal_id", "?"))[:8]
        if target not in INSIGHT_TARGETS:
            not_applicable.append({"proposal": short, "target": target, "queue": prop["_queue"]})
            continue
        content = str(args.get("content") or "")
        domain = normalize_domain(args.get("domain") or "")
        hits = index.get((content, domain), [])
        row = {
            "proposal": short,
            "queue": prop["_queue"],
            "filed_at": prop.get("timestamp"),
            "domain": domain,
            "content_head": content[:70],
        }
        if not hits:
            unmatched.append(row)
            continue
        if len(hits) > 1:
            row["candidates"] = len(hits)
            ambiguous.append(row)
            continue
        hit = hits[0]
        entry = hit["entry"]
        if entry.get("timestamp_source"):
            row["existing_source"] = entry["timestamp_source"]
            already_stamped.append(row)
            continue
        old_ts = str(entry.get("timestamp") or "")
        filed = str(prop.get("timestamp") or "")
        if not filed:
            row["why"] = "proposal carries no filing timestamp"
            unmatched.append(row)
            continue
        if filed == old_ts:
            already_aligned.append(row)
            continue
        new_entry = dict(entry)
        new_entry["timestamp"] = filed
        if not new_entry.get("occurred_at"):
            new_entry["occurred_at"] = old_ts
        new_entry["timestamp_source"] = TIMESTAMP_SOURCE
        row.update(
            {
                "shard": str(hit["shard"]),
                "lineno": hit["lineno"],
                "old_timestamp": old_ts,
                "new_timestamp": filed,
                "old_claim_id": derive_claim_id(entry),
                "new_claim_id": derive_claim_id(new_entry),
                "new_entry": new_entry,
            }
        )
        matched.append(row)

    # --min-gap: rows below the threshold are set aside as BELOW THRESHOLD,
    # never silently dropped — a filtered-out row is still part of the
    # denominator and is reported as its own count.
    below_threshold: list[dict] = []
    if min_gap_days > 0:
        kept: list[dict] = []
        for row in matched:
            gap = gap_days(row)
            if gap is not None and gap < min_gap_days:
                below_threshold.append(row)
            else:
                kept.append(row)
        matched = kept

    citations = find_citations(root, matched)
    return {
        "min_gap_days": min_gap_days,
        "below_threshold": below_threshold,
        "root": str(root),
        "proposals_committed": len(proposals),
        "matched": matched,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "not_applicable": not_applicable,
        "already_stamped": already_stamped,
        "already_aligned": already_aligned,
        "citations": citations,
        "problems": proposal_problems + index_problems,
    }


def find_citations(root: Path, matched: list[dict]) -> dict[str, list[str]]:
    """For each planned rewrite, where in the store its OLD id appears.

    Searches the 16-hex DISPLAY prefix, which is the form entries actually cite
    (provenance.display_id truncates to 16); a full-64 citation contains the
    prefix, so one probe catches both.

    NOTHING IS EXCLUDED, AND THAT IS THE CORRECTION. A first draft skipped the
    entry's own shard, on the reasoning that "an entry is not a citation of
    itself". But a claim id is DERIVED ON READ AND NEVER STORED — an entry
    cannot contain its own id — so the only way that id appears in that shard
    is a DIFFERENT entry citing it, which is precisely the citation this
    report exists to count. Excluding the shard suppressed exactly the
    same-domain supersession, the commonest citation shape there is, and it
    would have understated the one number gating --apply. The unit test that
    covered it passed either way, because the fixture had no in-shard citation
    to miss.
    """
    if not matched:
        return {}
    probes = {row["old_claim_id"][:16]: row["old_claim_id"] for row in matched}
    found: dict[str, list[str]] = {full: [] for full in probes.values()}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".jsonl", ".json", ".md", ".txt"):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for prefix, full in probes.items():
            if prefix in text:
                found[full].append(str(path))
    return {k: v for k, v in found.items() if v}


def gap_days(row: dict) -> float | None:
    """How far back this rewrite would move the entry, in days.

    THE NUMBER THAT TURNS A COUNT INTO A DECISION. A claim-id change buys
    something only in proportion to how wrong the stamp was: measured on the
    live store 2026-09-02, the MEDIAN correction is 0.02 days — about half an
    hour — while the maximum is 98 days. Rewriting 46 addresses to fix mostly
    half-hours is a different proposition from rewriting the handful that are
    months out, and the report must let a human tell them apart rather than
    presenting one undifferentiated total.
    """
    try:
        old = datetime.fromisoformat(row["old_timestamp"])
        new = datetime.fromisoformat(row["new_timestamp"])
    except (ValueError, KeyError, TypeError):
        return None
    if (old.tzinfo is None) != (new.tzinfo is None):
        return None
    return (old - new).total_seconds() / 86400.0


def print_report(plan: dict, verbose: bool) -> None:
    m, a, u = plan["matched"], plan["ambiguous"], plan["unmatched"]
    cited = plan["citations"]
    cited_planned = sum(1 for row in m if row["old_claim_id"] in cited)
    print("=" * 78)
    print("BACKFILL AUTHORSHIP TIME — DRY RUN" if not plan.get("_applied") else "APPLIED")
    print(f"store: {plan['root']}")
    print("=" * 78)
    print(f"committed proposals scanned : {plan['proposals_committed']}")
    print(f"  MATCHED (would rewrite)   : {len(m)}")
    print(f"  AMBIGUOUS (skipped)       : {len(a)}")
    print(f"  UNMATCHED (skipped)       : {len(u)}")
    print(f"  not applicable (non-insight targets) : {len(plan['not_applicable'])}")
    print(f"  already stamped           : {len(plan['already_stamped'])}")
    print(f"  already aligned (no change needed)   : {len(plan['already_aligned'])}")
    if plan.get("min_gap_days"):
        print(
            f"  below --min-gap {plan['min_gap_days']}d (set aside): "
            f"{len(plan['below_threshold'])}"
        )
    print()
    # HOW FAR BACK, BUCKETED. A claim-id rewrite buys something only in
    # proportion to how wrong the stamp was; an undifferentiated total of 46
    # hides that most of them are half-hours.
    gaps = [g for g in (gap_days(row) for row in m) if g is not None]
    if gaps:
        gaps.sort()
        buckets = {
            "under 1 hour": sum(1 for g in gaps if g < 1 / 24),
            "1 hour - 1 day": sum(1 for g in gaps if 1 / 24 <= g < 1),
            "1 - 7 days": sum(1 for g in gaps if 1 <= g < 7),
            "over 7 days": sum(1 for g in gaps if g >= 7),
        }
        print("  HOW FAR BACK each rewrite would move the entry:")
        for label, count in buckets.items():
            print(f"      {label:<16} {count}")
        print(
            f"      min {gaps[0]:.2f}d  median {gaps[len(gaps) // 2]:.2f}d  max {gaps[-1]:.2f}d"
        )
        print("      (--min-gap DAYS takes only the rewrites that buy something)")
    print()
    print(f"  CITED ELSEWHERE           : {cited_planned} of {len(m)} planned rewrites")
    print("      (backdating changes claim_id — timestamp is in the preimage)")
    if cited:
        for claim_id, where in cited.items():
            print(f"      {claim_id[:16]}  cited in {len(where)} file(s):")
            for w in where[:6]:
                print(f"          {w}")
            if len(where) > 6:
                print(f"          ... and {len(where) - 6} more")
    if plan["problems"]:
        print()
        print(f"  UNREADABLE (holes in the denominator): {len(plan['problems'])}")
        for p in plan["problems"][:10]:
            print(f"      {p}")
    if verbose:
        for label, rows in (("MATCHED", m), ("AMBIGUOUS", a), ("UNMATCHED", u)):
            if not rows:
                continue
            print(f"\n--- {label} ---")
            for row in rows:
                extra = ""
                if label == "MATCHED":
                    extra = f"  {row['old_timestamp']} -> {row['new_timestamp']}"
                elif label == "AMBIGUOUS":
                    extra = f"  ({row.get('candidates')} candidates)"
                print(f"  [{row['queue']}] {row['proposal']}  {row['domain']}{extra}")
                print(f"      {row['content_head']}")
    print()
    if not plan.get("_applied"):
        print("DRY RUN — nothing was written. Re-run with --apply to enact,")
        print("and only after the citation count above has been decided on.")


def apply_plan(root: Path, plan: dict) -> Path:
    """Rewrite matched lines, after backing up every shard it touches."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = root / "backups" / f"occurred_at_backfill_{stamp}"
    backup_root.mkdir(parents=True, exist_ok=True)

    by_shard: dict[str, list[dict]] = {}
    for row in plan["matched"]:
        by_shard.setdefault(row["shard"], []).append(row)

    changelog: list[str] = [
        f"# occurred_at / original_timestamp backfill — {stamp}",
        f"# store: {root}",
        f"# entries rewritten: {len(plan['matched'])} across {len(by_shard)} shard(s)",
        "",
    ]
    for shard_str, rows in sorted(by_shard.items()):
        shard = Path(shard_str)
        # BACKUP BEFORE THE REWRITE, mirroring the shard's path under the
        # store so a restore is an unambiguous copy-back.
        rel = shard.relative_to(root)
        dest = backup_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shard, dest)

        lines = shard.read_text(errors="replace").splitlines()
        by_lineno = {row["lineno"]: row for row in rows}
        out: list[str] = []
        for i, line in enumerate(lines, start=1):
            row = by_lineno.get(i)
            if row is None:
                out.append(line)
                continue
            out.append(json.dumps(row["new_entry"]))
            changelog.append(
                f"{shard_str}:{i}  {row['old_timestamp']} -> {row['new_timestamp']}  "
                f"claim {row['old_claim_id'][:16]} -> {row['new_claim_id'][:16]}  "
                f"[{row['queue']} {row['proposal']}]"
            )
        shard.write_text("\n".join(out) + "\n")

    # THE ALIAS FILE. A proposal, not a decision — nothing reads it yet.
    aliases = root / "chronicle" / "claim_aliases.jsonl"
    aliases.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with aliases.open("a") as f:
        for row in plan["matched"]:
            f.write(
                json.dumps(
                    {
                        "old_claim_id": row["old_claim_id"],
                        "new_claim_id": row["new_claim_id"],
                        "reason": (
                            "authorship-time backfill: timestamp backdated to the bridge "
                            "proposal's filing time; timestamp is in the claim_id preimage"
                        ),
                        "ts": now,
                    }
                )
                + "\n"
            )

    (backup_root / "CHANGELOG.txt").write_text("\n".join(changelog) + "\n")
    return backup_root


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--root", default=str(Path.home() / ".sovereign"))
    ap.add_argument("--apply", action="store_true", help="WRITE. Default is a dry run.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--min-gap",
        type=float,
        default=0.0,
        dest="min_gap",
        metavar="DAYS",
        help=(
            "Only plan rewrites that move the entry back at least DAYS. Rows below "
            "the threshold are reported as a count, never silently dropped."
        ),
    )
    ap.add_argument("--json", action="store_true", help="emit the plan as JSON")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"store not found: {root}", file=sys.stderr)
        return 2

    plan = build_plan(root, min_gap_days=args.min_gap)
    if args.apply:
        if not plan["matched"]:
            print("nothing to apply.")
            return 0
        backup = apply_plan(root, plan)
        plan["_applied"] = True
        print(f"backups + changelog: {backup}")
    if args.json:
        printable = json.loads(json.dumps(plan, default=str))
        for row in printable.get("matched", []):
            row.pop("new_entry", None)
        print(json.dumps(printable, indent=2))
    else:
        print_report(plan, args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
