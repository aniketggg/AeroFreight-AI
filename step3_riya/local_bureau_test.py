from uagents import Agent, Bureau, Context

from agent import (
    RouteRequestMessage,
    RouteResponseMessage,
    riya_agent,
)


test_orchestrator = Agent(
    name="test-aerofreight-orchestrator",
    seed="test aerofreight orchestrator development seed",
)


@test_orchestrator.on_event("startup")
async def send_routing_request(ctx: Context) -> None:
    ctx.logger.info("Sending shipment request to Riya...")

    await ctx.send(
        riya_agent.address,
        RouteRequestMessage(
            payload={
                "shipment": {
                    "origin": {
                        "country": "CN",
                        "state": "Guangdong",
                        "city": "Shenzhen",
                    },
                    "destination": {
                        "country": "US",
                        "state": "TX",
                        "city": "Austin",
                    },
                    "items": [
                        {
                            "name": "Electronics",
                            "quantity": 10,
                            "category": "electronics",
                        }
                    ],
                    "total_weight_kg": 800,
                    "total_volume_cbm": 4.2,
                    "timeframe": "COST",
                    "declared_value_usd": 5000,
                },
                "econ": {
                    "transport_preference": "EITHER",
                    "is_high_value": True,
                    "is_luxury": False,
                    "base_entry_tax_usd": 350,
                },
            }
        ),
    )


@test_orchestrator.on_message(model=RouteResponseMessage)
async def receive_route(
    ctx: Context,
    sender: str,
    msg: RouteResponseMessage,
) -> None:
    ctx.logger.info(f"Received response from Riya: {sender}")

    if msg.ok:
        result = msg.payload

        print("\n=== FETCH.AI ROUTING RESPONSE ===")
        print(f"Mode: {result['selected_mode']}")
        print(f"Route: {' -> '.join(result['optimal_route_nodes'])}")
        print(f"Freight: ${result['freight_cost_usd']:.2f}")
        print(
            "Tolls/tariffs: "
            f"${result['tolls_and_route_tariffs_usd']:.2f}"
        )
        print(f"Entry tax: ${result['entry_tax_usd']:.2f}")
        print(
            "Total landed cost: "
            f"${result['total_landed_cost_usd']:.2f}"
        )
        print("=================================\n")
    else:
        print(f"Routing failed: {msg.error}")


bureau = Bureau()
bureau.add(test_orchestrator)
bureau.add(riya_agent)


if __name__ == "__main__":
    bureau.run()
