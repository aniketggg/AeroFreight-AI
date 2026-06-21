# AeroFreight AI

**AeroFreight AI** is an autonomous, multi-agent logistics orchestration platform built on the [Fetch.ai uAgents framework](https://fetch.ai/). By utilizing a swarm of specialized agents governed by strict Pydantic data contracts, the system automates the end-to-end logistics lifecycle: from intent parsing and multi-modal routing to automated tax estimation, detailed invoice generation, and secure financial settlement.

---

## Architecture Overview

The system employs a **Hub-and-Spoke** orchestration model. A centralized **Orchestrator Agent** acts as the system's "brain," coordinating a swarm of specialized sub-agents.

* **Local vs. Distributed:** The system supports both local deterministic mocks for rapid testing and distributed execution via network addresses (`ECONOMIST_AGENT_ADDRESS`, `ROUTER_AGENT_ADDRESS`, etc.).
* **Data Contracts:** All inter-agent communication is governed by typed Pydantic models, ensuring modularity and data integrity across network boundaries.

---

## Technical Stack

* **Runtime:** Python 3.12+
* **Framework:** `uAgents`
* **Data Validation:** `Pydantic` (Strict type enforcement)
* **LLM:** Anthropic Claude 3.5 Sonnet
* **Payments:** Stripe Integration for invoice settlement
* **Storage:** Google Drive API for invoice and document persistence

---

## Getting Started

### 1. Installation

```bash
git clone https://github.com/your-org/aerofreight-ai.git
cd aerofreight-ai
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

```

### 2. Configuration

Create a `.env` file in the root directory. Use the following template:

```text
# --- LLM Configuration ---
ANTHROPIC_API_KEY=your_key_here
ANTHROPIC_MODEL=claude-sonnet-4-6

# --- Agent Network Configuration ---
AGENT_SEED=your_unique_seed
AGENT_NAME=
AGENT_PORT=

# --- Remote Agent Addresses ---
ECONOMIST_AGENT_ADDRESS=
ECONOMIST_AGENT_TIMEOUT_SECONDS=30
ROUTER_AGENT_ADDRESS=
ROUTER_AGENT_TIMEOUT_SECONDS=30
TREASURY_AGENT_ADDRESS=

# --- Financial & Settlement ---
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_SECRET_KEY=sk_test_...
STRIPE_CURRENCY=usd

# --- Document Persistence ---
GOOGLE_DRIVE_FOLDER_ID=your_folder_id
GOOGLE_SERVICE_ACCOUNT_JSON=your_json_string_or_path

```

### 3. Execution

To run the distributed swarm, start the individual agents in separate terminal sessions:

```bash
# Terminal 1: Start the Economist
python -m agents.economist

# Terminal 2: Start the Treasury
python -m agents.treasury

# Terminal 3: Start the Orchestrator
python orchestrator.py

```

---

## Agent Swarm Roles

| Agent | Primary Responsibility |
| --- | --- |
| **Orchestrator** | Intent parsing, workflow state management, and agent coordination. |
| **Economist** | Cargo classification, constraint logic, and U.S. entry tariff calculation. |
| **Navigator** | Geospatial routing, mode-aware distance calculation, and landed cost estimation. |
| **Treasury** | PDF invoice generation, Stripe checkout session creation, and escrow settlement. |

---

## Data Contracts

To prevent data mismatch across network boundaries, all agents adhere to strict Pydantic schemas:

```python
class ShipmentRequest(BaseModel):
    origin: dict
    destination: dict
    items: List[Item]
    total_weight_kg: float
    total_volume_cbm: float
    timeframe: Literal["SPEED", "COST"]
    declared_value_usd: float

```

---

## Logic & Constraints

* **Mode Threshold:** A **150kg Break Point** determines transport modes. Shipments $\le 150\text{kg}$ default to Air; shipments $>150\text{kg}$ trigger a cost-benefit comparison.
* **Routing:** Uses the Haversine formula and infrastructure-specific datasets (World Port Index/Airports) to calculate real-world costs.
* **Settlement:** The Treasury agent generates an itemized PDF invoice, triggers a Stripe Checkout session, and confirms settlement before finalizing the shipment record.

---
