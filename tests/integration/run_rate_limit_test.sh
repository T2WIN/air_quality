#!/usr/bin/env bash
# Convenience wrapper: sources .env then runs the live rate-limit test.
#
# Usage:
#   bash tests/integration/run_rate_limit_test.sh
#
# Exit codes:
#   0 — all requests succeeded without 429
#   1 — one or more 429 responses received
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Source .env if it exists
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.env"
    set +a
fi

# Validate PROJECT_ID (needed for Secret Manager fallback)
if [ -z "${PROJECT_ID:-}" ]; then
    echo "ERROR: PROJECT_ID is not set (check .env)" >&2
    exit 1
fi

echo "Running live rate-limit integration test..."
echo "Project root: $PROJECT_ROOT"
echo ""

cd "$PROJECT_ROOT"
.venv/bin/python -m tests.integration.test_rate_limit_live
