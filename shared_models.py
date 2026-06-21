from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class Item(BaseModel):
    name: str
    quantity: int
    category: str


class ShipmentRequest(BaseModel):
    origin: dict = Field(
        ...,
        description="{'country': 'CN', 'state': 'Guangdong', 'city': 'Shenzhen'}",
    )
    destination: dict = Field(
        ...,
        description="{'country': 'US', 'state': 'TX', 'city': 'Austin'}",
    )
    items: List[Item]
    total_weight_kg: float
    total_volume_cbm: float
    timeframe: Literal["SPEED", "COST"]
    declared_value_usd: float


class EconData(BaseModel):
    transport_preference: Literal["AIR", "SHIP", "EITHER"]
    is_high_value: bool
    is_luxury: bool
    base_entry_tax_usd: float


class RouteData(BaseModel):
    selected_mode: Literal["AIR", "SHIP"]
    optimal_route_nodes: List[str]
    countries_visited: List[str]
    freight_and_toll_cost_usd: float
    total_landed_cost_usd: float


class DocTemplates(BaseModel):
    required_form_names: List[str]
    blank_form_structures: dict


class SettlementStatus(BaseModel):
    filled_documents: dict
    final_user_prompt: str
    payment_hash: Optional[str] = None
