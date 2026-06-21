"""Deterministic mock teammate agents for local demonstration."""

from __future__ import annotations

import hashlib

from shared_models import EconData, RouteData, SettlementStatus, ShipmentRequest

LUXURY_KEYWORDS = (
    "luxury",
    "jewelry",
    "jewellery",
    "designer",
    "watch",
    "watches",
)


def _is_luxury_item(name: str, category: str) -> bool:
    combined = f"{name} {category}".lower()
    return any(keyword in combined for keyword in LUXURY_KEYWORDS)


class MockEconomistAgent:
    """Demo economics agent — not a real tariff or customs calculation."""

    def analyze(self, shipment: ShipmentRequest) -> EconData:
        is_high_value = shipment.declared_value_usd > 2500
        is_luxury = any(
            _is_luxury_item(item.name, item.category) for item in shipment.items
        )

        if shipment.total_weight_kg <= 500:
            transport_preference = "AIR"
        elif is_luxury:
            transport_preference = "AIR"
        elif shipment.timeframe == "SPEED":
            transport_preference = "AIR"
        elif shipment.total_weight_kg <= 2000:
            transport_preference = "EITHER"
        else:
            transport_preference = "SHIP"

        # Simulated entry tax — not a real tariff calculation.
        mock_processing_fee = max(30.0, shipment.declared_value_usd * 0.0035)
        mock_category_tariff = shipment.declared_value_usd * 0.05
        base_entry_tax_usd = round(mock_processing_fee + mock_category_tariff, 2)

        return EconData(
            transport_preference=transport_preference,
            is_high_value=is_high_value,
            is_luxury=is_luxury,
            base_entry_tax_usd=base_entry_tax_usd,
        )


class MockRoutingAgent:
    """Demo routing agent — simulated freight costs, not market prices."""

    def route(self, shipment: ShipmentRequest, econ_data: EconData) -> RouteData:
        selected_mode = _select_mode(econ_data.transport_preference, shipment.timeframe)

        if selected_mode == "AIR":
            freight_cost = (
                shipment.total_weight_kg * 5.0 + shipment.total_volume_cbm * 125.0
            )
            transit_charges = 450.0
        else:
            freight_cost = (
                shipment.total_weight_kg * 1.25 + shipment.total_volume_cbm * 70.0
            )
            transit_charges = 900.0

        freight_and_toll_cost_usd = round(freight_cost + transit_charges, 2)
        total_landed_cost_usd = round(
            freight_and_toll_cost_usd + econ_data.base_entry_tax_usd,
            2,
        )

        origin_city = str(shipment.origin.get("city", "Origin"))
        dest_city = str(shipment.destination.get("city", "Destination"))
        origin_country = str(shipment.origin.get("country", "XX")).upper()
        dest_country = str(shipment.destination.get("country", "US")).upper()

        if selected_mode == "AIR":
            optimal_route_nodes = [
                f"{origin_city} Export Airport",
                "United States Air Gateway",
                f"{dest_city} Final Delivery",
            ]
        else:
            optimal_route_nodes = [
                f"{origin_city} Export Port",
                "United States Ocean Port",
                f"{dest_city} Final Delivery",
            ]

        countries_visited = list(dict.fromkeys([origin_country, dest_country]))

        return RouteData(
            selected_mode=selected_mode,
            optimal_route_nodes=optimal_route_nodes,
            countries_visited=countries_visited,
            freight_and_toll_cost_usd=freight_and_toll_cost_usd,
            total_landed_cost_usd=total_landed_cost_usd,
        )


def _select_mode(
    transport_preference: str,
    timeframe: str,
) -> str:
    if transport_preference == "AIR":
        return "AIR"
    if transport_preference == "SHIP":
        return "SHIP"
    if timeframe == "SPEED":
        return "AIR"
    return "SHIP"


class MockTreasuryAgent:
    """Demo treasury agent — simulated documents and payment only."""

    def prepare_quote(
        self,
        shipment: ShipmentRequest,
        econ_data: EconData,
        route_data: RouteData,
    ) -> SettlementStatus:
        route_summary = " → ".join(route_data.optimal_route_nodes)
        quote = (
            "## AeroFreight AI Shipment Quote\n\n"
            f"**Suggested mode:** {route_data.selected_mode}\n\n"
            f"**Route:** {route_summary}\n\n"
            f"**Freight and transit charges:** "
            f"${route_data.freight_and_toll_cost_usd:,.2f} USD\n\n"
            f"**Entry tax:** ${econ_data.base_entry_tax_usd:,.2f} USD\n\n"
            f"**Total landed cost:** "
            f"${route_data.total_landed_cost_usd:,.2f} USD\n\n"
            "*Warning: All values in this quote are simulated demo values "
            "and are not current market prices or legal customs assessments.*\n\n"
            "Type CONFIRM to execute payment."
        )

        return SettlementStatus(
            filled_documents={
                "commercial_invoice": {
                    "status": "SIMULATED_DRAFT",
                    "origin": shipment.origin,
                    "destination": shipment.destination,
                    "declared_value_usd": shipment.declared_value_usd,
                }
            },
            final_user_prompt=quote,
            payment_hash=None,
        )

    def execute_payment(
        self,
        shipment: ShipmentRequest,
        route_data: RouteData,
    ) -> SettlementStatus:
        digest_input = shipment.model_dump_json() + route_data.model_dump_json()
        digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:20]
        payment_hash = f"SIMULATED_{digest}"

        completion_message = (
            "## AeroFreight AI Payment Simulation Complete\n\n"
            "No real payment occurred. This is a local demo only.\n\n"
            f"**Simulated payment reference:** `{payment_hash}`\n\n"
            "Type NEW SHIPMENT to begin another workflow."
        )

        return SettlementStatus(
            filled_documents={
                "commercial_invoice": {
                    "status": "SIMULATED_PAID",
                    "payment_reference": payment_hash,
                }
            },
            final_user_prompt=completion_message,
            payment_hash=payment_hash,
        )
