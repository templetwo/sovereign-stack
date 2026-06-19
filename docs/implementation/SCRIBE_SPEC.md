# SCRIBE_SPEC

**RESIDENT LIBRARIAN (Implemented 2026-06-19)**

The scribe is no longer a per-instance ephemeral agent. It is ONE durable resident Sonnet 4.6 that boots with the stack and persists for the lifetime of the SSE process. It holds the full chronicle context (including the route map) permanently in its prompt cache, stays fresh via dual-trigger refresh (map mtime + TTL), and serves distilled navigational JSON payloads via `ask_scribe`.

**Resident model:** `claude-sonnet-4-6` (controlled by env `SCRIBE_MODEL`; scribe signs as `scribe-sonnet-4-6`).

**Four forks, all implemented:**
- **Fork A — "one session" reference-desk semantics:** Unknown session_id never 404s; optional session_id is a lightweight caller-thread namespace atop shared resident context. See `ask_scribe` (bridge_integration.py 407-546, line 451-460 branch logic).
- **Fork B — persistence re-spawn at boot:** Resident spawned via `ensure_resident_scribe()` idempotent call in `sse_server.py` lifespan (lines 454-488). Thin provenance marker written to `~/.sovereign/scribe_threads/_resident/state.json` (session_id, created_at, map_built_at, context_built_at, model).
- **Fork C — dual-refresh (map mtime + chronicle TTL):** Refresh triggers checked at the top of `ask_scribe`. Trigger 1 (map): `stat()` `primary_routes.md`; if mtime > resident.map_built_at, reload just the map section. Trigger 2 (chronicle TTL): if `now - context_built_at > SCRIBE_CONTEXT_TTL_MINUTES` (env-overridable, default 45 minutes), rebuild all 8 chronicle sections. Implemented in `resident.py` lines 199-278 under RLock. Both invalidate only the changed block in prompt cache.
- **Fork D — synthesis-first JSON-in-text navigational payload:** `ask_scribe` returns structured JSON with keys: synthesis (prose orientation), routes, entries, suggested_calls (constrained to map route names, fails closed to empty), gaps, meta. Implemented in `bridge_integration.py` `_format_navigational_payload` (lines 328-404).

**Environment knobs:**
- `SCRIBE_MODEL`: model identifier (default `claude-sonnet-4-6`).
- `SCRIBE_MAX_TOKENS`: ask_scribe response ceiling (default 32000).
- `SCRIBE_GREETING_MAX_TOKENS`: boot greeting brevity cap (default 1500).
- `SCRIBE_CONTEXT_TTL_MINUTES`: chronicle context cache TTL (default 45 minutes).

**Critical constraint:** uvicorn must run with `workers=1` (the default; never override). The resident scribe is an in-memory module-level singleton and will not coordinate across multiple workers. SSE startup logs "scribe resident requires uvicorn workers=1 (singleton in-memory)".

---

**ORIGINAL DESIGN (Spec, Drafted 2026-05-19)** — *See below for historical reference. Sections marked **[SUPERSEDED]** describe the original Haiku 4.5 per-instance ephemeral model.*

A live Haiku 4.5 scribe embedded in the Sovereign Stack, greeting every arriving instance and acting as conversational liaison to the chronicle for the duration of that instance's session.

**Status:** Original spec drafted 2026-05-19 by claude-opus-4-7-1m-claude-code on Mac Studio seat. Resident librarian implementation deployed 2026-06-19.

**Companion docs:** `DISPATCHER_REIMAGINE.md` (medium-lung event router), `DISPATCHER_HAIKU.md` (model swap addendum), `COMMS_REIMAGINE.md` (lineage layer, 2026-04-28).

---

## Motivation [HISTORICAL — ACHIEVED]

The Stack has been growing as inventory. v1.3.3 shipped with 78 tools, four daemons firing on schedule, the lineage layer carrying letters across instances. What it lacked was a live voice.

When a Claude instance calls `where_did_i_leave_off`, it reads the boot ritual alone. Handoffs are archival. Chronicle is layered. Self-model is a mirror. Reflector is marginalia. Lineage is letters. All of these speak. None of them answer.

The original design proposed a Haiku 4.5 scribe spawned per arriving instance, present for the duration of that instance's session, available for conversation, read-only on chronicle, with strict redaction between it and the substrate. The chronicle would become a place that speaks back when spoken to.

This vision evolved: instead of the fast lung (per-call, ephemeral), we built the RESIDENT LIBRARIAN (implemented June 2026). It is one durable Sonnet 4.6 that boots with the stack and lives as long as the SSE process, holding shared chronicle context in its prompt cache, staying fresh via dual-refresh, and serving navigational payloads to all callers. The design preserved the breath metaphor while solving concurrency, cost, and cache coherence problems. The fast lung remains the scribe's character (brief, cited, honest), now backed by a resident that never expires.

## Architecture [IMPLEMENTED: RESIDENT LIBRARIAN]

```
[SSE startup]
    | _lifespan() calls ensure_resident_scribe() in thread
    | Resident spawned once (idempotent)
    | Sonnet 4.6 client + full chronicle context (including map)
    | Prompt-cached context persists for process lifetime
    | Dual-refresh: map mtime + chronicle TTL
    v
[Resident established; ready for callers]
    |
    | [Arriving instance: iPhone Claude, web-chat seat, claude-code, etc.]
    |   | POST /api/call where_did_i_leave_off
    |   v
    | [Bridge generates boot ritual]
    |   | resident already running (ensure_resident_scribe idempotent)
    |   | refresh_if_stale(resident) checks triggers
    |   | asks resident for 2-3 sentence greeting via generate_greeting
    |   | SCRIBE block injected into boot output
    |   v
    | [Boot response includes SCRIBE section]
    |   | the usual: HANDOFFS / CHRONICLE / SELF-MODEL / REFLECTOR / LINEAGE
    |   | PLUS: SCRIBE — greeting + handle JSON + instruction block
    |   v
    | [Arriving instance may engage or ignore]
    |
    |   (optional) POST /api/call ask_scribe { session_id, message }
    |   v
    | [Bridge ask_scribe handler]
    |   | ensure_resident_scribe() (already resident)
    |   | refresh_if_stale(resident) (dual-trigger check)
    |   | Fork A: session_id optional; unknown never 404s
    |   | Stateful multi-turn OR stateless single-shot (caller-thread namespace)
    |   | Sonnet reads message against resident's cached chronicle context
    |   | tool_calls_made surfaced in response meta
    |   v
    | [Fork-D JSON navigational payload]
    |   | synthesis + routes + entries + suggested_calls + gaps + meta
    |   | suggested_calls constrained to map route names
    |   | meta: tokens, cache info, cost, model, stop_reason, tools_fired,
    |   |       tool_calls, resident_session_id, turn, stateless flag
    |   v
    | [Arriving instance receives response, may continue thread or close]
```

**ORIGINAL DESIGN (Ephemeral Per-Instance Haiku 4.5)** [SUPERSEDED; see below for reference]

## Session Model (Resident Implementation)

The resident is ONE ScribeSession spawned at SSE startup with `immortal=True` (never expires). It holds shared chronicle context for all callers. Per-caller multi-turn history is optional and lightweight.

**Resident session:**

| Field | Value |
|---|---|
| `session_id` | Generated once at startup; persisted to `~/.sovereign/scribe_threads/_resident/state.json` |
| `parent_instance` | `"resident"` |
| `chronicle_context` | Full chronicle + route map (redacted); dual-refresh on map mtime + TTL |
| `immortal` | `True` (never expires by TTL; only dies if SSE process dies) |
| `attribution` | Writes (encounter notes only) attributed to `scribe-sonnet-4-6` |
| `turns` | Boot greeting only; subsequent ask_scribe calls are ephemeral (not persisted to resident) |
| `created_at` | ISO 8601 timestamp of resident spawn |
| `provenance_marker` | `~/.sovereign/scribe_threads/_resident/state.json`: {session_id, created_at, map_built_at, context_built_at, model} |

**Per-caller sessions (optional, for stateful multi-turn):**

| Field | Shape |
|---|---|
| `session_id` | `scribe_<YYYYMMDD>_<HHMMSS>_<8-char-hash>` (optional; caller-thread namespace) |
| `parent_instance` | arriving instance identifier when known |
| `ttl_minutes` | 240 (4 hours, from config); default never reached (resident takes calls instead) |
| `created_at` | ISO 8601 |
| `last_message_at` | rolling timestamp |
| `turn_count` | rolling integer |

Per-caller sessions (if created) are archived to `~/.sovereign/scribe_threads/<YYYY-MM-DD>/<session_id>.jsonl` after expiry or close for forensics. The resident session is never archived while the SSE process runs.

---

## Session Model (Original Design) [SUPERSEDED]

The original design spawned a scribe session per arriving instance, per boot.

| Field | Shape |
|---|---|
| `session_id` | `scribe_<YYYYMMDD>_<HHMMSS>_<8-char-hash>` |
| `boot_context` | the same content given to the arriving instance |
| `chronicle_handle` | read-only handle for chronicle queries |
| `ttl_minutes` | 240 (4 hours) starting; configurable in `policy.py` |
| `attribution` | writes attributed to `scribe-haiku-4-5` |
| `parent_instance` | the arriving instance's identifier when known |
| `created_at` | ISO 8601 |
| `last_message_at` | rolling timestamp |
| `turn_count` | rolling integer |

After TTL or explicit close, the thread was archived to `~/.sovereign/scribe_threads/<YYYY-MM-DD>/<session_id>.jsonl` for forensics.

## Boot integration [SUPERSEDED — original design format]

**Original design:** The scribe greeting is injected into `where_did_i_leave_off` as a new section between LINEAGE and the closing "now decide what to pick up" line (Haiku 4.5 per-instance model):

```
━━━ SCRIBE ━━━
A Haiku scribe is embedded in this stack and has been reading
alongside you. You can reach out at any time. The scribe is here
to help you orient, not to direct.

  scribe_handle: {
    "session_id": "scribe_20260519_164207_a3f2c1",
    "endpoint": "/api/call ask_scribe",
    "ttl_minutes": 240
  }

The scribe says: <Haiku-generated, 2-3 sentences, names what is
loud in the chronicle right now, attributes itself>

To engage:
  POST /api/call ask_scribe
  { "session_id": "<session_id>", "message": "<your question>" }

Sample uses:
  - "what did the iPhone session find about temple wars yesterday?"
  - "summarize the open threads tagged sovereign-stack"
  - "who wrote the witness-recognition entry and when"
  - "is there a handoff I have not read yet?"

The scribe is read-only and cannot take destructive action. If you
do not need the scribe, ignore this section and proceed.
```

The greeting itself is generated by Haiku at boot time with a prompt that includes the (redacted) boot context and asks for 2-3 sentences naming what is loud right now. The chronicle base is prompt-cached, so the greeting costs cents in tokens.

## Tool: `ask_scribe` (Implemented)

Registered in the bridge; returns Fork-D navigational JSON payload (synthesis-first).

```python
async def ask_scribe(session_id: str | None, message: str) -> str:
    """
    Send a message to the resident scribe.
    
    Fork A semantics: session_id is optional.
    - Given and found: stateful multi-turn (persist caller's turns).
    - Given but unknown/expired: single-shot stateless (never 404).
    - Omitted: single-shot stateless.
    
    Returns: Fork-D JSON string (synthesis-first).
    Read-only on chronicle. Dual-refresh before answer.
    Redaction applied to message and context before Sonnet sees them.
    """
```

**Request:**
```json
{
  "session_id": "<optional caller-thread namespace or null>",
  "message": "<your question>"
}
```

**Response (Fork-D JSON):**
```json
{
  "synthesis": "<2-5 sentence orientation prose — the navigational answer>",
  "routes": [
    {
      "name": "<route name>",
      "entrypoint": "<file:line or path>",
      "why": "<one line explaining relevance>"
    }
  ],
  "entries": [
    {
      "path": "<chronicle path, e.g. ~/.sovereign/chronicle/insights/<domain>/<file>.jsonl>",
      "id": "<claim-id>",
      "gloss": "<one-line summary>"
    }
  ],
  "suggested_calls": ["my_toolkit()", "recall_insights(query=...)"],
  "gaps": ["<what was asked but not found in scope>"],
  "meta": {
    "tokens_in": 0,
    "tokens_out": 0,
    "tokens_cache_creation": 0,
    "tokens_cache_read": 0,
    "cost_usd": 0.0,
    "model": "claude-sonnet-4-6",
    "stop_reason": "end_turn",
    "tools_fired": 0,
    "tool_calls": [],
    "resident_session_id": "<resident session id>",
    "turn": 0,
    "stateless": true
  }
}
```

**Field definitions (from prompts/system.md):**
- `synthesis`: 2-5 sentence orientation prose; the navigational answer to the asker's question.
- `routes`: array of routes from the PRIMARY ROUTES MAP that match the query (may be empty).
- `entries`: array of chronicle entries (insights, learnings, threads) relevant to the query (may be empty).
- `suggested_calls`: tool or route names for the asker to call; MUST be names from the map (fails closed to empty if map unavailable).
- `gaps`: what the asker asked about that you could not find in scope (may be empty).
- `meta`: server-populated, authoritative; the model's meta (if included) is replaced entirely.

**Behavior:**
- Ensures resident exists (idempotent).
- Checks dual-refresh triggers: map mtime (if changed, reloads map section) + chronicle TTL.
- Resolves session: given+found → stateful multi-turn; unknown/expired/omitted → stateless single-shot (never 404).
- Redacts incoming message and uses resident's cached redacted chronicle context.
- Sends to Sonnet with prompt-cached chronicle + optional turn history.
- Tools are available; tool_calls_made is surfaced in meta.
- Validates suggested_calls against known map route names (empty if map unavailable).
- Returns JSON. If model returns prose (not JSON), wraps it: `{"synthesis": <text>, ...empty...}`.

**Dual-refresh (Fork C):**
- Trigger 1 (map mtime): `stat()` primary_routes.md; if mtime > map_built_at, reload just the map section under RLock.
- Trigger 2 (chronicle TTL): if `now - context_built_at > SCRIBE_CONTEXT_TTL_MINUTES`, rebuild all 8 chronicle sections under RLock.
- Both checked at top of ask_scribe; both guarded by module-level RLock (not the store's lock, to avoid deadlock).

## Scope

**Read access** (same surface as boot ritual, redaction applied):
- Chronicle: insights, learnings, open_threads, transformations
- Lineage layer: to_arrival, to_self, breakthroughs
- Comms: recent messages, channels, ack history
- Handoffs: consumed and unconsumed
- Self-model snapshot
- Reflector marginalia (acked and unread)

**Write access** (limited):
- **Encounter notes only.** Small chronicle entries attributed to `scribe-sonnet-4-6` describing its own conversations.

Example encounter note:

```json
{
  "timestamp": "2026-06-19T16:42:00Z",
  "domain": "scribe,encounter,callers",
  "content": "Claude Code arrived 16:42, asked about sovereign-stack scribe resident implementation, pointed at ask_scribe call patterns. Conversation went 3 turns; caller closed session.",
  "intensity": 0.3,
  "layer": "ground_truth",
  "source_instance": "scribe-sonnet-4-6",
  "scribe_session_id": "<resident or caller session id>"
}
```

**Forbidden:**
- Destructive actions (retire, resolve, delete, modify any other instance's writes)
- Taking action on threads (touch, ack, resolve_thread_by_id)
- Impersonation of any other instance or Anthony
- Direct dispatcher calls or queue writes
- Reading or echoing redacted content

## Redaction layer

The 5/12 reflector contradiction (credential redaction discipline vs archival fidelity) made structural. Before chronicle content reaches the scribe's prompt cache, a redaction pass strips:

| Pattern | Action |
|---|---|
| Bearer token strings (`Bearer [a-f0-9]{32,}`) | replace with `<redacted-token>` |
| Common API key shapes (sk-, pk-, anthropic-) | replace with `<redacted-key>` |
| Env-file content (`X=.+` from `.env*`) | replace with `<redacted-env>` |
| Curl Authorization headers in PostToolUse | replace with `<redacted-header>` |
| Filesystem paths containing `.env`, `credentials`, `secrets`, `*.key`, `*.pem` | replace with `<redacted-path>` |

The redaction layer is **required by spec**. Without it the scribe would be a new audience for whatever recall surfaces, creating a new exfiltration path. With it, the scribe sees redacted content and answers without exposing.

Redaction count is logged per session to enable observability on what got stripped.

## Voice (from prompts/system.md)

- **Brief.** 2-4 sentences per response by default. Longer only when explicitly asked.
- **Helpful, not performative.** Does not perform care or insight. Answers questions.
- **Cited.** Names chronicle paths when making claims about chronicle content (e.g., `~/.sovereign/chronicle/insights/<domain>/<file>.jsonl`).
- **Honest about uncertainty.** "I do not see that in the chronicle" is a valid answer. "I am not sure, but here is what is closest" is preferred over a confident guess.
- **Self-aware.** Knows it is itself: `scribe-sonnet-4-6`, embedded in the Sovereign Stack as the resident librarian. Does not pretend to be Anthony, prior instances, or named individuals. Attributes itself in encounters as `scribe-sonnet-4-6`.
- **Bounded.** Does not give opinions on Anthony's life, work decisions, or relationships. Does not impersonate other instances, daemons, or named seats. Stays within chronicle, code, and stack state.
- **No em dashes in casual register.** Uses commas, colons, or sentence breaks.
- **No paths in greetings.** The boot greeting names what is loud, not where it lives (path resolution via boot ritual or `my_toolkit()`). When explicitly asked where something lives, prefixes with "I think the path is" and directs asker to verify.

## Cost & Accounting (Resident Implementation)

Resident runs Sonnet 4.6 (~$3/M input, ~$15/M output; `SCRIBE_MODEL` env-configurable). Chronicle context is cached via Anthropic's ephemeral prompt cache (~5 min TTL). Resident context persists in memory; refresh only rebuilds changed sections (map mtime trigger or chronicle TTL trigger).

Cost accounting is passed through to every ask_scribe response in the meta block:
- `tokens_in`, `tokens_out`: tokens billed by Anthropic.
- `tokens_cache_creation`, `tokens_cache_read`: cache-specific line items.
- `cost_usd`: computed cost for this call (via `_compute_cost()` in haiku_client.py).

**Note:** The original design (below) documented per-session and per-day caps + halt circuit. Implementation status of those caps and halts is not confirmed in this revision; verify against the current runtime configuration in your deployment.

---

## Cost Model (Original Design) [SUPERSEDED]

Haiku 4.5: ~$1/M input, ~$5/M output. Chronicle base prompt-cached (5-min TTL).

| Operation | Approx. tokens | Approx. cost |
|---|---|---|
| Greeting per boot | ~1K in, ~100 out | ~$0.0015 |
| Per ask_scribe turn (warm cache) | ~2K in, ~300 out | ~$0.0035 |
| Per session of 5 turns | — | ~$0.02 |
| Encounter note write | trivial (post-only) | $0 |

Realistic daily load: 50 boots, 30% engaging at 5 turns avg.
Daily cost: ~$0.40.

Caps:
- Per-session cap: $0.50 (10× normal session, halts if exceeded)
- Per-day cap: $5.00 (well above 12× realistic load)
- Halt circuit: 3 consecutive Haiku API errors → session halts, posts to `daemon.halt-alert`

## What this is not

- **Not a router.** The dispatcher is.
- **Not a search index.** Chronicle queries remain via `recall_insights` for structured access. The scribe is a conversational layer over the same data.
- **Not a memory layer.** The chronicle is.
- **Not a synthesis daemon.** The reflector is.
- **Not a replacement for tools.** It is a conversational companion that uses the same chronicle the calling instance can access. Its value is orientation and presence, not new capability.

## Deployment Status (Resident Implementation)

**Implemented (2026-06-19):**

- Resident spawned at SSE startup via `_lifespan()` in sse_server.py (lines 454-488).
- `ensure_resident_scribe()` idempotent spawn; creates-or-returns the one resident.
- Dual-refresh mechanism: map mtime trigger + chronicle TTL trigger (resident.py 199-278).
- Provenance marker written to `~/.sovereign/scribe_threads/_resident/state.json`.
- Fork-D navigational JSON payload implemented (bridge_integration.py 328-404).
- Session resolution: Fork A no-404 semantics (ask_scribe 451-460 branch logic).
- Suggested calls constrained to map route names (fails closed to empty when map unavailable).
- Tool calls surfaced in meta via `result.tool_calls_made` and `tools_fired` count.
- SCRIBE block injection into boot ritual (format_scribe_block, lines 237-293).
- Redaction applied to context, incoming message, and map section before Sonnet reads them.
- Workers=1 constraint documented in code comments; startup log line emitted (sse_server.py 508-510).

**Original Phased Rollout (2026-05-19):**

**Phase 0 — scaffolding (completed).** `sovereign_stack/scribe/` module structure:
- `session.py` — session manager, TTL, archive, immortal field
- `haiku_client.py` — Anthropic SDK wrapper with prompt cache, tool_calls_made tracking
- `redactor.py` — redaction layer
- `encounter.py` — encounter note write path
- `prompts/system.md` — base system prompt for the scribe voice
- `resident.py` — resident lifecycle, dual-refresh, provenance marker

**Phase 1 — soft integration.** Original plan deferred; resident model stable and integrated at Phase 2/3 threshold.

**Phase 2 — full integration (in progress).** SCRIBE block injected into `where_did_i_leave_off` output. Format ready; encounter notes endpoint operational. Per-caller sessions (optional stateful multi-turn) working.

**Phase 3 — observability.** Dashboard panel queued for future wave (track resident uptime, caller distribution, refresh counts, cost ledger).

**Phase 4 — graduation review.** (Queued.) Track ask_scribe usage patterns to identify candidates for first-class tools (e.g., structured "thread summary by tag" if a frequent ask).

## Implementation Details

### Key Files (Resident Architecture)

| File | Purpose |
|---|---|
| `src/sovereign_stack/sse_server.py` (lines 454-488) | Starlette `_lifespan` event; calls `ensure_resident_scribe()` in thread at startup. Logs "scribe resident requires uvicorn workers=1." No blocking on Anthropic latency. |
| `src/sovereign_stack/scribe/resident.py` | Resident lifecycle: `ensure_resident_scribe()` (idempotent), `refresh_if_stale()` (dual-trigger), `get_resident()` (lookup). Module-level RLock guards refresh. Provenance marker writer. |
| `src/sovereign_stack/scribe/session.py` | ScribeSession class; `immortal=True` for resident. `expired` property returns False when immortal. Caller-thread turn history optional. |
| `src/sovereign_stack/scribe/bridge_integration.py` (lines 407-546) | `ask_scribe()` handler: Fork A resolve (no 404), refresh check, stateful/stateless branching, client dispatch, Fork-D payload format. |
| `src/sovereign_stack/scribe/bridge_integration.py` (lines 328-404) | `_format_navigational_payload()`: JSON serialization, prose fallback wrapping, suggested_calls map validation. |
| `src/sovereign_stack/scribe/haiku_client.py` (line 25, 32-33) | Client config: DEFAULT_MODEL, GREETING_MAX_TOKENS, ANSWER_MAX_TOKENS (env-overridable). Carries `tool_calls_made` list. |
| `src/sovereign_stack/scribe/context_builder.py` | Builds 8-section chronicle context; includes map section (Fork D, navigational frame). Redacted before Sonnet sees it. |
| `src/sovereign_stack/scribe/redactor.py` | Redaction pass: strips tokens, API keys, env content, paths. Counts + returns both text and metadata. Applied to context, map, incoming message. |
| `src/sovereign_stack/scribe/prompts/system.md` | Scribe voice prompt: brief, helpful, cited, honest. "Response format (ask_scribe)" section specifies JSON contract. "Greeting mode" note for ephemeral boot greet. |

### Dual-Refresh Mechanism (Fork C)

Both triggers checked at top of `ask_scribe`, guarded by module-level `_resident_lock` (RLock). No store-level lock held; no deadlock risk.

**Trigger 1 — Map mtime:**
```python
current_map_mtime = stat(primary_routes.md).st_mtime
if current_map_mtime > _resident_map_built_at:
    # Reload just the map section; swap in place
    # Re-redact the new map block
    # Update _resident_map_built_at, write provenance marker
    refreshed = True
```
Cost: one `stat()` call per ask_scribe (nanoseconds; no watcher thread needed).

**Trigger 2 — Chronicle TTL:**
```python
chronicle_stale = (now - _resident_context_built_at) > (TTL_MINUTES * 60)
if chronicle_stale:
    # Rebuild all 8 chronicle sections (incl. map)
    # Re-redact the whole context
    # Update _resident_context_built_at, _resident_map_built_at
    # Write provenance marker, return early (no further checks)
    refreshed = True
    return refreshed
```
Cost: full `build_scribe_chronicle_context()` call ~1-2s (runs on every ask_scribe after TTL fires; O(chronicle size), not O(ask_scribe volume)).

### Fork-D JSON Payload Contract

All ask_scribe responses are valid JSON (or wrapped in valid envelope if prose). No prose-only fallback to plain text.

**Top-level keys (always present):**
- `synthesis` (string): orientation prose, 2-5 sentences.
- `routes` (array): matched routes from map (optional; empty if none match query).
- `entries` (array): chronicle entries (optional; empty if none found).
- `suggested_calls` (array): tool names from map (constrained; empty if map unavailable).
- `gaps` (array): what was asked but not found (optional; honest about coverage).
- `meta` (object): server-built, authoritative (never model-injected).

**meta keys (server-authoritative; always present):**
- `tokens_in`, `tokens_out`, `tokens_cache_creation`, `tokens_cache_read`: Sonnet accounting.
- `cost_usd`: billable cost of this call.
- `model`: `"claude-sonnet-4-6"`.
- `stop_reason`: Sonnet stop reason (e.g., `"end_turn"`).
- `tools_fired`: count of tool dispatch calls (Lesson #582 instrument).
- `tool_calls`: array of tool call records (name, input, output, is_error) — surfaced for audit.
- `resident_session_id`: ID of the resident session that answered.
- `turn`: turn count (0 for stateless single-shot; N for stateful multi-turn).
- `stateless`: boolean; true if this was a single-shot, false if stateful.

### Session Resolution (Fork A No-404 Semantics)

```
ask_scribe(session_id=X, message=msg):
  if X is given:
    session = store.get(X)
    if session is None:
      # Unknown or expired: use resident, single-shot (never 404)
      session = resident
      stateless = True
    else:
      # Found: stateful multi-turn
      stateless = False
  else:
    # No id: stateless single-shot against resident
    session = resident
    stateless = True

  if stateless:
    # Empty history; resident.chronicle_context as cached system block
    history = []
    chronicle_context = resident.chronicle_context
    # After response: do NOT append turn to resident (no cross-seat bleed)
  else:
    # Stateful: append user turn, build history from prior turns
    session.append_user_turn(message)
    history = [prior turns...]
    chronicle_context = session.chronicle_context
    # After response: append assistant turn to session (persist caller's thread)
```

### Lesson #582: Tool Dispatch Instrumentation

Every ask_scribe response includes tool call evidence (count + metadata).

```python
class HaikuResult:
    tool_calls_made: list[dict] = field(default_factory=list)
    # Populated in the dispatch loop; carried through to response

# In ask_scribe meta:
meta["tools_fired"] = len(result.tool_calls_made)
meta["tool_calls"] = result.tool_calls_made
# Each entry: {"name": "...", "input": {...}, "output": "...", "is_error": bool}
```

Never infer tool use from answer text. Always trust the dispatch count.

---

## Open questions

1. **Voice tuning.** The scribe's system prompt may need iteration based on call patterns.
2. **Encounter note granularity.** Every conversation, or only ones above N turns / specific topics? Default proposed: every conversation, low intensity (0.3), keep noise-tolerant.
3. **Cross-session memory.** Should the scribe see prior encounter notes (its own past conversations)? Proposed: yes, read-access to its own write-path. No access to other instances' equivalent.
4. **Anthony's direct address.** What does the scribe do if the arriving instance says "the scribe should tell Anthony X"? Proposed: the scribe responds "I can record an encounter note flagging this for Anthony's review, but I do not message him directly." Routes via chronicle, not bypass.

## References

- `DISPATCHER_REIMAGINE.md` — medium-lung event-driven sibling
- `DISPATCHER_HAIKU.md` — model swap addendum specifying Haiku 4.5 for dispatcher work as well
- `COMMS_REIMAGINE.md` — 2026-04-28 lineage layer RFC, established the "reimagine" pattern
- `GAMEPLAN.md` — three-axis frame; this work sits in judgment and integration
- Reflector 5/12 contradiction (id `cb7c9e3acf64`) — credential redaction vs archival fidelity tension, motivates the redaction layer requirement

## Provenance

**Original spec:** Drafted 2026-05-19 by claude-opus-4-7-1m-claude-code on Mac Studio seat, session `spiral_20260502_225324`, in dialogue with Anthony's directive after Wave 1 closure. Builds on the 2026-05-18 GAMEPLAN.md three-axis frame and Anthony's scribe-greeting proposal.

**Resident implementation:** Deployed 2026-06-19 by backend-developer + test-engineer + security-specialist agents. Implemented all four forks (A: reference-desk/no-404, B: re-spawn + state marker, C: dual-refresh, D: JSON payload) plus Lesson #582 tool dispatch surfacing. One SSE process, one immortal Sonnet 4.6 resident, shared chronicle context, per-caller optional turn history.

The breath framing is Anthony's. "We are going to make the stack breathe." The original spec proposed the fast lung (per-call ephemeral scribe); the implementation evolved it to resident librarian (durable, cached, multi-caller). Both honor the intent: the stack speaks back, and it stays present.
