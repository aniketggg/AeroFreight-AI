"""Treasury uAgent for post-confirmation settlement and Stripe payment."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any
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
from treasury_agent.messages import (
    PaymentFinalizeRequestMessage,
    PaymentFinalizeResponseMessage,
    PaymentSetupRequestMessage,
    PaymentSetupResponseMessage,
)
from treasury_agent.payment_backend import (
    create_settlement_checkout,
    is_configured as stripe_is_configured,
    resolve_checkout_session_id,
    verify_checkout_paid,
)
from treasury_agent.payment_protocol import build_payment_protocol
from treasury_agent.pricing import FeeBreakdown, compute_service_fee
from orchestrator.payment_trace import payment_trace, summarize_checkout
from orchestrator.uagents_mailbox import mailbox_registration_policy

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
_SAFE_FINALIZE_ERROR = (
    "Payment could not be verified. Please try again."
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


def _finalized_key(checkout_session_id: str) -> str:
    return f"{STORAGE_PREFIX}finalized:{checkout_session_id}"


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


def _parse_payment_payload(
    *,
    shipment: dict[str, Any],
    econ_data: dict[str, Any],
    route_data: dict[str, Any],
    doc_templates: dict[str, Any],
) -> tuple[ShipmentRequest, EconData, RouteData, DocTemplates]:
    return (
        ShipmentRequest.model_validate(shipment),
        EconData.model_validate(econ_data),
        RouteData.model_validate(route_data),
        DocTemplates.model_validate(doc_templates),
    )


def _build_settlement_status(
    *,
    pending: dict,
    transaction_id: str,
    invoice_path: str | None,
    invoice_link: str | None,
    docs: DocTemplates,
    route: RouteData,
    econ: EconData,
    fee: FeeBreakdown,
) -> SettlementStatus:
    route_summary = " → ".join(route.optimal_route_nodes)
    invoice_line = (
        f"**Invoice:** {invoice_link}"
        if invoice_link
        else "**Invoice:** Google Drive upload was skipped or unavailable."
    )
    prompt = (
        "## AeroFreight AI Shipment Quote\n\n"
        f"**Suggested mode:** {route.selected_mode}\n\n"
        f"**Route:** {route_summary}\n\n"
        f"**Freight and transit charges:** "
        f"${route.freight_and_toll_cost_usd:,.2f} USD\n\n"
        f"**Entry tax:** ${econ.base_entry_tax_usd:,.2f} USD\n\n"
        f"**Total landed cost:** ${route.total_landed_cost_usd:,.2f} USD\n\n"
        f"**AeroFreight service fee:** ${fee.total_fee_usd:,.2f} USD\n\n"
        f"{invoice_line}\n\n"
        f"**Stripe reference:** {transaction_id}\n\n"
        "*Warning: Freight, route, tariff, and customs values in this quote are "
        "simulated demo values and are not current market prices or legal customs "
        "assessments.*\n\n"
        "*Stripe ran in test mode when test keys are configured.*\n\n"
        "Type NEW SHIPMENT to begin another workflow."
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


def _build_checkout_description(
    shipment: ShipmentRequest,
    route: RouteData,
) -> str:
    return (
        f"Shipment {_format_location(shipment.origin)} -> "
        f"{_format_location(shipment.destination)}, "
        f"{route.selected_mode} mode, {len(route.countries_visited)} countries"
    )


async def _create_checkout_setup(
    *,
    user_address: str,
    session_id: str,
    shipment: ShipmentRequest,
    econ: EconData,
    route: RouteData,
    docs: DocTemplates,
    orchestrator_address: str = "",
) -> dict[str, Any]:
    """Create Stripe checkout and pending settlement state."""
    if not stripe_is_configured():
        return {
            "ok": False,
            "session_id": session_id,
            "error": "Payment is not configured on this agent.",
        }

    fee = compute_service_fee(econ, route)
    checkout = await asyncio.to_thread(
        create_settlement_checkout,
        user_address=user_address,
        session_id=session_id,
        amount_usd=fee.total_fee_usd,
        description=_build_checkout_description(shipment, route),
    )
    if not checkout:
        return {
            "ok": False,
            "session_id": session_id,
            "error": _SAFE_PAYMENT_SETUP_ERROR,
        }

    checkout_summary = summarize_checkout(checkout)
    payment_trace(
        None,
        "treasury.setup.checkout_created",
        session_id=session_id,
        fee_usd=fee.total_fee_usd,
        checkout_key_names=checkout_summary["checkout_key_names"],
        ui_mode=checkout_summary["ui_mode"],
        has_client_secret=checkout_summary["has_client_secret"],
        has_id=checkout_summary["has_id"],
        has_checkout_session_id=checkout_summary["has_checkout_session_id"],
    )

    checkout_session_id = checkout["checkout_session_id"]
    pending = {
        "user_address": user_address,
        "session_id": session_id,
        "orchestrator_address": orchestrator_address,
        "fee_usd": fee.total_fee_usd,
        "shipment": shipment.model_dump(),
        "econ_data": econ.model_dump(),
        "route_data": route.model_dump(),
        "doc_templates": docs.model_dump(),
        "checkout_session_id": checkout_session_id,
        "finalized": False,
    }
    return {
        "ok": True,
        "session_id": session_id,
        "checkout": checkout,
        "fee_usd": fee.total_fee_usd,
        "checkout_session_id": checkout_session_id,
        "pending": pending,
    }


def _store_pending_settlement(
    ctx: Context,
    *,
    pending: dict,
    checkout_session_id: str,
    user_address: str,
) -> None:
    ctx.storage.set(_pending_key(checkout_session_id), pending)
    ctx.storage.set(_pending_by_sender_key(user_address), checkout_session_id)


async def execute_settlement_finalization(
    ctx: Context,
    *,
    checkout_session_id: str,
    transaction_id: str,
    expected_session_id: str | None = None,
    expected_user_address: str | None = None,
) -> tuple[bool, SettlementStatus | None, str | None]:
    """Verify Stripe payment, generate invoice, and build settlement status."""
    finalized = ctx.storage.get(_finalized_key(checkout_session_id))
    if isinstance(finalized, dict) and finalized.get("final_user_prompt"):
        status = SettlementStatus.model_validate(finalized)
        return True, status, None

    pending = ctx.storage.get(_pending_key(checkout_session_id))
    if not pending:
        return False, None, "No matching payment request found."

    if pending.get("finalized"):
        status = SettlementStatus.model_validate(pending["settlement_status"])
        ctx.storage.set(_finalized_key(checkout_session_id), status.model_dump())
        return True, status, None

    if expected_session_id and pending.get("session_id") != expected_session_id:
        return False, None, _SAFE_FINALIZE_ERROR
    if expected_user_address and pending.get("user_address") != expected_user_address:
        return False, None, _SAFE_FINALIZE_ERROR

    paid = await asyncio.to_thread(verify_checkout_paid, checkout_session_id)
    if not paid:
        return False, None, _SAFE_PAYMENT_NOT_COMPLETE

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
        econ=econ,
        fee=fee,
    )

    pending["finalized"] = True
    pending["settlement_status"] = settlement_status.model_dump()
    ctx.storage.set(_pending_key(checkout_session_id), pending)
    ctx.storage.set(_finalized_key(checkout_session_id), settlement_status.model_dump())
    ctx.storage.remove(_pending_by_sender_key(pending["user_address"]))

    return True, settlement_status, None


async def _send_standalone_request_payment(
    ctx: Context,
    *,
    user_address: str,
    session_id: str,
    fee_usd: float,
    checkout: dict[str, Any],
) -> None:
    await ctx.send(
        user_address,
        RequestPayment(
            accepted_funds=[
                Funds(
                    currency="USD",
                    amount=f"{fee_usd:.2f}",
                    payment_method="stripe",
                )
            ],
            recipient=str(ctx.agent.address),
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
        ),
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
    """Standalone Treasury demo flow: create checkout and send RequestPayment."""
    setup = await _create_checkout_setup(
        user_address=user_address,
        session_id=session_id,
        shipment=shipment,
        econ=econ,
        route=route,
        docs=docs,
        orchestrator_address=orchestrator_address,
    )
    if not setup["ok"]:
        await _send_chat(ctx, user_address, setup.get("error", _SAFE_PAYMENT_SETUP_ERROR))
        return

    _store_pending_settlement(
        ctx,
        pending=setup["pending"],
        checkout_session_id=setup["checkout_session_id"],
        user_address=user_address,
    )
    await _send_standalone_request_payment(
        ctx,
        user_address=user_address,
        session_id=session_id,
        fee_usd=setup["fee_usd"],
        checkout=setup["checkout"],
    )


async def _finalize_checkout(
    ctx: Context,
    sender: str,
    checkout_session_id: str,
    transaction_id: str,
) -> None:
    """Standalone Treasury commit flow using the shared finalization core."""
    ok, settlement_status, error = await execute_settlement_finalization(
        ctx,
        checkout_session_id=checkout_session_id,
        transaction_id=transaction_id,
    )
    if not ok or settlement_status is None:
        reason = error or _SAFE_FINALIZE_ERROR
        await ctx.send(sender, RejectPayment(reason=reason))
        await _send_chat(ctx, sender, reason)
        return

    await ctx.send(sender, CompletePayment(transaction_id=transaction_id))
    ctx.storage.remove(_pending_key(checkout_session_id))
    await _send_chat(ctx, sender, settlement_status.final_user_prompt)


def _register_settlement_handlers(agent: Agent, orchestrator_address: str) -> None:
    @settlement_protocol.on_message(
        model=PaymentSetupRequestMessage,
        replies=PaymentSetupResponseMessage,
    )
    async def on_payment_setup(
        ctx: Context,
        sender: str,
        msg: PaymentSetupRequestMessage,
    ) -> None:
        payment_trace(
            ctx.logger,
            "treasury.setup.received",
            session_id=msg.session_id,
            sender=sender,
        )
        try:
            shipment, econ, route, docs = _parse_payment_payload(
                shipment=msg.shipment,
                econ_data=msg.econ_data,
                route_data=msg.route_data,
                doc_templates=msg.doc_templates,
            )
        except ValidationError:
            ctx.logger.error("Payment setup validation failed")
            await ctx.send(
                sender,
                PaymentSetupResponseMessage(
                    ok=False,
                    session_id=msg.session_id,
                    error=_SAFE_VALIDATION_ERROR,
                ),
            )
            return

        setup = await _create_checkout_setup(
            user_address=msg.user_address,
            session_id=msg.session_id,
            shipment=shipment,
            econ=econ,
            route=route,
            docs=docs,
            orchestrator_address=orchestrator_address or sender,
        )
        if not setup["ok"]:
            await ctx.send(
                sender,
                PaymentSetupResponseMessage(
                    ok=False,
                    session_id=msg.session_id,
                    error=setup.get("error", _SAFE_PAYMENT_SETUP_ERROR),
                ),
            )
            return

        _store_pending_settlement(
            ctx,
            pending=setup["pending"],
            checkout_session_id=setup["checkout_session_id"],
            user_address=msg.user_address,
        )
        response = PaymentSetupResponseMessage(
            ok=True,
            session_id=msg.session_id,
            checkout=setup["checkout"],
            fee_usd=setup["fee_usd"],
        )
        response_summary = summarize_checkout(response.checkout)
        payment_trace(
            ctx.logger,
            "treasury.setup.response_built",
            session_id=msg.session_id,
            sender=sender,
            fee_usd=setup["fee_usd"],
            response_model_class=type(response).__name__,
            response_ok=response.ok,
            checkout_key_names=response_summary["checkout_key_names"],
            ui_mode=response_summary["ui_mode"],
            has_client_secret=response_summary["has_client_secret"],
            has_id=response_summary["has_id"],
            has_checkout_session_id=response_summary["has_checkout_session_id"],
        )
        await ctx.send(sender, response)
        payment_trace(
            ctx.logger,
            "treasury.setup.response_sent",
            session_id=msg.session_id,
            sender=sender,
            response_model_class=type(response).__name__,
            response_ok=response.ok,
        )

    @settlement_protocol.on_message(
        model=PaymentFinalizeRequestMessage,
        replies=PaymentFinalizeResponseMessage,
    )
    async def on_payment_finalize(
        ctx: Context,
        sender: str,
        msg: PaymentFinalizeRequestMessage,
    ) -> None:
        ok, settlement_status, error = await execute_settlement_finalization(
            ctx,
            checkout_session_id=msg.checkout_session_id,
            transaction_id=msg.transaction_id,
            expected_session_id=msg.session_id,
            expected_user_address=msg.user_address,
        )
        if not ok or settlement_status is None:
            await ctx.send(
                sender,
                PaymentFinalizeResponseMessage(
                    ok=False,
                    session_id=msg.session_id,
                    error=error or _SAFE_FINALIZE_ERROR,
                ),
            )
            return

        ctx.storage.remove(_pending_key(msg.checkout_session_id))
        await ctx.send(
            sender,
            PaymentFinalizeResponseMessage(
                ok=True,
                session_id=msg.session_id,
                settlement_status=settlement_status.model_dump(),
            ),
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
        registration_policy=mailbox_registration_policy(),
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
