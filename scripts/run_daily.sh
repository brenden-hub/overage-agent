#!/bin/bash
# Daily wrapper for the overage agent. Invoked by cron — note that cron's PATH
# is minimal, so we explicitly reference the venv's python and project root.
set -euo pipefail

ROOT="/Users/brendensong/.superset/projects/overage-agent"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run_$(date +%Y-%m-%d).log"

{
    echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) overage-agent cron run ===="
    echo "--- MAU check ---"
    "$ROOT/.venv/bin/python" "$ROOT/scripts/run_overage_check.py"
    echo ""
    echo "--- Build credit check (build_only scope) ---"
    "$ROOT/.venv/bin/python" "$ROOT/scripts/run_build_overage_check.py"
    echo "==== exit $? ===="
} >> "$LOG" 2>&1
