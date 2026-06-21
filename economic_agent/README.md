# Step 2 — Economic & Constraints Agent (Owner: Ashwin)

Calculates the financial baseline and physically classifies the cargo.

```
Orchestrator --ShipmentRequest--> [economic-constraints-agent] --EconData--> Orchestrator
```

- **Input:** `ShipmentRequest` (from `shared_models.py`)
- **Output:** `EconData` (from `shared_models.py`)

## What it computes

| Field | Rule |
|---|---|
| `is_high_value` | `True` when `declared_value_usd > 2500`. |
| `is_luxury` | `True` if any item matches a luxury keyword (jewelry, watch, art, wine, designer brands, …) **or** per-unit declared value ≥ $5,000. |
| `transport_preference` | `AIR` if `weight ≤ 500 kg` **or** luxury **or** `timeframe == "SPEED"`; `SHIP` if `weight > 2000 kg` **and** `timeframe == "COST"`; otherwise `EITHER` (let Riya's router compare). |
| `base_entry_tax_usd` | U.S. **MPF** + ad-valorem **category tariff** on the declared value. |

### Entry tax detail
- **MPF (Merchandise Processing Fee):** formal entries (value > $2,500) = 0.3464% of value, clamped to **[$32.71, $634.62]** (CBP/COBRA FY2025); informal entries (≤ $2,500) = flat **$2.62**. Update these constants each fiscal year — see `economics.py`.
- **Category tariff:** keyword → HS-heading duty table mirroring the swarm's USITC HTS classifier (e.g. semiconductors 0%, lithium batteries 3.4%, cotton apparel 16.5%, jewelry 5.5%). Mixed shipments use the **highest** applicable rate (conservative customs estimate).

## Layout
- `economics.py` — pure, framework-agnostic logic (no uAgents dependency). Public entry point: `compute_econ_data(req) -> EconData`. `explain(req)` returns a verbose breakdown for logs/UI.
- `agent.py` — thin uAgents wrapper: `@on_message(ShipmentRequest, replies=EconData)`.
- `test_economics.py` — boundary tests (weight bands, $2,500 threshold, MPF clamps, duty classification).

## Run

```bash
# from repo root
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # uagents + pydantic

# tests (logic only — no agent stack needed)
python -m economic_agent.test_economics
# or: pytest economic_agent/test_economics.py

# run the agent (prints its address for the orchestrator to wire up)
python -m economic_agent.agent
```

### Config (env vars)
- `AEROFREIGHT_ECONOMIC_SEED` — deterministic seed → stable address (default `aerofreight-economic-seed-v1`).
- `AEROFREIGHT_ECONOMIC_PORT` — agent port (default `8002`).
- `AEROFREIGHT_MAILBOX` — `true` to run cross-process (Agentverse); default in-process.
