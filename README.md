
# AeroFreight AI

**AeroFreight AI** is an autonomous, multi-agent logistics orchestration platform built on the [Fetch.ai uAgents framework](https://fetch.ai/). By utilizing a swarm of specialized agents governed by strict Pydantic data contracts, the system automates the end-to-end logistics lifecycle: from natural-language intent parsing to multi-modal routing, tax estimation, and secure financial settlement.

---

## Architecture Overview

The system employs a **Hub-and-Spoke** model. A centralized **Orchestrator Agent** acts as the system's "brain," coordinating a swarm of specialized teammate agents (Economist, Navigator, Treasury).

### The Workflow Loop

```text
User message (CLI or Agent Chat Protocol)
  → ConversationController
  → ClaudeShipmentExtractor (natural-language extraction)
  → OrchestratorService + validation (deterministic Python)
  → WorkflowCoordinator
  → [Mock or Remote] Economist, Routing, and Treasury agents
  → Quote → User CONFIRM → Simulated Payment → COMPLETED

```

---

## Technical Stack

* **Runtime:** Python 3.12+ (Requires 3.11+)
* **Framework:** `uAgents`
* **Data Validation:** `Pydantic` (Strict inter-agent contracts)
* **LLM:** Anthropic Claude 3.5 Sonnet
* **Payments:** Stripe Integration & Fetch.ai Payment Protocol
* **Geospatial:** `geopy` (Haversine formula implementation)

---

## Getting Started

### 1. Installation

```bash
git clone https://github.com/your-org/aerofreight-ai.git
cd aerofreight-ai
source ../.venv/bin/activate
pip install -r requirements.txt

```

### 2. Configuration

Create a `.env` file in the root directory. **Never commit this file.**

```text
# --- LLM ---
ANTHROPIC_API_KEY=your_key_here
ANTHROPIC_MODEL=claude-sonnet-4-6

# --- Agent Network ---
AGENT_SEED=your_private_seed
AGENT_NAME=aerofreight-orchestrator
AGENT_PORT=8001

# --- Remote Agent Addresses ---
ECONOMIST_AGENT_ADDRESS=
ROUTER_AGENT_ADDRESS=
TREASURY_AGENT_ADDRESS=

# --- Financials & Persistence ---
STRIPE_SECRET_KEY=sk_test_...
GOOGLE_SERVICE_ACCOUNT_JSON=...

```

### 3. Execution

* **Local CLI Demo:** `python -m orchestrator.cli`
* **Distributed Mode:** Start individual agents in separate terminals:
```bash
python -m economic_agent.agent
python -m orchestrator.agent

```



---

## Integration: ASI:One & Agentverse

The Orchestrator exposes the workflow via the **Agent Chat Protocol**.

1. Run `python -m orchestrator.agent`.
2. Open the **Inspector URL** provided in the terminal.
3. Connect via **Mailbox** to chat directly with the agent through the **ASI:One** interface.

---

## Logic & Constraints

* **Mode Threshold:** We implement a **150kg Break Point**. Shipments $\le 150\text{kg}$ default to Air; shipments $>150\text{kg}$ trigger a cost-benefit comparison.
* **Deterministic Safety:** While Claude performs intent extraction, all workflow transitions, data validation, and financial calculations are handled by deterministic Python code in `orchestrator/validation.py` and `service.py`.
* **Settlement:** Once a quote is accepted (`CONFIRM`), the Treasury Agent generates an itemized PDF invoice and initiates the atomic settlement flow.

---

## Project Structure

```text
shared_models.py              # Inter-agent Pydantic contracts
orchestrator/
  service.py                  # Workflow state machine
  validation.py               # Deterministic data validation
  extractor.py                # Claude shipment extraction
  agent.py                    # uAgent + Agent Chat Protocol entry point
  cli.py                      # Interactive local demo
  mock_agents.py              # Local deterministic teammate agents
tests/                        # Unit tests with mocked Anthropic clients

```

---

*Warning: All freight costs, tariffs, routes, documents, and payments in this repository are simulated demo values for research purposes.*