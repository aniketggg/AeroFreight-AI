"""Tests for Treasury pricing mapped to central models."""

from __future__ import annotations

from shared_models import EconData, RouteData
from treasury_agent.pricing import FLOOR_FEE_USD, compute_service_fee


def _sample_route(*, countries: list[str] | None = None) -> RouteData:
    return RouteData(
        selected_mode="SHIP",
        optimal_route_nodes=["Shenzhen", "USLAX", "Austin"],
        countries_visited=countries or ["CN", "US"],
        freight_and_toll_cost_usd=645.0,
        total_landed_cost_usd=771.25,
    )


def _sample_econ(*, high_value: bool = True) -> EconData:
    return EconData(
        transport_preference="EITHER",
        is_high_value=high_value,
        is_luxury=False,
        base_entry_tax_usd=126.50,
    )


def test_pricing_accepts_central_models():
    fee = compute_service_fee(_sample_econ(), _sample_route())
    assert fee.total_fee_usd >= FLOOR_FEE_USD


def test_pricing_is_deterministic():
    econ = _sample_econ()
    route = _sample_route()
    first = compute_service_fee(econ, route)
    second = compute_service_fee(econ, route)
    assert first == second


def test_pricing_adds_complexity_surcharge_for_extra_countries():
    base = compute_service_fee(_sample_econ(), _sample_route(countries=["CN", "US"]))
    complex_route = compute_service_fee(
        _sample_econ(),
        _sample_route(countries=["CN", "SG", "US"]),
    )
    assert complex_route.complexity_surcharge_usd > base.complexity_surcharge_usd
