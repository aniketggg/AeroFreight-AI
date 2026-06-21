import os
from pathlib import Path

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from step3_riya.quote_models import QuoteRequest, QuoteResponse
from step3_riya.route_logic import build_ship_quote
from step3_riya.routing_models import RoutingRequest


class ShipAgentConfigurationError(Exception):
    """Raised when SHIP sub-agent configuration is missing or invalid."""


ship_quote_protocol = Protocol(
    name="AeroFreightShipQuoteProtocol",
    version="1.0.0",
)


def _require_ship_seed() -> str:
    load_dotenv(Path(__file__).with_name(".env"))
    seed = os.getenv("SHIP_AGENT_SEED", "").strip()
    if not seed:
        raise ShipAgentConfigurationError(
            "SHIP_AGENT_SEED is not configured. "
            "Set it in step3_riya/.env or the environment."
        )
    return seed


def _register_ship_handlers(agent: Agent) -> None:
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

    agent.include(ship_quote_protocol)


def create_ship_agent(*, seed: str | None = None) -> Agent:
    """Create and configure the SHIP quote sub-agent."""
    agent = Agent(
        name="aerofreight-ship-subagent",
        seed=seed or _require_ship_seed(),
    )
    _register_ship_handlers(agent)
    return agent


def main() -> None:
    agent = create_ship_agent()
    print(f"SHIP sub-agent address: {agent.address}")
    agent.run()


if __name__ == "__main__":
    main()
