"""Tests for Claude shipment extractor with mocked Anthropic client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import pytest

from orchestrator.extractor import (
    DEFAULT_MODEL,
    ClaudeShipmentExtractor,
    ExtractionError,
    ExtractionLocation,
    ExtractionPayload,
    ExtractorConfigurationError,
    build_extraction_user_content,
)
from orchestrator.models import ChatTurn, PartialItem, PartialShipmentData


def _parse_response(payload: ExtractionPayload | None) -> SimpleNamespace:
    return SimpleNamespace(parsed_output=payload)


def test_valid_parsed_payload_becomes_partial_shipment_data():
    client = MagicMock()
    client.messages.parse.return_value = _parse_response(
        ExtractionPayload(
            origin=ExtractionLocation(country="CN", state="Guangdong", city="Shenzhen"),
            destination=ExtractionLocation(country="US", state="TX", city="Austin"),
            items=[PartialItem(name="Widget", quantity=2, category="electronics")],
            total_weight_kg=100.0,
            total_volume_cbm=1.5,
            timeframe="SPEED",
            declared_value_usd=2000.0,
        )
    )
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    result = extractor.extract("Ship widgets", PartialShipmentData())
    assert result.origin["city"] == "Shenzhen"
    assert result.items[0].name == "Widget"


def test_partial_origin_and_destination_values():
    client = MagicMock()
    client.messages.parse.return_value = _parse_response(
        ExtractionPayload(
            origin=ExtractionLocation(city="Shenzhen"),
            destination=ExtractionLocation(city="Austin"),
        )
    )
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    result = extractor.extract(
        "Ship semiconductors from Shenzhen to Austin.",
        PartialShipmentData(),
    )
    assert result.origin == {"city": "Shenzhen"}
    assert result.destination == {"city": "Austin"}


def test_unknown_values_remain_none():
    client = MagicMock()
    client.messages.parse.return_value = _parse_response(
        ExtractionPayload(
            origin=ExtractionLocation(country="CN"),
            items=None,
            total_weight_kg=None,
        )
    )
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    result = extractor.extract("From China", PartialShipmentData())
    assert result.origin == {"country": "CN"}
    assert result.destination is None
    assert result.total_weight_kg is None


def test_conversion_to_partial_shipment_data():
    client = MagicMock()
    payload = ExtractionPayload(
        origin=ExtractionLocation(country="CN", state="Guangdong", city="Shenzhen"),
        destination=ExtractionLocation(country="US", state="TX", city="Austin"),
        items=[PartialItem(name="Semiconductor", quantity=1, category="electronics")],
    )
    client.messages.parse.return_value = _parse_response(payload)
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    result = extractor.extract("Ship semiconductors", PartialShipmentData())
    assert isinstance(result, PartialShipmentData)
    assert result.items[0].category == "electronics"


def test_request_includes_latest_user_message():
    client = MagicMock()
    client.messages.parse.return_value = _parse_response(ExtractionPayload())
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    extractor.extract("Ship 50kg to Austin", PartialShipmentData())
    user_content = client.messages.parse.call_args.kwargs["messages"][0]["content"]
    assert "Latest user message:" in user_content
    assert "Ship 50kg to Austin" in user_content


def test_request_includes_current_partial_data():
    client = MagicMock()
    client.messages.parse.return_value = _parse_response(ExtractionPayload())
    current = PartialShipmentData(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"}
    )
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    extractor.extract("Add destination Austin TX", current)
    user_content = client.messages.parse.call_args.kwargs["messages"][0]["content"]
    assert "Current partial shipment data:" in user_content
    assert "Guangdong" in user_content
    assert "Shenzhen" in user_content


def test_request_includes_previous_conversation_history():
    client = MagicMock()
    client.messages.parse.return_value = _parse_response(ExtractionPayload())
    history = [
        ChatTurn(role="user", content="Ship 2 items from Shenzhen to Austin"),
        ChatTurn(
            role="assistant",
            content="Please provide the item names, weight, and declared value.",
        ),
    ]
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    extractor.extract(
        "Item 1 is widgets, 50kg total",
        PartialShipmentData(),
        conversation_history=history,
    )
    user_content = client.messages.parse.call_args.kwargs["messages"][0]["content"]
    assert "Previous conversation during shipment collection:" in user_content
    assert "Ship 2 items from Shenzhen to Austin" in user_content
    assert "Item 1 is widgets, 50kg total" in user_content


def test_build_extraction_user_content_omits_history_when_empty():
    content = build_extraction_user_content(
        user_message="hello",
        current_data=PartialShipmentData(),
        conversation_history=[],
    )
    assert "Previous conversation during shipment collection:" not in content
    assert "Latest user message:" in content


def test_request_uses_extraction_payload_output_format():
    client = MagicMock()
    client.messages.parse.return_value = _parse_response(ExtractionPayload())
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    extractor.extract("hello", PartialShipmentData())
    assert client.messages.parse.call_args.kwargs["output_format"] is ExtractionPayload


def test_missing_parsed_output_raises_extraction_error():
    client = MagicMock()
    client.messages.parse.return_value = _parse_response(None)
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    with pytest.raises(ExtractionError):
        extractor.extract("hello", PartialShipmentData())


def test_invalid_parsed_data_raises_extraction_error():
    client = MagicMock()
    client.messages.parse.return_value = _parse_response(
        ExtractionPayload.model_construct(timeframe="FAST")
    )
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    with pytest.raises(ExtractionError):
        extractor.extract("urgent shipment", PartialShipmentData())


def test_simulated_api_failure_raises_safe_extraction_error():
    client = MagicMock()
    client.messages.parse.side_effect = anthropic.APIConnectionError(
        request=MagicMock()
    )
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    with pytest.raises(ExtractionError, match="Could not reach"):
        extractor.extract("hello", PartialShipmentData())


def test_blank_input_rejected_without_api_call():
    client = MagicMock()
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    with pytest.raises(ExtractionError):
        extractor.extract("   ", PartialShipmentData())
    client.messages.parse.assert_not_called()


def test_missing_api_key_raises_extractor_configuration_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ExtractorConfigurationError, match="ANTHROPIC_API_KEY"):
        ClaudeShipmentExtractor(client=None)


def test_injected_fake_client_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = MagicMock()
    client.messages.parse.return_value = _parse_response(ExtractionPayload())
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    extractor.extract("hello", PartialShipmentData())


def test_exception_messages_never_contain_fake_secret():
    secret = "sk-secret-test-key-12345"
    client = MagicMock()
    client.messages.parse.side_effect = anthropic.APIError(
        message=f"failed with {secret}",
        request=MagicMock(),
        body=None,
    )
    extractor = ClaudeShipmentExtractor(client=client, model=DEFAULT_MODEL)
    with pytest.raises(ExtractionError) as exc:
        extractor.extract("hello", PartialShipmentData())
    assert secret not in str(exc.value)


def test_authentication_error_is_safe():
    client = MagicMock()
    client.messages.parse.side_effect = anthropic.AuthenticationError(
        message="invalid x-api-key sk-live-secret",
        response=MagicMock(status_code=401),
        body=None,
    )
    extractor = ClaudeShipmentExtractor(client=client, model="test-model")
    with pytest.raises(ExtractionError) as exc:
        extractor.extract("hello", PartialShipmentData())
    assert "sk-live-secret" not in str(exc.value)
