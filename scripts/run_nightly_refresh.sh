#!/bin/bash
# Midnight cron — scans every account's HubSpot deals + notes for new MSAs /
# Order Forms / Renewals, extracts limits via Claude (cache hits are free), and
# updates `mau_limit` in HubSpot when the newest contract differs. Build limits
# are refreshed into a CSV for human review (no auto-write to HubSpot yet).
#
# DOES NOT post to Slack — only the 9 AM cron does that, and only when
# DRY_RUN=false in .env.
set -euo pipefail

ROOT="/Users/brendensong/.superset/projects/overage-agent"
cd "$ROOT"

LOG_DIR="$ROOT/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/nightly_refresh_$(date +%Y-%m-%d).log"

{
    echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) nightly MSA refresh ===="
    echo "--- MAU limits ---"
    "$ROOT/.venv/bin/python" "$ROOT/scripts/refresh_with_latest_msa.py"
    echo "--- Build limits (CSV only, no HubSpot write) ---"
    "$ROOT/.venv/bin/python" "$ROOT/scripts/refresh_build_limits.py"
    echo "==== exit $? ===="
} >> "$LOG" 2>&1
