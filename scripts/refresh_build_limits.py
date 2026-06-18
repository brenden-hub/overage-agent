#!/usr/bin/env python3
"""Build-credit refresh — same pattern as refresh_with_latest_msa.py but
extracts the EAS Build limits from the NEWEST contract per company.

Writes `data/refreshed_build_limits.csv` with side-by-side columns:
  old (= extracted_build_limits.csv pdf_credit_usd) vs new (newest contract)

Idempotent: extractions are cached in `data/build_extractions_cache.jsonl`.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from lib import csv_parser, hubspot_client, hubspot_files, msa_extractor

DOWNLOADS = ROOT / "downloads"; DOWNLOADS.mkdir(exist_ok=True)
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)
CACHE = DATA / "build_extractions_cache.jsonl"
OUT = DATA / "refreshed_build_limits.csv"


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


def load_old_build_csv() -> dict[str, dict]:
    """customer -> first row in extracted_build_limits.csv (the values we have today)"""
    p = DATA / "extracted_build_limits.csv"
    if not p.exists():
        return {}
    out: dict[str, dict] = {}
    for r in csv.DictReader(open(p)):
        out.setdefault(r["customer"], r)
    return out


COLUMNS = [
    "customer", "domain", "newest_note_ts", "newest_file_id", "newest_file_name",
    "old_credit_usd", "new_credit_usd", "credit_delta",
    "old_minutes", "new_minutes",
    "old_concurrent", "new_concurrent",
    "old_monthly_builds", "new_monthly_builds",
    "old_timeout_hours", "new_timeout_hours",
    "evidence_quote",
    "error",
]


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", nargs="?", default=str(
        Path.home() / "Downloads"
        / "Enterprise Limits from MSA's - msa_limits_normalized (2).csv"))
    ap.add_argument("--only", help="Comma-separated customer names")
    args = ap.parse_args()

    rows = csv_parser.load_msa_limits(args.csv_path)
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",")}
        rows = [r for r in rows if r.customer.lower() in wanted]
    rows = [r for r in rows if r.domain]

    cache = load_cache()
    old_by_cust = load_old_build_csv()

    out_rows: list[dict] = []
    n_changed = n_same = n_no_pdf = n_no_co = n_errors = 0

    for r in rows:
        co = hubspot_client.find_company_by_domain(r.domain)  # type: ignore[arg-type]
        if not co:
            n_no_co += 1; continue
        latest = next(iter(hubspot_files.iter_msa_candidates_for_company(co["id"])), None)
        if not latest:
            n_no_pdf += 1
            print(f"  [no pdf] {r.customer}")
            continue

        fid = latest["file_id"]
        pdf_path = DOWNLOADS / f"{fid}.pdf"

        # Extract (cache hit if seen before)
        if fid in cache:
            ext = cache[fid].get("extraction") or {}
            note = "cache"
        else:
            if not pdf_path.exists():
                if not hubspot_files.download(fid, str(pdf_path)):
                    n_errors += 1
                    print(f"  [DL FAIL] {r.customer}")
                    continue
            try:
                last_err = None
                for attempt in range(4):
                    try:
                        time.sleep(1 + attempt * 3)
                        ext = msa_extractor.extract_build_limits(pdf_path)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = str(e)[:120]
                        ext = None
                if not ext:
                    n_errors += 1
                    print(f"  [EXT FAIL] {r.customer:30} {last_err}")
                    continue
                append_cache({"file_id": fid, "name": latest["name"], "extraction": ext})
            except Exception as e:
                n_errors += 1
                print(f"  [EXT FAIL] {r.customer:30} {e!s}[:120]")
                continue
            note = "fresh"

        old_row = old_by_cust.get(r.customer, {})
        old_credit = _f(old_row.get("pdf_credit_usd"))
        new_credit = _f(ext.get("eas_build_monthly_credit_usd"))

        out = {col: "" for col in COLUMNS}
        out.update(
            customer=r.customer, domain=r.domain,
            newest_note_ts=latest["note_ts"][:19],
            newest_file_id=fid, newest_file_name=latest["name"],
            old_credit_usd=int(old_credit) if old_credit is not None else "",
            new_credit_usd=int(new_credit) if new_credit is not None else "",
            credit_delta=(int(new_credit) - int(old_credit)) if (old_credit is not None and new_credit is not None) else "",
            old_minutes=old_row.get("pdf_minutes",""),
            new_minutes=ext.get("eas_build_monthly_minutes") or "",
            old_concurrent=old_row.get("pdf_concurrent",""),
            new_concurrent=ext.get("eas_build_concurrent") or "",
            old_monthly_builds=old_row.get("pdf_monthly_builds",""),
            new_monthly_builds=ext.get("eas_build_monthly_builds") or "",
            old_timeout_hours=old_row.get("pdf_timeout_hours",""),
            new_timeout_hours=ext.get("eas_build_timeout_hours") or "",
            evidence_quote=(ext.get("evidence_quote") or "")[:140],
        )

        if old_credit is not None and new_credit is not None and old_credit != new_credit:
            n_changed += 1
            print(f"  [DIFF ] {r.customer:30} ts={latest['note_ts'][:10]} ${int(old_credit):>6,} -> ${int(new_credit):>6,}  file={latest['name'][:45]}")
        elif new_credit is not None:
            n_same += 1
            print(f"  [same ] {r.customer:30} ts={latest['note_ts'][:10]} ${int(new_credit):>6,}  ({note})  file={latest['name'][:45]}")
        else:
            print(f"  [no credit] {r.customer:30} ts={latest['note_ts'][:10]} file={latest['name'][:45]}")

        out_rows.append(out)

    # Write CSV
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out_rows)

    print(
        f"\nDone. changed={n_changed} same={n_same} no_pdf={n_no_pdf} "
        f"no_co={n_no_co} errors={n_errors}"
    )
    print(f"Wrote {OUT}")
    return 0 if n_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
