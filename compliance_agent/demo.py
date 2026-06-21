"""Demo for Step 4 (Aniket) — prints the required-forms packet for sample shipments.

Run from the repo root:
    python -m compliance_agent.demo

Edit the SCENARIOS list below to try your own cargo/route. No uAgents needed —
this calls the pure logic directly (simulated retrieval, fully offline).
"""

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from compliance_agent.compliance import compute_doc_templates, explain  # noqa: E402
from shared_models import (  # noqa: E402
    ComplianceRequest,
    EconData,
    Item,
    RouteData,
    ShipmentRequest,
)


def _req(shipment, econ, route) -> ComplianceRequest:
    return ComplianceRequest(shipment=shipment, econ=econ, route=route)


# (label, ComplianceRequest) — tweak these freely.
SCENARIOS = [
    (
        "Light high-value semiconductors, AIR  (Shenzhen -> Austin)",
        _req(
            ShipmentRequest(
                origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
                destination={"country": "US", "state": "TX", "city": "Austin"},
                items=[Item(name="semiconductor components", quantity=500,
                            category="electronics")],
                total_weight_kg=200, total_volume_cbm=3.0,
                timeframe="SPEED", declared_value_usd=2800,
            ),
            EconData(transport_preference="AIR", is_high_value=True,
                     is_luxury=False, base_entry_tax_usd=32.71),
            RouteData(selected_mode="AIR",
                      optimal_route_nodes=["SZX", "HKG", "LAX", "Austin"],
                      countries_visited=["CN", "HK", "US"],
                      freight_and_toll_cost_usd=5200.0,
                      total_landed_cost_usd=8032.71),
        ),
    ),
    (
        "Heavy cotton apparel, SHIP  (Mumbai -> Newark)",
        _req(
            ShipmentRequest(
                origin={"country": "IN", "state": "MH", "city": "Mumbai"},
                destination={"country": "US", "state": "NJ", "city": "Newark"},
                items=[Item(name="cotton t-shirts", quantity=8000,
                            category="apparel")],
                total_weight_kg=4200, total_volume_cbm=40.0,
                timeframe="COST", declared_value_usd=60000,
            ),
            EconData(transport_preference="SHIP", is_high_value=True,
                     is_luxury=False, base_entry_tax_usd=10107.84),
            RouteData(selected_mode="SHIP",
                      optimal_route_nodes=["Nhava Sheva", "Suez", "Newark"],
                      countries_visited=["IN", "EG", "US"],
                      freight_and_toll_cost_usd=6400.0,
                      total_landed_cost_usd=76507.84),
        ),
    ),
    (
        "Lithium battery packs, AIR  (Shenzhen -> LA)  [dangerous goods]",
        _req(
            ShipmentRequest(
                origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
                destination={"country": "US", "state": "CA", "city": "Los Angeles"},
                items=[Item(name="lithium-ion battery packs", quantity=300,
                            category="battery")],
                total_weight_kg=450, total_volume_cbm=4.0,
                timeframe="SPEED", declared_value_usd=45000,
            ),
            EconData(transport_preference="AIR", is_high_value=True,
                     is_luxury=False, base_entry_tax_usd=1683.00),
            RouteData(selected_mode="AIR",
                      optimal_route_nodes=["SZX", "ANC", "LAX"],
                      countries_visited=["CN", "US"],
                      freight_and_toll_cost_usd=8800.0,
                      total_landed_cost_usd=55483.00),
        ),
    ),
    (
        "Mexican tequila by truck/ship, USMCA  (Guadalajara -> Houston)",
        _req(
            ShipmentRequest(
                origin={"country": "MX", "state": "JAL", "city": "Guadalajara"},
                destination={"country": "US", "state": "TX", "city": "Houston"},
                items=[Item(name="bottled tequila", quantity=5000,
                            category="alcohol")],
                total_weight_kg=6000, total_volume_cbm=22.0,
                timeframe="COST", declared_value_usd=120000,
            ),
            EconData(transport_preference="SHIP", is_high_value=True,
                     is_luxury=False, base_entry_tax_usd=634.62),
            RouteData(selected_mode="SHIP",
                      optimal_route_nodes=["Manzanillo", "Houston"],
                      countries_visited=["MX", "US"],
                      freight_and_toll_cost_usd=4100.0,
                      total_landed_cost_usd=124734.62),
        ),
    ),
]


def main():
    for label, req in SCENARIOS:
        docs = compute_doc_templates(req)
        info = explain(req)
        print("=" * 78)
        print(label)
        print(f"  route : mode={info['mode']} | countries={info['countries_visited']} | "
              f"formal_entry={info['is_formal_entry']}")
        print(f"  required forms ({len(docs.required_form_names)}):")
        for name in docs.required_form_names:
            print(f"    - {name}   [{info['form_agencies'][name]}]  {info['form_sources'][name]}")
        # Show one blank skeleton so it's clear what Neel (Step 5) receives.
        first = docs.required_form_names[0]
        print(f"  sample blank structure — {first}:")
        print("   ", json.dumps(docs.blank_form_structures[first], indent=6)
              .replace("\n", "\n    "))
    print("=" * 78)


if __name__ == "__main__":
    main()
