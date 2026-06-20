"""AeroFreight Orchestrator — the brain of the swarm.

Hosts the ASI:One **Agent Chat Protocol** (so it's discoverable/usable from the
ASI:One chat interface), parses the natural-language request, fans out to the
Tariff and Freight-Router agents **in parallel**, synthesizes the numbers,
asks the Escrow agent to mint a contract, registers a Bill of Lading with the
mock API, and replies with a ready-to-sign Markdown plan.
"""

from __future__ import annotations

import asyncio
import os

import httpx
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

from agents.config import (
    API_BASE_URL,
    ESCROW_ADDRESS,
    FREIGHT_ADDRESS,
    ORCH_SEED,
    SUBAGENT_TIMEOUT,
    TARIFF_ADDRESS,
)
from agents.messages import (
    EscrowRequest,
    EscrowResponse,
    FreightRequest,
    FreightResponse,
    ShipmentSpec,
    TariffRequest,
    TariffResponse,
)
from agents.parser import parse_request

# Publishing the protocol manifest hits Agentverse/almanac (network). Keep it OFF
# for the offline local demo; the Agentverse deploy sets it ON via this env var.
_PUBLISH_MANIFEST = os.getenv("AEROFREIGHT_PUBLISH_MANIFEST", "false").lower() == "true"
# mailbox=True makes the orchestrator reachable from ASI:One (set for deploy).
_USE_MAILBOX = os.getenv("AEROFREIGHT_MAILBOX", "false").lower() == "true"

orchestrator = Agent(
    name="aerofreight-orchestrator",
    seed=ORCH_SEED,
    mailbox=_USE_MAILBOX,
)

chat_proto = Protocol(spec=chat_protocol_spec)


def _say(text: str) -> ChatMessage:
    return ChatMessage(content=[TextContent(text=text)])


async def _call(ctx: Context, address: str, message, response_type):
    """send_and_receive helper that returns the typed reply or None on timeout."""
    reply, _status = await ctx.send_and_receive(
        address, message, response_type=response_type, timeout=SUBAGENT_TIMEOUT
    )
    return reply


def _render_plan(
    spec: ShipmentSpec,
    tariff: TariffResponse,
    freight: FreightResponse,
    escrow: EscrowResponse,
    total: float,
    savings: float,
) -> str:
    """Compose the final, human-facing Markdown logistics plan."""
    legs_md = "\n".join(
        f"- {'✈️ Air' if leg.mode == 'air' else '🚚 Ground'}: "
        f"{leg.from_node} → {leg.to_node} ({leg.carrier} {leg.service})"
        for leg in freight.legs
    )
    deadline_line = (
        f"✅ ETA **{freight.eta_iso}** beats the **{spec.deadline_iso}** deadline."
        if freight.meets_deadline
        else f"⚠️ ETA **{freight.eta_iso}** misses the **{spec.deadline_iso}** deadline."
    )
    budget_line = (
        f"**Total: ${total:,.2f}** (Budget ${spec.budget_usd:,.2f} — "
        f"{'Saved' if savings >= 0 else 'Over by'} ${abs(savings):,.2f})"
    )

    return f"""✅ **Logistics Plan Ready!** We {'beat' if (savings >= 0 and freight.meets_deadline) else 'reviewed'} your deadline and budget.

**Itinerary** (Transit: {freight.transit_days} days)
{legs_md}
{deadline_line}

**Compliance**
- 📋 HS Code: **{tariff.hs_code}** ({tariff.description})
- 💰 US Customs Duty: **{tariff.duty_rate_pct}%** cleared (${tariff.duty_usd:,.2f} on ${spec.declared_value_usd:,.0f} declared)

**Financials**
- Freight: ${freight.total_cost_usd:,.2f}
- Customs Duty: ${tariff.duty_usd:,.2f}
- {budget_line}

🔗 **Review & authorize:** {escrow.payment_link}
Smart contract `{escrow.contract_id}` is staged. Once authorized, funds lock in escrow and carriers dispatch automatically."""


async def _register_bol(
    ctx: Context,
    spec: ShipmentSpec,
    tariff: TariffResponse,
    freight: FreightResponse,
    escrow: EscrowResponse,
    total: float,
    savings: float,
    vendor: str,
    shipment_ref: str,
) -> None:
    """POST the full Bill-of-Lading record so escrow.html can render it."""
    record = {
        "contract_id": escrow.contract_id,
        "status": "escrow_pending",
        "shipment_ref": shipment_ref,
        "origin": spec.origin,
        "destination": spec.destination,
        "weight_kg": spec.weight_kg,
        "commodity": spec.commodity,
        "hs_code": tariff.hs_code,
        "duty_rate_pct": tariff.duty_rate_pct,
        "duty_usd": tariff.duty_usd,
        "freight_usd": freight.total_cost_usd,
        "total_usd": total,
        "budget_usd": spec.budget_usd,
        "savings_usd": savings,
        "transit_days": freight.transit_days,
        "eta_iso": freight.eta_iso,
        "deadline_iso": spec.deadline_iso,
        "meets_deadline": freight.meets_deadline,
        "vendor": vendor,
        "legs": [leg.model_dump() for leg in freight.legs],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{API_BASE_URL}/bol", json=record)
    except Exception as exc:  # noqa: BLE001 — BoL registration is best-effort
        ctx.logger.warning(f"Could not register Bill of Lading: {exc!r}")


@chat_proto.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    text = msg.text()
    # 1) Acknowledge immediately (required by the chat protocol).
    await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
    ctx.logger.info(f"Analyzing logistics request: {text[:90]}")

    # 2) Parse intent.
    spec = parse_request(text)
    ctx.logger.info(
        f"Parsed -> {spec.origin}->{spec.destination} {spec.weight_kg}kg "
        f"'{spec.commodity}' budget ${spec.budget_usd:,.0f} by {spec.deadline_iso}"
    )
    await ctx.send(sender, _say("🛰️ Spawning **Tariff** and **Freight-Router** agents..."))

    # 3) Fan out to Tariff + Freight in PARALLEL.
    tariff, freight = await asyncio.gather(
        _call(
            ctx,
            TARIFF_ADDRESS,
            TariffRequest(commodity=spec.commodity, declared_value_usd=spec.declared_value_usd),
            TariffResponse,
        ),
        _call(
            ctx,
            FREIGHT_ADDRESS,
            FreightRequest(
                origin=spec.origin,
                destination=spec.destination,
                weight_kg=spec.weight_kg,
                deadline_iso=spec.deadline_iso,
            ),
            FreightResponse,
        ),
    )
    if tariff is None or freight is None:
        await ctx.send(sender, _say("⚠️ A sub-agent did not respond in time. Please retry."))
        return
    ctx.logger.info(
        f"Tariff HS {tariff.hs_code} @ {tariff.duty_rate_pct}% | "
        f"Freight ${freight.total_cost_usd:,.0f} in {freight.transit_days}d"
    )

    # 4) Financial synthesis.
    total = round(freight.total_cost_usd + tariff.duty_usd, 2)
    savings = round(spec.budget_usd - total, 2)
    vendor = freight.legs[0].carrier if freight.legs else "Carrier"
    shipment_ref = f"{spec.origin}-{spec.destination}-{int(spec.weight_kg)}KG"
    await ctx.send(sender, _say("🔐 Routes & tariffs verified. Preparing smart escrow contract..."))

    # 5) Escrow.
    escrow = await _call(
        ctx,
        ESCROW_ADDRESS,
        EscrowRequest(total_usd=total, vendor=vendor, shipment_ref=shipment_ref),
        EscrowResponse,
    )
    if escrow is None:
        await ctx.send(sender, _say("⚠️ Escrow agent did not respond in time. Please retry."))
        return

    # 6) Register the Bill of Lading for the success page, then reply.
    await _register_bol(ctx, spec, tariff, freight, escrow, total, savings, vendor, shipment_ref)
    await ctx.send(sender, _say(_render_plan(spec, tariff, freight, escrow, total, savings)))


@chat_proto.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    # Counterparty acknowledged one of our messages — nothing to do.
    pass


orchestrator.include(chat_proto, publish_manifest=_PUBLISH_MANIFEST)


if __name__ == "__main__":
    orchestrator.run()
