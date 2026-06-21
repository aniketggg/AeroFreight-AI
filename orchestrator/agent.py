"""AeroFreight orchestrator uAgent with Agent Chat Protocol support."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.payment import (
    CommitPayment,
    CompletePayment,
    RejectPayment,
)

from orchestrator.agent_interfaces import PaymentSetupResult
from orchestrator.conversation import ConversationController
from orchestrator.coordinator import WorkflowCoordinator
from orchestrator.extractor import ClaudeShipmentExtractor
from orchestrator.mock_agents import (
    MockEconomistAgent,
    MockRoutingAgent,
    MockTreasuryAgent,
)
from orchestrator.models import WorkflowStage
from orchestrator.remote_agents import (
    RemoteTreasuryError,
    UAgentsEconomistClient,
    UAgentsRoutingClient,
    UAgentsTreasuryPaymentClient,
)
from orchestrator.service import OrchestratorService
from orchestrator.uagents_storage import ContextSessionStore
from orchestrator.payment_trace import (
    build_orchestrator_request_payment,
    debug_ndjson_log,
    is_payment_debug_enabled,
    is_send_failure,
    log_payment_protocol_registration,
    normalize_fetch_checkout_metadata,
    payment_trace,
    redact_request_payment_payload,
    summarize_checkout,
    summarize_request_payment_dump,
    summarize_send_result,
)
from orchestrator.uagents_mailbox import mailbox_registration_policy
from treasury_agent.payment_protocol import build_payment_protocol

DEFAULT_AGENT_NAME = "aerofreight-orchestrator"
DEFAULT_AGENT_PORT = 8001

PENDING_PAYMENT_PREFIX = "orchestrator_pending_payment:"

SAFE_PROCESSING_ERROR = (
    "AeroFreight AI encountered a temporary processing error. Please try again."
)
EMPTY_TEXT_RESPONSE = "Please send a text message describing your shipment."
SAFE_PAYMENT_REJECT = (
    "Payment was not completed. Start a NEW SHIPMENT to try again."
)
SAFE_PAYMENT_FAILURE = (
    "Payment could not be verified. Please try again or start a NEW SHIPMENT."
)
PAYMENT_DELIVERY_FAILURE = (
    "The payment request could not be delivered. Please try again."
)
DEBUG_PAYMENT_DISPATCHED = "Payment request message was dispatched to ASI:One."
DEBUG_PAYMENT_DELIVERY_FAILED = (
    "Payment request delivery failed before reaching ASI:One."
)


class AgentConfigurationError(Exception):
    """Raised when the uAgent is not configured for startup."""


def extract_text_content(message: ChatMessage) -> str:
    """Collect and join text blocks from a chat message."""
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
            continue
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text = getattr(block, "text", "")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def strip_leading_agent_mention(text: str) -> str:
    """Remove one leading ASI:One agent mention from incoming text."""
    return re.sub(
        r"^\s*@agent1[a-z0-9]+\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    ).strip()


def _load_agent_settings() -> tuple[str, str, int]:
    load_dotenv()

    agent_seed = os.getenv("AGENT_SEED", "").strip()
    if not agent_seed:
        raise AgentConfigurationError(
            "AGENT_SEED is not configured. Set it in your environment or .env file."
        )

    agent_name = os.getenv("AGENT_NAME", DEFAULT_AGENT_NAME).strip() or DEFAULT_AGENT_NAME

    port_raw = os.getenv("AGENT_PORT", str(DEFAULT_AGENT_PORT)).strip()
    try:
        agent_port = int(port_raw)
    except ValueError as exc:
        raise AgentConfigurationError(
            f"AGENT_PORT must be a valid integer, got {port_raw!r}."
        ) from exc

    return agent_seed, agent_name, agent_port


def _resolve_economist(ctx: Context, economist_override=None):
    """Use injected economist, remote client, or mock fallback."""
    if economist_override is not None:
        return economist_override

    economist_address = os.getenv("ECONOMIST_AGENT_ADDRESS", "").strip()
    if economist_address:
        timeout_raw = os.getenv("ECONOMIST_AGENT_TIMEOUT_SECONDS", "30").strip()
        try:
            timeout_seconds = int(timeout_raw)
        except ValueError:
            timeout_seconds = 30
        ctx.logger.info("Using remote Economist agent from configuration")
        return UAgentsEconomistClient(
            ctx,
            economist_address,
            timeout_seconds=timeout_seconds,
        )

    ctx.logger.info(
        "Mock Economist mode active (ECONOMIST_AGENT_ADDRESS not set)"
    )
    return MockEconomistAgent()


def _resolve_router(ctx: Context, router_override=None):
    """Use injected router, remote client, or mock fallback."""
    if router_override is not None:
        return router_override

    router_address = os.getenv("ROUTER_AGENT_ADDRESS", "").strip()
    if router_address:
        timeout_raw = os.getenv("ROUTER_AGENT_TIMEOUT_SECONDS", "30").strip()
        try:
            timeout_seconds = int(timeout_raw)
        except ValueError:
            timeout_seconds = 30
        ctx.logger.info("Using remote Router agent from configuration")
        return UAgentsRoutingClient(
            ctx,
            router_address,
            timeout_seconds=timeout_seconds,
        )

    ctx.logger.info(
        "Mock Router mode active (ROUTER_AGENT_ADDRESS not set)"
    )
    return MockRoutingAgent()


def _resolve_treasury_payment_client(ctx: Context, treasury_payment_override=None):
    """Use injected Treasury payment client, remote client, or mock fallback."""
    if treasury_payment_override is not None:
        return treasury_payment_override

    treasury_address = os.getenv("TREASURY_AGENT_ADDRESS", "").strip()
    if treasury_address:
        ctx.logger.info("Using remote Treasury agent from configuration")
        return UAgentsTreasuryPaymentClient(ctx, treasury_address)

    ctx.logger.info(
        "Mock settlement mode active (TREASURY_AGENT_ADDRESS not set)"
    )
    return None


def _pending_payment_key(user_address: str) -> str:
    return f"{PENDING_PAYMENT_PREFIX}{user_address}"


def _build_response_message(response_text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=response_text)],
    )


def _store_pending_payment(
    ctx: Context,
    *,
    user_address: str,
    session_id: str,
    setup: PaymentSetupResult,
) -> None:
    ctx.storage.set(
        _pending_payment_key(user_address),
        {
            "session_id": session_id,
            "checkout_session_id": setup.checkout["checkout_session_id"],
            "fee_usd": setup.fee_usd,
            "checkout": setup.checkout,
        },
    )


def _load_pending_payment(ctx: Context, user_address: str) -> dict | None:
    pending = ctx.storage.get(_pending_payment_key(user_address))
    return pending if isinstance(pending, dict) else None


def _clear_pending_payment(ctx: Context, user_address: str) -> None:
    ctx.storage.remove(_pending_payment_key(user_address))


async def _send_orchestrator_request_payment(
    ctx: Context,
    *,
    user_address: str,
    session_id: str,
    fee_usd: float,
    checkout: dict,
) -> bool:
    normalized_checkout, normalization_changes = normalize_fetch_checkout_metadata(
        checkout
    )
    if normalized_checkout is None:
        payment_trace(
            ctx.logger,
            "orchestrator.request_payment.invalid_checkout",
            session_id=session_id,
        )
        return False

    if normalization_changes.get("changed"):
        payment_trace(
            ctx.logger,
            "orchestrator.request_payment.checkout_normalized",
            session_id=session_id,
            ui_mode_from=normalization_changes.get("ui_mode_from"),
            set_id=normalization_changes.get("set_id"),
            set_checkout_session_id=normalization_changes.get(
                "set_checkout_session_id"
            ),
        )
        # #region agent log
        debug_ndjson_log(
            hypothesis_id="J",
            location="orchestrator/agent.py:_send_orchestrator_request_payment",
            message="checkout metadata normalized before RequestPayment",
            data=normalization_changes,
        )
        # #endregion

    request_payment = build_orchestrator_request_payment(
        recipient=str(ctx.agent.address),
        session_id=session_id,
        fee_usd=fee_usd,
        checkout=normalized_checkout,
    )
    payload_dump = request_payment.model_dump()
    print(
        f"PAYMENT_TRACE Payload: "
        f"{redact_request_payment_payload(payload_dump)}"
    )
    dumped = payload_dump
    dump_summary = summarize_request_payment_dump(dumped)
    payment_trace(
        ctx.logger,
        "orchestrator.request_payment.built",
        session_id=session_id,
        request_model_class=type(request_payment).__name__,
        destination_user_address=user_address,
        recipient_address=dump_summary["recipient"],
        accepted_funds_count=dump_summary["accepted_funds_count"],
        payment_method=dump_summary["payment_method"],
        currency=dump_summary["currency"],
        amount=dump_summary["amount"],
        top_level_dumped_keys=dump_summary["top_level_keys"],
        metadata_type=dump_summary["metadata_type"],
        metadata_keys=dump_summary["metadata_keys"],
        stripe_metadata_type=dump_summary["stripe_metadata_type"],
        stripe_metadata_keys=dump_summary["stripe_metadata_keys"],
        stripe_ui_mode=dump_summary["stripe_ui_mode"],
        has_client_secret=dump_summary["has_client_secret"],
        has_publishable_key=dump_summary["has_publishable_key"],
        has_id=dump_summary["has_id"],
        has_checkout_session_id=dump_summary["has_checkout_session_id"],
        id_aliases_match=dump_summary["id_aliases_match"],
        amount_cents_python_type=dump_summary["amount_cents_python_type"],
        metadata_stripe_is_dict=dump_summary["metadata_stripe_is_dict"],
    )
    # #region agent log
    debug_ndjson_log(
        hypothesis_id="B",
        location="orchestrator/agent.py:_send_orchestrator_request_payment",
        message="RequestPayment built",
        data=dump_summary,
    )
    # #endregion

    ctx.logger.info(
        "PAYMENT_TRACE orchestrator.request_payment.dispatched "
        "session_id=%s ui_mode=%s send_method=ctx.send",
        session_id,
        dump_summary["stripe_ui_mode"],
    )
    # #region agent log
    debug_ndjson_log(
        hypothesis_id="K",
        location="orchestrator/agent.py:_send_orchestrator_request_payment",
        message="dispatching RequestPayment via ctx.send from chat handler",
        data={
            "destination": user_address[:16] + "…",
            "request_model": type(request_payment).__name__,
        },
        always=True,
    )
    # #endregion

    try:
        send_result = await ctx.send(user_address, request_payment)
        print(f"PAYMENT_TRACE Delivery Status: {send_result}")
    except Exception as exc:
        payment_trace(
            ctx.logger,
            "orchestrator.request_payment.send_exception",
            session_id=session_id,
            exception_class=type(exc).__name__,
            error_message=str(exc)[:240],
        )
        # #region agent log
        debug_ndjson_log(
            hypothesis_id="C",
            location="orchestrator/agent.py:_send_orchestrator_request_payment",
            message="ctx.send raised",
            data={"exception_class": type(exc).__name__},
        )
        # #endregion
        return False

    send_summary = summarize_send_result(send_result)
    delivery_failed = is_send_failure(send_result)
    payment_trace(
        ctx.logger,
        "orchestrator.request_payment.send_result",
        session_id=session_id,
        destination_user_address=user_address,
        delivery_failed=delivery_failed,
        **send_summary,
    )
    # #region agent log
    debug_ndjson_log(
        hypothesis_id="C",
        location="orchestrator/agent.py:_send_orchestrator_request_payment",
        message="ctx.send completed",
        data={**send_summary, "delivery_failed": delivery_failed},
        always=True,
    )
    # #endregion
    return not delivery_failed


async def _deliver_payment_wall(
    ctx: Context,
    *,
    user_address: str,
    session_id: str,
    setup: PaymentSetupResult | None,
) -> bool:
    payment_trace(
        ctx.logger,
        "orchestrator.payment_wall.enter",
        session_id=session_id,
        user_destination_address=user_address,
        has_new_setup=setup is not None,
        pending_payment_key_exists=_load_pending_payment(ctx, user_address) is not None,
    )
    # #region agent log
    debug_ndjson_log(
        hypothesis_id="A",
        location="orchestrator/agent.py:_deliver_payment_wall",
        message="enter payment wall delivery",
        data={
            "session_id": session_id,
            "has_new_setup": setup is not None,
            "pending_exists": _load_pending_payment(ctx, user_address) is not None,
        },
    )
    # #endregion

    if setup is not None:
        checkout_summary = summarize_checkout(setup.checkout)
        payment_trace(
            ctx.logger,
            "orchestrator.payment_wall.new_setup",
            session_id=session_id,
            user_destination_address=user_address,
            checkout_key_names=checkout_summary["checkout_key_names"],
            ui_mode=checkout_summary["ui_mode"],
            has_client_secret=checkout_summary["has_client_secret"],
            has_id=checkout_summary["has_id"],
            has_checkout_session_id=checkout_summary["has_checkout_session_id"],
        )
        _store_pending_payment(
            ctx,
            user_address=user_address,
            session_id=session_id,
            setup=setup,
        )
        payment_trace(
            ctx.logger,
            "orchestrator.payment_wall.send_start",
            session_id=session_id,
            user_destination_address=user_address,
            setup_source="new",
        )
        delivered = await _send_orchestrator_request_payment(
            ctx,
            user_address=user_address,
            session_id=session_id,
            fee_usd=setup.fee_usd,
            checkout=setup.checkout,
        )
        payment_trace(
            ctx.logger,
            "orchestrator.payment_wall.send_complete",
            session_id=session_id,
            user_destination_address=user_address,
            setup_source="new",
            delivery_succeeded=delivered,
        )
        return delivered

    pending = _load_pending_payment(ctx, user_address)
    if pending is None:
        payment_trace(
            ctx.logger,
            "orchestrator.payment_wall.send_complete",
            session_id=session_id,
            user_destination_address=user_address,
            setup_source="missing",
            delivery_succeeded=False,
        )
        return False

    checkout_summary = summarize_checkout(pending.get("checkout"))
    payment_trace(
        ctx.logger,
        "orchestrator.payment_wall.cached_setup",
        session_id=session_id,
        user_destination_address=user_address,
        checkout_key_names=checkout_summary["checkout_key_names"],
        ui_mode=checkout_summary["ui_mode"],
        has_client_secret=checkout_summary["has_client_secret"],
        has_id=checkout_summary["has_id"],
        has_checkout_session_id=checkout_summary["has_checkout_session_id"],
    )
    payment_trace(
        ctx.logger,
        "orchestrator.payment_wall.send_start",
        session_id=session_id,
        user_destination_address=user_address,
        setup_source="cached",
    )
    delivered = await _send_orchestrator_request_payment(
        ctx,
        user_address=user_address,
        session_id=pending["session_id"],
        fee_usd=pending["fee_usd"],
        checkout=pending["checkout"],
    )
    payment_trace(
        ctx.logger,
        "orchestrator.payment_wall.send_complete",
        session_id=session_id,
        user_destination_address=user_address,
        setup_source="cached",
        delivery_succeeded=delivered,
    )
    return delivered


async def handle_commit_payment(
    ctx: Context,
    sender: str,
    msg: CommitPayment,
    treasury_payment_client=None,
) -> None:
    """Verify Stripe payment through Treasury and complete the workflow."""
    if getattr(msg.funds, "payment_method", None) != "stripe":
        await ctx.send(sender, RejectPayment(reason="Unsupported payment method."))
        await ctx.send(sender, _build_response_message(SAFE_PAYMENT_FAILURE))
        return

    transaction_id = getattr(msg, "transaction_id", None)
    if not transaction_id or not str(transaction_id).strip():
        await ctx.send(sender, RejectPayment(reason="Missing payment reference."))
        await ctx.send(sender, _build_response_message(SAFE_PAYMENT_FAILURE))
        return

    pending = _load_pending_payment(ctx, sender)
    if pending is None:
        await ctx.send(sender, RejectPayment(reason="No pending payment found."))
        return

    session_store = ContextSessionStore(ctx.storage)
    service = OrchestratorService(session_store)
    session = session_store.get(sender)

    if session is not None and session.stage == WorkflowStage.COMPLETED:
        await ctx.send(sender, CompletePayment(transaction_id=transaction_id))
        _clear_pending_payment(ctx, sender)
        return

    client = _resolve_treasury_payment_client(ctx, treasury_payment_client)
    if client is None:
        await ctx.send(sender, RejectPayment(reason="Payment processing unavailable."))
        await ctx.send(sender, _build_response_message(SAFE_PAYMENT_FAILURE))
        return

    try:
        settlement_status = await client.finalize_payment(
            user_address=sender,
            session_id=pending["session_id"],
            checkout_session_id=pending["checkout_session_id"],
            transaction_id=str(transaction_id).strip(),
        )
        service.record_payment_result(sender, settlement_status)
    except (RemoteTreasuryError, ValueError, RuntimeError):
        ctx.logger.error("Payment finalization failed for sender")
        await ctx.send(sender, RejectPayment(reason="Payment could not be verified."))
        await ctx.send(sender, _build_response_message(SAFE_PAYMENT_FAILURE))
        return

    _clear_pending_payment(ctx, sender)
    await ctx.send(sender, CompletePayment(transaction_id=transaction_id))
    await ctx.send(sender, _build_response_message(settlement_status.final_user_prompt))


async def handle_reject_payment(
    ctx: Context,
    sender: str,
    msg: RejectPayment,
) -> None:
    """Handle user cancellation without revealing locked quote data."""
    _clear_pending_payment(ctx, sender)
    session_store = ContextSessionStore(ctx.storage)
    service = OrchestratorService(session_store)
    session = session_store.get(sender)
    if session is not None and session.stage == WorkflowStage.AWAITING_PAYMENT:
        service.mark_failed(sender, "Payment was cancelled.")
    await ctx.send(sender, _build_response_message(SAFE_PAYMENT_REJECT))


async def process_chat_message(
    ctx: Context,
    sender: str,
    msg: ChatMessage,
    extractor,
    economist=None,
    router=None,
    treasury=None,
    treasury_payment=None,
) -> None:
    """Acknowledge, process, and respond to an incoming chat message."""
    await ctx.send(
        sender,
        ChatAcknowledgement(
            timestamp=datetime.now(timezone.utc),
            acknowledged_msg_id=msg.msg_id,
        ),
    )

    user_text = strip_leading_agent_mention(extract_text_content(msg))
    if not user_text:
        await ctx.send(sender, _build_response_message(EMPTY_TEXT_RESPONSE))
        return

    response = EMPTY_TEXT_RESPONSE
    session = None
    payment_delivered = True

    try:
        session_store = ContextSessionStore(ctx.storage)
        service = OrchestratorService(session_store)
        conversation = ConversationController(service, extractor)
        resolved_treasury_payment = _resolve_treasury_payment_client(
            ctx,
            treasury_payment,
        )
        coordinator = WorkflowCoordinator(
            conversation=conversation,
            service=service,
            economist=_resolve_economist(ctx, economist),
            router=_resolve_router(ctx, router),
            treasury=treasury or MockTreasuryAgent(),
            treasury_payment_client=resolved_treasury_payment,
        )
        session, response, setup = await coordinator.handle_user_message_async(
            sender_address=sender,
            user_message=user_text,
        )
        if session.stage == WorkflowStage.AWAITING_PAYMENT:
            payment_delivered = await _deliver_payment_wall(
                ctx,
                user_address=sender,
                session_id=session.session_id,
                setup=setup,
            )
            if not payment_delivered:
                response = PAYMENT_DELIVERY_FAILURE
    except Exception as exc:
        ctx.logger.error("Chat processing failed: %s", type(exc).__name__)
        response = SAFE_PROCESSING_ERROR
        payment_delivered = False

    await ctx.send(sender, _build_response_message(response))

    if (
        is_payment_debug_enabled()
        and session is not None
        and session.stage == WorkflowStage.AWAITING_PAYMENT
    ):
        debug_text = (
            DEBUG_PAYMENT_DISPATCHED
            if payment_delivered
            else DEBUG_PAYMENT_DELIVERY_FAILED
        )
        await ctx.send(sender, _build_response_message(debug_text))


def create_agent(
    *,
    extractor=None,
    economist=None,
    router=None,
    treasury=None,
    treasury_payment=None,
) -> Agent:
    """Create and configure the AeroFreight orchestrator uAgent."""
    agent_seed, agent_name, agent_port = _load_agent_settings()

    if extractor is None:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise AgentConfigurationError(
                "ANTHROPIC_API_KEY is not configured. "
                "Set it in your environment or .env file."
            )
        extractor = ClaudeShipmentExtractor()

    treasury = treasury or MockTreasuryAgent()

    agent = Agent(
        name=agent_name,
        seed=agent_seed,
        port=agent_port,
        mailbox=True,
        publish_agent_details=True,
        readme_path="README.md",
        registration_policy=mailbox_registration_policy(),
    )

    chat_protocol = Protocol(spec=chat_protocol_spec)

    @chat_protocol.on_message(ChatMessage)
    async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage):
        await process_chat_message(
            ctx,
            sender,
            msg,
            extractor,
            economist,
            router,
            treasury,
            treasury_payment,
        )

    @chat_protocol.on_message(ChatAcknowledgement)
    async def handle_acknowledgement(
        ctx: Context,
        sender: str,
        msg: ChatAcknowledgement,
    ):
        ctx.logger.debug(
            "Received acknowledgement from %s for message %s",
            sender,
            msg.acknowledged_msg_id,
        )

    async def on_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
        await handle_commit_payment(ctx, sender, msg, treasury_payment)

    async def on_payment_reject(ctx: Context, sender: str, msg: RejectPayment):
        await handle_reject_payment(ctx, sender, msg)

    payment_proto = build_payment_protocol(on_payment_commit, on_payment_reject)
    agent.include(chat_protocol, publish_manifest=True)
    agent.include(payment_proto, publish_manifest=True)
    log_payment_protocol_registration(None, payment_proto)
    # #region agent log
    debug_ndjson_log(
        hypothesis_id="D",
        location="orchestrator/agent.py:create_agent",
        message="payment protocol registered",
        data={
            "protocol_name": getattr(
                getattr(payment_proto, "spec", None), "name", None
            ),
            "manifest_digest": getattr(payment_proto, "digest", None),
            "incoming_models": sorted(
                getattr(model, "__name__", str(model))
                for model in getattr(payment_proto, "models", {}).values()
            ),
        },
    )
    # #endregion
    return agent


def main() -> None:
    agent = create_agent()
    print(f"AeroFreight agent address: {agent.address}")
    agent.run()


if __name__ == "__main__":
    main()
