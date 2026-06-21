"""Demo for Step 2 (Ashwin) — prints EconData + tax breakdown for sample shipments.

Run from the repo root:
    python -m economic_agent.demo

Edit the SCENARIOS list below to try your own cargo. No uAgents needed — this
calls the pure logic directly.
"""

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from economic_agent.economics import compute_econ_data, explain  # noqa: E402
from shared_models import Item, ShipmentRequest, dump  # noqa: E402


# (label, ShipmentRequest) — tweak these freely.
SCENARIOS = [
    (
        "Light high-value semiconductors, SPEED  (Shenzhen -> Austin)",
        ShipmentRequest(
            origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
            destination={"country": "US", "state": "TX", "city": "Austin"},
            items=[Item(name="semiconductor components", quantity=500, category="electronics")],
            total_weight_kg=200, total_volume_cbm=3.0,
            timeframe="SPEED", declared_value_usd=2800,
        ),
    ),
    (
        "Heavy cotton apparel, COST  (Mumbai -> Newark)",
        ShipmentRequest(
            origin={"country": "IN", "state": "MH", "city": "Mumbai"},
            destination={"country": "US", "state": "NJ", "city": "Newark"},
            items=[Item(name="cotton t-shirts", quantity=8000, category="apparel")],
            total_weight_kg=4200, total_volume_cbm=40.0,
            timeframe="COST", declared_value_usd=60000,
        ),
    ),
    (
        "Mid-weight jewelry (luxury), COST  (Milan -> NYC)",
        ShipmentRequest(
            origin={"country": "IT", "state": "MI", "city": "Milan"},
            destination={"country": "US", "state": "NY", "city": "New York"},
            items=[Item(name="gold necklaces", quantity=50, category="jewelry")],
            total_weight_kg=1200, total_volume_cbm=2.0,
            timeframe="COST", declared_value_usd=400000,
        ),
    ),
    (
        "Mid-weight machinery, COST  (Hamburg -> Houston)",
        ShipmentRequest(
            origin={"country": "DE", "state": "HH", "city": "Hamburg"},
            destination={"country": "US", "state": "TX", "city": "Houston"},
            items=[Item(name="industrial pump", quantity=12, category="machinery")],
            total_weight_kg=1500, total_volume_cbm=18.0,
            timeframe="COST", declared_value_usd=90000,
        ),
    ),
]


def main():
    for label, req in SCENARIOS:
        econ = compute_econ_data(req)
        bd = explain(req)["entry_tax_breakdown"]
        print("=" * 78)
        print(label)
        print(f"  input : {req.total_weight_kg} kg | {req.timeframe} | "
              f"declared ${req.declared_value_usd:,.0f} | "
              f"items={[i.name for i in req.items]}")
        print("  EconData (Ashwin's output):")
        print("   ", json.dumps(dump(econ), indent=6).replace("\n", "\n    "))
        print(f"  tax = MPF ${bd['merchandise_processing_fee_usd']:,.2f} + "
              f"duty ${bd['duty_usd']:,.2f} ({bd['duty_rate_pct']}%, {bd['duty_classification']})")
    print("=" * 78)


if __name__ == "__main__":
    main()
