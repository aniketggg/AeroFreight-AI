"""AeroFreight AI — frozen data contracts (the integration spine).

Every agent in the hub-and-spoke swarm codes against these models. They are the
*exact* Pydantic models from the official workflow spec; changing a field here
is a breaking change for the orchestrator and every sub-agent, so coordinate
before editing.

Base class
----------
The agents communicate over the **uAgents** transport, which requires message
types to subclass ``uagents.Model`` (it derives the schema digest used for
routing). ``uagents.Model`` *is* a Pydantic ``BaseModel`` subclass — so this is
still "uAgents framework with Pydantic" and the field declarations are exactly
as written in the spec.

We import ``uagents.Model`` when available and fall back to a plain Pydantic
``BaseModel`` otherwise. The fallback lets the pure business logic (and its
unit tests) run without the full agent stack installed; the field shapes are
identical, so the orchestrator's Anthropic/OpenAI structured-output calls and
``.model_json_schema()`` work the same either way.
"""

from typing import List, Literal, Optional

# IMPORTANT: uAgents 0.25.x is built on **pydantic v1** (its ``Model``
# subclasses ``pydantic.v1.BaseModel``). ``Field`` must come from the SAME
# pydantic API as the base class, or the schema digest uAgents derives for
# routing breaks ("FieldInfo is not JSON serializable"). So we match it.
try:  # Production / agent runtime: real wire model with a schema digest.
    from uagents import Model as _Base

    try:
        from pydantic.v1 import Field  # pydantic v2 present; uAgents' v1 shim
    except ImportError:
        from pydantic import Field  # a genuine pydantic v1 install
except ImportError:  # Logic + tests without the agent stack installed.
    from pydantic import BaseModel as _Base, Field


def dump(model) -> dict:
    """Version-agnostic dict export (v2 ``model_dump`` / v1 ``dict``)."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


# --------------------------------------------------------------------------- #
# STEP 1: ORCHESTRATOR OUTPUT  (-> Ashwin)
# --------------------------------------------------------------------------- #
class Item(_Base):
    name: str
    quantity: int
    category: str


class ShipmentRequest(_Base):
    origin: dict = Field(
        ..., description="{'country': 'CN', 'state': 'Guangdong', 'city': 'Shenzhen'}"
    )
    destination: dict = Field(
        ..., description="{'country': 'US', 'state': 'TX', 'city': 'Austin'}"
    )
    items: List[Item]
    total_weight_kg: float
    total_volume_cbm: float
    timeframe: Literal["SPEED", "COST"]
    declared_value_usd: float


# --------------------------------------------------------------------------- #
# STEP 2: ASHWIN'S OUTPUT  (Economic & Constraints Agent)
# --------------------------------------------------------------------------- #
class EconData(_Base):
    transport_preference: Literal["AIR", "SHIP", "EITHER"]
    is_high_value: bool
    is_luxury: bool
    base_entry_tax_usd: float


# --------------------------------------------------------------------------- #
# STEP 3: RIYA'S OUTPUT  (Pathfinding & Routing Agent)
# --------------------------------------------------------------------------- #
class RouteData(_Base):
    selected_mode: Literal["AIR", "SHIP"]
    optimal_route_nodes: List[str]  # e.g. ["SZX", "LAX", "Austin"]
    countries_visited: List[str]
    freight_and_toll_cost_usd: float
    total_landed_cost_usd: float  # Includes Ashwin's entry tax


# --------------------------------------------------------------------------- #
# STEP 4: ANIKET'S INPUT + OUTPUT  (Compliance & Document Agent)
# --------------------------------------------------------------------------- #
class ComplianceRequest(_Base):
    """Orchestrator -> Aniket: the accumulated 'Global State' the compliance
    agent needs to pick the right paperwork (Inputs + Ashwin + Riya).

    Additive contract (does not alter the frozen output models): the
    orchestrator nests the three upstream artifacts so Aniket can key forms off
    the cargo, the chosen transport mode, and the countries on the route.
    """

    shipment: ShipmentRequest  # Step 1 — original request (cargo, value, route)
    econ: EconData             # Step 2 — Ashwin (high-value / luxury flags)
    route: RouteData           # Step 3 — Riya (selected_mode, countries_visited)


class DocTemplates(_Base):
    required_form_names: List[str]
    blank_form_structures: dict  # The empty templates found via browser


# --------------------------------------------------------------------------- #
# STEP 5: NEEL'S OUTPUT  (Treasury & Execution Agent)
# --------------------------------------------------------------------------- #
class SettlementStatus(_Base):
    filled_documents: dict  # The completed forms
    final_user_prompt: str  # The Markdown string asking for "CONFIRM"
    payment_hash: Optional[str] = None
