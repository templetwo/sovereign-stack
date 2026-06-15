# Rollback — v1.7.2 (lived-ground-truth)

Back-out plan, written and confirmed **before** deploy (per web-seat review,
2026-06-15). The change is purely additive (a vantage-keyed early-return in two
soft flags + optional named fields), so the revert is clean: no field semantics
changed, no data migration, existing entries never used the new fields.

## Trigger

Roll back if **either** holds after deploy:

- the **negative case fails** — a non-lived / absent-vantage `ground_truth`
  entry at `intensity>=0.9` with no receipt **stops** drawing the
  unreceipted-ground-truth honk (this would mean the exemption widened into the
  ruling-1 loophole). This is a STOP, not a partial win.
- anything else misbehaves (heartbeat wrong, dispatch perturbed, daemon errors).

## Back-out steps (target: 1.7.1)

Pre-merge `main` SHA = **4007a5df98e915a519076fa8abf7068e446f96e5**

```bash
cd ~/sovereign-stack
# 1. restore code
git checkout main
git reset --hard 4007a5df98e915a519076fa8abf7068e446f96e5      # if the merge was NOT pushed
#   — or, if already pushed —
git revert --no-edit <merge_sha> && git push origin main       # additive ⇒ clean revert

# 2. refresh version metadata back to 1.7.1
./venv/bin/pip install -e . --no-deps

# 3. restart the services so the reverted code runs (same dance as the #2276 fix)
launchctl bootout   gui/$(id -u)/com.templetwo.sovereign-sse
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.templetwo.sovereign-sse.plist
launchctl bootout   gui/$(id -u)/com.templetwo.sovereign-bridge
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.templetwo.sovereign-bridge.plist
```

## Confirm the rollback took

- `GET /api/heartbeat` (local 8100 + tunnel) shows **1.7.1**.
- A non-lived `ground_truth` `intensity=0.9` entry with no receipt **honks
  again** (baseline behavior restored) — check `~/.sovereign/nape/honks.jsonl`.

The lived-ground-truth skills in `~/.claude/skills/` are version-guarded; they
remain installed but describe a capability that is simply not live on 1.7.1.
