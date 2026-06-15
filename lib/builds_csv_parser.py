"""Parse the original MSA CSV for Build columns specifically — separate parsers
so we can keep monthly_minutes, concurrent, builds-count and credit USD apart."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass


@dataclass
class BuildLimits:
    customer: str
    domain: str | None
    credit_usd: int | None
    minutes: int | None
    concurrent: int | None
    monthly_builds: int | None
    timeout_hours: float | None
    raw: dict[str, str]


_NUM = re.compile(r"([\d,\.]+)")


def _to_int(raw: str) -> int | None:
    if not raw or raw.strip().lower() in ("unlimited", "n/a", "na", "-", ""):
        return None
    m = _NUM.search(raw)
    if not m:
        return None
    try:
        return int(float(m.group(1).replace(",", "")))
    except ValueError:
        return None


def _to_hours(raw: str) -> float | None:
    if not raw or raw.strip().lower() in ("unlimited", ""):
        return None
    m = _NUM.search(raw)
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if "minute" in raw.lower():
        return n / 60
    return n


def load_build_limits(path: str) -> list[BuildLimits]:
    out: list[BuildLimits] = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(
                BuildLimits(
                    customer=(r.get("customer") or "").strip(),
                    domain=((r.get("domain") or "").strip().lower()) or None,
                    credit_usd=_to_int(r.get("EAS Build — monthly_build_credit_usd") or ""),
                    minutes=_to_int(r.get("EAS Build — monthly_build_minutes") or ""),
                    concurrent=_to_int(r.get("EAS Build — concurrent_builds") or ""),
                    monthly_builds=_to_int(r.get("EAS Build — monthly_builds") or ""),
                    timeout_hours=_to_hours(r.get("EAS Build — build_timeout") or ""),
                    raw={
                        "credit": r.get("EAS Build — monthly_build_credit_usd") or "",
                        "minutes": r.get("EAS Build — monthly_build_minutes") or "",
                        "concurrent": r.get("EAS Build — concurrent_builds") or "",
                        "builds": r.get("EAS Build — monthly_builds") or "",
                        "timeout": r.get("EAS Build — build_timeout") or "",
                    },
                )
            )
    return out
