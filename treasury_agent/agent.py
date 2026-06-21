"""Treasury uAgent for post-confirmation settlement and Stripe payment."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv
from pydantic import ValidationError
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
    Funds,
    RejectPayment,
    RequestPayment,
)

from shared_models import (
    DocTemplates,
    EconData,
    Item,
    RouteData,
    SettlementStatus,
    ShipmentRequest,
)
from treasury_agent.drive_upload import upload_invoice_and_get_link
from treasury_agent.invoice import generate_invoice_pdf
from treasury_agent.messages import SettlementRequestMessage, SettlementResultMessage
from treasury_agent.payment_backend import (
    create_settlement_checkout,
    is_configured as stripe_is_configured,
    resolve_checkout_session_id,
    verify_checkout_paid,
)
from treasury_agent.payment_protocol import build_payment_protocol
from treasury_agent.pricing import compute_service_fee

DEFAULT_TREASURY_NAME = "aerofreight-treasury-agent"
DEFAULT_TREASURY_PORT = 8014

STORAGE_PREFIX = "pending_settlement:"
_CHECKOUT_CONFIRM_RE = re.compile(r"<stripe:payment_id:([^:>]+):CONFIRM>")
_MENTION_RE = re.compile(r"^@agent1[a-z0-9]+\s*", re.IGNORECASE)

_SAFE_VALIDATION_ERROR = (
    "The settlement request could not be processed. "
    "Please verify the shipment, route, and document data."
)
_SAFE_PAYMENT_SETUP_ERROR = (
    "Payment setup failed. Please try again shortly."
)
_SAFE_PAYMENT_NOT_COMPLETE = (
    "Payment has not been completed yet. "
    "Please finish checkout and try again."
)


class TreasuryAgentConfigurationError(RuntimeError):
    """Raised when Treasury agent configuration is missing or invalid."""


settlement_protocol = Protocol(
    name="AeroFreightTreasurySettlementProtocol",
    version="1.0.0",
)


def _require_treasury_seed(seed: str | None = None) -> str:
    if seed is not None and seed.strip():
        return seed.strip()
    load_dotenv()
    value = os.getenv("TREASURY_AGENT_SEED", "").strip()
    if not value:
        raise TreasuryAgentConfigurationError(
            "TREASURY_AGENT_SEED is not configured. "
            "Set it in your environment or .env file."
        )
    return value


def _resolve_treasury_port(port: int | None = None) -> int:
    if port is not None:
        return port
    load_dotenv()
    raw = os.getenv("TREASURY_AGENT_PORT", str(DEFAULT_TREASURY_PORT)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise TreasuryAgentConfigurationError(
            f"TREASURY_AGENT_PORT must be a valid integer, got {raw!r}."
        ) from exc


def _resolve_treasury_name() -> str:
    load_dotenv()
    return (
        os.getenv("TREASURY_AGENT_NAME", DEFAULT_TREASURY_NAME).strip()
        or DEFAULT_TREASURY_NAME
    )


def _resolve_orchestrator_address(orchestrator_address: str | None = None) -> str:
    if orchestrator_address is not None:
        return orchestrator_address.strip()
    load_dotenv()
    return os.getenv("ORCHESTRATOR_AGENT_ADDRESS", "").strip()


def _strip_agent_mention(text: str) -> str:
    return _MENTION_RE.sub("", text).strip()


def _pending_key(checkout_session_id: str) -> str:
    return f"{STORAGE_PREFIX}{checkout_session_id}"


def _pending_by_sender_key(sender: str) -> str:
    return f"{STORAGE_PREFIX}by_sender:{sender}"


def _format_location(location: dict) -> str:
    city = str(location.get("city", "")).strip()
    country = str(location.get("country", "")).strip()
    return ", ".join(part for part in (city, country) if part)


async def _send_chat(ctx: Context, to: str, text: str) -> None:
    await ctx.send(
        to,
        ChatMessage(
            content=[TextContent(type="text", text=text)],
            msg_id=uuid4(),
            timestamp=datetime.now(timezone.utc),
        ),
    )


def _parse_settlement_payload(
    msg: SettlementRequestMessage,
) -> tuple[ShipmentRequest, EconData, RouteData, DocTemplates]:
    return (
        ShipmentRequest.model_validate(msg.shipment),
        EconData.model_validate(msg.econ_data),
        RouteData.model_validate(msg.route_data),
        DocTemplates.model_validate(msg.doc_templates),
    )


def _build_settlement_status(
    *,
    pending: dict,
    transaction_id: str,
    invoice_path: str | None,
    invoice_link: str | None,
    docs: DocTemplates,
    route: RouteData,
    fee_total: float,
) -> SettlementStatus:
    drive_note = (
        f"Invoice uploaded to Google Drive: {invoice_link}"
        if invoice_link
        else "Google Drive upload was skipped or unavailable."
    )
    prompt = (
        "Payment completed successfully for your AeroFreight document package.\n\n"
        f"Service fee paid: ${fee_total:.2f}\n"
        f"Selected mode: {route.selected_mode}\n"
        f"Route: {' -> '.join(route.optimal_route_nodes)}\n"
        f"Stripe reference: {transaction_id}\n\n"
        f"{drive_note}"
    )
    filled_documents = {
        "required_form_names": docs.required_form_names,
        "blank_form_structures": docs.blank_form_structures,
        "invoice_local_path": invoice_path,
        "invoice_drive_link": invoice_link,
        "stripe_reference": transaction_id,
    }
    return SettlementStatus(
        filled_documents=filled_documents,
        final_user_prompt=prompt,
        payment_hash=transaction_id,
    )


async def _start_settlement(
    ctx: Context,
    *,
    user_address: str,
    session_id: str,
    shipment: ShipmentRequest,
    econ: EconData,
    route: RouteData,
    docs: DocTemplates,
    orchestrator_address: str,
) -> None:
    if not stripe_is_configured():
        await _send_chat(
            ctx,
            user_address,
            "Payment is not configured on this agent right now.",
        )
        if orchestrator_address:
            await ctx.send(
                orchestrator_address,
                SettlementResultMessage(
                    ok=False,
                    session_id=session_id,
                    error="Payment is not configured on this agent.",
                ),
            )
        return

    fee = compute_service_fee(econ, route)
    description = (
        f"Shipment {_format_location(shipment.origin)} -> "
        f"{_format_location(shipment.destination)}, "
        f"{route.selected_mode} mode, {len(route.countries_visited)} countries"
    )
    checkout = await asyncio.to_thread(
        create_settlement_checkout,
        user_address=user_address,
        session_id=session_id,
        amount_usd=fee.total_fee_usd,
        description=description,
    )
    if not checkout:
        await _send_chat(ctx, user_address, _SAFE_PAYMENT_SETUP_ERROR)
        if orchestrator_address:
            await ctx.send(
                orchestrator_address,
                SettlementResultMessage(
                    ok=False,
                    session_id=session_id,
                    error=_SAFE_PAYMENT_SETUP_ERROR,
                ),
            )
        return

    checkout_session_id = checkout["checkout_session_id"]
    ctx.storage.set(
        _pending_key(checkout_session_id),
        {
            "user_address": user_address,
            "session_id": session_id,
            "orchestrator_address": orchestrator_address,
            "fee_usd": fee.total_fee_usd,
            "shipment": shipment.model_dump(),
            "econ_data": econ.model_dump(),
            "route_data": route.model_dump(),
            "doc_templates": docs.model_dump(),
        },
    )
    ctx.storage.set(_pending_by_sender_key(user_address), checkout_session_id)

    await ctx.send(
        user_address,
        RequestPayment(
            accepted_funds=[
                Funds(
                    currency="USD",
                    amount=f"{fee.total_fee_usd:.2f}",
                    payment_method="stripe",
                )
            ],
            recipient=str(ctx.agent.address),
            deadline_seconds=1800,
            reference=session_id,
            description=(
                f"Pay ${fee.total_fee_usd:.2f} to receive your "
                "AeroFreight document package."
            ),
            metadata={
                "stripe": checkout,
                "service": "aerofreight_settlement_package",
            },
        ),
    )


async def _finalize_checkout(
    ctx: Context,
    sender: str,
    checkout_session_id: str,
    transaction_id: str,
) -> None:
    pending = ctx.storage.get(_pending_key(checkout_session_id))
    if not pending:
        reason = "No matching payment request found."
        await ctx.send(sender, RejectPayment(reason=reason))
        await _send_chat(ctx, sender, reason)
        return

    paid = await asyncio.to_thread(verify_checkout_paid, checkout_session_id)
    if not paid:
        reason = _SAFE_PAYMENT_NOT_COMPLETE
        await ctx.send(sender, RejectPayment(reason=reason))
        await _send_chat(ctx, sender, reason)
        return

    await ctx.send(sender, CompletePayment(transaction_id=transaction_id))
    ctx.storage.remove(_pending_key(checkout_session_id))

    shipment = ShipmentRequest.model_validate(pending["shipment"])
    econ = EconData.model_validate(pending["econ_data"])
    route = RouteData.model_validate(pending["route_data"])
    docs = DocTemplates.model_validate(pending["doc_templates"])
    fee = compute_service_fee(econ, route)

    invoice_path = None
    invoice_link = None
    try:
        invoice_path = os.path.join(
            tempfile.gettempdir(),
            f"aerofreight_invoice_{checkout_session_id}.pdf",
        )
        await asyncio.to_thread(
            generate_invoice_pdf,
            output_path=invoice_path,
            session_id=pending["session_id"],
            transaction_id=transaction_id,
            shipment=shipment,
            econ=econ,
            route=route,
            docs=docs,
            fee=fee,
        )
        invoice_link = await asyncio.to_thread(
            upload_invoice_and_get_link,
            invoice_path,
            f"AeroFreight_Invoice_{checkout_session_id}.pdf",
        )
    except Exception:
        ctx.logger.exception("Invoice generation or upload failed")

    settlement_status = _build_settlement_status(
        pending=pending,
        transaction_id=transaction_id,
        invoice_path=invoice_path,
        invoice_link=invoice_link,
        docs=docs,
        route=route,
        fee_total=pending["fee_usd"],
    )

    user_address = pending["user_address"]
    if invoice_link:
        message = (
            f"Payment received (${pending['fee_usd']:.2f}). "
            f"Your invoice is ready:\n\n{invoice_link}"
        )
    else:
        message = settlement_status.final_user_prompt

    await _send_chat(ctx, user_address, message)

    orchestrator_address = pending.get("orchestrator_address", "")
    if orchestrator_address:
        await ctx.send(
            orchestrator_address,
            SettlementResultMessage(
                ok=True,
                session_id=pending["session_id"],
                settlement_status=settlement_status.model_dump(),
            ),
        )


def _register_settlement_handlers(agent: Agent, orchestrator_address: str) -> None:
    @settlement_protocol.on_message(
        model=SettlementRequestMessage,
        replies=SettlementResultMessage,
    )
    async def on_settlement_request(
        ctx: Context,
        sender: str,
        msg: SettlementRequestMessage,
    ) -> None:
        try:
            shipment, econ, route, docs = _parse_settlement_payload(msg)
        except ValidationError:
            ctx.logger.error("Settlement request validation failed")
            await ctx.send(
                sender,
                SettlementResultMessage(
                    ok=False,
                    session_id=msg.session_id,
                    error=_SAFE_VALIDATION_ERROR,
                ),
            )
            return

        await _start_settlement(
            ctx,
            user_address=msg.user_address,
            session_id=msg.session_id,
            shipment=shipment,
            econ=econ,
            route=route,
            docs=docs,
            orchestrator_address=orchestrator_address or sender,
        )

    agent.include(settlement_protocol)


def _register_payment_handlers(agent: Agent) -> None:
    async def on_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
        if getattr(msg.funds, "payment_method", None) != "stripe" or not getattr(
            msg,
            "transaction_id",
            None,
        ):
            reason = "Unsupported payment method."
            await ctx.send(sender, RejectPayment(reason=reason))
            await _send_chat(ctx, sender, reason)
            return
        checkout_id = await asyncio.to_thread(
            resolve_checkout_session_id,
            msg.transaction_id,
        )
        await _finalize_checkout(ctx, sender, checkout_id, msg.transaction_id)

    async def on_payment_reject(ctx: Context, sender: str, msg: RejectPayment):
        ctx.logger.info("Payment rejected by %s", sender)

    agent.include(
        build_payment_protocol(on_payment_commit, on_payment_reject),
        publish_manifest=True,
    )


def _register_chat_handlers(agent: Agent, orchestrator_address: str) -> None:
    chat_proto = Protocol(spec=chat_protocol_spec)

    demo_shipment = ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="Electronics", quantity=10, category="electronics")],
        total_weight_kg=850.0,
        total_volume_cbm=3.2,
        timeframe="COST",
        declared_value_usd=4200.0,
    )
    demo_econ = EconData(
        transport_preference="EITHER",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=126.50,
    )
    demo_route = RouteData(
        selected_mode="SHIP",
        optimal_route_nodes=["Shenzhen", "USLAX", "Austin"],
        countries_visited=["CN", "US"],
        freight_and_toll_cost_usd=645.0,
        total_landed_cost_usd=771.25,
    )
    demo_docs = DocTemplates(
        required_form_names=["CBP Form 7501", "Bill of Lading"],
        blank_form_structures={
            "CBP Form 7501": {"status": "demo placeholder"},
            "Bill of Lading": {"status": "demo placeholder"},
        },
    )

    def _extract_text(msg: ChatMessage) -> str:
        return "".join(
            content.text
            for content in msg.content
            if isinstance(content, TextContent)
        )

    @chat_proto.on_message(ChatMessage)
    async def on_chat_message(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(
            sender,
            ChatAcknowledgement(
                acknowledged_msg_id=msg.msg_id,
                timestamp=datetime.now(timezone.utc),
            ),
        )

        text = _strip_agent_mention(_extract_text(msg).strip())
        confirm_match = _CHECKOUT_CONFIRM_RE.search(text)
        if confirm_match:
            checkout_session_id = ctx.storage.get(_pending_by_sender_key(sender))
            if checkout_session_id:
                await _finalize_checkout(
                    ctx,
                    sender,
                    checkout_session_id,
                    transaction_id=confirm_match.group(1),
                )
            return

        if text.upper() == "DEMO":
            await _start_settlement(
                ctx,
                user_address=sender,
                session_id=f"demo-{uuid4().hex[:8]}",
                shipment=demo_shipment,
                econ=demo_econ,
                route=demo_route,
                docs=demo_docs,
                orchestrator_address=orchestrator_address,
            )
            return

        await _send_chat(
            ctx,
            sender,
            "AeroFreight Treasury agent. Send DEMO to try a sample settlement flow.",
        )

    @chat_proto.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        ctx.logger.debug("Ack from %s", sender)

    agent.include(chat_proto, publish_manifest=True)


def create_treasury_agent(
    *,
    seed: str | None = None,
    port: int | None = None,
    orchestrator_address: str | None = None,
) -> Agent:
    """Create and configure the Treasury uAgent."""
    resolved_orchestrator = _resolve_orchestrator_address(orchestrator_address)
    agent = Agent(
        name=_resolve_treasury_name(),
        seed=_require_treasury_seed(seed),
        port=_resolve_treasury_port(port),
        mailbox=True,
        publish_agent_details=True,
    )
    _register_settlement_handlers(agent, resolved_orchestrator)
    _register_payment_handlers(agent)
    _register_chat_handlers(agent, resolved_orchestrator)
    return agent


def main() -> None:
    agent = create_treasury_agent()
    print(f"Treasury agent address: {agent.address}")
    agent.run()


if __name__ == "__main__":
    main()
