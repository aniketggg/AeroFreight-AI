"""
Pydantic data models for Neel's Settlement & Payment agent.

These mirror the field names the rest of the AeroFreight pipeline (Ashwin's
EconData, Riya's RouteData, Aniket's DocTemplates) is expected to produce, so
this module can be dropped in next to those without renaming anything. Only
the fields the settlement/payment step actually needs are modeled.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from uagents import Model


class ShipmentRequest(Model):
    origin_country: str
    destination_city: str
    weight_kg: float
    volume_cbm: float
    declared_value_usd: float
    timeframe_preference: str  # "SPEED" or "COST"
    destination_zip: Optional[str] = None
    goods_category: Optional[str] = None


class EconData(Model):
    is_high_value: bool
    entry_tax_usd: float
    mpf_usd: float
    allowed_modes: List[str]  # e.g. ["AIR"], ["AIR", "SHIP"], ["SHIP"]


class RouteData(Model):
    selected_mode: str  # "AIR" or "SHIP"
    freight_cost_usd: float
    tolls_tariffs_usd: float
    inland_cost_usd: float
    total_cost_usd: float
    baseline_cost_usd: float  # what a naive single-mode route would have cost
    countries_visited: List[str]


class DocTemplates(Model):
    doc_names: List[str]
    doc_bodies: Dict[str, str]  # {doc_name: filled_text_or_json}


class SettlementRequest(Model):
    """Sent by the Orchestrator to Neel once the user has typed CONFIRM."""

    user_address: str
    session_id: str
    shipment: ShipmentRequest
    econ: EconData
    route: RouteData
    docs: DocTemplates


class SettlementStatus(Model):
    """Sent by Neel back to the Orchestrator once settlement finishes."""

    session_id: str
    status: str  # "paid" | "rejected" | "unconfigured"
    fee_usd: float
    transaction_id: Optional[str] = None
