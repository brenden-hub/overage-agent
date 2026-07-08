#!/bin/bash
# Midnight cron — scans HubSpot deals + notes for new MSAs / Order Forms /
# Renewals, extracts limits via Claude Code CLI (subscription-funded), and
# updates `mau_limit` in HubSpot when the newest contract differs.
#
# Uses --recent-days 7 so we only re-walk companies with deal activity in the
# last week. Cached extractions are reused without re-calling Claude, so steady
# state is near-zero subscription cost.
#
# DOES NOT post to Slack — only the 9 AM cron does that, and only when
# DRY_RUN=false in .env.
set -euo pipefail

ROOT="/Users/brendensong/.superset/projects/overage-agent"
cd "$ROOT"

# cron has a minimal PATH; expose the claude CLI (subscription-funded).
export PATH="$HOME/.superset/bin:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

LOG_DIR="$ROOT/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/nightly_refresh_$(date +%Y-%m-%d).log"

{
    echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) nightly refresh ===="
    echo "--- MAU limits (recent activity only) ---"
    "$ROOT/.venv/bin/python" "$ROOT/scripts/refresh_with_latest_msa.py" --recent-days 7
    echo ""
    echo "--- Build counts (30-day, from BigQuery) ---"
    "$ROOT/.venv/bin/python" "$ROOT/scripts/sync_build_counts_from_bq.py"
    echo "==== exit $? ===="
} >> "$LOG" 2>&1
