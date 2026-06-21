"""Optional local Bureau demo for the Riya routing multi-agent stack."""

from uagents import Agent, Bureau, Context

from step3_riya.agent import (
    RouteRequestMessage,
    RouteResponseMessage,
    create_routing_agent,
)
from step3_riya.air_agent import create_air_agent
from step3_riya.ship_agent import create_ship_agent


def _build_demo_orchestrator(
    riya_agent,
) -> Agent:
    test_orchestrator = Agent(
        name="test-aerofreight-orchestrator",
        seed="local test orchestrator seed only",
    )

    request_sent = False

    @test_orchestrator.on_interval(period=1.0)
    async def send_test_request(ctx: Context) -> None:
        nonlocal request_sent

        if request_sent:
            return

        request_sent = True

        ctx.logger.info(
            "Sending ShipmentRequest + EconData to Riya..."
        )

        await ctx.send(
            riya_agent.address,
            RouteRequestMessage(
                shipment={
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
                econ={
                    "transport_preference": "EITHER",
                    "is_high_value": True,
                    "is_luxury": False,
                    "base_entry_tax_usd": 350,
                },
            ),
        )

    @test_orchestrator.on_message(model=RouteResponseMessage)
    async def receive_route_response(
        ctx: Context,
        sender: str,
        msg: RouteResponseMessage,
    ) -> None:
        if not msg.ok:
            print(f"\n❌ STEP 3 TEST FAILED: {msg.error}\n")
            return

        result = msg.route_data

        expected_fields = {
            "selected_mode",
            "optimal_route_nodes",
            "countries_visited",
            "freight_and_toll_cost_usd",
            "total_landed_cost_usd",
        }

        assert set(result.keys()) == expected_fields
        assert result["selected_mode"] in {"AIR", "SHIP"}
        assert result["total_landed_cost_usd"] >= (
            result["freight_and_toll_cost_usd"]
        )

        print("\n✅ FETCH.AI STEP 3 MULTI-AGENT TEST PASSED")
        print("========================================")
        print(f"Mode: {result['selected_mode']}")
        print(
            "Route: "
            + " -> ".join(result["optimal_route_nodes"])
        )
        print(
            "Countries: "
            + ", ".join(result["countries_visited"])
        )
        print(
            "Freight and toll cost: "
            f"${result['freight_and_toll_cost_usd']:.2f}"
        )
        print(
            "Total landed cost: "
            f"${result['total_landed_cost_usd']:.2f}"
        )
        print("========================================\n")

    return test_orchestrator


def main() -> None:
    air_agent = create_air_agent()
    ship_agent = create_ship_agent()
    riya_agent = create_routing_agent(
        air_agent_address=air_agent.address,
        ship_agent_address=ship_agent.address,
    )
    test_orchestrator = _build_demo_orchestrator(riya_agent)

    bureau = Bureau(
        [
            test_orchestrator,
            riya_agent,
            air_agent,
            ship_agent,
        ]
    )
    bureau.run()


if __name__ == "__main__":
    main()
