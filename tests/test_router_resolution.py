"""Tests for orchestrator Router agent resolution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.agent import _resolve_economist, _resolve_router
from orchestrator.mock_agents import MockEconomistAgent, MockRoutingAgent
from orchestrator.remote_agents import UAgentsEconomistClient, UAgentsRoutingClient


class FakeContext:
    def __init__(self) -> None:
        self.logger = SimpleNamespace(info=lambda *args, **kwargs: None)


def test_router_override_takes_priority_over_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ROUTER_AGENT_ADDRESS", "agent1qconfigured")
    ctx = FakeContext()
    override = MockRoutingAgent()

    resolved = _resolve_router(ctx, override)

    assert resolved is override


def test_configured_router_address_creates_uagents_routing_client(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ROUTER_AGENT_ADDRESS", "agent1qconfigured")
    monkeypatch.delenv("ROUTER_AGENT_TIMEOUT_SECONDS", raising=False)
    ctx = FakeContext()

    resolved = _resolve_router(ctx, None)

    assert isinstance(resolved, UAgentsRoutingClient)
    assert resolved.destination == "agent1qconfigured"
    assert resolved.timeout_seconds == 30


def test_configured_router_timeout_is_parsed(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ROUTER_AGENT_ADDRESS", "agent1qconfigured")
    monkeypatch.setenv("ROUTER_AGENT_TIMEOUT_SECONDS", "45")
    ctx = FakeContext()

    resolved = _resolve_router(ctx, None)

    assert isinstance(resolved, UAgentsRoutingClient)
    assert resolved.timeout_seconds == 45


def test_invalid_router_timeout_falls_back_to_30(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ROUTER_AGENT_ADDRESS", "agent1qconfigured")
    monkeypatch.setenv("ROUTER_AGENT_TIMEOUT_SECONDS", "not-a-number")
    ctx = FakeContext()

    resolved = _resolve_router(ctx, None)

    assert isinstance(resolved, UAgentsRoutingClient)
    assert resolved.timeout_seconds == 30


def test_blank_router_address_returns_mock_routing_agent(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ROUTER_AGENT_ADDRESS", "")
    ctx = FakeContext()

    resolved = _resolve_router(ctx, None)

    assert isinstance(resolved, MockRoutingAgent)


def test_missing_router_address_returns_mock_routing_agent(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("ROUTER_AGENT_ADDRESS", raising=False)
    ctx = FakeContext()

    resolved = _resolve_router(ctx, None)

    assert isinstance(resolved, MockRoutingAgent)


def test_economist_selection_still_works_with_router_configuration(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ECONOMIST_AGENT_ADDRESS", "agent1qeconomist")
    monkeypatch.setenv("ROUTER_AGENT_ADDRESS", "agent1qrouter")
    ctx = FakeContext()

    economist = _resolve_economist(ctx, None)
    router = _resolve_router(ctx, None)

    assert isinstance(economist, UAgentsEconomistClient)
    assert isinstance(router, UAgentsRoutingClient)


def test_economist_mock_fallback_still_works(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("ECONOMIST_AGENT_ADDRESS", raising=False)
    monkeypatch.delenv("ROUTER_AGENT_ADDRESS", raising=False)
    ctx = FakeContext()

    economist = _resolve_economist(ctx, None)
    router = _resolve_router(ctx, None)

    assert isinstance(economist, MockEconomistAgent)
    assert isinstance(router, MockRoutingAgent)
