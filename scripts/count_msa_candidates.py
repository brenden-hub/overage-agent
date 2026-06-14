#!/usr/bin/env python3
"""Cheap reconnaissance — walk every company in the MSA CSV and count how many
PDF attachments look like MSAs/Order Forms. No download, no Claude calls."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from lib import csv_parser, hubspot_client, hubspot_files


def main() -> int:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else str(
        Path.home() / "Downloads" / "Enterprise Limits from MSA's - msa_limits_normalized (2).csv"
    )
    rows = csv_parser.load_msa_limits(csv_path)
    print(f"Scanning {len(rows)} CSV rows for MSA-like PDFs...\n")

    grand_total = 0
    no_company = no_pdfs = with_pdfs = 0
    for r in rows:
        if not r.domain:
            no_company += 1
            continue
        c = hubspot_client.find_company_by_domain(r.domain)
        if not c:
            no_company += 1
            print(f"  [no co  ] {r.customer:30} | {r.domain}")
            continue
        cands = list(hubspot_files.iter_msa_candidates_for_company(c["id"]))
        if not cands:
            no_pdfs += 1
            print(f"  [0 pdfs ] {r.customer:30} | {r.domain}")
            continue
        with_pdfs += 1
        grand_total += len(cands)
        names = ", ".join(f"{x['name']}.{x['ext']}" for x in cands[:3])
        more = "" if len(cands) <= 3 else f" (+{len(cands) - 3} more)"
        print(f"  [{len(cands):2d} pdfs] {r.customer:30} | {r.domain:25} | {names}{more}")

    print(
        f"\nTotals: companies_with_pdfs={with_pdfs} no_company={no_company} "
        f"no_pdfs={no_pdfs}  candidate_pdfs={grand_total}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
