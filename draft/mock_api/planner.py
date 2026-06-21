"""Synchronous planning pipeline shared by the interactive web UI (POST /plan).

Runs the SAME real data services the agent swarm uses — real USITC duty + real
OpenFlights routing — assembles + persists a Bill of Lading, and returns the full
plan for the frontend. (The uAgents orchestrator produces the identical result
over the agent bus for the ASI:One chat path; this is the direct HTTP path.)
"""

from __future__ import annotations

from uuid import uuid4

from mock_api import carrier_data, store, tariff_data


def build_plan(spec: dict) -> dict:
    """Classify customs + route freight + price + mint escrow + persist BoL."""
    origin = (spec.get("origin") or "").strip().upper()
    destination = (spec.get("destination") or "").strip().upper()
    weight_kg = float(spec.get("weight_kg") or 0) or 1.0
    commodity = (spec.get("commodity") or "general cargo").strip()
    deadline_iso = (spec.get("deadline_iso") or "").strip()
    budget_usd = float(spec.get("budget_usd") or 0)
    declared_value_usd = float(spec.get("declared_value_usd") or 0)

    # Real customs (USITC HTS) + real routing (OpenFlights).
    tariff = tariff_data.classify(commodity, declared_value_usd)
    freight = carrier_data.quote(origin, destination, weight_kg, deadline_iso)

    total = round(freight["total_cost_usd"] + tariff["duty_usd"], 2)
    savings = round(budget_usd - total, 2) if budget_usd else 0.0
    vendor = freight["legs"][0]["carrier"] if freight.get("legs") else "Carrier"
    shipment_ref = f"{origin}-{destination}-{int(weight_kg)}KG"
    contract_id = "fetch1escrow" + uuid4().hex[:12]

    record = {
        "contract_id": contract_id,
        "status": "escrow_pending",
        "shipment_ref": shipment_ref,
        "origin": origin,
        "destination": destination,
        "weight_kg": weight_kg,
        "commodity": commodity,
        "hs_code": tariff["hs_code"],
        "duty_rate_pct": tariff["duty_rate_pct"],
        "duty_usd": tariff["duty_usd"],
        "freight_usd": freight["total_cost_usd"],
        "total_usd": total,
        "budget_usd": budget_usd,
        "savings_usd": savings,
        "transit_days": freight["transit_days"],
        "eta_iso": freight["eta_iso"],
        "deadline_iso": deadline_iso,
        "meets_deadline": freight["meets_deadline"],
        "vendor": vendor,
        "legs": freight["legs"],
    }
    store.save_bol(record)

    return {
        **record,
        "description": tariff.get("description", commodity),
        "within_budget": (budget_usd == 0) or (total <= budget_usd),
        "payment_link": f"/app/escrow.html?cid={contract_id}",
    }
