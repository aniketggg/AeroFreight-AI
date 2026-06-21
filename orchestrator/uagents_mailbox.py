"""Mailbox-only uAgents registration helpers."""

from __future__ import annotations

from uagents.config import ALMANAC_API_URL
from uagents.registration import AlmanacApiRegistrationPolicy


def mailbox_registration_policy() -> AlmanacApiRegistrationPolicy:
    """Register via Almanac API only; skip on-chain ledger for mailbox agents."""
    return AlmanacApiRegistrationPolicy(almanac_api=ALMANAC_API_URL)
