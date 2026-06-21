"""
AeroFreight AI -- Neel's Settlement & Payment Agent (single-file build).

This is the SAME logic as models.py + pricing.py + payment_backend.py +
payment_proto.py + settlement_agent.py, flattened into one file because the
Agentverse Hosted Agent editor runs a single script. If you're running
locally or with a local Mailbox instead, use the modular version
(run_pricing_agent.py) -- it's easier to read and edit.

Deploy steps (Agentverse Hosted Agent):
  1. Agentverse -> Agents -> + Launch an Agent -> Blank Agent.
  2. Open the Build tab's code editor and paste this entire file in as agent.py.
  3. Open the Agent's "Secrets" / environment settings and add:
       STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY
     (and optionally STRIPE_CURRENCY, STRIPE_SUCCESS_URL,
     STRIPE_CHECKOUT_EXPIRES_SECONDS, ORCHESTRATOR_AGENT_ADDRESS).
  4. Add "stripe" to the agent's package requirements if prompted (uagents,
     uagents-core, and python-dotenv are already available on Hosted Agents).
  5. Click Start. The agent is automatically registered and discoverable.
  6. In ASI:One, find/chat with the agent and send "DEMO" to test the full
     pricing + checkout + verification flow without the rest of the pipeline.

See README.md for getting Stripe keys and for the local/Mailbox path.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from uagents import Agent, Context, Model, Protocol
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
    payment_protocol_spec,
)

try:
    import stripe
except ImportError:  # pragma: no cover
    stripe = None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ShipmentRequest(Model):
    origin_country: str
    destination_city: str
    weight_kg: float
    volume_cbm: float
    declared_value_usd: float
    timeframe_preference: str
    destination_zip: Optional[str] = None
    goods_category: Optional[str] = None


class EconData(Model):
    is_high_value: bool
    entry_tax_usd: float
    mpf_usd: float
    allowed_modes: List[str]


class RouteData(Model):
    selected_mode: str
    freight_cost_usd: float
    tolls_tariffs_usd: float
    inland_cost_usd: float
    total_cost_usd: float
    baseline_cost_usd: float
    countries_visited: List[str]


class DocTemplates(Model):
    doc_names: List[str]
    doc_bodies: Dict[str, str]


class SettlementRequest(Model):
    user_address: str
    session_id: str
    shipment: ShipmentRequest
    econ: EconData
    route: RouteData
    docs: DocTemplates


class SettlementStatus(Model):
    session_id: str
    status: str
    fee_usd: float
    transaction_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Pricing: dynamic, value-anchored service fee
# ---------------------------------------------------------------------------

FEE_PCT_OF_SAVINGS = 0.10
FLOOR_FEE_USD = 4.99
CEILING_FEE_USD = 250.00
COMPLEXITY_SURCHARGE_PER_EXTRA_COUNTRY = 1.50
HIGH_VALUE_SURCHARGE_USD = 5.00


@dataclass
class FeeBreakdown:
    baseline_cost_usd: float
    optimized_cost_usd: float
    savings_usd: float
    base_fee_usd: float
    complexity_surcharge_usd: float
    high_value_surcharge_usd: float
    total_fee_usd: float

    def as_markdown(self) -> str:
        lines = [
            f"- Baseline (naive single-mode) cost: **${self.baseline_cost_usd:,.2f}**",
            f"- Optimized cost (this route): **${self.optimized_cost_usd:,.2f}**",
            f"- Savings found by the agent: **${self.savings_usd:,.2f}**",
            f"- Service fee (10% of savings, min ${FLOOR_FEE_USD:.2f}): "
            f"**${self.base_fee_usd:,.2f}**",
        ]
        if self.complexity_surcharge_usd:
            lines.append(
                f"- Multi-country documentation surcharge: "
                f"**${self.complexity_surcharge_usd:,.2f}**"
            )
        if self.high_value_surcharge_usd:
            lines.append(
                f"- High-value handling surcharge: **${self.high_value_surcharge_usd:,.2f}**"
            )
        lines.append(f"- **Total service fee: ${self.total_fee_usd:,.2f}**")
        return "\n".join(lines)


def compute_service_fee(econ: EconData, route: RouteData) -> FeeBreakdown:
    baseline = max(route.baseline_cost_usd, route.total_cost_usd)
    optimized = route.total_cost_usd
    savings = max(0.0, baseline - optimized)
    base_fee = max(FLOOR_FEE_USD, savings * FEE_PCT_OF_SAVINGS)
    extra_countries = max(0, len(route.countries_visited) - 1)
    complexity_surcharge = extra_countries * COMPLEXITY_SURCHARGE_PER_EXTRA_COUNTRY
    high_value_surcharge = HIGH_VALUE_SURCHARGE_USD if econ.is_high_value else 0.0
    total = min(CEILING_FEE_USD, base_fee + complexity_surcharge + high_value_surcharge)
    return FeeBreakdown(
        baseline_cost_usd=round(baseline, 2),
        optimized_cost_usd=round(optimized, 2),
        savings_usd=round(savings, 2),
        base_fee_usd=round(base_fee, 2),
        complexity_surcharge_usd=round(complexity_surcharge, 2),
        high_value_surcharge_usd=round(high_value_surcharge, 2),
        total_fee_usd=round(total, 2),
    )


# ---------------------------------------------------------------------------
# Stripe backend: sells the service, never the shipment's value or its tax
# ---------------------------------------------------------------------------


def _stripe_cfg() -> dict:
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


def stripe_is_configured() -> bool:
    c = _stripe_cfg()
    return bool(stripe and c["secret_key"] and c["publishable_key"])


def _stripe_client():
    if not stripe:
        return None
    stripe.api_key = _stripe_cfg()["secret_key"]
    return stripe


def _expires_at(seconds: int) -> int:
    seconds = max(1800, min(24 * 3600, seconds))
    return int(time.time()) + seconds


def create_settlement_checkout(
    *, user_address: str, session_id: str, amount_usd: float, description: str
) -> Optional[dict]:
    if not stripe_is_configured():
        return None
    s = _stripe_client()
    if not s:
        return None
    c = _stripe_cfg()
    amount_cents = int(round(amount_usd * 100))
    try:
        return_url = (
            f"{c['success_url']}?session_id={{CHECKOUT_SESSION_ID}}"
            f"&aerofreight_session={session_id}&user={user_address}"
        )
        session = s.checkout.Session.create(
            ui_mode="embedded",
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
            "ui_mode": "embedded",
        }
    except Exception:
        return None


def resolve_checkout_session_id(transaction_ref: str) -> str:
    ref = (transaction_ref or "").strip()
    if not ref or not stripe_is_configured() or ref.startswith("cs_"):
        return ref
    if not ref.startswith("pi_"):
        return ref
    s = _stripe_client()
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
    if not stripe_is_configured():
        return False
    s = _stripe_client()
    if not s:
        return False
    try:
        session = s.checkout.Session.retrieve(checkout_session_id)
        return getattr(session, "payment_status", None) == "paid"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Agent setup
# ---------------------------------------------------------------------------

AGENT_NAME = os.getenv("AGENT_NAME", "aerofreight-settlement-agent")
AGENT_SEED = os.getenv("AGENT_SEED_PHRASE", "aerofreight-settlement-agent-seed")
ORCHESTRATOR_ADDRESS = os.getenv("ORCHESTRATOR_AGENT_ADDRESS", "").strip()

agent = Agent(name=AGENT_NAME, seed=AGENT_SEED, mailbox=True)

chat_proto = Protocol(spec=chat_protocol_spec)
payment_proto = Protocol(spec=payment_protocol_spec, role="seller")

STORAGE_PREFIX = "pending_settlement:"
_CHECKOUT_CONFIRM_RE = re.compile(r"<stripe:payment_id:([^:>]+):CONFIRM>")


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


def _render_doc_package(docs: DocTemplates, route: RouteData, econ: EconData) -> str:
    lines = ["**Final route cost breakdown**", ""]
    lines.append(f"- Selected mode: **{route.selected_mode}**")
    lines.append(f"- Countries visited: {', '.join(route.countries_visited)}")
    lines.append(f"- Freight cost: **${route.freight_cost_usd:,.2f}**")
    lines.append(f"- Tolls / tariffs: **${route.tolls_tariffs_usd:,.2f}**")
    lines.append(f"- Inland trucking: **${route.inland_cost_usd:,.2f}**")
    lines.append(f"- **Total route cost: ${route.total_cost_usd:,.2f}**")
    lines.append(
        f"- Estimated entry tax (remit via your customs broker, not paid by this agent): "
        f"**${econ.entry_tax_usd:,.2f}**"
    )
    lines.append("")
    lines.append("**Completed document package**")
    lines.append("")
    for name in docs.doc_names:
        body = docs.doc_bodies.get(name, "(template)")
        lines.append(f"### {name}")
        lines.append(f"```\n{body}\n```")
    return "\n".join(lines)


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
            "doc_package_markdown": _render_doc_package(docs, route, econ),
        },
    )
    ctx.storage.set(_pending_by_sender_key(user_address), checkout_session_id)

    summary = (
        f"**Route confirmed: {route.selected_mode}** via {', '.join(route.countries_visited)}\n\n"
        f"{fee.as_markdown()}\n\n"
        "Complete the checkout above to receive your finished route summary and compliance "
        "document package. This fee covers the optimization and document automation service "
        "-- it does not include or hold your shipment's value, and it does not pay your "
        "entry tax on your behalf."
    )
    await _send_chat(ctx, user_address, summary)

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


async def _finalize_checkout(
    ctx: Context, sender: str, checkout_session_id: str, transaction_id: str
) -> None:
    pending = ctx.storage.get(_pending_key(checkout_session_id))
    if not pending:
        await ctx.send(
            sender,
            RejectPayment(
                reason="No matching payment request found (expired or already settled)."
            ),
        )
        return

    paid = await asyncio.to_thread(verify_checkout_paid, checkout_session_id)
    if not paid:
        await ctx.send(
            sender,
            RejectPayment(
                reason="Stripe payment not completed yet. Please finish checkout and resend confirmation."
            ),
        )
        return

    await ctx.send(sender, CompletePayment(transaction_id=transaction_id))
    ctx.storage.remove(_pending_key(checkout_session_id))

    await _send_chat(
        ctx,
        pending["user_address"],
        f"Payment received (${pending['fee_usd']:.2f}). Here is your completed package:\n\n"
        f"{pending['doc_package_markdown']}",
    )

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


@payment_proto.on_message(CommitPayment)
async def on_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
    if getattr(msg.funds, "payment_method", None) != "stripe" or not getattr(
        msg, "transaction_id", None
    ):
        await ctx.send(
            sender, RejectPayment(reason="Unsupported payment method (expected stripe).")
        )
        return
    checkout_id = await asyncio.to_thread(resolve_checkout_session_id, msg.transaction_id)
    await _finalize_checkout(ctx, sender, checkout_id, msg.transaction_id)


@payment_proto.on_message(RejectPayment)
async def on_payment_reject(ctx: Context, sender: str, msg: RejectPayment):
    ctx.logger.info("Payment rejected by %s: %s", sender, msg.reason)


# ---------------------------------------------------------------------------
# Demo data, for testing this agent standalone before the rest of the
# pipeline (Orchestrator/Ashwin/Riya/Aniket) is wired up
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

    text = _extract_text(msg).strip()

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
        "I'm the AeroFreight Settlement & Payment agent. I'm normally invoked by the "
        "Orchestrator once a route is confirmed. Send 'DEMO' to see a sample settlement "
        "and payment flow on a test shipment.",
    )


@chat_proto.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


agent.include(chat_proto, publish_manifest=True)
agent.include(payment_proto, publish_manifest=True)


if __name__ == "__main__":
    print("AeroFreight Settlement & Payment agent address:", agent.address)
    agent.run()