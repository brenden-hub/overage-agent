"""Walk closed-won deals → notes → attached PDFs (MSAs, Order Forms).

HubSpot's data model: files attach to *notes*, and notes associate to deals
(and companies). To find an MSA we have to:
  deal -> associated notes -> note.hs_attachment_ids -> file metadata + signed URL
"""
from __future__ import annotations

import os
import time
from typing import Iterator

import requests


def _h() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['HUBSPOT_TOKEN']}",
        "Content-Type": "application/json",
    }


def _get(path: str, **kw) -> requests.Response:
    for attempt in range(4):
        r = requests.get(f"https://api.hubapi.com{path}", headers=_h(), timeout=30, **kw)
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(2 ** attempt)
            continue
        return r
    return r  # type: ignore[return-value]


def _post(path: str, body: dict) -> requests.Response:
    for attempt in range(4):
        r = requests.post(
            f"https://api.hubapi.com{path}", headers=_h(), json=body, timeout=30
        )
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(2 ** attempt)
            continue
        return r
    return r  # type: ignore[return-value]


def deals_for_company(company_id: str) -> list[str]:
    r = _get(f"/crm/v4/objects/companies/{company_id}/associations/deals?limit=100")
    if r.status_code != 200:
        return []
    return [a["toObjectId"] for a in (r.json().get("results") or [])]


def closed_won_deals(deal_ids: list[str]) -> list[dict]:
    """Batch-fetch deals with stage + closedate; return only closedwon."""
    if not deal_ids:
        return []
    out: list[dict] = []
    # batch read
    for i in range(0, len(deal_ids), 100):
        chunk = deal_ids[i : i + 100]
        body = {
            "inputs": [{"id": str(d)} for d in chunk],
            "properties": ["dealname", "dealstage", "closedate", "amount"],
        }
        r = _post("/crm/v3/objects/deals/batch/read", body)
        if r.status_code != 200:
            continue
        for d in r.json().get("results", []):
            if (d.get("properties") or {}).get("dealstage") == "closedwon":
                out.append(d)
    return out


def notes_for_deal(deal_id: str) -> list[str]:
    r = _get(f"/crm/v4/objects/deals/{deal_id}/associations/notes?limit=100")
    if r.status_code != 200:
        return []
    return [a["toObjectId"] for a in (r.json().get("results") or [])]


def notes_for_company(company_id: str) -> list[str]:
    r = _get(f"/crm/v4/objects/companies/{company_id}/associations/notes?limit=200")
    if r.status_code != 200:
        return []
    return [a["toObjectId"] for a in (r.json().get("results") or [])]


def attachment_ids_for_notes(note_ids: list[str]) -> list[str]:
    if not note_ids:
        return []
    body = {
        "inputs": [{"id": str(n)} for n in note_ids],
        "properties": ["hs_attachment_ids", "hs_note_body", "hs_timestamp"],
    }
    r = _post("/crm/v3/objects/notes/batch/read", body)
    if r.status_code != 200:
        return []
    attids: list[str] = []
    for n in r.json().get("results", []):
        raw = (n.get("properties") or {}).get("hs_attachment_ids") or ""
        for fid in raw.split(";"):
            fid = fid.strip()
            if fid:
                attids.append(fid)
    return attids


def file_metadata(file_id: str) -> dict | None:
    r = _get(f"/files/v3/files/{file_id}")
    if r.status_code != 200:
        return None
    return r.json()


def signed_download_url(file_id: str) -> str | None:
    r = _get(f"/files/v3/files/{file_id}/signed-url")
    if r.status_code != 200:
        return None
    return r.json().get("url")


def download(file_id: str, dest_path: str) -> bool:
    """Download via /files/v3/files/{id}/download — it streams the bytes through
    the authenticated API. The /signed-url endpoint returns CDN URLs that 404 for
    HIDDEN_PRIVATE files attached to notes, so this is the only reliable path."""
    r = requests.get(
        f"https://api.hubapi.com/files/v3/files/{file_id}/download",
        headers=_h(),
        timeout=60,
        allow_redirects=True,
    )
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        # Fall back to the signed-url path (works for files in the public CDN).
        url = signed_download_url(file_id)
        if not url:
            return False
        r = requests.get(url, timeout=60, allow_redirects=True)
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            return False
    with open(dest_path, "wb") as f:
        f.write(r.content)
    return True


def iter_msa_candidates_for_company(company_id: str) -> Iterator[dict]:
    """Yield {file_id, name, ext, size, deal_id, deal_name, note_ts} for every PDF
    on any associated deal's notes (plus company notes) whose filename looks
    like an MSA / Order Form / Renewal / etc.

    Yields newest-first by note timestamp so callers can take the freshest
    contract as the source of truth. We deliberately do NOT filter to
    closed-won here — the Renewals pipeline uses different stage IDs than
    Sales, and signed MSAs often appear on a renewal deal before it closes."""
    deal_ids = deals_for_company(company_id)
    # Fetch deal metadata so the yielded row knows the parent deal name/closedate,
    # but include notes from every stage (Sales + Renewals pipelines).
    all_deals: list[dict] = []
    for i in range(0, len(deal_ids), 100):
        chunk = deal_ids[i : i + 100]
        r = _post(
            "/crm/v3/objects/deals/batch/read",
            {
                "inputs": [{"id": str(d)} for d in chunk],
                "properties": ["dealname", "dealstage", "closedate", "amount", "pipeline"],
            },
        )
        if r.status_code == 200:
            all_deals.extend(r.json().get("results", []))

    candidates: list[tuple[str, dict | None]] = []
    for nid in notes_for_company(company_id):
        candidates.append((nid, None))
    for d in all_deals:
        for nid in notes_for_deal(d["id"]):
            candidates.append((nid, d))

    note_to_deal: dict[str, dict | None] = {n: d for n, d in candidates}
    note_ids = list(note_to_deal.keys())
    if not note_ids:
        return

    r = _post(
        "/crm/v3/objects/notes/batch/read",
        {
            "inputs": [{"id": str(n)} for n in note_ids],
            "properties": ["hs_attachment_ids", "hs_timestamp"],
        },
    )
    if r.status_code != 200:
        return

    seen: set[str] = set()
    rows: list[dict] = []
    for n in r.json().get("results", []):
        nid = n.get("id")
        props = n.get("properties") or {}
        ts = props.get("hs_timestamp") or ""
        raw = props.get("hs_attachment_ids") or ""
        deal = note_to_deal.get(nid)
        for fid in raw.split(";"):
            fid = fid.strip()
            if not fid or fid in seen:
                continue
            seen.add(fid)
            meta = file_metadata(fid)
            if not meta:
                continue
            ext = (meta.get("extension") or "").lower()
            name = meta.get("name") or ""
            if ext != "pdf" or not _looks_like_msa(name):
                continue
            rows.append(
                {
                    "file_id": fid,
                    "name": name,
                    "ext": ext,
                    "size": meta.get("size"),
                    "note_ts": ts,
                    "deal_id": deal["id"] if deal else None,
                    "deal_name": (deal["properties"] or {}).get("dealname") if deal else None,
                    "deal_closedate": (deal["properties"] or {}).get("closedate") if deal else None,
                }
            )

    rows.sort(key=lambda r: (r["note_ts"], r.get("deal_closedate") or ""), reverse=True)
    yield from rows


_MSA_KEYWORDS = (
    "msa",
    "master service",
    "order form",
    "order_form",
    "orderform",
    "exhibit",
    "agreement",
    "contract",
    "service_agreement",
    "service agreement",
    # Renewals + expansions almost always carry the current limits; the original
    # MSA can be years stale. Real example: Wealthsimple's 2026 renewal said 3M
    # MAUs while the 2025 MSA said 1M.
    "renewal",
    "expansion",
    "proposal",
    "amendment",
    "addendum",
    "sow",
    "subscription",
    "executed",
)


def _looks_like_msa(filename: str) -> bool:
    s = filename.lower()
    return any(k in s for k in _MSA_KEYWORDS)
