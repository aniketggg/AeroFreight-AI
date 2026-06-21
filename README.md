# AeroFreight AI

**AeroFreight AI** is an autonomous, multi-agent logistics orchestration platform built on the [Fetch.ai uAgents framework](https://fetch.ai/). By utilizing a swarm of specialized agents governed by strict Pydantic data contracts, the system automates the end-to-end logistics lifecycle: from intent parsing and multi-modal routing to automated tax estimation, detailed invoice generation, and secure financial settlement.

---

## Architecture Overview

AeroFreight AI employs a **Hub-and-Spoke** orchestration model. A centralized **Orchestrator Agent** acts as the system's "brain," managing the shipment request lifecycle by coordinating a swarm of specialized agents. Communication is strictly governed by typed Pydantic data contracts to ensure modularity and data integrity.

### Agent Swarm Roles

| Agent | Primary Responsibility |
| --- | --- |
| **Orchestrator** | Natural language intent parsing, global state management, and agent coordination. |
| **Economist** | Cargo classification, transport mode constraint logic, and U.S. entry tariff calculation. |
| **Navigator** | Geospatial routing, mode-aware distance calculation, and landed cost estimation. |
| **Treasury** | PDF invoice generation, itemized cost breakdown, and settlement execution. |

---

## Technical Stack

* **Runtime:** Python 3.12+
* **Framework:** `uAgents`
* **Data Validation:** `Pydantic` (Strict type enforcement for inter-agent communication)
* **LLM:** Anthropic Claude 3.5 Sonnet (via Structured Outputs)
* **Document Generation:** `reportlab` / `fpdf2`
* **Geospatial:** `geopy` (Haversine formula implementation)

---

## Getting Started

### 1. Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/your-org/aerofreight-ai.git
cd aerofreight-ai
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

```

### 2. Configuration

Create a `.env` file in the root directory and add your API keys:

```text
ANTHROPIC_API_KEY=your_key_here
FETCH_AI_KEY=your_key_here

```

### 3. Execution

Run the orchestrator to begin the autonomous logistics pipeline:

```bash
python orchestrator.py

```

---

## Data Contracts

To prevent data mismatch, all agents adhere to strict Pydantic schemas:

```python
# Core Data Models
class ShipmentRequest(BaseModel):
    origin: dict
    destination: dict
    items: List[Item]
    total_weight_kg: float
    total_volume_cbm: float
    timeframe: Literal["SPEED", "COST"]
    declared_value_usd: float

class RouteData(BaseModel):
    selected_mode: Literal["AIR", "SHIP"]
    optimal_route_nodes: List[str]
    countries_visited: List[str]
    freight_and_toll_cost_usd: float
    total_landed_cost_usd: float

```

---

## Logic & Constraints

* **Mode Threshold:** We implement a **150kg Break Point**. Shipments $\le 150\text{kg}$ default to Air; shipments $>150\text{kg}$ trigger a cost-benefit comparison between Air and Sea.
* **Routing:** The Navigator uses the Haversine formula to calculate distances across global segments, integrating infrastructure-specific data from the World Port Index (WPI) and global airport databases.
* **Settlement:** Upon user confirmation, the Treasury Agent generates an itemized PDF invoice including freight, tolls, entry taxes, and service fees, then triggers the simulation of an atomic escrow settlement via the Fetch.ai Payment Protocol.

---

