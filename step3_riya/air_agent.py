import os
from pathlib import Path

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from step3_riya.quote_models import QuoteRequest, QuoteResponse
from step3_riya.route_logic import build_air_quote
from step3_riya.routing_models import RoutingRequest


class AirAgentConfigurationError(Exception):
    """Raised when AIR sub-agent configuration is missing or invalid."""


air_quote_protocol = Protocol(
    name="AeroFreightAirQuoteProtocol",
    version="1.0.0",
)


def _require_air_seed() -> str:
    load_dotenv(Path(__file__).with_name(".env"))
    seed = os.getenv("AIR_AGENT_SEED", "").strip()
    if not seed:
        raise AirAgentConfigurationError(
            "AIR_AGENT_SEED is not configured. "
            "Set it in step3_riya/.env or the environment."
        )
    return seed


def _register_air_handlers(agent: Agent) -> None:
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

    agent.include(air_quote_protocol)


def create_air_agent(*, seed: str | None = None) -> Agent:
    """Create and configure the AIR quote sub-agent."""
    agent = Agent(
        name="aerofreight-air-subagent",
        seed=seed or _require_air_seed(),
        mailbox=True,
        publish_agent_details=True,
        enable_agent_inspector=False,
    )
    _register_air_handlers(agent)
    return agent


def main() -> None:
    agent = create_air_agent()
    print(f"AIR sub-agent address: {agent.address}")
    agent.run()


if __name__ == "__main__":
    main()
