"""Deterministic validation and merging for partial shipment data."""

from __future__ import annotations

from shared_models import Item, ShipmentRequest

from orchestrator.location_normalization import (
    US_COUNTRY_CODE,
    canonicalize_country,
    is_us_country,
)
from orchestrator.models import PartialShipmentData

REQUIRED_LOCATION_FIELDS = ("country", "state", "city")

_FIELD_LABELS: dict[str, str] = {
    "total_weight_kg": "total weight in kilograms",
    "total_volume_cbm": "total volume in cubic meters",
    "declared_value_usd": "declared value in USD",
    "timeframe": "whether SPEED or COST is preferred",
    "origin": "origin location",
    "destination": "destination location",
    "items": "shipment items",
    "origin.country": "origin country",
    "origin.state": "origin state or province",
    "origin.city": "origin city",
    "destination.country": "destination country",
    "destination.state": "destination state",
    "destination.city": "destination city",
}


def _is_blank(value: object | None) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _merge_location(current: dict | None, incoming: dict | None) -> dict | None:
    if incoming is None:
        return dict(current) if current else None
    merged: dict = dict(current) if current else {}
    for key, value in incoming.items():
        if value is not None and not (isinstance(value, str) and not value.strip()):
            merged[key] = value
    return merged or None


def merge_partial_data(
    current: PartialShipmentData,
    incoming: PartialShipmentData,
) -> PartialShipmentData:
    """Merge incoming partial data into current data without mutating inputs."""
    merged_origin = _merge_location(current.origin, incoming.origin)
    merged_destination = _merge_location(current.destination, incoming.destination)
    merged_items = incoming.items if incoming.items is not None else current.items

    data: dict = {}
    for field_name in PartialShipmentData.model_fields:
        if field_name == "origin":
            data["origin"] = merged_origin
        elif field_name == "destination":
            data["destination"] = merged_destination
        elif field_name == "items":
            data["items"] = merged_items
        else:
            incoming_value = getattr(incoming, field_name)
            current_value = getattr(current, field_name)
            data[field_name] = (
                incoming_value if incoming_value is not None else current_value
            )

    return PartialShipmentData.model_validate(data)


def get_missing_fields(data: PartialShipmentData) -> list[str]:
    """Return internal field identifiers for all missing required information."""
    missing: list[str] = []

    if data.origin is None:
        missing.append("origin")
    else:
        for field in REQUIRED_LOCATION_FIELDS:
            if _is_blank(data.origin.get(field)):
                missing.append(f"origin.{field}")

    if data.destination is None:
        missing.append("destination")
    else:
        for field in REQUIRED_LOCATION_FIELDS:
            if _is_blank(data.destination.get(field)):
                missing.append(f"destination.{field}")

    if data.items is None or len(data.items) == 0:
        missing.append("items")
    else:
        for index, item in enumerate(data.items):
            if _is_blank(item.name):
                missing.append(f"items[{index}].name")
            if _is_blank(item.category):
                missing.append(f"items[{index}].category")
            if item.quantity is None:
                missing.append(f"items[{index}].quantity")

    if data.total_weight_kg is None:
        missing.append("total_weight_kg")
    if data.total_volume_cbm is None:
        missing.append("total_volume_cbm")
    if data.timeframe is None:
        missing.append("timeframe")
    if data.declared_value_usd is None:
        missing.append("declared_value_usd")

    return missing


def validate_business_rules(data: PartialShipmentData) -> list[str]:
    """Return readable validation errors without raising exceptions."""
    errors: list[str] = []

    if data.destination:
        dest_country = data.destination.get("country")
        if not _is_blank(dest_country) and not is_us_country(dest_country):
            errors.append(
                "The explicit destination country is outside the United States."
            )

    if data.origin and data.origin.get("country"):
        origin_country = canonicalize_country(data.origin.get("country"))
        if origin_country == US_COUNTRY_CODE:
            errors.append("The origin country cannot be the United States.")

    if data.total_weight_kg is not None and data.total_weight_kg <= 0:
        errors.append("Total weight must be greater than zero.")

    if data.total_volume_cbm is not None and data.total_volume_cbm <= 0:
        errors.append("Total volume must be greater than zero.")

    if data.declared_value_usd is not None and data.declared_value_usd <= 0:
        errors.append("Declared value must be greater than zero.")

    if data.items:
        for index, item in enumerate(data.items, start=1):
            if item.quantity is not None and item.quantity <= 0:
                errors.append(f"Item {index} quantity must be greater than zero.")
            if item.name is not None and not item.name.strip():
                errors.append(f"Item {index} name cannot be blank.")
            if item.category is not None and not item.category.strip():
                errors.append(f"Item {index} category cannot be blank.")

    if data.origin and data.destination:
        origin_complete = all(
            not _is_blank(data.origin.get(field)) for field in REQUIRED_LOCATION_FIELDS
        )
        dest_complete = all(
            not _is_blank(data.destination.get(field))
            for field in REQUIRED_LOCATION_FIELDS
        )
        if origin_complete and dest_complete:
            same = all(
                str(data.origin.get(field, "")).strip().lower()
                == str(data.destination.get(field, "")).strip().lower()
                for field in REQUIRED_LOCATION_FIELDS
            )
            if same:
                errors.append(
                    "Origin and destination cannot be the same city, state, and country."
                )

    return errors


def build_shipment_request(data: PartialShipmentData) -> ShipmentRequest:
    """Build a validated ShipmentRequest or raise ValueError with all problems."""
    missing = get_missing_fields(data)
    validation_errors = validate_business_rules(data)
    problems = missing + validation_errors
    if problems:
        raise ValueError("; ".join(problems))

    assert data.origin is not None
    assert data.destination is not None
    assert data.items is not None
    assert data.timeframe is not None
    assert data.total_weight_kg is not None
    assert data.total_volume_cbm is not None
    assert data.declared_value_usd is not None

    origin = {
        **data.origin,
        "country": canonicalize_country(data.origin["country"]),
    }
    destination = {
        **data.destination,
        "country": canonicalize_country(data.destination["country"]),
    }

    items = [
        Item(name=item.name, quantity=item.quantity, category=item.category)
        for item in data.items
    ]

    return ShipmentRequest(
        origin=origin,
        destination=destination,
        items=items,
        total_weight_kg=data.total_weight_kg,
        total_volume_cbm=data.total_volume_cbm,
        timeframe=data.timeframe,
        declared_value_usd=data.declared_value_usd,
    )


def _label_for_field(field: str) -> str:
    if field in _FIELD_LABELS:
        return _FIELD_LABELS[field]
    if field.startswith("items[") and field.endswith(".name"):
        index = field.split("[")[1].split("]")[0]
        return f"product name for item {int(index) + 1}"
    if field.startswith("items[") and field.endswith(".quantity"):
        index = field.split("[")[1].split("]")[0]
        return f"quantity for item {int(index) + 1}"
    if field.startswith("items[") and field.endswith(".category"):
        index = field.split("[")[1].split("]")[0]
        return f"category for item {int(index) + 1}"
    return field.replace("_", " ")


def make_follow_up_question(
    missing_fields: list[str],
    validation_errors: list[str] | None = None,
) -> str:
    """Produce a concise, friendly message asking for missing or invalid information."""
    parts: list[str] = []

    if validation_errors:
        parts.append(" ".join(validation_errors))

    if missing_fields:
        labels = [_label_for_field(field) for field in missing_fields]
        unique_labels: list[str] = []
        for label in labels:
            if label not in unique_labels:
                unique_labels.append(label)
        parts.append(
            "To continue, please provide: "
            + ", ".join(unique_labels)
            + "."
        )

    if not parts:
        return "Please share your shipment details so I can help."

    return " ".join(parts)
