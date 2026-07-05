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
(`CLAUDE_REFRESH_TOKEN_TTL`), elevation 15min (`CLAUDE_ELEVATION_TTL`). No new
static secrets: the bridge never accepts `BRIDGE_TOKEN` (deliberately — the
master token is quadruple-duty already and must not gain a public route).

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
- **Fail-closed tiers.** `DESTRUCTIVE_TOOLS` (11) and `BASE_TOOLS` (83) were
  frozen from the live registry; a tool in neither set requires step-up. The
  registry-drift test (`tests/test_claude_bridge_tiers.py`) fails CI when the
  native registry changes, forcing explicit classification.
- **Known side effect, documented not gated:** `where_did_i_leave_off`
  consumes handoffs. Remote seats are steered to `arrive_lineage` (the
  side-effect-free door) by the existing boot guidance.

## Destructive tier (step-up required)

`set_policy`, `govern`, `supersede_insight`, `retire_hypothesis`,
`guardian_quarantine`, `guardian_baseline`, `metabolize`, `synthesize_now`,
`watch_cancel`, `watch_resample`, `open_protected_record`.

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
2. **Cloudflared ingress** — add to `~/.cloudflared/config.yml` *before* the
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
       path: /.well-known/
       service: http://localhost:3434
       originRequest:
         noTLSVerify: true
   ```

   (8100 serves nothing under `/.well-known/` — today those requests 404 at
   the FastAPI catch-all, which is exactly the #4030 retry-loop fuel; routing
   them to 3434 lets the bridge answer discovery definitively.)
3. **Restart services:**

   ```bash
   launchctl kickstart -k gui/$(id -u)/com.templetwo.sovereign-sse
   launchctl kickstart -k gui/$(id -u)/com.templetwo.cloudflared-tunnel
   ```
4. **Verify (local, then public):**

   ```bash
   curl -s http://127.0.0.1:3434/claude/info | head -c 300
   curl -s http://127.0.0.1:3434/claude/.well-known/oauth-authorization-server
   curl -si -X POST http://127.0.0.1:3434/claude/mcp | head -5   # expect 401 + WWW-Authenticate
   curl -s https://stack.templetwo.com/.well-known/oauth-protected-resource/claude/mcp
   ```
5. **Connect claude.ai:** Settings → Connectors → Add custom connector →
   `https://stack.templetwo.com/claude/mcp`. The OAuth consent page will open
   in your browser; Approve is the human gate for the initial grant.
6. **Revocation drill (recommended once):**
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

`tests/test_claude_bridge_oauth.py` (47 tests: PKCE both ends, RFC 8707 at
authorize/token/route, rotation + reuse family revocation, DCR pinning,
single-use/TTL codes, revocation endpoint, discovery), plus
`test_claude_bridge_mcp_gate.py`, `test_claude_bridge_elevation.py`,
`test_claude_bridge_routes.py` (routing, rate limits, 401 shape,
openai/grok-unaffected guards), `test_claude_bridge_tiers.py` (fail-closed +
registry drift guard), and `clients/claude_bridge/_smoke_test.py` (manual,
offline-safe). Pre-existing failures NOT from this branch: 2 in
`tests/test_boot_ritual.py` (protected-drawer boot-line tests, fail on clean
origin/main too — environment-tied).
