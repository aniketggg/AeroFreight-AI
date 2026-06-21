"""
AeroFreight AI -- Neel's Settlement & Payment Agent (standalone).

Responsibilities
----------------
1. Receive a SettlementRequest (e.g. from an Orchestrator, or trigger it
   yourself via the DEMO command in chat) once a route has been confirmed.
2. Compute a dynamic, value-anchored service fee (pricing.py) for the route
   optimization + compliance document package -- never a flat constant, and
   never framed as holding the shipment's value or paying its taxes.
3. Create a Stripe embedded Checkout session for that fee (payment_backend.py)
   and send the user a RequestPayment message, inside the same ASI:One
   conversation -- no custom frontend required.
4. On confirmation of payment, verify directly with Stripe (never trust the
   client's claim), then either:
     - send CompletePayment and release the finished document package
       (including the full route cost breakdown), or
     - send RejectPayment with a clear reason and leave the request open so
       the user can retry.

Run locally / with a Mailbox:
    python run_pricing_agent.py
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

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

from models import (
    DocTemplates,
    EconData,
    RouteData,
    SettlementRequest,
    SettlementStatus,
    ShipmentRequest,
)
from payment_backend import (
    create_settlement_checkout,
    is_configured as stripe_is_configured,
    resolve_checkout_session_id,
    verify_checkout_paid,
)
from payment_proto import build_payment_protocol
from pricing import compute_service_fee
from invoice import generate_invoice_pdf
from drive_upload import upload_invoice_and_get_link

AGENT_NAME = os.getenv("AGENT_NAME", "aerofreight-settlement-agent")
AGENT_SEED = os.getenv("AGENT_SEED_PHRASE", "aerofreight-settlement-agent-seed")
AGENT_PORT = int(os.getenv("AGENT_PORT", "8003"))
ORCHESTRATOR_ADDRESS = os.getenv("ORCHESTRATOR_AGENT_ADDRESS", "").strip()

agent = Agent(
    name=AGENT_NAME,
    seed=AGENT_SEED,
    port=AGENT_PORT,
    mailbox=True,
    network="testnet",
)

chat_proto = Protocol(spec=chat_protocol_spec)

STORAGE_PREFIX = "pending_settlement:"
_CHECKOUT_CONFIRM_RE = re.compile(r"<stripe:payment_id:([^:>]+):CONFIRM>")
_MENTION_RE = re.compile(r"^@agent1[a-z0-9]+\s*", re.IGNORECASE)


def _strip_agent_mention(text: str) -> str:
    return _MENTION_RE.sub("", text).strip()


def _pending_key(checkout_session_id: str) -> str:
    return f"{STORAGE_PREFIX}{checkout_session_id}"


def _pending_by_sender_key(sender: str) -> str:
    return f"{STORAGE_PREFIX}by_sender:{sender}"


async def _send_chat(ctx: Context, to: str, text: str) -> None:
    await ctx.send(
        to,
        ChatMessage(
            content=[TextContent(type="text", text=text)],
            msg_id=uuid4(),
            timestamp=datetime.now(timezone.utc),
        ),
    )


# ---------------------------------------------------------------------------
# Step 1: trigger -> compute fee, open Stripe checkout, request payment
# ---------------------------------------------------------------------------


@agent.on_message(model=SettlementRequest)
async def on_settlement_request(ctx: Context, sender: str, msg: SettlementRequest):
    await _start_settlement(
        ctx,
        user_address=msg.user_address,
        session_id=msg.session_id,
        shipment=msg.shipment,
        econ=msg.econ,
        route=msg.route,
        docs=msg.docs,
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
) -> None:
    if not stripe_is_configured():
        await _send_chat(
            ctx,
            user_address,
            "Payment is not configured on this agent right now. The operator needs to "
            "set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY.",
        )
        if ORCHESTRATOR_ADDRESS:
            await ctx.send(
                ORCHESTRATOR_ADDRESS,
                SettlementStatus(session_id=session_id, status="unconfigured", fee_usd=0.0),
            )
        return

    fee = compute_service_fee(econ, route)
    description = (
        f"Shipment {shipment.origin_country} -> {shipment.destination_city}, "
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
        await _send_chat(ctx, user_address, "Payment setup failed. Please try again shortly.")
        return

    checkout_session_id = checkout["checkout_session_id"]

    ctx.storage.set(
        _pending_key(checkout_session_id),
        {
            "user_address": user_address,
            "session_id": session_id,
            "fee_usd": fee.total_fee_usd,
            "shipment": shipment.model_dump(),
            "econ": econ.model_dump(),
            "route": route.model_dump(),
            "docs": docs.model_dump(),
        },
    )
    ctx.storage.set(_pending_by_sender_key(user_address), checkout_session_id)

    await ctx.send(
        user_address,
        RequestPayment(
            accepted_funds=[
                Funds(currency="USD", amount=f"{fee.total_fee_usd:.2f}", payment_method="stripe")
            ],
            recipient=str(ctx.agent.address),
            deadline_seconds=1800,
            reference=session_id,
            description=f"Pay ${fee.total_fee_usd:.2f} to receive your AeroFreight document package.",
            metadata={"stripe": checkout, "service": "aerofreight_settlement_package"},
        ),
    )


# ---------------------------------------------------------------------------
# Step 2: user pays, confirmation arrives -> verify -> release or reject
# ---------------------------------------------------------------------------


async def _finalize_checkout(
    ctx: Context, sender: str, checkout_session_id: str, transaction_id: str
) -> None:
    pending = ctx.storage.get(_pending_key(checkout_session_id))
    if not pending:
        reason = "No matching payment request found (expired or already settled)."
        await ctx.send(sender, RejectPayment(reason=reason))
        await _send_chat(ctx, sender, reason)
        return

    paid = await asyncio.to_thread(verify_checkout_paid, checkout_session_id)
    if not paid:
        reason = "Stripe payment not completed yet. Please finish checkout and resend confirmation."
        await ctx.send(sender, RejectPayment(reason=reason))
        await _send_chat(ctx, sender, reason)
        return

    await ctx.send(sender, CompletePayment(transaction_id=transaction_id))
    ctx.storage.remove(_pending_key(checkout_session_id))

    user_address = pending["user_address"]
    shipment = ShipmentRequest(**pending["shipment"])
    econ = EconData(**pending["econ"])
    route = RouteData(**pending["route"])
    docs = DocTemplates(**pending["docs"])
    fee = compute_service_fee(econ, route)

    invoice_link = None
    try:
        invoice_path = os.path.join(
            tempfile.gettempdir(), f"aerofreight_invoice_{checkout_session_id}.pdf"
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
        import traceback

        traceback.print_exc()

    if invoice_link:
        message = (
            f"Payment received (${pending['fee_usd']:.2f}). Your invoice and document "
            f"package are ready:\n\n{invoice_link}"
        )
    else:
        # Fallback so a Drive/PDF problem never blocks delivery of the result.
        message = (
            f"Payment received (${pending['fee_usd']:.2f}). Total route cost: "
            f"${route.total_cost_usd:,.2f} ({route.selected_mode} via "
            f"{', '.join(route.countries_visited)}). The PDF invoice link is "
            f"temporarily unavailable -- check the agent's logs."
        )

    await _send_chat(ctx, user_address, message)

    if ORCHESTRATOR_ADDRESS:
        await ctx.send(
            ORCHESTRATOR_ADDRESS,
            SettlementStatus(
                session_id=pending["session_id"],
                status="paid",
                fee_usd=pending["fee_usd"],
                transaction_id=transaction_id,
            ),
        )


async def on_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
    if getattr(msg.funds, "payment_method", None) != "stripe" or not getattr(
        msg, "transaction_id", None
    ):
        reason = "Unsupported payment method (expected stripe)."
        await ctx.send(sender, RejectPayment(reason=reason))
        await _send_chat(ctx, sender, reason)
        return
    checkout_id = await asyncio.to_thread(resolve_checkout_session_id, msg.transaction_id)
    await _finalize_checkout(ctx, sender, checkout_id, msg.transaction_id)


async def on_payment_reject(ctx: Context, sender: str, msg: RejectPayment):
    # The human declined/cancelled checkout. Don't clear the pending entry --
    # the Stripe Checkout Session may still be open, so if they come back and
    # actually pay later, the same checkout_session_id still resolves
    # correctly through _finalize_checkout.
    ctx.logger.info("Payment rejected by %s: %s", sender, msg.reason)
    await _send_chat(
        ctx,
        sender,
        "No worries -- checkout cancelled. The same payment link stays open if you "
        "change your mind, or send DEMO again to start a new one.",
    )


# ---------------------------------------------------------------------------
# Chat handling: ack, catch ASI:One's checkout-confirm text trigger, and
# offer a standalone DEMO path so this agent is fully testable on its own.
# ---------------------------------------------------------------------------

_DEMO_SHIPMENT = ShipmentRequest(
    origin_country="Vietnam",
    destination_city="Austin, TX",
    weight_kg=850.0,
    volume_cbm=3.2,
    declared_value_usd=4200.0,
    timeframe_preference="COST",
)
_DEMO_ECON = EconData(
    is_high_value=True, entry_tax_usd=126.50, mpf_usd=27.75, allowed_modes=["AIR", "SHIP"]
)
_DEMO_ROUTE = RouteData(
    selected_mode="SHIP",
    freight_cost_usd=410.0,
    tolls_tariffs_usd=95.0,
    inland_cost_usd=140.0,
    total_cost_usd=771.25,
    baseline_cost_usd=1180.0,
    countries_visited=["Vietnam", "Singapore", "United States"],
)
_DEMO_DOCS = DocTemplates(
    doc_names=["CBP Form 7501", "Bill of Lading"],
    doc_bodies={
        "CBP Form 7501": "Entry Summary (demo placeholder)",
        "Bill of Lading": "B/L No. DEMO-0001 (demo placeholder)",
    },
)


def _extract_text(msg: ChatMessage) -> str:
    return "".join(c.text for c in msg.content if isinstance(c, TextContent))


@chat_proto.on_message(ChatMessage)
async def on_chat_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(
        sender,
        ChatAcknowledgement(acknowledged_msg_id=msg.msg_id, timestamp=datetime.now(timezone.utc)),
    )

    text = _strip_agent_mention(_extract_text(msg).strip())

    confirm_match = _CHECKOUT_CONFIRM_RE.search(text)
    if confirm_match:
        checkout_session_id = ctx.storage.get(_pending_by_sender_key(sender))
        if checkout_session_id:
            await _finalize_checkout(
                ctx, sender, checkout_session_id, transaction_id=confirm_match.group(1)
            )
        return

    if text.upper() == "DEMO":
        await _start_settlement(
            ctx,
            user_address=sender,
            session_id=f"demo-{uuid4().hex[:8]}",
            shipment=_DEMO_SHIPMENT,
            econ=_DEMO_ECON,
            route=_DEMO_ROUTE,
            docs=_DEMO_DOCS,
        )
        return

    await _send_chat(
        ctx,
        sender,
        "I'm the AeroFreight Settlement & Payment agent. Send 'DEMO' to see a sample "
        "settlement and payment flow on a test shipment.",
    )


@chat_proto.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


agent.include(chat_proto, publish_manifest=True)
agent.include(build_payment_protocol(on_payment_commit, on_payment_reject), publish_manifest=True)


if __name__ == "__main__":
    print("AeroFreight Settlement & Payment agent address:", agent.address)
    agent.run()