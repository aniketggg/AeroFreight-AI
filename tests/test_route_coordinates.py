"""Tests for route_logic coordinate resolution and country normalization."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from shared_models import EconData, Item, ShipmentRequest
from step3_riya.route_logic import (
    UnsupportedLocationError,
    build_air_quote,
    build_ship_quote,
    calculate_route,
    resolve_coordinates,
)
from step3_riya.routing_models import RoutingRequest


def _shenzhen_austin_shipment(
    *,
    origin_country: str,
    destination_country: str,
) -> ShipmentRequest:
    return ShipmentRequest(
        origin={
            "country": origin_country,
            "state": "Guangdong",
            "city": "Shenzhen",
        },
        destination={
            "country": destination_country,
            "state": "TX",
            "city": "Austin",
        },
        items=[Item(name="Electronics", quantity=10, category="electronics")],
        total_weight_kg=800,
        total_volume_cbm=4.2,
        timeframe="COST",
        declared_value_usd=5000,
    )


def _routing_request(
    shipment: ShipmentRequest,
    *,
    transport_preference: str = "AIR",
) -> RoutingRequest:
    return RoutingRequest(
        shipment=shipment,
        econ=EconData(
            transport_preference=transport_preference,
            is_high_value=True,
            is_luxury=False,
            base_entry_tax_usd=350,
        ),
    )


@pytest.mark.parametrize(
    "country",
    ["China", "CHINA", "CN"],
)
def test_shenzhen_resolves_for_china_country_variants(country: str):
    coordinates = resolve_coordinates(
        {"country": country, "state": "Guangdong", "city": "Shenzhen"}
    )

    assert isinstance(coordinates, tuple)
    assert len(coordinates) == 2
    assert 22.0 < coordinates[0] < 23.0
    assert 114.0 < coordinates[1] < 115.0


@pytest.mark.parametrize(
    "country",
    ["United States", "US"],
)
def test_austin_resolves_for_united_states_country_variants(country: str):
    coordinates = resolve_coordinates(
        {"country": country, "state": "TX", "city": "Austin"}
    )

    assert isinstance(coordinates, tuple)
    assert len(coordinates) == 2
    assert 30.0 < coordinates[0] < 31.0
    assert -98.0 < coordinates[1] < -97.0


def test_city_outside_curated_dictionary_resolves_via_dataset():
    coordinates = resolve_coordinates(
        {"country": "France", "state": "IDF", "city": "Paris"}
    )

    assert 48.0 < coordinates[0] < 49.0
    assert 2.0 < coordinates[1] < 3.0


def test_unknown_city_raises_unsupported_location_error():
    with pytest.raises(UnsupportedLocationError, match="No coordinates configured"):
        resolve_coordinates(
            {"country": "CN", "state": "Guangdong", "city": "NotARealCityName"}
        )


@pytest.mark.parametrize(
    "location",
    [
        {"country": "Canada", "state": "Ontario", "city": "Toronto"},
        {"country": "CA", "state": "ON", "city": "Toronto"},
        {"country": "India", "state": "Maharashtra", "city": "Mumbai"},
        {"country": "IN", "state": "MH", "city": "Mumbai"},
    ],
)
def test_international_state_name_falls_back_to_largest_city(location: dict):
    coordinates = resolve_coordinates(location)

    assert isinstance(coordinates, tuple)
    assert len(coordinates) == 2


def test_build_air_quote_shenzhen_china_to_austin_united_states():
    request = _routing_request(
        _shenzhen_austin_shipment(
            origin_country="China",
            destination_country="United States",
        )
    )

    quote = build_air_quote(request)

    assert quote.mode == "AIR"
    assert quote.route_nodes
    assert quote.countries_visited == ["CN", "US"]
    assert quote.transport_subtotal_usd > 0


def test_build_ship_quote_with_full_country_names():
    request = _routing_request(
        _shenzhen_austin_shipment(
            origin_country="China",
            destination_country="United States",
        ),
        transport_preference="SHIP",
    )

    quote = build_ship_quote(request)

    assert quote.mode == "SHIP"
    assert quote.route_nodes
    assert quote.countries_visited == ["CN", "US"]
    assert quote.transport_subtotal_usd > 0


def test_airport_and_port_helpers_receive_iso_country_codes():
    request = _routing_request(
        _shenzhen_austin_shipment(
            origin_country="China",
            destination_country="United States",
        )
    )

    with (
        patch(
            "step3_riya.route_logic.airports_in_country",
            wraps=__import__(
                "step3_riya.airport_data",
                fromlist=["airports_in_country"],
            ).airports_in_country,
        ) as mock_airports,
        patch(
            "step3_riya.route_logic.ports_in_country",
            wraps=__import__(
                "step3_riya.port_data",
                fromlist=["ports_in_country"],
            ).ports_in_country,
        ) as mock_ports,
    ):
        build_air_quote(request)
        build_ship_quote(request)

    assert mock_airports.call_args_list[0].args[0] == "CN"
    assert mock_airports.call_args_list[1].args[0] == "US"
    assert mock_ports.call_args_list[0].args[0] == "CN"
    assert mock_ports.call_args_list[1].args[0] == "US"


def test_route_selection_and_costs_unchanged_for_iso_country_codes():
    iso_request = _routing_request(
        _shenzhen_austin_shipment(
            origin_country="CN",
            destination_country="US",
        ),
        transport_preference="EITHER",
    )

    first = calculate_route(iso_request)
    second = calculate_route(iso_request)

    assert first == second
    assert first.selected_mode in {"AIR", "SHIP"}
    assert first.countries_visited == ["CN", "US"]
    assert first.total_landed_cost_usd >= first.freight_and_toll_cost_usd
