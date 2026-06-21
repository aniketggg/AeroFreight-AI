import os
from pathlib import Path

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from quote_models import QuoteRequest, QuoteResponse
from route_logic import RoutingRequest, build_air_quote


# Load the private AIR agent seed from step3_riya/.env
load_dotenv(Path(__file__).with_name(".env"))

AIR_AGENT_SEED = os.getenv("AIR_AGENT_SEED")

if not AIR_AGENT_SEED:
    raise RuntimeError("AIR_AGENT_SEED is missing from .env")


# This is a genuine Fetch.ai uAgent with its own identity.
air_agent = Agent(
    name="aerofreight-air-subagent",
    seed=AIR_AGENT_SEED,
)


air_quote_protocol = Protocol(
    name="AeroFreightAirQuoteProtocol",
    version="1.0.0",
)


@air_quote_protocol.on_message(
    model=QuoteRequest,
    replies=QuoteResponse,
)
async def handle_air_quote(
    ctx: Context,
    sender: str,
    msg: QuoteRequest,
) -> None:
    ctx.logger.info(f"Received AIR quote request from {sender}")

    try:
        request = RoutingRequest.model_validate(
            {
                "shipment": msg.shipment,
                "econ": msg.econ,
            }
        )

        quote = build_air_quote(request)

        subtotal = round(
            quote.freight_cost_usd
            + quote.inland_trucking_cost_usd
            + quote.tolls_and_route_tariffs_usd,
            2,
        )

        ctx.logger.info(
            "AIR quote: %s | $%.2f",
            " -> ".join(quote.route_nodes),
            subtotal,
        )

        await ctx.send(
            sender,
            QuoteResponse(
                ok=True,
                mode="AIR",
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
        ctx.logger.exception("AIR quote calculation failed")

        await ctx.send(
            sender,
            QuoteResponse(
                ok=False,
                mode="AIR",
                error=str(exc),
            ),
        )


air_agent.include(air_quote_protocol)


if __name__ == "__main__":
    air_agent.run()
