from typing import Any, Literal, Optional

from pydantic.v1 import Field
from uagents import Model


class QuoteRequest(Model):
    """Request sent by Riya to an AIR or SHIP Fetch.ai sub-agent."""

    shipment: dict[str, Any]
    econ: dict[str, Any]


class QuoteResponse(Model):
    """Internal quote returned by an AIR or SHIP Fetch.ai sub-agent."""

    ok: bool
    mode: Literal["AIR", "SHIP"]

    optimal_route_nodes: list[str] = Field(default_factory=list)
    countries_visited: list[str] = Field(default_factory=list)

    freight_cost_usd: float = 0.0
    inland_trucking_cost_usd: float = 0.0
    tolls_and_route_tariffs_usd: float = 0.0
    freight_and_toll_cost_usd: float = 0.0
    estimated_transit_days: float = 0.0

    error: Optional[str] = None
