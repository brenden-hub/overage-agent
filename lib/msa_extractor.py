"""Send a downloaded MSA PDF to Claude (vision/document) and pull the contract
limits out as structured JSON. Mirrors the columns in the original
msa_limits_normalized CSV."""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

from anthropic import Anthropic


_SYSTEM = """You are a contract-extraction agent. The user will give you the
PDF of an Expo enterprise MSA / Order Form. Find Exhibit A (or the "Description
of Services / Limits" section) and extract the numeric limits.

Return ONLY a JSON object — no prose, no markdown fence — with these keys, and
use null when the document does not specify a value:

{
  "eas_update_mau": <int or null>,                   // Monthly Active Users (MAUs) for EAS Update
  "eas_update_bandwidth_tib_month": <number or null>,
  "eas_update_storage_tib_month": <number or null>,
  "eas_build_monthly_credit_usd": <number or null>,
  "eas_build_concurrent": <int or null>,
  "eas_build_monthly_builds": <int or null>,
  "eas_build_timeout_hours": <number or null>,
  "eas_workflows_credit_usd_month": <number or null>,
  "eas_submit_unlimited": <true|false|null>,
  "enterprise_seats": <int or "unlimited" or null>,
  "enterprise_projects": <int or "unlimited" or null>,
  "enterprise_organizations": <int or null>,
  "priority_support_response_time": <string or null>,
  "evidence_quote": <string>                         // short verbatim snippet
}

Rules:
- Convert "1M" -> 1000000, "1.5M" -> 1500000, "500K" -> 500000.
- For bandwidth/storage, return the number of TiB only (drop "/month").
- For dollar values, return the integer USD only.
- If the doc lists multiple values, prefer Exhibit A's table over body prose.
- evidence_quote: <=120 chars, copied verbatim from the contract, that
  supports the MAU value.
"""


def _model() -> str:
    return os.environ.get("OVERAGE_MODEL", "claude-opus-4-7")


_BUILDS_SYSTEM = """You are a contract-extraction agent. The user will give you
the PDF of an Expo enterprise MSA / Order Form. Find Exhibit A (or the
"Description of Services / Limits" section) and extract the EAS BUILD limits
ONLY.

Return ONLY a JSON object — no prose, no markdown fence — with these keys, and
use null when the document does not specify a value:

{
  "eas_build_monthly_credit_usd": <number or null>,
  "eas_build_monthly_minutes": <int or null>,            // e.g. "50000 minutes/month" -> 50000
  "eas_build_concurrent": <int or null>,                 // concurrent builds / concurrencies
  "eas_build_monthly_builds": <int or null>,             // total builds per month, NOT minutes
  "eas_build_timeout_hours": <number or null>,           // e.g. "2 hours" -> 2
  "eas_build_unlimited": <true|false|null>,
  "evidence_quote": <string>                             // <=140 char verbatim snippet
}

Rules:
- Convert "1M" -> 1000000, "999/month" -> 999.
- monthly_minutes vs monthly_builds are different fields — minutes is "build
  time", monthly_builds is "count of builds". Don't conflate them.
- timeout is hours — "2 hours" -> 2, "120 minutes" -> 2.
- evidence_quote: copy verbatim from the contract.
"""


def extract_build_limits(pdf_path: str | Path) -> dict:
    """Build-focused second pass — pulls the 5 build fields the original
    extraction missed or only partially captured."""
    pdf_bytes = Path(pdf_path).read_bytes()
    b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    client = Anthropic()
    resp = client.messages.create(
        model=_model(),
        max_tokens=600,
        system=[{"type": "text", "text": _BUILDS_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": "Extract EAS Build limits. JSON only."},
                ],
            }
        ],
    )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    return _parse_json(text)


def extract_from_pdf(pdf_path: str | Path) -> dict:
    pdf_bytes = Path(pdf_path).read_bytes()
    b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")

    client = Anthropic()
    resp = client.messages.create(
        model=_model(),
        max_tokens=1200,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Extract the limits from this MSA. JSON only.",
                    },
                ],
            }
        ],
    )

    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    return _parse_json(text)


def _parse_json(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            return json.loads(m.group(0))
        return {"_parse_error": text[:500]}
