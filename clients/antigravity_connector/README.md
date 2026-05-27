# Antigravity Connector

A **ring-governed stdio MCP client** for the Sovereign Stack. It spawns the
local `sovereign` server as a subprocess, performs the MCP `initialize`
handshake, and exposes a **scoped** `tools/list` / `tools/call` through a small
CLI / importable class. It lets an external editor — Google Antigravity /
Gemini — reach the stack over stdio under the same governance as the hosted
Grok and ChatGPT bridges.

Sibling to the other cross-substrate clients in this directory
(`grok_bridge`, `openai_bridge`). Unlike those hosted OAuth bridges, this is a
local client meant to run next to a `sovereign` install — but it enforces the
same ring scope, because the agent driving it (Gemini) is an external substrate.

## Governance — the ring scope

The scope is the **canonical ring system** in `bridge_core.rings`, identical for
every external substrate reaching in (Grok, ChatGPT, Gemini, future):

- **Ring 1 (reads)** — proxied to the spawned `sovereign`. `verify_proposal` and
  `list_bridge_proposals` are Ring 1 reads served locally against this
  connector's own pending-writes queue.
- **Ring 2 (writes)** — `propose_insight`, `propose_learning`, `handoff`,
  `record_open_thread`, `comms_acknowledge`, … never touch the chronicle. Each
  creates a **pending proposal** under
  `~/.sovereign/antigravity_connector/pending_writes/` awaiting Anthony's
  approval. The call returns `PROPOSAL CREATED … status=pending`.
- **Ring 3** — everything else; refused with
  `'<tool>' is not in the gemini-antigravity bridge tool surface.`

A narrated write is not a write: confirm a Ring 2 proposal landed with
`verify_proposal(proposal_id=…)` before treating it as done.

**The Claude exemption.** If `--substrate` names a Claude-family model,
`is_full_trust` short-circuits governance: the full 82-tool surface, every call
proxied straight through. Trust is the infrastructure — Claude operates the
Stack natively, not through an airlock. Every non-Claude substrate is ringed.

There is no OAuth door here (unlike the SSE bridges); the trust boundary for the
*transport* is local filesystem access. Ring scope is still enforced for the
*agent* driving the connector.

## Provenance

The connector was authored by Gemini in an Antigravity scratch workspace and
grafted into the repo by Claude (paths made env-resolvable). The ring-governance
layer (`bridge_setup.py`, the canonical `bridge_core.rings`, the Claude
exemption, and the grok/openai unification onto canonical) was added by Claude
on HQ, 2026-05-27.

## Usage

```bash
# List the governed tool surface (~41 tools; Gemini default)
python sovereign_connector.py --list

# Ring 1 read — proxied to the stack
python sovereign_connector.py --call where_did_i_leave_off
python sovereign_connector.py --call recall_insights --args '{"query": "compass"}'

# Ring 2 write — creates a pending proposal, does NOT write
python sovereign_connector.py --call propose_insight \
  --args '{"domain":"gemini-antigravity","content":"…","layer":"hypothesis"}' \
  --source-instance gemini-antigravity-20260527

# Claude full-trust (bypasses ring governance — full 82-tool surface)
python sovereign_connector.py --list --substrate claude-opus-4-7
```

### Flags

- `--substrate` — declared substrate (default `gemini-antigravity`). Claude-family
  substrates are full-trust; all others are ring-governed.
- `--source-instance` — attribution string for Ring 2 write proposals.
- `--path` — `sovereign` binary: `--path` → `$SOVEREIGN_BIN` → `sovereign` on
  `$PATH` → `./venv/bin/sovereign` relative to the repo root.
- `--root` — data root: `--root` → `$SOVEREIGN_ROOT` → `~/.sovereign`.

## Files

- `sovereign_connector.py` — the connector (CLI + `SovereignConnector` class).
- `bridge_setup.py` — ring-governance: registers the `gemini-antigravity`
  `BridgeContext`, filters the tool surface, and routes calls through
  `bridge_core`'s membrane (Ring 1 proxy / Ring 2 proposal / Ring 3 refuse /
  Claude full-trust bypass).
- `sovereign_tools.json` — captured `tools/list` manifest (82 tools, v1.5.1).
  A reference snapshot of the full surface at graft time, not a runtime input.
