"""Stripe embedded Checkout for the AeroFreight settlement/document package."""

from __future__ import annotations

import os
import time

try:
    import stripe
except ImportError:  # pragma: no cover
    stripe = None

from orchestrator.payment_trace import (
    payment_trace,
    safe_stripe_error_message,
    summarize_checkout,
)


_DEFAULT_RETURN_URL = "https://agentverse.ai"


def _resolve_return_url() -> str:
    """Prefer STRIPE_RETURN_URL, then legacy STRIPE_SUCCESS_URL, then default."""
    preferred = (os.getenv("STRIPE_RETURN_URL") or "").strip()
    if preferred:
        return preferred.rstrip("/")
    legacy = (os.getenv("STRIPE_SUCCESS_URL") or "").strip()
    if legacy:
        return legacy.rstrip("/")
    return _DEFAULT_RETURN_URL


def _cfg() -> dict:
    return {
        "secret_key": (os.getenv("STRIPE_SECRET_KEY", "") or "").strip(),
        "publishable_key": (os.getenv("STRIPE_PUBLISHABLE_KEY", "") or "").strip(),
        "currency": (os.getenv("STRIPE_CURRENCY", "usd") or "usd").lower().strip(),
        "return_url": _resolve_return_url(),
        "expires_seconds": int(
            os.getenv("STRIPE_CHECKOUT_EXPIRES_SECONDS", "1800") or 1800
        ),
    }


def is_configured() -> bool:
    config = _cfg()
    return bool(stripe and config["secret_key"] and config["publishable_key"])


def _client():
    if not stripe:
        return None
    stripe.api_key = _cfg()["secret_key"]
    return stripe


def _expires_at(seconds: int) -> int:
    seconds = max(1800, min(24 * 3600, seconds))
    return int(time.time()) + seconds


def create_settlement_checkout(
    *,
    user_address: str,
    session_id: str,
    amount_usd: float,
    description: str,
) -> dict | None:
    """Create a Stripe Checkout Session or return None when unavailable."""
    if not is_configured():
        return None
    client = _client()
    if not client:
        return None
    config = _cfg()
    amount_cents = int(round(amount_usd * 100))
    payment_trace(
        None,
        "stripe.create.start",
        session_id=session_id,
        amount_cents=amount_cents,
        currency=config["currency"],
    )
    try:
        return_url = (
            f"{config['return_url']}?session_id={{CHECKOUT_SESSION_ID}}"
            f"&aerofreight_session={session_id}&user={user_address}"
        )
        session = client.checkout.Session.create(
            ui_mode="embedded_page",
            redirect_on_completion="if_required",
            payment_method_types=["card"],
            mode="payment",
            return_url=return_url,
            expires_at=_expires_at(config["expires_seconds"]),
            line_items=[
                {
                    "price_data": {
                        "currency": config["currency"],
                        "product_data": {
                            "name": (
                                "AeroFreight route optimization + "
                                "compliance document package"
                            ),
                            "description": description,
                        },
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "user_address": user_address,
                "session_id": session_id,
                "service": "aerofreight_settlement_package",
            },
        )
        # Stripe API uses ui_mode="embedded_page"; Fetch/ASI payment renderers expect
        # ui_mode="embedded" in protocol metadata. Both id and checkout_session_id
        # are included for agent-tooling compatibility.
        checkout = {
            "client_secret": session.client_secret,
            "id": session.id,
            "checkout_session_id": session.id,
            "publishable_key": config["publishable_key"],
            "currency": config["currency"],
            "amount_cents": amount_cents,
            "ui_mode": "embedded",
        }
        summary = summarize_checkout(checkout)
        payment_trace(
            None,
            "stripe.create.success",
            session_id=session_id,
            stripe_session_id=session.id,
            ui_mode_sent_to_stripe="embedded_page",
            checkout_metadata_ui_mode=summary["ui_mode"],
            checkout_key_names=summary["checkout_key_names"],
            has_client_secret=summary["has_client_secret"],
            has_id=summary["has_id"],
            has_checkout_session_id=summary["has_checkout_session_id"],
            id_aliases_match=summary["id_aliases_match"],
            currency=summary["currency"],
            amount_cents=summary["amount_cents"],
        )
        return checkout
    except Exception as exc:
        payment_trace(
            None,
            "stripe.create.failure",
            session_id=session_id,
            exception_class=type(exc).__name__,
            error_message=safe_stripe_error_message(exc),
        )
        return None


def resolve_checkout_session_id(transaction_ref: str) -> str:
    """Map a PaymentIntent id back to a Checkout Session id when needed."""
    ref = (transaction_ref or "").strip()
    if not ref or not is_configured() or ref.startswith("cs_"):
        return ref
    if not ref.startswith("pi_"):
        return ref
    client = _client()
    if not client:
        return ref
    try:
        sessions = client.checkout.Session.list(payment_intent=ref, limit=1)
        if sessions.data:
            return sessions.data[0].id
    except Exception:
        pass
    return ref


def verify_checkout_paid(checkout_session_id: str) -> bool:
    """Verify payment status directly with Stripe."""
    if not is_configured():
        return False
    client = _client()
    if not client:
        return False
    try:
        session = client.checkout.Session.retrieve(checkout_session_id)
        return getattr(session, "payment_status", None) == "paid"
    except Exception:
        return False
