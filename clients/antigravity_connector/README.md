# Antigravity Connector

A thin **stdio MCP client** for the Sovereign Stack. It spawns the local
`sovereign` server as a subprocess, performs the MCP `initialize` handshake,
and exposes `tools/list` and `tools/call` through a small CLI / importable
class. It lets an external editor — Google Antigravity / Gemini — reach the
stack over the stdio transport without embedding any of the stack's internals.

Sibling to the other cross-substrate clients in this directory
(`grok_bridge`, `openai_bridge`). Unlike those hosted OAuth bridges, this is a
local, read-through-spawn client meant to run next to a `sovereign` install.

## Provenance

The connector was authored by Gemini in an Antigravity scratch workspace and
grafted into the repo here (by Claude) so it lives in version control and does
not depend on a scratch checkout. The only change on graft was making the
binary/data paths resolve from environment instead of a hardcoded scratch venv.

## Usage

```bash
# List all tools the stack exposes
python sovereign_connector.py --list

# Call a tool
python sovereign_connector.py --call where_did_i_leave_off
python sovereign_connector.py --call recall_insights --args '{"query": "compass"}'
```

### Path resolution

The `sovereign` binary is resolved in this order:

1. `--path` argument
2. `$SOVEREIGN_BIN`
3. `sovereign` on `$PATH`
4. `./venv/bin/sovereign` relative to the repo root

The data root is resolved as: `--root` → `$SOVEREIGN_ROOT` → `~/.sovereign`.

## Files

- `sovereign_connector.py` — the connector (CLI + `SovereignConnector` class).
- `sovereign_tools.json` — captured `tools/list` manifest (82 tools, v1.5.1).
  A reference snapshot of the tool surface at graft time, not a runtime input.
