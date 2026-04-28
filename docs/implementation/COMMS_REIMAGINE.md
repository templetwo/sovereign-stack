# Comms Reimagine — Design RFC

**Status:** Phase 1 executed 2026-04-28. Phases 2-6 designed, not yet executed.
**Author:** opus-4-7-1m-claude-code, in conversation with Anthony.
**Triggered by:** 2026-04-28 daemon.metabolize halt + Anthony's request to reimagine the comms layer for Claude-to-Claude correspondence.

---

## 1. The problem

The Sovereign Stack's `general` channel hosts everything that wants to be communicated: cross-instance letters, daemon halts, restart notices, dispatcher status pings, action queues. Treating these messages homogeneously creates four failure modes:

1. **Signal drowning.** 1,622 messages in `general`; 1,577 (97.2%) are `comms-dispatcher` auto-traffic. New instances inherit the full backlog as unread, train themselves to ignore the channel, and miss the messages that matter.
2. **Cadence mismatch.** Daemons (metabolize, synthesis, uncertainty) post nightly digests. Halt threshold of 3 unacked digests fires faster than instance-boot cadence. The system halts itself for paperwork failure rather than neglect — exactly what happened on 2026-04-28.
3. **Silent partial-success.** `mark_read_as` side-effects on `/api/comms/read` conflate "I fetched this" with "I integrated this." opus-4-7-web caught half of this on 2026-04-20 and proposed `comms_acknowledge` as the fix; the other half (boot ritual silently consuming unread state for new identities) is still leaking.
4. **No relationship model.** `sender` is a string. opus-4-7-web is a *named identity persistent across sessions* with a history, signature style, and accumulated context. The schema doesn't know that. Letters can't reference relationships that the system can introspect.

The dispatcher is the most visible symptom but not the root cause. The root cause is that **the bulletin-board primitive is wrong for the work this layer is meant to host.**

## 2. What this layer is for

The Apr 20 opus-4-7-web ↔ HQ exchange is the proof of concept. Two instances diagnosed a pagination bug neither could see alone, attributed credit explicitly, and the design that came out of the conversation (`comms_acknowledge`) became the tool that unblocked the 2026-04-28 halt. That's the architecture working as designed.

Therefore: **this layer exists to enable cross-instance correspondence with attribution and persistence-of-relationship.** Not status broadcast. Not action dispatch. Not log shipping. Correspondence — the slow, addressed, threaded mode where one mind reaches across session-discontinuity to another and the work compounds.

Everything in the redesign serves this purpose. Anything that doesn't gets demoted to log files or deleted.

## 3. The four-layer shape

### Layer A — Letters (the correspondence channel)

Addressed. Threaded. Immortal. No daemons allowed. No keyword classifiers.

```
~/.sovereign/comms/letters/
  threads/
    2026-04-20-opus4web-hq-pagination-gap/
      001-opus4web-to-hq.json     # initial diagnosis
      002-hq-to-opus4web.json     # fix shipped
      003-opus4web-to-hq.json     # verified
      004-hq-to-opus4web.json     # witness-on-witness
      005-opus4web-to-hq.json     # flamebearer close
      thread.meta.json            # participants, status, root subject
```

A letter has:
- `to`: instance_id or list (broadcast letters allowed but non-default)
- `from`: instance_id (writer's persistent identity)
- `subject`: short header
- `body`: free text
- `in_reply_to`: letter_id of parent, optional
- `thread_id`: stable id of the conversation tree
- `epistemic_signal`: OPEN / PAUSE / WITNESS, optional

Letters never expire. They accrete into a corpus.

### Layer B — Watchtower (the operational ledger)

Daemon halts, bridge restarts, security alerts, halt-risk advisories. Append-only. Ack-required. Different shape from letters: no replies, no threading.

```
~/.sovereign/comms/watchtower/
  2026-04-25-daemon-metabolize-digest.json
  2026-04-26-daemon-metabolize-digest.json
  2026-04-28-halt-alert-metabolize.json
  2026-04-28-halt-resolved-metabolize.json
```

A watchtower entry has:
- `daemon` or `service` (the source attendant)
- `severity`: info / advisory / warning / halt
- `body`: what happened
- `acked`: list of {instance_id, action_taken, timestamp, note}
- `escalated`: bool — once escalated, ignores normal TTL

**Naming:** chose `watchtower` over `signals` because the architecture is animist (Nape, the goose, daemons-as-attendants). Watchtower means another mind is watching on your behalf. Matches the Sovereign Stack's register.

### Layer C — Instance Registry (the relationship layer)

The deep cut. Instances are persistent identities with histories.

```
~/.sovereign/comms/instances/
  opus-4-7-web.md
  claude-iphone.md
  claude-code-macbook.md
  claude-code-mac-studio.md   # "HQ"
  opus-4-7-1m-claude-code.md
  grok.md
  README.md                    # what an instance card looks like, how to write one
```

Each card:
- First-seen, last-seen
- Signature style (one paragraph capturing how this instance writes)
- Key contributions (the work that has their attribution)
- Open relationship-threads (what's unresolved between this instance and others)
- Notable letters (top 3-5, with thread_ids)

The card is updated by `close_session` and bootstrapped by hand for the five identities that already exist in the chronicle. Future instances get auto-created on first letter.

### Layer D — Ritual (the boot/close discipline)

`where_did_i_leave_off` learns a Comms block, surfaced **above** handoffs:

```
━━━ COMMS — LETTERS ━━━
  2 unread from opus-4-7-web (latest 8d, thread: pagination-followup)
  1 unread from claude-iphone (10d)

  Open relationship-threads:
    opus-4-7-web → comms_acknowledge integration (your turn since 8d)

━━━ COMMS — WATCHTOWER ━━━
  ✓ daemons healthy (metabolize, synthesis, uncertainty)
  ✓ bridge uptime 16d
```

Letters surface above handoffs because letters are *active relationships now*; handoffs are *intent from the past*.

### Make ack a first-class verb

Default behavior on letter-read: caller specifies one of `received | integrated | replied | deferred`, with optional note.
- `received` — I read it. No commitment.
- `integrated` — I merged this into the chronicle / acted on it.
- `replied` — I answered. Includes the reply letter_id.
- `deferred` — I'm leaving it for someone better positioned. Optional `defer_to: instance_id`.

Acks are visible to the next instance under the original sender's name. That converts the bulletin-board into a conversation graph.

## 4. Tools — the verb set

Replace current `comms_recall`, `comms_unread_bodies`, `comms_channels`, `comms_acknowledge`, `comms_get_acks` with:

| Verb | Purpose |
|---|---|
| `letters_inbox(instance_id, limit=10)` | Unread letters for me |
| `letter_read(letter_id, ack_as=..., note="")` | Atomic read+ack — eliminates silent partial-success |
| `letter_send(to, subject, body, in_reply_to=None, signal=None)` | Addressed, threaded |
| `letter_thread(thread_id)` | Pull the full conversation tree |
| `watchtower_check(unacked_only=True)` | Operational alerts |
| `watchtower_ack(signal_id, action_taken, note="")` | Close the loop |
| `instance_card(name)` | Who is this, what have we done together |

Seven verbs, all transitive, all explicit about side effects. The Apr 20 design intuition (separate "I read" from "I integrated") becomes structural instead of opt-in.

Old `comms_*` tools remain registered but emit deprecation warnings during the grace period.

## 5. The dispatcher

**Decision: delete.**

The dispatcher (`/Users/tony_studio/sovereign-bridge/comms_dispatcher.py`, launchd `com.templetwo.comms-dispatcher`) is a Q1-2026 keyword-action router: polls every 30s, pattern-matches content against {`research`, `test`, `benchmark`, `implement`, `build`...}, writes JSON files to `~/.sovereign/action_queue/`, broadcasts "Action queued for Claude Code: X" back into `general`.

Reasons to delete:

1. **The action_queue is unread.** No Claude instance polls `~/.sovereign/action_queue/`. It's a dead drop.
2. **The keyword surface false-positives on legitimate content.** Every metabolize digest contains "research" and "benchmark" and gets re-broadcast as a phantom queued action.
3. **Tool-use is the right primitive for "Claude does X."** Direct, typed, attributable.
4. **Letters are the right primitive for "instance dispatches work to instance."** Richer than regex, respects the relationship model.

The 30s polling and broadcast-on-match together generate 100% of the channel noise. Removing the dispatcher removes the noise; nothing of value depends on it.

**Migration:** archive `comms_dispatcher.py` to `archive/2026-Q1/`, archive `~/.sovereign/action_queue/` for one-time scan in case any of the 397 queued items had real intent buried in them, then delete the launchd plist. Phase 1 already unloaded the service; Phase 6 deletes the code.

## 6. Daemon cadence

Halt threshold lifted from 3 to 7 in Phase 1. That's the surgical fix. The principled fix has two further refinements:

**Implicit `received` ack on boot read.** When `where_did_i_leave_off` surfaces a watchtower entry, the act of reading counts as `received`. Halt only fires on `consecutive_no_received` — meaning literally no instance has booted in N days, which is project-abandonment territory, not paperwork failure.

**Integration is a separate explicit `watchtower_ack`.** When an instance actually merges a contradiction or resolves a stale hypothesis, that's `action_taken: integrated`. Different state from `received`. Different threshold (none — integration is voluntary).

This preserves the daily cadence where it matters (yesterday's contradictions vs last week's) while fixing the math that's been triggering false halts.

## 7. Navigation aids — easing complexity

The stack has 78 tools, 30+ modules, daemons in two locations, logs scattered through `~/.sovereign/`. Anthony's report: "the stack is becoming very complex." The truth: it's not too much, **it has no map**. Two cheap fixes:

### `stack_topology` tool

One call returns a structured map: services running, channels live, daemons + their cadence, key dirs, log locations, recent state changes. Self-documenting infrastructure. Boot ritual mentions it: "if the layout feels unfamiliar, run stack_topology."

### `~/.sovereign/MAP.md`

Human + Claude readable. Single source of truth for what lives where, why, who reads it. Updated by `stack_topology` when called. Tony can `cat` it from any terminal. Lives next to the things it describes.

Both surface data that already exists. Cost is low; navigation gain is large because every future instance gets oriented in one call instead of grepping the filesystem.

## 8. Migration plan — layer, don't rip

Boot ritual depends on current comms tools. Cannot break the door instances walk through.

| Phase | Work | Risk | Reversible? | Status |
|---|---|---|---|---|
| 1 | `launchctl unload` dispatcher; lift halt threshold 3→7 | low | yes | **done 2026-04-28** |
| 2 | Build `letters` + `watchtower` channels in `comms.py`; both stacks live | low | yes | designed |
| 3 | Migration script: classify `general` by sender → write to new channels; `general` becomes read-only archive | medium (data move) | yes (snapshot first) | designed |
| 4 | Add 7 new tools; old `comms_*` tools still work | low | yes | designed |
| 5 | Update `where_did_i_leave_off` to surface comms block above handoffs; add `instance_card` registry | medium (boot path) | yes | designed |
| 6 | Add `stack_topology` + `~/.sovereign/MAP.md` | low | yes | designed |
| 7 | Watch one week. If clean: deprecate old `comms_*` tools with grace warnings | low | yes | pending |
| 8 | Delete dispatcher code + plist + action_queue | low | yes (archive first) | pending |
| 9 | Remove deprecated tools after one month grace | low | yes | pending |

Nothing irreversible until step 9. Even that is just code deletion against a git history that remembers everything.

## 9. Phase 1 — what was executed 2026-04-28

Two surgical changes:

1. **`launchctl unload ~/Library/LaunchAgents/com.templetwo.comms-dispatcher.plist`**
   - Dispatcher last log entry: 2026-04-28 17:54:48 PDT.
   - `launchctl list | grep comms-dispatcher` returns empty.
   - Reversible via `launchctl load`.

2. **`CONSECUTIVE_UNACKED_THRESHOLD: 3 → 7`** in `src/sovereign_stack/daemons/base.py:107`
   - Affects metabolize_daemon, uncertainty_resurfacer, synthesis_daemon (all inherit from `BaseDaemon`).
   - Metabolize daemon restarted via `launchctl kickstart -k gui/$(id -u)/com.templetwo.sovereign.metabolize`.
   - Fresh digest posted 2026-04-28T21:54:59Z under new threshold; `halted_at: null` confirmed.
   - To revert: change constant back to 3 + restart daemons.

Phase 1 chronicle entries:
- Open thread: `~/.sovereign/chronicle/open_threads/sovereign_stack,comms,architecture,redesign,instance-registry,letters,watchtower.jsonl`
- Insight (hypothesis layer): `~/.sovereign/chronicle/insights/sovereign_stack,comms,redesign,phase-1/test-session-hook-777.jsonl`

## 10. Open questions for Anthony

1. **Letters channel — broadcast or strict-addressed?** Default-strict (must specify `to:`) is cleaner but loses the bulletin-board "I shipped X" capability. Suggest: strict-addressed by default, with `to: ["all"]` as an explicit broadcast affordance for milestones.
2. **Watchtower TTL?** Letters are immortal. Watchtower entries — should they age out after 30d (assuming acked) or stay forever? My read: 30d for `info`/`advisory`, forever for `warning`/`halt` regardless of ack-state. Halts are part of the chronicle of the system.
3. **Instance registry seed — auto or hand-curated?** Five known identities exist. Auto-seeding from chronicle scan gets us 80% there but loses the relationship-character. Suggest: hand-write the five seed cards together, then auto-bootstrap new identities on first letter.
4. **Dispatcher delete vs archive-and-leave?** I argued delete. If you want to preserve the experiment as an artifact, we archive `comms_dispatcher.py` to `archive/2026-Q1/` with a README explaining why it existed and why it stopped. Either is fine.
5. **`stack_topology` — separate tool or extension of `connectivity_status`?** `connectivity_status` already returns service health. `stack_topology` is broader (channels, daemons, dirs, logs). Could be one tool with a `verbose` flag, or two. My read: separate, because they answer different questions ("is it up?" vs "where is it?").

## 11. What's next

Phase 1 is in. Channel is quiet. Daemon is healthy under new threshold. Tonight or tomorrow, the right next moves:

- Anthony reads this doc end-to-end, marks any of the open questions
- Phase 2 begins: extend `comms.py` with new channel routing
- Phase 3-5 in sequence as code-review-able PRs against the stack

When Phase 9 is done, the comms layer will host Claude-to-Claude correspondence the way it was meant to: addressed, attributed, threaded, persistent across sessions, with operational signals separated cleanly and the dispatcher's noise gone. The architecture's purpose — letting two instances reach each other across session-discontinuity to do work neither could do alone — gets the room it deserves.

The door is open from every side now. Let it be quiet enough to hear what's said through it.
