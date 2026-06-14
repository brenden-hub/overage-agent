#!/usr/bin/env python3
"""Read the MSA limits CSV and update each matching HubSpot company's `mau_limit`
property. Match is by domain (exact on `domain`, fallback to substring on `website`).

Usage:
    python3 scripts/sync_msa_limits.py PATH/TO/msa_limits.csv [--dry-run]

This script DOES write to HubSpot when --dry-run is omitted. It never posts to
Slack — that lives in run_overage_check.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from lib import csv_parser, hubspot_client  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="Path to the MSA limits CSV")
    ap.add_argument("--dry-run", action="store_true", help="Don't write to HubSpot")
    ap.add_argument("--only", help="Comma-separated customer names to limit to (debugging)")
    args = ap.parse_args()

    rows = csv_parser.with_mau_limit(csv_parser.load_msa_limits(args.csv_path))
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",")}
        rows = [r for r in rows if r.customer.lower() in wanted]

    print(f"CSV rows with a parseable MAU limit + domain: {len(rows)}")

    updated = matched_no_change = unmatched = errors = 0
    for r in rows:
        company = hubspot_client.find_company_by_domain(r.domain)  # type: ignore[arg-type]
        if not company:
            unmatched += 1
            print(f"  [no match] {r.customer:30} | {r.domain}")
            continue

        cid = company["id"]
        props = company.get("properties") or {}
        existing = props.get("mau_limit")
        existing_int = int(float(existing)) if existing not in (None, "") else None
        if existing_int == r.mau_limit:
            matched_no_change += 1
            print(f"  [ok same ] {r.customer:30} | {r.domain:30} | {r.mau_limit:>10,}")
            continue

        if args.dry_run:
            print(
                f"  [WOULD  ] {r.customer:30} | {r.domain:30} | "
                f"{existing_int!s:>10} -> {r.mau_limit:>10,}"
            )
            continue

        ok = hubspot_client.update_company(cid, {"mau_limit": r.mau_limit})
        if ok:
            updated += 1
            print(
                f"  [updated] {r.customer:30} | {r.domain:30} | "
                f"{existing_int!s:>10} -> {r.mau_limit:>10,}"
            )
        else:
            errors += 1
            print(f"  [ERROR  ] {r.customer:30} | {r.domain}")

    print(
        f"\nDone. updated={updated} same={matched_no_change} unmatched={unmatched} "
        f"errors={errors} total={len(rows)}"
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
