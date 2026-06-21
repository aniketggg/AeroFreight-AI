
# AeroFreight AI

**AeroFreight AI** is an autonomous, multi-agent logistics orchestration platform built on the [Fetch.ai uAgents framework](https://fetch.ai/). A swarm of specialized agents, governed by strict Pydantic data contracts, automates the end-to-end logistics lifecycle: natural-language intent parsing, mode/route selection, pricing, and simulated financial settlement (Stripe checkout + PDF invoicing).

---

## Architecture Overview

The system employs a **Hub-and-Spoke** model. A centralized **Orchestrator Agent** acts as the system's "brain," coordinating specialized teammate agents — **Economic Agent** (pricing), **Riya/Routing Agent** (route + carrier selection), and **Treasury Agent** (invoicing + payment). Each teammate can run as a local in-process mock or as a remote uAgent reachable over the Fetch.ai network.

### The Workflow Loop

```text
User message (CLI, Agent Chat Protocol, or browser UI via server.py)
  → ConversationController
  → ClaudeShipmentExtractor (natural-language extraction)
  → OrchestratorService + validation (deterministic Python)
  → WorkflowCoordinator
  → [Mock or Remote] Economist, Routing, and Treasury agents
  → Quote → User CONFIRM → Stripe checkout (simulated) → Invoice → COMPLETED
```

---

## Technical Stack

* **Runtime:** Python 3.11+
* **Agent Framework:** `uagents` (Fetch.ai)
* **Data Validation:** `Pydantic` (strict inter-agent contracts in [shared_models.py](shared_models.py))
* **LLM:** Anthropic Claude (`anthropic` SDK, default model `claude-opus-4-8`)
* **Web/API:** `FastAPI` + `uvicorn` ([server.py](server.py)) serving a static browser demo ([index.html](index.html))
* **Payments:** Stripe (embedded checkout via `treasury_agent/payment_backend.py`)
* **Invoicing:** `reportlab` (PDF generation), with optional Google Drive upload for invoice links
* **Testing:** `pytest`, with mocked Anthropic/Stripe clients

---

## Getting Started

### 1. Installation

```bash
git clone <your-repo-url>
cd AeroFreight-AI
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 2. Configuration

Copy [.env.example](.env.example) to `.env` and fill in your keys. **Never commit `.env`.** Key variables:

```text
# --- LLM ---
ANTHROPIC_API_KEY=replace_with_your_key
ANTHROPIC_MODEL=claude-sonnet-4-6

# --- Agent network ---
AGENT_SEED=replace_with_a_private_random_seed
AGENT_NAME=aerofreight-orchestrator
AGENT_PORT=8001

# Leave blank to fall back to the local mock agents
ECONOMIST_AGENT_ADDRESS=
ROUTER_AGENT_ADDRESS=
TREASURY_AGENT_ADDRESS=

# --- Treasury process (separate agent) ---
TREASURY_AGENT_NAME=aerofreight-treasury-agent
TREASURY_AGENT_SEED=
TREASURY_AGENT_PORT=8014
ORCHESTRATOR_AGENT_ADDRESS=

# --- Stripe ---
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=
STRIPE_RETURN_URL=https://agentverse.ai

# --- Optional: Google Drive invoice upload ---
GOOGLE_DRIVE_FOLDER_ID=
GOOGLE_SERVICE_ACCOUNT_JSON=
```

See [.env.example](.env.example) for the full list of supported variables.

### 3. Running it

* **Local CLI demo** (no network agents, mocked teammates):
  ```bash
  python -m orchestrator.cli
  ```
* **Browser demo** (FastAPI bridge driving real Stripe checkout + invoice generation):
  ```bash
  uvicorn server:app --reload
  ```
  then open `index.html` via the server's root route.
* **Distributed mode** — run teammate agents and the orchestrator as separate uAgents, each in its own terminal:
  ```bash
  python -m economic_agent.agent
  python -m step3_riya.agent
  python -m treasury_agent.agent
  python -m orchestrator.agent
  ```

---

## Integration: ASI:One & Agentverse

The Orchestrator exposes the workflow via the **Agent Chat Protocol**:

1. Run `python -m orchestrator.agent`.
2. Open the **Inspector URL** printed in the terminal.
3. Connect via **Mailbox** to chat with the agent through the **ASI:One** interface.

---

## Logic & Constraints

* **Deterministic safety:** Claude performs natural-language intent extraction only. All workflow transitions, validation, and pricing math are deterministic Python in [orchestrator/validation.py](orchestrator/validation.py) and [orchestrator/service.py](orchestrator/service.py).
* **Mode/route selection:** Handled by the routing agent ([step3_riya](step3_riya)), which resolves airports/seaports/cities from local reference data (`step3_riya/data/`) and applies route logic to compare carriers/modes.
* **Settlement:** Once a quote is accepted (`CONFIRM`), the Treasury Agent ([treasury_agent](treasury_agent)) creates a Stripe checkout session, then on payment confirmation generates an itemized PDF invoice (optionally uploaded to Google Drive).

---

## Project Structure

```text
shared_models.py              # Inter-agent Pydantic contracts
schemas.py                    # Additional shared schemas
server.py                     # FastAPI bridge for the browser demo (index.html)
index.html                    # Static browser UI for the demo

orchestrator/
  agent.py                    # uAgent + Agent Chat Protocol entry point
  cli.py                       # Interactive local CLI demo
  conversation.py              # ConversationController
  extractor.py                  # Claude-based shipment extraction
  coordinator.py                  # WorkflowCoordinator
  service.py                        # Workflow state machine
  validation.py                       # Deterministic data validation
  mock_agents.py                       # Local in-process mock teammate agents
  remote_agents.py                      # Remote uAgent clients
  location_normalization.py              # Address/location cleanup
  session_store.py                        # Conversation/session persistence
  uagents_mailbox.py / uagents_storage.py  # Mailbox + storage helpers

economic_agent/                # Pricing teammate agent
step3_riya/                    # Routing teammate agent (airports/ports/cities lookups)
treasury_agent/                # Invoicing + Stripe settlement teammate agent

tests/                         # Unit tests, with mocked Anthropic/Stripe clients
```

---

*Warning: All freight costs, tariffs, routes, documents, and payments in this repository are simulated demo values for research purposes.*
