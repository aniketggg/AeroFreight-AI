"""Tests for deterministic validation."""

import pytest

from orchestrator.models import PartialItem, PartialShipmentData
from orchestrator.validation import (
    build_shipment_request,
    get_missing_fields,
    make_follow_up_question,
    merge_partial_data,
    validate_business_rules,
)


def _complete_data() -> PartialShipmentData:
    return PartialShipmentData(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[PartialItem(name="Widget", quantity=10, category="electronics")],
        total_weight_kg=100.0,
        total_volume_cbm=2.0,
        timeframe="SPEED",
        declared_value_usd=3000.0,
    )


def test_fully_valid_shipment():
    data = _complete_data()
    assert get_missing_fields(data) == []
    assert validate_business_rules(data) == []
    request = build_shipment_request(data)
    assert request.origin["country"] == "CN"
    assert request.timeframe == "SPEED"


def test_missing_weight_volume_value_timeframe():
    data = PartialShipmentData(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[PartialItem(name="Widget", quantity=1, category="electronics")],
    )
    missing = get_missing_fields(data)
    assert "total_weight_kg" in missing
    assert "total_volume_cbm" in missing
    assert "declared_value_usd" in missing
    assert "timeframe" in missing


def test_missing_nested_location_values():
    data = PartialShipmentData(
        origin={"country": "CN"},
        destination={"country": "US", "state": "TX"},
    )
    missing = get_missing_fields(data)
    assert "origin.state" in missing
    assert "origin.city" in missing
    assert "destination.city" in missing


def test_partial_item_information():
    data = PartialShipmentData(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[PartialItem(name="Widget")],
        total_weight_kg=10.0,
        total_volume_cbm=1.0,
        timeframe="COST",
        declared_value_usd=100.0,
    )
    missing = get_missing_fields(data)
    assert "items[0].quantity" in missing
    assert "items[0].category" in missing


def test_empty_item_list():
    data = PartialShipmentData(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[],
        total_weight_kg=10.0,
        total_volume_cbm=1.0,
        timeframe="COST",
        declared_value_usd=100.0,
    )
    assert "items" in get_missing_fields(data)


def test_zero_and_negative_numeric_values():
    data = _complete_data().model_copy(
        update={
            "total_weight_kg": 0,
            "total_volume_cbm": -1,
            "declared_value_usd": 0,
            "items": [PartialItem(name="X", quantity=0, category="goods")],
        }
    )
    errors = validate_business_rules(data)
    assert any("weight" in e.lower() for e in errors)
    assert any("volume" in e.lower() for e in errors)
    assert any("declared value" in e.lower() for e in errors)
    assert any("quantity" in e.lower() for e in errors)


def test_destination_outside_united_states():
    data = _complete_data().model_copy(
        update={"destination": {"country": "CA", "state": "ON", "city": "Toronto"}}
    )
    errors = validate_business_rules(data)
    assert any("outside the united states" in e.lower() for e in errors)


def test_origin_inside_united_states():
    data = _complete_data().model_copy(
        update={"origin": {"country": "US", "state": "CA", "city": "LA"}}
    )
    errors = validate_business_rules(data)
    assert any("origin country" in e.lower() for e in errors)


def test_same_origin_and_destination():
    location = {"country": "US", "state": "TX", "city": "Austin"}
    data = _complete_data().model_copy(
        update={"origin": location, "destination": dict(location)}
    )
    errors = validate_business_rules(data)
    assert any("same city" in e.lower() for e in errors)


def test_merge_without_mutating_original_objects():
    current = PartialShipmentData(
        origin={"country": "CN", "state": "Guangdong"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
    )
    incoming = PartialShipmentData(
        origin={"city": "Shenzhen"},
        total_weight_kg=50.0,
    )
    merged = merge_partial_data(current, incoming)
    assert current.origin == {"country": "CN", "state": "Guangdong"}
    assert merged.origin == {
        "country": "CN",
        "state": "Guangdong",
        "city": "Shenzhen",
    }
    assert merged.total_weight_kg == 50.0


def test_nested_location_merging_preserves_country():
    current = PartialShipmentData(origin={"country": "CN", "state": "Guangdong"})
    incoming = PartialShipmentData(origin={"city": "Shenzhen"})
    merged = merge_partial_data(current, incoming)
    assert merged.origin["country"] == "CN"
    assert merged.origin["state"] == "Guangdong"
    assert merged.origin["city"] == "Shenzhen"


def test_country_code_normalization():
    data = _complete_data().model_copy(
        update={
            "origin": {"country": "cn", "state": "Guangdong", "city": "Shenzhen"},
            "destination": {"country": "us", "state": "TX", "city": "Austin"},
        }
    )
    request = build_shipment_request(data)
    assert request.origin["country"] == "CN"
    assert request.destination["country"] == "US"


def test_follow_up_friendly_wording():
    message = make_follow_up_question(
        ["total_weight_kg", "timeframe", "items[0].name"],
        ["Declared value must be greater than zero."],
    )
    assert "total weight in kilograms" in message
    assert "whether SPEED or COST is preferred" in message
    assert "product name" in message
    assert "Declared value must be greater than zero." in message
    assert "pydantic" not in message.lower()
    assert "json" not in message.lower()


def test_build_shipment_request_raises_on_problems():
    with pytest.raises(ValueError):
        build_shipment_request(PartialShipmentData())
