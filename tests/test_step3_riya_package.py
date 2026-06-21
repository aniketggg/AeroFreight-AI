"""Tests for step3_riya package import safety and routing contracts."""

from __future__ import annotations

import asyncio
import importlib
import sys

import pytest
from uagents import Model

from shared_models import EconData, Item, RouteData, ShipmentRequest


ROUTING_ENV_VARS = (
    "RIYA_AGENT_SEED",
    "AIR_AGENT_ADDRESS",
    "SHIP_AGENT_ADDRESS",
    "AIR_AGENT_SEED",
    "SHIP_AGENT_SEED",
)


def _clear_routing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ROUTING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _reload_step3_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "step3_riya" or module_name.startswith("step3_riya."):
            del sys.modules[module_name]
    importlib.import_module("step3_riya")


def _sample_shipment() -> ShipmentRequest:
    return ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="Electronics", quantity=10, category="electronics")],
        total_weight_kg=800,
        total_volume_cbm=4.2,
        timeframe="COST",
        declared_value_usd=5000,
    )


def _sample_econ() -> EconData:
    return EconData(
        transport_preference="EITHER",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=350,
    )


def test_agent_module_imports_without_routing_env(monkeypatch: pytest.MonkeyPatch):
    _clear_routing_env(monkeypatch)
    _reload_step3_modules()

    module = importlib.import_module("step3_riya.agent")

    assert module.create_routing_agent is not None
    assert module.RouteRequestMessage is not None


def test_air_agent_module_imports_without_env(monkeypatch: pytest.MonkeyPatch):
    _clear_routing_env(monkeypatch)
    _reload_step3_modules()

    module = importlib.import_module("step3_riya.air_agent")

    assert module.create_air_agent is not None


def test_ship_agent_module_imports_without_env(monkeypatch: pytest.MonkeyPatch):
    _clear_routing_env(monkeypatch)
    _reload_step3_modules()

    module = importlib.import_module("step3_riya.ship_agent")

    assert module.create_ship_agent is not None


def test_importing_agent_modules_does_not_create_agents(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_routing_env(monkeypatch)
    _reload_step3_modules()

    agent_module = importlib.import_module("step3_riya.agent")
    air_module = importlib.import_module("step3_riya.air_agent")
    ship_module = importlib.import_module("step3_riya.ship_agent")

    assert not hasattr(agent_module, "riya_agent")
    assert not hasattr(air_module, "air_agent")
    assert not hasattr(ship_module, "ship_agent")


def test_create_routing_agent_requires_configuration(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_routing_env(monkeypatch)
    _reload_step3_modules()
    monkeypatch.setattr("step3_riya.agent.load_dotenv", lambda *args, **kwargs: False)

    from step3_riya.agent import RoutingConfigurationError, create_routing_agent

    with pytest.raises(RoutingConfigurationError, match="RIYA_AGENT_SEED"):
        create_routing_agent()


def test_create_air_agent_requires_configuration(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_routing_env(monkeypatch)
    _reload_step3_modules()
    monkeypatch.setattr("step3_riya.air_agent.load_dotenv", lambda *args, **kwargs: False)

    from step3_riya.air_agent import AirAgentConfigurationError, create_air_agent

    with pytest.raises(AirAgentConfigurationError, match="AIR_AGENT_SEED"):
        create_air_agent()


def test_create_ship_agent_requires_configuration(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_routing_env(monkeypatch)
    _reload_step3_modules()
    monkeypatch.setattr("step3_riya.ship_agent.load_dotenv", lambda *args, **kwargs: False)

    from step3_riya.ship_agent import ShipAgentConfigurationError, create_ship_agent

    with pytest.raises(ShipAgentConfigurationError, match="SHIP_AGENT_SEED"):
        create_ship_agent()


def test_route_wire_messages_are_uagents_models():
    from step3_riya.agent import RouteRequestMessage, RouteResponseMessage

    assert issubclass(RouteRequestMessage, Model)
    assert issubclass(RouteResponseMessage, Model)


def test_shared_models_serialize_into_route_request_message():
    from step3_riya.agent import RouteRequestMessage

    shipment = _sample_shipment()
    econ = _sample_econ()

    message = RouteRequestMessage(
        shipment=shipment.model_dump(),
        econ=econ.model_dump(),
    )

    assert ShipmentRequest.model_validate(message.shipment) == shipment
    assert EconData.model_validate(message.econ) == econ


def test_route_data_dict_validates_against_shared_models():
    from step3_riya.route_logic import calculate_route
    from step3_riya.routing_models import RoutingRequest

    detailed = calculate_route(
        RoutingRequest(
            shipment=_sample_shipment(),
            econ=_sample_econ(),
        )
    )

    orchestrator_route = RouteData.model_validate(
        {
            "selected_mode": detailed.selected_mode,
            "optimal_route_nodes": detailed.optimal_route_nodes,
            "countries_visited": detailed.countries_visited,
            "freight_and_toll_cost_usd": detailed.freight_and_toll_cost_usd,
            "total_landed_cost_usd": detailed.total_landed_cost_usd,
        }
    )

    assert orchestrator_route.selected_mode in {"AIR", "SHIP"}
    assert orchestrator_route.total_landed_cost_usd >= (
        orchestrator_route.freight_and_toll_cost_usd
    )


def test_calculate_route_supports_air_preference():
    from step3_riya.route_logic import calculate_route
    from step3_riya.routing_models import RoutingRequest

    econ = _sample_econ().model_copy(update={"transport_preference": "AIR"})
    result = calculate_route(
        RoutingRequest(shipment=_sample_shipment(), econ=econ)
    )

    assert result.selected_mode == "AIR"
    assert result.optimal_route_nodes
    assert result.total_landed_cost_usd >= result.freight_and_toll_cost_usd


def test_calculate_route_supports_ship_preference():
    from step3_riya.route_logic import calculate_route
    from step3_riya.routing_models import RoutingRequest

    econ = _sample_econ().model_copy(update={"transport_preference": "SHIP"})
    result = calculate_route(
        RoutingRequest(shipment=_sample_shipment(), econ=econ)
    )

    assert result.selected_mode == "SHIP"
    assert result.optimal_route_nodes
    assert result.total_landed_cost_usd >= result.freight_and_toll_cost_usd


def test_local_bureau_demo_does_not_run_on_import():
    module = importlib.import_module("step3_riya.local_bureau_demo")

    assert hasattr(module, "main")
    assert callable(module.main)


TEST_AIR_SEED = "test-air-mailbox-seed-only"
TEST_SHIP_SEED = "test-ship-mailbox-seed-only"
TEST_ROUTER_SEED = "test-router-mailbox-seed-only"
TEST_AIR_ADDRESS = "agent1qtestair000000000000000000000000000000000000000000000000"
TEST_SHIP_ADDRESS = "agent1qtestship000000000000000000000000000000000000000000000000"


def _ensure_event_loop() -> None:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("event loop is closed")
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def test_air_agent_factory_uses_mailbox_mode():
    _ensure_event_loop()
    from step3_riya.air_agent import air_quote_protocol, create_air_agent

    agent = create_air_agent(seed=TEST_AIR_SEED)

    assert agent.name == "aerofreight-air-subagent"
    assert agent._use_mailbox is True
    assert agent._enable_agent_inspector is False
    assert agent._rest_handlers == {}
    assert agent.mailbox_client is not None
    assert air_quote_protocol.digest in agent.protocols


def test_ship_agent_factory_uses_mailbox_mode():
    _ensure_event_loop()
    from step3_riya.ship_agent import create_ship_agent, ship_quote_protocol

    agent = create_ship_agent(seed=TEST_SHIP_SEED)

    assert agent.name == "aerofreight-ship-subagent"
    assert agent._use_mailbox is True
    assert agent._enable_agent_inspector is False
    assert agent._rest_handlers == {}
    assert agent.mailbox_client is not None
    assert ship_quote_protocol.digest in agent.protocols


def test_routing_agent_factory_uses_mailbox_mode():
    _ensure_event_loop()
    from step3_riya.agent import create_routing_agent, routing_protocol

    agent = create_routing_agent(
        seed=TEST_ROUTER_SEED,
        air_agent_address=TEST_AIR_ADDRESS,
        ship_agent_address=TEST_SHIP_ADDRESS,
    )

    assert agent.name == "aerofreight-riya-routing"
    assert agent._use_mailbox is True
    assert agent._enable_agent_inspector is False
    assert agent._rest_handlers == {}
    assert agent.mailbox_client is not None
    assert routing_protocol.digest in agent.protocols


def test_mailbox_agents_do_not_require_explicit_port_configuration():
    _ensure_event_loop()
    from uagents_core.config import AgentverseConfig

    from step3_riya.agent import create_routing_agent
    from step3_riya.air_agent import create_air_agent
    from step3_riya.ship_agent import create_ship_agent

    mailbox_url = AgentverseConfig().mailbox_endpoint

    air = create_air_agent(seed=TEST_AIR_SEED)
    ship = create_ship_agent(seed=TEST_SHIP_SEED)
    router = create_routing_agent(
        seed=TEST_ROUTER_SEED,
        air_agent_address=TEST_AIR_ADDRESS,
        ship_agent_address=TEST_SHIP_ADDRESS,
    )

    for agent in (air, ship, router):
        assert agent._use_mailbox is True
        assert agent._enable_agent_inspector is False
        assert agent._rest_handlers == {}
        assert len(agent._endpoints) == 1
        assert agent._endpoints[0].url == mailbox_url
        assert "127.0.0.1" not in agent._endpoints[0].url


def test_same_seed_produces_same_deterministic_address():
    _ensure_event_loop()
    from step3_riya.air_agent import create_air_agent

    first = create_air_agent(seed=TEST_AIR_SEED)
    second = create_air_agent(seed=TEST_AIR_SEED)

    assert first.address == second.address


def test_routing_agent_preserves_deterministic_address_for_same_seed():
    _ensure_event_loop()
    from step3_riya.agent import create_routing_agent

    first = create_routing_agent(
        seed=TEST_ROUTER_SEED,
        air_agent_address=TEST_AIR_ADDRESS,
        ship_agent_address=TEST_SHIP_ADDRESS,
    )
    second = create_routing_agent(
        seed=TEST_ROUTER_SEED,
        air_agent_address=TEST_AIR_ADDRESS,
        ship_agent_address=TEST_SHIP_ADDRESS,
    )

    assert first.address == second.address
