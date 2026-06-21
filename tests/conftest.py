"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_treasury_address_from_private_env(monkeypatch: pytest.MonkeyPatch):
    """Keep mock payment tests independent of live TREASURY_AGENT_ADDRESS in .env."""
    monkeypatch.delenv("TREASURY_AGENT_ADDRESS", raising=False)
