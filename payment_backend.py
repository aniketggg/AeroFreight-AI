"""
Stripe embedded Checkout for the AeroFreight settlement/document package.

This module sells a SERVICE -- route optimization plus a filled compliance
document package -- and never custodies the shipment's freight value or any
government tax/duty. Entry tax is reported to the user as a line item to be
remitted by their customs broker; it is never paid by this agent.

Reads env vars at call time (not import time) so it behaves correctly whether
dotenv is loaded before or after this module is imported.
"""

from __future__ import annotations

import os
import time
import traceback

try:
    import stripe
except ImportError:  # pragma: no cover - allows the agent to boot without the package
    stripe = None


def _cfg() -> dict:
    return {
        "secret_key": (os.getenv("STRIPE_SECRET_KEY", "") or "").strip(),
        "publishable_key": (os.getenv("STRIPE_PUBLISHABLE_KEY", "") or "").strip(),
        "currency": (os.getenv("STRIPE_CURRENCY", "usd") or "usd").lower().strip(),
        "success_url": (
            os.getenv("STRIPE_SUCCESS_URL", "https://agentverse.ai")
            or "https://agentverse.ai"
        ).rstrip("/"),
        "expires_seconds": int(os.getenv("STRIPE_CHECKOUT_EXPIRES_SECONDS", "1800") or 1800),
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(stripe and c["secret_key"] and c["publishable_key"])


def _client():
    if not stripe:
        return None
    stripe.api_key = _cfg()["secret_key"]
    return stripe


def _expires_at(seconds: int) -> int:
    seconds = max(1800, min(24 * 3600, seconds))  # Stripe requires >= 30 minutes
    return int(time.time()) + seconds


def create_settlement_checkout(
    *,
    user_address: str,
    session_id: str,
    amount_usd: float,
    description: str,
) -> dict | None:
    """
    Create a Stripe Checkout Session selling the route-optimization + document
    package for one shipment. Returns the fields a RequestPayment message
    needs, or None if Stripe isn't configured or the call fails.
    """
    if not is_configured():
        return None
    s = _client()
    if not s:
        return None
    c = _cfg()
    amount_cents = int(round(amount_usd * 100))
    try:
        return_url = (
            f"{c['success_url']}?session_id={{CHECKOUT_SESSION_ID}}"
            f"&aerofreight_session={session_id}&user={user_address}"
        )
        session = s.checkout.Session.create(
            ui_mode="embedded_page",
            redirect_on_completion="if_required",
            payment_method_types=["card"],
            mode="payment",
            return_url=return_url,
            expires_at=_expires_at(c["expires_seconds"]),
            line_items=[
                {
                    "price_data": {
                        "currency": c["currency"],
                        "product_data": {
                            "name": "AeroFreight route optimization + compliance document package",
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
        return {
            "client_secret": session.client_secret,
            "checkout_session_id": session.id,
            "publishable_key": c["publishable_key"],
            "currency": c["currency"],
            "amount_cents": amount_cents,
            "ui_mode": "embedded_page",
        }
    except Exception:
        # Logged (not swallowed) so a misconfigured key/account shows up in the
        # agent's own terminal/log output instead of just failing silently as
        # "Payment setup failed" with no further clue.
        traceback.print_exc()
        return None


def resolve_checkout_session_id(transaction_ref: str) -> str:
    """
    Settlement is keyed by Checkout Session id (``cs_...``). If a client ever
    sends a PaymentIntent id (``pi_...``) instead, map it back to the Checkout
    Session so verification and the pending-settlement lookup stay aligned.
    """
    ref = (transaction_ref or "").strip()
    if not ref or not is_configured() or ref.startswith("cs_"):
        return ref
    if not ref.startswith("pi_"):
        return ref
    s = _client()
    if not s:
        return ref
    try:
        sessions = s.checkout.Session.list(payment_intent=ref, limit=1)
        if sessions.data:
            return sessions.data[0].id
    except Exception:
        pass
    return ref


def verify_checkout_paid(checkout_session_id: str) -> bool:
    """Always verify against Stripe directly -- never trust a client's claim."""
    if not is_configured():
        return False
    s = _client()
    if not s:
        return False
    try:
        session = s.checkout.Session.retrieve(checkout_session_id)
        return getattr(session, "payment_status", None) == "paid"
    except Exception:
        return False