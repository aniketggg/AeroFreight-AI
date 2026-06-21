"""Deterministic U.S. destination and country normalization."""

from __future__ import annotations

import re

from orchestrator.models import PartialShipmentData

US_COUNTRY_CODE = "US"

_US_COUNTRY_ALIASES: dict[str, str] = {
    "US": US_COUNTRY_CODE,
    "USA": US_COUNTRY_CODE,
    "U S": US_COUNTRY_CODE,
    "U S A": US_COUNTRY_CODE,
    "UNITED STATES": US_COUNTRY_CODE,
    "UNITED STATES OF AMERICA": US_COUNTRY_CODE,
}

_US_STATE_NAMES: dict[str, str] = {
    "ALABAMA": "AL",
    "ALASKA": "AK",
    "ARIZONA": "AZ",
    "ARKANSAS": "AR",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "CONNECTICUT": "CT",
    "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "HAWAII": "HI",
    "IDAHO": "ID",
    "ILLINOIS": "IL",
    "INDIANA": "IN",
    "IOWA": "IA",
    "KANSAS": "KS",
    "KENTUCKY": "KY",
    "LOUISIANA": "LA",
    "MAINE": "ME",
    "MARYLAND": "MD",
    "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI",
    "MINNESOTA": "MN",
    "MISSISSIPPI": "MS",
    "MISSOURI": "MO",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM",
    "NEW YORK": "NY",
    "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND",
    "OHIO": "OH",
    "OKLAHOMA": "OK",
    "OREGON": "OR",
    "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "UTAH": "UT",
    "VERMONT": "VT",
    "VIRGINIA": "VA",
    "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
}

_US_STATE_CODES = frozenset(_US_STATE_NAMES.values())

_CANADIAN_PROVINCES = frozenset(
    {
        "AB",
        "BC",
        "MB",
        "NB",
        "NL",
        "NS",
        "NT",
        "NU",
        "ON",
        "PE",
        "QC",
        "SK",
        "YT",
        "ALBERTA",
        "BRITISH COLUMBIA",
        "MANITOBA",
        "NEW BRUNSWICK",
        "NEWFOUNDLAND AND LABRADOR",
        "NOVA SCOTIA",
        "NORTHWEST TERRITORIES",
        "NUNAVUT",
        "ONTARIO",
        "PRINCE EDWARD ISLAND",
        "QUEBEC",
        "SASKATCHEWAN",
        "YUKON",
    }
)


def _is_blank(value: object | None) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _normalize_key(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[.\s]+", " ", text)
    return " ".join(text.upper().split())


def canonicalize_country(value: object | None) -> str | None:
    """Map common country aliases to ISO-style codes used by ShipmentRequest."""
    if _is_blank(value):
        return None

    text = str(value).strip()
    compact = _normalize_key(text)
    if compact in _US_COUNTRY_ALIASES:
        return _US_COUNTRY_ALIASES[compact]

    upper = text.upper()
    if len(upper) == 2 and upper.isalpha():
        return upper

    return upper


def is_us_country(value: object | None) -> bool:
    return canonicalize_country(value) == US_COUNTRY_CODE


def is_us_state_or_territory(value: object | None) -> bool:
    if _is_blank(value):
        return False
    key = _normalize_key(value)
    if key in _US_STATE_NAMES:
        return True
    return key in _US_STATE_CODES


def is_canadian_province(value: object | None) -> bool:
    if _is_blank(value):
        return False
    return _normalize_key(value) in _CANADIAN_PROVINCES


def infer_us_country_from_state(state: object | None) -> str | None:
    """Infer US only from a recognized state/province field, never from city."""
    if _is_blank(state) or is_canadian_province(state):
        return None
    if is_us_state_or_territory(state):
        return US_COUNTRY_CODE
    return None


def normalize_location(location: dict | None) -> dict | None:
    """Normalize country aliases and infer US from state when country is absent."""
    if location is None:
        return None

    normalized = dict(location)
    explicit_country = normalized.get("country")

    if not _is_blank(explicit_country):
        normalized["country"] = canonicalize_country(explicit_country)
        return normalized

    inferred = infer_us_country_from_state(normalized.get("state"))
    if inferred is not None:
        normalized["country"] = inferred

    return normalized


def normalize_partial_shipment(data: PartialShipmentData) -> PartialShipmentData:
    """Apply deterministic location normalization to partial shipment data."""
    payload = data.model_dump()
    payload["origin"] = normalize_location(payload.get("origin"))
    payload["destination"] = normalize_location(payload.get("destination"))
    return PartialShipmentData.model_validate(payload)
