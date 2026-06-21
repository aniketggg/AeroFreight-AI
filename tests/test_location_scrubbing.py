"""Tests for location city scrubbing and incremental extraction prompts."""

from __future__ import annotations

from orchestrator.extractor import (
    ExtractionLocation,
    ExtractionPayload,
    INCREMENTAL_EXTRACTION_REMINDER,
    build_extraction_user_content,
)
from orchestrator.location_normalization import (
    normalize_location,
    normalize_partial_shipment,
    scrub_city_name,
)
from orchestrator.models import ChatTurn, PartialShipmentData


def test_scrub_city_name_strips_issuer_and_state_commentary():
    raw = (
        "Mumbaiissuer state is Maharashtra, "
        "it should be: Maharashtra only in state field"
    )
    assert scrub_city_name(raw) == "Mumbai"


def test_scrub_city_name_strips_json_fragment():
    assert scrub_city_name("Mumbai','state':'Maharashtra") == "Mumbai"


def test_scrub_city_name_preserves_clean_city():
    assert scrub_city_name("Shenzhen") == "Shenzhen"
    assert scrub_city_name("New York") == "New York"


def test_extraction_location_validator_scrubs_city():
    location = ExtractionLocation(
        city="Mumbaiissuer state is Maharashtra",
        state="Maharashtra",
        country="India",
    )
    assert location.city == "Mumbai"


def test_normalize_location_scrubs_city_on_merge():
    normalized = normalize_location(
        {
            "country": "India",
            "state": "Maharashtra",
            "city": "Mumbaiissuer state is Maharashtra",
        }
    )
    assert normalized is not None
    assert normalized["city"] == "Mumbai"


def test_normalize_partial_shipment_scrubs_destination_city():
    data = normalize_partial_shipment(
        PartialShipmentData(
            destination={
                "country": "US",
                "state": "TX",
                "city": "Austinissuer state is Texas",
            }
        )
    )
    assert data.destination is not None
    assert data.destination["city"] == "Austin"


def test_incremental_user_prompt_includes_strict_location_rules():
    content = build_extraction_user_content(
        user_message="Origin is Mumbai, Maharashtra, India",
        current_data=PartialShipmentData(origin={"country": "IN"}),
        conversation_history=[
            ChatTurn(role="user", content="Ship electronics to Austin"),
            ChatTurn(role="assistant", content="What is the origin?"),
        ],
    )
    assert INCREMENTAL_EXTRACTION_REMINDER in content
    assert "Never combine city and state" in content


def test_single_shot_prompt_omits_incremental_reminder_without_context():
    content = build_extraction_user_content(
        user_message="Ship from Shenzhen to Austin",
        current_data=PartialShipmentData(),
        conversation_history=[],
    )
    assert INCREMENTAL_EXTRACTION_REMINDER not in content


def test_payload_to_partial_scrubs_hallucinated_city_via_validator():
    payload = ExtractionPayload(
        origin=ExtractionLocation.model_validate(
            {
                "country": "India",
                "state": "Maharashtra",
                "city": "Mumbaiissuer state is Maharashtra",
            }
        )
    )
    assert payload.origin is not None
    assert payload.origin.city == "Mumbai"
