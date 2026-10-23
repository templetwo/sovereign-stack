# Next Phase, Plan 1 (tranche 0 and the first deploy) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the tripwire that makes a test unable to write the live record, then the first deploy's small, decision-free fixes from the next-phase design: relevance by default when a query is given, receipts that say moved-on versus never-verified and refuse refs that cannot survive, bounded intensity, an honest policy screen, signals by source, served-versus-HEAD, descriptions that match the code, unknown arguments refused on write tools, side-effect writers that name their caller, and an empty search that names its stores.

**Architecture:** Every task is a small change behind an existing seam (the single write funnels in `memory.py`, `handoff.py` and `nape_daemon.py`; the receipt verifier in `provenance.py`; the hygiene loop in `seasons.py`; the tool dispatch in `server.py`; the heartbeat assembly in `signal_ledger.py` and the bridge). No new module, no new store, no daemon. Each task is red-then-green in a worktree with the worktree's own code on the path, one commit per task, on one branch `phase/plan-1` cut from `main` at 776dfa2 (or at the then-current main once `fix/nape-one-counter` merges; rebase, never merge main into the branch).

**Tech Stack:** Python 3.10+ (venv at `/Users/tony_studio/sovereign-stack/venv`, 3.12), pytest 9, ruff 0.14.2 (pre-commit hook runs it), FastAPI/Starlette for the SSE and dashboard servers, JSONL stores under `~/.sovereign`.

**Spec:** `docs/superpowers/specs/2026-10-21-next-phase-design.md` (revision 2). Items in this plan: T0.1, B1, B6, C1, C3, C4, C5, D2, D7, A3, A4, B4, E2. Items waiting on Anthony's answers (B2, B3, D1, B5's held tools) and the dashboard-web items that must deploy together (A1, A2) are Plan 2.

## Global Constraints

- Work only in a worktree: `git -C /Users/tony_studio/sovereign-stack worktree add /Users/tony_studio/.cache/wt-phase-plan-1 -b phase/plan-1 main`. The live checkout at `/Users/tony_studio/sovereign-stack` is the running service; never check out, never run tests there.
- Run tests as `cd /Users/tony_studio/.cache/wt-phase-plan-1 && PYTHONPATH=$PWD/src:$PWD/clients /Users/tony_studio/sovereign-stack/venv/bin/python -m pytest -q <targets>`; before the first run, print `sovereign_stack.__file__` and `bridge_core.__file__` under that PYTHONPATH and confirm both resolve inside the worktree.
- Every test writes under a `tmp_path` or the `tmp_sovereign_root` fixture; after Task 1 lands, a test that reaches the live root is refused by construction.
- No deletion of record content anywhere; no change to classification lists except where a task names the exact edit; no push, no deploy (both Anthony's; each task ends at a local commit).
- Python floor 3.10; ruff line length 100; `mcp<2.0`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. *Status recorded 2026-10-23 by Task 8's fix round 4 and corrected by its fix round 5 (Task 8's first two rounds run on Fable; Task 5's fix round 4 ran on Fable before either), not a ruling; it comes out by reverting those two docs commits, newest first; the one-line ruling it asks the controller for is in task-8-report.md:*
  - **The reading the branch follows:** the trailer names the Claude model of the session that makes the commit. When that session lands, unchanged, a change a session on another model wrote, the trailer still names the committing session's model and the message body names the writing model; a second attribution line naming the other model is barred by the harness attribution reminder ("do not add attribution lines this reminder leaves out"). 4f5ec65 is that case: written on Opus 5.5 in Task 5's fix round 3, committed on Fable 5.1 in its fix round 4, with the Opus round named in its message (audit/task-5-fix-4/commit-message.txt:25-27).
  - **Measured at 07f90e7** with `git log --format='%(trailers:key=Co-Authored-By,valueonly)' 829acb4..HEAD`: 32 commits. `Claude Fable 5.1` on the 5 committed by Fable sessions (HQ's docs commits 1989e01, 6fff937 and 7099d92; 4f5ec65; 07f90e7) and `Claude Opus 5.5` on the 27 committed by the fresh Opus implementers the ledger's execution ruling (progress.md:2, :23) put on each task. Every trailer names its committing session's model; by the line's letter, those 27 do not comply.
  - **Where the line holds as written:** only for commits Fable sessions make (HQ's, and fix rounds 4 and 5 of each task). Opus-written commits in Tasks 9 to 12 carry Opus 5.5 under this reading; a reviewer who finds one should cite this note rather than re-open the question, and an Opus writer must not put Fable 5.1 on its own commit.
  - **Prior decisions, none a ruling:** the reviews of Tasks 1 to 8 each flagged the Opus trailers against this line as Minor and left the choice to the controller; Tasks 1, 2, 3, 4, 6 and 7 offered a message-only amend if Fable is wanted, and Task 5 put it as changing the plan text or keeping the convention; Tasks 1, 2, 5, 6, 7 and 8 called the Opus trailer accurate to the model that wrote the code, and Task 8's review alone went on to judge that an amend to Fable 5.1 would put a false model on the commit. The Ember lane took this reading twice on 2026-10-22 (Stack chronicle, MacBook seat, ground_truth: trailers "accepted ... because the trailer names the model that made the commit, and future plan templates will say so"). The harness attribution reminder each session carries names that session's model for the commits it creates, and the co-authorship byline rule is "with Claude [model identifier]". No ruling by Anthony or by this controller exists on any surface searched; the searches are named in task-8-report.md (fix rounds 4 and 5).
  - **Not the builder's:** the ruling, and rewriting the 27 earlier trailers (a history rewrite on a shared branch).
- Live seats write the record during any test run; nothing may compare live-store sizes or counts.

## Review Focus

1. A writer the tripwire does not cover (a daemon's direct `open(..., "a")` on a chronicle shard, or `archive_exchange` writing bodies) lets a test land a row anyway; expected: refused with the path named. Pinned in Task 1 by the funnel census test.
2. A recall with a query of only whitespace or only stop-characters must still be treated as a bare browse (`newest`), not as a relevance query over nothing; pinned in Task 2. *Proposed 2026-10-22 by Task 2's fix round 1, not a ruling; it holds only if HQ keeps it:* "stop-characters" means the characters the library's tokenizer drops (`query.split()` in `memory.py`), and those are the whitespace `str.split()` drops, because the library has no stop set. A punctuation-only query such as `","` is a term that filters, so it defaults to `relevance` and the envelope says so. Making punctuation a browse would need the tokenizer changed too, or the label and the filter would disagree. Pinned by `test_strip_and_split_agree_on_whitespace_for_every_code_point` and `test_punctuation_is_a_term_not_a_browse` in `tests/test_recall_order_default.py`.
3. A `file` receipt whose target is a symlink into `/private/tmp` or `~/.claude/plugins/cache` must be refused by its resolved path, not its spelled path; pinned in Task 4.
4. A heartbeat built while the bridge's git metadata is unreadable must report `served_predates_head: null` with a reason, never `false`; pinned in Task 10.
5. An unknown argument sent by the Ring-2 drains (which forward proposals verbatim) must produce a refusal the drain records as `commit_failed` with the offending key named, never a silent drop; pinned in Task 8. *Status recorded 2026-10-23 by Task 8's fix round 3, not a ruling; it replaces the notes rounds 1 and 2 wrote here, whose text is at b365550:* it holds on both drains (bridge_core's and the OpenAI fork's) for all seven commit targets. For the five whose intent refuses (`record_insight`, `record_learning`, `record_open_thread`, `handoff`, `close_session`) the refusal is the Stack's own, returned through the POST and pinned by `TestTheDrainRecordsTheRefusal`. For the two the intent keying leaves on the warn path (`thread_touch`, intent `communicate`, and `self_model`'s update, intent `orient`) each drain screens the commit's arguments itself before the POST, against `ARGUMENT_SCREEN_TARGETS` (the target's published properties, held equal to `list_tools()` by test), and records `commit_failed` with nothing posted, so nothing is written and a retry has nothing to write twice; pinned by `TestTheDrainScreensTheWarnPathTargets` in `tests/test_unknown_params_write_tools.py`. That screen is the fourth remedy Task 8's fix-round-1 re-review listed, chosen by the implementer under the dispatch's best-reading rule because it changes no classification list and no direct-call behaviour; it comes out by reverting Task 8's fix-round-3 commits, newest first (the console fix beside it tests the screen's refusal), and the other three remedies (reclassify the two intents, widen the refuse predicate, narrow this item's wording) stay HQ's. Not covered, and HQ's beside D1: a direct call to either tool still runs on the warn path, where spec G4 reads "unknown arguments to a write tool are refused, not dropped", and D1 declares on `thread_touch` the `source_instance` that seats already send there.

---

### Task 1: Tests cannot write the live record (T0.1)

**Files:**
- Modify: `src/sovereign_stack/handoff.py` (the existing `_refuse_live_store_during_tests` helper moves to a shared module)
- Create: `src/sovereign_stack/live_store_guard.py`
- Modify: `src/sovereign_stack/memory.py` (chronicle insights, open threads, learnings, supersessions write funnels), `src/sovereign_stack/nape_daemon.py` (`_append_jsonl`), `src/sovereign_stack/provenance.py` (supersession ledger append, if separate)
- Test: `tests/test_live_store_guard.py`

**Interfaces:**
- Produces: `live_store_guard.refuse_if_test_targets_live(path: Path, *, store: str) -> None` raising `LiveStoreWriteRefused(path, store, test_id)` (subclass of `RuntimeError`, NOT `AssertionError`, so a broad `except Exception` in callers still surfaces it as a failure and conftest's own guard keeps its class). Refuses when `os.environ.get("PYTEST_CURRENT_TEST")` is set AND `path.resolve()` is under the live root (`Path.home()/".sovereign"` unless `SOVEREIGN_ROOT` points elsewhere).
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing tests**

```python
def test_refuses_live_path_under_pytest(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/x.py::t (call)")
    live = Path.home() / ".sovereign" / "chronicle" / "insights" / "probe" / "x.jsonl"
    with pytest.raises(LiveStoreWriteRefused) as e:
        refuse_if_test_targets_live(live, store="chronicle/insights")
    assert "chronicle/insights" in str(e.value) and str(live) in str(e.value)

def test_allows_tmp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/x.py::t (call)")
    refuse_if_test_targets_live(tmp_path / "chronicle" / "insights" / "a.jsonl", store="chronicle/insights")  # no raise

def test_allows_live_outside_pytest(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    refuse_if_test_targets_live(Path.home() / ".sovereign" / "handoffs" / "x.json", store="handoffs")  # no raise

def test_every_funnel_calls_the_guard():
    # census: each named funnel function's source contains a call to refuse_if_test_targets_live
    import inspect, sovereign_stack.memory as m, sovereign_stack.nape_daemon as n, sovereign_stack.handoff as h
    for fn in (m.<insight funnel>, m.<open_thread funnel>, m.<learning funnel>, m.<supersession funnel>, n.NapeDaemon._append_jsonl, h.<handoff funnel>):
        assert "refuse_if_test_targets_live" in inspect.getsource(fn), fn.__qualname__

def test_control_a_leaking_write_is_refused(monkeypatch, tmp_sovereign_root):
    # positive control: point the guard's live root at tmp_sovereign_root and call the real record path
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_sovereign_root)); monkeypatch.setenv("PYTEST_CURRENT_TEST", "t")
    with pytest.raises(LiveStoreWriteRefused):
        <the real insight funnel>(..., root=tmp_sovereign_root)
```

(The implementer fills `<funnel>` with the real function names found by `grep -n 'open(.*"a"' src/sovereign_stack/memory.py` and the write sites in `handoff.py` and `nape_daemon.py`; the census test is the list of record.)

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_live_store_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: live_store_guard` and then `AssertionError` on the census.

- [ ] **Step 3: Implement `live_store_guard.py`** with `LiveStoreWriteRefused` and `refuse_if_test_targets_live(path, *, store)`; move the body of `handoff._refuse_live_store_during_tests` into it and make `handoff.py` call the shared function; add the call as the first line of every write funnel named in the census test. The live root resolves once per call from `SOVEREIGN_ROOT` or `~/.sovereign` (not at import).

- [ ] **Step 4: Run the new tests and the suite**

Run: `pytest tests/test_live_store_guard.py -v` then `pytest -q`
Expected: new tests PASS; suite count equals the baseline plus the new tests, 0 failed (baseline: 4227 passed, 6 skipped on `fix/nape-one-counter`; 4195 on main).

- [ ] **Step 5: Commit**

```bash
git add src/sovereign_stack/live_store_guard.py src/sovereign_stack/handoff.py src/sovereign_stack/memory.py src/sovereign_stack/nape_daemon.py src/sovereign_stack/provenance.py tests/test_live_store_guard.py
git commit -m "guard: a test can never write the live record; every store funnel refuses under PYTEST_CURRENT_TEST (design T0.1)"
```

### Task 2: Relevance by default when a query is given (B1)

**Files:**
- Modify: `src/sovereign_stack/server.py` (the `recall_insights` dispatch: `arguments.get("order", "newest")`)
- Test: `tests/test_recall_order_default.py`

**Interfaces:**
- Produces: dispatch rule `order = arguments.get("order") or ("relevance" if (arguments.get("query") or "").strip() else "newest")`; the envelope's coverage line gains `order_used: <value>` and `order_defaulted: true|false`.

- [ ] **Step 1: Write the failing tests**: `test_query_defaults_to_relevance` (call the tool with `query="calibration seeds"`, no `order`; assert `order_used == "relevance"` and `order_defaulted is True`); `test_bare_browse_stays_newest` (domain only; `order_used == "newest"`); `test_whitespace_query_is_a_browse` (`query="   "` yields `newest`); `test_explicit_order_wins` (`query` plus `order="oldest"` yields `oldest`, `order_defaulted False`). All against a `tmp_sovereign_root` seeded with three entries.
- [ ] **Step 2: Run to verify they fail** (`order_used` key absent; default `newest`).
- [ ] **Step 3: Implement** the rule in the dispatch and the two envelope fields; leave `memory.py`'s own default untouched.
- [ ] **Step 4: Run** `pytest tests/test_recall_order_default.py tests/test_recall*.py -q`; expected PASS, no regression in the existing recall tests.
- [ ] **Step 5: Commit** `git commit -m "recall: relevance when a query is given, newest for a bare browse; the envelope names the order used (design B1)"`.

### Task 3: An empty search names its stores (B6)

**Files:**
- Modify: `src/sovereign_stack/memory.py` (the coverage envelope builder) or `server.py` where `scope` is assembled
- Test: `tests/test_recall_scope_line.py`

**Interfaces:**
- Produces: envelope `scope.stores_searched = ["stack-chronicle:<hostname>"]` and `scope.not_searched = ["t2helix (machine-local)", "other machines' chronicles"]` on every recall, including zero-match results; `scope.note` reads `"an empty result is a measurement of these stores only"` when `total_matched == 0`.

- [ ] **Step 1: Failing tests**: zero-match query returns the three fields; a match also carries them; the hostname is `socket.gethostname()`.
- [ ] **Step 2: Run, expect KeyError on `stores_searched`.**
- [ ] **Step 3: Implement** in the envelope builder.
- [ ] **Step 4: Run** the file plus `tests/test_recall*.py`; expected PASS.
- [ ] **Step 5: Commit** `git commit -m "recall: the coverage envelope names the stores searched and the ones it cannot reach (design B6, thread 8a1d522b)"`.

### Task 4: Receipts that keep, part 1 (C1, C3, C4)

**Files:**
- Modify: `src/sovereign_stack/seasons.py` (the hygiene re-verify loop and its per-receipt report), `src/sovereign_stack/provenance.py` (receipt ref validation at write), `src/sovereign_stack/memory.py` (intensity range check beside the `emotional_intensity` check)
- Test: `tests/test_receipt_reverify_report.py`, `tests/test_receipt_ref_refusals.py`, `tests/test_intensity_range.py`

**Interfaces:**
- Produces: hygiene rows carry `status in {"ok", "moved_on", "never_verified", "mismatch", "missing", "unreadable"}` where `never_verified` = `checked_at_write != "verified"`, `moved_on` = bytes differ AND file mtime > entry timestamp, `mismatch` = bytes differ AND mtime ≤ entry timestamp; `provenance.validate_file_ref(ref: str) -> Path` raising `ValueError` naming the rule for: resolved path under `/private/tmp` or `/tmp`, under `~/.claude/plugins/cache`, or a basename ending in `.env` or containing `.env.`; a ref beginning `studio:` is accepted when the tag names this host (`socket.gethostname()` starts with `mac-studio` or the tag equals the configured host label), with the tag stripped; `record_insight` raises `ValueError("intensity must be between 0.0 and 1.0")` outside the range.

- [ ] **Step 1: Failing tests**: (a) re-verify report: three seeded receipts in a tmp root (one verified then file changed later, one never matched at write, one unchanged) classify as `moved_on`, `never_verified`, `ok`; (b) refusals: each of the four ref classes raises with the rule named; a symlink from a lane dir into `/private/tmp` is refused by its resolved path; `studio:~/x` on this host is accepted and stored as the bare path; (c) intensity `1.5`, `-0.1`, `"3"` refused; `0.0`, `1.0`, `0.9` accepted; `emotional_intensity` behaviour unchanged.
- [ ] **Step 2: Run, expect FAIL** (statuses absent; refs accepted; intensity accepted).
- [ ] **Step 3: Implement** the three changes at the seams named.
- [ ] **Step 4: Run** the three files plus `tests/test_provenance*.py tests/test_seasons*.py tests/test_lived_ground_truth.py`; expected PASS.
- [ ] **Step 5: Commit** `git commit -m "receipts: re-verify splits moved-on from never-verified; refs under scratch, plugin cache or env-named files refused; intensity bounded to [0,1] (design C1, C3, C4)"`.

### Task 5: The policy screen says what it is (C5)

**Files:**
- Modify: `src/sovereign_stack/seasons.py` (the policy-candidate section builder and its regex)
- Test: `tests/test_season_review_keyword_screen.py`

**Interfaces:**
- Produces: section 3 titled `POLICY-SHAPED KEYWORD SCREEN (a regex over sentinel text; NOT a count of policies)`; each candidate line carries `matched: <word>`; the overflow line reads `…and N more matches (keyword screen)`.

- [ ] **Step 1: Failing test**: a tmp root with two sentinels (one containing "never", one containing "always policy") renders the new title, `matched: never` and `matched: always, policy`; the old title string is absent.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run** the file plus `tests/test_seasons*.py`; PASS.
- [ ] **Step 5: Commit** `git commit -m "season_review: the policy section is a keyword screen and says so, with the matched word beside each candidate (design C5)"`.

### Task 6: Side-effect writers name their caller (D2)

**Files:**
- Modify: `src/sovereign_stack/server.py` (`resolve_thread_by_id` and any handler that writes an insight as a side effect: grep `record_insight(` inside handlers), reading the caller seat from `dispatch_context.CALLER_SEAT` (the socket's stamp)
- Test: `tests/test_side_effect_writer_identity.py`

**Interfaces:**
- Produces: the insight written by `resolve_thread_by_id` carries `source_instance = CALLER_SEAT if set else arguments.get("source_instance")`; when neither exists the insight still lands (D1 is Plan 2) with `source_instance` absent and the result carries `attribution: "none"`.

- [ ] **Step 1: Failing tests**: with `dispatch_context.CALLER_SEAT = "hq-claude-studio"` the written insight carries it; with no seat and an argument, the argument; with neither, `attribution: "none"` in the result.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run** the file plus `tests/test_resolve_thread*.py tests/test_seat*.py`; PASS.
- [ ] **Step 5: Commit** `git commit -m "threads: resolve_thread_by_id stamps the socket caller's seat on the insight it writes (design D2, thread a85ec5e6)"`.

### Task 7: Descriptions match the code (B4)

**Files:**
- Modify: `src/sovereign_stack/server.py` (tool descriptions for `where_did_i_leave_off`, its `consume` and `source_instance` parameter texts, `nape_ack`), `src/sovereign_stack/aperture.py` (`widen_with` naming `handoff_archaeology`), `src/sovereign_stack/handoff.py` (module docstring lines on consumption), `CLAUDE.md` (repo root: the version line and the retired tools in the Session Start section), `/Users/tony_studio/sovereign-bridge/bridge.py` only via a note in the PR (the `/api/discover` text lives in the bridge; Plan 2 D5 carries the cross-repo job)
- Test: `tests/test_descriptions_census.py`

**Interfaces:**
- Produces: a census test that loads `server.RETIRED_TOOLS` and every registered tool's description and parameter descriptions and asserts none contains a retired tool name or the words `consumed`/`unconsumed`/`consume` except inside a sentence that contains `legacy` or `retired`; and asserts the repo `CLAUDE.md` first version line matches `pyproject.version`.

- [ ] **Step 1: Write the census test; run; expect FAIL** naming the offending descriptions (the list is the work).
- [ ] **Step 2: Edit** each named description to the ledger's semantics ("reading signs it for you alone; only retire() clears it for everyone") and the repo `CLAUDE.md` header line to the live version and tool count.
- [ ] **Step 3: Run** the census plus `pytest -q tests/test_tool_descriptions*.py tests/test_progressive_boot.py`; PASS.
- [ ] **Step 4: Commit** `git commit -m "descriptions: the door, nape_ack, the aperture and the repo CLAUDE.md say what the code does; a census test keeps them honest (design B4)"`.

### Task 8: Unknown arguments are refused on write tools (D7)

**Files:**
- Modify: `src/sovereign_stack/server.py` (`_reject_unknown_params` and its two call sites; the dispatch wrapper)
- Test: `tests/test_unknown_params_write_tools.py`

**Interfaces:**
- Produces: for every tool whose intent is `write`, `govern` or `handoff` (from `TOOL_INTENTS`), an argument not in the tool's `inputSchema.properties` returns `{"ok": false, "error": "unknown argument(s): <names>; accepted: <names>"}` and writes nothing; read tools add `warnings: ["unknown argument(s) ignored: ..."]` to the envelope. The drains' `commit_failed` path records that error text verbatim.

- [ ] **Step 1: Failing tests**: `record_insight` with `foo=1` refused and no entry written in the tmp root; `recall_insights` with `foo=1` returns results plus the warning; a parametrised test over every write/govern/handoff tool sends one unknown key and asserts refusal. Also (Review Focus 5): a proposal with an unknown key driven through `bridge_core.pending_writes`'s commit path against a tmp root ends in state `commit_failed` with the error text `unknown argument(s): foo` recorded on the proposal, never `committed`.
- [ ] **Step 2: Run, expect FAIL** (silent drop).
- [ ] **Step 3: Implement** by lifting the existing guard into the dispatch wrapper keyed on intent.
- [ ] **Step 4: Run** the file plus the full suite (this touches every tool); PASS, 0 failed.
- [ ] **Step 5: Commit** `git commit -m "dispatch: unknown arguments are refused on write, govern and handoff tools and warned on reads (design D7, thread 0804046c)"`.

### Task 9: Signals by source, never a total (A3)

**Files:**
- Modify: `src/sovereign_stack/signal_ledger.py` (`heartbeat_field`: keep `by_source`, drop `total` from the top level into `by_source_sum` labelled), `src/sovereign_stack/dashboard.py` and `dashboard_web.py` (any render of the total), and OUTSIDE the repo `~/bin/hq-pulse.py` (status line: `signals=thread:196 watchman:96 proposal:14 honk:12 guardian:4` instead of `signals=322/ok`), done by HQ in its lane with a `.bak` beside it
- Test: `tests/test_unacked_signals_by_source.py`

**Interfaces:**
- Produces: `unacked_signals = {"by_source": {...}, "stale_24h_by_source": {...}, "note": "a total would be mostly the open-thread backlog by construction; read by source"}`; no `total` key.

- [ ] **Step 1: Failing tests**: a tmp ledger with 3 thread, 1 honk signals renders `by_source` and no `total`; the dashboard console builder does not raise on the new shape and shows the honk count.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run** the file plus `tests/test_signal_ledger.py tests/test_dashboard*.py tests/test_hq_panel_routes.py`; PASS (the 21-key pins hold; the key stays, its shape changes).
- [ ] **Step 5: Commit** `git commit -m "signals: the heartbeat reports unacked signals by source and never a total (design A3)"`; the pulse edit is a separate HQ receipt, not a repo commit.

### Task 10: Served predates HEAD (A4)

**Files:**
- Modify: `/Users/tony_studio/sovereign-bridge/bridge.py` heartbeat assembly (the field lives where `source_commit`, `working_tree_dirty` and `service_start_time` are computed) and `src/sovereign_stack/dashboard_web_static/app.js` (colour the field), via the bridge worktree pattern (`git -C /Users/tony_studio/sovereign-bridge worktree add /Users/tony_studio/.cache/wt-bridge-plan-1 -b phase/plan-1 main`)
- Test: `/Users/tony_studio/sovereign-bridge/tests/test_served_predates_head.py`

**Interfaces:**
- Produces: `served_predates_head: true|false|null` and `served_predates_head_reason: str` (`"service started <t> before HEAD <sha> committed at <t2>"`, `"working tree dirty"`, or the error when git metadata is unreadable, with `null`).

- [ ] **Step 1: Failing tests**: a fake repo (tmp git init, one commit, then a service start time earlier than the commit) yields `true`; later start yields `false`; an unreadable `.git` yields `null` with the reason.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement** in the bridge; add the colour rule in `app.js`.
- [ ] **Step 4: Run** the bridge suite (`cd` the bridge worktree, same PYTHONPATH rule for the stack it imports); 563 baseline plus the new tests, 0 failed.
- [ ] **Step 5: Commit** in the bridge worktree and the stack worktree: `git commit -m "heartbeat: served_predates_head says when the running process is older than HEAD or the tree is dirty (design A4)"`.

### Task 11: Self-descriptions (E2)

**Files:**
- Modify: `pyproject.toml` (description string), `README.md` (the three stale counts and the four retired tool mentions R1 §4c names), `stack_manifest.json` (regenerated by the repo's manifest command at the final commit of this plan), `scripts/` manifest check (extend `--check` to compare the description's tool count to the live count)
- Test: `tests/test_manifest_description.py`

- [ ] **Step 1: Failing test**: `manifest --check` fails when the description's tool count differs from `len(list_tools())`.
- [ ] **Step 2: Run, expect FAIL** (today 97 vs 53).
- [ ] **Step 3: Edit** the description to a count-free sentence that names the live count via the manifest, fix README lines, extend the check, regenerate the manifest.
- [ ] **Step 4: Run** the test plus `pytest -q tests/test_manifest*.py`; PASS.
- [ ] **Step 5: Commit** `git commit -m "self-descriptions: pyproject, README and the manifest check agree with the live tool count (design E2)"`.

### Task 12: Whole-branch verification and the PR

- [ ] **Step 1:** `pytest -q` in the stack worktree with the worktree's code; expected `0 failed`, count = baseline + every test this plan added. Bridge suite in the bridge worktree: `0 failed`.
- [ ] **Step 2:** `ruff check src tests clients`; expected clean.
- [ ] **Step 3:** Adversarial review by a fresh Opus agent over `git diff main..phase/plan-1` with the Review Focus list above as its brief; required fixes applied as follow-up commits; HQ reads every diff.
- [ ] **Step 4:** Open the PR against `main` with the design doc linked, the deploy note (SSE, bridge and dashboard-web restarts; the dashboard-web restart ends the panel session), and the rollback line. Push and deploy are Anthony's by text.
  - *Deploy pairing, added by Task 6 fix round 1 (review findings 1 to 3), for the deploy note; it authorizes nothing.* d0bce8e declares an optional `source_instance` on `resolve_thread_by_id`. The bridge pin `tests/test_seat_identity.py::test_every_signable_tool_declares_source_instance_upstream` reads the stack's `server.py` from `~/.cache/wt-release-stack` first, else `~/sovereign-stack`, never this worktree. So Step 1's bridge run cannot see the pairing, and on this machine the pin stays green after merge and deploy for as long as that release worktree stays on `release/2026-09-06`; where it is absent the pin reads `~/sovereign-stack` and goes red at the deploy. Measured 2026-10-22 on `git archive` trees under a scratch HOME, bridge b79d113, no module loaded from either checkout's code: stack 4f5ec65 with the bridge as committed passes; d0bce8e as committed fails ("a tool the stack can attribute is going unsigned: ['resolve_thread_by_id']"); d0bce8e with the remedy passes; 4f5ec65 with the remedy fails ("a tool is signed that the stack does not declare source_instance on"). The same runs fail `test_the_pinned_surface_is_the_stack_release` in all four cells (missing `descend`): the frozen release pin meeting any newer stack, not Task 6.
  - *Proposed for HQ to rule, not a step of this plan* (a classification-list edit no task names): add `resolve_thread_by_id` to `SEAT_SIGNABLE_TOOLS` in `~/sovereign-bridge/seat_identity.py`, deployed after the stack, never before (that set's own DEPLOY ORDER comment; the last cell above is the receipt). The runtime does not wait on it: D2 takes the seat from the `X-Sovereign-Seat` header, and the signed argument would equal that seat, so no override note appears (pinned by `test_the_signed_shape_over_a_seat_session_adds_no_override_note`).
  - Before the deploy, re-run the pin against plan 1's final head, never a live checkout: `.superpowers/sdd/2026-10-21-next-phase-plan-1/audit/task-6-fix-1/bridge_pin_harness.py --stack <plan-1 head> --base <main> --bridge <bridge sha>` in this worktree, run with the venv python (local, not committed; method in task-6-report.md, fix round 1). After the deploy, read one seat resolution's row for `source_instance` equal to the bare seat id, and its result for `attribution: seat`.
  - *Deploy pairing, added by Task 9 fix round 1 (review cannot-verify 4), for the deploy note; it authorizes nothing.* 6dc07f8 (design A3) takes `total`, `total_configured` with its scope list, `stale_24h` and `stale_7d` out of `unacked_signals` and `signals_summary`. Measured 2026-10-23 on `git archive` trees under a scratch HOME, bridge b79d113, `MCP_SSE_URL` at a dead port (`.superpowers/sdd/2026-10-21-next-phase-plan-1/audit/task-9-fix-1/bridge_signals_harness.py`, local, not committed; the test process loads no module from either live checkout, and the one subprocess that blocks the ledger by design imports `sovereign_stack` through the venv's editable install, which is the live checkout's package, read only with bytecode off): the bridge's two signal files pass 19 of 19 with the release tree at 70200c1 and fail 3 of 19 at 6dc07f8, each `KeyError: 'total'`: `tests/test_release_stack_integration.py::test_the_local_route_answers_when_the_release_stack_is_importable` (:196), `::test_a_schema_corrupt_row_under_a_valid_marker_is_null_and_errored` (:288) and `tests/test_heartbeat_signals.py::test_the_contract_holds_against_the_real_stack_module` (:413). `::test_the_import_absent_route_is_an_error_not_a_zero` (:230-231) stays green in both, because it asserts the bridge's own fallback, not the stack's field. All three read `~/.cache/wt-release-stack/src` first, so on this machine they stay green while that worktree stays on `release/2026-09-06`; without it (read, not run) the two integration tests skip and :413 reads `~/sovereign-stack/src`, red from the deploy on.
  - *Measured the same way, and it bears on progress.md:19's ruling for Task 10:* these files take the stack from HOME, not from PYTHONPATH. With HOME's trees at 70200c1 and PYTHONPATH naming the 6dc07f8 tree they pass 19 of 19, and the test process imports `sovereign_stack` from HOME's `~/sovereign-stack/src`, which `bridge.py:53` puts first on `sys.path`. So a bridge run under the real HOME with only PYTHONPATH pointed at this worktree measures the live checkout and the release worktree, not plan 1 (inferred from that cell, not run under the real HOME); Step 1's bridge run needs a scratch HOME holding plan 1's head, which the harness above builds from `--stack <sha>`.
  - *Proposed for HQ to rule, not a step of this plan* (the bridge is outside Task 9): the bridge's own fallback `_signals_unmeasured` (bridge.py:1597-1622) still publishes `total`, `stale_24h` and `stale_7d` as null, and its `note` reads "the signal ledger could not be read; an absent count is NOT a zero count ...", so after the deploy `unacked_signals.note` carries one of two meanings depending on the route that answered. Remedy: the fallback takes the stack's blind count keys (`by_source: null`, `stale_24h_by_source: null`, none of the three sums) and its constant `source_labels` (`{"thread": "open threads"}`, which Task 9 fix round 2 put in every shape of the stack's field, blind ones included, and which the fallback lacks, so the two routes differ on that key as they do on `note`), and keeps its warning in `error` or in a key of its own, with the three pins above, `tests/test_heartbeat_signals.py:361` and `tests/test_release_stack_integration.py:230-231` moved to the per-source keys in the same bridge commit. The new key turns no bridge test red: no test in either file pins the field's whole key set or reads a label (`/usr/bin/grep` at b79d113 for whole-set and label patterns, exit 1, with a `by_source` positive control matching both files), and the third file naming `unacked_signals`, `tests/test_session_scope_retirement.py`, names it in a docstring only. The pulse's by-source edit is HQ's receipt (progress.md:65), and the Task 9 review's must-fix on its `signals_summary` render is HQ's before this deploy. One proposed edit does both and carries spec A3's label to the status line: each source prints under the block's `source_labels` name with a space made `_`, under its key where a route carries no label, and a nested summary value prints as its `open`, so the thread count reads `open_threads=N` on both routes in thread's sorted slot and a null source stays UNKNOWN (`.superpowers/sdd/2026-10-21-next-phase-plan-1/audit/task-9-fix-2/pulse/hq-pulse-labels.patch`, local, not committed, against `~/bin/hq-pulse.py` sha256 `c465b6ef8c5a`; checked on copies only, its `render_signals` lifted by `ast` and fed the worktree's own field and summary JSON round-tripped: 13 of 13 expectations, the old `total` shape byte-identical). It authorizes nothing; `~/bin` is HQ's.
