"""Tests for opt-in payment tracing and RequestPayment diagnostics."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator import payment_diagnostic
from orchestrator.agent import (
    _deliver_payment_wall,
    _send_orchestrator_request_payment,
)
from orchestrator.agent_interfaces import PaymentSetupResult
from orchestrator.models import PartialItem, PartialShipmentData, WorkflowStage
from orchestrator.payment_trace import (
    PLACEHOLDER_CHECKOUT,
    build_orchestrator_request_payment,
    build_treasury_request_payment,
    compare_request_payment_dumps,
    is_payment_debug_enabled,
    is_send_failure,
    normalize_fetch_checkout_metadata,
    payment_trace,
    summarize_request_payment_dump,
)
from orchestrator.remote_agents import UAgentsTreasuryPaymentClient
from treasury_agent.messages import PaymentSetupResponseMessage
from uagents_core.contrib.protocols.chat import ChatMessage, TextContent
from uagents_core.contrib.protocols.payment import RequestPayment


COMPAT_CHECKOUT = dict(PLACEHOLDER_CHECKOUT)


def _run(coro):
    return asyncio.run(coro)


class CaptureLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str, *args, **kwargs) -> None:
        self.messages.append(message if not args else message % args)


@pytest.fixture(autouse=True)
def disable_payment_debug(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AEROFREIGHT_PAYMENT_DEBUG", raising=False)


def test_payment_debug_disabled_by_default():
    assert is_payment_debug_enabled() is False


def test_payment_trace_redacts_secret_values(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AEROFREIGHT_PAYMENT_DEBUG", "true")
    logger = CaptureLogger()
    payment_trace(
        logger,
        "test.event",
        session_id="session-1",
        sample="sk_test_should_not_appear",
    )
    combined = "\n".join(logger.messages)
    assert "PAYMENT_TRACE test.event" in combined
    assert "sk_test_should_not_appear" not in combined


def test_payment_trace_does_not_log_client_secret_value(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AEROFREIGHT_PAYMENT_DEBUG", "true")
    logger = CaptureLogger()
    payment_trace(
        logger,
        "test.checkout",
        session_id="session-1",
        has_client_secret=True,
    )
    assert "secret_placeholder" not in "\n".join(logger.messages)


def test_normalize_fetch_checkout_metadata_maps_embedded_page_to_embedded():
    checkout, changes = normalize_fetch_checkout_metadata(
        {
            "client_secret": "secret_placeholder",
            "checkout_session_id": "cs_test_placeholder",
            "publishable_key": "pk_test_placeholder",
            "currency": "usd",
            "amount_cents": 500,
            "ui_mode": "embedded_page",
        }
    )
    assert checkout is not None
    assert checkout["ui_mode"] == "embedded"
    assert checkout["id"] == "cs_test_placeholder"
    assert changes["changed"] is True


def _mock_payment_ctx(*, send_result=None, send_side_effect=None):
    logger = CaptureLogger()
    ctx = SimpleNamespace(
        logger=logger,
        agent=SimpleNamespace(address="agent1qorchestrator"),
        storage=MagicMock(),
    )
    ctx.storage.get.return_value = None
    captured: dict = {}

    async def _capture_send(destination, message):
        captured["destination"] = destination
        captured["message"] = message
        if send_side_effect is not None:
            raise send_side_effect
        return send_result or SimpleNamespace(status="delivered")

    ctx.send = _capture_send
    ctx.captured = captured
    return ctx


def test_deliver_payment_wall_reaches_send_function(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AEROFREIGHT_PAYMENT_DEBUG", "true")
    ctx = _mock_payment_ctx()

    setup = PaymentSetupResult(checkout=dict(COMPAT_CHECKOUT), fee_usd=5.0)
    delivered = _run(
        _deliver_payment_wall(
            ctx,
            user_address="user-a",
            session_id="session-trace-1",
            setup=setup,
        )
    )

    assert delivered is True
    assert isinstance(ctx.captured.get("message"), RequestPayment)
    assert ctx.captured["destination"] == "user-a"
    trace_text = "\n".join(ctx.logger.messages)
    assert "PAYMENT_TRACE orchestrator.payment_wall.send_start" in trace_text
    assert "PAYMENT_TRACE orchestrator.payment_wall.send_complete" in trace_text
    assert "PAYMENT_TRACE orchestrator.request_payment.dispatched" in trace_text


def test_request_payment_metadata_survives_validation():
    request = build_orchestrator_request_payment(
        recipient="agent1qorchestrator",
        session_id="session-1",
        fee_usd=5.0,
        checkout=dict(COMPAT_CHECKOUT),
    )
    dumped = request.model_dump()
    summary = summarize_request_payment_dump(dumped)
    assert summary["metadata_stripe_is_dict"] is True
    assert summary["stripe_ui_mode"] == "embedded"
    assert summary["has_id"] is True
    assert summary["has_checkout_session_id"] is True
    assert summary["id_aliases_match"] is True
    assert summary["amount_cents_python_type"] == "str"


def test_standalone_and_orchestrator_request_payment_compare():
    orchestrator_dump = build_orchestrator_request_payment(
        recipient="agent1qorchestrator",
        session_id="session-1",
        fee_usd=5.0,
        checkout=dict(COMPAT_CHECKOUT),
    ).model_dump()
    treasury_dump = build_treasury_request_payment(
        recipient="agent1qtreasury",
        session_id="session-1",
        fee_usd=5.0,
        checkout=dict(COMPAT_CHECKOUT),
    ).model_dump()
    comparison = compare_request_payment_dumps(orchestrator_dump, treasury_dump)
    assert comparison["difference_count"] == 0


def test_send_result_failure_is_not_treated_as_success():
    ctx = _mock_payment_ctx(
        send_result=SimpleNamespace(status="failed", detail="undelivered")
    )

    delivered = _run(
        _send_orchestrator_request_payment(
            ctx,
            user_address="user-a",
            session_id="session-1",
            fee_usd=5.0,
            checkout=dict(COMPAT_CHECKOUT),
        )
    )
    assert delivered is False
    assert is_send_failure(SimpleNamespace(status="failed")) is True


def test_send_exception_returns_false():
    ctx = _mock_payment_ctx(send_side_effect=RuntimeError("send failed"))
    delivered = _run(
        _send_orchestrator_request_payment(
            ctx,
            user_address="user-a",
            session_id="session-1",
            fee_usd=5.0,
            checkout=dict(COMPAT_CHECKOUT),
        )
    )
    assert delivered is False


def test_remote_client_traces_send_and_receive(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AEROFREIGHT_PAYMENT_DEBUG", "true")
    logger = CaptureLogger()
    context = SimpleNamespace(logger=logger)
    reply = PaymentSetupResponseMessage(
        ok=True,
        session_id="session-1",
        checkout=dict(COMPAT_CHECKOUT),
        fee_usd=5.0,
    )

    async def _send_and_receive(*args, **kwargs):
        return reply, SimpleNamespace(status="delivered")

    context.send_and_receive = _send_and_receive
    client = UAgentsTreasuryPaymentClient(context, "agent1qtreasury")

    from shared_models import DocTemplates, EconData, Item, RouteData, ShipmentRequest

    shipment = ShipmentRequest(
        origin={"country": "CN", "state": "GD", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="Widget", quantity=1, category="electronics")],
        total_weight_kg=10.0,
        total_volume_cbm=1.0,
        timeframe="SPEED",
        declared_value_usd=100.0,
    )
    econ = EconData(
        transport_preference="AIR",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=10.0,
    )
    route = RouteData(
        selected_mode="AIR",
        optimal_route_nodes=["Shenzhen", "Austin"],
        countries_visited=["CN", "US"],
        freight_and_toll_cost_usd=100.0,
        total_landed_cost_usd=115.0,
    )
    docs = DocTemplates(required_form_names=["invoice"], blank_form_structures={})

    result = _run(
        client.prepare_payment(
            user_address="user-a",
            session_id="session-1",
            shipment=shipment,
            econ_data=econ,
            route_data=route,
            doc_templates=docs,
        )
    )
    assert result.checkout == COMPAT_CHECKOUT
    trace_text = "\n".join(logger.messages)
    assert "PAYMENT_TRACE orchestrator.treasury_setup.send" in trace_text
    assert "PAYMENT_TRACE orchestrator.treasury_setup.receive" in trace_text


def test_offline_diagnostic_makes_no_network_call(capsys):
    payment_diagnostic.main()
    captured = capsys.readouterr().out
    assert "Package versions" in captured
    assert "Serialization survival checks" in captured
    assert "secret_placeholder" not in captured


def test_mock_ctx_send_returns_delivered_status():
    ctx = _mock_payment_ctx(send_result=SimpleNamespace(status="delivered"))
    result = _run(
        _send_orchestrator_request_payment(
            ctx,
            user_address="user-a",
            session_id="session-1",
            fee_usd=5.0,
            checkout=dict(COMPAT_CHECKOUT),
        )
    )
    assert result is True
