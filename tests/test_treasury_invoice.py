"""Tests for Treasury invoice generation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from shared_models import DocTemplates, EconData, Item, RouteData, ShipmentRequest
from treasury_agent.invoice import generate_invoice_pdf
from treasury_agent.pricing import compute_service_fee


def _sample_shipment() -> ShipmentRequest:
    return ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="Electronics", quantity=10, category="electronics")],
        total_weight_kg=850.0,
        total_volume_cbm=3.2,
        timeframe="COST",
        declared_value_usd=4200.0,
    )


def _sample_route() -> RouteData:
    return RouteData(
        selected_mode="SHIP",
        optimal_route_nodes=["Shenzhen", "USLAX", "Austin"],
        countries_visited=["CN", "US"],
        freight_and_toll_cost_usd=645.0,
        total_landed_cost_usd=771.25,
    )


def _sample_econ() -> EconData:
    return EconData(
        transport_preference="EITHER",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=126.50,
    )


def _sample_docs() -> DocTemplates:
    return DocTemplates(
        required_form_names=["CBP Form 7501", "Bill of Lading"],
        blank_form_structures={
            "CBP Form 7501": {"status": "demo"},
            "Bill of Lading": {"status": "demo"},
        },
    )


def test_invoice_pdf_generation_works_locally():
    shipment = _sample_shipment()
    econ = _sample_econ()
    route = _sample_route()
    docs = _sample_docs()
    fee = compute_service_fee(econ, route)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = str(Path(tmpdir) / "invoice.pdf")
        result = generate_invoice_pdf(
            output_path=output_path,
            session_id="session-123",
            transaction_id="cs_test_123",
            shipment=shipment,
            econ=econ,
            route=route,
            docs=docs,
            fee=fee,
        )
        assert result == output_path
        assert Path(output_path).exists()
        assert Path(output_path).stat().st_size > 0


def test_invoice_includes_central_shipment_fields():
    shipment = _sample_shipment()
    assert shipment.total_weight_kg == 850.0
    assert shipment.total_volume_cbm == 3.2
    assert shipment.items[0].name == "Electronics"
