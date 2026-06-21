import os
from pathlib import Path

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from quote_models import QuoteRequest, QuoteResponse
from route_logic import RoutingRequest, build_ship_quote


# Load the private SHIP agent seed from step3_riya/.env
load_dotenv(Path(__file__).with_name(".env"))

SHIP_AGENT_SEED = os.getenv("SHIP_AGENT_SEED")

if not SHIP_AGENT_SEED:
    raise RuntimeError("SHIP_AGENT_SEED is missing from .env")


# This is a genuine Fetch.ai uAgent with its own identity.
ship_agent = Agent(
    name="aerofreight-ship-subagent",
    seed=SHIP_AGENT_SEED,
)


ship_quote_protocol = Protocol(
    name="AeroFreightShipQuoteProtocol",
    version="1.0.0",
)


@ship_quote_protocol.on_message(
    model=QuoteRequest,
    replies=QuoteResponse,
)
async def handle_ship_quote(
    ctx: Context,
    sender: str,
    msg: QuoteRequest,
) -> None:
    ctx.logger.info(f"Received SHIP quote request from {sender}")

    try:
        request = RoutingRequest.model_validate(
            {
                "shipment": msg.shipment,
                "econ": msg.econ,
            }
        )

        quote = build_ship_quote(request)

        subtotal = round(
            quote.freight_cost_usd
            + quote.inland_trucking_cost_usd
            + quote.tolls_and_route_tariffs_usd,
            2,
        )

        ctx.logger.info(
            "SHIP quote: %s | $%.2f",
            " -> ".join(quote.route_nodes),
            subtotal,
        )

        await ctx.send(
            sender,
            QuoteResponse(
                ok=True,
                mode="SHIP",
                optimal_route_nodes=quote.route_nodes,
                countries_visited=quote.countries_visited,
                freight_cost_usd=quote.freight_cost_usd,
                inland_trucking_cost_usd=(
                    quote.inland_trucking_cost_usd
                ),
                tolls_and_route_tariffs_usd=(
                    quote.tolls_and_route_tariffs_usd
                ),
                freight_and_toll_cost_usd=subtotal,
                estimated_transit_days=quote.estimated_transit_days,
            ),
        )

    except Exception as exc:
        ctx.logger.exception("SHIP quote calculation failed")

        await ctx.send(
            sender,
            QuoteResponse(
                ok=False,
                mode="SHIP",
                error=str(exc),
            ),
        )


ship_agent.include(ship_quote_protocol)


if __name__ == "__main__":
    ship_agent.run()
