from pydantic import BaseModel, Field
from typing import List, Literal, Optional

# --- STEP 1: ORCHESTRATOR OUTPUT ---
class Item(BaseModel):
    name: str
    quantity: int
    category: str

class ShipmentRequest(BaseModel):
    origin: dict = Field(..., description="{'country': 'CN', 'state': 'Guangdong', 'city': 'Shenzhen'}")
    destination: dict = Field(..., description="{'country': 'US', 'state': 'TX', 'city': 'Austin'}")
    items: List[Item]
    total_weight_kg: float
    total_volume_cbm: float
    timeframe: Literal["SPEED", "COST"]
    declared_value_usd: float

# --- STEP 2: ASHWIN'S OUTPUT ---
class EconData(BaseModel):
    transport_preference: Literal["AIR", "SHIP", "EITHER"]
    is_high_value: bool
    is_luxury: bool
    base_entry_tax_usd: float

# --- STEP 3: RIYA'S OUTPUT ---
class RouteData(BaseModel):
    selected_mode: Literal["AIR", "SHIP"]
    optimal_route_nodes: List[str] # e.g. ["SZX", "LAX", "Austin"]
    countries_visited: List[str]
    freight_and_toll_cost_usd: float
    total_landed_cost_usd: float # Includes Ashwin's entry tax

# --- STEP 4: ANIKET'S OUTPUT ---
class DocTemplates(BaseModel):
    required_form_names: List[str]
    blank_form_structures: dict # The empty templates found via browser

# --- STEP 5: NEEL'S OUTPUT ---
class SettlementStatus(BaseModel):
    filled_documents: dict # The completed forms
    final_user_prompt: str # The Markdown string asking for "CONFIRM"
    payment_hash: Optional[str] = None
