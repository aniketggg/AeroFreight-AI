"""Local uAgents round-trip for Step 4 (Aniket).

Spins up a Bureau with the REAL compliance-document agent plus a tiny
orchestrator-stub agent. The stub sends a `ComplianceRequest` (the accumulated
Global State) on startup; the compliance agent "browses" for the required forms
and replies with `DocTemplates` over the uAgents transport; the stub prints it
and exits. This proves the agent works end-to-end on uAgents.

Run from the repo root (after `pip install -r requirements.txt`):
    python -m compliance_agent.run_local
"""

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from uagents import Agent, Bureau, Context  # noqa: E402

from compliance_agent.agent import compliance_agent  # the real Step-4 agent  # noqa: E402
from shared_models import (  # noqa: E402
    ComplianceRequest,
    DocTemplates,
    EconData,
    Item,
    RouteData,
    ShipmentRequest,
    dump,
)

# The Global State the stub orchestrator will send (edit freely).
SAMPLE = ComplianceRequest(
    shipment=ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="semiconductor components", quantity=500,
                    category="electronics")],
        total_weight_kg=200, total_volume_cbm=3.0,
        timeframe="SPEED", declared_value_usd=2800,
    ),
    econ=EconData(transport_preference="AIR", is_high_value=True,
                  is_luxury=False, base_entry_tax_usd=32.71),
    route=RouteData(selected_mode="AIR",
                    optimal_route_nodes=["SZX", "HKG", "LAX", "Austin"],
                    countries_visited=["CN", "HK", "US"],
                    freight_and_toll_cost_usd=5200.0,
                    total_landed_cost_usd=8032.71),
)

# Stub stands in for the orchestrator (Step 1) during local testing.
orchestrator_stub = Agent(name="orchestrator-stub", seed="aerofreight-tester-seed-v1")


@orchestrator_stub.on_event("startup")
async def _send_request(ctx: Context):
    ctx.logger.info(f"-> sending ComplianceRequest to {compliance_agent.address[:16]}…")
    await ctx.send(compliance_agent.address, SAMPLE)


@orchestrator_stub.on_message(model=DocTemplates)
async def _on_docs(ctx: Context, sender: str, msg: DocTemplates):
    ctx.logger.info("<- received DocTemplates reply:")
    print("\n=============  DocTemplates (Aniket's output)  =============")
    print(json.dumps(dump(msg), indent=2))
    print("===========================================================\n")
    # os._exit skips stdout flushing, so flush the printed reply first.
    sys.stdout.flush()
    # Demo over: stop cleanly so the script returns instead of running forever.
    os._exit(0)


bureau = Bureau()
bureau.add(compliance_agent)
bureau.add(orchestrator_stub)


if __name__ == "__main__":
    print(f"compliance agent address   : {compliance_agent.address}")
    print(f"orchestrator-stub address  : {orchestrator_stub.address}\n")
    bureau.run()
