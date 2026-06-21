"""Tests for AeroFreight uAgent chat behavior."""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from orchestrator.agent import (
    SAFE_PROCESSING_ERROR,
    extract_text_content,
    process_chat_message,
    strip_leading_agent_mention,
)
from orchestrator.models import PartialItem, PartialShipmentData, WorkflowStage
from orchestrator.mock_agents import (
    MockEconomistAgent,
    MockRoutingAgent,
    MockTreasuryAgent,
)
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    TextContent,
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

    async def send(self, destination: str, message) -> SimpleNamespace:
        self.sent.append((destination, message))
        return SimpleNamespace(status="delivered")


class FakeExtractor:
    def __init__(
        self,
        responses: list[PartialShipmentData] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.responses = list(responses or [])
        self.fail = fail
        self.calls: list[tuple[str, PartialShipmentData]] = []

    def extract(
        self,
        user_message: str,
        current_data: PartialShipmentData,
        conversation_history=None,
    ) -> PartialShipmentData:
        self.calls.append((user_message, current_data))
        if self.fail:
            raise RuntimeError("extractor exploded with sk-secret-key")
        if self.responses:
            return self.responses.pop(0)
        return PartialShipmentData()


def _complete_partial() -> PartialShipmentData:
    return PartialShipmentData(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[PartialItem(name="Widget", quantity=5, category="electronics")],
        total_weight_kg=120.0,
        total_volume_cbm=2.0,
        timeframe="SPEED",
        declared_value_usd=4000.0,
    )


def _chat_message(*text_blocks: str, msg_id=None) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=msg_id or uuid4(),
        content=[TextContent(type="text", text=text) for text in text_blocks],
    )


def test_one_incoming_text_block_is_extracted():
    msg = _chat_message("Ship widgets")
    assert extract_text_content(msg) == "Ship widgets"


def test_multiple_text_blocks_are_joined():
    msg = _chat_message("Ship widgets", "from Shenzhen")
    assert extract_text_content(msg) == "Ship widgets\nfrom Shenzhen"


def test_unsupported_content_is_ignored():
    msg = ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[
            MetadataContent(type="metadata", metadata={"role": "system"}),
            TextContent(type="text", text="Ship widgets"),
        ],
    )
    assert extract_text_content(msg) == "Ship widgets"


def test_empty_text_returns_friendly_response_without_extractor():
    ctx = FakeContext()
    extractor = FakeExtractor()
    msg = ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[MetadataContent(type="metadata", metadata={"role": "system"})],
    )
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            msg,
            extractor,
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    assert extractor.calls == []
    response = ctx.sent[-1][1]
    assert isinstance(response, ChatMessage)
    assert "text message" in response.content[0].text.lower()


def test_acknowledgement_sent_before_response():
    ctx = FakeContext()
    msg_id = uuid4()
    msg = _chat_message("hello", msg_id=msg_id)
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            msg,
            FakeExtractor([_complete_partial()]),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    assert isinstance(ctx.sent[0][1], ChatAcknowledgement)
    assert isinstance(ctx.sent[1][1], ChatMessage)


def test_acknowledgement_references_incoming_msg_id():
    ctx = FakeContext()
    msg_id = uuid4()
    msg = _chat_message("hello", msg_id=msg_id)
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            msg,
            FakeExtractor([_complete_partial()]),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    ack = ctx.sent[0][1]
    assert ack.acknowledged_msg_id == msg_id


def test_response_is_valid_chat_message_with_text_content():
    ctx = FakeContext()
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("ship"),
            FakeExtractor([_complete_partial()]),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    response = ctx.sent[-1][1]
    assert isinstance(response, ChatMessage)
    assert isinstance(response.content[0], TextContent)


def test_response_preserves_markdown():
    ctx = FakeContext()
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("ship"),
            FakeExtractor([_complete_partial()]),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    response_text = ctx.sent[-1][1].content[0].text
    assert "## AeroFreight AI Shipment Quote" in response_text


def test_response_does_not_contain_end_session_content():
    ctx = FakeContext()
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("ship"),
            FakeExtractor([_complete_partial()]),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    response = ctx.sent[-1][1]
    for block in response.content:
        assert getattr(block, "type", None) != "end-session"


def test_complete_shipment_reaches_awaiting_confirmation():
    ctx = FakeContext()
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("ship complete"),
            FakeExtractor([_complete_partial()]),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    from orchestrator.uagents_storage import ContextSessionStore

    session = ContextSessionStore(ctx.storage).get("sender-a")
    assert session is not None
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION


def test_follow_up_message_uses_persisted_partial_data():
    ctx = FakeContext()
    extractor = FakeExtractor(
        [
            PartialShipmentData(
                origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
                destination={"country": "US", "state": "TX", "city": "Austin"},
            ),
            PartialShipmentData(
                items=[PartialItem(name="Widget", quantity=5, category="electronics")],
                total_weight_kg=120.0,
                total_volume_cbm=2.0,
                timeframe="SPEED",
                declared_value_usd=4000.0,
            ),
        ]
    )
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("from Shenzhen to Austin"),
            extractor,
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("5 widgets, 120kg, 2 cbm, speed, $4000"),
            extractor,
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    assert extractor.calls[1][1].origin["city"] == "Shenzhen"


def test_confirm_completes_simulated_payment():
    ctx = FakeContext()
    extractor = FakeExtractor([_complete_partial()])
    agents = (
        MockEconomistAgent(),
        MockRoutingAgent(),
        MockTreasuryAgent(),
    )
    _run(process_chat_message(ctx, "sender-a", _chat_message("ship"), extractor, *agents))
    _run(
        process_chat_message(
            ctx, "sender-a", _chat_message("CONFIRM"), extractor, *agents
        )
    )
    from orchestrator.uagents_storage import ContextSessionStore

    session = ContextSessionStore(ctx.storage).get("sender-a")
    assert session is not None
    assert session.stage == WorkflowStage.COMPLETED
    assert session.settlement_status is not None
    assert session.settlement_status.payment_hash.startswith("SIMULATED_")


def test_two_sender_addresses_have_independent_sessions():
    ctx = FakeContext()
    extractor = FakeExtractor([_complete_partial(), _complete_partial()])
    agents = (
        MockEconomistAgent(),
        MockRoutingAgent(),
        MockTreasuryAgent(),
    )
    _run(process_chat_message(ctx, "sender-a", _chat_message("ship a"), extractor, *agents))
    _run(process_chat_message(ctx, "sender-b", _chat_message("ship b"), extractor, *agents))
    from orchestrator.uagents_storage import ContextSessionStore

    store = ContextSessionStore(ctx.storage)
    session_a = store.get("sender-a")
    session_b = store.get("sender-b")
    assert session_a is not None
    assert session_b is not None
    assert session_a.session_id != session_b.session_id


def test_processing_failure_returns_safe_message():
    ctx = FakeContext()
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("ship"),
            FakeExtractor(fail=True),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    response_text = ctx.sent[-1][1].content[0].text
    assert response_text == SAFE_PROCESSING_ERROR


def test_processing_failure_does_not_expose_secrets():
    ctx = FakeContext()
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("ship"),
            FakeExtractor(fail=True),
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    response_text = ctx.sent[-1][1].content[0].text
    assert "sk-secret-key" not in response_text


def test_importing_agent_module_does_not_start_agent(monkeypatch):
    monkeypatch.setenv("AGENT_SEED", "test-seed-for-import-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    module = importlib.import_module("orchestrator.agent")
    importlib.reload(module)
    assert hasattr(module, "create_agent")
    assert hasattr(module, "process_chat_message")


def test_strip_mention_confirm():
    assert strip_leading_agent_mention("@agent1abc123 CONFIRM") == "CONFIRM"


def test_strip_mention_lowercase_confirm():
    assert strip_leading_agent_mention("@agent1abc123 confirm") == "confirm"


def test_strip_mention_new_shipment():
    assert strip_leading_agent_mention("@agent1abc123 NEW SHIPMENT") == "NEW SHIPMENT"


def test_strip_mention_allows_leading_whitespace():
    assert strip_leading_agent_mention("   @agent1abc123 CONFIRM") == "CONFIRM"


def test_strip_mention_leaves_normal_shipment_message_unchanged():
    text = "Ship semiconductors from Shenzhen to Austin."
    assert strip_leading_agent_mention(text) == text


def test_strip_mention_leaves_later_mention_unchanged():
    text = "Please notify @agent1abc123 after shipment completion."
    assert strip_leading_agent_mention(text) == text


def test_strip_mention_removes_only_one_leading_mention():
    assert (
        strip_leading_agent_mention("@agent1aaa @agent1bbb CONFIRM")
        == "@agent1bbb CONFIRM"
    )


def test_empty_result_after_stripping_uses_empty_message_response():
    ctx = FakeContext()
    extractor = FakeExtractor()
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("@agent1abc123"),
            extractor,
            MockEconomistAgent(),
            MockRoutingAgent(),
            MockTreasuryAgent(),
        )
    )
    assert extractor.calls == []
    assert "text message" in ctx.sent[-1][1].content[0].text.lower()


def _advance_to_awaiting_confirmation(ctx: FakeContext, sender: str) -> None:
    agents = (
        MockEconomistAgent(),
        MockRoutingAgent(),
        MockTreasuryAgent(),
    )
    _run(
        process_chat_message(
            ctx,
            sender,
            _chat_message("ship"),
            FakeExtractor([_complete_partial()]),
            *agents,
        )
    )


def test_prefixed_confirm_completes_workflow():
    ctx = FakeContext()
    agents = (
        MockEconomistAgent(),
        MockRoutingAgent(),
        MockTreasuryAgent(),
    )
    _advance_to_awaiting_confirmation(ctx, "sender-a")
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("@agent1abc123 CONFIRM"),
            FakeExtractor(),
            *agents,
        )
    )
    from orchestrator.uagents_storage import ContextSessionStore

    session = ContextSessionStore(ctx.storage).get("sender-a")
    assert session is not None
    assert session.stage == WorkflowStage.COMPLETED


def test_prefixed_lowercase_confirm_completes_workflow():
    ctx = FakeContext()
    agents = (
        MockEconomistAgent(),
        MockRoutingAgent(),
        MockTreasuryAgent(),
    )
    _advance_to_awaiting_confirmation(ctx, "sender-a")
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("@agent1abc123 confirm"),
            FakeExtractor(),
            *agents,
        )
    )
    from orchestrator.uagents_storage import ContextSessionStore

    session = ContextSessionStore(ctx.storage).get("sender-a")
    assert session is not None
    assert session.stage == WorkflowStage.COMPLETED


def test_prefixed_new_shipment_resets_workflow():
    ctx = FakeContext()
    agents = (
        MockEconomistAgent(),
        MockRoutingAgent(),
        MockTreasuryAgent(),
    )
    _advance_to_awaiting_confirmation(ctx, "sender-a")
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("@agent1abc123 NEW SHIPMENT"),
            FakeExtractor(),
            *agents,
        )
    )
    from orchestrator.uagents_storage import ContextSessionStore

    session = ContextSessionStore(ctx.storage).get("sender-a")
    assert session is not None
    assert session.stage == WorkflowStage.COLLECTING_INPUT
    assert session.shipment_request is None


def test_prefixed_confirm_now_is_rejected():
    ctx = FakeContext()
    agents = (
        MockEconomistAgent(),
        MockRoutingAgent(),
        MockTreasuryAgent(),
    )
    _advance_to_awaiting_confirmation(ctx, "sender-a")
    _run(
        process_chat_message(
            ctx,
            "sender-a",
            _chat_message("@agent1abc123 confirm now"),
            FakeExtractor(),
            *agents,
        )
    )
    from orchestrator.uagents_storage import ContextSessionStore

    session = ContextSessionStore(ctx.storage).get("sender-a")
    assert session is not None
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION
    assert "CONFIRM" in ctx.sent[-1][1].content[0].text


def test_non_prefixed_confirm_still_works():
    ctx = FakeContext()
    extractor = FakeExtractor([_complete_partial()])
    agents = (
        MockEconomistAgent(),
        MockRoutingAgent(),
        MockTreasuryAgent(),
    )
    _run(process_chat_message(ctx, "sender-a", _chat_message("ship"), extractor, *agents))
    _run(
        process_chat_message(
            ctx, "sender-a", _chat_message("CONFIRM"), extractor, *agents
        )
    )
    from orchestrator.uagents_storage import ContextSessionStore

    session = ContextSessionStore(ctx.storage).get("sender-a")
    assert session is not None
    assert session.stage == WorkflowStage.COMPLETED
