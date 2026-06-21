# AeroFreight AI

**AeroFreight AI** is an autonomous, multi-agent logistics orchestration platform built on the [Fetch.ai uAgents framework](https://fetch.ai/). By utilizing a swarm of specialized agents governed by strict Pydantic data contracts, the system automates the end-to-end logistics lifecycle: from intent parsing and multi-modal routing to automated tax estimation, detailed invoice generation, and secure financial settlement.

## Architecture
The system employs a **Hub-and-Spoke** orchestration model. A centralized **Orchestrator Agent** acts as the system's "brain," managing the shipment request lifecycle by coordinating specialized sub-agents. 

## Technical Stack
* **Runtime:** Python 3.12+
* **Framework:** `uAgents`
* **Data Validation:** `Pydantic`
* **LLM:** Anthropic Claude 3.5 Sonnet
* **Geospatial:** `geopy` (Haversine distance)

## Getting Started
1. `pip install -r requirements.txt`
2. Create a `.env` file with your `ANTHROPIC_API_KEY`.
3. Run `python orchestrator.py` to start the pipeline.

## Agent Swarm
* **Orchestrator:** Intent parsing & state management.
* **Economist:** Cargo classification & tariff calculation.
* **Navigator:** Geospatial routing & cost estimation.
* **Treasury:** Invoice generation & escrow settlement.

---

