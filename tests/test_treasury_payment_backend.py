"""Tests for Treasury Stripe backend using mocks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import treasury_agent.payment_backend as payment_backend


def _mock_checkout_context(*, return_url: str = "https://agentverse.ai"):
    mock_session = MagicMock()
    mock_session.id = "cs_test_123"
    mock_session.client_secret = "secret_test"

    mock_client = MagicMock()
    mock_client.checkout.Session.create.return_value = mock_session

    patches = (
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
                "return_url": return_url,
                "expires_seconds": 1800,
            },
        ),
    )
    return patches, mock_client, mock_session


def test_create_settlement_checkout_uses_stripe_mock():
    patches, mock_client, _ = _mock_checkout_context()
    with patches[0], patches[1], patches[2], patches[3]:
        result = payment_backend.create_settlement_checkout(
            user_address="agent1quser",
            session_id="session-1",
            amount_usd=12.34,
            description="demo",
        )

    assert result is not None
    assert result["checkout_session_id"] == "cs_test_123"
    assert result["id"] == "cs_test_123"
    assert result["checkout_session_id"] == result["id"]
    assert result["client_secret"] == "secret_test"
    assert result["publishable_key"] == "pk_test"
    assert result["currency"] == "usd"
    assert result["amount_cents"] == 1234
    assert result["ui_mode"] == "embedded"
    assert "secret_key" not in result
    mock_client.checkout.Session.create.assert_called_once()


def test_fetch_checkout_metadata_includes_both_session_id_aliases():
    patches, mock_client, _ = _mock_checkout_context()
    with patches[0], patches[1], patches[2], patches[3]:
        result = payment_backend.create_settlement_checkout(
            user_address="agent1quser",
            session_id="session-1",
            amount_usd=10.0,
            description="demo",
        )

    assert result is not None
    assert "id" in result
    assert "checkout_session_id" in result
    assert result["id"] == result["checkout_session_id"] == "cs_test_123"
    assert result["ui_mode"] == "embedded"
    assert mock_client.checkout.Session.create.call_args.kwargs["ui_mode"] == "embedded_page"


def test_fetch_checkout_metadata_excludes_stripe_secret_key():
    patches, _, _ = _mock_checkout_context()
    with patches[0], patches[1], patches[2], patches[3]:
        result = payment_backend.create_settlement_checkout(
            user_address="agent1quser",
            session_id="session-1",
            amount_usd=10.0,
            description="demo",
        )

    assert result is not None
    assert "secret_key" not in result
    assert "sk_test" not in result.values()


def test_session_create_uses_embedded_page_and_redirect_if_required():
    patches, mock_client, _ = _mock_checkout_context()
    with patches[0], patches[1], patches[2], patches[3]:
        payment_backend.create_settlement_checkout(
            user_address="agent1quser",
            session_id="session-1",
            amount_usd=10.0,
            description="demo",
        )

    kwargs = mock_client.checkout.Session.create.call_args.kwargs
    assert kwargs["ui_mode"] == "embedded_page"
    assert kwargs["redirect_on_completion"] == "if_required"
    assert "success_url" not in kwargs


def test_session_create_return_url_contains_checkout_session_id_placeholder():
    patches, mock_client, _ = _mock_checkout_context(
        return_url="https://example.com/return"
    )
    with patches[0], patches[1], patches[2], patches[3]:
        payment_backend.create_settlement_checkout(
            user_address="agent1quser",
            session_id="session-1",
            amount_usd=10.0,
            description="demo",
        )

    return_url = mock_client.checkout.Session.create.call_args.kwargs["return_url"]
    assert "{CHECKOUT_SESSION_ID}" in return_url
    assert return_url.startswith("https://example.com/return?session_id={CHECKOUT_SESSION_ID}")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {
                "STRIPE_RETURN_URL": "https://return.example",
                "STRIPE_SUCCESS_URL": "https://legacy.example",
            },
            "https://return.example",
        ),
        (
            {"STRIPE_RETURN_URL": "", "STRIPE_SUCCESS_URL": "https://legacy.example"},
            "https://legacy.example",
        ),
        ({}, "https://agentverse.ai"),
    ],
)
def test_resolve_return_url_precedence(monkeypatch: pytest.MonkeyPatch, env, expected):
    for key in ("STRIPE_RETURN_URL", "STRIPE_SUCCESS_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert payment_backend._resolve_return_url() == expected


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


def test_no_real_stripe_module_used_during_create(monkeypatch: pytest.MonkeyPatch):
    """Ensure tests patch Stripe and never call the real SDK."""
    monkeypatch.setattr(payment_backend, "stripe", None)
    assert payment_backend.create_settlement_checkout(
        user_address="agent1quser",
        session_id="session-1",
        amount_usd=1.0,
        description="demo",
    ) is None
