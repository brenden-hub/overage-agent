#!/usr/bin/env python3
"""Walk every HubSpot company that has both `mau_limit` and `expo_update_count`
set, compute overage, update `mau_overage_status`, and post Slack alerts for
accounts that flipped to Over Limit.

Slack posts are gated by DRY_RUN (default true). Set DRY_RUN=false in .env to
actually post to #revenue_overages.

Usage:
    python3 scripts/run_overage_check.py [--limit N]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import requests  # noqa: E402

from lib import classifier, hubspot_client, slack_client, stripe_client  # noqa: E402


PROPERTIES = [
    "name",
    "domain",
    "website",
    "mau_limit",
    "mau_overage_status",
    "expo_update_count",
    "expo_stripe_customer_id",
    "expo_current_plan_name",
]


def search_companies_with_limit() -> list[dict]:
    body = {
        "filterGroups": [
            {
                "filters": [
                    {"propertyName": "mau_limit", "operator": "HAS_PROPERTY"},
                    {"propertyName": "expo_update_count", "operator": "HAS_PROPERTY"},
                ]
            }
        ],
        "properties": PROPERTIES,
        "limit": 100,
    }
    tok = os.environ["HUBSPOT_TOKEN"]
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

    results: list[dict] = []
    after: str | None = None
    while True:
        if after:
            body["after"] = after
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            headers=headers,
            json=body,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        paging = (data.get("paging") or {}).get("next") or {}
        after = paging.get("after")
        if not after:
            break
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Stop after N companies (debugging)")
    args = ap.parse_args()

    companies = search_companies_with_limit()
    print(f"Companies with mau_limit + expo_update_count: {len(companies)}")

    threshold = float(os.environ.get("OVERAGE_THRESHOLD", "0.0"))
    now_iso = datetime.now(timezone.utc).isoformat()
    posted = updated = skipped = 0

    for i, c in enumerate(companies):
        if args.limit and i >= args.limit:
            break
        p = c.get("properties") or {}
        try:
            limit = int(float(p["mau_limit"]))
            usage = int(float(p["expo_update_count"]))
        except (TypeError, ValueError, KeyError):
            skipped += 1
            continue

        is_over = usage > limit * (1 + threshold)
        new_status = "over_limit" if is_over else "under_limit"
        prior_status = p.get("mau_overage_status")

        hubspot_client.update_company(
            c["id"],
            {"mau_overage_status": new_status, "mau_overage_last_checked_at": now_iso},
        )
        updated += 1

        if is_over and prior_status != "over_limit":
            name = p.get("name") or "unknown"
            domain = p.get("domain") or p.get("website") or "—"
            plan = p.get("expo_current_plan_name")
            stripe_url = stripe_client.customer_url(p.get("expo_stripe_customer_id"))
            hs_url = hubspot_client.company_url(c["id"])

            try:
                summary = classifier.draft_summary(
                    company_name=name,
                    mau_limit=limit,
                    current_mau=usage,
                    plan=plan,
                )
            except Exception as e:
                summary = (
                    f"{name} is over MAU limit: {usage:,} of {limit:,} "
                    f"({(usage - limit) / limit * 100:.0f}% over)"
                )
                print(f"  [classifier err] {name}: {e}")

            blocks = slack_client.build_overage_blocks(
                company_name=name,
                domain=domain,
                plan=plan,
                current_mau=usage,
                mau_limit=limit,
                hubspot_url=hs_url,
                stripe_url=stripe_url,
            )
            if slack_client.post_overage(blocks, summary=summary):
                posted += 1

    print(f"\nDone. status_updated={updated} slack_posted={posted} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
