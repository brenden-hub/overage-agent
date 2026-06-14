"""Tiny Claude wrapper — drafts the one-liner summary that goes in the Slack
notification's text fallback (for mobile / notifications), and can be extended
to reason about whether an account warrants escalation.

Uses the Anthropic SDK directly. Model defaults to claude-opus-4-7 (latest Opus
in the Claude 4.x family at time of writing); override with OVERAGE_MODEL.
"""
from __future__ import annotations

import os

from anthropic import Anthropic

_SYSTEM = """You are the Expo Overage Agent.
Given an enterprise account's contract MAU limit and its current 30-day MAU
usage, write a single-sentence Slack notification summary (<= 140 chars).
Be factual and quantitative. No emojis. No salutation."""


def _model() -> str:
    return os.environ.get("OVERAGE_MODEL", "claude-opus-4-7")


def draft_summary(
    *,
    company_name: str,
    mau_limit: int,
    current_mau: int,
    plan: str | None,
) -> str:
    client = Anthropic()
    user = (
        f"Account: {company_name}\n"
        f"Plan: {plan or 'unknown'}\n"
        f"MAU limit (contract): {mau_limit:,}\n"
        f"Current 30-day MAUs: {current_mau:,}\n"
        f"Overage: {current_mau - mau_limit:,} "
        f"({(current_mau - mau_limit) / mau_limit * 100:.0f}% over)"
    )
    resp = client.messages.create(
        model=_model(),
        max_tokens=200,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
