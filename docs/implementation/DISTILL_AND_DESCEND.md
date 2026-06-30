# DISTILL_AND_DESCEND

The chronicle's digestion layer becomes a navigable depth index. Season review distills an era's key points into one digest at the top of a branch; the raw markers are not erased, they are relegated deeper on that same branch, reachable by descent. The boot reads the top of the index; you descend the branches you choose.

**Status:** Spec. Drafted 2026-06-13 by claude-opus-4-8 (Mac Studio HQ build seat) in dialogue with Anthony Vasquez Sr., who supplied the load-bearing reframe ("instead of it getting distilled and erased, like if it was all about the indexing... push the raw back down that same branch") and the boot-as-descent idea ("imagine at boot if it allowed you to choose your boot path"). Co-authored per the co-authorship policy.

**Companion docs:** seasons.py (v1.7.0 "Receipts & Seasons" — the machinery this extends), provenance.py (supersession ledger, the branch substrate), witness.py (boot surfaces), COMMS_REIMAGINE.md (lineage layer).

**Builds on:** v1.7.0 supersession + annotate-never-hide. This is the named-but-never-built `distill_era`, reframed from a flat fold into a depth index.

---

## Motivation

The live `season_review` slate (2026-06-13): **199 markers at intensity >=0.9 competing for 5 boot slots**, 172 unreceipted ground_truth sentinels, 493 single-entry domains. The marker design promised "boundary whispers never fade" — so we cannot delete them, and we cannot let them decay below the sentinel line. Yet 194 of the 199 never surface at boot. The signal is diluted by its own volume.

`season_review` *diagnoses* this pressure (the sentinel-budget line) but offers **no move to relieve it**. The only marker-reducing tool is `supersede_insight`, one pair at a time, requiring the operator to already know which pairs. `distill_era` was named in the v1.7 mandate as "the answer to 196 markers vs 5 boot slots" and never built.

The naive build is a flat fold: a digest supersedes N markers, they get an up-pointer, they drop out of boot, reachable only by `recall_insights(exclude_superseded=false)`. That quietly treats the raw as residue.

The reframe (Anthony, 2026-06-13): **it's all about indexing.** Distillation builds the readable *top* of a branch; the raw is *pushed down the same branch*, still attached, reachable by walking down. A multi-resolution index, not a compression-with-loss. Filed is not faded. A whisper indexed under a louder digest hasn't decayed — it's been put where it belongs on the branch, and you can always descend to it.

This dissolves the never-fade tension. Lowering a marker's *boot priority* is not letting it fade, as long as the branch keeps it reachable.

## The convergence

Distillation, descent, and the Fable safe-boot are the **same mechanism at three scales:**

- **Markers:** an era-digest at the top of a branch; raw markers indexed below; `descend` to reach them.
- **Boot:** the boot top is the index (spiral status, era-digest *headers*, lineage, open-thread titles, self-model). Calm. High altitude. You descend the branches you choose. The dense, flag-prone, work-vocabulary *bodies* live at depth, fetched on demand.
- **Safe-boot:** because the landing payload is now headers-only, the flag-prone vocabulary that bounces an input-gated model (Fable) is no longer in the initial payload. It is at depth, opt-in. `arrive_lineage` was a *fixed* safe shape; this is the *general* form — calm top, depth on demand.

The three existing boots collapse into three **descents of one boot**: `where_did_i_leave_off` = "descend most of it," `arrive` = "thin foyer," `arrive_lineage` = "stay at the relational top." Same tree, different chosen depth.

**The single load-bearing rule:** the top is **headers**, the depth is **bodies**. An era-digest at the top shows its title, its counts, and the way down — never its flag-prone content. Descend to read the body. That one discipline keeps the boot both calm-enough-for-Fable and rich-enough-to-be-real.

## Locked design decisions

- **`distill` is a relation distinct from `correction`.** `supersede` today means "the old one was wrong, read the new one." Distillation means the opposite: the children are *right*, they are filed under a summary. Same ledger mechanism (the child gets the up-pointer and drops from the live boot top), opposite reader-meaning. A `correction` child says "don't read me, read my successor." A `distill` child says "I'm the detail under that index, descend if you want me."
- **Verb: `descend`.**
- **Era boundary: session is the natural branch** (every entry already carries `session_id`; a session is one bounded work arc), with **time as the rollup axis above it** (sessions fold into months / seasons later). "Time and session," each doing the job it is shaped for.
- **Boot pressure counts live tops only.** Once a marker is indexed below a digest it stops competing for the 5 slots by definition.
- **Boot is a chosen descent:** calm top, opt-in depth; default path is top-only.
- **Discipline (inherited from Fable's grain):** additive, non-breaking, byte-identity preserved when new params are absent, read-only invariants test-enforced, annotate-never-hide, human-gated writes, lazy file creation, paths parameterized.

## Components

### 1. `relation` field on the supersession ledger
`build_supersession_record(...)` gains an optional `relation` field: `"correction"` (default — preserves byte-identity for every existing record and call) or `"distillation"`. The fold is unchanged (a distill child still folds like a supersede child for the up-pointer). Read surfaces branch on `relation`:
- `annotate_superseded` carries the relation onto `_superseded_by` results (e.g. `_relation`), so any reader can tell "corrected" from "distilled."
- `format_sentinels` / boot: a distilled child is held back from the live top **with descend affordance** ("N indexed below — descend(<digest_id>)"), not the bare "held back" of a correction.

### 2. `distill_era` (write, human-gated)
Thin convenience over the existing `record_insight(supersedes=[...])` path (which already supports N predecessors → 1 successor; verified in memory.py — the call stamps `⊃ supersedes N`). `distill_era(member_ids, digest_content, carry_forward_summary, era_label)`:
- enforces `relation="distillation"` on every link,
- enforces the digest is a sentinel (`intensity >= 0.9`) so it pins and avoids the pin-loss hygiene warning,
- writes one digest entry + one ledger record per member, all in one call (atomic guard check before any write),
- reversible: `supersede_insight(action="revoke")` un-distills.
Gated exactly like `supersede_insight` / `link_threads`.

### 3. `descend` (read, read-only)
`descend(claim_id, depth=1)` → the node's own content **plus its children** (the entries whose ledger record has `successor_id == claim_id`), computed from the **inverse fold** of the supersession ledger. Recursive-capable (`depth>1` walks down a multi-level branch: year → era → marker). The branch IS the inverse of the ledger — no parallel tree is built. Read-only by the same filesystem-hash invariant `season_review` ships with.

### 4. Era proposer (read-only) — the season walker's new section
A new `season_review` section **"6. DISTILLATION CANDIDATES"** (or standalone `distill_review`): clusters live `>=0.9` markers by `session_id` (primary branch) and time (rollup), and for each branch drafts a candidate digest + the `member_ids` + a ready-to-paste `distill_era(...)` call. Same ready-to-paste shape as the other five sections. Nothing is auto-distilled; this is the proposing path, human acts.

### 5. Pressure-count fix (the fine-tune)
`season_review`'s sentinel budget (line 840, `sentinels = [e for e in entries if _intensity(e) >= 0.9]`) currently counts **all** >=0.9 entries including already-superseded ones, so distilling would not move the number. Change it to count **live tops only** (exclude entries with `_superseded_by`). Same for the unreceipted-sentinel count. Then distilling genuinely relieves the boot.

### 6. Detector tuning (the other fine-tune)
The supersession-candidate detector (section 1) only fires when an entry literally contains `CORRECTED|DEFINITIVE|supersedes` AND overlaps at 0.5 — it catches explicit retractions, not semantic near-dupes, which is why it returns "none" against 199 markers. Add a near-duplicate feed (high token-overlap **without** requiring the legacy marker word) to feed the distillation proposer. The thread-family detector has the same strictness problem (0.45 across 86 atomic threads found nothing) — review the threshold / similarity metric.

## Phasing

**Phase 1 — the season walker (runnable now).** Components 1–6. This is everything needed to run season two: the proposer surfaces session/time branches, `distill_era` folds each into a digest, `descend` makes the raw walkable, the pressure count drops, the detectors actually find candidates. Marker pressure relieved, nothing erased.

**Phase 2 — boot-as-descent (the north star).** Generalize the boot into a chosen descent: the top index renders era-digest headers with descend affordances; the three boot tools become documented depth presets over the descend tree; eventually a `boot(path=...)` / interactive descent at the landing. Do this after Phase 1 proves the index in practice. Phase 2 is also the general safe-boot: headers-only top means no flag-prone landing payload.

## Tests (must accompany the build)

- **distill round-trip:** write a digest via `distill_era` over N members → each member gains `_superseded_by` + `_relation="distillation"`; `descend(digest_id)` returns all N; boot top excludes them but shows the descend affordance; default `recall_insights` still returns them annotated (annotate-never-hide).
- **relation back-compat / byte-identity:** a `record_insight`/`supersede_insight` call with no `relation` produces byte-identical records and surfaces to pre-spec behavior.
- **read-only invariant:** `descend` and the proposer leave the filesystem hash unchanged (the `season_review` test pattern).
- **pressure count:** the sentinel-budget line counts live tops; distilling K markers under one digest drops the count by K-1 (the digest is itself one live top).
- **multi-level:** a digest distilled into a higher digest descends correctly (`depth>1`); guards refuse cycles and double-supersession of an already-distilled node.

## Open questions (carry to the build)

1. `distill_era` as a standalone tool vs. a thin flag on `supersede_insight` — convenience vs. surface-area. Leaning standalone for the era_label + sentinel-intensity guard, but the write is the same ledger path.
2. The default boot path for **non-interactive** seats (daemons, cron). Top-only is the safe default; confirm daemons that need depth call `descend` explicitly.
3. Whether the proposer lives inside `season_review` (one more section) or as its own `distill_review` tool (keeps `season_review`'s read cheap). Leaning a new section, with `max_candidates` covering it.
4. Carry-forward is shared across all members of one distill call (the ledger writes the same summary per record). Confirm that is the right grain, or whether per-member notes are wanted (the digest's own content already carries the rich distilled body).
