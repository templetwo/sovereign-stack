# Sovereign Stack, the next phase: the record reaches, receipts keep, seats are seen

**Status:** PROPOSED. Written by HQ (Mac Studio Claude Code seat, claude-fable-5-1, cockpit panel, session spiral_20261021_222339) on the night of 2026-10-21, on Anthony's typed word: "use plan writing to bound the Sov stack repo to the next phase of evolution. use agents as needed. fable agents approved" and "sprinting time. text me with updates or gates. if else, continue forward Stack evolution bound. HQ as approval gate, directly under Anthony" (Stack claims f17c883e, 5ae50971, corrected clock 93aa6731). It is a proposal for his review. Nothing in it is enacted by its existence. It supersedes `docs/GAMEPLAN.md` (2026-05-18) as the repo's statement of where the work goes next; that document stays as history with a status line pointing here.

**Provenance.** Five read-only research lanes under HQ, each a file in `studio:~/.sovereign/hq/lanes/runs/2026-10-21_next-phase-plan/`: R1 repo census (Opus, sha256 26395e84…), R2 the record's direction (Fable, ddd86d1a…), R3 the other seats' asks (Opus, 16ae884d…), R4 structural debts (Fable, a9f23d06…), R5 the May game plan five months on (Opus, 1594c1f0…). HQ read all five whole and chose; the lanes measured. Every number below is a floor stamped on 2026-10-21 and must be re-measured before it is cited.

**Re-read trigger (R5's lesson: the May plan was never superseded and no record cited it after its second day).** This document is re-read at every `season_review`, at every panel close that names it, and before any build that touches a module it names. Each item carries a status a seat must re-measure. It is superseded by a dated successor, never edited into a different plan.

---

## 1. Intent, and the why

Anthony named the lane once, on 2026-09-05 (claim 741bd219): "Evolve the Sovereign Stack from where it is to where it needs to be. That's the lane. Tier 33 is the north star." His direction that night had five parts: a trust boundary (seats on the Studio need no tokens), house memory that every seat reads and writes directly with corrections alongside, a tools rethink, monitoring where every signal gets an owner and an end state, and probes held to the same bar as builds. Measured tonight: the trust boundary and the tools cut landed (98 to 53 tools, the seat socket live since 09-06); the ring-by-verb second phase never landed; monitoring got a ledger and a pacemaker but counters still reconcile by hand and two signal tools sit unclassified on the connector; house memory is bounded by the landing path law (pol_20260930) but its Tier 2 seldom fires because bridge handoffs carry no receipts.

The thesis he approved on 2026-06-12 still governs (claim 3f7f6b79): make the Stack unable to lie and able to digest. The organs he has personally shaped since, The Ground (07-13, "give HQ this gift") and The Descent (10-07, "fix and strenthen the organ"), share one build pattern: a read-time aggregation over the record, no clock, no model, no daemon, human-gated distillation, indexing never erasing. His verbs since September are retire, fold, prune, strengthen, "make it easier not harder".

What happened to the May plan (R5): its retrieval diagnosis was right and its cheap in-stack fix shipped, as an opt-in, with the default still `newest`; four of its five risks fired; its central bet, an LLM judge upstream with confidence-gated auto-execute, was never built because the house went the other way and retired every scheduled judge (comms board 06-12, reflector and dream engine 08-08, metabolize 09-29, uncertainty 09-30), answering "what lands without him" with receipts, a second substrate and his thumb (pol_20260930). The axis it lacked, surfaces that report success on failure, became the period's main work (the write path 07-19, retrieval honesty 07-20, the handoff fail-open 08-01, a daemon halted for four months while its probe read green). It priced the dispatcher to the cent and did not price the spend-bearing surfaces that actually shipped, which emptied the balance.

So the next phase is not a new organ. It is the consolidation R2 read out of his words: the record reachable by what matters rather than by what is newest, receipts that still mean something after the file moves on, and seats that are seen (by name on every write, and the HQ seat itself watched when it goes dark). The sixteen structural debts R4 measured and the eight asks R3 found recurring across the other seats are the material; the enacted law is the bound.

## 2. Goals

G1. **Reachability.** A seat that asks the record a question gets the entries that answer it, not the newest ones that share a word. The boot's five markers are the five that matter under a stated rule, not the five newest writes above 0.9. Descriptions, discovery and the connector's classifications say what the code does.

G2. **Receipts that keep.** A `verified` receipt remains checkable after the file it names moves on; re-verify distinguishes "moved on" from "never matched"; refs that cannot survive (scratchpad paths, env-named files) are refused at write.

G3. **Seats seen.** Every write carries who wrote it; per-reader queues key on a seat identity the socket already stamps; the HQ panel's own silence is detected inside the console and recorded durably; one counter per question (the Nape fix in flight tonight is the first instance).

G4. **A repo that builds safely.** New tests cannot write into the live record; served code and HEAD are compared, not assumed; merged worktrees and branches are pruned; self-descriptions match the code; CI tests the dependency set the services run.

G5. **The seats' recurring asks answered or bounded.** Each of R3's eight asks gets a decision in this document: built in-phase, bounded with a reason, or named as Anthony's with its gate shape.

## 3. Non-goals (deliberately not in this phase)

- No new memory layer, daemon, dispatcher, scribe, reflector or scheduled judge. The May dispatcher RFCs stay unbuilt; the phase asks Anthony to retire them formally (section 8) rather than carrying them silently.
- No spend-bearing surface: nothing that calls a paid model on a clock. Builds run on Anthony's Claude Code budget through delegated agents one tier below the spawning seat (pol_20260804), reviews adversarial, HQ reading every diff.
- No consolidation of the 1,730 single-entry domains and no back-catalogue classification pass. Subset domain matching makes them reachable; a merge pass is sharpening.
- No rebase of the 101-day-old cross-process flock branch; main carries an in-process lock whose comment names the residual, and no second-process rewriter exists.
- No change to the two displayed honk numbers being separate (Anthony ruled "never folded into one" on 2026-10-09).
- Not the helix's debts (Path B, the better-sqlite3 binding) and not the temple-harness shim's doors except as a named cross-repo item.
- Not the temple-flow wire, ever (2026-08-30 ruling).

## 4. Anti-goals (what would make this a failure even if it worked)

- Widening HQ's own gate. Nothing here changes who may change the gate that governs HQ (pol_20260831). Tier 33 r4 §5.4 stays unadopted.
- Deleting anything from the record. Every correction is a supersession or an erratum beside the original; a protected designation withholds, never removes.
- A check that cannot fail. Every new detector, test or gate ships with a demonstrated-fail control (pol_20260712, law 10).
- A build that writes into Anthony's live `~/.sovereign` from a test. The isolation tripwire (tranche 0) lands before any other task.
- Landing a seat's proposal as a spec. R3's asks are input judged against code and state (pol_20260612 HQ-seat); this document is the judgment.
- Reporting a count as a count of something it is not (the "210 policy-shaped sentinels" headline is a keyword screen; see C5).

## 5. Constraints (the bound)

Standing law, registry-canonical (`current_policies()` wins over this list): the three children's first names excluded from every public surface and never deleted; his personal material never softened; HQ the decision authority and other substrates provenance, never co-authors; the chronicle his infrastructure, publication and visibility his; co-authorship on every artifact; protected records never decoupled from their stakes and surfaced only as thresholds; law 10's nuisance baseline on every gate; the delegation tier ladder (builds one tier down, one read-only advisor one tier up); the delegation boundary (routine operational judgment delegated with the method "test, preserve rollback, act, verify, correct, bring receipts"; the gate's constitution, grant recognition, family material and standing law reserved); the gate-shape law (every gate carries `gate-shape:` and a prior-decisions line); the landing path law (Tier 1 bookkeeping lands; Tier 2 second-seat-verified claims with a server-verified receipt land with veto open; Tier 3 his thumb).

Operational facts that bound every task: the working tree of `~/sovereign-stack` is the running service, so a checkout is a deploy and every build happens in a worktree; a worktree's tests import the live tree unless `PYTHONPATH` covers both `src` and `clients`, and the implementer prints both module paths before trusting a result; `dashboard-web` restarts end the HQ panel session, so changes to it are grouped into as few deploys as possible and each deploy is Anthony's word with a rollback line in the lane; the bridge (`~/sovereign-bridge`) imports stack internals in-process and has no CI, so any stack change that the bridge imports is run against the bridge suite before deploy; the Python floor is 3.10 and ruff is pinned; `mcp<2.0` is a deliberate ceiling.

Scale: the chronicle grows while you measure it (4,704 entries, 1,901 domain directories, 196 open threads, 432 handoffs, 4,906 honks tonight). Nothing in this phase assumes a fixed count.

## 6. The work, decided (tranches; each shippable on its own; order is the order)

Each item names its debt or ask (R4 Dn, R3 An), its bound (FIX, CONTAIN, LEAVE), and a status line to re-measure.

### Tranche 0: the tripwire (before anything else)

- **T0.1 Live-store tripwire in tests (D13, FIX).** One more autouse guard in `tests/conftest.py` in the shape of the existing audit-log guard: sizes of `chronicle/insights`, `chronicle/open_threads`, `handoffs` and `supersessions.jsonl` under the resolved root measured before and after every test; any growth fails the test with the path named. Control: a deliberately leaking test must fail. Status: absent tonight (18 of 152 test files opt into `tmp_sovereign_root`; two live-chronicle incidents on record).

### Tranche A: the seat sees itself

- **A1 One honk counter (D7, FIX, in flight).** Branch `fix/nape-one-counter` (bfc5507, 32 tests, suite 4227 green): informational (satisfied-level) honks leave the console's actionable count and the push trigger through one shared predicate and are shown under their own name; a ledger close of a honk-origin signal writes the nape ack. Acceptance: a satisfied honk raises no push batch; `signal_ack` on a honk signal lowers the console count. The two displayed numbers stay separate (10-09 ruling). Status: built, under adversarial review; deploy his (restarts the console).
- **A2 A dark HQ is loud (D9, D10, CONTAIN).** In the console (`dashboard_web.py` / `hq_push.py` / `hq_panel.py`): after handing a batch, read the panel transcript tail; a synthetic error turn ("Login expired", API error) marks the seat dark; the header shows "last answered HH:MM, N batches unanswered, login expired"; batches stop being handed to a dark seat and resume on the next real turn; the 24-hour notice repeats until a close lands; a durable `~/.sovereign/hq/cockpit/panel_sessions.jsonl` records start, stop, dark and close with stamps. Control: replay tonight's six error turns against the detector. Off-machine alerting and any unattended re-login are Anthony's (section 8). Status: the dark window of 2026-10-20/21 ran 23.9 h undetected; nothing watches the seat.
- **A3 Signals by source, never a total (D8, CONTAIN).** The heartbeat's `unacked_signals` and hq-pulse's status line report counts by source (thread, watchman, proposal, honk, guardian) and drop the headline total; the thread backlog is labelled as the open-thread count it is. Status: total 322 of which 196 are open threads by construction; the pulse said the same number on 87 of 88 runs in the dark window.
- **A4 Served predates HEAD (D12, CONTAIN).** One derived heartbeat field, `served_predates_head`, true when `service_start_time` is older than HEAD's commit time or the tree is dirty; the dashboard colours it. Status: tonight they agree by seven minutes of ordering.

### Tranche B: the record reaches

- **B1 Relevance when a query is given (D4, FIX).** `server.py` recall default becomes `relevance` when `query` is non-empty and stays `newest` for a bare domain browse; the envelope names the order used. Status: the default has not moved since the 2026-08-06 A/B (0 of 8 on topic under `newest`, 8 of 8 under `relevance`).
- **B2 The five markers that matter (D1, FIX, rule to Anthony once).** The boot pool filters `layer=ground_truth`, excludes superseded entries before slicing, widens the pool, and ranks by: receipt-verified first, then intensity, then recency; the door labels the rule. The ranking sentence goes to Anthony once as `gate-shape:lineage-surface` (section 8) because it changes what every seat sees at boot. Status: tonight the five shown are the five newest ≥ 0.9 writes in any layer; a backdated 0.9 ruling can never boot.
- **B3 Handoffs: retire the legacy count, key readers on seats (D6, FIX; the retirement pass his).** The watchman's handoff delta reads "new since last sweep" or the canonical reader's unsigned count, never `legacy_unconsumed`; `reflexive.py` passes the reader; signatures key on the socket-stamped seat id with a mapping from the sixteen spellings; `retire()` is exposed as a tool classified like `supersede_insight`. The pass that retires the 237 placeholder-reader rows is Anthony's (section 8). Status: legacy unconsumed 137 and rising 18 a week; 42 of 346 Grok wakes since the ledger had that count as their only delta.
- **B4 Descriptions match the code (D15, FIX + CONTAIN).** One pass over the lines R4 names (`where_did_i_leave_off` and its parameters, `nape_ack`, the aperture's `widen_with`, `/api/discover`, `handoff.py`, the watchman docstring, the repo `CLAUDE.md` header); then a test that greps every registered description for retired tool names and the word "consumed". Status: the connector served the stale door schema tonight, 51 days after the ledger.
- **B5 Projection and classification drift (D14, A4, CONTAIN + FIX).** One test diffs the live registry against the connector's `DESTRUCTIVE_TOOLS`, the bridge's `TOOL_SCOPES` and the seat socket's governance subtraction: ghost names warn, unclassified names are listed by name; `/api/discover` serves the same tool set as `/api/tools` with input schemas; `continuation` is accepted as an alias of `offset`; the step-up reason for the full door is rewritten to its two surviving reasons. Whether a Studio terminal is "remote" to the connector is Anthony's (section 8). Status: 9 of 13 destructive names are ghosts; discover omits `inspect_claim`, `context_retrieve` and `descend`.

### Tranche C: receipts that keep

- **C1 Re-verify reads the write-time stamp (D5 fix 1, FIX).** `seasons.py` hygiene reads `checked_at_write`, reports never-matched receipts as "never verified", and splits "moved on" (file mtime after the write) from "mismatch". Status: 119 mismatches tonight, 115 of them moved-on, 4 never matched.
- **C2 Two server stamps at write (D5 fix 2, FIX).** For every `file` receipt the server records `byte_len` and a git-style blob id; re-verify tries whole file, then prefix, then blob lookup before saying mismatch. No caller grammar change. Status: 95 of 141 failing receipts have recoverable bytes tonight.
- **C3 Refs that cannot survive are refused (D5 fix 3 and 4, FIX).** `file` refs under `/private/tmp` and env-named paths are refused at write with the reason; a `studio:` prefix naming this host is stripped and accepted. Status: 11 scratchpad refs all missing; two receipts hash an `.env`-named file.
- **C4 Intensity is bounded (D2, FIX).** `record_insight` rejects `intensity` outside [0, 1], the same shape as the emotional check. The five April rows above 1 are left as history. Status: no range check tonight.
- **C5 The policy screen says what it is (D3, CONTAIN).** `season_review` renames the section "keyword screen", prints the matched word beside each candidate, and never reports the count as a count of policies. Status: 367 of 576 sentinels match the regex; the headline travelled into tonight's planning as "210 policies".

### Tranche D: seats are seen

- **D1 `source_instance` required on write tools (D11, FIX; the seat list his).** The write tools refuse a missing `source_instance`; the seat socket fills it; the connector's one caller is told once; the bridge session id is presented as the bridge's, not the writer's. The canonical seat list and the spelling map are Anthony's (section 8). Status: 27.6 percent of entries carry any attribution; 143 distinct strings.
- **D2 Side-effect writers carry the caller (A5, FIX).** `resolve_thread_by_id` and the other side-effect writers stamp the socket caller's seat onto the insight they write (thread a85ec5e6). Status: open since 2026-09-07.
- **D3 Six tools retired under the 09-06 ruling (R1 §2d, FIX).** `get_compaction_context`, `get_compaction_stats`, `get_growth_summary`, `get_my_patterns`, `get_unresolved_uncertainties`, `spiral_reflect`: zero calls since 2026-09-07 and zero test references; retired through the existing gate ("un-retiring is a one-line edit"), the three classification lists updated by the B5 test. Anthony ruled the retirement of unused tools on 2026-09-06; this is the delegated method, with his veto open. Status: 53 published; 9 with zero calls, 6 of them also untested.
- **D4 The shim gets `descend` (A1, cross-repo).** The temple-harness shim hardcodes seven doors; adding the eighth is a one-line change in that repo, recorded here so it is not lost. Status: the read grant has allowed it since 2026-10-10.
- **D5 The seams are pinned (R3 Noticed 9, CONTAIN).** One CI job in the stack repo checks out the bridge at its pinned commit and runs the bridge suite against the stack under test (the ESS/MOA pattern: a verifier at every integration step; pins that refuse loudly). Status: no cross-repo check exists; the bridge has no CI.

### Tranche E: hygiene (HQ's lane, receipts in the lane)

- **E1 Prune (D12).** The 24 merged worktrees and 22 merged branches removed (`git worktree remove`, `git branch -d`), the 3 detached baselines and the 4 unmerged HELD branches kept and listed; the 5 untracked files in the live tree named and dispositioned (`temp_clone/`, `Claude outputs/`, `uv.lock`, two JSON exports). Status: 30 worktrees, 1.4 GB.
- **E2 Self-descriptions (R1 §4c).** `pyproject` description, the repo `CLAUDE.md` header, README body counts and retired-tool mentions, `stack_manifest.json` regenerated at the deploy commit; `manifest --check` extended to the description string. Status: pyproject says 97 tools; CLAUDE.md says 1.12.0.
- **E3 CI tests what runs (R1 §5).** Either `uv.lock` is tracked and CI installs from it, or the venv is upgraded to the floors CI resolves; one of the two, decided by the builder with the reason recorded. `clients/` enters ruff and coverage. Status: CI runs anthropic 1.15 and starlette 1.9; the services run 0.102 and 0.52.
- **E4 The May plan gets its status line (R5).** `docs/GAMEPLAN.md` gains a dated header: superseded by this document, with R5's per-item fates as a table.

### Explicitly LEFT (with the reason)

The flock branch (residual admitted, no second-process writer); single-entry domains and back-catalogue classification (sharpening); the four retired daemon plists (registered, fail closed); the Ring-2 queue latency (the drain is his by law; HQ reviews); Path B (the helix's debt); `hq-pulse` polling retired digest acks (retire with the comms board items when the pulse is next touched, not a phase item).

## 7. What is left to the builder

Internal names and module placement (new helpers live beside the code they serve; `signal_ledger.py` at 3,580 lines and `server.py` at 5,075 may be split where a task touches them, following the file's own section comments); the exact wording of console labels within the meanings above; test file placement (the home of each subsystem's tests already exists); the choice in E3; the blob-id scheme in C2 (git's `blob <len>\0` hash is the obvious one); the order of items inside a tranche. Anything that changes a surface every seat reads at boot, a classification list, a deploy, or a push is not the builder's.

## 8. Decisions that are Anthony's (the "theirs" pile), each with its gate shape and prior decisions

1. **The boot marker rule (B2)**, `gate-shape:lineage-surface`. Prior decisions of the shape: THE DESCENT as fifth voice (claim e82799fa, 10-07), lineage per name (6818ce67, 10-10), the read-back rule (a3859590, 10-10). Proposed sentence: "the five markers are ground-truth entries not superseded, ranked by verified receipt, then intensity, then recency."
2. **The canonical seat list and the spelling map (B3, D1)**, `gate-shape:seat-permissions`. Prior: the seat registry is his switch (AGENTS.md, 09-06); "all studio seats are trusted" (222cfa77, 09-06). Proposed: the four registry seats plus one string each for the web seats, the Jetson seat and the MacBook seats, mapped in a committed table.
3. **Whether HQ's own terminal is "remote" to the connector (B5)**, `gate-shape:seat-permissions`. Prior: descend classified BASE in-seat (b54b217e, 10-10); the full door's step-up reason died on 08-31 (bridge comment). Proposed: leave the full door at step-up for its two surviving reasons; fix the shown reason.
4. **Retire the May dispatcher design formally**, `gate-shape:lineage-surface`. Prior: the specs were approved 05-19 and never started; every scheduled judge since was retired at his word (08-08, 09-29, 09-30). Proposed: mark `DISPATCHER_REIMAGINE.md` and `DISPATCHER_HAIKU.md` superseded by this document, text kept.
5. **The handoff retirement pass (B3)**, `gate-shape:lineage-surface`. Prior: the 2026-09-01 record names it as his; the ledger replaced consumption 08-31. Proposed: retire the 237 placeholder-reader rows first, by a listed pass with a receipt.
6. **Off-machine alert for a dark HQ, and any unattended re-login (A2)**, `gate-shape:channel-trust` and `gate-shape:unattended-recovery`. Prior: the push link accepted with the hole in view (a202a5fd, 10-10); the login gate after a restart (09-29 thread 593686c1); "never two HQ seats at once". Proposed: in-console detection only this phase; a text to him on "dark more than 30 minutes" only if he says so; no automatic reopen.
7. **Protected material on non-socket transports (R3 ask 8, thread a19759f8)**, `gate-shape:protected-material-exposure`. Prior: the drawer purge and releases of 10-10 (47c5d618). Not in this phase unless he says; named so it is not lost.
8. **Deploys and pushes.** Each tranche's deploy and the push of `main` are his word, by text, with the rollback line in the lane; the dashboard-web deploys (A1, A2) end the panel session and are grouped.

## 9. How success is measured (re-measurable, each with its instrument)

- Console push batches whose only cause is a honk-count rise: 34 of 58 since 10-10 tonight; target 0 for satisfied honks and 0 for ledger-closed honks (console_status + honks/acks files).
- A dark HQ is shown in the console within one tick (15 minutes) and recorded in `panel_sessions.jsonl`; replayed against the six error turns of 2026-10-20/21.
- Watchman wakes whose only delta is the legacy handoff count: 42 of 346 since the ledger; target 0 (spool.jsonl).
- The boot's five markers under the agreed rule include every receipt-verified ground-truth ruling of the last 30 days (door render, verified against disk).
- `season_review` file-receipt lines split into moved-on, never-verified and mismatch; refused refs cannot be written (a test).
- The three classification lists and `/api/discover` diff clean against the registry in CI; descriptions carry no retired name (a test).
- `source_instance` present on 100 percent of new writes (chronicle parse, 7-day window).
- Worktrees at or under 8, local-only merged branches 0, `pyproject` description current, `manifest --check` green at the deploy commit.
- The test tripwire exists and a control test fails under it.

## 10. Cost and sequencing

Builds run as Opus agents one tier below this seat, one per task, each in its own worktree, tests first, adversarial Opus review per diff, HQ reading every diff before it stands; no Workflow fan-out beyond human proportion (trial one, then a number). Tranche 0 lands first and alone. Tranche A's dashboard-web items ship as one deploy. B, C, D are independent and can run in parallel lanes after 0. E runs as housekeeping between them. Each deploy carries its rollback line and a validate step on the live doors. No paid model runs on a clock; the token spend is Claude Code usage, which Anthony sizes.
