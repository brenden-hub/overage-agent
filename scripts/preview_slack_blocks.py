#!/usr/bin/env python3
"""Print a sample overage Block Kit payload to stdout so you can paste into
https://app.slack.com/block-kit-builder/ to preview the formatting.
No network calls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import slack_client  # noqa: E402


def main() -> int:
    blocks = slack_client.build_overage_blocks(
        company_name="AGL",
        domain="agl.com.au",
        plan="EAS Enterprise",
        current_mau=1_487_213,
        mau_limit=1_000_000,
        hubspot_url="https://app.hubspot.com/contacts/22007177/company/12345",
        stripe_url="https://dashboard.stripe.com/customers/cus_EXAMPLE",
    )
    payload = {"text": "AGL is over MAU limit: 1,487,213 of 1,000,000 (49% over)", "blocks": blocks}
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
