"""Tests for deterministic mock teammate agents."""

from shared_models import EconData, Item, RouteData, ShipmentRequest

from orchestrator.mock_agents import MockEconomistAgent, MockRoutingAgent, MockTreasuryAgent


def _shipment(**overrides) -> ShipmentRequest:
    base = ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="Widget", quantity=10, category="electronics")],
        total_weight_kg=300.0,
        total_volume_cbm=2.0,
        timeframe="COST",
        declared_value_usd=3000.0,
    )
    if overrides:
        return base.model_copy(update=overrides)
    return base


def test_economist_high_value_above_2500():
    econ = MockEconomistAgent().analyze(_shipment(declared_value_usd=2500.01))
    assert econ.is_high_value is True


def test_economist_not_high_value_at_exactly_2500():
    econ = MockEconomistAgent().analyze(_shipment(declared_value_usd=2500.0))
    assert econ.is_high_value is False


def test_economist_luxury_keyword_detection():
    shipment = ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="Designer handbag", quantity=1, category="accessories")],
        total_weight_kg=800.0,
        total_volume_cbm=1.0,
        timeframe="COST",
        declared_value_usd=4000.0,
    )
    econ = MockEconomistAgent().analyze(shipment)
    assert econ.is_luxury is True
    assert econ.transport_preference == "AIR"


def test_economist_air_for_weight_at_most_500():
    econ = MockEconomistAgent().analyze(_shipment(total_weight_kg=500.0))
    assert econ.transport_preference == "AIR"


def test_economist_air_for_speed():
    econ = MockEconomistAgent().analyze(
        _shipment(total_weight_kg=1500.0, timeframe="SPEED")
    )
    assert econ.transport_preference == "AIR"


def test_economist_either_for_middle_weight_cost():
    econ = MockEconomistAgent().analyze(
        _shipment(total_weight_kg=1500.0, timeframe="COST")
    )
    assert econ.transport_preference == "EITHER"


def test_economist_ship_for_heavy_cost():
    econ = MockEconomistAgent().analyze(
        _shipment(total_weight_kg=2500.0, timeframe="COST")
    )
    assert econ.transport_preference == "SHIP"


def test_economist_tax_calculation_is_deterministic():
    shipment = _shipment(declared_value_usd=10000.0)
    first = MockEconomistAgent().analyze(shipment)
    second = MockEconomistAgent().analyze(shipment)
    expected = round(max(30.0, 10000.0 * 0.0035) + 10000.0 * 0.05, 2)
    assert first.base_entry_tax_usd == expected
    assert second.base_entry_tax_usd == expected


def test_router_air_mode():
    shipment = _shipment(total_weight_kg=400.0, timeframe="SPEED")
    econ = EconData(
        transport_preference="AIR",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=500.0,
    )
    route = MockRoutingAgent().route(shipment, econ)
    assert route.selected_mode == "AIR"


def test_router_ship_mode():
    shipment = _shipment(total_weight_kg=2500.0, timeframe="COST")
    econ = EconData(
        transport_preference="SHIP",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=500.0,
    )
    route = MockRoutingAgent().route(shipment, econ)
    assert route.selected_mode == "SHIP"


def test_router_either_speed_chooses_air():
    shipment = _shipment(total_weight_kg=1500.0, timeframe="SPEED")
    econ = EconData(
        transport_preference="EITHER",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=500.0,
    )
    route = MockRoutingAgent().route(shipment, econ)
    assert route.selected_mode == "AIR"


def test_router_either_cost_chooses_ship():
    shipment = _shipment(total_weight_kg=1500.0, timeframe="COST")
    econ = EconData(
        transport_preference="EITHER",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=500.0,
    )
    route = MockRoutingAgent().route(shipment, econ)
    assert route.selected_mode == "SHIP"


def test_router_total_landed_cost_includes_entry_tax():
    shipment = _shipment()
    econ = MockEconomistAgent().analyze(shipment)
    route = MockRoutingAgent().route(shipment, econ)
    assert route.total_landed_cost_usd == round(
        route.freight_and_toll_cost_usd + econ.base_entry_tax_usd,
        2,
    )


def test_router_route_nodes_include_origin_and_destination_cities():
    shipment = _shipment()
    econ = MockEconomistAgent().analyze(shipment)
    route = MockRoutingAgent().route(shipment, econ)
    joined = " ".join(route.optimal_route_nodes)
    assert "Shenzhen" in joined
    assert "Austin" in joined


def test_router_countries_visited_ordered_and_deduplicated():
    shipment = _shipment()
    econ = MockEconomistAgent().analyze(shipment)
    route = MockRoutingAgent().route(shipment, econ)
    assert route.countries_visited == ["CN", "US"]


def test_router_same_input_produces_same_result():
    shipment = _shipment()
    econ = MockEconomistAgent().analyze(shipment)
    router = MockRoutingAgent()
    first = router.route(shipment, econ)
    second = router.route(shipment, econ)
    assert first == second


def test_treasury_quote_has_no_payment_hash():
    shipment = _shipment()
    econ = MockEconomistAgent().analyze(shipment)
    route = MockRoutingAgent().route(shipment, econ)
    quote = MockTreasuryAgent().prepare_quote(shipment, econ, route)
    assert quote.payment_hash is None


def test_treasury_quote_contains_route_costs_and_entry_tax():
    shipment = _shipment()
    econ = MockEconomistAgent().analyze(shipment)
    route = MockRoutingAgent().route(shipment, econ)
    quote = MockTreasuryAgent().prepare_quote(shipment, econ, route)
    prompt = quote.final_user_prompt
    assert "Route:" in prompt
    assert "Freight and transit charges" in prompt
    assert "Entry tax" in prompt
    assert "Total landed cost" in prompt


def test_treasury_quote_ends_with_confirm_instruction():
    shipment = _shipment()
    econ = MockEconomistAgent().analyze(shipment)
    route = MockRoutingAgent().route(shipment, econ)
    quote = MockTreasuryAgent().prepare_quote(shipment, econ, route)
    lines = [line for line in quote.final_user_prompt.splitlines() if line.strip()]
    assert lines[-1] == "Type CONFIRM to execute payment."


def test_treasury_payment_result_contains_simulated_hash():
    shipment = _shipment()
    econ = MockEconomistAgent().analyze(shipment)
    route = MockRoutingAgent().route(shipment, econ)
    result = MockTreasuryAgent().execute_payment(shipment, route)
    assert result.payment_hash is not None
    assert result.payment_hash.startswith("SIMULATED_")


def test_treasury_payment_hash_is_deterministic():
    shipment = _shipment()
    econ = MockEconomistAgent().analyze(shipment)
    route = MockRoutingAgent().route(shipment, econ)
    treasury = MockTreasuryAgent()
    first = treasury.execute_payment(shipment, route)
    second = treasury.execute_payment(shipment, route)
    assert first.payment_hash == second.payment_hash


def test_treasury_payment_message_states_no_real_payment():
    shipment = _shipment()
    econ = MockEconomistAgent().analyze(shipment)
    route = MockRoutingAgent().route(shipment, econ)
    result = MockTreasuryAgent().execute_payment(shipment, route)
    assert "no real payment occurred" in result.final_user_prompt.lower()
