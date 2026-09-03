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

EXIT CODES, and they are the machine-readable half of everything below. The
report says "SHARDS REFUSED" in prose; a caller scripting `--apply && ...`
never reads prose, so a refusal has to be a code or it is a fail-open on the
exit status of a script written against fail-open (SOP #1: exit code 0 is not
"ran").
    0  the run did what it said — a dry run, or an --apply that refused nothing
    2  no store at --root
    3  --apply REFUSED ENTIRELY: a live server is answering (guard 1)
    4  --apply ran but AT LEAST ONE SHARD WAS REFUSED by guard 2. Partial or
       total: any refusal is 4, because "some of it landed" is not "it landed".
       Nothing is corrupted — a refused shard is genuinely untouched — and the
       report names every one of them.

--apply is GATED TWICE, both fail-closed, because this rewrites the primary
record by LINE POSITION and holds no lock any other process respects
(`provenance.chronicle_write_lock` is an in-process RLock; the real
cross-process fix is the unmerged `hardening/cross-process-flock` branch, whose
own race proof loses 35 of 60 writes silently):

  1. A LIVE SERVER REFUSES THE WHOLE RUN. If anything answers on
     127.0.0.1:3434 (sovereign-sse) or 127.0.0.1:8100 (sovereign-bridge),
     --apply exits 3 naming the port and the launchctl labels to stop. A
     server appending to a shard mid-rewrite loses its write to this script's
     whole-file `write_text`, with no error on either side.
  2. EVERY PLANNED LINE IS RE-READ AND COMPARED against the entry the plan
     captured, as a PARSED DICT — not bytes (which would refuse over key
     ordering) and not the claim id (whose preimage is only
     timestamp+domain+content, so a line that drifted in `layer` or
     `verified_by` would be waved through). One mismatch refuses the WHOLE
     shard: the file moved under the plan, so every other line number in it is
     suspect. A refused shard gets no backup, no rewrite, no alias row and no
     changelog line, is named in the report, and makes the whole run EXIT 4 —
     partial or total, because a caller scripting `--apply && ...` reads prose
     never and the exit status always.

The DRY RUN is gated by neither — the report a human needs in order to decide
is always available.

--apply, in one pass, per touched shard that passes both gates:
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
import copy
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
            index.setdefault(key, []).append({"entry": entry, "shard": shard, "lineno": lineno})
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
        # DEEP, not `dict(entry)`. `new_entry` below is a shallow copy, so a
        # shallow `old_entry` would share the SAME nested `verified_by` /
        # `supersedes` objects with it. Nothing mutates them today; the moment
        # something edits a nested field on `new_entry`, the guard's own
        # reference mutates with it and `_revalidate_shard`'s comparison goes
        # vacuously true — a guard that cannot fail, which is the thing this
        # script exists to not be.
        old_entry = copy.deepcopy(entry)
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
                # BOTH SIDES ARE CARRIED. `old_entry` is what apply_plan
                # re-reads the target line against before overwriting it —
                # comparing the PARSED DICT, because `derive_claim_id` hashes
                # only timestamp+domain+content and a line that had drifted in
                # `layer`, `verified_by` or any other field would match on id
                # while being a different record.
                "old_entry": old_entry,
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
            f"  below --min-gap {plan['min_gap_days']}d (set aside): {len(plan['below_threshold'])}"
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
        print(f"      min {gaps[0]:.2f}d  median {gaps[len(gaps) // 2]:.2f}d  max {gaps[-1]:.2f}d")
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
    refused = plan.get("refused_shards") or []
    if refused:
        print()
        print(f"  SHARDS REFUSED (left completely untouched): {len(refused)}")
        print("      a planned line was no longer the entry that was planned — the")
        print("      file moved under the plan, so the WHOLE shard was skipped.")
        for r in refused:
            print(f"      {r['shard']}  ({r['entries']} planned entries)")
            print(f"          {r['reason']}")
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


def _revalidate_shard(shard: Path, rows: list[dict]) -> tuple[str | None, list[str]]:
    """`(None, lines)` when every planned line in `shard` is still the entry
    that was planned; otherwise `(reason, [])` — the reason the WHOLE shard
    must be refused.

    IT RETURNS THE LINES IT VALIDATED, and that is not a convenience. The
    first version read the shard here and `apply_plan` re-read the same file
    before writing it: a shard that changed between those two reads was
    validated in one state and rewritten from another — the cure containing a
    narrow instance of the disease. The window is only one `shutil.copy2`
    wide, and the live-port gate covers the realistic writers, so this was
    never the same class as the plan->apply window the guard exists to close.
    It was free to shut, so it is shut: ONE read is validated, backed up and
    rewritten.

    THE TRADE, STATED RATHER THAN CLAIMED AWAY. Reusing the validated snapshot
    means an append that lands between the read and the `write_text` is dropped
    by the rewrite instead of carried through it. That is a real cost and it is
    the smaller one: the whole-file `write_text` loses concurrent appends in
    every version of this code (which is why the live-port gate exists at all),
    while a SHIFT in the same window under a re-read would overwrite an
    innocent entry with another entry's backdated content — silent corruption
    of an existing record, and the exact failure this guard was written to make
    impossible. Losing a just-appended row is recoverable and loud; corrupting
    a stored one is neither.

    THE GUARD THIS FUNCTION IS. `build_plan` captures a LINE POSITION, and
    `apply_plan` overwrites that position later — with no lock any other writer
    respects (`chronicle_write_lock` is an in-process RLock; the cross-process
    fix lives unmerged on `hardening/cross-process-flock`). Between the two, an
    append shifts nothing but a compaction, an archive pass or a metabolism
    rewrite shifts everything, and the script would then overwrite whatever
    innocent entry had slid into line N with the backdated content of a
    different one. That failure is SILENT and INDISTINGUISHABLE from a correct
    run afterwards, which is precisely the class the whole script is written
    against.

    COMPARISON IS THE PARSED DICT, NOT THE BYTES AND NOT THE CLAIM ID. Bytes
    would refuse a shard over key ordering or whitespace that changes no
    meaning; the claim id would ACCEPT a drifted line, because its preimage is
    only timestamp+domain+content and says nothing about `layer`,
    `verified_by`, `supersedes` or any other field.

    ALL-OR-NOTHING PER SHARD. One bad line means the file has moved under the
    plan, so every other line number in it is suspect too — rewriting the rest
    "because they still match" would be trusting the same stale index that just
    failed.
    """
    try:
        lines = shard.read_text(errors="replace").splitlines()
    except OSError as exc:
        return f"unreadable: {exc}", []
    for row in rows:
        lineno = row["lineno"]
        if lineno < 1 or lineno > len(lines):
            return (
                f"line {lineno} no longer exists (shard now has {len(lines)} lines) "
                f"— the file moved under the plan",
                [],
            )
        try:
            found = json.loads(lines[lineno - 1])
        except json.JSONDecodeError as exc:
            return f"line {lineno} is no longer parseable JSON: {exc}", []
        if found != row["old_entry"]:
            return (
                f"line {lineno} is not the entry that was planned "
                f"(planned content {str(row['old_entry'].get('content'))[:40]!r}, "
                f"found {str(found.get('content'))[:40]!r}) — the file moved under the plan",
                [],
            )
    return None, lines


def apply_plan(root: Path, plan: dict) -> Path:
    """Rewrite matched lines, after backing up every shard it touches.

    FAILS CLOSED PER SHARD. Every planned line is re-read and compared against
    the entry the plan captured BEFORE anything is copied or written; a shard
    that fails is left completely untouched — no backup, no rewrite, no alias
    row, no changelog line — and named in ``plan["refused_shards"]`` for the
    report. Validating after the backup would leave a stray copy asserting a
    rewrite that never happened.

    ONE READ, VALIDATED AND REWRITTEN. `_revalidate_shard` hands back the lines
    it checked and those are the lines written out, so there is no second read
    for the file to change underneath.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = root / "backups" / f"occurred_at_backfill_{stamp}"
    backup_root.mkdir(parents=True, exist_ok=True)

    by_shard: dict[str, list[dict]] = {}
    for row in plan["matched"]:
        by_shard.setdefault(row["shard"], []).append(row)

    refused: list[dict] = []
    written_rows: list[dict] = []
    changelog: list[str] = []
    for shard_str, rows in sorted(by_shard.items()):
        shard = Path(shard_str)
        # REVALIDATE FIRST — before the copy, before the write. The lines it
        # validated are the lines rewritten below; re-reading the file here
        # would validate one snapshot and rewrite a different one.
        reason, lines = _revalidate_shard(shard, rows)
        if reason is not None:
            refused.append({"shard": shard_str, "entries": len(rows), "reason": reason})
            continue

        # BACKUP BEFORE THE REWRITE, mirroring the shard's path under the
        # store so a restore is an unambiguous copy-back.
        rel = shard.relative_to(root)
        dest = backup_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shard, dest)

        by_lineno = {row["lineno"]: row for row in rows}
        out: list[str] = []
        for i, line in enumerate(lines, start=1):
            row = by_lineno.get(i)
            if row is None:
                out.append(line)
                continue
            out.append(json.dumps(row["new_entry"]))
            written_rows.append(row)
            changelog.append(
                f"{shard_str}:{i}  {row['old_timestamp']} -> {row['new_timestamp']}  "
                f"claim {row['old_claim_id'][:16]} -> {row['new_claim_id'][:16]}  "
                f"[{row['queue']} {row['proposal']}]"
            )
        shard.write_text("\n".join(out) + "\n")

    # The counts describe what was WRITTEN, never what was planned — a header
    # claiming the plan's totals over a partially refused run is the same
    # confident-partiality this script exists to make impossible.
    shards_written = len(by_shard) - len(refused)
    changelog = [
        f"# occurred_at / original_timestamp backfill — {stamp}",
        f"# store: {root}",
        f"# entries rewritten: {len(written_rows)} across {shards_written} shard(s)",
        *(
            [f"# shards REFUSED (left untouched): {len(refused)}"]
            + [f"#   {r['shard']}  ({r['entries']} entries)  {r['reason']}" for r in refused]
            if refused
            else []
        ),
        "",
        *changelog,
    ]
    plan["refused_shards"] = refused

    # THE ALIAS FILE. A proposal, not a decision — nothing reads it yet.
    # Iterates the rows actually WRITTEN, not plan["matched"]: an alias row for
    # a line a refused shard never rewrote would point a reader at an id that
    # does not exist, which is the fail-open shape one layer over.
    # NOT EVEN CREATED when nothing was written: an empty alias file left
    # behind by a fully-refused run reads as "the backfill ran and mapped
    # nothing", which is a different claim from "the backfill refused".
    if written_rows:
        aliases = root / "chronicle" / "claim_aliases.jsonl"
        aliases.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        with aliases.open("a") as f:
            for row in written_rows:
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


# Ports a running Sovereign Stack answers on: the SSE daemon and the local
# bridge. Both hold the chronicle open and both write to it.
LIVE_PORTS: tuple[int, ...] = (3434, 8100)
LIVE_PORT_LABELS = ("com.templetwo.sovereign-sse", "com.templetwo.sovereign-bridge")


def answering_ports(ports: tuple[int, ...] = LIVE_PORTS, host: str = "127.0.0.1") -> list[int]:
    """Which of `ports` accept a TCP connection right now.

    A CONNECT, NOT A REQUEST. The question is only "is something holding this
    port", and a connect answers it in milliseconds without an auth token, a
    route or a dependency on either service's HTTP surface staying the shape
    this script remembers.

    Presence is a weak probe in general (SOP #5) — but here the weak direction
    is the safe one: this gate refuses on presence, so a false POSITIVE costs a
    re-run and a false negative is the only outcome that could hurt.
    """
    import socket

    answering: list[int] = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            try:
                if s.connect_ex((host, port)) == 0:
                    answering.append(port)
            except OSError:
                continue
    return answering


def main(argv: list[str] | None = None, *, live_ports: tuple[int, ...] = LIVE_PORTS) -> int:
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

    # THE LIVE-SERVER GATE, and it is checked BEFORE the plan is even built.
    #
    # This script rewrites shards by LINE POSITION and holds no lock any other
    # process respects: `provenance.chronicle_write_lock` is an in-process
    # RLock, so it does not exist as far as the bridge and the SSE daemon are
    # concerned (the real cross-process fix is the unmerged
    # `hardening/cross-process-flock` branch — 35 of 60 writes lost silently in
    # its own race proof). A running server appending to a shard mid-rewrite
    # loses its write to this script's whole-file `write_text`, with no error
    # on either side.
    #
    # Refusal is the only safe answer, and it is DRY-RUN-ONLY-SAFE by design:
    # the dry run reads and is never gated, so the report a human needs in
    # order to decide is always available.
    if args.apply:
        answering = answering_ports(live_ports)
        if answering:
            print(
                "REFUSED: a live server is answering on "
                + ", ".join(f"127.0.0.1:{p}" for p in answering)
                + ".\n"
                "This script rewrites chronicle shards by line position and holds no "
                "cross-process lock, so a concurrent append is lost silently.\n"
                "Stop them first:\n"
                + "\n".join(f"    launchctl bootout gui/$(id -u)/{lbl}" for lbl in LIVE_PORT_LABELS)
                + "\nThe dry run is unaffected — re-run without --apply for the report.",
                file=sys.stderr,
            )
            return 3

    plan = build_plan(root, min_gap_days=args.min_gap)
    refused = False
    if args.apply:
        if not plan["matched"]:
            print("nothing to apply.")
            return 0
        backup = apply_plan(root, plan)
        plan["_applied"] = True
        refused = bool(plan.get("refused_shards"))
        print(f"backups + changelog: {backup}")
    if args.json:
        printable = json.loads(json.dumps(plan, default=str))
        for row in printable.get("matched", []):
            row.pop("new_entry", None)
            row.pop("old_entry", None)
        print(json.dumps(printable, indent=2))
    else:
        print_report(plan, args.verbose)
    # THE REPORT FIRST, THE CODE LAST. Guard 2 is fail-closed on the WRITE —
    # a refused shard is genuinely untouched — and was fail-OPEN on the exit
    # status: this returned 0 whether one shard was refused or all of them,
    # so a caller scripting `--apply && ...` read a fully refused migration as
    # a completed one. Guard 1 three lines up already returns 3 and the
    # missing store returns 2; the vocabulary existed and this path was the
    # one hole in it. Any refusal, partial or total, is 4.
    return 4 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
