"""Claude-powered shipment field extraction."""

from __future__ import annotations

import json
import os
from typing import Literal, Protocol

import anthropic
from anthropic import Anthropic, AuthenticationError, RateLimitError
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from orchestrator.location_normalization import scrub_city_name
from orchestrator.models import PartialItem, PartialShipmentData, ChatTurn

load_dotenv()

DEFAULT_MODEL = "claude-opus-4-6"

SYSTEM_INSTRUCTIONS = """You extract shipment information for a freight forwarding orchestrator.

Rules:
- Extract shipment fields from the latest user message.
- Use the previous conversation and current partial data to resolve references such as "item 1", "that destination", or "same as before".
- Preserve existing information unless the user clearly corrects it.
- Return null for unknown fields.
- Never invent weight, volume, declared value, quantity, location, or timeframe.
- Never estimate freight costs, taxes, tariffs, routes, documents, or payment details.
- Convert wording such as "fast," "urgent," "quickest," and "as soon as possible" to SPEED.
- Convert wording such as "cheap," "cheapest," "budget," and "lowest cost" to COST.
- Normalize obvious country codes to uppercase.
- For U.S. destinations, prefer country "United States" with city and state/region fields, for example Austin + Texas + United States.
- When the user gives a U.S. city and state without a country, still extract the city and state; do not invent a non-U.S. country.
- Never combine city and state into a single field. The city field must contain ONLY the name of the city (e.g., "Mumbai"). The state field must contain ONLY the state/province name. Do not write internal thoughts or sentences into the JSON.
- When updating existing partial shipment data across multiple conversation turns, apply the same location rules strictly. Fill in missing or corrected fields only. Never embed explanations, corrections, or reasoning inside JSON string values (for example, never city "Mumbai, state is Maharashtra").
- You may infer a broad item category only when clearly implied by the item.
- Do not default an unknown quantity to 1.
- Do not interpret CONFIRM or NEW SHIPMENT as shipment information.
- Return all known current values, including unchanged values, so the result represents the updated partial shipment.

Return structured shipment data matching the requested output format."""


class ExtractionError(Exception):
    """Safe error raised when shipment extraction fails."""


class ExtractorConfigurationError(ExtractionError):
    """Raised when the extractor is not configured."""


class ExtractionLocation(BaseModel):
    country: str | None = None
    state: str | None = None
    city: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("city", mode="before")
    @classmethod
    def _scrub_city(cls, value: object) -> str | None:
        return scrub_city_name(value)


class ExtractionPayload(BaseModel):
    origin: ExtractionLocation | None = None
    destination: ExtractionLocation | None = None
    items: list[PartialItem] | None = None
    total_weight_kg: float | None = None
    total_volume_cbm: float | None = None
    timeframe: Literal["SPEED", "COST"] | None = None
    declared_value_usd: float | None = None

    model_config = ConfigDict(extra="forbid")


class ShipmentExtractor(Protocol):
    def extract(
        self,
        user_message: str,
        current_data: PartialShipmentData,
        conversation_history: list[ChatTurn] | None = None,
    ) -> PartialShipmentData:
        ...


INCREMENTAL_EXTRACTION_REMINDER = """\
Incremental update rules (strict):
- Merge the latest user message into the current partial shipment data.
- Never combine city and state. The city field must contain ONLY the name of the city.
- The state field must contain ONLY the state/province name.
- Do not write internal thoughts, explanations, or sentences into JSON values."""


def _is_incremental_extraction(
    current_data: PartialShipmentData,
    conversation_history: list[ChatTurn] | None,
) -> bool:
    if conversation_history:
        return True
    return bool(current_data.model_dump(exclude_none=True))


def build_extraction_user_content(
    *,
    user_message: str,
    current_data: PartialShipmentData,
    conversation_history: list[ChatTurn] | None = None,
) -> str:
    """Build the user prompt for Claude including prior collection context."""
    parts = [
        "Current partial shipment data:",
        json.dumps(current_data.model_dump(mode="json"), indent=2),
    ]
    history = conversation_history or []
    if history:
        parts.append("\nPrevious conversation during shipment collection:")
        for turn in history:
            speaker = "User" if turn.role == "user" else "Assistant"
            parts.append(f"{speaker}: {turn.content}")
    if _is_incremental_extraction(current_data, history):
        parts.append(f"\n{INCREMENTAL_EXTRACTION_REMINDER}")
    parts.extend(["\nLatest user message:", user_message])
    return "\n".join(parts)


def _location_to_dict(location: ExtractionLocation | None) -> dict | None:
    if location is None:
        return None
    data = location.model_dump(exclude_none=True)
    return data or None


def _payload_to_partial(payload: ExtractionPayload) -> PartialShipmentData:
    data = {
        "origin": _location_to_dict(payload.origin),
        "destination": _location_to_dict(payload.destination),
        "items": payload.items,
        "total_weight_kg": payload.total_weight_kg,
        "total_volume_cbm": payload.total_volume_cbm,
        "timeframe": payload.timeframe,
        "declared_value_usd": payload.declared_value_usd,
    }
    return PartialShipmentData.model_validate(data)


class ClaudeShipmentExtractor:
    """Extract partial shipment data using Anthropic Claude structured output."""

    def __init__(
        self,
        client: Anthropic | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model or os.getenv("ANTHROPIC_MODEL") or DEFAULT_MODEL

        if client is not None:
            self._client = client
            return

        resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ExtractorConfigurationError(
                "ANTHROPIC_API_KEY is not configured. "
                "Set it in your environment or .env file."
            )
        self._client = Anthropic(api_key=resolved_key)

    def extract(
        self,
        user_message: str,
        current_data: PartialShipmentData,
        conversation_history: list[ChatTurn] | None = None,
    ) -> PartialShipmentData:
        """Extract shipment fields using latest message plus collection context."""
        if not user_message.strip():
            raise ExtractionError("Please provide a message describing your shipment.")

        user_content = build_extraction_user_content(
            user_message=user_message,
            current_data=current_data,
            conversation_history=conversation_history,
        )

        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=SYSTEM_INSTRUCTIONS,
                messages=[{"role": "user", "content": user_content}],
                output_format=ExtractionPayload,
            )
        except AuthenticationError as exc:
            raise ExtractionError(
                "The extraction service could not authenticate. "
                "Please check your API key configuration."
            ) from exc
        except RateLimitError as exc:
            raise ExtractionError(
                "The extraction service is temporarily busy. Please try again shortly."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError(
                "Could not reach the extraction service. Please try again later."
            ) from exc
        except anthropic.APIError as exc:
            raise ExtractionError(
                "The extraction service returned an unexpected error."
            ) from exc

        parsed_output = response.parsed_output
        if parsed_output is None:
            raise ExtractionError(
                "The extraction service did not return structured shipment data."
            )

        try:
            return _payload_to_partial(parsed_output)
        except ValidationError as exc:
            raise ExtractionError(
                "The extraction service returned invalid shipment data."
            ) from exc
