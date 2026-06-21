"""Tests for remote uAgents Economist client."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from economic_agent.messages import EconomistError, EconomistRequest, EconomistResponse
from orchestrator.mock_agents import MockEconomistAgent, MockRoutingAgent
from orchestrator.remote_agents import (
    RemoteEconomistError,
    RemoteRoutingError,
    UAgentsEconomistClient,
    UAgentsRoutingClient,
)
from shared_models import EconData, Item, RouteData, ShipmentRequest
from step3_riya.agent import RouteRequestMessage, RouteResponseMessage


def _run(coro):
    return asyncio.run(coro)


def _sample_shipment() -> ShipmentRequest:
    return ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="Widget", quantity=5, category="electronics")],
        total_weight_kg=120.0,
        total_volume_cbm=2.0,
        timeframe="SPEED",
        declared_value_usd=4000.0,
    )


class FakeContext:
    def __init__(
        self,
        *,
        reply=None,
        status=None,
        raise_on_send: Exception | None = None,
    ) -> None:
        self.reply = reply
        self.status = status
        self.raise_on_send = raise_on_send
        self.last_call: dict | None = None
        self.logger = SimpleNamespace(error=lambda *args, **kwargs: None)

    async def send_and_receive(
        self,
        destination,
        message,
        response_type,
        sync=False,
        timeout=30,
    ):
        self.last_call = {
            "destination": destination,
            "message": message,
            "response_type": response_type,
            "timeout": timeout,
        }
        if self.raise_on_send is not None:
            raise self.raise_on_send
        return self.reply, self.status


def test_sends_economist_request():
    shipment = _sample_shipment()
    expected = MockEconomistAgent().analyze(shipment)
    ctx = FakeContext(
        reply=EconomistResponse(econ_data_json=expected.model_dump_json()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsEconomistClient(ctx, "agent1qtest", timeout_seconds=12)

    result = _run(client.analyze(shipment))

    assert isinstance(ctx.last_call["message"], EconomistRequest)
    assert result == expected


def test_sends_to_configured_destination():
    shipment = _sample_shipment()
    expected = MockEconomistAgent().analyze(shipment)
    ctx = FakeContext(
        reply=EconomistResponse(econ_data_json=expected.model_dump_json()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsEconomistClient(ctx, "agent1qremote123", timeout_seconds=15)

    _run(client.analyze(shipment))

    assert ctx.last_call["destination"] == "agent1qremote123"


def test_shipment_json_round_trips():
    shipment = _sample_shipment()
    expected = MockEconomistAgent().analyze(shipment)
    ctx = FakeContext(
        reply=EconomistResponse(econ_data_json=expected.model_dump_json()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsEconomistClient(ctx, "agent1qtest")

    _run(client.analyze(shipment))

    request = ctx.last_call["message"]
    assert isinstance(request, EconomistRequest)
    round_trip = ShipmentRequest.model_validate_json(request.shipment_json)
    assert round_trip == shipment


def test_economist_response_becomes_econ_data():
    shipment = _sample_shipment()
    expected = MockEconomistAgent().analyze(shipment)
    ctx = FakeContext(
        reply=EconomistResponse(econ_data_json=expected.model_dump_json()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsEconomistClient(ctx, "agent1qtest")

    result = _run(client.analyze(shipment))

    assert result == expected


def test_economist_error_raises_remote_economist_error():
    shipment = _sample_shipment()
    ctx = FakeContext(
        reply=EconomistError(error_message="bad shipment"),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsEconomistClient(ctx, "agent1qtest")

    with pytest.raises(RemoteEconomistError, match="could not process"):
        _run(client.analyze(shipment))


def test_none_response_raises_remote_economist_error():
    shipment = _sample_shipment()
    ctx = FakeContext(reply=None, status=SimpleNamespace(detail="timeout"))
    client = UAgentsEconomistClient(ctx, "agent1qtest")

    with pytest.raises(RemoteEconomistError, match="did not respond"):
        _run(client.analyze(shipment))


def test_unexpected_response_type_raises_remote_economist_error():
    shipment = _sample_shipment()
    ctx = FakeContext(
        reply=SimpleNamespace(unexpected=True),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsEconomistClient(ctx, "agent1qtest")

    with pytest.raises(RemoteEconomistError, match="unexpected response"):
        _run(client.analyze(shipment))


def test_malformed_econ_data_json_raises_remote_economist_error():
    shipment = _sample_shipment()
    ctx = FakeContext(
        reply=EconomistResponse(econ_data_json="{not valid json"),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsEconomistClient(ctx, "agent1qtest")

    with pytest.raises(RemoteEconomistError, match="invalid data") as exc_info:
        _run(client.analyze(shipment))

    assert exc_info.value.__cause__ is not None


def test_timeout_passed_to_send_and_receive():
    shipment = _sample_shipment()
    expected = MockEconomistAgent().analyze(shipment)
    ctx = FakeContext(
        reply=EconomistResponse(econ_data_json=expected.model_dump_json()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsEconomistClient(ctx, "agent1qtest", timeout_seconds=45)

    _run(client.analyze(shipment))

    assert ctx.last_call["timeout"] == 45


def test_send_failure_raises_remote_economist_error_without_internal_details():
    shipment = _sample_shipment()
    ctx = FakeContext(raise_on_send=RuntimeError("network down"))
    client = UAgentsEconomistClient(ctx, "agent1qtest")

    with pytest.raises(RemoteEconomistError, match="could not be reached") as exc_info:
        _run(client.analyze(shipment))

    message = str(exc_info.value)
    assert "network down" not in message
    assert "agent1q" not in message


def _sample_econ() -> EconData:
    return MockEconomistAgent().analyze(_sample_shipment())


def _sample_route_data() -> RouteData:
    shipment = _sample_shipment()
    econ = _sample_econ()
    return MockRoutingAgent().route(shipment, econ)


def test_routing_valid_response_returns_route_data():
    shipment = _sample_shipment()
    econ = _sample_econ()
    expected = _sample_route_data()
    ctx = FakeContext(
        reply=RouteResponseMessage(ok=True, route_data=expected.model_dump()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsRoutingClient(ctx, "agent1qrouter", timeout_seconds=12)

    result = _run(client.route(shipment, econ))

    assert result == expected


def test_routing_request_contains_shipment_model_dump():
    shipment = _sample_shipment()
    econ = _sample_econ()
    expected = _sample_route_data()
    ctx = FakeContext(
        reply=RouteResponseMessage(ok=True, route_data=expected.model_dump()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsRoutingClient(ctx, "agent1qrouter")

    _run(client.route(shipment, econ))

    request = ctx.last_call["message"]
    assert isinstance(request, RouteRequestMessage)
    assert request.shipment == shipment.model_dump()


def test_routing_request_contains_econ_model_dump():
    shipment = _sample_shipment()
    econ = _sample_econ()
    expected = _sample_route_data()
    ctx = FakeContext(
        reply=RouteResponseMessage(ok=True, route_data=expected.model_dump()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsRoutingClient(ctx, "agent1qrouter")

    _run(client.route(shipment, econ))

    request = ctx.last_call["message"]
    assert request.econ == econ.model_dump()


def test_routing_sends_to_configured_destination():
    shipment = _sample_shipment()
    econ = _sample_econ()
    expected = _sample_route_data()
    ctx = FakeContext(
        reply=RouteResponseMessage(ok=True, route_data=expected.model_dump()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsRoutingClient(ctx, "agent1qremote456", timeout_seconds=15)

    _run(client.route(shipment, econ))

    assert ctx.last_call["destination"] == "agent1qremote456"


def test_routing_timeout_passed_to_send_and_receive():
    shipment = _sample_shipment()
    econ = _sample_econ()
    expected = _sample_route_data()
    ctx = FakeContext(
        reply=RouteResponseMessage(ok=True, route_data=expected.model_dump()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsRoutingClient(ctx, "agent1qrouter", timeout_seconds=45)

    _run(client.route(shipment, econ))

    assert ctx.last_call["timeout"] == 45


def test_routing_response_type_is_route_response_message():
    shipment = _sample_shipment()
    econ = _sample_econ()
    expected = _sample_route_data()
    ctx = FakeContext(
        reply=RouteResponseMessage(ok=True, route_data=expected.model_dump()),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsRoutingClient(ctx, "agent1qrouter")

    _run(client.route(shipment, econ))

    assert ctx.last_call["response_type"] is RouteResponseMessage


def test_routing_reply_not_ok_raises_remote_routing_error():
    shipment = _sample_shipment()
    econ = _sample_econ()
    ctx = FakeContext(
        reply=RouteResponseMessage(
            ok=False,
            error="internal routing failure details",
        ),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsRoutingClient(ctx, "agent1qrouter")

    with pytest.raises(RemoteRoutingError, match="could not process") as exc_info:
        _run(client.route(shipment, econ))

    assert "internal routing failure details" not in str(exc_info.value)


def test_routing_none_response_raises_remote_routing_error():
    shipment = _sample_shipment()
    econ = _sample_econ()
    ctx = FakeContext(reply=None, status=SimpleNamespace(detail="timeout"))
    client = UAgentsRoutingClient(ctx, "agent1qrouter")

    with pytest.raises(RemoteRoutingError, match="did not respond"):
        _run(client.route(shipment, econ))


def test_routing_unexpected_response_type_raises_remote_routing_error():
    shipment = _sample_shipment()
    econ = _sample_econ()
    ctx = FakeContext(
        reply=SimpleNamespace(unexpected=True),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsRoutingClient(ctx, "agent1qrouter")

    with pytest.raises(RemoteRoutingError, match="unexpected response"):
        _run(client.route(shipment, econ))


def test_routing_invalid_route_data_raises_remote_routing_error():
    shipment = _sample_shipment()
    econ = _sample_econ()
    ctx = FakeContext(
        reply=RouteResponseMessage(
            ok=True,
            route_data={"selected_mode": "INVALID"},
        ),
        status=SimpleNamespace(detail=None),
    )
    client = UAgentsRoutingClient(ctx, "agent1qrouter")

    with pytest.raises(RemoteRoutingError, match="invalid data") as exc_info:
        _run(client.route(shipment, econ))

    assert exc_info.value.__cause__ is not None


def test_routing_send_failure_wraps_with_exception_chaining():
    shipment = _sample_shipment()
    econ = _sample_econ()
    ctx = FakeContext(raise_on_send=RuntimeError("network down"))
    client = UAgentsRoutingClient(ctx, "agent1qrouter")

    with pytest.raises(RemoteRoutingError, match="could not be reached") as exc_info:
        _run(client.route(shipment, econ))

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "network down" not in str(exc_info.value)
