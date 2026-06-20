"""Frozen message contracts — the integration spine.

Every agent codes against these. Changing a field here is a breaking change for
the orchestrator and every sub-agent; coordinate before editing.

All wire types are ``uagents.Model`` (pydantic-backed) so they serialize over
the agent transport.
"""

from typing import List

from uagents import Model


# --------------------------------------------------------------------------- #
# Parsed intent (produced by the orchestrator's rule-based parser)
# --------------------------------------------------------------------------- #
class ShipmentSpec(Model):
    origin: str                 # IATA code, e.g. "SZX"
    destination: str            # IATA code, e.g. "AUS"
    weight_kg: float
    commodity: str              # free text, e.g. "semiconductor components"
    deadline_iso: str           # normalized "YYYY-MM-DD"
    budget_usd: float
    declared_value_usd: float   # goods value for the duty calc (parser default)


# --------------------------------------------------------------------------- #
# Tariff agent  (POST /tariff/classify)
# --------------------------------------------------------------------------- #
class TariffRequest(Model):
    commodity: str
    declared_value_usd: float


class TariffResponse(Model):
    hs_code: str                # "8541.10"
    description: str            # "Semiconductor devices"
    duty_rate_pct: float        # 2.5
    duty_usd: float             # round(rate/100 * declared_value, 2)


# --------------------------------------------------------------------------- #
# Freight-Router agent  (POST /freight/quote)
# --------------------------------------------------------------------------- #
class FreightLeg(Model):
    mode: str                   # "air" | "ground"
    carrier: str                # "Cathay Pacific Cargo"
    service: str                # "CX086" | "FedEx Priority"
    from_node: str              # "SZX"
    to_node: str                # "LAX"


class FreightRequest(Model):
    origin: str
    destination: str
    weight_kg: float
    deadline_iso: str


class FreightResponse(Model):
    legs: List[FreightLeg]
    total_cost_usd: float
    transit_days: int
    eta_iso: str                # "YYYY-MM-DD"
    meets_deadline: bool


# --------------------------------------------------------------------------- #
# Escrow & Payment agent  (in-process; registers a BoL via POST /bol)
# --------------------------------------------------------------------------- #
class EscrowRequest(Model):
    total_usd: float
    vendor: str                 # e.g. "Cathay Pacific Cargo"
    shipment_ref: str           # short id, e.g. "SZX-AUS-200KG"


class EscrowResponse(Model):
    contract_id: str            # "fetch1escrow..." (mock)
    payment_link: str           # ".../app/escrow.html?cid=..."
    status: str                 # "pending_authorization"
