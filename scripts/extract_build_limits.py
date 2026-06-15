#!/usr/bin/env python3
"""Build-focused second pass: for every PDF already downloaded in `downloads/`,
ask Claude to extract just the EAS Build limits. Writes a review-friendly
CSV at `data/extracted_build_limits.csv` with side-by-side columns:

  customer, domain, file, PDF_credit_usd, CSV_credit_usd, PDF_minutes,
  CSV_minutes, PDF_concurrent, CSV_concurrent, PDF_builds, CSV_builds,
  PDF_timeout_hours, CSV_timeout_hours, evidence_quote, delta_flag

Idempotent: extractions get cached to `data/build_extractions_cache.jsonl`.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from lib import builds_csv_parser, hubspot_client, msa_extractor

DOWNLOADS = ROOT / "downloads"
DATA = ROOT / "data"
CACHE = DATA / "build_extractions_cache.jsonl"
OUT_CSV = DATA / "extracted_build_limits.csv"
PRIMARY_CSV = DATA / "extracted_msa_limits.csv"  # gives us file_id -> customer mapping


def load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    out: dict[str, dict] = {}
    for line in CACHE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            out[r["file_id"]] = r
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def append_cache(rec: dict) -> None:
    with CACHE.open("a") as f:
        f.write(json.dumps(rec) + "\n")


COLUMNS = [
    "customer",
    "domain",
    "file_id",
    "file_name",
    "deal_name",
    "deal_closedate",
    # Side-by-side: PDF -> CSV for each build dimension
    "pdf_credit_usd",
    "csv_credit_usd",
    "pdf_minutes",
    "csv_minutes",
    "pdf_concurrent",
    "csv_concurrent",
    "pdf_monthly_builds",
    "csv_monthly_builds",
    "pdf_timeout_hours",
    "csv_timeout_hours",
    "pdf_unlimited",
    "evidence_quote",
    "delta_flag",
    "error",
]


def _delta_flag(pdf: dict, csv_row: builds_csv_parser.BuildLimits) -> str:
    diffs = []
    pairs = [
        ("credit_usd", "eas_build_monthly_credit_usd"),
        ("minutes",    "eas_build_monthly_minutes"),
        ("concurrent", "eas_build_concurrent"),
        ("monthly_builds", "eas_build_monthly_builds"),
        ("timeout_hours", "eas_build_timeout_hours"),
    ]
    for csv_key, pdf_key in pairs:
        c = getattr(csv_row, csv_key)
        p = pdf.get(pdf_key)
        if c is not None and p is not None and float(c) != float(p):
            diffs.append(csv_key)
    if not diffs:
        return ""
    return "DIFF:" + ",".join(diffs)


def main() -> int:
    if not PRIMARY_CSV.exists():
        print(f"Need {PRIMARY_CSV} first (run extract_msas.py)")
        return 1
    primary = list(csv.DictReader(open(PRIMARY_CSV)))
    csv_path = (
        Path.home()
        / "Downloads"
        / "Enterprise Limits from MSA's - msa_limits_normalized (2).csv"
    )
    csv_rows = builds_csv_parser.load_build_limits(str(csv_path))
    csv_by_domain = {r.domain: r for r in csv_rows if r.domain}

    cache = load_cache()
    print(f"PDFs in primary CSV: {len(primary)}  cached build extractions: {len(cache)}")

    out_rows: list[dict] = []
    for i, p in enumerate(primary, start=1):
        fid = p["file_id"]
        pdf_path = DOWNLOADS / f"{fid}.pdf"
        row = {col: "" for col in COLUMNS}
        row.update(
            customer=p["customer"],
            domain=p["domain"],
            file_id=fid,
            file_name=p["file_name"],
            deal_name=p["deal_name"],
            deal_closedate=p["deal_closedate"],
        )
        cs = csv_by_domain.get(p["domain"])
        if cs:
            row.update(
                csv_credit_usd=cs.credit_usd if cs.credit_usd is not None else "",
                csv_minutes=cs.minutes if cs.minutes is not None else "",
                csv_concurrent=cs.concurrent if cs.concurrent is not None else "",
                csv_monthly_builds=cs.monthly_builds if cs.monthly_builds is not None else "",
                csv_timeout_hours=cs.timeout_hours if cs.timeout_hours is not None else "",
            )

        if not pdf_path.exists():
            row["error"] = "pdf_missing"
            out_rows.append(row)
            continue

        if fid in cache:
            ext = cache[fid].get("extraction") or {}
        else:
            try:
                last_err = None
                for attempt in range(3):
                    try:
                        time.sleep(1 + attempt * 2)
                        ext = msa_extractor.extract_build_limits(pdf_path)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = str(e)[:120]
                if last_err:
                    row["error"] = f"extract_failed: {last_err}"
                    out_rows.append(row)
                    print(f"  [{i}/{len(primary)}] FAIL  {p['customer']:25} {last_err}")
                    continue
                append_cache({"file_id": fid, "name": p["file_name"], "extraction": ext})
            except Exception as e:
                row["error"] = f"extract_failed: {e!s}[:200]"
                out_rows.append(row)
                continue

        row.update(
            pdf_credit_usd=ext.get("eas_build_monthly_credit_usd", "") or "",
            pdf_minutes=ext.get("eas_build_monthly_minutes", "") or "",
            pdf_concurrent=ext.get("eas_build_concurrent", "") or "",
            pdf_monthly_builds=ext.get("eas_build_monthly_builds", "") or "",
            pdf_timeout_hours=ext.get("eas_build_timeout_hours", "") or "",
            pdf_unlimited=ext.get("eas_build_unlimited", "") if ext.get("eas_build_unlimited") is not None else "",
            evidence_quote=(ext.get("evidence_quote") or "")[:140],
        )
        if cs:
            row["delta_flag"] = _delta_flag(ext, cs)
        out_rows.append(row)
        print(
            f"  [{i}/{len(primary)}] {p['customer']:25} credit={ext.get('eas_build_monthly_credit_usd')!s:>6} "
            f"min={ext.get('eas_build_monthly_minutes')!s:>6} conc={ext.get('eas_build_concurrent')!s:>3}"
        )

    DATA.mkdir(exist_ok=True)
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)

    diffs = sum(1 for r in out_rows if r.get("delta_flag"))
    errs = sum(1 for r in out_rows if r.get("error"))
    print(f"\nWrote {len(out_rows)} rows -> {OUT_CSV}")
    print(f"  rows flagged with deltas vs CSV: {diffs}")
    print(f"  rows with extraction errors: {errs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
