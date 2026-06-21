"""Tests for Treasury wire message validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from uagents import Model

from shared_models import (
    DocTemplates,
    EconData,
    RouteData,
    SettlementStatus,
    ShipmentRequest,
    Item,
)
from treasury_agent.messages import (
    PaymentFinalizeRequestMessage,
    PaymentFinalizeResponseMessage,
    PaymentSetupRequestMessage,
    PaymentSetupResponseMessage,
    SettlementRequestMessage,
    SettlementResultMessage,
)


def _valid_payload() -> dict:
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
    return {
        "user_address": "agent1quser",
        "session_id": "session-1",
        "shipment": shipment.model_dump(),
        "econ_data": econ.model_dump(),
        "route_data": route.model_dump(),
        "doc_templates": docs.model_dump(),
    }


def test_wire_messages_are_uagents_models():
    assert issubclass(SettlementRequestMessage, Model)
    assert issubclass(SettlementResultMessage, Model)
    assert issubclass(PaymentSetupRequestMessage, Model)
    assert issubclass(PaymentSetupResponseMessage, Model)
    assert issubclass(PaymentFinalizeRequestMessage, Model)
    assert issubclass(PaymentFinalizeResponseMessage, Model)


def test_payment_setup_request_validates_into_central_models():
    payload = _valid_payload()
    message = PaymentSetupRequestMessage(**payload)

    assert ShipmentRequest.model_validate(message.shipment)
    assert EconData.model_validate(message.econ_data)
    assert RouteData.model_validate(message.route_data)
    assert DocTemplates.model_validate(message.doc_templates)


def test_invalid_wire_shipment_produces_safe_validation_failure():
    payload = _valid_payload()
    payload["shipment"] = {"origin": "invalid"}

    with pytest.raises(ValidationError):
        ShipmentRequest.model_validate(payload["shipment"])


def test_successful_result_serializes_central_settlement_status():
    status = SettlementStatus(
        filled_documents={"invoice_drive_link": None},
        final_user_prompt="Payment completed.",
        payment_hash="cs_test_123",
    )
    message = SettlementResultMessage(
        ok=True,
        session_id="session-1",
        settlement_status=status.model_dump(),
    )

    round_trip = SettlementStatus.model_validate(message.settlement_status)
    assert round_trip.payment_hash == "cs_test_123"
