"""Local uAgents round-trip for Step 2 (Ashwin).

Spins up a Bureau with the REAL economic-constraints agent plus a tiny
orchestrator-stub agent. The stub sends a `ShipmentRequest` on startup; the
economic agent replies with `EconData` over the uAgents transport; the stub
prints it and exits. This proves the agent works end-to-end on uAgents.

Run from the repo root (after `pip install -r requirements.txt`):
    python -m economic_agent.run_local
"""

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from uagents import Agent, Bureau, Context  # noqa: E402

from economic_agent.agent import economic_agent  # the real Step-2 agent  # noqa: E402
from shared_models import EconData, Item, ShipmentRequest, dump  # noqa: E402

# The shipment the stub orchestrator will send (edit freely).
SAMPLE = ShipmentRequest(
    origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
    destination={"country": "US", "state": "TX", "city": "Austin"},
    items=[Item(name="semiconductor components", quantity=500, category="electronics")],
    total_weight_kg=200,
    total_volume_cbm=3.0,
    timeframe="SPEED",
    declared_value_usd=2800,
)

# Stub stands in for the orchestrator (Step 1) during local testing.
orchestrator_stub = Agent(name="orchestrator-stub", seed="aerofreight-tester-seed-v1")


@orchestrator_stub.on_event("startup")
async def _send_request(ctx: Context):
    ctx.logger.info(f"-> sending ShipmentRequest to {economic_agent.address[:16]}…")
    await ctx.send(economic_agent.address, SAMPLE)


@orchestrator_stub.on_message(model=EconData)
async def _on_econ(ctx: Context, sender: str, msg: EconData):
    ctx.logger.info("<- received EconData reply:")
    print("\n================  EconData (Ashwin's output)  ================")
    print(json.dumps(dump(msg), indent=2))
    print("=============================================================\n")
    # os._exit skips stdout flushing, so flush the printed reply first.
    sys.stdout.flush()
    # Demo over: stop cleanly so the script returns instead of running forever.
    os._exit(0)


bureau = Bureau()
bureau.add(economic_agent)
bureau.add(orchestrator_stub)


if __name__ == "__main__":
    print(f"economic agent address     : {economic_agent.address}")
    print(f"orchestrator-stub address  : {orchestrator_stub.address}\n")
    bureau.run()
