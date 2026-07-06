"""HubSpot company-owner → Slack user mapping.

Keyed by HubSpot owner_id (stable) rather than email because some Expo owners
have differing email addresses in HubSpot (@expo.io) vs Slack (@expo.dev).

To regenerate: query HubSpot owners, then Slack users.lookupByEmail, then
paste the result here. The mapping is intentionally checked in so the cron
doesn't need Slack MCP access at runtime — it just uses these IDs verbatim.
"""
from __future__ import annotations

# HubSpot owner_id -> (Slack user_id, display name, hubspot email)
OWNER_TO_SLACK: dict[str, tuple[str, str, str]] = {
    "93614246":  ("U0B9QMBJY48", "Tyler Last",           "tyler@expo.dev"),
    "87888369":  ("U0ACAAG258S", "Sarah Rico",           "srico@expo.dev"),
    # Alex + Joe: HubSpot lists @expo.io, Slack is @expo.dev — matched by name.
    "81666260":  ("U096EJ15048", "Alex Fopma",           "alex@expo.io"),
    "78704863":  ("U08JB3HJFHU", "Joe Ryan",             "joe@expo.io"),
    "85769974":  ("U0A1M5PCHBJ", "Jaden delaConcepcion", "jaden@expo.dev"),
    "535744645": ("U05QR137YCU", "Dan Kelly",            "dan@expo.dev"),
    "85275863":  ("U09SDUNLAHH", "Colin Hunt",           "hunt@expo.dev"),
}


def slack_mention_for(owner_id: str | None) -> str | None:
    """Return `<@Uxxxx>` for the given HubSpot owner_id, or None if unmapped.

    Slack renders `<@Uxxxx>` as an actual @-mention in messages sent through
    the incoming webhook."""
    if not owner_id:
        return None
    entry = OWNER_TO_SLACK.get(str(owner_id))
    if not entry:
        return None
    return f"<@{entry[0]}>"


def owner_display_name(owner_id: str | None) -> str | None:
    if not owner_id:
        return None
    entry = OWNER_TO_SLACK.get(str(owner_id))
    return entry[1] if entry else None
