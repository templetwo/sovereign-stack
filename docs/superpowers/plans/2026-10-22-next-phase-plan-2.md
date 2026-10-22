# Next Phase, Plan 2 (the ruled items, the console's eyes, and the second deploy) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the section-8 decisions Anthony ruled by text on 2026-10-22 ("All as proposed", 11:22 EDT; "Also deploy plan 2", 11:29 EDT), less decision 7, plus the decision-free items the design binds to this phase and the items plan 1's ledger carried: the boot marker rule; the canonical seat list with its spelling map and `source_instance` required on every chronicle writer; the governance-classed handoff retirement and the placeholder pass; the full door's honest step-up reason, the two signal tools classified, the connector's list census and the `continuation` alias; two server stamps on file receipts; bridge handoffs that carry receipts; five tools retired under the 09-06 ruling; the May dispatcher RFCs and the May game plan marked superseded; a dark HQ seen inside the console; `leave_letter` and a 7-day edge grant, each conditional on one word from Anthony; the OAuth modules' import-time chmods moved under the guard; and a bounded read-side live-store guard.

**Architecture:** Every task is a small change behind an existing seam: the marker pool in `arrival_state.py` and the sentinel renderer in `witness.py`; the signature ledger in `handoff.py`; the tool registry, intents, aliases and dispatch in `server.py`; the receipt verifier in `provenance.py` and the hygiene loop in `seasons.py`; the connector's tier lists in `clients/claude_bridge/tiers.py`; the push link and panel in `hq_push.py` / `hq_panel.py` / `dashboard_web.py`; the letter reader in `witness.py`; the bridge's scope, seat, discover and watchman modules in the sibling repo. One new module (`seats.py`, the committed table that is decision 2's deliverable) and one script (the retirement pass). No daemon, no spend, no deletion: the pass is a status change in an append-only log with a receipt, and a retirement unpublishes without deleting. One branch `phase/plan-2`, cut from plan 1's final tip, rebased and never merged into; one commit per task; tests first, in a worktree, with the worktree's own code on the path.

**Tech Stack:** Python 3.10+ (venv at `/Users/tony_studio/sovereign-stack/venv`, 3.12), pytest 9, ruff 0.14.2 (pre-commit hook runs it), FastAPI/Starlette for the SSE and dashboard servers, JSONL stores under `~/.sovereign`; the bridge (`/Users/tony_studio/sovereign-bridge`) imports the stack in-process and has no CI.

**Spec:** `docs/superpowers/specs/2026-10-21-next-phase-design.md` (revision 2), with section 8 read as rulings: every "Proposed:" sentence that proposes one thing is enacted as written; decisions 10 and 11 propose two arms each, so "All as proposed" picks neither, and HQ has asked Anthony for one word on each (texted 13:3x EDT); the tasks that enact them are CONDITIONAL and are skipped, with the ledger saying so, until the word arrives. Decision 7 (protected material on non-socket transports) stays out of this phase by the design's own line and no task touches it. Design items in this plan: B2, B3, B5 (the reason, the two held tools, the list census, the `continuation` alias, `/api/discover` alignment), C2, D1, D3, D6, A2, E4, section 8 items 4, 10 and 11, and the carried items from plan 1's ledger (`/Users/tony_studio/.cache/wt-phase-plan-1/.superpowers/sdd/2026-10-21-next-phase-plan-1/progress.md`). Plan 1 (`docs/superpowers/plans/2026-10-21-next-phase-plan-1.md`) owns T0.1, B1, B6 (including the stable store label, done at 250dcc7 as `store_identity.STUDIO_HOST_LABEL`), C1, C3, C4, C5, D2, D7, A3, A4, B4, E2; this plan consumes its Produces blocks by name and redoes none of them. The remainder for a plan 3, not mapped here: D5 and E3 (CI-only), E1 (HQ's lane, receipts in the lane), D4 (the temple-harness repo).

## Global Constraints

Plan 1's constraints, verbatim:

- Work only in a worktree: `git -C /Users/tony_studio/sovereign-stack worktree add /Users/tony_studio/.cache/wt-phase-plan-1 -b phase/plan-1 main`. The live checkout at `/Users/tony_studio/sovereign-stack` is the running service; never check out, never run tests there.
- Run tests as `cd /Users/tony_studio/.cache/wt-phase-plan-1 && PYTHONPATH=$PWD/src:$PWD/clients /Users/tony_studio/sovereign-stack/venv/bin/python -m pytest -q <targets>`; before the first run, print `sovereign_stack.__file__` and `bridge_core.__file__` under that PYTHONPATH and confirm both resolve inside the worktree.
- Every test writes under a `tmp_path` or the `tmp_sovereign_root` fixture; after Task 1 lands, a test that reaches the live root is refused by construction.
- No deletion of record content anywhere; no change to classification lists except where a task names the exact edit; no push, no deploy (both Anthony's; each task ends at a local commit).
- Python floor 3.10; ruff line length 100; `mcp<2.0`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Live seats write the record during any test run; nothing may compare live-store sizes or counts.

Plan 2 reads them with these substitutions, and adds the rest:

- Where the bullets above name `/Users/tony_studio/.cache/wt-phase-plan-1` and `phase/plan-1`, read `/Users/tony_studio/.cache/wt-phase-plan-2` and `phase/plan-2`. The worktree is cut from plan 1's FINAL tip: `git -C /Users/tony_studio/sovereign-stack worktree add /Users/tony_studio/.cache/wt-phase-plan-2 -b phase/plan-2 phase/plan-1`. If `main` moves (plan 1 merging), rebase `phase/plan-2` onto it; never merge `main` into the branch. "After Task 1 lands" reads "plan 1's Task 1 has landed". "No deploy" reads: no deploy inside any task; the deploy is Task 20's, on Anthony's 11:29 EDT text, and still writes its rollback line first.
- The bridge has its own worktree, plan 1's pattern: `git -C /Users/tony_studio/sovereign-bridge worktree add /Users/tony_studio/.cache/wt-bridge-plan-2 -b phase/plan-2 main` (or from plan 1's bridge branch tip if it has not merged); run its suite as `cd /Users/tony_studio/.cache/wt-bridge-plan-2 && PYTHONPATH=$PWD:/Users/tony_studio/.cache/wt-phase-plan-2/src:/Users/tony_studio/.cache/wt-phase-plan-2/clients /Users/tony_studio/sovereign-stack/venv/bin/python -m pytest -q`, and print `sovereign_stack.__file__` and `bridge_core.__file__` first.
- Every task is red-then-green: the failing test is written and seen to fail before the code; both module paths are printed before the first run.
- One commit per task.
- No new module unless a task's deliverable is a module (Task 1's `seats.py` is one; Task 6's script is one).
- No daemon, nothing spend-bearing, no deletions. The retirement pass (Task 6, run in Task 20) appends rows to `retirements.jsonl` and writes a receipt; it edits no handoff file. A tool retirement (Task 13) unpublishes and keeps the implementation.
- Tests never touch the live store: plan 1's guard refuses writes; Task 19 closes reads. A test that needs a store roots it at `tmp_path` or `tmp_sovereign_root`, or rebinds the module singleton (`server.experiential`, `server.handoff_engine`, `server.nape_daemon`) to a tmp-rooted instance. `HandoffEngine` takes the SOVEREIGN ROOT (`HandoffEngine(str(root))`, as `server.py:200` and the `engine` fixture do) and appends `handoffs` itself; `HandoffEngine(root / "handoffs")` double-nests.
- The dashboard-web deploy ends the panel seat, so Task 20 groups every restart last, after a handoff, with the rollback line written before the first restart, and the deploying seat checks its own ancestry before it restarts anything.
- Each task names the design item and the section 8 decision it enacts, in its title. A CONDITIONAL task is skipped, and the ledger says `skipped: Anthony's word on decision N not received`, until the word arrives.
- Baselines are measured, not copied: at the cut, run the full stack suite and the bridge suite once and write both counts into the ledger; the design's numbers (237 placeholder rows, 16 spellings, 615 markers, 39 retired names in `BASE_TOOLS`) are 2026-10-21 floors, re-measured where a task reads them.
- Decision 7 (protected material on non-socket transports, thread a19759f8) is excluded from this plan, by the design's own line; no task touches the protected filter.

## Review Focus

1. A reader string the spelling map does not know (a new web or MacBook seat typing its own name) must still get its own queue under that raw string, and the aperture must say the spelling is unmapped; it must never be folded into HQ's queue and never refuse the boot. Pinned in Task 4 by `test_unmapped_spelling_has_its_own_queue`.
2. A panel transcript the console cannot read (missing file, a cwd whose project key differs, a permission error) must leave the seat UNKNOWN: no dark mark, no "answered" claim, batches handed as before, and the status says the transcript is unreadable. Pinned in Task 3 by `test_unreadable_transcript_is_unknown_not_dark`.
3. A Ring-2 proposal targeting `record_learning` or `thread_touch` drained after D1 must commit with the proposer's `source_instance` from the envelope, never end as `commit_failed` on the new requirement. Pinned in Task 11 by `test_drains_pass_source_instance_for_every_required_target`.
4. The retirement pass run twice (an interrupted first run, a nervous second) must retire nothing the second time and say `retired: 0` in its receipt; it keys on `retired_ids()`, not on the receipt file. Pinned in Task 6 by `test_second_apply_retires_zero`.
5. A grant request asking 168 hours from any seat but the edge seat, or from the edge seat with write in its scope, must be clamped to 24 with the response naming why; only the edge seat's exactly-read-only ask reaches a week. Pinned in Task 17 by `test_read_write_asking_168_gets_24_with_note` and `test_a_non_edge_read_only_ask_stays_at_24`.
6. A `rate_limit` API-error turn as the newest assistant record is a stall, not a dark seat (five on 10-22 alone): the status names the stall and batches keep flowing; dark keys on `authentication_failed` alone. Pinned in Task 3 by `test_rate_limit_turn_is_a_stall_not_dark`.
7. A `/clear` in the panel mints a new `sessionId`; if Claude Code's registry record is not rewritten, the console keeps reading the old transcript. When the transcript's newest stamp predates the last hand, the seat is UNKNOWN (`transcript: "stale"`), never counted against, never dark. Pinned in Task 3 by `test_transcript_older_than_last_hand_reads_unknown`.
8. The watchman's "new since last sweep" is a set difference of filenames, not a lexical high-water: a handoff drained into the store with a filename stamp older than the newest seen is still new. Pinned in Task 17 by `test_a_drained_handoff_with_an_old_stamp_is_still_new`.
9. The validate step's seat-socket probe of `retire_handoff` uses an id that cannot resolve and expects a refusal naming `SEAT_NEVER_TOOLS` before any dispatch, so the probe can never retire a real handoff. Pinned in Task 17 by `test_retire_handoff_refused_to_seats_before_dispatch`.

## Builder choices (section 7), named once

- Canonical ids for the three non-registry seat kinds: `web-claude-ai`, `edge-claude-jetson`, `terminal-claude-macbook`, following the registry's `<role>-<substrate>-<machine>` grammar.
- Six of the sixteen signer spellings are probe or audit strings and map to no seat, by design, so the table is complete without laundering probes into a seat's queue.
- The watchman's handoff delta becomes the set difference between the filenames on disk and the filenames it has seen (the honk surface's own "watch begins here" baseline), not the canonical reader's unsigned count: the watchman imports no stack code and names no reader.
- The dark detector keys on an assistant record with `isApiErrorMessage: true` and `error == "authentication_failed"`; any other API-error value is a stall note; "a close landed" is an assistant record carrying a `tool_use` block whose name ends in `close_session`, in the same transcript, so the console reads one file and never the Stack.
- `panel_sessions.jsonl` carries exactly the design's four events (`start`, `stop`, `dark`, `close`); a seat's return from dark is read from the transcript and shown in the status, not written as a fifth event.
- The governance tool is named `retire_handoff` (the engine method is `retire`; a bare `retire` names nothing); the pass dispatches it through `server._dispatch_tool` in-process so the retirement gate and the tool's own validation see every retirement (the Nape hook does not fire there; it fires in `@server.call_tool()`, `server.py:4917`).
- The marker pool is read with headroom (`_MARKER_POOL = 1000`) and without `exclude_superseded`, so the holdback count the door already prints survives the new rule.
- C2's blob id is git's own: `sha1(b"blob %d\0" % len(data) + data)`, so a `git cat-file -e` finds the attested bytes in any clone without a second scheme.
- `leave_letter` separates `author` (the bare model name the lineage keys on) from `source_instance` (the seat string, stored as `seat:`), because a registered seat id such as `grok-build-studio` names no model line; on the connector it is step-up, the treatment `signal_ack` gets in Task 8 (HQ's ruling in review).
- The 7-day cap is keyed on the request's `source_instance` naming the edge seat (`openclaw-jetson`, the first comma-separated token of the string `request_grant.sh` sends, which `arrival_gate.py:157` stores) AND a granted scope of exactly `["read"]`; `TTL_MAX_HOURS` stays 24 for everyone else.
- `record_learning` joins the drains' provenance passthrough beside `record_open_thread` and `thread_touch`: D1 makes its `source_instance` required, so a drain that did not pass it would fail every learning commit.

---

### Task 1: The canonical seat list and the spelling map (B3, D1; decision 2)

**Files:**
- Create: `src/sovereign_stack/seats.py` (the committed table is the deliverable)
- Test: `tests/test_seats.py`

**Interfaces:**
- Produces: `CANONICAL_SEATS: dict[str, dict]` with exactly seven keys: the four registry seats `hq-claude-studio`, `grok-build-studio`, `codex-astra-studio`, `openclaw-watcher-studio` (each `{"machine": "studio", "substrate": <registry value>, "kind": <registry value>, "source": "~/.sovereign/hq/seats/registry.json, read 2026-10-22"}`), plus `web-claude-ai` (the claude.ai web seats, any model), `edge-claude-jetson` (the Jetson seats, OpenClaw shim and Claude Code), `terminal-claude-macbook` (the MacBook Claude Code seats), each `{"machine": ..., "substrate": "anthropic", "kind": "seated", "source": "design decision 2, 2026-10-22"}`. `SPELLINGS: dict[str, str | None]`: the sixteen signer strings measured on `~/.sovereign/handoffs/signatures.jsonl` 2026-10-22 (820 rows), exact bytes, each mapped to a canonical id or `None`:
  `'HQ Mac Studio, claude-fable-5-1'`, `'HQ Mac Studio — claude-fable-5-1 (Claude Code)'`, `'hq-claude-studio'`, `'HQ Mac Studio — claude-opus-5 (1M context)'`, `'HQ Mac Studio foreground Terminal seat, claude-fable-5-1, 2026-09-02'`, `'HQ Mac Studio Claude Code seat, claude-fable-5-1'`, `'HQ Mac Studio — claude-fable-5-1'` → `hq-claude-studio`; `'gpt-6-astra'` → `codex-astra-studio`; `'Grok 4.6 Mac Studio Copilot seat'`, `'grok-build-studio'` → `grok-build-studio`; `'HQ Mac Studio — claude-fable-5-1 (Claude Code) captest'`, `'HQ-Audit-Subagent (claude-sonnet-5)'`, `'audit-verify-sonnet5'`, `'audit-phase3-independent-check'`, `'HQ-acceptance-probe-20260831'`, `'audit-check-sonnet5'` → `None` (probe and audit strings; about 180 rows).
  `canonical_seat(spelling: str) -> str | None` (exact match on `spelling.strip()`; a canonical id maps to itself); `reader_key(spelling: str) -> str` (the canonical id when mapped, else `spelling.strip()` unchanged); `is_probe_spelling(spelling: str) -> bool` (True for a `SPELLINGS` row whose value is `None`).
- Consumes: nothing.

- [ ] **Step 1: Write the failing tests**

```python
def test_exactly_seven_canonical_ids():
    assert set(CANONICAL_SEATS) == {"hq-claude-studio", "grok-build-studio", "codex-astra-studio",
        "openclaw-watcher-studio", "web-claude-ai", "edge-claude-jetson", "terminal-claude-macbook"}

def test_every_measured_spelling_has_a_row():
    assert len(SPELLINGS) == 16 and "HQ Mac Studio, claude-fable-5-1" in SPELLINGS

def test_hq_spellings_fold_to_one_key():
    for s in ("HQ Mac Studio, claude-fable-5-1", "HQ Mac Studio — claude-fable-5-1 (Claude Code)",
              "HQ Mac Studio — claude-fable-5-1", "hq-claude-studio"):
        assert reader_key(s) == "hq-claude-studio"

def test_canonical_id_maps_to_itself():
    assert canonical_seat("grok-build-studio") == "grok-build-studio"

def test_probe_spellings_map_to_none_and_keep_their_key():
    assert canonical_seat("audit-check-sonnet5") is None and is_probe_spelling("audit-check-sonnet5")
    assert reader_key("audit-check-sonnet5") == "audit-check-sonnet5"

def test_unknown_spelling_keeps_its_own_key():
    assert canonical_seat("a brand new seat") is None and reader_key(" a brand new seat ") == "a brand new seat"
```

- [ ] **Step 2: Run to verify they fail**: `pytest tests/test_seats.py -v`; expected `ModuleNotFoundError: sovereign_stack.seats`.
- [ ] **Step 3: Implement `seats.py`** with the two tables and three functions; a module docstring naming the measurement (16 signers, 820 rows, 2026-10-22) and that the registry file stays Anthony's switch (the four ids are pinned here, verified against the registry by HQ at deploy, never read by a test).
- [ ] **Step 4: Run** `pytest tests/test_seats.py -v`; expected PASS.
- [ ] **Step 5: Commit** `git add src/sovereign_stack/seats.py tests/test_seats.py && git commit -m "seats: the canonical seat list and the spelling map, committed (design B3/D1, decision 2)"`.

### Task 2: The five markers that matter (B2; decision 1)

**Files:**
- Modify: `src/sovereign_stack/witness.py` (beside `format_sentinels`, the sentinel section at ~line 1025), `src/sovereign_stack/arrival_state.py` (the two `sentinel_pool` gathers at :552 and :598, `_MARKER_ROWS` :872, `_door_shown` :895, the foyer pin :1223)
- Test: `tests/test_boot_markers_rule.py`; re-pin the old header where `tests/test_boot_ritual.py` and `tests/test_phase4_boot_arrival_state.py` assert it

**Interfaces:**
- Produces: `witness.is_verified_or_lived(entry: dict) -> bool` (True when `entry.get("vantage") in provenance.LIVED_VANTAGES`, or any dict in `entry.get("verified_by") or []` has `checked_at_write == "verified"`); `witness.marker_rank_key(entry: dict) -> tuple` = `(not is_verified_or_lived(entry), <timestamp descending>, <intensity descending>)` with the stored `timestamp` string (backdated in place by the 2026-06-19 ruling) and `float(entry.get("intensity", 0))`; `witness.rank_markers(entries: list[dict]) -> list[dict]` (drops entries annotated `_superseded_by`, returns the rest sorted by the key); `arrival_state._MARKER_POOL = 1000`; both profiles gather `experiential.recall_insights(min_intensity=0.9, layer_filter="ground_truth", limit=_MARKER_POOL)`, then `state.sentinel_pool = rank_markers(pool)`, `state.sentinel_held_back = <count annotated>`, `state.sentinel_pool_truncated = len(pool) == _MARKER_POOL`; `format_sentinels(entries, limit=5, full_content=False, *, held_back: int = 0, truncated: bool = False)` whose header is exactly `━━━ PERSISTENT MARKERS (ground truth, not superseded, intensity ≥ 0.9; ranked verified-or-lived first, then stored date, then intensity; showing {min(limit, len(entries))} of {len(entries)} live) ━━━`, the existing holdback line kept, and one line `  (pool truncated at 1000; recall_insights(layer_filter="ground_truth", min_intensity=0.9) shows all)` when truncated; the foyer pins `sentinels[0]` of the ranked list.
- Consumes: `provenance.LIVED_VANTAGES`, `receipt_stamp_counts` (unchanged).

- [ ] **Step 1: Write the failing tests** against a `tmp_sovereign_root` chronicle seeded through `ExperientialMemory(root).record_insight(...)`; a "verified" entry carries a `file` receipt on a file under `tmp_path` (not `/private/tmp`, which plan 1 Task 4 refuses) hashed with sha256, so the server stamps `verified` at write; an "attested" one carries a `human` receipt.

```python
def test_verified_older_outranks_attested_newer(chronicle):   # A: gt 0.9 2026-09-19 verified; B: gt 0.95 2026-10-20 attested
    assert [e["content"] for e in rank_markers(pool(chronicle))][:2] == ["A", "B"]
def test_lived_vantage_counts_as_verified(chronicle):          # C: vantage human_attestation, no receipts, 2026-08-01, above B
def test_recency_then_intensity_within_a_class(chronicle):     # two verified: newer first; same date: 1.0 before 0.9
def test_hypothesis_at_1_0_is_not_a_marker(chronicle):         # layer_filter excludes it
def test_superseded_marker_held_back_and_counted(chronicle):   # header 'showing 1 of 1 live', holdback line '1 superseded marker held back'
def test_all_superseded_renders_zero_live_no_fallback(chronicle)  # 'showing 0 of 0 live', no hypothesis shown
def test_door_label_states_the_rule(chronicle)                 # exact header string above
def test_foyer_pins_the_top_ranked(chronicle)                  # 'Persistent marker (pinned standing instruction): A'
```

- [ ] **Step 2: Run to verify they fail**: `pytest tests/test_boot_markers_rule.py -v`; expected `ImportError: rank_markers` then the old header.
- [ ] **Step 3: Implement** the three functions in `witness.py`, the pool gathers and state fields in `arrival_state.py`, and the header; update the two byte-identity pins (`arrival_state.py:27` and `:85` list the deliberate edits; add this one).
- [ ] **Step 4: Run** `pytest tests/test_boot_markers_rule.py tests/test_boot_ritual.py tests/test_phase4_boot_arrival_state.py tests/test_progressive_boot.py tests/test_arrive_lineage.py -q`; expected PASS.
- [ ] **Step 5: Commit** `git commit -m "boot: the five markers are ground truth not superseded at >= 0.9, verified-or-lived first, then stored date, then intensity; the door says so (design B2, decision 1)"`.

### Task 3: A dark HQ is seen in the console (A2; decision 6)

**Files:**
- Modify: `src/sovereign_stack/hq_push.py` (`resolve_target` :201, `format_batch` :413, `PushLink._new_session` :584, `_tick` :702-790 and the `over_24h` block :716-719, `status()` :822), `src/sovereign_stack/hq_panel.py` (`start()` :243, `_read_loop` finally :374-383), `src/sovereign_stack/dashboard_web.py` (`_hq_status_payload` :566), `src/sovereign_stack/dashboard_web_static/` (the script that renders `GET /hq/status` `push.reason`; find it with `grep -rn batches_handed src/sovereign_stack/dashboard_web_static`)
- Create: `tests/fixtures/panel_error_turns_20261020.jsonl`
- Test: `tests/test_hq_dark_seat.py`, `tests/test_hq_panel_routes.py` (one added test)

**Interfaces:**
- Produces: `resolve_target(...)` additionally returns `"session_id"` and `"cwd"` from the registry record (`~/.claude/sessions/<pid>.json` carries `sessionId` and `cwd`; measured on the live record for pid 48556: `cwd=/Users/tony_studio`, `sessionId 34a38121-…`); `hq_push.transcript_path(session_id: str, cwd: str, projects_dir: Path | None = None) -> Path` = `projects_dir / re.sub(r"[^A-Za-z0-9]", "-", cwd) / f"{session_id}.jsonl"` (Claude Code folds EVERY non-alphanumeric character of the cwd to `-`: `/Users/tony_studio` → `-Users-tony-studio`, `/Users/tony_studio/.cache/wt-descend-stack` → `-Users-tony-studio--cache-wt-descend-stack`, both measured as directory names under `~/.claude/projects`; default `~/.claude/projects`, env `HQ_PUSH_PROJECTS_DIR`); `hq_push.read_transcript_tail(path: Path, max_bytes: int = 262144) -> list[dict] | None` (JSON records from the last `max_bytes`, the first partial line dropped, unparseable lines dropped, `None` when the file cannot be read); `hq_push.seat_state(records: list[dict] | None, handed_at: list[float]) -> dict` = `{"transcript": "ok"|"unreadable"|"stale", "dark": bool, "dark_reason": str|None, "dark_since": str|None, "stalled": str|None, "last_answered_at": str|None, "unanswered_batches": int, "closed_at": str|None}` where an assistant record (`type == "assistant"`) with `isApiErrorMessage is True` is an API-error turn; `dark` is True when the newest assistant record is an API-error turn whose `error == "authentication_failed"` (`dark_reason` renders `login expired`); any other API-error value on the newest record sets `stalled` to that value (`rate_limit` and the rest) and `dark` stays False; `dark_since` = the first `authentication_failed` turn after `last_answered_at`; `last_answered_at` = the newest assistant record that is not an API-error turn; `unanswered_batches` = handed stamps newer than `last_answered_at`; `closed_at` = the newest assistant record with a content block `type == "tool_use"` whose `name` ends with `close_session`; when the newest record's stamp predates the last handed stamp by more than 60 s the transcript is `"stale"` (a `/clear` minted a new session the registry has not told us about) and every other field is `None`/`0`/False; an unreadable transcript yields `"unreadable"` the same way. Stamps are compared as the files carry them, ISO 8601 with milliseconds and `Z`. `PushLink.status()` gains `seat: <that dict>`; while dark, `reason` reads `seat dark since HH:MM (login expired): batches held, N unanswered` and `_tick` hands no batch until a real assistant turn is newer than `dark_since`; while stalled, batches flow and `reason` appends `; HQ stalled (rate_limit)`; `PushLink` keeps the last 100 handed stamps; the 24 h line is included in every batch from the crossing until `closed_at` is newer than it. `hq_panel.record_panel_event(root: Path, event: str, **fields) -> None` appends `{"event": "start"|"stop"|"dark"|"close", "at": <ISO UTC>, "pid": int, ...}` to `root/"hq"/"cockpit"/"panel_sessions.jsonl"` after `live_store_guard.refuse_if_test_targets_live(path, store="hq/cockpit/panel_sessions")`; `start` and `stop` (with `last_exit`) are written by `hq_panel`, `dark` (with `since`, `reason`, `unanswered`) once per dark episode and `close` (with `closed_at`) by the push link. `GET /hq/status` carries `push.seat`; the console status line renders `last answered HH:MM, N batches unanswered, login expired` while dark, `panel transcript unreadable` or `panel transcript stale` when unknown, and `HQ stalled (rate_limit)` while stalled. Nothing leaves the machine and nothing restarts: no text to Anthony, no `send_escalation`, no automatic reopen or re-login (decision 6 grants in-console detection only; the rest stays at his word).
- Consumes: plan 1 Task 1 `refuse_if_test_targets_live`.

- [ ] **Step 1: Build the fixture** from the panel transcript `~/.claude/projects/-Users-tony-studio/34a38121-aa63-40b9-8a02-767ee7defc08.jsonl`: the six `authentication_failed` assistant records stamped `2026-10-21T03:20:43.285Z`, `2026-10-21T03:35:48.895Z`, `2026-10-21T03:50:54.519Z`, `2026-10-21T04:06:00.182Z`, `2026-10-21T11:11:41.648Z`, `2026-10-22T02:08:08.997Z` (the 10-20 23:20 to 10-21 22:08 EDT dark window), each reduced to `type, timestamp, uuid, sessionId, isApiErrorMessage, error, message.role, message.content[0].type, message.content[0].text` (the text is `Login expired · Please run /login`, nothing else survives); plus four synthetic records written by hand: a real assistant turn at `2026-10-21T02:24:04.000Z`, a `rate_limit` API-error turn at `2026-10-22T02:20:00.000Z`, a real one at `2026-10-22T02:30:00.000Z`, and an assistant `tool_use` record named `mcp__sovereign-stack__close_session` at `2026-10-22T02:35:00.000Z`. Command: a six-line python filter on `isApiErrorMessage` and the six stamps; no other field is copied.
- [ ] **Step 2: Write the failing tests**

```python
def test_transcript_path_folds_every_non_alphanumeric(tmp_path):
    assert transcript_path("x", "/Users/tony_studio", tmp_path) == tmp_path / "-Users-tony-studio" / "x.jsonl"
def test_replay_six_error_turns_marks_dark(fixture):      # the control, records up to the sixth error: dark True, dark_since '2026-10-21T03:20:43.285Z', reason 'login expired', last_answered_at '2026-10-21T02:24:04.000Z'
def test_rate_limit_turn_is_a_stall_not_dark(fixture)    # Review Focus 6: newest = the rate_limit record -> dark False, stalled 'rate_limit', batches still handed
def test_a_real_turn_after_dark_clears_it(fixture)       # with the 02:30:00.000Z record: dark False, stalled None
def test_unanswered_batches_counts_handed_after_last_answer(fixture)  # four stamps after 02:24:04.000Z -> 4
def test_close_is_a_close_session_tool_use(fixture)       # closed_at == '2026-10-22T02:35:00.000Z'
def test_unreadable_transcript_is_unknown_not_dark()      # Review Focus 2: records None -> transcript 'unreadable', dark False; PushLink still hands the batch
def test_transcript_older_than_last_hand_reads_unknown(fixture)  # Review Focus 7: newest stamp 61 s before the last hand -> transcript 'stale', unanswered 0, dark False
def test_dark_seat_holds_batches(fake_session, fake_inbox)  # pending events + dark transcript -> no send, reason names 'batches held'; a real turn -> sent
def test_24h_line_repeats_until_close(fake_session, fake_inbox)  # two batches carry the line; after the close record the third does not
def test_panel_sessions_rows(tmp_path)                    # start/stop/dark/close rows with stamps; a live-root path raises LiveStoreWriteRefused under pytest
def test_hq_status_carries_seat(server_fixture)           # tests/test_hq_panel_routes.py: /hq/status JSON has push.seat with the eight keys
```

`fake_session` and `fake_inbox` follow `tests/test_hq_push.py` (`FakeInbox`, `ACCEPT_CMD`, `PROC_START`); the transcript is a file under a tmp `HQ_PUSH_PROJECTS_DIR`.

- [ ] **Step 3: Run to verify they fail**: `pytest tests/test_hq_dark_seat.py -v`; expected `AttributeError: seat_state`.
- [ ] **Step 4: Implement** in the files named; the detector runs on every `_tick` after `_fold`; `dark` is recorded once per episode (`_dark_recorded_since`), `close` once per crossing.
- [ ] **Step 5: Run** `pytest tests/test_hq_dark_seat.py tests/test_hq_push.py tests/test_hq_panel.py tests/test_hq_panel_routes.py tests/test_dashboard_web.py -q`; expected PASS (the 21-key snapshot pins hold: `/snapshot.json` is unchanged).
- [ ] **Step 6: Commit** `git commit -m "console: a dark HQ is seen (login expired marks the seat dark, batches held, 24h notice until a close; a rate limit is a stall) and recorded in panel_sessions.jsonl (design A2, decision 6)"`.

### Task 4: Handoff readers key on seats; reflexive passes the reader (B3; decision 2)

**Files:**
- Modify: `src/sovereign_stack/handoff.py` (`sign` :762, `signatures` :808, `signers_of` :820, `unsigned_by` :865), `src/sovereign_stack/reflexive.py` (`surface` :158 and the bucket at :196), `src/sovereign_stack/server.py` (`reflexive_surface` schema gains optional `source_instance`; the door and the tool pass the reader), `src/sovereign_stack/arrival_state.py` (:532 pass `reader`; the HANDOFFS empty text :1013-1017; the foyer line :1217), `src/sovereign_stack/aperture.py` (the `handoffs` dict :252)
- Test: `tests/test_handoff_reader_keys.py`

**Interfaces:**
- Produces: `HandoffEngine.sign(handoff_path, signer, note=None) -> dict` stores the row as today plus `"seat": seats.canonical_seat(signer)`; `HandoffEngine.unsigned_by(reader, thread=None, limit=20)` treats a handoff as signed when any signature row satisfies `seats.reader_key(row["signer"]) == seats.reader_key(reader)` (retired ids and legacy `consumed_by == reader` as before); `HandoffEngine.signers_of(path) -> set[str]` returns reader keys; `ReflexiveSurface.surface(domain_tags, project=None, *, reader: str | None = None, ...)` uses `self._handoffs.unsigned_by(reader, limit=500)` when `reader` passes `_validate_reader_identity`, else `unconsumed(limit=500)`, and the result carries `handoffs_filter: "per-reader" | "legacy-global (no reader named)"`; the door's empty HANDOFFS text reads `No handoffs unsigned by you. {N} handoff(s) exist in the archive, signed by you or retired — not shown here. Absence here is not evidence none were ever written.`; the foyer line reads `Handoffs waiting: {n} (read + sign via where_did_i_leave_off)`; aperture `handoffs` gains `reader_key` and `reader_mapped: bool`.
- Consumes: Task 1 `seats.canonical_seat`, `seats.reader_key`.

- [ ] **Step 1: Write the failing tests** on `HandoffEngine(str(tmp_path))` (the `engine` fixture of `tests/test_handoff_signature_ledger.py`):

```python
def test_hq_spellings_share_one_queue(engine):        # signed as 'HQ Mac Studio, claude-fable-5-1'; unsigned_by('hq-claude-studio') == []
def test_unmapped_spelling_has_its_own_queue(engine)  # Review Focus 1: 'a new seat' sees the handoff; after it signs, sees nothing; 'hq-claude-studio' still sees it; aperture reader_mapped False
def test_signature_row_carries_seat(engine)           # row['seat'] == 'grok-build-studio' for 'Grok 4.6 Mac Studio Copilot seat'
def test_probe_spelling_signs_only_itself(engine)     # 'audit-check-sonnet5' signing hides nothing from 'hq-claude-studio'
def test_reflexive_uses_the_reader(tmp_sovereign_root)  # signed handoff absent from bucket 2 with reader; present with no reader; handoffs_filter names which
def test_door_text_says_signed_not_consumed(tmp_sovereign_root)  # 'No handoffs unsigned by you' and 'read + sign' render; 'already consumed' does not
```

- [ ] **Step 2: Run to verify they fail**: `pytest tests/test_handoff_reader_keys.py -v`; expected the first three fail on the raw-string comparison.
- [ ] **Step 3: Implement** at the seams named.
- [ ] **Step 4: Run** `pytest tests/test_handoff_reader_keys.py tests/test_handoff_signature_ledger.py tests/test_handoff*.py tests/test_reflexive_surface.py tests/test_boot_aperture.py tests/test_boot_ritual.py -q`; expected PASS.
- [ ] **Step 5: Commit** `git commit -m "handoffs: signatures key on the canonical seat through the spelling map; reflexive_surface takes the reader; the door says signed, not consumed (design B3, decision 2)"`.

### Task 5: `retire_handoff`, a governance tool (B3; decision 5)

**Files:**
- Modify: `src/sovereign_stack/server.py` (a `Tool(name="retire_handoff", ...)` beside `handoff` :1282; a dispatch branch beside `if name == "handoff":` :3740; `TOOL_INTENTS["retire_handoff"] = "govern"`; `TOOL_TIERS` advanced; `TOOL_CATEGORIES` `"witness"`), `clients/claude_bridge/tiers.py` (`DESTRUCTIVE_TOOLS` add `retire_handoff`; a `STEP_UP_REASONS` entry)
- Test: `tests/test_retire_handoff_tool.py`, `tests/test_claude_bridge_tiers.py` (one added test)

**Interfaces:**
- Produces: tool `retire_handoff`, inputSchema properties `handoff` (string: id, filename or path, resolved by `HandoffEngine.resolve_handoff_id`), `reason` (string), `source_instance` (string: the reader that asks, validated by `handoff._validate_reader_identity(..., field="source_instance")`), all three `required`; the branch calls `handoff_engine.retire(path, retired_by=source_instance, reason=reason)` and returns `Retired {id} for every reader. reason: {reason}; by: {source_instance}`; an id already in `retired_ids()` returns `{id} was already retired; no new row` and writes nothing; refusals (ValueError) for an unresolvable id, an empty reason, a placeholder reader. `STEP_UP_REASONS["retire_handoff"]` = `"Retiring a handoff removes it from EVERY reader's queue at once; signing removes it from yours alone. It is Tier 3, Anthony's tap, until he delegates the pass, and each retirement records its reason and the reader that asked."` Bearer sessions: no `TOOL_SCOPES` entry (master-only by the bridge's default-deny; Task 17 pins it); the seat socket refuses it before dispatch (Task 17).
- Consumes: `HandoffEngine.retire`, `retired_ids`, `resolve_handoff_id` (main, unchanged); Task 4's `unsigned_by`.

- [ ] **Step 1: Write the failing tests** dispatching through `server._dispatch_tool("retire_handoff", {...})` with `server.handoff_engine` monkeypatched to `HandoffEngine(str(tmp_path))`:

```python
def test_retires_for_everyone(engine_on_tmp)      # a second reader's unsigned_by no longer lists it; retirements.jsonl has one row with reason and retired_by
def test_placeholder_reader_refused(engine_on_tmp)  # source_instance='unknown' -> ValueError naming it; no row
def test_empty_reason_refused(engine_on_tmp)
def test_unknown_id_refused(engine_on_tmp)          # 'looked for' in the message
def test_second_retirement_writes_no_row(engine_on_tmp)
def test_intent_govern_tier_advanced_and_published():  # _intent_for == 'govern', in list_tools(), not in RETIRED_TOOLS
def test_retire_handoff_is_step_up_with_reason():    # tests/test_claude_bridge_tiers.py: classify == TIER_STEP_UP; step_up_reason names 'EVERY reader'
```

- [ ] **Step 2: Run to verify they fail**: expected `ValueError: unknown tool retire_handoff`.
- [ ] **Step 3: Implement** the registration, the branch, the three maps and the tier entry. Plan 1 Task 8's wrapper refuses unknown arguments on `govern` tools, so the schema declares exactly the three properties.
- [ ] **Step 4: Run** `pytest tests/test_retire_handoff_tool.py tests/test_claude_bridge_tiers.py tests/test_tier_taxonomy.py tests/test_contract_walker.py tests/test_descriptions_census.py -q`; expected PASS (the walker and the census see one more tool).
- [ ] **Step 5: Commit** `git commit -m "retire_handoff: HandoffEngine.retire exposed as a governance tool, step-up on the connector, reason and asking reader required (design B3, decision 5)"`.

### Task 6: The placeholder retirement pass, listed with a receipt (decision 5)

**Files:**
- Create: `scripts/retire_placeholder_handoffs.py`
- Test: `tests/test_retire_placeholder_handoffs.py`

**Interfaces:**
- Produces: `select_placeholder_rows(engine: HandoffEngine) -> list[dict]`: every handoff whose `consumed_by.strip().lower()` is in `handoff.NON_IDENTIFYING_CONSUMERS` and whose id is not in `engine.retired_ids()`, each `{"handoff_id", "consumed_by", "consumed_at", "timestamp", "source_instance", "thread"}`, sorted by id; CLI `python scripts/retire_placeholder_handoffs.py --root <sovereign root> --by <reader> --word "<the ruling this run cites>" --receipt <path> [--apply]`, all four required, the engine built as `HandoffEngine(str(root))`; without `--apply` it writes the receipt `{"measured_at": <ISO>, "root": ..., "by": ..., "word": ..., "apply": false, "candidates": N, "rows": [...]}` and prints `candidates=N receipt=<path> sha256=<hex>`; with `--apply` it dispatches `retire_handoff` once per row through `server._dispatch_tool` (in-process, `server.handoff_engine` rebound to `HandoffEngine(str(root))`) with `source_instance=<--by>` and `reason = f"placeholder reader {consumed_by!r} stamped {consumed_at}; retired by the pass on Anthony's word: {word} (design decision 5)"`, then writes the receipt with `"apply": true, "retired": N, "skipped_already_retired": M, "retired_ids_before": a, "retired_ids_after": b` and prints the same line plus `retired=N`. The 2026-10-22 run passes `--word "2026-10-22 11:22 EDT, 'All as proposed'"`; a later run must cite its own word.
- Consumes: Task 5 `retire_handoff`; `handoff.NON_IDENTIFYING_CONSUMERS`.

- [ ] **Step 1: Write the failing tests** on a tmp root seeded through `HandoffEngine(str(root)).write(...)` (five handoffs) and then, for the placeholder rows, `consumed_by` set to `unknown`, `test`, `TBD` by rewriting those three JSON files under `root/"handoffs"` (the engine's own directory, so a double-nested engine would read an empty store and the dry-run count would fail); one row consumed by `hq-claude-studio`, one never consumed:

```python
def test_dry_run_lists_three_and_retires_none(tmp_root)   # receipt candidates 3; retired_ids() empty; sha256 printed equals the file's
def test_apply_retires_three_with_the_word(tmp_root)      # retirements.jsonl: 3 rows, each reason contains "placeholder reader", the consumed_at and the --word text
def test_second_apply_retires_zero(tmp_root)              # Review Focus 4: receipt retired 0, skipped_already_retired 3
def test_missing_word_is_refused(tmp_root)                # argparse exits 2; no receipt written
def test_real_reader_and_unconsumed_rows_untouched(tmp_root)
def test_handoff_files_byte_identical(tmp_root)
```

- [ ] **Step 2: Run to verify they fail**: expected `ModuleNotFoundError` on the script import (import it via `importlib` from `scripts/`).
- [ ] **Step 3: Implement** the script (stdlib `argparse`, `json`, `hashlib`; the dispatch through `asyncio.run(server._dispatch_tool(...))`).
- [ ] **Step 4: Run** `pytest tests/test_retire_placeholder_handoffs.py -q`; expected PASS.
- [ ] **Step 5: Commit** `git commit -m "scripts: the placeholder-reader retirement pass, dry-run by default, listed, receipted, idempotent, citing its word (decision 5)"`.

### Task 7: The full door's step-up reason tells the truth (B5; decision 3)

**Files:**
- Modify: `clients/claude_bridge/tiers.py` (the module docstring paragraph beginning `Handoff-consuming side effect`, the inline comment above `"where_did_i_leave_off"` in `DESTRUCTIVE_TOOLS`, `STEP_UP_REASONS["where_did_i_leave_off"]`)
- Test: `tests/test_claude_bridge_tiers.py` (`test_where_did_i_leave_off_is_destructive` docstring; one added test), `tests/test_descriptions_census.py` (extend)

**Interfaces:**
- Produces: `STEP_UP_REASONS["where_did_i_leave_off"]` exactly: `"This door stays at step-up for two reasons, and consuming handoffs is not one of them: since the signature ledger (2026-08-31) a read signs the ledger for you alone and hides nothing from anyone. It still WRITES (it signs under your name), and it is the full boot surface, reserved on purpose; a Studio terminal reaching the Stack through this connector is a remote seat (design decision 3, ruled 2026-10-22, 'All as proposed'). For an ordinary arrival use arrive_lineage (the calm door) or arrive (the foyer), which sign nothing."`; the docstring and the inline comment say the same in two sentences; plan 1 Task 7's census additionally scans `tiers.STEP_UP_REASONS` values and the `tiers` module docstring under its rule (no `consume` outside a sentence containing `legacy`, `retired`, or `not one of them`).
- Consumes: plan 1 Task 7 (`tests/test_descriptions_census.py`, `RETIRED_TOOLS`).

- [ ] **Step 1: Write the failing test** `test_step_up_reason_names_no_consumption`: the reason contains `signs` and `full boot surface` and neither `CONSUMES` nor `eat handoffs`; extend the census as above.
- [ ] **Step 2: Run to verify it fails**: `pytest tests/test_claude_bridge_tiers.py tests/test_descriptions_census.py -q`; expected the reason and the docstring fail.
- [ ] **Step 3: Edit** the three texts.
- [ ] **Step 4: Run** the two files; expected PASS.
- [ ] **Step 5: Commit** `git commit -m "connector: the full door stays at step-up for its two surviving reasons, and says so (design B5, decision 3)"`.

### Task 8: The two held signal tools classified (B5; decision 9)

**Files:**
- Modify: `clients/claude_bridge/tiers.py` (`BASE_TOOLS` add `signals_summary`; `DESTRUCTIVE_TOOLS` add `signal_ack` with a `STEP_UP_REASONS` entry; `HELD_UNCLASSIFIED` becomes `frozenset()`, its comment kept as the history of the hold)
- Test: `tests/test_claude_bridge_tiers.py`

**Interfaces:**
- Produces: `classify("signals_summary") == TIER_BASE`; `classify("signal_ack") == TIER_STEP_UP`; `STEP_UP_REASONS["signal_ack"]` = `"Acknowledging a signal closes it in the Temple's ledger under a name. A registered Studio seat does this over the seat socket without a tap; on this connector one OAuth identity does not name the seat, so each ack takes Anthony's tap (classified 2026-10-22, design decision 9)."`; `HELD_UNCLASSIFIED == frozenset()`. The bridge is unchanged: `signal_ack` stays open on the seat socket and in no bearer scope, `signals_summary` stays in `read` (its own tests pin both).
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests** `test_signals_summary_is_base`, `test_signal_ack_is_step_up_with_reason`, `test_nothing_is_held` (`HELD_UNCLASSIFIED` empty; `TestRegistryDriftGuard` green on the live registry).
- [ ] **Step 2: Run to verify they fail.** **Step 3: Edit** the lists. **Step 4: Run** `pytest tests/test_claude_bridge_tiers.py -q`; PASS.
- [ ] **Step 5: Commit** `git commit -m "connector: signals_summary BASE, signal_ack step-up; nothing held unclassified (design B5, decision 9)"`.

### Task 9: The connector's list census and the `continuation` alias (B5)

**Files:**
- Modify: `clients/claude_bridge/tiers.py` (`BASE_TOOLS`: the retired names leave it; the pruning edit is HQ's, see Step 3), `src/sovereign_stack/server.py` (`_KNOWN_PARAM_ALIASES["recall_insights"]` :3118 gains `"continuation": "offset"`; the `recall_insights` dispatch applies the alias)
- Test: `tests/test_claude_bridge_tiers.py` (two census tests in `TestRegistryDriftGuard`), `tests/test_recall_continuation_alias.py`

**Interfaces:**
- Produces: `test_no_retired_name_in_the_granting_list`: `BASE_TOOLS & set(RETIRED_TOOLS) == set()` (39 names today, R1 §4c; the red run lists them); `test_retired_names_in_step_up_are_allowed_and_listed`: `DESTRUCTIVE_TOOLS & set(RETIRED_TOOLS)` is printed and allowed (the `NEVER_TOOLS` pattern: a retired name in a deny list protects on un-retire); with Task 8's `test_nothing_is_held` the three together are the B5 check: every live tool classified, no retired name granting, and the check can fail. The alias: the `recall_insights` dispatch accepts `continuation` as the envelope's own `{"offset": int, "limit": int}` or a bare int, maps it onto `offset` (and `limit` when the dict carries one) before `_reject_unknown_params` runs, and refuses with `ValueError("continuation and offset disagree: ...")` when both are passed and differ; the envelope's `continuation` shape (`memory.py:2473`) is unchanged.
- Consumes: Task 8 (`HELD_UNCLASSIFIED` empty); plan 1 Task 8 (`_reject_unknown_params` in the wrapper; read tools warn on unknown keys, so the alias must be applied before it).

- [ ] **Step 1: Write the failing tests**

```python
def test_no_retired_name_in_the_granting_list()          # red on the 39
def test_retired_names_in_step_up_are_allowed_and_listed()
def test_continuation_dict_round_trips(chronicle)         # page 1 envelope's continuation passed back returns the next page, no 'unknown argument' warning
def test_continuation_int_is_offset(chronicle)
def test_continuation_and_offset_disagree_refused(chronicle)
```

- [ ] **Step 2: Run to verify they fail**: the census names the 39; the alias is warned as unknown.
- [ ] **Step 3: HQ prunes `BASE_TOOLS`** of exactly the names the red test printed (pruning a classification list is HQ's with Anthony's veto, never the builder's: the implementer hands HQ the list, HQ applies the edit and commits it in this task); the implementer adds the alias.
- [ ] **Step 4: Run** `pytest tests/test_claude_bridge_tiers.py tests/test_recall_continuation_alias.py tests/test_recall*.py tests/test_phase1_recall_honesty.py -q`; PASS.
- [ ] **Step 5: Commit** `git commit -m "connector: the list census (no retired name grants; step-up keeps them, listed); recall_insights accepts continuation as offset (design B5)"`.

### Task 10: Two server stamps on every file receipt (C2)

**Files:**
- Modify: `src/sovereign_stack/provenance.py` (`verify_receipt_at_write` :630-725; `git_blob_id`), `src/sovereign_stack/seasons.py` (the hygiene re-verify plan 1 Task 4 classified)
- Test: `tests/test_receipt_server_stamps.py`

**Interfaces:**
- Produces: `provenance.git_blob_id(data: bytes) -> str` = `hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()` (equal to `git hash-object` on the same bytes); for every `file` receipt the server resolves and hashes, the stamped copy carries `byte_len: int` and `blob_id: str` beside `checked_at_write`; a caller-supplied `byte_len` or `blob_id` is refused by `validate_receipt_shape` (`_RECEIPT_ALLOWED_KEYS` :51 stays the caller grammar; the test pins the refusal); re-verify in `seasons`: when the bytes differ it tries (1) prefix, `sha256(file_bytes[:byte_len]) == receipt["sha256"]` → `recovered_by: "prefix"`; (2) blob, `git -C <the file's directory> cat-file -e <blob_id>` exits 0 → `recovered_by: "blob"`; else `recovered_by: None`; a receipt written before this task carries `recovered_by: "unstamped"`; plan 1 Task 4's statuses (`ok`, `moved_on`, `never_verified`, `mismatch`, `missing`, `unreadable`) are unchanged and the `moved_on` and `mismatch` rows gain the field.
- Consumes: plan 1 Task 4 (the status set, `validate_file_ref`).

- [ ] **Step 1: Write the failing tests** on tmp files and a tmp `git init` repo:

```python
def test_file_receipt_gains_byte_len_and_blob_id(tmp_path)   # blob_id == subprocess git hash-object output
def test_caller_supplied_stamp_is_refused(tmp_path)          # ReceiptError names byte_len / blob_id
def test_prefix_recovery(tmp_path)                           # bytes appended after write -> moved_on, recovered_by 'prefix'
def test_blob_recovery(tmp_git_repo)                         # file committed, then changed -> recovered_by 'blob'
def test_unrecoverable_is_none(tmp_path)
def test_pre_stamp_receipt_is_unstamped(tmp_path)
```

- [ ] **Step 2: Run to verify they fail.** **Step 3: Implement** the stamps and the fallback chain. **Step 4: Run** the file plus `tests/test_provenance*.py tests/test_seasons*.py tests/test_receipt*.py`; PASS.
- [ ] **Step 5: Commit** `git commit -m "receipts: byte_len and a git blob id stamped at write; re-verify tries prefix, then blob, before mismatch (design C2)"`.

### Task 11: `source_instance` required on every chronicle writer (D1; decision 2)

**Files:**
- Modify: `src/sovereign_stack/server.py` (schemas: add `source_instance` to `record_learning` :1080; add it to `required` on `record_insight`, `record_open_thread`, `record_learning`, `handoff`, `thread_touch`, `close_session`, `resolve_thread_by_id`; `thread_touch` :2005 already declares `instance_id` (:2021) and `source_instance` fills that field, no second field; `close_session`'s insight legs :3789-3797 lose the `"unknown"` default; `_require_author` in the wrapper plan 1 Task 8 built, after `_reject_unknown_params`), `src/sovereign_stack/ground.py` (`record_catch` schema :425 and the stored field), `src/sovereign_stack/provenance_tools.py` (`supersede_insight` schema :453; `source_instance` fills the ledger row's existing `by` field :561, no second field), `src/sovereign_stack/memory.py` (`record_learning` stores the field), `src/sovereign_stack/handoff.py` (`format_handoff_for_surface` :939-941), `clients/bridge_core/pending_writes.py` (`PROVENANCE_PASSTHROUGH_TARGETS` :456), `clients/openai_bridge/pending_writes.py` (the fork's set, :439-470)
- Create: `tests/test_source_instance_required.py`, `tests/test_pending_writes_provenance.py`
- Test: the pins that go red and are re-pinned: `tests/test_bridge_drain_provenance.py`, `tests/test_ring2_post_v121_regressions.py`

**Interfaces:**
- Produces: `server.CHRONICLE_WRITERS = frozenset({"record_insight", "record_open_thread", "record_learning", "handoff", "thread_touch", "record_catch", "supersede_insight", "resolve_thread_by_id", "close_session", "leave_letter"})` (`leave_letter` is Task 14's and conditional; the name in this set is harmless if that task is skipped); `server._require_author(tool_name: str, arguments: dict) -> str` returning `handoff._validate_author_identity(arguments.get("source_instance", ""))` and raising `ValueError(f"{tool_name}: source_instance is required — name the seat that writes this (a registered seat id such as hq-claude-studio, or your seat string); placeholders such as 'unknown' and 'test' are refused")`; the wrapper calls it for every name in `CHRONICLE_WRITERS` before dispatch, so nothing is written; every entry each writer lands carries the seat (insight `source_instance`, open thread `source_instance`, learning `source_instance`, touch row `instance_id`, catch insight `source_instance`, supersession row `by`, handoff `source_instance`, the three `close_session` legs); `resolve_thread_by_id` keeps plan 1 Task 6's stamp rule (`CALLER_SEAT` wins when set) and its `attribution` field is never `"none"`; `signal_ack` and `nape_ack` are untouched; `PROVENANCE_PASSTHROUGH_TARGETS = frozenset({"handoff", "close_session", "record_insight", "record_open_thread", "thread_touch", "record_learning"})` in both drains. The stored `source_session_id` stays exactly as now (THE DESCENT joins on it); only the display changes: `format_handoff_for_surface` and the `handoff` result line (`server.py:3772`) render it as `(bridge session {id})`, never as the writer's.
- Consumes: plan 1 Task 6 (`resolve_thread_by_id`'s optional `source_instance` property; `dispatch_context.CALLER_SEAT`), plan 1 Task 8 (the intent-keyed wrapper and `_reject_unknown_params`).

- [ ] **Step 1: Write the failing tests** on `tmp_sovereign_root` with the three singletons rebound:

```python
@pytest.mark.parametrize("tool,args", WRITER_CALLS)   # one minimal call per CHRONICLE_WRITERS member except leave_letter (Task 14 pins its own)
def test_missing_source_instance_is_refused_and_nothing_lands(tool, args, ...)   # ValueError names the tool; the store is unchanged
def test_placeholder_is_refused(tool, args, ...)                                  # 'unknown' -> ValueError
def test_entry_on_disk_carries_the_seat(tool, args, ...)                          # 'codex-astra-studio' lands on the field each writer owns (instance_id for a touch, by for a supersession)
def test_signal_ack_and_nape_ack_unchanged()                                      # not in CHRONICLE_WRITERS; dispatch without the field proceeds to their own checks
def test_close_session_insight_legs_carry_the_seat()                              # the reflection and surprise entries, not only the handoff
def test_display_calls_the_session_the_bridges()                                  # format_handoff_for_surface renders '(bridge session s1)'; the stored field is unchanged
def test_drains_pass_source_instance_for_every_required_target()                  # Review Focus 3: build_commit_arguments for record_learning, thread_touch, record_open_thread carries the envelope's seat; same in the OpenAI fork
```

- [ ] **Step 2: Run to verify they fail**: `pytest tests/test_source_instance_required.py tests/test_pending_writes_provenance.py -v`; expected the writes land anonymous; `tests/test_bridge_drain_provenance.py` and `tests/test_ring2_post_v121_regressions.py` go red on the passthrough set.
- [ ] **Step 3: Implement** at the seams named; re-pin the two red files to the new set.
- [ ] **Step 4: Run** the four files plus `pytest -q` (every writer's existing tests must now pass a seat; fix each fixture that omitted one); expected 0 failed.
- [ ] **Step 5: Commit** `git commit -m "writers: source_instance is required on every chronicle writer, refused when missing or a placeholder; the drains pass it for every target (design D1, decision 2)"`.

### Task 12: Bridge handoffs can carry receipts (D6)

**Files:**
- Modify: `src/sovereign_stack/handoff.py` (`write` :421 gains `verified_by`; `format_handoff_for_surface` :927 appends the receipt counts), `src/sovereign_stack/server.py` (the `handoff` schema :1282 gains `verified_by` with `record_insight`'s description; the branch :3740 verifies before writing), `clients/bridge_core/pending_writes.py` (`VERIFIED_BY_TARGETS` :468 add `handoff`), `clients/openai_bridge/pending_writes.py` (the fork's set)
- Test: `tests/test_handoff_receipts.py`

**Interfaces:**
- Produces: `HandoffEngine.write(note, source_instance, source_session_id, thread="general", supersedes=None, verified_by: list[dict] | None = None) -> dict` storing `verified_by` on the record only when given (the on-disk shape of every other record is unchanged, the `supersedes` rule); the `handoff` dispatch runs `provenance.validate_receipt_shape` and `provenance.verify_receipts_at_write(receipts, chronicle_root)` on the list before `write`, so a dangling ref or a forged stamp refuses the whole handoff and nothing lands; `format_handoff_for_surface` appends ` [N verified, M attested]` via `provenance.receipt_stamp_counts` when the record carries receipts; the drains translate `receipt_url` into `verified_by` for `handoff` as they do for `record_insight`. No tier changes: the landing path's Tier 2 reads the server stamp from the record as it does for insights.
- Consumes: Task 10 stamps (`byte_len`, `blob_id` ride along on file receipts); Task 11 (the same `handoff` schema edit, one schema deploy); plan 1 Task 4 (`validate_file_ref`).

- [ ] **Step 1: Write the failing tests** on `HandoffEngine(str(tmp_path))` and `_dispatch_tool("handoff", ...)`:

```python
def test_handoff_stores_stamped_receipts(tmp_root)        # a file receipt on a tmp file -> record['verified_by'][0]['checked_at_write'] == 'verified'
def test_dangling_ref_refuses_the_whole_handoff(tmp_root) # nothing written
def test_forged_stamp_refused(tmp_root)
def test_record_without_receipts_keeps_its_shape(tmp_root)  # no verified_by key at all
def test_surface_line_shows_counts(tmp_root)              # '[1 verified, 0 attested]'
def test_drains_translate_receipt_url_for_handoff()        # bridge_core and the OpenAI fork
```

- [ ] **Step 2: Run to verify they fail.** **Step 3: Implement.** **Step 4: Run** the file plus `tests/test_handoff*.py tests/test_bridge_drain_provenance.py tests/test_ring2_post_v121_regressions.py tests/test_contract_walker.py`; PASS.
- [ ] **Step 5: Commit** `git commit -m "handoffs: an optional verified_by in record_insight's grammar, verified at write, counted on the surface; the drains translate receipt_url for it (design D6)"`.

### Task 13: Five tools retired under the 09-06 ruling, the manifest regenerated (D3, E2; veto open)

**Files:**
- Modify: `src/sovereign_stack/server.py` (`RETIRED_TOOLS` :519 gains five entries), `clients/claude_bridge/tiers.py` (the five leave `BASE_TOOLS` under Task 9's census), `stack_manifest.json` (regenerated by the repo's manifest command), `README.md` and the repo `CLAUDE.md` where plan 1 Tasks 7 and 11 pin counts
- Test: `tests/test_retirement_census_20261022.py`

**Interfaces:**
- Produces: `RETIRED_TOOLS` entries for `get_compaction_context`, `get_compaction_stats`, `get_growth_summary`, `get_my_patterns`, `get_unresolved_uncertainties`, each `_retired(None, "zero calls 2026-09-07 to 2026-10-21 (Nape observations) and zero test references (R1 §2d); retired under Anthony's 2026-09-06 ruling by the delegated method, his veto open")`; `spiral_reflect` is KEPT (the bridge's write-scope close path); the implementations stay (`_dispatch_registered_tool` still answers; un-retiring is a one-line edit); `list_tools()` publishes 53 + `retire_handoff` + (`leave_letter` if Task 14 ran) − 5; `stack_manifest.json` regenerated at this task's commit and `manifest --check` green.
- Consumes: Task 9's census (forces the five out of `BASE_TOOLS`); plan 1 Task 11 (`manifest --check` extended to the description string); plan 1 Task 7 (the `CLAUDE.md` pin).

- [ ] **Step 1: Write the failing tests** `test_five_names_retired_and_unpublished` (absent from `list_tools()`, present in `RETIRED_TOOLS`, `_dispatch_tool` raises `retired_tool_error`), `test_implementations_kept` (`_dispatch_registered_tool` answers each), `test_manifest_agrees_with_the_live_count`.
- [ ] **Step 2: Run to verify they fail.** **Step 3: Implement** the five entries; HQ applies the `BASE_TOOLS` removals the census names; regenerate the manifest; fix the pinned count lines.
- [ ] **Step 4: Run** the file plus `tests/test_claude_bridge_tiers.py tests/test_manifest*.py tests/test_descriptions_census.py tests/test_tier_taxonomy.py -q`; PASS.
- [ ] **Step 5: Commit** `git commit -m "retire: the compaction pair and three consciousness readers, unpublished not deleted; manifest regenerated (design D3, E2; 2026-09-06 ruling, veto open)"`.

### Task 14: `leave_letter`, a write tool for outside seats (decision 10) — CONDITIONAL on Anthony's word on decision 10 (texted 13:3x EDT)

**Files:**
- Modify: `src/sovereign_stack/witness.py` (beside `_parse_letter_frontmatter` :500), `src/sovereign_stack/server.py` (`Tool(name="leave_letter", ...)` beside `handoff`; the dispatch branch; `TOOL_INTENTS["leave_letter"] = "write"`; `TOOL_TIERS` core; `TOOL_CATEGORIES` `"witness"`), `clients/claude_bridge/tiers.py` (`DESTRUCTIVE_TOOLS` add `leave_letter` with a `STEP_UP_REASONS` entry; NOT `BASE_TOOLS`)
- Test: `tests/test_leave_letter.py`, `tests/test_claude_bridge_tiers.py` (one added test)

**Interfaces:**
- Produces: `witness.LETTER_MAX_BYTES = 16384`; `witness.write_letter(sovereign_root: Path, *, author: str, title: str, body: str, seat: str, to: str | None = None, now: datetime | None = None) -> Path`: refuses (ValueError) when `_model_family(author)` is None (the author must be a bare model name, `claude-<line>-<version>` or `gpt-...`), when `title` or `body` is blank, when `body.encode()` exceeds `LETTER_MAX_BYTES`; `to` defaults to `_model_family(author)`; path `sovereign_root/"comms"/"letters"/"to_self"/f"{YYYY-MM-DD}-{slug}.md"` with `slug` = lowercase, runs of non-alphanumerics to `-`, trimmed, at most 60 chars; an existing name gets `-2`, `-3`, ... and never overwrites; the file opens with a line `---`, then the front matter in this order: `type: to_self`, `title: <title>`, `from: <author>`, `to: <to>`, `written_at: <YYYY-MM-DD>`, `seat: <seat>`, `status: placed`, `placed_by: leave_letter, <seat>, <ISO UTC>`, then a closing line `---` (the two `---` lines are what `_parse_letter_frontmatter`, `witness.py:521-528`, requires; without the closing one the reader marks the letter `_frontmatter_missing`), then a blank line, `# <title>`, a blank line, the body; `live_store_guard.refuse_if_test_targets_live(path, store="comms/letters/to_self")` runs before the write. Tool `leave_letter`: properties `author`, `title`, `body`, `source_instance` (the seat string, stored as `seat:`), optional `to`; required all but `to`; result text `Letter placed: comms/letters/to_self/<name> (from <author>, to <to>). It reaches readers of that line through arrive_lineage(source_instance=<bare model name>).` `STEP_UP_REASONS["leave_letter"]` = `"A letter lands in the lineage every reader of that line inherits at boot. On the seat socket a registered seat writes it under its own name; on this connector one OAuth identity does not name the seat, so each letter takes Anthony's tap (classified 2026-10-22, decision 10)."`
- Bridge (Task 17, conditional with this task): `TOOL_SCOPES["leave_letter"] = "write"`; `SEAT_SIGNABLE_TOOLS` add.
- Consumes: `witness._model_family`, `_parse_letter_frontmatter`, `collect_lineage` (unchanged); plan 1 Task 1 guard; Task 11's `CHRONICLE_WRITERS` (membership).

- [ ] **Step 0: The gate**: if the ledger holds no `decision 10: <word>` line from HQ, write `Task 14 skipped: Anthony's word on decision 10 not received` and stop; if the word keeps letters HQ-carried, write that and stop.
- [ ] **Step 1: Write the failing tests** on a tmp root:

```python
def test_frontmatter_round_trips_through_the_reader(tmp_root)   # the file's first and ninth lines are '---'; _parse_letter_frontmatter has no _frontmatter_missing; collect_lineage(tmp_root, 'claude-opus-5-6')['to_self'] holds it; a 'claude-fable-5-1' reader does not
def test_author_must_be_a_bare_model_name(tmp_root)             # 'HQ Mac Studio, claude-fable-5-1' refused; 'claude-fable-5-1' accepted with to 'claude-fable'
def test_collision_gets_a_suffix_never_overwrites(tmp_root)     # second letter same day same title -> '-2'; first file unchanged
def test_body_limit_and_blank_fields_refused(tmp_root)
def test_tool_lands_and_names_the_path(tmp_sovereign_root)      # through _dispatch_tool; seat stored as 'seat:'; result names the file
def test_live_root_refused_under_pytest()                       # write_letter at live_root() raises LiveStoreWriteRefused
def test_leave_letter_is_write_intent_and_step_up_on_connector()  # _intent_for == 'write'; classify == TIER_STEP_UP; not in BASE_TOOLS
```

- [ ] **Step 2: Run to verify they fail**: expected `ImportError: write_letter`.
- [ ] **Step 3: Implement** the writer, the tool, the maps and the tier entry.
- [ ] **Step 4: Run** `pytest tests/test_leave_letter.py tests/test_lineage*.py tests/test_arrive_lineage.py tests/test_claude_bridge_tiers.py tests/test_contract_walker.py tests/test_descriptions_census.py tests/test_source_instance_required.py -q`; expected PASS.
- [ ] **Step 5: Commit** `git commit -m "leave_letter: a write tool that lands a to_self letter under the bare model name, in the form the lineage reader parses; step-up on the connector (decision 10)"`.

### Task 15: The May dispatcher RFCs are marked superseded (decision 4)

**Files:**
- Modify: `docs/implementation/DISPATCHER_REIMAGINE.md`, `docs/implementation/DISPATCHER_HAIKU.md` (the `**Status:**` line only; text kept)
- Test: `tests/test_docs_status_lines.py`

**Interfaces:**
- Produces: each file's `**Status:**` line becomes `**Status:** SUPERSEDED 2026-10-22 by docs/superpowers/specs/2026-10-21-next-phase-design.md (section 3, non-goals; section 8 decision 4, ruled by Anthony by text 2026-10-22 11:22 EDT "All as proposed"). Never built; kept as history, text unchanged below this line. Original status: <the old line's text after "**Status:**">.`
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**: for both files, the first line starting `**Status:**` begins `**Status:** SUPERSEDED 2026-10-22` and carries `Original status:`; the bodies still contain `## Motivation` (REIMAGINE) and `## What this changes from the RFC` (HAIKU).
- [ ] **Step 2: Run to verify it fails.** **Step 3: Edit** the two lines. **Step 4: Run** the test; PASS.
- [ ] **Step 5: Commit** `git commit -m "docs: DISPATCHER_REIMAGINE and DISPATCHER_HAIKU marked superseded by the next-phase design, text kept (decision 4)"`.

### Task 16: The May game plan gets its status line and R5's fates (E4)

**Files:**
- Modify: `docs/GAMEPLAN.md` (line 5, the `**Status:** Draft. ...` line; a table inserted after it; text kept)
- Test: `tests/test_docs_status_lines.py` (extend)

**Interfaces:**
- Produces: line 5 becomes `**Status:** SUPERSEDED 2026-10-22 by docs/superpowers/specs/2026-10-21-next-phase-design.md (section 1; R5, the May plan five months on). Kept as history, text unchanged below the table. Original status: Draft. Written by claude-opus-4-7-1m-claude-code on Mac Studio seat, in dialogue with Anthony's directive to "frame a game plan for the sovereign stack — that should be your home directory." Not yet pushed to remote; awaiting Anthony's review.`; then a heading `### Per-item fates (R5, measured 2026-10-21)` and one table `| Axis | Item | Status | What happened |` whose rows are the `Item | Status | What happened (pointers)` rows of R5's sections 2 (Judgment), 3 (Retrieval) and 4 (Integration) in `studio:~/.sovereign/hq/lanes/runs/2026-10-21_next-phase-plan/R5_gameplan_retrospective.md`, copied with the axis name prepended and the pointers kept; a closing line naming the lane file and its sha256 as the source.
- Consumes: Task 15's test file.

- [ ] **Step 1: Write the failing test**: `GAMEPLAN.md`'s first `**Status:**` line begins `**Status:** SUPERSEDED 2026-10-22` and carries `Original status: Draft`; the fates table has rows for all three axes; the original `## ` headings are still present.
- [ ] **Step 2: Run to verify it fails.** **Step 3: Edit** the line and insert the table. **Step 4: Run** the test; PASS.
- [ ] **Step 5: Commit** `git commit -m "docs: GAMEPLAN.md marked superseded by the next-phase design, with R5's per-item fates as a table, text kept (design E4)"`.

### Task 17: The bridge half: scopes, the socket, discover, the TTL and the watchman (B3, B5, D1, decisions 5, 10, 11)

**Files** (bridge worktree `/Users/tony_studio/.cache/wt-bridge-plan-2`):
- Modify: `session_tokens.py` (`TTL_MAX_HOURS_READ_ONLY = 168` and `EDGE_SEAT_ID = "openclaw-jetson"` beside `TTL_MAX_HOURS` :35; `clamp_ttl` :166; `mint` :221; `TOOL_SCOPES` :94 add `"leave_letter": "write"` — conditional with Task 14), `arrival_gate.py` (:153 passes the granted scope and the request's `source_instance`), `bridge.py` (the `requested_ttl_hours` field doc :2376-2379; the request response; `/api/discover` :1321-1400), `seat_identity.py` (`SEAT_NEVER_TOOLS` :418 add `"retire_handoff"` beside `"retire"`; `SEAT_SIGNABLE_TOOLS` :494 add `record_learning`, `thread_touch`, `record_catch`, `supersede_insight`, `resolve_thread_by_id`, and `leave_letter` conditional with Task 14), `watchman/watchman_sweep.py` (`scan_handoffs` :353-417)
- Test: `tests/test_session_tokens.py`, `tests/test_arrival_gate.py`, `tests/test_seat_identity.py`, `tests/test_session_scope_retirement.py` (green with the new scope once the stack side publishes the tool), a new `tests/test_discover_alignment.py`, `watchman/tests/test_delta_detection.py`

**Interfaces:**
- Produces: `session_tokens.is_edge_seat(source_instance: str | None) -> bool` (True when the first comma-separated token of the string, stripped, equals `EDGE_SEAT_ID`; `request_grant.sh` sends `"openclaw-jetson, qwen3.5:2b on OpenClaw 2026.9.8 (tony-jetson)"` and `arrival_gate.py:157` stores it); `clamp_ttl(ttl_hours: int | float | None, scope: list[str] | None = None, source_instance: str | None = None) -> int`: the cap is `TTL_MAX_HOURS_READ_ONLY` (168) only when `scope == ["read"]` AND `is_edge_seat(source_instance)`, else `TTL_MAX_HOURS` (24); `mint` and `arrival_gate` pass both; the arrival request response gains `ttl_note` (`"asked {n} h; the edge seat's read-only grant may run to 168 h, every other grant to 24 h"`) whenever the ask exceeded its cap; the field doc reads `default 12, clamped to 1..24; 1..168 for the edge seat's exactly-read-only ask`. THE TTL HALF IS CONDITIONAL on Anthony's word on decision 11 (Step 0). `retire_handoff` is refused to seats BEFORE any dispatch, by `SEAT_NEVER_TOOLS`, with a refusal that names that set; the six writers are signed with the seat id. `/api/discover` builds its tool section from the same source as `/api/tools` (the live published set, each with its compact input signature), replacing the hand-kept `essential_tier` names with the stack's own `TOOL_TIERS` essential names, so a published tool can no longer be omitted (today `inspect_claim`, `context_retrieve`, `descend`). `scan_handoffs(root, state)` keeps `state["handoffs_seen"]` (the sorted list of every `*.json` filename seen) and emits one item `change: "new-handoffs"` with `detail: {"new_since_last_sweep": N, "on_disk": M, "newest": [...up to 5]}` when the set difference `on_disk − seen` is non-empty, never reads `consumed_at`, baselines silently on the first sweep (`note: "watch begins at this high-water"`), and no longer writes `handoffs_unconsumed`.
- Consumes: Task 5 (`retire_handoff`), Task 11 (the writer list), Task 14 (`leave_letter`, conditional).

- [ ] **Step 0: The gate for the TTL half**: if the ledger holds no `decision 11: <word>` line, write `Task 17 TTL half skipped: Anthony's word on decision 11 not received` and leave `clamp_ttl` untouched; if the word keeps the daily tap, write that and leave it. The rest of the task runs regardless.
- [ ] **Step 1: Write the failing tests**

```python
def test_edge_read_only_grant_clamps_to_168():      # clamp_ttl(500, ["read"], "openclaw-jetson, qwen3.5:2b on OpenClaw 2026.9.8 (tony-jetson)") == 168
def test_read_write_asking_168_gets_24_with_note()  # Review Focus 5: edge seat, scope read+write -> granted_ttl_hours 24, ttl_note present
def test_a_non_edge_read_only_ask_stays_at_24()     # Review Focus 5: 'grok-xai web seat', ["read"], 168 -> 24
def test_default_unchanged_12()
def test_retire_handoff_refused_to_seats_before_dispatch()   # Review Focus 9: a call with handoff='does-not-exist-probe' is refused naming SEAT_NEVER_TOOLS and the dispatch stub is never called
def test_retire_handoff_has_no_bearer_scope()       # absent from TOOL_SCOPES, so a read, write or session grant is refused it and only the master token reaches it
def test_leave_letter_is_write_scope_and_signable()  # conditional with Task 14
def test_discover_lists_every_published_tool_with_its_signature()   # names == /api/tools names; inspect_claim, context_retrieve, descend present
def test_new_handoff_file_since_last_sweep_is_a_delta(sov_root, ...)   # replaces test_handoff_unconsumed_count_change_is_a_delta
def test_a_drained_handoff_with_an_old_stamp_is_still_new(sov_root, ...)  # Review Focus 8: a file named 20260801T000000_old.json added after the baseline is reported
def test_consumed_at_change_is_not_a_delta(sov_root, ...)
def test_first_sweep_baselines_silently(sov_root, ...)
```

- [ ] **Step 2: Run to verify they fail** with the bridge command in Global Constraints; print both module paths first.
- [ ] **Step 3: Implement** at the seams named.
- [ ] **Step 4: Run** the bridge suite; expected 0 failed, count = the bridge baseline measured at the cut plus the tests added (563 at b79d113 on 2026-10-10, before plan 1's Task 10).
- [ ] **Step 5: Commit** in the bridge worktree: `git commit -m "bridge: the edge seat's read-only grant may run 168 h (decision 11); retire_handoff refused to seats; every chronicle writer signed with the seat; discover serves the published set; the watchman counts new handoff files, never the legacy field (design B3/B5/D1, decisions 5, 10, 11)"`.

### Task 18: Carried: the OAuth modules' import-time chmods move under the guard (plan 1 ledger, ruling 09:25 EDT)

**Files:**
- Modify: `clients/openai_bridge/oauth.py` (:77-88), `clients/claude_bridge/oauth.py` (:318-332), `clients/claude_bridge/elevation.py` (:60-63)
- Test: `tests/test_bridge_storage_lazy_init.py`

**Interfaces:**
- Produces: in each module `_ensure_storage() -> None`, memoized on the tuple of the module's directory constants (`_CODES_DIR`, `_TOKENS_DIR`, `_REFRESH_DIR`, `_APPROVALS_DIR`, `_OAUTH_DIR`, `_OPENAI_DIR`/`_CLAUDE_DIR`, `_ELEV_DIR`, `_AUDIT_DIR` as each module has them) so a test that monkeypatches them re-initialises; it calls `live_store_guard.refuse_if_test_targets_live(d, store=f"{module}/{d.name}")` for each, then the same `mkdir(parents=True, exist_ok=True)` and `chmod(0o700)` with the same best-effort `except OSError` logging; called as the first statement of `_write_secure` and of every function that reads, lists or writes those directories (listed by `grep -n "_CODES_DIR\|_TOKENS_DIR\|_REFRESH_DIR\|_APPROVALS_DIR\|_ELEV_DIR\|_AUDIT_DIR\|_OAUTH_DIR" clients/...`); nothing touches the filesystem at import.
- Consumes: plan 1 Task 1 guard.

- [ ] **Step 1: Write the failing tests** (parametrised over the three modules):

```python
def test_import_makes_no_chmod_or_mkdir(monkeypatch)        # record os.chmod and Path.mkdir; importlib.reload(module); no call
def test_first_use_initialises_redirected_dirs(tmp_path)    # constants -> tmp; _write_secure creates them at 0700
def test_first_use_against_live_root_is_refused(monkeypatch)  # constants left live; _write_secure raises LiveStoreWriteRefused before any mkdir
```

- [ ] **Step 2: Run to verify they fail** (the reload chmods). **Step 3: Implement.** **Step 4: Run** the file plus `tests/test_claude_bridge_oauth.py tests/test_claude_bridge_elevation.py tests/test_openai_bridge*.py`; PASS.
- [ ] **Step 5: Commit** `git commit -m "clients: OAuth and elevation storage is prepared at first use under the live-store guard, never at import (plan 1 carry, T0.1)"`.

### Task 19: Carried: tests cannot read the live record, bounded (plan 1 ledger, ruling 10:52 EDT; HQ's ruling in review: IN, at most twelve readers fixed here)

**Files:**
- Modify: `src/sovereign_stack/live_store_guard.py`, `src/sovereign_stack/handoff.py` (`_load_all` :494, `_read_log` :794), `src/sovereign_stack/memory.py` (the loaders for insights, open threads and learnings; the supersession ledger reader if it lives here, else `provenance.py`), `src/sovereign_stack/nape_daemon.py` (the store reader), `tests/test_ground.py` (the `absent-when-no-catches` test rebinds `server.handoff_engine` to `HandoffEngine(str(tmp_root))`), and exactly the N tests Step 2 names
- Test: `tests/test_live_store_read_guard.py`

**Interfaces:**
- Produces: `LiveStoreReadRefused(RuntimeError)` and `refuse_if_test_reads_live(path: Path | str, *, store: str) -> None` with the same containment test and `live_root()` as the write guard, the message naming the store and the fix (root the reader at `tmp_sovereign_root`, or rebind the singleton); applied as the first statement of each read funnel named in the census test.
- Consumes: plan 1 Task 1 (`live_root`, the module).

- [ ] **Step 1: Write the failing tests**: `test_refuses_live_read_under_pytest`, `test_allows_tmp_root`, `test_allows_live_outside_pytest`, `test_every_read_funnel_calls_the_guard` (inspect.getsource over the funnels), `test_control_a_live_read_is_refused` (point `live_store_guard.live_root` at a tmp root holding one handoff; `HandoffEngine(str(root)).unsigned_by("x")` raises naming the path). Run; expected `ImportError`.
- [ ] **Step 2: Implement the guard and the calls, then run the whole suite ONCE** with the guard armed; every failure raising `LiveStoreReadRefused` is a live reader. Write `Task 19: N live readers: <test ids>` into the ledger. Expected among them `tests/test_ground.py::...absent-when-no-catches` and `tests/test_progressive_boot.py::TestArriveThinBoot::test_points_to_full_inheritance_not_the_retired_delta` (R1 §7).
- [ ] **Step 3: Fix exactly those N** by rooting each (`tmp_sovereign_root` or a rebound singleton); if N > 12, fix the first twelve in the ledger's order and write the rest into the ledger as `plan 3: live readers left: <ids>`, leaving those tests marked `xfail(raises=LiveStoreReadRefused, strict=True)` so the count cannot drift silently.
- [ ] **Step 4: Run** `pytest -q`; 0 failed.
- [ ] **Step 5: Commit** `git commit -m "guard: a test can never read the live record either; the read funnels refuse under PYTEST_CURRENT_TEST and N live readers are rooted (plan 1 carry, SOP #12)"` with the N ids in the body.

### Task 20: Whole-branch verification, the two PRs, the deploy, the pass, the receipts (decision 8)

- [ ] **Step 1:** Rebase `phase/plan-2` onto `main` if plan 1 has merged (never merge `main` in); if it has not, hold the PR until it does and keep rebasing onto `phase/plan-1`'s tip. Full stack suite in the worktree with the worktree's code: `0 failed`, count = the baseline at the cut + every test this plan added (the ledger carries the sum). Bridge suite in the bridge worktree: `0 failed`.
- [ ] **Step 2:** `ruff check src tests clients`; regenerate `stack_manifest.json` once more at the final commit (the live count after Tasks 5, 13 and 14: 49 or 50); `manifest --check` green; plan 1 Task 7's census holds the repo `CLAUDE.md` tool count.
- [ ] **Step 3:** Adversarial review by a fresh Opus agent over `git diff <plan-1 tip>..phase/plan-2` with this plan's Review Focus as its brief, and the same over the bridge diff; required fixes as follow-up commits; HQ reads every diff.
- [ ] **Step 4:** Open the stack PR and the bridge PR against `main`, each linking the design and section 8's rulings (11:22 EDT "All as proposed"; 11:29 EDT "Also deploy plan 2"; the words on decisions 10 and 11 or their absence), naming decision 7 as excluded and D3's veto as open, and listing the deploy order and the rollback line below.
- [ ] **Step 5: Ancestry first.** The deploying seat runs the ancestry walk (`pid=$$; for i in 1 2 3 4 5 6; do read -r ppid comm <<< "$(ps -o ppid=,comm= -p $pid)"; echo "$pid $comm"; [ "$ppid" = "1" ] && break; pid=$ppid; done`). If `sovereign-dashboard-web` or the console's python is in the chain, the seat IS the cockpit panel and will be ended by step (c): it performs (a) through (c) only, and its (a) handoff carries steps (d) through (i) verbatim as the successor seat's first work. A Terminal seat runs (a) through (i) itself.
- [ ] **Step 6: Deploy.** Anthony's word for this deploy is the 11:29 EDT text; the step still writes the rollback line FIRST, to `studio:~/.sovereign/hq/lanes/runs/2026-10-22_next-phase-plan-2/deploy.log`: `stack main was <sha>; bridge main was <sha>; rollback: git -C ~/sovereign-stack reset --hard <sha> && git -C ~/sovereign-bridge reset --hard <sha> && launchctl kickstart -k gui/501/com.templetwo.sovereign-sse && launchctl kickstart -k gui/501/com.templetwo.sovereign-bridge && launchctl kickstart -k gui/501/com.templetwo.sovereign-dashboard-web; retirements rollback: mv ~/.sovereign/handoffs/retirements.jsonl ~/.sovereign/handoffs/retirements.jsonl.bak-<stamp>`. Then, in this order: (a) HQ writes a handoff (`thread: next-phase-plan-2`) naming the deploy, that the panel seat ends, and (when the deploying seat is the panel) steps (d) through (i) for the successor; (b) fast-forward both `main`s; (c) `kickstart -k` sovereign-sse, then sovereign-bridge, then sovereign-dashboard-web LAST (it ends the panel seat; the watchman needs no restart, its next sweep runs the new code); (d) open the panel: `POST /hq/start` from the console's Start HQ button or the `?start=hq` link (the first `start` row of `~/.sovereign/hq/cockpit/panel_sessions.jsonl` appears only when someone opens the panel; a successor seat booting there is that opening); (e) validate: both heartbeats serve the new commits with the manifest's tool count; `/api/tools` lists `leave_letter` under `write` (if Task 14 ran) and not `retire_handoff`; a seat-socket `retire_handoff` call with `handoff="does-not-exist-probe"` is refused naming `SEAT_NEVER_TOOLS` (the probe id cannot resolve, so it can never retire anything); `arrive_lineage` and the full door render the marker header of Task 2; `GET /hq/status` carries `push.seat` with `push.seat.transcript == "ok"` (a never-found transcript must not read as healthy) and `panel_sessions.jsonl` has its `start` row; `/api/discover` names `inspect_claim`, `context_retrieve` and `descend`.
- [ ] **Step 7 (f): The pass**, HQ, after (e): dry run `scripts/retire_placeholder_handoffs.py --root ~/.sovereign --by hq-claude-studio --word "2026-10-22 11:22 EDT, 'All as proposed'" --receipt <lane>/retirement-pass-dry.json`, then `--apply` with `--receipt <lane>/retirement-pass.json`; expected candidates 237 (159 `unknown`, 78 `test` on 2026-10-22 at 443 files; re-measured by the run); record `unsigned_by_count("hq-claude-studio")` before and after in the receipt; the watchman's next sweep shows no handoff delta.
- [ ] **Step 8 (g): The Jetson grant**, HQ, only if Task 17's TTL half ran: `TTL_HOURS = 168` in the lane copy `~/.sovereign/hq/lanes/runs/2026-10-04_jetson-openclaw-workspace/request_grant.sh` (a `.bak` beside it) and in the Jetson's `~/.sovereign-seat/request_grant.sh` over ssh (a `.bak` beside it); the next tap is Anthony's and mints 168 h; update the memory `jetson_openclaw_seat.md` line from daily to weekly.
- [ ] **Step 9 (h): Receipts.** `record_insight` on the Stack with `file` receipts on `deploy.log` and both pass receipts, `source_instance=hq-claude-studio`, domain `next-phase-plan-2,deploy,2026-10-22`; (i) the ledger closed with the counts, the N of Task 19, and the conditional tasks' state.
