from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic.v1 import Field
from shared_models import EconData, RouteData, ShipmentRequest
from uagents import Agent, Context, Model, Protocol

from step3_riya.quote_models import QuoteRequest, QuoteResponse


class RoutingConfigurationError(Exception):
    """Raised when routing agent configuration is missing or invalid."""


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

    route_data matches the exact RouteData schema from shared_models.py.
    """

    ok: bool
    route_data: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


routing_protocol = Protocol(
    name="AeroFreightRoutingProtocol",
    version="1.0.0",
)


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RoutingConfigurationError(
            f"{name} is not configured. "
            "Set it in step3_riya/.env or the environment."
        )
    return value


def _load_routing_settings() -> tuple[str, str, str]:
    load_dotenv(Path(__file__).with_name(".env"))
    return (
        _require_env("RIYA_AGENT_SEED"),
        _require_env("AIR_AGENT_ADDRESS"),
        _require_env("SHIP_AGENT_ADDRESS"),
    )


async def request_transport_quote(
    ctx: Context,
    agent_address: str,
    request: QuoteRequest,
    mode_name: str,
) -> QuoteResponse:
    """Send a real Fetch.ai message to AIR or SHIP and wait for its response."""
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
    Rank permitted AIR/SHIP quotes using both cost and time.

    COST: 80% cost, 20% time.
    SPEED: 20% cost, 80% time.
    """
    if not quotes:
        raise RuntimeError(
            "No valid transport quotes were returned."
        )

    costs = [
        quote.freight_and_toll_cost_usd
        for quote in quotes
    ]
    times = [
        quote.estimated_transit_days
        for quote in quotes
    ]

    minimum_cost = min(costs)
    maximum_cost = max(costs)
    minimum_time = min(times)
    maximum_time = max(times)

    def normalize(
        value: float,
        minimum: float,
        maximum: float,
    ) -> float:
        if maximum == minimum:
            return 0.0

        return (value - minimum) / (maximum - minimum)

    if timeframe == "SPEED":
        cost_weight = 0.20
        time_weight = 0.80
    else:
        cost_weight = 0.80
        time_weight = 0.20

    def ranking_key(
        quote: QuoteResponse,
    ) -> tuple[float, float, float]:
        score = (
            cost_weight
            * normalize(
                quote.freight_and_toll_cost_usd,
                minimum_cost,
                maximum_cost,
            )
            + time_weight
            * normalize(
                quote.estimated_transit_days,
                minimum_time,
                maximum_time,
            )
        )

        if timeframe == "SPEED":
            return (
                score,
                quote.estimated_transit_days,
                quote.freight_and_toll_cost_usd,
            )

        return (
            score,
            quote.freight_and_toll_cost_usd,
            quote.estimated_transit_days,
        )

    return min(quotes, key=ranking_key)


def _register_routing_handlers(
    agent: Agent,
    air_agent_address: str,
    ship_agent_address: str,
) -> None:
    @routing_protocol.on_message(
        model=RouteRequestMessage,
        replies=RouteResponseMessage,
    )
    async def handle_routing_request(
        ctx: Context,
        sender: str,
        msg: RouteRequestMessage,
    ) -> None:
        ctx.logger.info(f"Received routing request from {sender}")

        try:
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

            if econ.transport_preference == "AIR":
                air_quote = await request_transport_quote(
                    ctx,
                    air_agent_address,
                    quote_request,
                    "AIR",
                )
                quotes.append(air_quote)

            elif econ.transport_preference == "SHIP":
                ship_quote = await request_transport_quote(
                    ctx,
                    ship_agent_address,
                    quote_request,
                    "SHIP",
                )
                quotes.append(ship_quote)

            elif econ.transport_preference == "EITHER":
                air_quote = await request_transport_quote(
                    ctx,
                    air_agent_address,
                    quote_request,
                    "AIR",
                )

                ship_quote = await request_transport_quote(
                    ctx,
                    ship_agent_address,
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

    agent.include(routing_protocol)

    @agent.on_event("startup")
    async def startup(ctx: Context) -> None:
        ctx.logger.info(f"Riya agent address: {agent.address}")
        ctx.logger.info(f"AIR sub-agent address: {air_agent_address}")
        ctx.logger.info(f"SHIP sub-agent address: {ship_agent_address}")


def create_routing_agent(
    *,
    seed: str | None = None,
    air_agent_address: str | None = None,
    ship_agent_address: str | None = None,
) -> Agent:
    """Create and configure the main routing uAgent."""
    if (
        seed is None
        or air_agent_address is None
        or ship_agent_address is None
    ):
        loaded_seed, loaded_air, loaded_ship = _load_routing_settings()
        seed = seed or loaded_seed
        air_agent_address = air_agent_address or loaded_air
        ship_agent_address = ship_agent_address or loaded_ship

    agent = Agent(
        name="aerofreight-riya-routing",
        seed=seed,
        mailbox=True,
        publish_agent_details=True,
        enable_agent_inspector=False,
    )
    _register_routing_handlers(agent, air_agent_address, ship_agent_address)
    return agent


def main() -> None:
    agent = create_routing_agent()
    print(f"Riya routing agent address: {agent.address}")
    agent.run()


if __name__ == "__main__":
    main()
