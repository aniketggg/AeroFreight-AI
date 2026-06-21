from typing import List, Literal, Optional

from pydantic import BaseModel

from shared_models import EconData, ShipmentRequest


class RoutingRequest(BaseModel):
    shipment: ShipmentRequest
    econ: EconData


class RouteData(BaseModel):
    """Detailed internal route breakdown used by route_logic."""

    selected_mode: Literal["AIR", "SHIP"]
    optimal_route_nodes: List[str]
    countries_visited: List[str]
    freight_cost_usd: float
    inland_trucking_cost_usd: float
    tolls_and_route_tariffs_usd: float
    entry_tax_usd: float
    freight_and_toll_cost_usd: float
    total_landed_cost_usd: float


class DocTemplates(BaseModel):
    required_form_names: List[str]
    blank_form_structures: dict


class SettlementStatus(BaseModel):
    filled_documents: dict
    final_user_prompt: str
    payment_hash: Optional[str] = None
