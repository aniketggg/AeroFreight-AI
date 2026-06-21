"""Tests for Treasury agent package import safety and factory configuration."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TEST_TREASURY_SEED = "test-treasury-mailbox-seed-only"


def _ensure_event_loop() -> None:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("event loop is closed")
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def test_no_root_level_duplicate_models_file():
    assert not Path("models.py").exists()


def test_importing_treasury_agent_does_not_create_agent():
    module = importlib.import_module("treasury_agent.agent")
    assert hasattr(module, "create_treasury_agent")
    assert not hasattr(module, "treasury_agent")


def test_factory_defaults_to_port_8014(monkeypatch: pytest.MonkeyPatch):
    _ensure_event_loop()
    monkeypatch.delenv("TREASURY_AGENT_PORT", raising=False)
    monkeypatch.setattr(
        "treasury_agent.agent.load_dotenv",
        lambda *args, **kwargs: False,
    )

    from treasury_agent.agent import create_treasury_agent

    agent = create_treasury_agent(seed=TEST_TREASURY_SEED)
    assert agent._port == 8014


def test_injected_port_overrides_default(monkeypatch: pytest.MonkeyPatch):
    _ensure_event_loop()
    monkeypatch.setattr(
        "treasury_agent.agent.load_dotenv",
        lambda *args, **kwargs: False,
    )

    from treasury_agent.agent import create_treasury_agent

    agent = create_treasury_agent(seed=TEST_TREASURY_SEED, port=9104)
    assert agent._port == 9104


def test_environment_port_is_respected(monkeypatch: pytest.MonkeyPatch):
    _ensure_event_loop()
    monkeypatch.setenv("TREASURY_AGENT_PORT", "8114")
    monkeypatch.setattr(
        "treasury_agent.agent.load_dotenv",
        lambda *args, **kwargs: False,
    )

    from treasury_agent.agent import create_treasury_agent

    agent = create_treasury_agent(seed=TEST_TREASURY_SEED)
    assert agent._port == 8114


def test_invalid_port_raises_configuration_error(monkeypatch: pytest.MonkeyPatch):
    _ensure_event_loop()
    monkeypatch.setenv("TREASURY_AGENT_PORT", "not-a-port")
    monkeypatch.setattr(
        "treasury_agent.agent.load_dotenv",
        lambda *args, **kwargs: False,
    )

    from treasury_agent.agent import (
        TreasuryAgentConfigurationError,
        create_treasury_agent,
    )

    with pytest.raises(TreasuryAgentConfigurationError, match="TREASURY_AGENT_PORT"):
        create_treasury_agent(seed=TEST_TREASURY_SEED)


def test_missing_seed_raises_configuration_error(monkeypatch: pytest.MonkeyPatch):
    _ensure_event_loop()
    monkeypatch.delenv("TREASURY_AGENT_SEED", raising=False)
    monkeypatch.setattr(
        "treasury_agent.agent.load_dotenv",
        lambda *args, **kwargs: False,
    )

    from treasury_agent.agent import (
        TreasuryAgentConfigurationError,
        create_treasury_agent,
    )

    with pytest.raises(TreasuryAgentConfigurationError, match="TREASURY_AGENT_SEED"):
        create_treasury_agent()


def test_mailbox_and_inspector_remain_enabled(monkeypatch: pytest.MonkeyPatch):
    _ensure_event_loop()
    monkeypatch.setattr(
        "treasury_agent.agent.load_dotenv",
        lambda *args, **kwargs: False,
    )

    from treasury_agent.agent import create_treasury_agent, settlement_protocol

    agent = create_treasury_agent(seed=TEST_TREASURY_SEED)
    assert agent._use_mailbox is True
    assert agent._enable_agent_inspector is True
    assert agent.mailbox_client is not None
    assert settlement_protocol.digest in agent.protocols


def _run(coro):
    return asyncio.run(coro)


def test_finalize_checkout_does_not_claim_paid_without_verification(
    monkeypatch: pytest.MonkeyPatch,
):
    from treasury_agent.agent import _finalize_checkout

    ctx = MagicMock()
    ctx.storage.get.return_value = {
        "user_address": "agent1quser",
        "session_id": "session-1",
        "orchestrator_address": "",
        "fee_usd": 5.0,
        "shipment": {},
        "econ_data": {},
        "route_data": {},
        "doc_templates": {},
    }
    ctx.send = AsyncMock()

    with patch(
        "treasury_agent.agent.verify_checkout_paid",
        return_value=False,
    ):
        _run(_finalize_checkout(ctx, "agent1quser", "cs_test_123", "cs_test_123"))

    reject_calls = [
        call
        for call in ctx.send.call_args_list
        if call.args[1].__class__.__name__ == "RejectPayment"
    ]
    assert reject_calls


def test_finalize_checkout_builds_settlement_status_on_success(
    monkeypatch: pytest.MonkeyPatch,
):
    from shared_models import DocTemplates, EconData, Item, RouteData, ShipmentRequest
    from treasury_agent.agent import _finalize_checkout

    shipment = ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="Electronics", quantity=10, category="electronics")],
        total_weight_kg=850.0,
        total_volume_cbm=3.2,
        timeframe="COST",
        declared_value_usd=4200.0,
    )
    econ = EconData(
        transport_preference="EITHER",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=126.50,
    )
    route = RouteData(
        selected_mode="SHIP",
        optimal_route_nodes=["Shenzhen", "USLAX", "Austin"],
        countries_visited=["CN", "US"],
        freight_and_toll_cost_usd=645.0,
        total_landed_cost_usd=771.25,
    )
    docs = DocTemplates(
        required_form_names=["CBP Form 7501"],
        blank_form_structures={"CBP Form 7501": {"status": "demo"}},
    )

    ctx = MagicMock()
    ctx.storage.get.return_value = {
        "user_address": "agent1quser",
        "session_id": "session-1",
        "orchestrator_address": "agent1qorch",
        "fee_usd": 5.0,
        "shipment": shipment.model_dump(),
        "econ_data": econ.model_dump(),
        "route_data": route.model_dump(),
        "doc_templates": docs.model_dump(),
    }
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()

    with (
        patch("treasury_agent.agent.verify_checkout_paid", return_value=True),
        patch("treasury_agent.agent.generate_invoice_pdf", return_value="/tmp/x.pdf"),
        patch("treasury_agent.agent.upload_invoice_and_get_link", return_value=None),
    ):
        _run(_finalize_checkout(ctx, "agent1quser", "cs_test_123", "cs_test_123"))

    result_messages = [
        call.args[1]
        for call in ctx.send.call_args_list
        if call.args[1].__class__.__name__ == "SettlementResultMessage"
    ]
    assert result_messages
    assert result_messages[0].ok is True
    assert result_messages[0].settlement_status["payment_hash"] == "cs_test_123"
