"""TARIFF / CUSTOMS agent for the AeroFreight AI swarm.

This agent owns HS-code classification and import-duty estimation. It listens
for :class:`TariffRequest` messages (typically from the orchestrator), forwards
the commodity + declared value to the mock data API's ``/tariff/classify``
endpoint, and replies to the original sender with a fully-populated
:class:`TariffResponse`.

The agent is intentionally thin: all customs logic lives in
``mock_api/tariff_data.py`` behind the HTTP boundary, so the same classification
table can be reused by other tools and tested independently.
"""

import os

from uagents import Agent, Context

from agents.config import TARIFF_SEED, API_BASE_URL, SUBAGENT_TIMEOUT
from agents.messages import TariffRequest, TariffResponse

import httpx

# When deploying the agents as separate processes (e.g. on Agentverse), set
# AEROFREIGHT_MAILBOX=true so an out-of-process orchestrator can reach this
# agent via its mailbox. In the local Bureau demo it stays False (in-process).
_USE_MAILBOX = os.getenv("AEROFREIGHT_MAILBOX", "false").lower() == "true"

# Single agent instance; address is derived deterministically from TARIFF_SEED
# so peers can resolve it from config without a handshake.
tariff_agent = Agent(name="tariff-agent", seed=TARIFF_SEED, mailbox=_USE_MAILBOX)


@tariff_agent.on_message(model=TariffRequest)
async def handle_tariff_request(ctx: Context, sender: str, msg: TariffRequest):
    """Classify a commodity and return its HS code + computed duty.

    Flow:
      1. Receive the commodity description and declared customs value.
      2. POST them to the mock data API, which runs the HS lookup and duty calc.
      3. Wrap the JSON result in a TariffResponse and send it back to ``sender``.

    On any HTTP/transport failure we log the error and fall back to a safe,
    well-formed response (default 3.0% rate computed locally) so the calling
    workflow never stalls waiting on a reply.
    """
    ctx.logger.info(
        f"Tariff request: classifying '{msg.commodity}' "
        f"(declared value ${msg.declared_value_usd:,.2f})"
    )

    try:
        # SUBAGENT_TIMEOUT caps the round trip so the orchestrator's
        # send_and_receive budget is respected even if the API is slow.
        async with httpx.AsyncClient(timeout=SUBAGENT_TIMEOUT) as client:
            response = await client.post(
                f"{API_BASE_URL}/tariff/classify",
                json={
                    "commodity": msg.commodity,
                    "declared_value_usd": msg.declared_value_usd,
                },
            )
            response.raise_for_status()
            data = response.json()

        result = TariffResponse(**data)
        ctx.logger.info(
            f"Classified '{msg.commodity}' -> HS Code: {result.hs_code} "
            f"({result.description}) | Duty: {result.duty_rate_pct}% "
            f"= ${result.duty_usd:,.2f}"
        )

    except Exception as exc:  # noqa: BLE001 - degrade gracefully on any failure
        # Local fallback: a conservative default classification so downstream
        # steps (escrow, BoL) still receive a complete, valid response.
        ctx.logger.error(f"Tariff classification failed: {exc!r}; using default rate")
        fallback_rate = 3.0
        result = TariffResponse(
            hs_code="9999.99",
            description="Unclassified merchandise (general rate)",
            duty_rate_pct=fallback_rate,
            duty_usd=round(fallback_rate / 100.0 * msg.declared_value_usd, 2),
        )

    await ctx.send(sender, result)


if __name__ == "__main__":
    # Standalone run for local testing; not executed on import.
    tariff_agent.run()
