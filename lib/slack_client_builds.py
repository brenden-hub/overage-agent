"""Slack Block Kit builder for EAS Build credit overage alerts.

Mirrors the layout of lib/slack_client.build_overage_blocks (MAU version) so
the two message families feel like they came from the same agent. Adds a
per-platform build-mix section since that's the story reps need to see.
"""
from __future__ import annotations

from typing import Any


def build_build_overage_blocks(
    *,
    company_name: str,
    domain: str,
    plan: str | None,
    credit_limit_usd: float,
    spend_30d_usd: float,
    android_medium: int,
    ios_medium: int,
    android_large: int,
    ios_large: int,
    hubspot_url: str,
    stripe_url: str | None,
    owner_mention: str | None = None,
) -> list[dict[str, Any]]:
    overage = spend_30d_usd - credit_limit_usd
    ratio = spend_30d_usd / credit_limit_usd if credit_limit_usd else 0
    pct_over = (overage / credit_limit_usd * 100) if credit_limit_usd else 0
    emoji = "🚨" if pct_over >= 100 else "⚠️"

    # Per-platform contribution (matches your pricing exactly)
    a_med_cost = android_medium * 1
    i_med_cost = ios_medium * 2
    a_lrg_cost = android_large * 2
    i_lrg_cost = ios_large * 4

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} Build Credit Overage · {company_name}"},
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
                {"type": "mrkdwn", "text": f"*Build Credit (contract)*\n${credit_limit_usd:,.0f}/mo"},
                {"type": "mrkdwn", "text": f"*30d Build Spend*\n${spend_30d_usd:,.0f}"},
                {"type": "mrkdwn", "text": f"*Overage*\n+${overage:,.0f} ({pct_over:.0f}% over)"},
                {"type": "mrkdwn", "text": f"*Ratio*\n{ratio:.2f}×"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*30-day build mix* (BigQuery)\n"
                    f"• Android medium: {android_medium:,} × $1 = ${a_med_cost:,}\n"
                    f"• iOS medium: {ios_medium:,} × $2 = ${i_med_cost:,}\n"
                    f"• Android large: {android_large:,} × $2 = ${a_lrg_cost:,}\n"
                    f"• iOS large: {ios_large:,} × $4 = ${i_lrg_cost:,}"
                ),
            },
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
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Open in Stripe"},
            "url": stripe_url,
        })
    blocks.append({"type": "actions", "elements": action_elements})

    if owner_mention:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Owner:* {owner_mention}"},
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "overage-agent · builds · #revenue_overages"}],
    })
    return blocks
