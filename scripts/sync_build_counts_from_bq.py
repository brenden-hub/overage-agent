#!/usr/bin/env python3
"""Pull 30-day EAS Build counts (Android/iOS × medium/large) from BigQuery,
match to HubSpot companies via Stripe customer id, and write the four count
fields + `eas_build_30d_last_synced_at`.

The calculated property `eas_build_spend_usd` will auto-recompute using:
  android_medium × $1 + ios_medium × $2 + android_large × $2 + ios_large × $4

Single BQ query for all companies (partition-pruned to last 30 days).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import requests  # noqa: E402


def _h() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['HUBSPOT_TOKEN']}", "Content-Type": "application/json"}


def companies_with_stripe() -> list[dict]:
    """Enterprise companies we track — filtered to those with either an MAU
    limit or a build credit limit set (~65 accounts). Anything wider hits the
    89K+ paying-customer set and blows past HubSpot's search depth."""
    fields = [
        "name", "expo_stripe_customer_id",
        "eas_build_30d_android_medium", "eas_build_30d_ios_medium",
        "eas_build_30d_android_large", "eas_build_30d_ios_large",
    ]
    results: list[dict] = []
    after: str | None = None
    while True:
        body = {
            "filterGroups": [
                {"filters": [
                    {"propertyName": "expo_stripe_customer_id", "operator": "HAS_PROPERTY"},
                    {"propertyName": "mau_limit", "operator": "HAS_PROPERTY"},
                ]},
                {"filters": [
                    {"propertyName": "expo_stripe_customer_id", "operator": "HAS_PROPERTY"},
                    {"propertyName": "eas_build_credit_limit_usd", "operator": "HAS_PROPERTY"},
                ]},
            ],
            "properties": fields,
            "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
            "limit": 100,
        }
        if after:
            body["after"] = after
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            headers=_h(), json=body, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    # Dedupe by id (the two filterGroups OR together but may double-count if
    # a company has both mau_limit AND eas_build_credit_limit_usd)
    seen: set[str] = set()
    unique: list[dict] = []
    for c in results:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        unique.append(c)
    return unique


def bq_query_build_counts(stripe_ids: list[str]) -> dict[str, dict]:
    """Returns {stripe_customer_id: {am, im, al, il}} for the last 30 days.
    Partition-pruned via `created_at > NOW - 30 DAY` so total scan stays well
    under the 3GB budget."""
    if not stripe_ids:
        return {}
    quoted = ",".join(f"'{s}'" for s in stripe_ids)
    sql = f"""
    WITH stripe_accounts AS (
      SELECT DISTINCT
        CAST(account_id AS STRING) AS account_id,
        stripe_customer_id
      FROM `usage_metrics.projects_and_subscriptions_by_account`
      WHERE stripe_customer_id IN ({quoted})
    ),
    build_usage AS (
      SELECT
        atr.account_id,
        COUNTIF(bb.platform='android' AND bb.billing_resource_class='medium') AS am,
        COUNTIF(bb.platform='ios'     AND bb.billing_resource_class='medium') AS im,
        COUNTIF(bb.platform='android' AND bb.billing_resource_class='large')  AS al,
        COUNTIF(bb.platform='ios'     AND bb.billing_resource_class='large')  AS il
      FROM `usage_metrics.eas_build_builds` bb
      JOIN `www_unified.app_transfer_records` atr ON bb.project_id = atr.app_id
      WHERE atr.ended_at IS NULL
        AND bb.waived_at IS NULL
        AND bb.priority = 'high'
        AND bb.created_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
      GROUP BY atr.account_id
    )
    SELECT
      sa.stripe_customer_id,
      COALESCE(SUM(bu.am), 0) AS am,
      COALESCE(SUM(bu.im), 0) AS im,
      COALESCE(SUM(bu.al), 0) AS al,
      COALESCE(SUM(bu.il), 0) AS il
    FROM stripe_accounts sa
    LEFT JOIN build_usage bu USING (account_id)
    GROUP BY sa.stripe_customer_id
    """
    proc = subprocess.run(
        ["bq", "query", "--format=json", "--max_rows=1000", "--nouse_legacy_sql", sql],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"bq query failed: {proc.stderr[:400]}")
    rows = json.loads(proc.stdout)
    out: dict[str, dict] = {}
    for r in rows:
        out[r["stripe_customer_id"]] = {k: int(r[k]) for k in ("am", "im", "al", "il")}
    return out


def update_company(company_id: str, props: dict) -> bool:
    r = requests.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
        headers=_h(), json={"properties": props}, timeout=20,
    )
    return r.status_code == 200


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="Comma-separated Stripe customer IDs")
    args = ap.parse_args()

    companies = companies_with_stripe()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        companies = [c for c in companies if (c["properties"] or {}).get("expo_stripe_customer_id") in wanted]

    stripe_ids = [(c["properties"] or {}).get("expo_stripe_customer_id") for c in companies]
    stripe_ids = [s for s in stripe_ids if s]
    print(f"HubSpot companies with Stripe id: {len(stripe_ids)}")

    counts = bq_query_build_counts(stripe_ids)
    print(f"BQ returned counts for: {len(counts)}\n")

    now_iso = datetime.now(timezone.utc).isoformat()
    updated = same = missing = errors = 0
    for c in companies:
        p = c.get("properties") or {}
        cid = p.get("expo_stripe_customer_id")
        if not cid or cid not in counts:
            missing += 1
            continue
        target = counts[cid]
        existing = {
            "am": int(float(p.get("eas_build_30d_android_medium") or 0)),
            "im": int(float(p.get("eas_build_30d_ios_medium") or 0)),
            "al": int(float(p.get("eas_build_30d_android_large") or 0)),
            "il": int(float(p.get("eas_build_30d_ios_large") or 0)),
        }
        if existing == target and not args.dry_run:
            same += 1
            continue

        props = {
            "eas_build_30d_android_medium": target["am"],
            "eas_build_30d_ios_medium": target["im"],
            "eas_build_30d_android_large": target["al"],
            "eas_build_30d_ios_large": target["il"],
            "eas_build_30d_last_synced_at": now_iso,
        }
        spend = target["am"] * 1 + target["im"] * 2 + target["al"] * 2 + target["il"] * 4
        name = p.get("name", "?")
        if args.dry_run:
            print(f"  [WOULD  ] {name:25}  A/m={target['am']:>4} i/m={target['im']:>4} A/L={target['al']:>4} i/L={target['il']:>4}  spend=${spend:,}")
            continue

        if update_company(c["id"], props):
            updated += 1
            print(f"  [updated] {name:25}  A/m={target['am']:>4} i/m={target['im']:>4} A/L={target['al']:>4} i/L={target['il']:>4}  spend=${spend:,}")
        else:
            errors += 1
            print(f"  [ERROR  ] {name}")

    print(f"\nDone. updated={updated} same={same} missing={missing} errors={errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
