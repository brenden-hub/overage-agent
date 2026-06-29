#!/usr/bin/env python3
"""For each company in the MSA CSV, find the MOST RECENT contract-like PDF in
HubSpot, extract its MAU limit, and PATCH the HubSpot `mau_limit` to that
value.

Uses the subscription-backed Claude Code CLI (`claude -p`) instead of the
Anthropic API — no API credit drain. Cache-aware: file_ids already present in
`data/extractions_cache.jsonl` are reused without re-calling Claude.

By default, scans every company. With `--recent-days N`, restricts to companies
whose deals have been modified in the last N days (much smaller nightly load).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from lib import csv_parser, hubspot_client, hubspot_files, msa_extractor_cli

DOWNLOADS = ROOT / "downloads"; DOWNLOADS.mkdir(exist_ok=True)
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)
CACHE_FILE = DATA / "extractions_cache.jsonl"


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


def append_cache(file_id: str, name: str, extraction: dict) -> None:
    with CACHE_FILE.open("a") as f:
        f.write(json.dumps({"file_id": file_id, "name": name, "extraction": extraction}) + "\n")


def company_has_recent_activity(company_id: str, since_iso: str) -> bool:
    """True if any of the company's deals was modified since the watermark."""
    import requests, os
    H = {"Authorization": f"Bearer {os.environ['HUBSPOT_TOKEN']}", "Content-Type":"application/json"}
    deal_ids = hubspot_files.deals_for_company(company_id)
    if not deal_ids:
        return False
    for i in range(0, len(deal_ids), 100):
        chunk = deal_ids[i : i + 100]
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/deals/batch/read",
            headers=H, timeout=20,
            json={"inputs":[{"id":str(d)} for d in chunk],
                  "properties":["hs_lastmodifieddate"]},
        )
        if r.status_code != 200:
            continue
        for d in r.json().get("results", []):
            ts = (d.get("properties") or {}).get("hs_lastmodifieddate") or ""
            if ts and ts >= since_iso:
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", nargs="?",
                    default=str(Path.home() / "Downloads"
                                / "Enterprise Limits from MSA's - msa_limits_normalized (2).csv"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="Comma-separated customer names")
    ap.add_argument("--recent-days", type=int, default=0,
                    help="Only scan companies whose deals were modified in the last N days (0 = no filter).")
    args = ap.parse_args()

    rows = csv_parser.load_msa_limits(args.csv_path)
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",")}
        rows = [r for r in rows if r.customer.lower() in wanted]
    rows = [r for r in rows if r.domain]

    since_iso = ""
    if args.recent_days > 0:
        since = datetime.now(timezone.utc) - timedelta(days=args.recent_days)
        since_iso = since.isoformat()
        print(f"Activity filter: only companies with deals modified since {since_iso}")

    cache = load_cache()
    print(f"Cache contains {len(cache)} prior extractions")

    changed = same = no_pdf = no_co = errors = skipped_inactive = cache_hits = 0
    for r in rows:
        co = hubspot_client.find_company_by_domain(r.domain)  # type: ignore[arg-type]
        if not co:
            no_co += 1
            continue

        if args.recent_days > 0 and not company_has_recent_activity(co["id"], since_iso):
            skipped_inactive += 1
            continue

        latest = next(iter(hubspot_files.iter_msa_candidates_for_company(co["id"])), None)
        if not latest:
            no_pdf += 1
            continue

        fid = latest["file_id"]

        # Cache hit — no Claude call.
        if fid in cache:
            ext = cache[fid].get("extraction") or {}
            cache_hits += 1
        else:
            pdf_path = DOWNLOADS / f"{fid}.pdf"
            if not pdf_path.exists():
                if not hubspot_files.download(fid, str(pdf_path)):
                    print(f"  [DL FAIL] {r.customer:25} {latest['name'][:50]}")
                    errors += 1
                    continue
            try:
                ext = msa_extractor_cli.extract_from_pdf(pdf_path)
            except Exception as e:
                print(f"  [EXT FAIL] {r.customer:25} {e!s}[:120]")
                errors += 1
                continue
            if not isinstance(ext, dict) or ext.get("_parse_error"):
                print(f"  [PARSE FAIL] {r.customer:25} {ext}")
                errors += 1
                continue
            append_cache(fid, latest["name"], ext)

        new_mau = ext.get("eas_update_mau")
        if not isinstance(new_mau, int):
            print(f"  [no mau ] {r.customer:25} ts={latest['note_ts'][:10]} file={latest['name'][:45]}")
            continue

        current = (co.get("properties") or {}).get("mau_limit")
        current_int = int(float(current)) if current not in (None, "") else None
        if current_int == new_mau:
            same += 1
            cache_tag = " (cached)" if fid in cache else ""
            print(f"  [ok same] {r.customer:25} ts={latest['note_ts'][:10]} mau={new_mau:>10,}{cache_tag}")
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
        f"\nDone. changed={changed} same={same} cache_hits={cache_hits} "
        f"skipped_inactive={skipped_inactive} no_pdf={no_pdf} no_co={no_co} "
        f"errors={errors} total={len(rows)}"
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
