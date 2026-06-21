"""Tests for Treasury Stripe backend using mocks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import treasury_agent.payment_backend as payment_backend


def test_create_settlement_checkout_uses_stripe_mock():
    mock_session = MagicMock()
    mock_session.id = "cs_test_123"
    mock_session.client_secret = "secret_test"

    mock_client = MagicMock()
    mock_client.checkout.Session.create.return_value = mock_session

    with (
        patch.object(payment_backend, "stripe", MagicMock()),
        patch.object(payment_backend, "is_configured", return_value=True),
        patch.object(payment_backend, "_client", return_value=mock_client),
        patch.object(
            payment_backend,
            "_cfg",
            return_value={
                "secret_key": "sk_test",
                "publishable_key": "pk_test",
                "currency": "usd",
                "success_url": "https://agentverse.ai",
                "expires_seconds": 1800,
            },
        ),
    ):
        result = payment_backend.create_settlement_checkout(
            user_address="agent1quser",
            session_id="session-1",
            amount_usd=12.34,
            description="demo",
        )

    assert result is not None
    assert result["checkout_session_id"] == "cs_test_123"
    mock_client.checkout.Session.create.assert_called_once()


def test_verify_checkout_paid_rejects_unpaid_session():
    mock_session = MagicMock()
    mock_session.payment_status = "unpaid"
    mock_client = MagicMock()
    mock_client.checkout.Session.retrieve.return_value = mock_session

    with (
        patch.object(payment_backend, "is_configured", return_value=True),
        patch.object(payment_backend, "_client", return_value=mock_client),
    ):
        assert payment_backend.verify_checkout_paid("cs_test_123") is False


def test_verify_checkout_paid_accepts_paid_session():
    mock_session = MagicMock()
    mock_session.payment_status = "paid"
    mock_client = MagicMock()
    mock_client.checkout.Session.retrieve.return_value = mock_session

    with (
        patch.object(payment_backend, "is_configured", return_value=True),
        patch.object(payment_backend, "_client", return_value=mock_client),
    ):
        assert payment_backend.verify_checkout_paid("cs_test_123") is True
