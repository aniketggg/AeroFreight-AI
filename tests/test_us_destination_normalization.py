"""Tests for U.S. destination normalization and correction merging."""

from __future__ import annotations

import asyncio

import pytest

from orchestrator.agent_interfaces import PaymentSetupResult
from orchestrator.conversation import ConversationController
from orchestrator.coordinator import WorkflowCoordinator
from orchestrator.location_normalization import (
    US_COUNTRY_CODE,
    _US_STATE_CODES,
    _US_STATE_NAMES,
    canonicalize_country,
    infer_us_country_from_state,
    is_us_country,
    normalize_location,
    normalize_partial_shipment,
)
from orchestrator.mock_agents import (
    MockEconomistAgent,
    MockRoutingAgent,
    MockTreasuryAgent,
)
from orchestrator.models import PartialItem, PartialShipmentData, WorkflowStage
from orchestrator.service import OrchestratorService
from orchestrator.session_store import InMemorySessionStore
from orchestrator.validation import (
    build_shipment_request,
    get_missing_fields,
    merge_partial_data,
    validate_business_rules,
)

LIVE_SHIPMENT_AUSTIN_NO_COUNTRY = (
    "Ship 500 kilograms of semiconductors from Shenzhen, Guangdong, China to "
    "Austin, Texas. The cargo is 3 cubic meters, worth $100,000, contains "
    "200 units, and speed is the priority."
)
LIVE_SHIPMENT_AUSTIN_WITH_COUNTRY = (
    "Ship 500 kilograms of semiconductors from Shenzhen, Guangdong, China to "
    "Austin, Texas, United States. The cargo is 3 cubic meters, worth $100,000, "
    "contains 200 units, and speed is the priority."
)


def _live_base_partial(*, destination: dict) -> PartialShipmentData:
    return PartialShipmentData(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination=destination,
        items=[PartialItem(name="semiconductors", quantity=200, category="electronics")],
        total_weight_kg=500.0,
        total_volume_cbm=3.0,
        timeframe="SPEED",
        declared_value_usd=100_000.0,
    )


@pytest.mark.parametrize(
    "state",
    ["Texas", "TX", "texas", " tx ", "California", "CA", "New York", "NY", "District of Columbia", "DC"],
)
def test_us_state_inference_is_case_insensitive(state: str):
    location = normalize_location({"city": "Austin", "state": state})
    assert location is not None
    assert location["country"] == US_COUNTRY_CODE


@pytest.mark.parametrize("code", sorted(_US_STATE_CODES))
def test_all_us_state_abbreviations_infer_united_states(code: str):
    assert infer_us_country_from_state(code) == US_COUNTRY_CODE


@pytest.mark.parametrize("name", _US_STATE_NAMES.keys())
def test_all_us_state_names_infer_united_states(name: str):
    assert infer_us_country_from_state(name) == US_COUNTRY_CODE


@pytest.mark.parametrize(
    ("country", "expected"),
    [
        ("United States", US_COUNTRY_CODE),
        ("United States of America", US_COUNTRY_CODE),
        ("US", US_COUNTRY_CODE),
        ("USA", US_COUNTRY_CODE),
        ("U.S.", US_COUNTRY_CODE),
        ("U.S.A.", US_COUNTRY_CODE),
        (" united states ", US_COUNTRY_CODE),
        ("China", "CHINA"),
    ],
)
def test_country_aliases_canonicalize(country: str, expected: str):
    assert canonicalize_country(country) == expected
    if expected == US_COUNTRY_CODE:
        assert is_us_country(country)


def test_austin_texas_without_country_is_accepted_after_normalization():
    data = normalize_partial_shipment(
        PartialShipmentData(
            origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
            destination={"city": "Austin", "state": "Texas"},
            items=[PartialItem(name="Widget", quantity=1, category="electronics")],
            total_weight_kg=10.0,
            total_volume_cbm=1.0,
            timeframe="SPEED",
            declared_value_usd=100.0,
        )
    )
    assert validate_business_rules(data) == []
    request = build_shipment_request(data)
    assert request.destination["country"] == US_COUNTRY_CODE


def test_austin_texas_united_states_explicit_is_accepted():
    data = normalize_partial_shipment(
        PartialShipmentData(
            origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
            destination={"city": "Austin", "state": "Texas", "country": "United States"},
            items=[PartialItem(name="Widget", quantity=1, category="electronics")],
            total_weight_kg=10.0,
            total_volume_cbm=1.0,
            timeframe="SPEED",
            declared_value_usd=100.0,
        )
    )
    assert validate_business_rules(data) == []
    assert build_shipment_request(data).destination["country"] == US_COUNTRY_CODE


def test_explicit_conflicting_country_is_not_overwritten():
    location = normalize_location(
        {"city": "Austin", "state": "Texas", "country": "Canada"}
    )
    assert location is not None
    assert location["country"] == "CANADA"
    errors = validate_business_rules(
        PartialShipmentData(
            origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
            destination=location,
            items=[PartialItem(name="Widget", quantity=1, category="electronics")],
            total_weight_kg=10.0,
            total_volume_cbm=1.0,
            timeframe="SPEED",
            declared_value_usd=100.0,
        )
    )
    assert any("outside the united states" in error.lower() for error in errors)


def test_city_without_state_does_not_infer_country():
    location = normalize_location({"city": "Austin"})
    assert location is not None
    assert "country" not in location or not location.get("country")


@pytest.mark.parametrize("province", ["Ontario", "ON", "British Columbia", "BC", "Quebec"])
def test_canadian_provinces_do_not_infer_united_states(province: str):
    assert infer_us_country_from_state(province) is None


def test_missing_country_asks_for_country_not_scope_error():
    data = PartialShipmentData(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"city": "Paris", "state": "Paris"},
        items=[PartialItem(name="Widget", quantity=1, category="electronics")],
        total_weight_kg=10.0,
        total_volume_cbm=1.0,
        timeframe="SPEED",
        declared_value_usd=100.0,
    )
    missing = get_missing_fields(normalize_partial_shipment(data))
    errors = validate_business_rules(normalize_partial_shipment(data))
    assert "destination.country" in missing
    assert errors == []


def test_correction_message_updates_stale_destination_country():
    current = _live_base_partial(destination={"city": "Austin", "state": "Texas"})
    incoming = PartialShipmentData(
        destination={"city": "Austin", "state": "Texas", "country": "United States"}
    )
    merged = normalize_partial_shipment(merge_partial_data(current, incoming))
    assert merged.destination["country"] == US_COUNTRY_CODE
    assert merged.total_weight_kg == 500.0
    assert merged.total_volume_cbm == 3.0
    assert merged.declared_value_usd == 100_000.0
    assert merged.timeframe == "SPEED"
    assert merged.items[0].quantity == 200
    assert validate_business_rules(merged) == []


def test_fresh_live_wording_with_united_states_succeeds():
    data = normalize_partial_shipment(
        _live_base_partial(
            destination={"city": "Austin", "state": "Texas", "country": "United States"}
        )
    )
    assert validate_business_rules(data) == []
    request = build_shipment_request(data)
    assert request.destination["country"] == US_COUNTRY_CODE


def test_fresh_live_wording_with_texas_only_infers_united_states():
    data = normalize_partial_shipment(
        _live_base_partial(destination={"city": "Austin", "state": "Texas"})
    )
    assert validate_business_rules(data) == []
    request = build_shipment_request(data)
    assert request.destination["country"] == US_COUNTRY_CODE


class FakeExtractor:
    def __init__(self, responses: list[PartialShipmentData]) -> None:
        self.responses = list(responses)

    def extract(self, user_message: str, current_data: PartialShipmentData):
        if self.responses:
            return self.responses.pop(0)
        return PartialShipmentData()


class FakeTreasuryPaymentClient:
    async def prepare_payment(self, **kwargs) -> PaymentSetupResult:
        return PaymentSetupResult(
            checkout={"checkout_session_id": "cs_test_123"},
            fee_usd=5.0,
        )

    async def finalize_payment(self, **kwargs):
        raise NotImplementedError


def _run(coro):
    return asyncio.run(coro)


def test_two_message_live_correction_reaches_remote_payment_workflow():
    """Correction updates stale destination when the first extraction omits state."""
    service = OrchestratorService(InMemorySessionStore())
    controller = ConversationController(
        service,
        FakeExtractor(
            [
                _live_base_partial(
                    destination={"city": "Austin"}
                ),
                _live_base_partial(
                    destination={
                        "city": "Austin",
                        "state": "Texas",
                        "country": "United States",
                    }
                ),
            ]
        ),
    )
    coordinator = WorkflowCoordinator(
        conversation=controller,
        service=service,
        economist=MockEconomistAgent(),
        router=MockRoutingAgent(),
        treasury=MockTreasuryAgent(),
        treasury_payment_client=FakeTreasuryPaymentClient(),
    )

    session, response, _ = _run(
        coordinator.handle_user_message_async("user-1", LIVE_SHIPMENT_AUSTIN_NO_COUNTRY)
    )
    assert session.stage == WorkflowStage.COLLECTING_INPUT
    assert "destination country" in response.lower()

    session, response, setup = _run(
        coordinator.handle_user_message_async(
            "user-1", LIVE_SHIPMENT_AUSTIN_WITH_COUNTRY
        )
    )
    assert session.stage == WorkflowStage.AWAITING_PAYMENT
    assert setup is not None
    assert "Suggested mode" not in response
    assert session.partial_data.destination["country"] == US_COUNTRY_CODE
    assert session.partial_data.total_weight_kg == 500.0


def test_live_wording_austin_texas_only_reaches_remote_payment_workflow():
    service = OrchestratorService(InMemorySessionStore())
    controller = ConversationController(
        service,
        FakeExtractor(
            [_live_base_partial(destination={"city": "Austin", "state": "Texas"})]
        ),
    )
    coordinator = WorkflowCoordinator(
        conversation=controller,
        service=service,
        economist=MockEconomistAgent(),
        router=MockRoutingAgent(),
        treasury=MockTreasuryAgent(),
        treasury_payment_client=FakeTreasuryPaymentClient(),
    )

    session, response, setup = _run(
        coordinator.handle_user_message_async("user-1", LIVE_SHIPMENT_AUSTIN_NO_COUNTRY)
    )
    assert session.stage == WorkflowStage.AWAITING_PAYMENT
    assert setup is not None
    assert session.shipment_request.destination["country"] == US_COUNTRY_CODE
