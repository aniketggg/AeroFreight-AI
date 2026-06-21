from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic.v1 import Field
from uagents import Agent, Context, Model, Protocol

# Allow imports from the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Exact shared schemas supplied in the original workflow.
from schemas import EconData, RouteData, ShipmentRequest

from air_agent import air_agent
from quote_models import QuoteRequest, QuoteResponse
from ship_agent import ship_agent


# ---------------------------------------------------------------------------
# Environment and identity
# ---------------------------------------------------------------------------

load_dotenv(Path(__file__).with_name(".env"))

RIYA_AGENT_SEED = os.getenv("RIYA_AGENT_SEED")

if not RIYA_AGENT_SEED:
    raise RuntimeError("RIYA_AGENT_SEED is missing from step3_riya/.env")


riya_agent = Agent(
    name="aerofreight-riya-routing",
    seed=RIYA_AGENT_SEED,
)


# ---------------------------------------------------------------------------
# Messages exchanged with the central Orchestrator
# ---------------------------------------------------------------------------

class RouteRequestMessage(Model):
    """
    Message sent by the central Orchestrator to Riya.

    shipment must match ShipmentRequest.
    econ must match EconData.
    """

    shipment: dict[str, Any]
    econ: dict[str, Any]


class RouteResponseMessage(Model):
    """
    Riya's response to the central Orchestrator.

    route_data matches the exact RouteData schema from schemas.py.
    """

    ok: bool
    route_data: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


routing_protocol = Protocol(
    name="AeroFreightRoutingProtocol",
    version="1.0.0",
)


async def request_transport_quote(
    ctx: Context,
    agent_address: str,
    request: QuoteRequest,
    mode_name: str,
) -> QuoteResponse:
    """
    Send a real Fetch.ai message to AIR or SHIP and wait for its response.
    """

    ctx.logger.info(f"Requesting {mode_name} quote from {agent_address}")

    response, status = await ctx.send_and_receive(
        agent_address,
        request,
        response_type=QuoteResponse,
    )

    if not isinstance(response, QuoteResponse):
        raise RuntimeError(
            f"{mode_name} agent did not return a valid quote. "
            f"Status: {status}"
        )

    if not response.ok:
        raise RuntimeError(
            response.error or f"{mode_name} quote calculation failed."
        )

    return response


def select_quote(
    quotes: list[QuoteResponse],
    timeframe: str,
) -> QuoteResponse:
    """
    Apply the Step 3 decision matrix.

    SPEED chooses the fastest permitted quote.
    COST chooses the cheapest permitted quote.
    """

    if not quotes:
        raise RuntimeError("No valid transport quotes were returned.")

    if timeframe == "SPEED":
        return min(
            quotes,
            key=lambda quote: quote.estimated_transit_days,
        )

    return min(
        quotes,
        key=lambda quote: quote.freight_and_toll_cost_usd,
    )


@routing_protocol.on_message(
    model=RouteRequestMessage,
    replies=RouteResponseMessage,
)
async def handle_routing_request(
    ctx: Context,
    sender: str,
    msg: RouteRequestMessage,
) -> None:
    """
    Exact Step 3 flow:

    1. Receive ShipmentRequest + EconData from the Orchestrator.
    2. Obey Ashwin's transport_preference.
    3. Request quotes from permitted Fetch.ai sub-agents.
    4. Select the route using timeframe.
    5. Add Ashwin's entry tax.
    6. Return the exact RouteData contract.
    """

    ctx.logger.info(f"Received routing request from {sender}")

    try:
        # Validate against the exact shared contracts.
        shipment = ShipmentRequest.model_validate(msg.shipment)
        econ = EconData.model_validate(msg.econ)

        destination_country = str(
            shipment.destination.get("country", "")
        ).upper()

        if destination_country != "US":
            raise ValueError(
                "Step 3 only supports international shipments "
                "with a United States destination."
            )

        quote_request = QuoteRequest(
            shipment=shipment.model_dump(),
            econ=econ.model_dump(),
        )

        quotes: list[QuoteResponse] = []

        # Obey Ashwin's exact constraint.
        if econ.transport_preference == "AIR":
            air_quote = await request_transport_quote(
                ctx,
                str(air_agent.address),
                quote_request,
                "AIR",
            )
            quotes.append(air_quote)

        elif econ.transport_preference == "SHIP":
            ship_quote = await request_transport_quote(
                ctx,
                str(ship_agent.address),
                quote_request,
                "SHIP",
            )
            quotes.append(ship_quote)

        elif econ.transport_preference == "EITHER":
            # Contact both real Fetch.ai transport agents.
            air_quote = await request_transport_quote(
                ctx,
                str(air_agent.address),
                quote_request,
                "AIR",
            )

            ship_quote = await request_transport_quote(
                ctx,
                str(ship_agent.address),
                quote_request,
                "SHIP",
            )

            quotes.extend([air_quote, ship_quote])

        else:
            raise ValueError(
                f"Unsupported transport preference: "
                f"{econ.transport_preference}"
            )

        selected = select_quote(
            quotes=quotes,
            timeframe=shipment.timeframe,
        )

        transport_cost = round(
            selected.freight_and_toll_cost_usd,
            2,
        )

        total_landed_cost = round(
            transport_cost + econ.base_entry_tax_usd,
            2,
        )

        # Return exactly the original RouteData fields.
        route_data = RouteData(
            selected_mode=selected.mode,
            optimal_route_nodes=selected.optimal_route_nodes,
            countries_visited=selected.countries_visited,
            freight_and_toll_cost_usd=transport_cost,
            total_landed_cost_usd=total_landed_cost,
        )

        ctx.logger.info(
            "Selected %s route | transport $%.2f | landed $%.2f",
            route_data.selected_mode,
            route_data.freight_and_toll_cost_usd,
            route_data.total_landed_cost_usd,
        )

        await ctx.send(
            sender,
            RouteResponseMessage(
                ok=True,
                route_data=route_data.model_dump(),
            ),
        )

    except Exception as exc:
        ctx.logger.exception("Step 3 routing failed")

        await ctx.send(
            sender,
            RouteResponseMessage(
                ok=False,
                error=str(exc),
            ),
        )


riya_agent.include(routing_protocol)


@riya_agent.on_event("startup")
async def startup(ctx: Context) -> None:
    ctx.logger.info(f"Riya agent address: {riya_agent.address}")
    ctx.logger.info(f"AIR sub-agent address: {air_agent.address}")
    ctx.logger.info(f"SHIP sub-agent address: {ship_agent.address}")


if __name__ == "__main__":
    riya_agent.run()
