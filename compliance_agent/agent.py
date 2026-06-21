"""Step 4 — Compliance & Document Agent (Owner: Aniket).

Thin uAgents transport wrapper around :mod:`compliance_agent.compliance`.

Flow (hub-and-spoke):
    Orchestrator --ComplianceRequest--> [this agent] --DocTemplates--> Orchestrator

The agent is intentionally thin: it logs the request, runs the pure
``compute_doc_templates`` retrieval (which "browses" for the required CBP forms
and transport documents), logs the chosen packet, and replies to the sender
with a :class:`DocTemplates`. All selection logic lives in ``compliance.py`` and
the browser/search logic in ``retrieval.py`` so they can be tested without the
agent stack.

Run standalone (prints the agent's address for the orchestrator to wire up):
    python -m compliance_agent.agent
"""

import os
import sys

# Make the repo-root `shared_models.py` importable no matter the working dir.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from uagents import Agent, Context  # noqa: E402

from compliance_agent.compliance import compute_doc_templates, explain  # noqa: E402
from shared_models import ComplianceRequest, DocTemplates  # noqa: E402

# Deterministic seed -> stable address, so the orchestrator can resolve this
# agent from config without a handshake (override via env for deployments).
COMPLIANCE_SEED = os.getenv(
    "AEROFREIGHT_COMPLIANCE_SEED", "aerofreight-compliance-seed-v1"
)
COMPLIANCE_PORT = int(os.getenv("AEROFREIGHT_COMPLIANCE_PORT", "8004"))

# Set AEROFREIGHT_MAILBOX=true to reach this agent across processes (Agentverse);
# in the local in-process Bureau demo it stays False.
_USE_MAILBOX = os.getenv("AEROFREIGHT_MAILBOX", "false").lower() == "true"

compliance_agent = Agent(
    name="compliance-document-agent",
    seed=COMPLIANCE_SEED,
    port=COMPLIANCE_PORT,
    endpoint=[f"http://127.0.0.1:{COMPLIANCE_PORT}/submit"],
    mailbox=_USE_MAILBOX,
)


@compliance_agent.on_message(model=ComplianceRequest, replies=DocTemplates)
async def handle_compliance_request(
    ctx: Context, sender: str, msg: ComplianceRequest
):
    """Pick the required forms for the route + cargo, then reply with DocTemplates."""
    ctx.logger.info(
        f"ComplianceRequest from {sender[:16]}…: "
        f"mode={msg.route.selected_mode}, "
        f"countries={msg.route.countries_visited}, "
        f"{len(msg.shipment.items)} item(s), "
        f"declared ${msg.shipment.declared_value_usd:,.2f}, "
        f"high_value={msg.econ.is_high_value}, luxury={msg.econ.is_luxury}"
    )

    # `live` is left as None so the retrieval layer reads AEROFREIGHT_COMPLIANCE_LIVE.
    docs = compute_doc_templates(msg, logger=ctx.logger)

    ctx.logger.info(
        f"DocTemplates -> {len(docs.required_form_names)} form(s): "
        f"{', '.join(docs.required_form_names)}"
    )
    ctx.logger.debug(f"Sources: {explain(msg)['form_sources']}")

    await ctx.send(sender, docs)


@compliance_agent.on_event("startup")
async def _announce(ctx: Context):
    ctx.logger.info(
        f"Compliance & Document Agent address: {compliance_agent.address}"
    )


if __name__ == "__main__":
    # Print the address up-front so the orchestrator lead can wire it in.
    print(f"compliance-document-agent address: {compliance_agent.address}")
    compliance_agent.run()
