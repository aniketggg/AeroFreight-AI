"""Tests for shared inter-agent models."""

import pytest
from pydantic import ValidationError

from shared_models import (
    EconData,
    Item,
    RouteData,
    SettlementStatus,
    ShipmentRequest,
)


def test_item_valid_construction():
    item = Item(name="Widget", quantity=2, category="electronics")
    assert item.name == "Widget"
    assert item.quantity == 2
    assert item.category == "electronics"


def test_shipment_request_serialization_and_reconstruction():
    request = ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="Widget", quantity=1, category="electronics")],
        total_weight_kg=100.0,
        total_volume_cbm=2.5,
        timeframe="SPEED",
        declared_value_usd=5000.0,
    )
    data = request.model_dump()
    restored = ShipmentRequest.model_validate(data)
    assert restored == request


def test_invalid_lowercase_timeframe():
    with pytest.raises(ValidationError):
        ShipmentRequest(
            origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
            destination={"country": "US", "state": "TX", "city": "Austin"},
            items=[Item(name="Widget", quantity=1, category="electronics")],
            total_weight_kg=100.0,
            total_volume_cbm=2.5,
            timeframe="speed",
            declared_value_usd=5000.0,
        )


def test_invalid_transport_preference():
    with pytest.raises(ValidationError):
        EconData(
            transport_preference="TRUCK",
            is_high_value=False,
            is_luxury=False,
            base_entry_tax_usd=100.0,
        )


def test_settlement_status_optional_payment_hash():
    status = SettlementStatus(
        filled_documents={},
        final_user_prompt="Quote",
    )
    assert status.payment_hash is None


def test_route_data_construction():
    route = RouteData(
        selected_mode="AIR",
        optimal_route_nodes=["A", "B"],
        countries_visited=["CN", "US"],
        freight_and_toll_cost_usd=1000.0,
        total_landed_cost_usd=1200.0,
    )
    assert route.selected_mode == "AIR"
