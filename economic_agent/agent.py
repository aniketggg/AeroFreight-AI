"""Step 2 — Economic & Constraints Agent (Owner: Ashwin).

Thin uAgents transport wrapper around :mod:`economic_agent.economics`.

Flow (hub-and-spoke):
    Orchestrator --ShipmentRequest--> [this agent] --EconData--> Orchestrator

The agent is intentionally thin: it logs the request, runs the pure
``compute_econ_data`` calculation, logs the decision, and replies to the
sender with an :class:`EconData`. All business logic lives in ``economics.py``
so it can be tested without the agent stack.

Run standalone (prints the agent's address for the orchestrator to wire up):
    python -m economic_agent.agent
"""

import os
import sys

# Make the repo-root `shared_models.py` importable no matter the working dir.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from uagents import Agent, Context  # noqa: E402

from economic_agent.economics import compute_econ_data, explain  # noqa: E402
from shared_models import EconData, ShipmentRequest  # noqa: E402

# Deterministic seed -> stable address, so the orchestrator can resolve this
# agent from config without a handshake (override via env for deployments).
ECONOMIC_SEED = os.getenv("AEROFREIGHT_ECONOMIC_SEED", "aerofreight-economic-seed-v1")
ECONOMIC_PORT = int(os.getenv("AEROFREIGHT_ECONOMIC_PORT", "8002"))

# Set AEROFREIGHT_MAILBOX=true to reach this agent across processes (Agentverse);
# in the local in-process Bureau demo it stays False.
_USE_MAILBOX = os.getenv("AEROFREIGHT_MAILBOX", "false").lower() == "true"

economic_agent = Agent(
    name="economic-constraints-agent",
    seed=ECONOMIC_SEED,
    port=ECONOMIC_PORT,
    endpoint=[f"http://127.0.0.1:{ECONOMIC_PORT}/submit"],
    mailbox=_USE_MAILBOX,
)


@economic_agent.on_message(model=ShipmentRequest, replies=EconData)
async def handle_shipment_request(ctx: Context, sender: str, msg: ShipmentRequest):
    """Classify cargo + price the entry tax, then reply with EconData."""
    ctx.logger.info(
        f"ShipmentRequest from {sender[:16]}…: "
        f"{msg.total_weight_kg} kg, {len(msg.items)} item(s), "
        f"declared ${msg.declared_value_usd:,.2f}, timeframe={msg.timeframe}"
    )

    econ = compute_econ_data(msg)

    ctx.logger.info(
        f"EconData -> transport={econ.transport_preference}, "
        f"high_value={econ.is_high_value}, luxury={econ.is_luxury}, "
        f"entry_tax=${econ.base_entry_tax_usd:,.2f}"
    )
    ctx.logger.debug(f"Breakdown: {explain(msg)['entry_tax_breakdown']}")

    await ctx.send(sender, econ)


@economic_agent.on_event("startup")
async def _announce(ctx: Context):
    ctx.logger.info(f"Economic & Constraints Agent address: {economic_agent.address}")


if __name__ == "__main__":
    # Print the address up-front so the orchestrator lead can wire it in.
    print(f"economic-constraints-agent address: {economic_agent.address}")
    economic_agent.run()
