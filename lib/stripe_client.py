"""Tiny Stripe helper — we just need a dashboard deep-link for the Slack button.

The customer id is stored on the HubSpot company as `expo_stripe_customer_id`,
so this module doesn't need to call the Stripe API.
"""
from __future__ import annotations


def customer_url(stripe_customer_id: str | None) -> str | None:
    if not stripe_customer_id:
        return None
    return f"https://dashboard.stripe.com/customers/{stripe_customer_id}"
