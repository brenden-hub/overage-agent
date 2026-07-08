#!/usr/bin/env python3
"""Parallel to run_overage_check.py, but for EAS Build credit overages.

For every company that has a build credit limit AND scope=build_only (skips
combined Build+Workflows accounts since they can't be evaluated on build
spend alone), compare eas_build_spend_usd vs eas_build_credit_limit_usd,
write eas_build_overage_status + eas_build_overage_last_checked_at, and post
a Slack alert for any account that newly flips to over_limit.

Slack posts gated by DRY_RUN=true.
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

from lib import hubspot_client, owner_map, slack_client, slack_client_builds, stripe_client  # noqa: E402


PROPERTIES = [
    "name", "domain", "website",
    "eas_build_credit_limit_usd", "eas_build_spend_usd", "eas_build_overage_amount_usd",
    "eas_build_credit_scope", "eas_build_overage_status",
    "eas_build_30d_android_medium", "eas_build_30d_ios_medium",
    "eas_build_30d_android_large", "eas_build_30d_ios_large",
    "expo_stripe_customer_id", "expo_current_plan_name", "hubspot_owner_id",
]


def _f(v) -> float | None:
    try: return float(v)
    except (TypeError, ValueError): return None


def search_build_tracked() -> list[dict]:
    """Companies with a build_only credit limit. Combined-bucket accounts are
    intentionally excluded from this overage check — their credit covers both
    Build AND Workflows so build_spend_usd alone can't tell you if they're
    over."""
    body = {
        "filterGroups": [{"filters": [
            {"propertyName": "eas_build_credit_limit_usd", "operator": "HAS_PROPERTY"},
            {"propertyName": "eas_build_credit_scope", "operator": "EQ", "value": "build_only"},
        ]}],
        "properties": PROPERTIES,
        "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
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
            headers=headers, json=body, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Stop after N")
    args = ap.parse_args()

    companies = search_build_tracked()
    print(f"Build-only companies to check: {len(companies)}")

    now_iso = datetime.now(timezone.utc).isoformat()
    posted = updated = skipped = 0

    for i, c in enumerate(companies):
        if args.limit and i >= args.limit:
            break
        p = c.get("properties") or {}
        limit = _f(p.get("eas_build_credit_limit_usd"))
        spend = _f(p.get("eas_build_spend_usd"))
        if limit is None or spend is None:
            skipped += 1
            continue

        is_over = spend > limit
        new_status = "over_limit" if is_over else "under_limit"
        prior_status = p.get("eas_build_overage_status")

        hubspot_client.update_company(
            c["id"],
            {
                "eas_build_overage_status": new_status,
                "eas_build_overage_last_checked_at": now_iso,
            },
        )
        updated += 1

        if is_over and prior_status != "over_limit":
            name = p.get("name") or "unknown"
            domain = p.get("domain") or p.get("website") or "—"
            plan = p.get("expo_current_plan_name")
            stripe_url = stripe_client.customer_url(p.get("expo_stripe_customer_id"))
            hs_url = hubspot_client.company_url(c["id"])
            owner_mention = owner_map.slack_mention_for(p.get("hubspot_owner_id"))

            overage = spend - limit
            pct_over = overage / limit * 100 if limit else 0
            summary = (
                f"{name} is +${overage:,.0f} over their ${limit:,.0f} build credit "
                f"({pct_over:.0f}% over)"
                + (f". cc {owner_mention}" if owner_mention else "")
            )

            blocks = slack_client_builds.build_build_overage_blocks(
                company_name=name, domain=domain, plan=plan,
                credit_limit_usd=limit, spend_30d_usd=spend,
                android_medium=int(_f(p.get("eas_build_30d_android_medium")) or 0),
                ios_medium=int(_f(p.get("eas_build_30d_ios_medium")) or 0),
                android_large=int(_f(p.get("eas_build_30d_android_large")) or 0),
                ios_large=int(_f(p.get("eas_build_30d_ios_large")) or 0),
                hubspot_url=hs_url, stripe_url=stripe_url,
                owner_mention=owner_mention,
            )
            if slack_client.post_overage(blocks, summary=summary):
                posted += 1
                print(f"  posted: {name}  +${overage:,.0f} over ({pct_over:.0f}%)")

    print(f"\nDone. status_updated={updated} slack_posted={posted} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
