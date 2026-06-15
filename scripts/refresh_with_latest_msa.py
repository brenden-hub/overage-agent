#!/usr/bin/env python3
"""For each company in the MSA CSV, find the MOST RECENT contract-like PDF in
HubSpot, extract its MAU limit, and PATCH the HubSpot `mau_limit` to that
value. Reports every change before applying.

Runs the smarter iter_msa_candidates_for_company() which sorts newest-first
and accepts a wider keyword set (renewal, expansion, proposal, amendment, etc).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from lib import csv_parser, hubspot_client, hubspot_files, msa_extractor

DOWNLOADS = ROOT / "downloads"
DOWNLOADS.mkdir(exist_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", nargs="?",
                    default=str(Path.home() / "Downloads"
                                / "Enterprise Limits from MSA's - msa_limits_normalized (2).csv"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="Comma-separated customer names")
    args = ap.parse_args()

    rows = csv_parser.load_msa_limits(args.csv_path)
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",")}
        rows = [r for r in rows if r.customer.lower() in wanted]
    rows = [r for r in rows if r.domain]

    changed = same = no_pdf = no_co = errors = 0
    for r in rows:
        co = hubspot_client.find_company_by_domain(r.domain)  # type: ignore[arg-type]
        if not co:
            no_co += 1
            continue
        # Take the newest matching PDF — iter_msa_candidates returns newest-first
        latest = next(iter(hubspot_files.iter_msa_candidates_for_company(co["id"])), None)
        if not latest:
            no_pdf += 1
            continue

        fid = latest["file_id"]
        pdf_path = DOWNLOADS / f"{fid}.pdf"
        if not pdf_path.exists():
            ok = hubspot_files.download(fid, str(pdf_path))
            if not ok:
                print(f"  [DL FAIL] {r.customer:25} {latest['name'][:50]}")
                errors += 1
                continue

        try:
            ext = msa_extractor.extract_from_pdf(pdf_path)
        except Exception as e:
            print(f"  [EXT FAIL] {r.customer:25} {e!s}[:120]")
            errors += 1
            continue

        new_mau = ext.get("eas_update_mau")
        if not isinstance(new_mau, int):
            print(f"  [no mau ] {r.customer:25} ts={latest['note_ts'][:10]} file={latest['name'][:45]}")
            continue

        current = (co.get("properties") or {}).get("mau_limit")
        current_int = int(float(current)) if current not in (None, "") else None
        if current_int == new_mau:
            same += 1
            print(f"  [ok same] {r.customer:25} ts={latest['note_ts'][:10]} mau={new_mau:>10,}  file={latest['name'][:45]}")
            continue

        print(f"  [DIFF   ] {r.customer:25} ts={latest['note_ts'][:10]} "
              f"hubspot={current_int!s:>10} -> {new_mau:>10,}  file={latest['name'][:45]}")
        if args.dry_run:
            continue
        ok = hubspot_client.update_company(co["id"], {"mau_limit": new_mau})
        if ok:
            changed += 1
        else:
            errors += 1

    print(
        f"\nDone. changed={changed} same={same} no_pdf={no_pdf} "
        f"no_co={no_co} errors={errors} total={len(rows)}"
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
