"""Slack incoming-webhook poster — Block Kit only.

Mirrors lib/slack_client.py in the PQA agent: raw requests.post() with
exponential backoff, no slack_sdk dependency. Webhook URL is read from
SLACK_WEBHOOK_URL. If DRY_RUN=true (default), the payload is logged and not sent.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import requests


def _dry_run() -> bool:
    return os.environ.get("DRY_RUN", "true").lower() not in ("false", "0", "no")


def _post_with_retry(url: str, payload: dict[str, Any]) -> bool:
    for attempt in range(6):
        try:
            r = requests.post(url, json=payload, timeout=15)
        except requests.RequestException as e:
            print(f"[slack] network err (attempt {attempt + 1}): {e}")
            time.sleep(min(2 * (1.6 ** attempt), 60))
            continue
        if r.status_code < 300:
            return True
        if r.status_code in (429,) or 500 <= r.status_code < 600:
            time.sleep(min(2 * (1.6 ** attempt), 60))
            continue
        print(f"[slack] failed {r.status_code}: {r.text[:200]}")
        return False
    return False


def build_overage_blocks(
    *,
    company_name: str,
    domain: str,
    plan: str | None,
    current_mau: int,
    mau_limit: int,
    hubspot_url: str,
    stripe_url: str | None,
    owner_mention: str | None = None,
) -> list[dict[str, Any]]:
    """Mirrors the PQA agent's block layout: header → summary section → action
    buttons → footer context. `owner_mention` should be like `<@U0ABC1234>`;
    if present, a dedicated section calls out the owner so they get @-pinged."""
    overage = current_mau - mau_limit
    ratio = current_mau / mau_limit if mau_limit else 0
    pct_over = (overage / mau_limit * 100) if mau_limit else 0
    emoji = "🚨" if pct_over >= 50 else "⚠️"

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} MAU Overage · {company_name}"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"`{domain}`" + (f" · *Plan:* {plan}" if plan else "")},
            ],
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*MAU Limit*\n{mau_limit:,}"},
                {"type": "mrkdwn", "text": f"*Current MAUs (30d)*\n{current_mau:,}"},
                {"type": "mrkdwn", "text": f"*Overage*\n+{overage:,} ({pct_over:.0f}% over)"},
                {"type": "mrkdwn", "text": f"*Ratio*\n{ratio:.2f}×"},
            ],
        },
        {"type": "divider"},
    ]

    action_elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Open in HubSpot"},
            "url": hubspot_url,
            "style": "primary",
        },
    ]
    if stripe_url:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open in Stripe"},
                "url": stripe_url,
            }
        )
    blocks.append({"type": "actions", "elements": action_elements})

    footer_text = "overage-agent · #revenue_overages"
    if owner_mention:
        # Put the @-mention in its own section so Slack fires the notification
        # (mentions inside `context` blocks don't always ping reliably).
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Owner:* {owner_mention}"},
            }
        )
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": footer_text}],
        }
    )
    return blocks


def post_overage(blocks: list[dict[str, Any]], *, summary: str) -> bool:
    """Send the Block Kit message to SLACK_WEBHOOK_URL. No-op when DRY_RUN=true."""
    payload = {"text": summary, "blocks": blocks}
    url = os.environ.get("SLACK_WEBHOOK_URL")

    if _dry_run():
        print(f"[slack DRY_RUN] would post: {summary}")
        print(json.dumps(payload, indent=2))
        return True

    if not url:
        print(f"[slack] skipped (no SLACK_WEBHOOK_URL): {summary}")
        return False

    return _post_with_retry(url, payload)
