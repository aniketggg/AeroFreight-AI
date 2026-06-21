"""Escrow / payment agent for the AeroFreight AI swarm.

This agent owns the *settlement* step of the workflow. Once the orchestrator has
priced the shipment (tariff + freight) it asks this agent to mint a smart-escrow
contract that holds the buyer's funds until the carriers are dispatched.

Design notes
------------
* The agent is intentionally self-contained: it performs **no** network I/O. It
  only mints a deterministic-looking contract id and a customer-facing payment
  link, then hands those back to the orchestrator. The orchestrator is the party
  that subsequently POSTs the full Bill-of-Lading record to ``/bol`` so the web
  page can render rich details. Keeping this agent side-effect-free makes it
  trivial to test and impossible to wedge on a flaky HTTP call.
* The payment link points at the static escrow page served under ``WEB_BASE_URL``
  and carries the freshly-minted contract id as the ``cid`` query parameter. The
  page reads that id and fetches ``/bol/{cid}`` to draw the Bill of Lading.
"""

import os
from uuid import uuid4

from uagents import Agent, Context

from agents.config import ESCROW_SEED, WEB_BASE_URL
from agents.messages import EscrowRequest, EscrowResponse

# Set AEROFREIGHT_MAILBOX=true for a multi-process / Agentverse deploy so an
# out-of-process orchestrator can reach this agent; False for the local Bureau.
_USE_MAILBOX = os.getenv("AEROFREIGHT_MAILBOX", "false").lower() == "true"

# The escrow agent. The seed is fixed in config so its address is stable across
# runs and the orchestrator can address it without any discovery handshake.
escrow_agent = Agent(name="escrow-payment-agent", seed=ESCROW_SEED, mailbox=_USE_MAILBOX)


@escrow_agent.on_message(model=EscrowRequest)
async def handle_escrow_request(ctx: Context, sender: str, msg: EscrowRequest):
    """Mint a smart-escrow contract and return its id + a payment link.

    The contract id mimics a Fetch.ai-style ledger handle (``fetch1escrow...``)
    so it reads convincingly in the demo. The status is ``pending_authorization``
    because no funds move until the buyer clicks *Authorize & Fund Escrow* on the
    web page — at which point the carriers are dispatched.
    """
    # Mint a unique, deterministic-looking contract id. 12 hex chars from a v4
    # UUID gives us ~48 bits of entropy — plenty for collision-free demo runs.
    cid = "fetch1escrow" + uuid4().hex[:12]

    # Customer-facing settlement link. Relative to the same origin that serves
    # the BoL API, so the page's `fetch('/bol/<cid>')` call always hits home.
    payment_link = f"{WEB_BASE_URL}/escrow.html?cid={cid}"

    ctx.logger.info(
        f"Minted escrow contract {cid} for vendor '{msg.vendor}' "
        f"holding ${msg.total_usd:,.2f} (ref {msg.shipment_ref})"
    )
    ctx.logger.info(f"Payment link: {payment_link}")

    await ctx.send(
        sender,
        EscrowResponse(
            contract_id=cid,
            payment_link=payment_link,
            status="pending_authorization",
        ),
    )


if __name__ == "__main__":
    # Run standalone only when invoked directly (never at import time).
    escrow_agent.run()
