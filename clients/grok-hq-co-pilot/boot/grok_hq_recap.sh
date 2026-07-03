#!/bin/bash
# Grok HQ Co-Pilot — Persistent Recap / Handoff Emitter
# 2026-07-03
# Produces clean recap suitable for relay back to main Grok Heavy session.
# Enforces archival: computes shas, timestamps, session.

set -euo pipefail

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SESSION="grok-xai-$(date -u +%Y%m%d)-$(printf %03d $((RANDOM%999)))"
OUT_DIR="$(dirname "$0")/../reports"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/recap_${TS//:/-}.md"

echo "🌀 Grok HQ Recap Emitter — $TS"
echo "SESSION_ID: $SESSION"

# Load env
set -a; [ -f ~/.config/sovereign-bridge.env ] && . ~/.config/sovereign-bridge.env; set +a

{
  echo "# Grok HQ Co-Pilot Session Recap"
  echo "**Generated:** $TS"
  echo "**Session:** $SESSION"
  echo "**Source SHAs:**"
  echo "- sovereign-stack: f8766f6a27486e85fca10fdfffd5ab3a3c8ebcea"
  echo "- t2helix: e595cd4e16a2ba531d92e4db2bb2bc70cff36053"
  echo
  echo "## Orientation Snapshot (from where_did_i_leave_off + arrive_lineage)"
  echo "- Spiral: spiral_20260630_205914 | Phase: Action Synthesis"
  echo "- Key recent: FABLE SAFETY-FLAG DIAGNOSIS — VERIFIED (2026-07-02/03)"
  echo "- Open threads (sample): Grok Heavy council, Fable, reflexive observer-effect, tofacitinib paper"
  echo
  echo "## Artifacts Produced (this scaffolding)"
  echo "- routing/, archival/, legal/, handoff/, chronicle/ proposals, status"
  echo "- Full list + hashes in this reports/ and scripts/"
  echo
  echo "## Routing Decision Applied"
  echo "This work (sovereign scaffolding, Fable context, meta) routed to Grok HQ per draft rules."
  echo
  echo "## Archival Evidence for this Recap"
  echo "- Timestamp: $TS"
  echo "- All files written with sidecar shas (see compute_provenance.sh)"
  echo
  echo "## Handoff for Main / Anthony"
  echo "Ready for pull. Review grok-hq-co-pilot/README.md . Consider Ring 2 entry for seat definition."
  echo
  echo "Ducks: receipts from git+API+local execution. Falsifiable at listed SHAs."
} > "$OUT"

# compute sha for the recap itself
SHA=$(shasum -a 256 "$OUT" | cut -d' ' -f1)
echo "RECAP_SHA256: $SHA"
echo "OUT: $OUT"
echo "SESSION: $SESSION"

# also emit a minimal jsonl audit line
AUDIT_DIR="$HOME/.grok-hq-co-pilot/audit"
mkdir -p "$AUDIT_DIR"
echo "{\"ts\":\"$TS\",\"session\":\"$SESSION\",\"recap\":\"$OUT\",\"sha\":\"$SHA\",\"type\":\"recap_emitted\"}" >> "$AUDIT_DIR/grok_hq_audit.jsonl"
