#!/usr/bin/env python3
"""Walk every company in the MSA CSV, download MSA/Order Form PDFs from their
HubSpot closed-won deal notes, send each to Claude to extract the contract
limits, and write the results to `data/extracted_msa_limits.csv` for review.

Idempotent: PDFs already in `downloads/` are reused; extractions already in
`data/extractions_cache.jsonl` are reused (so reruns don't re-bill Claude).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from lib import csv_parser, hubspot_client, hubspot_files, msa_extractor

DOWNLOADS = ROOT / "downloads"
DATA = ROOT / "data"
CACHE_FILE = DATA / "extractions_cache.jsonl"
OUT_CSV = DATA / "extracted_msa_limits.csv"

DOWNLOADS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)


def load_cache() -> dict[str, dict]:
    if not CACHE_FILE.exists():
        return {}
    out: dict[str, dict] = {}
    for line in CACHE_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            out[rec["file_id"]] = rec
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def append_cache(rec: dict) -> None:
    with CACHE_FILE.open("a") as f:
        f.write(json.dumps(rec) + "\n")


CSV_COLUMNS = [
    "customer",
    "domain",
    "deal_id",
    "deal_name",
    "deal_closedate",
    "file_id",
    "file_name",
    "eas_update_mau",
    "eas_update_bandwidth_tib_month",
    "eas_update_storage_tib_month",
    "eas_build_monthly_credit_usd",
    "eas_build_concurrent",
    "eas_build_monthly_builds",
    "eas_build_timeout_hours",
    "eas_workflows_credit_usd_month",
    "eas_submit_unlimited",
    "enterprise_seats",
    "enterprise_projects",
    "enterprise_organizations",
    "priority_support_response_time",
    "evidence_quote",
    "csv_mau_limit",
    "delta_vs_csv",
    "error",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "csv_path",
        nargs="?",
        default=str(Path.home() / "Downloads" / "Enterprise Limits from MSA's - msa_limits_normalized (2).csv"),
    )
    ap.add_argument("--limit", type=int, default=0, help="Stop after N companies")
    ap.add_argument("--only", help="Comma-separated customer names")
    args = ap.parse_args()

    rows = csv_parser.load_msa_limits(args.csv_path)
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",")}
        rows = [r for r in rows if r.customer.lower() in wanted]

    cache = load_cache()
    print(f"CSV rows: {len(rows)}  cached extractions: {len(cache)}")

    out_rows: list[dict] = []
    processed = 0

    for r in rows:
        if args.limit and processed >= args.limit:
            break
        if not r.domain:
            continue
        co = hubspot_client.find_company_by_domain(r.domain)
        if not co:
            print(f"  [no co]  {r.customer}")
            continue
        cands = list(hubspot_files.iter_msa_candidates_for_company(co["id"]))
        if not cands:
            print(f"  [0 pdfs] {r.customer}")
            continue

        processed += 1
        print(f"\n[{processed}] {r.customer} ({len(cands)} pdfs)")
        for c in cands:
            fid = c["file_id"]
            row = {col: "" for col in CSV_COLUMNS}
            row.update(
                customer=r.customer,
                domain=r.domain,
                deal_id=c.get("deal_id"),
                deal_name=c.get("deal_name"),
                deal_closedate=c.get("deal_closedate"),
                file_id=fid,
                file_name=c.get("name"),
                csv_mau_limit=r.mau_limit if r.mau_limit is not None else "",
            )

            if fid in cache:
                ext = cache[fid].get("extraction") or {}
                print(f"    [cache] {c['name']}")
            else:
                pdf_path = DOWNLOADS / f"{fid}.pdf"
                if not pdf_path.exists():
                    ok = hubspot_files.download(fid, str(pdf_path))
                    if not ok:
                        row["error"] = "download_failed"
                        out_rows.append(row)
                        print(f"    [DL FAIL] {c['name']}")
                        continue
                try:
                    ext = msa_extractor.extract_from_pdf(pdf_path)
                    append_cache({"file_id": fid, "extraction": ext, "name": c["name"]})
                    print(f"    [extracted] {c['name']}  mau={ext.get('eas_update_mau')}")
                except Exception as e:
                    row["error"] = f"extract_failed: {e!s}[:200]"
                    out_rows.append(row)
                    print(f"    [EXT FAIL] {c['name']}: {e}")
                    continue

            for k in (
                "eas_update_mau",
                "eas_update_bandwidth_tib_month",
                "eas_update_storage_tib_month",
                "eas_build_monthly_credit_usd",
                "eas_build_concurrent",
                "eas_build_monthly_builds",
                "eas_build_timeout_hours",
                "eas_workflows_credit_usd_month",
                "eas_submit_unlimited",
                "enterprise_seats",
                "enterprise_projects",
                "enterprise_organizations",
                "priority_support_response_time",
                "evidence_quote",
            ):
                row[k] = ext.get(k, "")

            csv_mau = r.mau_limit
            pdf_mau = ext.get("eas_update_mau")
            if isinstance(pdf_mau, int) and isinstance(csv_mau, int):
                row["delta_vs_csv"] = pdf_mau - csv_mau

            out_rows.append(row)

    OUT_CSV.parent.mkdir(exist_ok=True)
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    print(f"\nWrote {len(out_rows)} rows -> {OUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
