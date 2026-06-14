"""HubSpot REST client — companies search + property updates.

Mirrors the pattern in /Users/brendensong/agents/pqa-agent/lib/hubspot_client.py:
raw HTTP via `requests`, private app token from HUBSPOT_TOKEN.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

_BASE = "https://api.hubapi.com"


def _headers() -> dict[str, str]:
    tok = os.environ.get("HUBSPOT_TOKEN")
    if not tok:
        raise RuntimeError("HUBSPOT_TOKEN not set")
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _request(method: str, path: str, **kwargs: Any) -> requests.Response:
    url = f"{_BASE}{path}"
    for attempt in range(5):
        r = requests.request(method, url, headers=_headers(), timeout=20, **kwargs)
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(min(2 ** attempt, 16))
            continue
        return r
    return r  # noqa: F821


def find_company_by_domain(domain: str) -> dict[str, Any] | None:
    """Domain match against `domain` and `website` props; returns the first hit."""
    body = {
        "filterGroups": [
            {"filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}]},
            {"filters": [{"propertyName": "website", "operator": "CONTAINS_TOKEN", "value": domain}]},
        ],
        "properties": [
            "name",
            "domain",
            "website",
            "mau_limit",
            "mau_overage_status",
            "expo_update_count",
            "expo_stripe_customer_id",
            "expo_account_id",
            "expo_current_plan_name",
        ],
        "limit": 5,
    }
    r = _request("POST", "/crm/v3/objects/companies/search", json=body)
    if r.status_code != 200:
        return None
    results = r.json().get("results") or []
    return results[0] if results else None


def update_company(company_id: str, props: dict[str, Any]) -> bool:
    r = _request(
        "PATCH",
        f"/crm/v3/objects/companies/{company_id}",
        json={"properties": props},
    )
    return r.status_code == 200


def portal_id() -> str:
    return os.environ.get("HUBSPOT_PORTAL_ID", "22007177")


def company_url(company_id: str) -> str:
    return f"https://app.hubspot.com/contacts/{portal_id()}/company/{company_id}"
