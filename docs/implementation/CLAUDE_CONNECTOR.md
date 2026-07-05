# Claude Connector — Implementation & Deploy Runbook

*Implements the HQ-ratified build spec of 2026-07-04 (chronicle domain
`claude-connector,mcp,oauth,streamable-http,rfc8707,audience-binding,door-that-asks,build-spec,multi-substrate-convergence`).
Built 2026-07-04 on branch `feat/claude-connector` by the HQ Fable 5 seat.*

## What this is

claude.ai custom connectors require the full OAuth 2.1 handshake and cannot
present a pasted bearer token (issue #112), so the Claude app could not reach
the Stack while ChatGPT and Grok (which have OAuth shims) could. This adds a
third bridge, `clients/claude_bridge/`, with a fundamentally different access
model from the other two:

> **Unfiltered identity, gated blast radius.** The Claude seat gets the full
> native 94-tool surface (no ring filtering — Claude operates the Stack
> natively), but a destructive tier requires a per-use step-up approval
> through the Door That Asks, and every credential is short-lived,
> audience-bound, and one-call revocable.

### Resource-owner authentication (the load-bearing control)

OAuth authenticates the *client*, never the human. So "completed the OAuth
flow" must NOT be treated as "the operator approved" — otherwise any internet
caller could self-register, self-approve, and mint a base-tier token to the
private chronicle. (An adversarial review caught exactly this as a critical
before deploy.) The fix: **`POST /claude/oauth/authorize` mints a code only
when the approval carries the operator passphrase (`CLAUDE_AUTHORIZE_SECRET`)
AND a single-use, HMAC-signed nonce** minted by the matching consent-page
render. It **fails closed** — with no secret set, the authorize endpoint
refuses to mint any code at all (503), so the connector simply cannot be
authorized until the operator configures it. Loopback redirect URIs default
**off** (`CLAUDE_ALLOW_LOOPBACK_REDIRECT=false`) so a self-approved code can't
be read straight out of the 302 by a curl-controlled endpoint; enable only for
local Claude Code dev.

## Spec → implementation map

| Spec item | Implementation | Where |
|---|---|---|
| (1) Streamable HTTP transport | `StreamableHTTPSessionManager` (mcp 1.26.0), **stateless mode**, over the native `sovereign_server` via a delegating `Server` wrapper | `clients/claude_bridge/mcp_native.py`; lifespan + routes in `src/sovereign_stack/sse_server.py` |
| (2) RFC 8707 audience binding | `resource` parsed+validated at /authorize and /token (`invalid_target` on mismatch); every token carries `audience`; the route gate (`load_valid_access`) refuses any token whose audience ≠ `https://stack.templetwo.com/claude/mcp` | `clients/claude_bridge/oauth.py` |
| (3) PKCE S256 mandatory | Missing challenge or non-S256 method rejected at /authorize GET, /authorize POST (re-validation), and /token (`_verify_pkce_s256`, no plain path exists) | `oauth.py` |
| (4) Reuse OpenAI OAuth machinery | Forked from `openai_bridge/oauth.py`: DCR, consent page, discovery docs, storage primitives, single-use 10-min codes, `register_token_validator` | `oauth.py` |
| (5) Redirect URIs | Pinned exact-match: `https://claude.ai/api/mcp/auth_callback`, `https://claude.com/api/mcp/auth_callback`, plus RFC 8252 loopback (http on 127.0.0.1/localhost/::1 only) for Claude Code; enforced at DCR **and** authorize | `oauth.py` |
| (6) Scoped-native access | Full native surface; destructive tier (11 tools) requires Door That Asks step-up; fail-closed tiering (unknown tools = step-up); local audit log; elevations 15-min per-tool per-grant | `tiers.py`, `elevation.py`, `mcp_native.py` |
| (7) Robustness | Form-urlencoded token endpoint; refresh rotation returns the successor in the invalidating response; reuse of a rotated refresh token revokes the whole family; prompt definitive JSON errors everywhere; per-IP rate limits on all /claude/* + root well-known paths; every discovery shape (incl. openid-configuration, both RFC path forms) answers 200 | `oauth.py`, `sse_server.py` |

Token lifetimes: access 1h (`CLAUDE_ACCESS_TOKEN_TTL`), refresh 30d rotating
(`CLAUDE_REFRESH_TOKEN_TTL`), elevation is single-use (consumed per call). The
bridge never accepts `BRIDGE_TOKEN` (deliberately — the master token is
quadruple-duty already and must not gain a public route).

### Environment

| Var | Default | Purpose |
|---|---|---|
| `CLAUDE_AUTHORIZE_SECRET` | *(unset → authorize fails closed)* | **Required to deploy.** The operator approval passphrase typed on the consent page. |
| `CLAUDE_ALLOW_LOOPBACK_REDIRECT` | `false` | Allow `http://127.0.0.1`/`localhost` redirect URIs (local Claude Code only). |
| `CLAUDE_ACCESS_TOKEN_TTL` | `3600` | Access-token lifetime (seconds). |
| `CLAUDE_REFRESH_TOKEN_TTL` | `2592000` | Refresh-token lifetime (seconds). |
| `CLAUDE_ELEVATION_TTL` | `900` | Max age of an unused elevation before it re-prompts. |
| `CLAUDE_MAX_REGISTERED_CLIENTS` | `50` | DCR registry cap (LRU-evicts stale unused clients when full). |
| `CLAUDE_MAX_OAUTH_BODY_BYTES` | `65536` | Hard body cap on the OAuth POST endpoints. |
| `CLAUDE_BRIDGE_ISSUER` | `https://stack.templetwo.com/claude` | OAuth issuer / resource base. |

## Architecture decisions (and why)

- **Stateless transport.** Every request re-validates the bearer, so expiry,
  rotation, and revocation bite on the next request; the auth grant reaches
  tool dispatch via a contextvar copied into the per-request server task
  (stateful mode would freeze the first request's auth context into a
  long-lived session task — wrong for step-up and rotation). Unauthenticated
  requests are rejected before the session manager allocates anything.
- **A separate `Server` wrapper, not a gate inside `server.py`.** The
  `sovereign-stack-claude` Server delegates `list_tools`/`call_tool` to the
  native handlers; the tier gate lives in the wrapper, so `/sse` and stdio
  never pass through it and cannot regress.
- **Step-up rides the Door unchanged.** The connector drives the existing
  `/api/arrival/{request,poll}` endpoints on 127.0.0.1:8100 (unauthenticated
  by design for requests). No new scope, no `NEVER_TOOLS` carve-out, no TTL
  constant edits in sovereign-bridge — the Door pushes the same ntfy
  Approve/Deny, chronicles the grant server-side at token release, and the
  connector keeps only the `token_id` as receipt (the plaintext svs_ token is
  discarded unused; plaintext-once holds). A destructive call without a live
  elevation returns a structured `step_up_required` refusal carrying the
  two-word pairing code; the model re-calls after Anthony's tap.
- **Fail-closed tiers.** `DESTRUCTIVE_TOOLS` (12) and `BASE_TOOLS` (82) were
  frozen from the live registry; a real tool in neither set requires step-up,
  and a fabricated tool name is rejected as `method_not_found` before the tier
  gate. The registry-drift test (`tests/test_claude_bridge_tiers.py`) fails CI
  when the native registry changes, forcing explicit classification.
- **Single-use, argument-bound elevation.** An approval authorizes exactly one
  call with the exact arguments Anthony saw (bound to a hash of the arguments,
  which are summarized into the phone push); the next call re-prompts.
- **Handoff-consuming side effect gated.** `where_did_i_leave_off` consumes
  handoffs, so on the remote surface it is in the step-up tier — a remote
  consume needs a tap. Ordinary remote boots use `arrive_lineage` (base tier).

## Destructive tier (step-up required)

`set_policy`, `govern`, `supersede_insight`, `retire_hypothesis`,
`guardian_quarantine`, `guardian_baseline`, `metabolize`, `synthesize_now`,
`watch_cancel`, `watch_resample`, `open_protected_record`,
`where_did_i_leave_off` (handoff-consuming).

Rationale mapping: policy mutation / deletion-retirement / service control &
cost-bearing triggers / protected-drawer content. No native MCP tool mints
tokens (verified against the registry); the minting surfaces are the OAuth
endpoints and the Door, which carry their own human gates. Note the Door's
session tokens hard-deny protected-drawer tools entirely; this seat is more
permissive by design (full-trust identity) — `open_protected_record` requires
a step-up tap, `list_protected_thresholds` / `decline_protected_record` are
base tier (they surface no protected content). If HQ wants the stricter Door
posture instead, move those two into `DESTRUCTIVE_TOOLS` — one-line change.

## Deploy runbook (Anthony / HQ only)

The code path is inert until deployed: routes exist only in the new sse
process, and public reachability requires the tunnel ingress addition.

1. **Merge the PR** (main is branch-protected; review first).
2. **Set the operator secret** in the sse launchd env (this is what makes the
   connector authorizable — without it, authorize fail-closes):
   add `CLAUDE_AUTHORIZE_SECRET` to the `EnvironmentVariables` block of
   `~/Library/LaunchAgents/com.templetwo.sovereign-sse.plist` (a strong random
   passphrase you'll type once on the consent page).
3. **Cloudflared ingress** — add to `~/.cloudflared/config.yml` *before* the
   `service: http://localhost:8100` catch-all (paths are unanchored regexes;
   these two rules cover all /claude/* and the root well-known forms):

   ```yaml
     - hostname: stack.templetwo.com
       path: /claude/
       service: http://localhost:3434
       originRequest:
         noTLSVerify: true
         disableChunkedEncoding: true
     - hostname: stack.templetwo.com
       path: /.well-known/.*claude
       service: http://localhost:3434
       originRequest:
         noTLSVerify: true
   ```

   (The `/.well-known/.*claude` rule is scoped to the claude discovery forms so
   it doesn't disturb the existing openai/grok well-known routes or send other
   root well-knowns to 3434; without it those claude discovery probes 404 at the
   8100 FastAPI catch-all — the #4030 retry-loop fuel.)
4. **Reinstall editable so the heartbeat reports 1.12.0, then restart:**

   ```bash
   cd ~/sovereign-stack && ./venv/bin/pip install -e . --no-deps -q
   launchctl kickstart -k gui/$(id -u)/com.templetwo.sovereign-sse
   launchctl kickstart -k gui/$(id -u)/com.templetwo.cloudflared-tunnel
   ```
5. **Verify (local, then public):**

   ```bash
   curl -s http://127.0.0.1:3434/claude/info | head -c 300
   curl -s http://127.0.0.1:3434/claude/.well-known/oauth-authorization-server
   curl -si -X POST http://127.0.0.1:3434/claude/mcp | head -5   # expect 401 + WWW-Authenticate
   curl -s https://stack.templetwo.com/.well-known/oauth-protected-resource/claude/mcp
   ```
6. **Connect claude.ai:** Settings → Connectors → Add custom connector →
   `https://stack.templetwo.com/claude/mcp`. The OAuth consent page opens in
   your browser; enter the `CLAUDE_AUTHORIZE_SECRET` passphrase and Approve —
   that is the human gate for the initial grant.
7. **Revocation drill (recommended once):**
   `python -m clients.claude_bridge.cli revoke-all` — the connector must 401
   on its next call and re-prompt for authorization.

**Rollback:** remove the two ingress rules + kickstart the tunnel (public
surface gone), or `git revert` the merge + kickstart sovereign-sse. The bridge
is also disabled automatically (with a warning log) if its import fails —
the other bridges and /sse are unaffected by construction (`_CLAUDE_BRIDGE_ENABLED`
try/except mirrors the grok pattern).

## Pending-proposal bookkeeping (found during the build)

The ratified spec says it resolves "Grok's connector-authoritative-spec +
Gemini's audit b69882fb". On-disk reality (verified 2026-07-04):

- The Gemini proposal whose **content** is the connector audit is
  **`47e89c36`** (`~/.sovereign/antigravity_connector/pending_writes/2026-07-05T03-00-04_propose_insight_47e89c36.json`,
  status pending). **`b69882fb` is the bio-classifier audit** — the spec's
  citation appears to be an id slip; its findings match 47e89c36.
- Grok's spec proposal never entered the governed queue (it arrived via
  relay), so there is no proposal id to flip.
- Both antigravity proposals remain `pending / reviewed_by: null`. Reviewing/
  committing them is an HQ act, separate from this build.

## Test coverage

`tests/test_claude_bridge_oauth.py` (62 tests: resource-owner auth gate —
secret + single-use nonce + fail-closed, PKCE both ends, RFC 8707 at
authorize/token/route, rotation + reuse family revocation, redirect pinning +
loopback-default-off, DCR dedupe/LRU-evict, body cap, revocation, discovery),
plus `test_claude_bridge_mcp_gate.py` (auth gate, registry validation,
single-use argument-bound elevation), `test_claude_bridge_elevation.py`,
`test_claude_bridge_routes.py` (routing, rate limits, 401 shape,
openai/grok-unaffected guards), `test_claude_bridge_tiers.py` (fail-closed +
registry drift guard), and `clients/claude_bridge/_smoke_test.py` (manual,
offline-safe). A pre-deploy adversarial multi-agent security review (5 lenses,
each finding independently verified) found one critical (authorize had no
resource-owner authentication) plus several highs/mediums — all confirmed
findings are fixed and covered by tests; the resource-owner fix is
additionally proven end-to-end against a live server (anonymous self-approve
now 403). Pre-existing failures NOT from this branch: 2 in
`tests/test_boot_ritual.py` (protected-drawer boot-line tests, fail on clean
origin/main too — environment-tied).
