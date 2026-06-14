"""Parse the MSA limits CSV into normalized records keyed by domain."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from typing import Iterable


@dataclass
class MsaLimit:
    customer: str
    domain: str | None
    mau_limit: int | None
    closedate: str | None
    raw_mau: str


# Multiplier suffix must be glued to the number (e.g. "1.5M"), not separated by
# whitespace — otherwise "200 MAUs/month" would be read as 200,000,000 because
# the leading 'M' of "MAUs" looks like a millions suffix.
_NUM_SUFFIX = re.compile(r"([\d,\.]+)([KkMmBb])\b")
_NUM_PLAIN = re.compile(r"([\d,\.]+)")


def parse_mau(raw: str) -> int | None:
    """Convert strings like '1000000 users/month', '1.5M MAUs/month', '200 MAUs/month' to int."""
    if not raw:
        return None
    s = raw.strip()
    if not s or s.lower() in ("unlimited", "n/a", "na", "-"):
        return None
    m = _NUM_SUFFIX.search(s)
    if m:
        n_str, suffix = m.groups()
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix.lower()]
    else:
        m = _NUM_PLAIN.search(s)
        if not m:
            return None
        n_str, mult = m.group(1), 1
    try:
        n = float(n_str.replace(",", ""))
    except ValueError:
        return None
    return int(n * mult)


def load_msa_limits(path: str) -> list[MsaLimit]:
    rows: list[MsaLimit] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            raw_mau = (r.get("EAS Update — MAU") or "").strip()
            rows.append(
                MsaLimit(
                    customer=(r.get("customer") or "").strip(),
                    domain=(r.get("domain") or "").strip().lower() or None,
                    mau_limit=parse_mau(raw_mau),
                    closedate=(r.get("latest_closedate") or "").strip() or None,
                    raw_mau=raw_mau,
                )
            )
    return rows


def with_mau_limit(rows: Iterable[MsaLimit]) -> list[MsaLimit]:
    return [r for r in rows if r.mau_limit is not None and r.domain]
