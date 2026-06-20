"""Freight-Router agent for the AeroFreight swarm.

Listens for a :class:`FreightRequest` from the orchestrator, asks the mock
carrier API for the cheapest deadline-meeting itinerary (air + ground), and
replies with a :class:`FreightResponse` describing the chosen multimodal route.

The actual routing logic lives in ``mock_api.carrier_data.quote`` behind the
FastAPI endpoint ``POST /freight/quote``; this agent is the transport-layer
adapter that bridges the uAgents message bus to that HTTP endpoint.
"""

from __future__ import annotations

import httpx
from uagents import Agent, Context

from agents.config import API_BASE_URL, FREIGHT_SEED, SUBAGENT_TIMEOUT
from agents.messages import FreightLeg, FreightRequest, FreightResponse


# Deterministic seed -> stable address that the orchestrator already knows.
freight_agent = Agent(name="freight-router-agent", seed=FREIGHT_SEED)


@freight_agent.on_message(model=FreightRequest)
async def handle_freight_request(
    ctx: Context, sender: str, msg: FreightRequest
) -> None:
    """Quote a route for the requested shipment and reply to the sender."""
    ctx.logger.info(
        f"Quoting freight {msg.origin} -> {msg.destination} "
        f"({msg.weight_kg:g} kg, deadline {msg.deadline_iso})"
    )

    # Call the mock carrier API. Use the shared sub-agent timeout so a slow or
    # unreachable API surfaces as a clear error rather than hanging the swarm.
    async with httpx.AsyncClient(timeout=SUBAGENT_TIMEOUT) as client:
        r = await client.post(
            f"{API_BASE_URL}/freight/quote",
            json={
                "origin": msg.origin,
                "destination": msg.destination,
                "weight_kg": msg.weight_kg,
                "deadline_iso": msg.deadline_iso,
            },
        )
        r.raise_for_status()
        data = r.json()

    # uagents/pydantic coerces the nested leg dicts into FreightLeg models, but
    # we construct them explicitly so the response is well-typed regardless of
    # the pydantic version's coercion behaviour.
    legs = [FreightLeg(**leg) for leg in data.get("legs", [])]
    resp = FreightResponse(
        legs=legs,
        total_cost_usd=data["total_cost_usd"],
        transit_days=data["transit_days"],
        eta_iso=data["eta_iso"],
        meets_deadline=data["meets_deadline"],
    )

    # Narrate the chosen multimodal route for demo observability.
    route_desc = " + ".join(
        f"{leg.carrier} {leg.service} ({leg.mode} {leg.from_node}->{leg.to_node})"
        for leg in resp.legs
    )
    deadline_note = "meets deadline" if resp.meets_deadline else "MISSES deadline"
    ctx.logger.info(
        f"Best route: {route_desc} | ${resp.total_cost_usd:,.2f}, "
        f"{resp.transit_days}d, ETA {resp.eta_iso} ({deadline_note})"
    )

    await ctx.send(sender, resp)


if __name__ == "__main__":  # pragma: no cover
    # Standalone run for local testing; the orchestrator launches it normally.
    freight_agent.run()
