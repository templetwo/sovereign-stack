#!/usr/bin/env bash
# One canonical local pre-flight command: lint -> format check -> tests.
#
# CI is the authority on Linux + the Python matrix; this is a local pre-flight.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [ -x "./venv/bin/ruff" ]; then
  RUFF="./venv/bin/ruff"
else
  RUFF="ruff"
fi

if [ -x "./venv/bin/pytest" ]; then
  PYTEST="./venv/bin/pytest"
else
  PYTEST="pytest"
fi

"$RUFF" check src/sovereign_stack/ tests/
"$RUFF" format --check src/sovereign_stack/ tests/
"$PYTEST" tests/ -q

echo "all checks passed"
