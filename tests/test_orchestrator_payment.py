"""Tests for orchestrator payment wall and commit handling."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from orchestrator.agent import (
    PENDING_PAYMENT_PREFIX,
    _pending_payment_key,
    handle_commit_payment,
    handle_reject_payment,
    process_chat_message,
)
from orchestrator.agent_interfaces import PaymentSetupResult
from orchestrator.remote_agents import RemoteTreasuryError
from orchestrator.models import PartialItem, PartialShipmentData, WorkflowStage
from orchestrator.mock_agents import (
    MockEconomistAgent,
    MockRoutingAgent,
    MockTreasuryAgent,
)
from orchestrator.uagents_storage import ContextSessionStore
from shared_models import SettlementStatus
from uagents_core.contrib.protocols.chat import ChatMessage, TextContent
from uagents_core.contrib.protocols.payment import (
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
)


def _run(coro):
    return asyncio.run(coro)


class FakeStorage:
    def __init__(self) -> None:
        self._data: dict = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def remove(self, key: str) -> None:
        self._data.pop(key, None)


class FakeContext:
    def __init__(self) -> None:
        self.storage = FakeStorage()
        self.logger = MagicMock()
        self.sent: list[tuple[str, object]] = []
        self.agent = SimpleNamespace(address="agent1qorchestrator")

    async def send(self, destination: str, message) -> SimpleNamespace:
        self.sent.append((destination, message))
        return SimpleNamespace(status="delivered")


class FakeExtractor:
    def extract(self, user_message: str, current_data: PartialShipmentData):
        return PartialShipmentData(
            origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
            destination={"country": "US", "state": "TX", "city": "Austin"},
            items=[PartialItem(name="Widget", quantity=5, category="electronics")],
            total_weight_kg=120.0,
            total_volume_cbm=2.0,
            timeframe="SPEED",
            declared_value_usd=4000.0,
        )


COMPAT_STRIPE_CHECKOUT = {
    "client_secret": "secret_test",
    "id": "cs_test_123",
    "checkout_session_id": "cs_test_123",
    "publishable_key": "pk_test",
    "currency": "usd",
    "amount_cents": 500,
    "ui_mode": "embedded",
}


def _assert_stripe_metadata(stripe_meta: dict) -> None:
    """Assert Fetch-compatible checkout fields are present and preserved."""
    assert set(stripe_meta.keys()) == set(COMPAT_STRIPE_CHECKOUT.keys())
    for key in COMPAT_STRIPE_CHECKOUT:
        if key == "amount_cents":
            assert int(stripe_meta[key]) == COMPAT_STRIPE_CHECKOUT[key]
        else:
            assert stripe_meta[key] == COMPAT_STRIPE_CHECKOUT[key]
    assert "secret_key" not in stripe_meta
    assert stripe_meta["id"] == stripe_meta["checkout_session_id"]


class FakeTreasuryPaymentClient:
    def __init__(self, *, fail_finalize: bool = False) -> None:
        self.fail_finalize = fail_finalize
        self.setup_calls = 0
        self.finalize_calls = 0

    async def prepare_payment(self, **kwargs) -> PaymentSetupResult:
        self.setup_calls += 1
        return PaymentSetupResult(
            checkout=dict(COMPAT_STRIPE_CHECKOUT),
            fee_usd=5.0,
        )

    async def finalize_payment(self, **kwargs) -> SettlementStatus:
        self.finalize_calls += 1
        if self.fail_finalize:
            raise RemoteTreasuryError("Payment could not be verified.")
        return SettlementStatus(
            filled_documents={"invoice": {"status": "ready"}},
            final_user_prompt="## AeroFreight AI Shipment Quote\n\nPaid quote.",
            payment_hash="cs_test_123",
        )


def _chat_message(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)],
    )


def _request_payments(ctx: FakeContext) -> list[RequestPayment]:
    return [msg for _, msg in ctx.sent if isinstance(msg, RequestPayment)]


def _advance_to_awaiting_payment(ctx: FakeContext, sender: str) -> None:
    client = FakeTreasuryPaymentClient()
    _run(
        process_chat_message(
            ctx,
            sender,
            _chat_message("ship"),
            FakeExtractor(),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
            client,
        )
    )
    session = ContextSessionStore(ctx.storage).get(sender)
    assert session is not None
    assert session.stage == WorkflowStage.AWAITING_PAYMENT
    assert _request_payments(ctx)


def test_remote_mode_sends_request_payment_from_orchestrator():
    ctx = FakeContext()
    client = FakeTreasuryPaymentClient()
    _run(
        process_chat_message(
            ctx,
            "user-a",
            _chat_message("ship"),
            FakeExtractor(),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
            client,
        )
    )

    session = ContextSessionStore(ctx.storage).get("user-a")
    assert session is not None
    assert session.stage == WorkflowStage.AWAITING_PAYMENT
    assert session.stage != WorkflowStage.AWAITING_CONFIRMATION
    payments = _request_payments(ctx)
    assert len(payments) == 1
    assert payments[0].recipient == "agent1qorchestrator"
    _assert_stripe_metadata(payments[0].metadata["stripe"])
    assert client.setup_calls == 1


def test_request_payment_metadata_forwards_compat_checkout_unchanged():
    ctx = FakeContext()
    client = FakeTreasuryPaymentClient()
    _run(
        process_chat_message(
            ctx,
            "user-a",
            _chat_message("ship"),
            FakeExtractor(),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
            client,
        )
    )

    stripe_meta = _request_payments(ctx)[0].metadata["stripe"]
    _assert_stripe_metadata(stripe_meta)
    assert stripe_meta["ui_mode"] == "embedded"
    assert stripe_meta["id"] == stripe_meta["checkout_session_id"]

    pending = ctx.storage.get(_pending_payment_key("user-a"))
    assert pending["checkout"] == COMPAT_STRIPE_CHECKOUT


def test_remote_mode_does_not_reveal_quote_before_payment():
    ctx = FakeContext()
    _advance_to_awaiting_payment(ctx, "user-a")
    session = ContextSessionStore(ctx.storage).get("user-a")
    assert session is not None
    chat_texts = [
        msg.content[0].text
        for _, msg in ctx.sent
        if isinstance(msg, ChatMessage)
    ]
    combined = "\n".join(chat_texts)
    assert "Suggested mode" not in combined
    assert "Total landed cost" not in combined
    assert session.settlement_status is not None


def test_repeated_message_resends_same_payment_wall_without_new_setup():
    ctx = FakeContext()
    client = FakeTreasuryPaymentClient()
    _run(
        process_chat_message(
            ctx,
            "user-a",
            _chat_message("ship"),
            FakeExtractor(),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
            client,
        )
    )
    _run(
        process_chat_message(
            ctx,
            "user-a",
            _chat_message("hello"),
            FakeExtractor(),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
            client,
        )
    )
    assert client.setup_calls == 1
    payments = _request_payments(ctx)
    assert len(payments) == 2
    assert payments[0].metadata["stripe"] == payments[1].metadata["stripe"]
    _assert_stripe_metadata(payments[0].metadata["stripe"])
    pending = ctx.storage.get(_pending_payment_key("user-a"))
    assert pending["checkout"] == COMPAT_STRIPE_CHECKOUT


def test_commit_payment_completes_session_and_sends_quote():
    ctx = FakeContext()
    client = FakeTreasuryPaymentClient()
    _advance_to_awaiting_payment(ctx, "user-a")
    ctx.storage.set(
        _pending_payment_key("user-a"),
        {
            "session_id": ContextSessionStore(ctx.storage).get("user-a").session_id,
            "checkout_session_id": "cs_test_123",
            "fee_usd": 5.0,
            "checkout": {"checkout_session_id": "cs_test_123"},
        },
    )

    commit = CommitPayment(
        funds=Funds(currency="USD", amount="5.00", payment_method="stripe"),
        recipient="agent1qorchestrator",
        transaction_id="cs_test_123",
    )
    _run(handle_commit_payment(ctx, "user-a", commit, client))

    session = ContextSessionStore(ctx.storage).get("user-a")
    assert session is not None
    assert session.stage == WorkflowStage.COMPLETED
    assert any(isinstance(msg, CompletePayment) for _, msg in ctx.sent)
    assert client.finalize_calls == 1
    final_chat = [
        msg.content[0].text
        for _, msg in ctx.sent
        if isinstance(msg, ChatMessage) and "Shipment Quote" in msg.content[0].text
    ]
    assert final_chat


def test_unpaid_finalize_sends_reject_payment():
    ctx = FakeContext()
    client = FakeTreasuryPaymentClient(fail_finalize=True)
    _advance_to_awaiting_payment(ctx, "user-a")
    ctx.storage.set(
        _pending_payment_key("user-a"),
        {
            "session_id": ContextSessionStore(ctx.storage).get("user-a").session_id,
            "checkout_session_id": "cs_test_123",
            "fee_usd": 5.0,
            "checkout": {"checkout_session_id": "cs_test_123"},
        },
    )

    commit = CommitPayment(
        funds=Funds(currency="USD", amount="5.00", payment_method="stripe"),
        recipient="agent1qorchestrator",
        transaction_id="cs_test_123",
    )
    _run(handle_commit_payment(ctx, "user-a", commit, client))

    session = ContextSessionStore(ctx.storage).get("user-a")
    assert session is not None
    assert session.stage == WorkflowStage.AWAITING_PAYMENT
    assert any(isinstance(msg, RejectPayment) for _, msg in ctx.sent)


def test_reject_payment_marks_failed_without_quote():
    ctx = FakeContext()
    _advance_to_awaiting_payment(ctx, "user-a")
    _run(handle_reject_payment(ctx, "user-a", RejectPayment(reason="cancelled")))

    session = ContextSessionStore(ctx.storage).get("user-a")
    assert session is not None
    assert session.stage == WorkflowStage.FAILED
    assert ctx.storage.get(_pending_payment_key("user-a")) is None


def test_duplicate_commit_on_completed_session_is_idempotent():
    ctx = FakeContext()
    client = FakeTreasuryPaymentClient()
    _advance_to_awaiting_payment(ctx, "user-a")
    pending = {
        "session_id": ContextSessionStore(ctx.storage).get("user-a").session_id,
        "checkout_session_id": "cs_test_123",
        "fee_usd": 5.0,
        "checkout": {"checkout_session_id": "cs_test_123"},
    }
    ctx.storage.set(_pending_payment_key("user-a"), pending)

    commit = CommitPayment(
        funds=Funds(currency="USD", amount="5.00", payment_method="stripe"),
        recipient="agent1qorchestrator",
        transaction_id="cs_test_123",
    )
    _run(handle_commit_payment(ctx, "user-a", commit, client))
    first_finalize_calls = client.finalize_calls
    _run(handle_commit_payment(ctx, "user-a", commit, client))
    assert client.finalize_calls == first_finalize_calls
