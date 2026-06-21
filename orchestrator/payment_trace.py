"""Opt-in PAYMENT_TRACE diagnostics for the remote Stripe paywall path."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

_TRACE_PREFIX = "PAYMENT_TRACE"
_DEFAULT_LOGGER = logging.getLogger("aerofreight.payment")
_DEBUG_LOG_PATH = "/Users/aniketgupta/Documents/AeroFreightAi/.cursor/debug-37aa6c.log"
_DEBUG_SESSION_ID = "37aa6c"
_SECRET_PATTERNS = (
    re.compile(r"sk_(live|test)_[A-Za-z0-9]+"),
    re.compile(r"whsec_[A-Za-z0-9]+"),
    re.compile(r"cs_(live|test)_[a-zA-Z0-9]+"),
    re.compile(r"pi_(live|test)_[a-zA-Z0-9]+"),
)

REQUIRED_CHECKOUT_KEYS = frozenset(
    {
        "client_secret",
        "id",
        "checkout_session_id",
        "publishable_key",
        "currency",
        "amount_cents",
        "ui_mode",
    }
)

PLACEHOLDER_CHECKOUT = {
    "client_secret": "secret_placeholder",
    "id": "cs_test_placeholder",
    "checkout_session_id": "cs_test_placeholder",
    "publishable_key": "pk_test_placeholder",
    "currency": "usd",
    "amount_cents": 500,
    "ui_mode": "embedded",
}

_PAYMENT_PROTOCOL_DIGEST: str | None = None


async def _noop_payment_handler(*_args, **_kwargs) -> None:
    return None


def get_payment_protocol_digest() -> str:
    """Return the published AgentPaymentProtocol digest for outbound RequestPayment."""
    global _PAYMENT_PROTOCOL_DIGEST
    if _PAYMENT_PROTOCOL_DIGEST is None:
        from treasury_agent.payment_protocol import build_payment_protocol

        proto = build_payment_protocol(_noop_payment_handler, _noop_payment_handler)
        _PAYMENT_PROTOCOL_DIGEST = proto.digest
    return _PAYMENT_PROTOCOL_DIGEST


def is_payment_debug_enabled() -> bool:
    """Return True when AEROFREIGHT_PAYMENT_DEBUG is truthy."""
    return os.getenv("AEROFREIGHT_PAYMENT_DEBUG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def redact_sensitive_text(text: str) -> str:
    """Remove credential-like substrings from free-form log text."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("<redacted>", redacted)
    return redacted


def redact_request_payment_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a RequestPayment dump with Stripe secrets redacted."""
    redacted = json.loads(json.dumps(payload, default=str))
    metadata = redacted.get("metadata")
    if isinstance(metadata, dict):
        stripe = metadata.get("stripe")
        if isinstance(stripe, dict):
            if stripe.get("client_secret"):
                stripe["client_secret"] = "<redacted>"
            if stripe.get("publishable_key"):
                stripe["publishable_key"] = "<redacted>"
    return redacted


def payment_trace(
    logger: Any | None,
    event: str,
    *,
    session_id: str | None = None,
    **fields: Any,
) -> None:
    """Emit one grep-friendly PAYMENT_TRACE line when debugging is enabled."""
    if not is_payment_debug_enabled():
        return

    parts = [f"{_TRACE_PREFIX} {event}"]
    if session_id:
        parts.append(f"session_id={session_id}")
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = redact_sensitive_text(value)
        parts.append(f"{key}={value}")
    message = " ".join(parts)

    if logger is not None and hasattr(logger, "info"):
        logger.info(message)
    else:
        _DEFAULT_LOGGER.info(message)


def debug_ndjson_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    run_id: str = "pre-fix",
    always: bool = False,
) -> None:
    """Append one NDJSON debug line for debug-mode analysis (no secrets)."""
    if not always and not is_payment_debug_enabled():
        return
    payload = {
        "sessionId": _DEBUG_SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    try:
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
    except OSError:
        pass


def summarize_checkout(checkout: dict[str, Any] | None) -> dict[str, Any]:
    """Return a safe structural summary of Stripe checkout metadata."""
    if not checkout:
        return {
            "checkout_key_names": [],
            "ui_mode": None,
            "has_client_secret": False,
            "has_id": False,
            "has_checkout_session_id": False,
            "id_aliases_match": False,
            "currency": None,
            "amount_cents": None,
            "amount_cents_type": None,
            "has_all_required_keys": False,
        }

    amount_cents = checkout.get("amount_cents")
    key_names = sorted(checkout.keys())
    return {
        "checkout_key_names": key_names,
        "ui_mode": checkout.get("ui_mode"),
        "has_client_secret": bool(checkout.get("client_secret")),
        "has_id": bool(checkout.get("id")),
        "has_checkout_session_id": bool(checkout.get("checkout_session_id")),
        "id_aliases_match": checkout.get("id") == checkout.get("checkout_session_id"),
        "currency": checkout.get("currency"),
        "amount_cents": amount_cents,
        "amount_cents_type": type(amount_cents).__name__,
        "has_all_required_keys": REQUIRED_CHECKOUT_KEYS.issubset(set(key_names)),
    }


def summarize_request_payment_dump(dumped: dict[str, Any]) -> dict[str, Any]:
    """Return a safe structural summary of a RequestPayment model_dump()."""
    metadata = dumped.get("metadata")
    stripe = metadata.get("stripe") if isinstance(metadata, dict) else None
    accepted = dumped.get("accepted_funds") or []
    first_fund = accepted[0] if accepted else {}

    stripe_summary = summarize_checkout(stripe if isinstance(stripe, dict) else None)
    return {
        "top_level_keys": sorted(dumped.keys()),
        "metadata_type": type(metadata).__name__,
        "metadata_keys": sorted(metadata.keys()) if isinstance(metadata, dict) else [],
        "stripe_metadata_type": type(stripe).__name__,
        "stripe_metadata_keys": (
            sorted(stripe.keys()) if isinstance(stripe, dict) else []
        ),
        "stripe_ui_mode": stripe.get("ui_mode") if isinstance(stripe, dict) else None,
        "has_client_secret": stripe_summary["has_client_secret"],
        "has_publishable_key": bool(
            isinstance(stripe, dict) and stripe.get("publishable_key")
        ),
        "has_id": stripe_summary["has_id"],
        "has_checkout_session_id": stripe_summary["has_checkout_session_id"],
        "id_aliases_match": stripe_summary["id_aliases_match"],
        "amount_cents_python_type": stripe_summary["amount_cents_type"],
        "accepted_funds_count": len(accepted),
        "payment_method": first_fund.get("payment_method"),
        "currency": first_fund.get("currency"),
        "amount": first_fund.get("amount"),
        "amount_python_type": type(first_fund.get("amount")).__name__,
        "recipient": dumped.get("recipient"),
        "reference": dumped.get("reference"),
        "metadata_stripe_is_dict": isinstance(stripe, dict),
        "has_all_required_stripe_keys": stripe_summary["has_all_required_keys"],
    }


def normalize_fetch_checkout_metadata(
    checkout: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Ensure Fetch/ASI-compatible Stripe metadata before RequestPayment validation."""
    changes: dict[str, Any] = {"changed": False}
    if not checkout:
        return None, changes

    normalized = dict(checkout)
    session_id = normalized.get("checkout_session_id") or normalized.get("id")
    if session_id:
        if normalized.get("id") != session_id:
            normalized["id"] = session_id
            changes["changed"] = True
            changes["set_id"] = True
        if normalized.get("checkout_session_id") != session_id:
            normalized["checkout_session_id"] = session_id
            changes["changed"] = True
            changes["set_checkout_session_id"] = True

    ui_mode = str(normalized.get("ui_mode") or "").strip().lower()
    if ui_mode != "embedded":
        normalized["ui_mode"] = "embedded"
        changes["changed"] = True
        changes["ui_mode_from"] = ui_mode or None

    amount_cents = normalized.get("amount_cents")
    if amount_cents is not None:
        coerced = int(amount_cents)
        if normalized.get("amount_cents") != coerced:
            normalized["amount_cents"] = coerced
            changes["changed"] = True
            changes["amount_cents_coerced"] = True

    return normalized, changes


def summarize_send_result(result: Any) -> dict[str, Any]:
    """Inspect a ctx.send() return object without assuming a fixed schema."""
    if result is None:
        return {"result_class": "NoneType"}

    summary: dict[str, Any] = {"result_class": type(result).__name__}
    for attr in (
        "status",
        "detail",
        "destination",
        "endpoint",
        "message",
        "delivery_status",
        "success",
        "error",
    ):
        if hasattr(result, attr):
            value = getattr(result, attr)
            if value is None:
                continue
            if hasattr(value, "value"):
                value = value.value
            summary[attr] = redact_sensitive_text(str(value))
    return summary


def is_send_failure(result: Any) -> bool:
    """Return True when a ctx.send() result clearly indicates delivery failure."""
    if result is None:
        return False

    if hasattr(result, "success") and getattr(result, "success") is False:
        return True

    status = getattr(result, "status", None)
    if status is not None:
        status_value = status.value if hasattr(status, "value") else str(status)
        status_text = str(status_value).strip().lower()
        if status_text == "failed":
            return True
        if status_text in {"delivered", "sent"}:
            return False

    for attr in ("delivery_status", "detail", "error"):
        if not hasattr(result, attr):
            continue
        value = getattr(result, attr)
        if hasattr(value, "value"):
            value = value.value
        value = str(value).strip().lower()
        if not value:
            continue
        if value in {"failed", "failure", "error", "rejected", "undelivered"}:
            return True
        if "fail" in value and value not in {"delivered", "success", "ok", "sent"}:
            return True
    return False


def safe_stripe_error_message(exc: BaseException) -> str:
    """Return a safe Stripe exception message for debug traces."""
    message = redact_sensitive_text(str(exc)).strip()
    if not message:
        return type(exc).__name__
    return message[:240]


def compare_request_payment_dumps(
    orchestrator_dump: dict[str, Any],
    treasury_dump: dict[str, Any],
) -> dict[str, Any]:
    """Compare post-validation RequestPayment dumps for structural differences."""
    ignored_paths = {
        ("recipient",),
        ("reference",),
        ("description",),
        ("metadata", "service"),
        ("accepted_funds", 0, "amount"),
    }

    def _walk(path: tuple, left: Any, right: Any, diffs: list[dict[str, Any]]) -> None:
        if path in ignored_paths:
            return
        if type(left) is not type(right):
            diffs.append(
                {
                    "path": ".".join(str(part) for part in path) or "<root>",
                    "orchestrator_type": type(left).__name__,
                    "treasury_type": type(right).__name__,
                }
            )
            return
        if isinstance(left, dict):
            left_keys = set(left.keys())
            right_keys = set(right.keys())
            if left_keys != right_keys:
                diffs.append(
                    {
                        "path": ".".join(str(part) for part in path) or "<root>",
                        "orchestrator_keys": sorted(left_keys),
                        "treasury_keys": sorted(right_keys),
                    }
                )
            for key in sorted(left_keys | right_keys):
                _walk(path + (key,), left.get(key), right.get(key), diffs)
            return
        if isinstance(left, list):
            if len(left) != len(right):
                diffs.append(
                    {
                        "path": ".".join(str(part) for part in path),
                        "orchestrator_len": len(left),
                        "treasury_len": len(right),
                    }
                )
            for index, (left_item, right_item) in enumerate(zip(left, right)):
                _walk(path + (index,), left_item, right_item, diffs)
            return
        if left != right:
            diffs.append(
                {
                    "path": ".".join(str(part) for part in path),
                    "orchestrator_value": left,
                    "treasury_value": right,
                }
            )

    differences: list[dict[str, Any]] = []
    _walk((), orchestrator_dump, treasury_dump, differences)
    return {
        "difference_count": len(differences),
        "differences": differences,
        "orchestrator_summary": summarize_request_payment_dump(orchestrator_dump),
        "treasury_summary": summarize_request_payment_dump(treasury_dump),
    }


def build_orchestrator_request_payment(
    *,
    recipient: str,
    session_id: str,
    fee_usd: float,
    checkout: dict[str, Any],
):
    """Build the orchestrator RequestPayment message (shared for tests/diagnostic)."""
    from uagents_core.contrib.protocols.payment import Funds, RequestPayment

    normalized_checkout, _changes = normalize_fetch_checkout_metadata(checkout)
    assert normalized_checkout is not None

    return RequestPayment(
        accepted_funds=[
            Funds(
                currency="USD",
                amount=f"{fee_usd:.2f}",
                payment_method="stripe",
            )
        ],
        recipient=recipient,
        deadline_seconds=1800,
        reference=session_id,
        description="Pay to unlock your AeroFreight shipment quote and invoice.",
        metadata={
            "stripe": normalized_checkout,
            "service": "aerofreight_shipment_quote",
        },
    )


def build_treasury_request_payment(
    *,
    recipient: str,
    session_id: str,
    fee_usd: float,
    checkout: dict[str, Any],
):
    """Build the standalone Treasury RequestPayment message (shared for tests/diagnostic)."""
    from uagents_core.contrib.protocols.payment import Funds, RequestPayment

    return RequestPayment(
        accepted_funds=[
            Funds(
                currency="USD",
                amount=f"{fee_usd:.2f}",
                payment_method="stripe",
            )
        ],
        recipient=recipient,
        deadline_seconds=1800,
        reference=session_id,
        description=(
            f"Pay ${fee_usd:.2f} to receive your "
            "AeroFreight document package."
        ),
        metadata={
            "stripe": checkout,
            "service": "aerofreight_settlement_package",
        },
    )


def log_payment_protocol_registration(logger: Any | None, payment_proto: Any) -> None:
    """Log seller payment-protocol registration details at orchestrator startup."""
    if not is_payment_debug_enabled():
        return

    incoming_models = sorted(
        getattr(model, "__name__", str(model))
        for model in getattr(payment_proto, "models", {}).values()
    )
    payment_trace(
        logger,
        "orchestrator.payment_protocol.registered",
        protocol_name=getattr(getattr(payment_proto, "spec", None), "name", None),
        protocol_version=getattr(getattr(payment_proto, "spec", None), "version", None),
        protocol_role="seller",
        manifest_digest=getattr(payment_proto, "digest", None),
        registered_incoming_models=incoming_models,
        registered_outgoing_models=["RequestPayment"],
    )
