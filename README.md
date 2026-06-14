# overage-agent

Detects Expo enterprise accounts that have exceeded their contractual MAU limits
from the MSA, updates HubSpot, and posts a Slack alert to `#revenue_overages`.

Patterned after `~/agents/pqa-agent` (Python, raw HubSpot REST, Slack incoming
webhook with Block Kit, Anthropic SDK for the summary line). Built on Claude
Opus 4.7 (the latest Opus in the Claude 4.x family).

## What it does

1. **`scripts/sync_msa_limits.py`** — reads the MSA limits CSV (the
   `EAS Update — MAU` column) and writes the integer limit into a new
   `mau_limit` company property in HubSpot. Match by domain.
2. **`scripts/run_overage_check.py`** — walks every company that has both
   `mau_limit` and `expo_update_count`, computes overage, sets
   `mau_overage_status` to `over_limit` / `under_limit`, and posts a Block
   Kit-formatted Slack message for accounts that newly flipped to Over Limit.

Slack posts are gated by `DRY_RUN=true` (the default). Set `DRY_RUN=false` in
`.env` only after you have reviewed the dry-run output.

## HubSpot properties (created automatically the first time)

| Property | Type | Source |
|---|---|---|
| `mau_limit` | number | MSA CSV (`EAS Update — MAU`) via `sync_msa_limits.py` |
| `mau_overage_status` | enum (`over_limit` / `under_limit` / `unknown`) | `run_overage_check.py` |
| `mau_overage_last_checked_at` | datetime | `run_overage_check.py` |

Existing properties read: `expo_update_count` (current MAU usage),
`expo_stripe_customer_id` (Stripe deep-link), `expo_current_plan_name`.

## Setup

```bash
cd ~/.superset/projects/overage-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in ANTHROPIC_API_KEY, HUBSPOT_TOKEN, SLACK_WEBHOOK_URL
```

## Usage

```bash
# Push MSA limits into HubSpot (the main thing you do first)
python3 scripts/sync_msa_limits.py "~/Downloads/Enterprise Limits from MSA's - msa_limits_normalized (2).csv" --dry-run
python3 scripts/sync_msa_limits.py "~/Downloads/Enterprise Limits from MSA's - msa_limits_normalized (2).csv"

# Compute overages + update HubSpot status. Slack stays silent while DRY_RUN=true.
python3 scripts/run_overage_check.py

# Render a sample Slack message JSON (paste into Block Kit Builder)
python3 scripts/preview_slack_blocks.py
```

## Env vars

| Var | Required? | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | for the summary line |
| `OVERAGE_MODEL` | no | defaults to `claude-opus-4-7` |
| `HUBSPOT_TOKEN` | yes | private app token, same one as PQA agent |
| `HUBSPOT_PORTAL_ID` | no | defaults to `22007177` |
| `SLACK_WEBHOOK_URL` | yes | `#revenue_overages` incoming webhook |
| `DRY_RUN` | no | default `true` — set `false` to actually post |
| `OVERAGE_THRESHOLD` | no | default `0.0` — fraction over limit before flagging |
