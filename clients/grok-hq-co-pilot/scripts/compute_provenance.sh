#!/bin/bash
# Compute provenance hashes + manifest for grok-hq-co-pilot artifacts
# 2026-07-03
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/reports/provenance_$(date -u +%Y%m%d-%H%M%S).json"

echo "Computing provenance for $ROOT ..."

declare -a FILES=()
while IFS= read -r -d '' f; do
  FILES+=("$f")
done < <(find "$ROOT" -type f \( -name "*.md" -o -name "*.yaml" -o -name "*.sh" -o -name "*.py" -o -name "*.json" \) ! -path "*/.git/*" -print0 | sort -z)

MANIFEST="[]"
for f in "${FILES[@]}"; do
  rel=${f#$ROOT/}
  h=$(shasum -a 256 "$f" | awk '{print $1}')
  ts=$(date -u -r "$f" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
  MANIFEST=$(echo "$MANIFEST" | python3 -c "
import sys, json
m = json.load(sys.stdin)
m.append({'path': '$rel', 'sha256': '$h', 'mtime': '$ts'})
print(json.dumps(m, indent=2))
")
done

SUMMARY=$(echo "$MANIFEST" | python3 -c '
import sys, json, hashlib
m = json.load(sys.stdin)
root = hashlib.sha256(json.dumps(sorted(m, key=lambda x:x["path"]), sort_keys=True).encode()).hexdigest()
print(json.dumps({"file_count": len(m), "root_sha256": root, "files": m}, indent=2))
')

echo "$SUMMARY" > "$OUT"
echo "Provenance manifest: $OUT"
echo "Root hash (all files): $(echo "$SUMMARY" | python3 -c 'import sys,json; print(json.load(sys.stdin)["root_sha256"])')"
