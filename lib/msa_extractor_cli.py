"""Subscription-backed MSA extractor — replaces the Anthropic API SDK with the
local `claude -p` Claude Code CLI so calls draw from the user's Pro/Max
subscription quota instead of API credits.

Flow:
  1. Convert the PDF to plain text locally via pypdf (free).
  2. Pipe a structured prompt + that text into `claude -p --output-format text`.
  3. Parse the JSON the model returns.

The cache lives in `data/extractions_cache.jsonl` (keyed by HubSpot file_id).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pypdf


_SYSTEM = """You are a contract-extraction agent for Expo enterprise contracts.

Given the extracted text of an MSA / Order Form / Renewal, return ONLY a JSON
object — no prose, no markdown fence — with these keys, using null when the
document does not specify a value:

{
  "eas_update_mau": <int or null>,
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
  "evidence_quote": <string>
}

Rules:
- Convert "1M" -> 1000000, "1.5M" -> 1500000, "500K" -> 500000.
- For bandwidth/storage, return the number of TiB only (drop "/month").
- For dollar values, return the integer USD only (per month).
- If a value is annual, convert to per-month (e.g. $67,700/yr -> 5641).
- evidence_quote: <=120 chars verbatim snippet supporting the MAU value.
"""


def _claude_path() -> str:
    p = shutil.which("claude")
    if p:
        return p
    # Common install location for Claude Code on Mac
    candidates = [
        os.path.expanduser("~/.superset/bin/claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise RuntimeError("claude CLI not found in PATH or known locations")


def pdf_to_text(pdf_path: str | Path, max_pages: int = 30) -> str:
    """Extract plaintext from a PDF using pypdf. Limited to first N pages so
    Exhibit A / Order Form pricing tables stay in the prompt budget."""
    reader = pypdf.PdfReader(str(pdf_path))
    pages = reader.pages[:max_pages]
    out: list[str] = []
    for i, page in enumerate(pages, start=1):
        try:
            out.append(f"--- Page {i} ---\n{page.extract_text() or ''}")
        except Exception:
            continue
    return "\n\n".join(out)


def extract_from_pdf(pdf_path: str | Path, *, timeout: int = 180, retries: int = 4) -> dict:
    """Send the PDF text through `claude -p` and return the parsed JSON.

    Subscription-funded — no Anthropic API key needed. Retries with exponential
    backoff on transient `claude` CLI failures (rc != 0 or empty stdout), which
    happen when several CLI calls land in the same rate-limit window."""
    import time

    text = pdf_to_text(pdf_path)
    if not text.strip():
        return {"_parse_error": "pdf had no extractable text"}

    prompt = f"{_SYSTEM}\n\n=== CONTRACT TEXT ===\n{text}\n=== END ===\n\nReturn JSON only."
    if len(prompt) > 180_000:
        prompt = prompt[:180_000] + "\n[...truncated]"

    # Strip Claude Code / Superset wrapper env vars: when we're invoked from
    # inside an interactive `claude` session, the nested `claude -p` call sees
    # CLAUDECODE / SUPERSET_* and refuses to run. The cron environment doesn't
    # have these, but sanitizing here keeps manual testing usable too.
    clean_env = {
        k: v for k, v in os.environ.items()
        if not (k.startswith(("CLAUDE_", "CLAUDECODE", "SUPERSET_")))
    }
    # Keep ANTHROPIC_API_KEY out of the clean env on principle — claude CLI uses
    # subscription auth from ~/.claude/, not the env API key.
    clean_env.pop("ANTHROPIC_API_KEY", None)

    last_err: str | None = None
    for attempt in range(retries):
        if attempt > 0:
            # 4s, 12s, 30s — gives the subscription rate-limit window room.
            time.sleep(min(4 * (3 ** (attempt - 1)), 60))
        proc = subprocess.run(
            [_claude_path(), "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=clean_env,
            start_new_session=True,
        )
        if proc.returncode != 0:
            last_err = f"claude exit {proc.returncode}: {proc.stderr[:200]!r}"
            continue
        raw = proc.stdout.strip()
        if not raw:
            last_err = "empty stdout"
            continue
        parsed = _parse_json(raw)
        if isinstance(parsed, dict) and "_parse_error" not in parsed:
            return parsed
        last_err = f"unparseable: {raw[:200]!r}"

    return {"_parse_error": last_err or "unknown"}


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
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {"_parse_error": text[:500]}
