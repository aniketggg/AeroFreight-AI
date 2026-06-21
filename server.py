"""Local HTTP bridge between index.html and the AeroFreight agent pipeline.

Runs the same local/mock economist -> router -> treasury pipeline the
orchestrator uses, then drives a real Stripe embedded checkout and invoice
generation/upload. No uAgents messaging involved - this is a thin synchronous
wrapper for local-only use (`uvicorn server:app --reload`).
"""

from __future__ import annotations

import os
import tempfile
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from shared_models import DocTemplates, EconData, Item, RouteData, ShipmentRequest
from orchestrator.mock_agents import MockEconomistAgent, MockRoutingAgent
from treasury_agent.drive_upload import upload_invoice_and_get_link
from treasury_agent.invoice import generate_invoice_pdf
from treasury_agent.payment_backend import (
    create_settlement_checkout,
    is_configured as stripe_is_configured,
    verify_checkout_paid,
)
from treasury_agent.pricing import compute_service_fee

load_dotenv()

app = FastAPI()

_PENDING: dict[str, dict[str, Any]] = {}

_INDEX_PATH = os.path.join(os.path.dirname(__file__), "index.html")


class DispatchPayload(BaseModel):
    awb_number: str = ""
    priority: str = "COST"
    route: dict[str, Any]
    cargo: dict[str, Any]
    specs: dict[str, Any]


class FinalizePayload(BaseModel):
    session_id: str


def _shipment_from_payload(payload: DispatchPayload) -> ShipmentRequest:
    origin = payload.route.get("origin", {})
    destination = payload.route.get("destination", {})
    line_items = payload.cargo.get("line_items", [])
    items = [
        Item(
            name=item.get("description", "") or "Item",
            quantity=int(item.get("quantity") or 0),
            category=item.get("category", "general"),
        )
        for item in line_items
    ]
    return ShipmentRequest(
        origin={
            "country": origin.get("country", ""),
            "state": origin.get("state", ""),
            "city": origin.get("city", ""),
        },
        destination={
            "country": destination.get("country", ""),
            "state": destination.get("state", ""),
            "city": destination.get("city", ""),
        },
        items=items,
        total_weight_kg=float(payload.specs.get("gross_weight_kg") or 0),
        total_volume_cbm=float(payload.specs.get("volume_cbm") or 0),
        timeframe=payload.priority if payload.priority in ("SPEED", "COST") else "COST",
        declared_value_usd=float(payload.specs.get("declared_value_usd") or 0),
    )


def _doc_templates(shipment: ShipmentRequest) -> DocTemplates:
    return DocTemplates(
        required_form_names=["Commercial Invoice"],
        blank_form_structures={
            "Commercial Invoice": {
                "status": "SIMULATED_DRAFT",
                "origin": shipment.origin,
                "destination": shipment.destination,
                "declared_value_usd": shipment.declared_value_usd,
            }
        },
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_INDEX_PATH)


@app.post("/api/dispatch")
def dispatch(payload: DispatchPayload) -> JSONResponse:
    shipment = _shipment_from_payload(payload)
    econ: EconData = MockEconomistAgent().analyze(shipment)
    route: RouteData = MockRoutingAgent().route(shipment, econ)
    fee = compute_service_fee(econ, route)
    docs = _doc_templates(shipment)

    session_id = uuid4().hex

    quote = {
        "selected_mode": route.selected_mode,
        "route_nodes": route.optimal_route_nodes,
        "freight_and_toll_cost_usd": route.freight_and_toll_cost_usd,
        "base_entry_tax_usd": econ.base_entry_tax_usd,
        "total_landed_cost_usd": route.total_landed_cost_usd,
        "service_fee_usd": fee.total_fee_usd,
    }

    checkout_payload = None
    if stripe_is_configured():
        description = (
            f"Shipment {shipment.origin.get('city', '')} -> "
            f"{shipment.destination.get('city', '')}, {route.selected_mode} mode"
        )
        checkout = create_settlement_checkout(
            user_address=f"web:{session_id}",
            session_id=session_id,
            amount_usd=fee.total_fee_usd,
            description=description,
        )
        if checkout:
            _PENDING[session_id] = {
                "shipment": shipment,
                "econ": econ,
                "route": route,
                "docs": docs,
                "fee": fee,
                "checkout_session_id": checkout["checkout_session_id"],
                "finalized": False,
            }
            checkout_payload = {
                "client_secret": checkout["client_secret"],
                "publishable_key": checkout["publishable_key"],
                "checkout_session_id": checkout["checkout_session_id"],
            }

    return JSONResponse(
        {
            "session_id": session_id,
            "quote": quote,
            "checkout": checkout_payload,
        }
    )


@app.post("/api/finalize")
def finalize(payload: FinalizePayload) -> JSONResponse:
    pending = _PENDING.get(payload.session_id)
    if not pending:
        return JSONResponse({"paid": False, "error": "Unknown session."}, status_code=404)

    if pending["finalized"]:
        return JSONResponse(
            {
                "paid": True,
                "final_message": pending["final_message"],
                "invoice_link": pending["invoice_link"],
            }
        )

    if not verify_checkout_paid(pending["checkout_session_id"]):
        return JSONResponse({"paid": False})

    shipment: ShipmentRequest = pending["shipment"]
    econ: EconData = pending["econ"]
    route: RouteData = pending["route"]
    docs: DocTemplates = pending["docs"]
    fee = pending["fee"]
    transaction_id = pending["checkout_session_id"]

    invoice_path = os.path.join(
        tempfile.gettempdir(),
        f"aerofreight_invoice_{payload.session_id}.pdf",
    )
    generate_invoice_pdf(
        output_path=invoice_path,
        session_id=payload.session_id,
        transaction_id=transaction_id,
        shipment=shipment,
        econ=econ,
        route=route,
        docs=docs,
        fee=fee,
    )
    invoice_link = upload_invoice_and_get_link(
        invoice_path,
        f"AeroFreight_Invoice_{payload.session_id}.pdf",
    )

    route_summary = " -> ".join(route.optimal_route_nodes)
    invoice_line = (
        f"Invoice: {invoice_link}"
        if invoice_link
        else "Invoice: Google Drive upload was skipped or unavailable."
    )
    final_message = (
        "Payment confirmed. Total landed cost "
        f"${route.total_landed_cost_usd:,.2f} USD via {route.selected_mode} "
        f"({route_summary}). Service fee ${fee.total_fee_usd:,.2f} USD paid. "
        f"Stripe reference: {transaction_id}. {invoice_line}"
    )

    pending["finalized"] = True
    pending["final_message"] = final_message
    pending["invoice_link"] = invoice_link

    return JSONResponse(
        {"paid": True, "final_message": final_message, "invoice_link": invoice_link}
    )
