# Next Phase, Plan 2 (the ruled items, the console's eyes, and the second deploy) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the eleven section-8 decisions Anthony ruled by text on 2026-10-22 ("All as proposed", 11:22 EDT; "Also deploy plan 2", 11:29 EDT), less decision 7, plus the three items plan 1's ledger carried: the boot marker rule, the canonical seat list with its spelling map and `source_instance` required on every chronicle writer, the governance-classed handoff retirement and the placeholder pass, the full door's honest step-up reason and the two signal tools classified, the May dispatcher RFCs marked superseded, a dark HQ seen inside the console, a `leave_letter` tool, a 7-day read grant for the Jetson seat, the OAuth modules' import-time chmods moved under the guard, a read-side live-store guard, and the empty-search label if plan 1 did not already fix it.

**Architecture:** Every task is a small change behind an existing seam: the marker pool in `arrival_state.py` and the sentinel renderer in `witness.py`; the signature ledger in `handoff.py`; the tool registry, intents and dispatch in `server.py`; the connector's tier lists in `clients/claude_bridge/tiers.py`; the push link and panel in `hq_push.py` / `hq_panel.py` / `dashboard_web.py`; the letter reader in `witness.py`; the bridge's scope, seat and watchman modules in the sibling repo. One new module (`seats.py`, the committed table that is decision 2's deliverable) and one script (the retirement pass). No daemon, no spend, no deletion: the pass is a status change in an append-only log with a receipt. One branch `phase/plan-2`, cut from plan 1's final tip, rebased and never merged into; one commit per task; tests first, in a worktree, with the worktree's own code on the path.

**Tech Stack:** Python 3.10+ (venv at `/Users/tony_studio/sovereign-stack/venv`, 3.12), pytest 9, ruff 0.14.2 (pre-commit hook runs it), FastAPI/Starlette for the SSE and dashboard servers, JSONL stores under `~/.sovereign`; the bridge (`/Users/tony_studio/sovereign-bridge`) imports the stack in-process and has no CI.

**Spec:** `docs/superpowers/specs/2026-10-21-next-phase-design.md` (revision 2), with section 8 read as rulings: every "Proposed:" sentence is enacted as written, except decision 7 (protected material on non-socket transports), which the design itself keeps out of this phase and this plan does not touch. Design items in this plan: B2, B3, B5 (the reason and the two held tools), D1, A2, section 8 items 4, 10 and 11, and the three carried items from plan 1's ledger (`/Users/tony_studio/.cache/wt-phase-plan-1/.superpowers/sdd/2026-10-21-next-phase-plan-1/progress.md`). Plan 1 (`docs/superpowers/plans/2026-10-21-next-phase-plan-1.md`) owns T0.1, B1, B6, C1, C3, C4, C5, D2, D7, A3, A4, B4, E2; this plan consumes its Produces blocks by name and redoes none of them. Left for a plan 3, not mapped here because HQ's scope for plan 2 did not name them: C2, D3, D4, D5, D6, E1, E3, E4, and B5's list census, `/api/discover` alignment and the `continuation` alias.

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

- Where the bullets above name `/Users/tony_studio/.cache/wt-phase-plan-1` and `phase/plan-1`, read `/Users/tony_studio/.cache/wt-phase-plan-2` and `phase/plan-2`. The worktree is cut from plan 1's FINAL tip: `git -C /Users/tony_studio/sovereign-stack worktree add /Users/tony_studio/.cache/wt-phase-plan-2 -b phase/plan-2 phase/plan-1`. If `main` moves (plan 1 merging), rebase `phase/plan-2` onto it; never merge `main` into the branch. "After Task 1 lands" reads "plan 1's Task 1 has landed". "No deploy" reads: no deploy inside any task; the deploy is Task 16's, on Anthony's 11:29 EDT text, and still writes its rollback line first.
- The bridge has its own worktree, plan 1's pattern: `git -C /Users/tony_studio/sovereign-bridge worktree add /Users/tony_studio/.cache/wt-bridge-plan-2 -b phase/plan-2 main` (or from plan 1's bridge branch tip if it has not merged); run its suite as `cd /Users/tony_studio/.cache/wt-bridge-plan-2 && PYTHONPATH=$PWD:/Users/tony_studio/.cache/wt-phase-plan-2/src:/Users/tony_studio/.cache/wt-phase-plan-2/clients /Users/tony_studio/sovereign-stack/venv/bin/python -m pytest -q`, and print `sovereign_stack.__file__` and `bridge_core.__file__` first.
- Every task is red-then-green: the failing test is written and seen to fail before the code; both module paths are printed before the first run.
- One commit per task.
- No new module unless a task's deliverable is a module (Task 1's `seats.py` is one; Task 6's script is one).
- No daemon, nothing spend-bearing, no deletions. The retirement pass (Task 6, run in Task 16) appends rows to `retirements.jsonl` and writes a receipt; it edits no handoff file.
- Tests never touch the live store: plan 1's guard refuses writes; Task 14 closes reads. A test that needs a store roots it at `tmp_path` or `tmp_sovereign_root`, or rebinds the module singleton (`server.experiential`, `server.handoff_engine`, `server.nape_daemon`) to a tmp-rooted instance.
- The dashboard-web deploy ends the panel seat, so Task 16 groups every restart last, after a handoff, with the rollback line written before the first restart.
- Each task names the design item and the section 8 decision it enacts, in its title.
- Baselines are measured, not copied: at the cut, run the full stack suite and the bridge suite once and write both counts into the ledger; the design's numbers (237 placeholder rows, 16 spellings, 615 markers) are 2026-10-21 floors, re-measured where a task reads them.
- Decision 7 (protected material on non-socket transports, thread a19759f8) is excluded from this plan, by the design's own line; no task touches the protected filter.

## Review Focus

1. A reader string the spelling map does not know (a new web or MacBook seat typing its own name) must still get its own queue under that raw string, and the aperture must say the spelling is unmapped; it must never be folded into HQ's queue and never refuse the boot. Pinned in Task 4 by `test_unmapped_spelling_has_its_own_queue`.
2. A panel transcript the console cannot read (missing file, a cwd whose project key differs, a permission error) must leave the seat UNKNOWN: no dark mark, no "answered" claim, batches handed as before, and the status says the transcript is unreadable. Pinned in Task 3 by `test_unreadable_transcript_is_unknown_not_dark`.
3. A Ring-2 proposal targeting `record_learning` or `thread_touch` drained after D1 must commit with the proposer's `source_instance` from the envelope, never end as `commit_failed` on the new requirement. Pinned in Task 9 by `test_drains_pass_source_instance_for_every_required_target`.
4. The retirement pass run twice (an interrupted first run, a nervous second) must retire nothing the second time and say `retired: 0` in its receipt; it keys on `retired_ids()`, not on the receipt file. Pinned in Task 6 by `test_second_apply_retires_zero`.
5. A grant request asking 168 hours with scope `["read", "write"]` must be clamped to 24 with the response naming why; only an exactly-read-only ask reaches a week. Pinned in Task 12 by `test_read_write_asking_168_gets_24_with_note`.

## Builder choices (section 7), named once

- Canonical ids for the three non-registry seat kinds: `web-claude-ai`, `edge-claude-jetson`, `terminal-claude-macbook`, following the registry's `<role>-<substrate>-<machine>` grammar.
- Six of the sixteen signer spellings are probe or audit strings and map to no seat, by design, so the table is complete without laundering probes into a seat's queue.
- The watchman's handoff delta becomes "new files since last sweep" (a filename high-water mark, the honk surface's own pattern) rather than the canonical reader's unsigned count: the watchman imports no stack code and names no reader.
- The dark detector keys on `isApiErrorMessage: true` on the newest assistant record of the panel transcript; "a close landed" is an assistant record carrying a `tool_use` block whose name ends in `close_session`, in the same transcript, so the console reads one file and never the Stack.
- `panel_sessions.jsonl` carries exactly the design's four events (`start`, `stop`, `dark`, `close`); a seat's return from dark is read from the transcript and shown in the status, not written as a fifth event.
- The governance tool is named `retire_handoff` (the engine method is `retire`; a bare `retire` names nothing); the pass dispatches it through `server._dispatch_tool` in-process so the retirement gate, the author validation and the Nape hook all see every retirement.
- The marker pool is read with headroom (`_MARKER_POOL = 1000`) and without `exclude_superseded`, so the holdback count the door already prints survives the new rule.
- `leave_letter` separates `author` (the bare model name the lineage keys on) from `source_instance` (the seat string, stored as `seat:`), because a registered seat id such as `grok-build-studio` names no model line.
- The 7-day cap applies only when the granted scope is exactly `["read"]`; `TTL_MAX_HOURS` stays 24 for everything else.
- The empty-search label's stable id is `SOVEREIGN_STORE_ID`, default `studio`, read at call time.
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
- Produces: `resolve_target(...)` additionally returns `"session_id"` and `"cwd"` from the registry record; `hq_push.transcript_path(session_id: str, cwd: str, projects_dir: Path | None = None) -> Path` = `projects_dir / cwd.replace("/", "-") / f"{session_id}.jsonl"` (default `~/.claude/projects`, env `HQ_PUSH_PROJECTS_DIR`); `hq_push.read_transcript_tail(path: Path, max_bytes: int = 262144) -> list[dict] | None` (JSON records from the last `max_bytes`, the first partial line dropped, unparseable lines dropped, `None` when the file cannot be read); `hq_push.seat_state(records: list[dict] | None, handed_at: list[float]) -> dict` = `{"transcript": "ok"|"unreadable", "dark": bool, "dark_reason": str|None, "dark_since": str|None, "last_answered_at": str|None, "unanswered_batches": int, "closed_at": str|None}` where an assistant record (`type == "assistant"`) is an error turn when `isApiErrorMessage is True` (`dark_reason` = its `error` value, rendered `login expired` for `authentication_failed`); `dark` is True when the newest assistant record is an error turn; `dark_since` = the first error turn after `last_answered_at`; `last_answered_at` = the newest assistant record that is not an error turn; `unanswered_batches` = handed stamps newer than `last_answered_at`; `closed_at` = the newest assistant record with a content block `type == "tool_use"` whose `name` ends with `close_session`; an unreadable transcript yields `transcript: "unreadable"`, `dark: False` and every other field `None`/`0`. `PushLink.status()` gains `seat: <that dict>`; while dark, `reason` reads `seat dark since HH:MM (login expired): batches held, N unanswered` and `_tick` hands no batch until a real assistant turn is newer than `dark_since`; `PushLink` keeps the last 100 handed stamps; the 24 h line is included in every batch from the crossing until `closed_at` is newer than it. `hq_panel.record_panel_event(root: Path, event: str, **fields) -> None` appends `{"event": "start"|"stop"|"dark"|"close", "at": <ISO UTC>, "pid": int, ...}` to `root/"hq"/"cockpit"/"panel_sessions.jsonl"` after `live_store_guard.refuse_if_test_targets_live(path, store="hq/cockpit/panel_sessions")`; `start` and `stop` (with `last_exit`) are written by `hq_panel`, `dark` (with `since`, `reason`, `unanswered`) once per dark episode and `close` (with `closed_at`) by the push link. `GET /hq/status` carries `push.seat`; the console status line renders `last answered HH:MM, N batches unanswered, login expired` while dark and `panel transcript unreadable` when unknown. Nothing leaves the machine and nothing restarts: no text to Anthony, no `send_escalation`, no automatic reopen or re-login (decision 6 grants in-console detection only; the rest stays at his word).
- Consumes: plan 1 Task 1 `refuse_if_test_targets_live`.

- [ ] **Step 1: Build the fixture** from the panel transcript `~/.claude/projects/-Users-tony-studio/34a38121-aa63-40b9-8a02-767ee7defc08.jsonl`: the six `authentication_failed` assistant records stamped (UTC) `2026-10-21T03:20:43Z`, `03:35:48Z`, `03:50:54Z`, `04:06:00Z`, `11:11:41Z`, `2026-10-22T02:08:08Z` (the 10-20 23:20 to 10-21 22:08 EDT dark window), each reduced to `type, timestamp, uuid, sessionId, isApiErrorMessage, error, message.role, message.content[0].type, message.content[0].text` (the text is `Login expired · Please run /login`, nothing else survives); plus three synthetic records written by hand: a real assistant turn at `2026-10-21T02:24:04Z`, a real one at `2026-10-22T02:30:00Z`, and an assistant `tool_use` record named `mcp__sovereign-stack__close_session` at `2026-10-22T02:35:00Z`. Command: a six-line python filter on `isApiErrorMessage` and the six stamps; no other field is copied.
- [ ] **Step 2: Write the failing tests**

```python
def test_replay_six_error_turns_marks_dark(fixture):      # the control: dark True, dark_since '2026-10-21T03:20:43Z', reason 'login expired', last_answered_at '2026-10-21T02:24:04Z'
def test_a_real_turn_after_dark_clears_it(fixture)       # with the 02:30Z record: dark False
def test_unanswered_batches_counts_handed_after_last_answer(fixture)  # four stamps after 02:24:04Z -> 4
def test_close_is_a_close_session_tool_use(fixture)       # closed_at == '2026-10-22T02:35:00Z'
def test_unreadable_transcript_is_unknown_not_dark()      # Review Focus 2: records None -> transcript 'unreadable', dark False; PushLink still hands the batch
def test_dark_seat_holds_batches(fake_session, fake_inbox)  # pending events + dark transcript -> no send, reason names 'batches held'; a real turn -> sent
def test_24h_line_repeats_until_close(fake_session, fake_inbox)  # two batches carry the line; after the close record the third does not
def test_panel_sessions_rows(tmp_path)                    # start/stop/dark/close rows with stamps; a live-root path raises LiveStoreWriteRefused under pytest
def test_hq_status_carries_seat(server_fixture)           # tests/test_hq_panel_routes.py: /hq/status JSON has push.seat with the seven keys
```

`fake_session` and `fake_inbox` follow `tests/test_hq_push.py` (`FakeInbox`, `ACCEPT_CMD`, `PROC_START`); the transcript is a file under a tmp `HQ_PUSH_PROJECTS_DIR`.

- [ ] **Step 3: Run to verify they fail**: `pytest tests/test_hq_dark_seat.py -v`; expected `AttributeError: seat_state`.
- [ ] **Step 4: Implement** in the files named; the detector runs on every `_tick` after `_fold`; `dark` is recorded once per episode (`_dark_recorded_since`), `close` once per crossing.
- [ ] **Step 5: Run** `pytest tests/test_hq_dark_seat.py tests/test_hq_push.py tests/test_hq_panel.py tests/test_hq_panel_routes.py tests/test_dashboard_web.py -q`; expected PASS (the 21-key snapshot pins hold: `/snapshot.json` is unchanged).
- [ ] **Step 6: Commit** `git commit -m "console: a dark HQ is seen (login expired marks the seat dark, batches held, 24h notice until a close) and recorded in panel_sessions.jsonl (design A2, decision 6)"`.

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
- Produces: tool `retire_handoff`, inputSchema properties `handoff` (string: id, filename or path, resolved by `HandoffEngine.resolve_handoff_id`), `reason` (string), `source_instance` (string: the reader that asks, validated by `handoff._validate_reader_identity(..., field="source_instance")`), all three `required`; the branch calls `handoff_engine.retire(path, retired_by=source_instance, reason=reason)` and returns `Retired {id} for every reader. reason: {reason}; by: {source_instance}`; an id already in `retired_ids()` returns `{id} was already retired; no new row` and writes nothing; refusals (ValueError) for an unresolvable id, an empty reason, a placeholder reader. `STEP_UP_REASONS["retire_handoff"]` = `"Retiring a handoff removes it from EVERY reader's queue at once; signing removes it from yours alone. It is Tier 3, Anthony's tap, until he delegates the pass, and each retirement records its reason and the reader that asked."` Bearer sessions: no `TOOL_SCOPES` entry (master-only by the bridge's default-deny; Task 12 pins it); the seat socket refuses it (Task 12).
- Consumes: `HandoffEngine.retire`, `retired_ids`, `resolve_handoff_id` (main, unchanged); Task 4's `unsigned_by`.

- [ ] **Step 1: Write the failing tests** dispatching through `server._dispatch_tool("retire_handoff", {...})` with `server.handoff_engine` monkeypatched to a tmp-rooted engine:

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
- Produces: `select_placeholder_rows(engine: HandoffEngine) -> list[dict]`: every handoff whose `consumed_by.strip().lower()` is in `handoff.NON_IDENTIFYING_CONSUMERS` and whose id is not in `engine.retired_ids()`, each `{"handoff_id", "consumed_by", "consumed_at", "timestamp", "source_instance", "thread"}`, sorted by id; CLI `python scripts/retire_placeholder_handoffs.py --root <sovereign root> --by <reader> --receipt <path> [--apply]`; without `--apply` it writes the receipt `{"measured_at": <ISO>, "root": ..., "by": ..., "apply": false, "candidates": N, "rows": [...]}` and prints `candidates=N receipt=<path> sha256=<hex>`; with `--apply` it dispatches `retire_handoff` once per row through `server._dispatch_tool` (in-process, `server.handoff_engine` rebound to `HandoffEngine(root/"handoffs")`) with `source_instance=<--by>` and `reason = f"placeholder reader {consumed_by!r} stamped {consumed_at}; retired by the 2026-10-22 pass on Anthony's word (11:22 EDT, 'All as proposed'; design decision 5)"`, then writes the receipt with `"apply": true, "retired": N, "skipped_already_retired": M, "retired_ids_before": a, "retired_ids_after": b` and prints the same line plus `retired=N`.
- Consumes: Task 5 `retire_handoff`; `handoff.NON_IDENTIFYING_CONSUMERS`.

- [ ] **Step 1: Write the failing tests** on a tmp root seeded with five handoffs (`consumed_by` `unknown`, `test`, `TBD`, `hq-claude-studio`, and one never consumed):

```python
def test_dry_run_lists_three_and_retires_none(tmp_root)   # receipt candidates 3; retired_ids() empty; sha256 printed equals the file's
def test_apply_retires_three_with_the_reason(tmp_root)    # retirements.jsonl: 3 rows, each reason contains "placeholder reader" and the consumed_at
def test_second_apply_retires_zero(tmp_root)              # Review Focus 4: receipt retired 0, skipped_already_retired 3
def test_real_reader_and_unconsumed_rows_untouched(tmp_root)
def test_handoff_files_byte_identical(tmp_root)
```

- [ ] **Step 2: Run to verify they fail**: expected `ModuleNotFoundError` on the script import (import it via `importlib` from `scripts/`).
- [ ] **Step 3: Implement** the script (stdlib `argparse`, `json`, `hashlib`; the dispatch through `asyncio.run(server._dispatch_tool(...))`).
- [ ] **Step 4: Run** `pytest tests/test_retire_placeholder_handoffs.py -q`; expected PASS.
- [ ] **Step 5: Commit** `git commit -m "scripts: the placeholder-reader retirement pass, dry-run by default, listed, receipted, idempotent (decision 5)"`.

### Task 7: The full door's step-up reason tells the truth (B5; decision 3)

**Files:**
- Modify: `clients/claude_bridge/tiers.py` (the module docstring paragraph beginning `Handoff-consuming side effect`, the inline comment above `"where_did_i_leave_off"` in `DESTRUCTIVE_TOOLS`, `STEP_UP_REASONS["where_did_i_leave_off"]`)
- Test: `tests/test_claude_bridge_tiers.py` (`test_where_did_i_leave_off_is_destructive` docstring; one added test), `tests/test_descriptions_census.py` (extend)

**Interfaces:**
- Produces: `STEP_UP_REASONS["where_did_i_leave_off"]` exactly: `"This door stays at step-up for two reasons, and consuming handoffs is not one of them: since the signature ledger (2026-08-31) a read signs the ledger for you alone and hides nothing from anyone. It still WRITES (it signs under your name), and it is the full boot surface, reserved on purpose; a Studio terminal reaching the Stack through this connector is a remote seat (Anthony, 2026-10-22). For an ordinary arrival use arrive_lineage (the calm door) or arrive (the foyer), which sign nothing."`; the docstring and the inline comment say the same in two sentences; plan 1 Task 7's census additionally scans `tiers.STEP_UP_REASONS` values and the `tiers` module docstring under its rule (no `consume` outside a sentence containing `legacy`, `retired`, or `not one of them`).
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

### Task 9: `source_instance` required on every chronicle writer (D1; decision 2)

**Files:**
- Modify: `src/sovereign_stack/server.py` (schemas: add `source_instance` to `record_learning` :1080 and `thread_touch` :2005; add it to `required` on `record_insight`, `record_open_thread`, `record_learning`, `handoff`, `thread_touch`, `close_session`, `resolve_thread_by_id`; `close_session`'s insight legs :3789-3797 lose the `"unknown"` default; `_require_author` in the wrapper plan 1 Task 8 built, after `_reject_unknown_params`), `src/sovereign_stack/ground.py` (`record_catch` schema :425 and the stored field), `src/sovereign_stack/provenance_tools.py` (`supersede_insight` schema :453; the ledger row's `by`), `src/sovereign_stack/memory.py` (`record_learning` and `thread_touch` writers store the field), `clients/bridge_core/pending_writes.py` (`PROVENANCE_PASSTHROUGH_TARGETS` :456), `clients/openai_bridge/pending_writes.py` (the fork's set, :439-470)
- Test: `tests/test_source_instance_required.py`, `tests/test_pending_writes_provenance.py`

**Interfaces:**
- Produces: `server.CHRONICLE_WRITERS = frozenset({"record_insight", "record_open_thread", "record_learning", "handoff", "thread_touch", "record_catch", "supersede_insight", "resolve_thread_by_id", "close_session", "leave_letter"})`; `server._require_author(tool_name: str, arguments: dict) -> str` returning `handoff._validate_author_identity(arguments.get("source_instance", ""))` and raising `ValueError(f"{tool_name}: source_instance is required — name the seat that writes this (a registered seat id such as hq-claude-studio, or your seat string); placeholders such as 'unknown' and 'test' are refused")`; the wrapper calls it for every name in `CHRONICLE_WRITERS` before dispatch, so nothing is written; every entry each writer lands carries `source_instance` (insight, open thread, learning, touch row, catch insight, supersession row `by`, handoff, the three `close_session` legs); `resolve_thread_by_id` keeps plan 1 Task 6's stamp rule (`CALLER_SEAT` wins when set) and its `attribution` field is never `"none"`; `signal_ack` and `nape_ack` are untouched; `PROVENANCE_PASSTHROUGH_TARGETS = frozenset({"handoff", "close_session", "record_insight", "record_open_thread", "thread_touch", "record_learning"})` in both drains. The stored `source_session_id` stays exactly as now (THE DESCENT joins on it); only the display changes: `handoff.format_handoff_for_surface` (:939-941) and the `handoff` result line (`server.py:3772`) render it as `(bridge session {id})`, never as the writer's.
- Consumes: plan 1 Task 6 (`resolve_thread_by_id`'s optional `source_instance` property; `dispatch_context.CALLER_SEAT`), plan 1 Task 8 (the intent-keyed wrapper and `_reject_unknown_params`), Task 10's `leave_letter` (listed here; Task 10 lands the tool).

- [ ] **Step 1: Write the failing tests** on `tmp_sovereign_root` with the three singletons rebound:

```python
@pytest.mark.parametrize("tool,args", WRITER_CALLS)   # one minimal call per CHRONICLE_WRITERS member except leave_letter (Task 10 pins its own)
def test_missing_source_instance_is_refused_and_nothing_lands(tool, args, ...)   # ValueError names the tool; the store is unchanged
def test_placeholder_is_refused(tool, args, ...)                                  # 'unknown' -> ValueError
def test_entry_on_disk_carries_the_seat(tool, args, ...)                          # 'codex-astra-studio' lands on the entry each writer produces
def test_signal_ack_and_nape_ack_unchanged()                                      # not in CHRONICLE_WRITERS; dispatch without the field proceeds to their own checks
def test_close_session_insight_legs_carry_the_seat()                              # the reflection and surprise entries, not only the handoff
def test_display_calls_the_session_the_bridges()                                  # format_handoff_for_surface renders '(bridge session s1)'; the stored field is unchanged
def test_drains_pass_source_instance_for_every_required_target()                  # Review Focus 3: build_commit_arguments for record_learning, thread_touch, record_open_thread carries the envelope's seat; same in the OpenAI fork
```

- [ ] **Step 2: Run to verify they fail**: `pytest tests/test_source_instance_required.py tests/test_pending_writes_provenance.py -v`; expected the writes land anonymous.
- [ ] **Step 3: Implement** at the seams named.
- [ ] **Step 4: Run** the two files plus `pytest -q` (every writer's existing tests must now pass a seat; fix each fixture that omitted one); expected 0 failed.
- [ ] **Step 5: Commit** `git commit -m "writers: source_instance is required on every chronicle writer, refused when missing or a placeholder; the drains pass it for every target (design D1, decision 2)"`.

### Task 10: `leave_letter`, a write tool for outside seats (decision 10)

**Files:**
- Modify: `src/sovereign_stack/witness.py` (beside `_parse_letter_frontmatter` :500), `src/sovereign_stack/server.py` (`Tool(name="leave_letter", ...)` beside `handoff`; the dispatch branch; `TOOL_INTENTS["leave_letter"] = "write"`; `TOOL_TIERS` core; `TOOL_CATEGORIES` `"witness"`), `clients/claude_bridge/tiers.py` (`BASE_TOOLS` add `leave_letter`, with a note in the shape of `record_catch`'s)
- Test: `tests/test_leave_letter.py`, `tests/test_claude_bridge_tiers.py` (one added test)

**Interfaces:**
- Produces: `witness.LETTER_MAX_BYTES = 16384`; `witness.write_letter(sovereign_root: Path, *, author: str, title: str, body: str, seat: str, to: str | None = None, now: datetime | None = None) -> Path`: refuses (ValueError) when `_model_family(author)` is None (the author must be a bare model name, `claude-<line>-<version>` or `gpt-...`), when `title` or `body` is blank, when `body.encode()` exceeds `LETTER_MAX_BYTES`; `to` defaults to `_model_family(author)`; path `sovereign_root/"comms"/"letters"/"to_self"/f"{YYYY-MM-DD}-{slug}.md"` with `slug` = lowercase, runs of non-alphanumerics to `-`, trimmed, at most 60 chars; an existing name gets `-2`, `-3`, ... and never overwrites; the file is a frontmatter block in this order: `type: to_self`, `title: <title>`, `from: <author>`, `to: <to>`, `written_at: <YYYY-MM-DD>`, `seat: <seat>`, `status: placed`, `placed_by: leave_letter, <seat>, <ISO UTC>`, then a blank line, `# <title>`, a blank line, the body; `live_store_guard.refuse_if_test_targets_live(path, store="comms/letters/to_self")` runs before the write. Tool `leave_letter`: properties `author`, `title`, `body`, `source_instance` (the seat string, stored as `seat:`), optional `to`; required all but `to`; result text `Letter placed: comms/letters/to_self/<name> (from <author>, to <to>). It reaches readers of that line through arrive_lineage(source_instance=<bare model name>).`
- Bridge: `TOOL_SCOPES["leave_letter"] = "write"`; `SEAT_SIGNABLE_TOOLS` add (Task 12).
- Consumes: `witness._model_family`, `_parse_letter_frontmatter`, `collect_lineage` (unchanged); plan 1 Task 1 guard; Task 9's `CHRONICLE_WRITERS` (membership).

- [ ] **Step 1: Write the failing tests** on a tmp root:

```python
def test_frontmatter_round_trips_through_the_reader(tmp_root)   # collect_lineage(tmp_root, 'claude-opus-5-6')['to_self'] holds it; a 'claude-fable-5-1' reader does not
def test_author_must_be_a_bare_model_name(tmp_root)             # 'HQ Mac Studio, claude-fable-5-1' refused; 'claude-fable-5-1' accepted with to 'claude-fable'
def test_collision_gets_a_suffix_never_overwrites(tmp_root)     # second letter same day same title -> '-2'; first file unchanged
def test_body_limit_and_blank_fields_refused(tmp_root)
def test_tool_lands_and_names_the_path(tmp_sovereign_root)      # through _dispatch_tool; seat stored as 'seat:'; result names the file
def test_live_root_refused_under_pytest()                       # write_letter at live_root() raises LiveStoreWriteRefused
def test_leave_letter_is_write_intent_and_base_on_connector()   # _intent_for == 'write'; classify == TIER_BASE
```

- [ ] **Step 2: Run to verify they fail**: expected `ImportError: write_letter`.
- [ ] **Step 3: Implement** the writer, the tool, the maps and the tier entry.
- [ ] **Step 4: Run** `pytest tests/test_leave_letter.py tests/test_lineage*.py tests/test_arrive_lineage.py tests/test_claude_bridge_tiers.py tests/test_contract_walker.py tests/test_descriptions_census.py tests/test_source_instance_required.py -q`; expected PASS.
- [ ] **Step 5: Commit** `git commit -m "leave_letter: a write tool that lands a to_self letter under the bare model name, in the form the lineage reader parses (decision 10)"`.

### Task 11: The May dispatcher RFCs are marked superseded (decision 4)

**Files:**
- Modify: `docs/implementation/DISPATCHER_REIMAGINE.md`, `docs/implementation/DISPATCHER_HAIKU.md` (the `**Status:**` line only; text kept)
- Test: `tests/test_docs_status_lines.py`

**Interfaces:**
- Produces: each file's `**Status:**` line becomes `**Status:** SUPERSEDED 2026-10-22 by docs/superpowers/specs/2026-10-21-next-phase-design.md (section 3, non-goals; section 8 decision 4, ruled by Anthony by text 2026-10-22 11:22 EDT "All as proposed"). Never built; kept as history, text unchanged below this line. Original status: <the old line's text after "**Status:**">.`
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**: for both files, the first line starting `**Status:**` begins `**Status:** SUPERSEDED 2026-10-22` and carries `Original status:`; the bodies still contain `## Motivation` (REIMAGINE) and `## What this changes from the RFC` (HAIKU).
- [ ] **Step 2: Run to verify it fails.** **Step 3: Edit** the two lines. **Step 4: Run** the test; PASS.
- [ ] **Step 5: Commit** `git commit -m "docs: DISPATCHER_REIMAGINE and DISPATCHER_HAIKU marked superseded by the next-phase design, text kept (decision 4)"`.

### Task 12: The bridge half: scopes, the socket, the TTL and the watchman (B3, D1, decisions 5, 10, 11)

**Files** (bridge worktree `/Users/tony_studio/.cache/wt-bridge-plan-2`):
- Modify: `session_tokens.py` (`TTL_MAX_HOURS_READ_ONLY = 168` beside `TTL_MAX_HOURS` :35; `clamp_ttl` :166; `mint` :221; `TOOL_SCOPES` :94 add `"leave_letter": "write"`), `arrival_gate.py` (:153), `bridge.py` (the `requested_ttl_hours` field doc :2376-2379; the request response), `seat_identity.py` (`SEAT_NEVER_TOOLS` :418 add `"retire_handoff"` beside `"retire"`; `SEAT_SIGNABLE_TOOLS` :494 add `record_learning`, `thread_touch`, `record_catch`, `supersede_insight`, `resolve_thread_by_id`, `leave_letter`), `watchman/watchman_sweep.py` (`scan_handoffs` :353-417)
- Test: `tests/test_session_tokens.py`, `tests/test_arrival_gate.py`, `tests/test_seat_identity.py`, `tests/test_session_scope_retirement.py` (green with the new scope once the stack side publishes the tool), `watchman/tests/test_delta_detection.py`

**Interfaces:**
- Produces: `clamp_ttl(ttl_hours: int | float | None, scope: list[str] | None = None) -> int`: the cap is `TTL_MAX_HOURS_READ_ONLY` (168) when `scope == ["read"]`, else `TTL_MAX_HOURS` (24); `mint` and `arrival_gate` pass the granted scope; the arrival request response gains `ttl_note` (`"asked {n} h; a read-only grant may run to 168 h, a grant that includes write to 24 h"`) whenever the ask exceeded its cap; the field doc reads `default 12, clamped to 1..24, or 1..168 when the granted scope is exactly read`; `retire_handoff` is refused to seats; the six writers are signed with the seat id; `scan_handoffs(root, state)` keeps `state["handoffs_newest_seen"]` (the lexically greatest `*.json` filename) and emits one item `change: "new-handoffs"` with `detail: {"new_since_last_sweep": N, "on_disk": M, "newest": [...up to 5]}` when files sort after it, never reads `consumed_at`, baselines silently on the first sweep (`note: "watch begins at this high-water"`), and no longer writes `handoffs_unconsumed`.
- Consumes: Task 5 (`retire_handoff`), Task 9 (the writer list), Task 10 (`leave_letter`).

- [ ] **Step 1: Write the failing tests**

```python
def test_read_only_grant_clamps_to_168():           # clamp_ttl(500, ["read"]) == 168; mint(scope=["read"], ttl_hours=168)["ttl_hours"] == 168
def test_read_write_asking_168_gets_24_with_note()  # Review Focus 5: arrival request scope read+write, 168 -> granted_ttl_hours 24, ttl_note present
def test_default_unchanged_12()
def test_retire_handoff_refused_to_seats()          # in SEAT_NEVER_TOOLS; the seat surface excludes it
def test_retire_handoff_has_no_bearer_scope()       # absent from TOOL_SCOPES, so a read, write or session grant is refused it and only the master token reaches it
def test_leave_letter_is_write_scope_and_signable()
def test_new_handoff_file_since_last_sweep_is_a_delta(sov_root, ...)   # replaces test_handoff_unconsumed_count_change_is_a_delta
def test_consumed_at_change_is_not_a_delta(sov_root, ...)
def test_first_sweep_baselines_silently(sov_root, ...)
```

- [ ] **Step 2: Run to verify they fail** with the bridge command in Global Constraints; print both module paths first.
- [ ] **Step 3: Implement** at the seams named.
- [ ] **Step 4: Run** the bridge suite; expected 0 failed, count = the bridge baseline measured at the cut plus the tests added (563 at b79d113 on 2026-10-10, before plan 1's Task 10).
- [ ] **Step 5: Commit** in the bridge worktree: `git commit -m "bridge: read-only grants may run 168 h; leave_letter write scope; retire_handoff refused to seats; every chronicle writer signed with the seat; the watchman counts new handoffs, never the legacy field (design B3/D1, decisions 5, 10, 11)"`.

### Task 13: Carried: the OAuth modules' import-time chmods move under the guard (plan 1 ledger, ruling 09:25 EDT)

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

### Task 14: Carried: tests cannot read the live record (plan 1 ledger, ruling 10:52 EDT)

**Files:**
- Modify: `src/sovereign_stack/live_store_guard.py`, `src/sovereign_stack/handoff.py` (`_load_all` :494, `_read_log` :794), `src/sovereign_stack/memory.py` (the loaders for insights, open threads and learnings; the supersession ledger reader if it lives here, else `provenance.py`), `src/sovereign_stack/nape_daemon.py` (the store reader), `tests/test_ground.py` (the `absent-when-no-catches` test rebinds `server.handoff_engine` to a tmp-rooted engine), and every test the census turns up
- Test: `tests/test_live_store_read_guard.py`

**Interfaces:**
- Produces: `LiveStoreReadRefused(RuntimeError)` and `refuse_if_test_reads_live(path: Path | str, *, store: str) -> None` with the same containment test and `live_root()` as the write guard, the message naming the store and the fix (root the reader at `tmp_sovereign_root`, or rebind the singleton); applied as the first statement of each read funnel named in the census test.
- Consumes: plan 1 Task 1 (`live_root`, the module).

- [ ] **Step 1: Write the failing tests**: `test_refuses_live_read_under_pytest`, `test_allows_tmp_root`, `test_allows_live_outside_pytest`, `test_every_read_funnel_calls_the_guard` (inspect.getsource over the funnels), `test_control_a_live_read_is_refused` (point `live_store_guard.live_root` at a tmp root holding one handoff; `HandoffEngine(str(root/"handoffs")).unsigned_by("x")` raises naming the path).
- [ ] **Step 2: Run to verify they fail.** **Step 3: Implement** the guard and the calls.
- [ ] **Step 4: Run the whole suite**; every failure raising `LiveStoreReadRefused` is a live reader: fix each by rooting it, and list them in the commit body with the count. Expected at least `tests/test_ground.py::...absent-when-no-catches` and `tests/test_progressive_boot.py::TestArriveThinBoot::test_points_to_full_inheritance_not_the_retired_delta` (R1 §7). Then `pytest -q`; 0 failed.
- [ ] **Step 5: Commit** `git commit -m "guard: a test can never read the live record either; the read funnels refuse under PYTEST_CURRENT_TEST and N live readers are rooted (plan 1 carry, SOP #12)"`.

### Task 15: Carried, conditional: the empty-search store label (plan 1 ledger, "Final" ruling)

**Files:**
- Modify: `src/sovereign_stack/memory.py` (:2503 on plan 1's tip), `tests/test_recall_scope_line.py`, `tests/test_phase1_recall_honesty.py`

- [ ] **Step 1: Check first**: `grep -n gethostname src/sovereign_stack/memory.py tests/test_recall_scope_line.py tests/test_phase1_recall_honesty.py` at the plan-2 cut. Zero hits: write `Task 15: done in plan 1's final wave at <sha>` in the ledger and stop here.
- [ ] **Step 2: Otherwise write the failing test** `test_the_store_label_is_a_stable_id_not_the_hostname` (replacing `test_the_store_is_named_by_socket_gethostname_at_call_time`): monkeypatching `socket.gethostname` changes nothing; `SOVEREIGN_STORE_ID=probe` yields `["stack-chronicle:probe"]`; unset yields `["stack-chronicle:studio"]`.
- [ ] **Step 3: Implement** `memory.store_id() -> str` (env `SOVEREIGN_STORE_ID`, default `"studio"`, read at call time) and the label `f"stack-chronicle:{store_id()}"`; re-pin the second test file.
- [ ] **Step 4: Run** `pytest tests/test_recall_scope_line.py tests/test_phase1_recall_honesty.py tests/test_recall*.py -q`; PASS.
- [ ] **Step 5: Commit** `git commit -m "recall: the scope label names the store by a stable id, never the hostname (design B6, plan 1 carry)"`.

### Task 16: Whole-branch verification, the two PRs, the deploy, the pass, the receipts (decision 8)

- [ ] **Step 1:** Rebase `phase/plan-2` onto `main` if plan 1 has merged (never merge `main` in); if it has not, hold the PR until it does and keep rebasing onto `phase/plan-1`'s tip. Full stack suite in the worktree with the worktree's code: `0 failed`, count = the baseline at the cut + every test this plan added (the ledger carries the sum). Bridge suite in the bridge worktree: `0 failed`.
- [ ] **Step 2:** `ruff check src tests clients`; regenerate `stack_manifest.json` (53 tools become 55: `retire_handoff`, `leave_letter`); `manifest --check` green; plan 1 Task 7's census holds the repo `CLAUDE.md` tool count.
- [ ] **Step 3:** Adversarial review by a fresh Opus agent over `git diff <plan-1 tip>..phase/plan-2` with this plan's Review Focus as its brief, and the same over the bridge diff; required fixes as follow-up commits; HQ reads every diff.
- [ ] **Step 4:** Open the stack PR and the bridge PR against `main`, each linking the design and section 8's rulings (11:22 EDT "All as proposed"; 11:29 EDT "Also deploy plan 2"), naming decision 7 as excluded, and listing the deploy order and the rollback line below.
- [ ] **Step 5: Deploy.** Anthony's word for this deploy is the 11:29 EDT text; the step still writes the rollback line FIRST, to `studio:~/.sovereign/hq/lanes/runs/2026-10-22_next-phase-plan-2/deploy.log`: `stack main was <sha>; bridge main was <sha>; rollback: git -C ~/sovereign-stack reset --hard <sha> && git -C ~/sovereign-bridge reset --hard <sha> && launchctl kickstart -k gui/501/com.templetwo.sovereign-sse && launchctl kickstart -k gui/501/com.templetwo.sovereign-bridge && launchctl kickstart -k gui/501/com.templetwo.sovereign-dashboard-web; retirements rollback: mv ~/.sovereign/handoffs/retirements.jsonl ~/.sovereign/handoffs/retirements.jsonl.bak-<stamp>`. Then, in this order: (a) HQ writes a handoff (`thread: next-phase-plan-2`) naming the deploy and that the panel seat ends; (b) fast-forward both `main`s; (c) `kickstart -k` sovereign-sse, then sovereign-bridge, then sovereign-dashboard-web LAST (it ends this panel seat; the watchman needs no restart, its next sweep runs the new code); (d) validate: both heartbeats serve the new commits with `tools: 55`; `/api/tools` lists `leave_letter` under `write` and not `retire_handoff`; a seat-socket `retire_handoff` call is refused; `arrive_lineage` and the full door render the marker header of Task 2; `GET /hq/status` carries `push.seat`; `~/.sovereign/hq/cockpit/panel_sessions.jsonl` has its first `start` row.
- [ ] **Step 6: The pass**, HQ, after (d): dry run `scripts/retire_placeholder_handoffs.py --root ~/.sovereign --by hq-claude-studio --receipt <lane>/retirement-pass-dry.json`, then `--apply` with `--receipt <lane>/retirement-pass.json`; expected candidates 237 (159 `unknown`, 78 `test` on 2026-10-22 at 443 files; re-measured by the run); record `unsigned_by_count("hq-claude-studio")` before and after in the receipt; the watchman's next sweep shows no handoff delta.
- [ ] **Step 7: The Jetson grant**, HQ: `TTL_HOURS = 168` in the lane copy `~/.sovereign/hq/lanes/runs/2026-10-04_jetson-openclaw-workspace/request_grant.sh` (a `.bak` beside it) and in the Jetson's `~/.sovereign-seat/request_grant.sh` over ssh (a `.bak` beside it); the next tap is Anthony's and mints 168 h; update the memory `jetson_openclaw_seat.md` line from daily to weekly.
- [ ] **Step 8: Receipts.** `record_insight` on the Stack with `file` receipts on `deploy.log` and both pass receipts, `source_instance=hq-claude-studio`, domain `next-phase-plan-2,deploy,2026-10-22`; the ledger closed with the counts.
