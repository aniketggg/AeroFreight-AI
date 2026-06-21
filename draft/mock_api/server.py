"""AeroFreight data + settlement API (FastAPI).

One process serves the agent-facing data endpoints (real USITC tariff data + real
OpenFlights routing), the **on-chain escrow authorization** endpoint, and the
static Bill-of-Lading / escrow page (mounted at ``/app``).

Routes
------
POST /tariff/classify                 -> real USITC HTS duty lookup
POST /freight/quote                   -> real OpenFlights routing + transparent pricing
POST /bol                             -> register a Bill of Lading (persisted in SQLite)
GET  /bol/{contract_id}               -> fetch a registered BoL (consumed by escrow.html)
POST /escrow/{contract_id}/authorize  -> sign + broadcast a REAL on-chain escrow tx (Fetch testnet)
GET  /escrow/info                     -> platform/vault addresses + balance (diagnostics)
GET  /health                          -> liveness probe
GET  /app/escrow.html                 -> the static settlement page
"""

from __future__ import annotations

import datetime
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.config import DEFAULT_DECLARED_VALUE_USD
from agents.parser import parse_request
from mock_api import carrier_data, chain, planner, store, tariff_data

app = FastAPI(title="AeroFreight API", version="2.0.0")

# The static page calls the API from the same origin; CORS open keeps it painless.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Tariff / customs  (real USITC HTS data)
# --------------------------------------------------------------------------- #
class TariffIn(BaseModel):
    commodity: str
    declared_value_usd: float


@app.post("/tariff/classify")
def tariff_classify(body: TariffIn) -> dict:
    return tariff_data.classify(body.commodity, body.declared_value_usd)


# --------------------------------------------------------------------------- #
# Freight routing  (real OpenFlights data)
# --------------------------------------------------------------------------- #
class FreightIn(BaseModel):
    origin: str
    destination: str
    weight_kg: float
    deadline_iso: str


@app.post("/freight/quote")
def freight_quote(body: FreightIn) -> dict:
    return carrier_data.quote(
        body.origin, body.destination, body.weight_kg, body.deadline_iso
    )


# --------------------------------------------------------------------------- #
# Interactive planning  (POST /plan) — drives the whole pipeline from the web UI
# --------------------------------------------------------------------------- #
class PlanIn(BaseModel):
    """Either a free-text request, or structured fields (or both — fields win)."""

    text: str | None = None
    origin: str | None = None
    destination: str | None = None
    weight_kg: float | None = None
    commodity: str | None = None
    deadline_iso: str | None = None
    budget_usd: float | None = None
    declared_value_usd: float | None = None


_STRUCT_FIELDS = (
    "origin", "destination", "weight_kg", "commodity",
    "deadline_iso", "budget_usd", "declared_value_usd",
)


@app.post("/plan")
def plan(body: PlanIn) -> dict:
    """Classify + route + price a shipment with REAL data and return the plan."""
    if body.text and body.text.strip():
        # Natural-language path: parse, then let any structured fields override.
        spec = parse_request(body.text).model_dump()
        for field in _STRUCT_FIELDS:
            value = getattr(body, field)
            if value not in (None, ""):
                spec[field] = value
    else:
        spec = {field: getattr(body, field) for field in _STRUCT_FIELDS}

    if not spec.get("origin") or not spec.get("destination"):
        raise HTTPException(
            status_code=422,
            detail="Provide an origin and destination (IATA codes) — or a sentence describing the shipment.",
        )
    if not spec.get("deadline_iso"):
        spec["deadline_iso"] = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    if not spec.get("declared_value_usd"):
        spec["declared_value_usd"] = DEFAULT_DECLARED_VALUE_USD

    return planner.build_plan(spec)


# --------------------------------------------------------------------------- #
# Bill of Lading (assembled by the orchestrator, rendered by escrow.html)
# --------------------------------------------------------------------------- #
class BoLLeg(BaseModel):
    mode: str
    carrier: str
    service: str
    from_node: str
    to_node: str


class BoLRecord(BaseModel):
    """Full Bill-of-Lading record. Typed so malformed/partial records are rejected
    with a 422 instead of reaching the page and rendering a half-empty BoL."""

    contract_id: str
    status: str = "escrow_pending"
    shipment_ref: str
    origin: str
    destination: str
    weight_kg: float
    commodity: str
    hs_code: str
    duty_rate_pct: float
    duty_usd: float
    freight_usd: float
    total_usd: float
    budget_usd: float
    savings_usd: float
    transit_days: int
    eta_iso: str
    deadline_iso: str
    meets_deadline: bool
    vendor: str
    legs: list[BoLLeg]


@app.post("/bol")
def save_bol(record: BoLRecord) -> dict:
    store.save_bol(record.model_dump())
    return {"ok": True, "contract_id": record.contract_id}


@app.get("/bol/{contract_id}")
def get_bol(contract_id: str) -> dict:
    record = store.get_bol(contract_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Bill of Lading not found")
    return record


# --------------------------------------------------------------------------- #
# Escrow settlement — REAL on-chain transaction on the Fetch.ai testnet
# --------------------------------------------------------------------------- #
def _settlement_view(record: dict) -> dict:
    return {
        "ok": True,
        "contract_id": record["contract_id"],
        "status": record.get("status"),
        "tx_hash": record.get("tx_hash"),
        "explorer_url": record.get("explorer_url"),
        "vault_address": record.get("vault_address"),
        "amount_fet": record.get("amount_fet"),
        "chain_id": record.get("chain_id"),
        "network": record.get("network"),
    }


@app.post("/escrow/{contract_id}/authorize")
def authorize_escrow(contract_id: str) -> dict:
    """Lock the escrow on-chain: a real, explorer-verifiable Fetch.ai testnet tx."""
    record = store.get_bol(contract_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Bill of Lading not found")

    # Idempotent: if already settled, return the existing on-chain reference.
    if record.get("status") == "funded" and record.get("tx_hash"):
        return _settlement_view(record)

    memo = f"AeroFreight {contract_id} {record.get('shipment_ref', '')}".strip()
    try:
        result = chain.submit_escrow(contract_id, memo=memo)
    except Exception as exc:  # noqa: BLE001 — surface a graceful, structured error
        raise HTTPException(
            status_code=503,
            detail={
                "error": "settlement_unavailable",
                "message": str(exc),
                "platform_address": chain.platform_address(),
                "hint": "Fund the platform address via the dorado faucet to enable settlement.",
            },
        )

    updated = store.update_bol(
        contract_id,
        status="funded",
        tx_hash=result["tx_hash"],
        explorer_url=result["explorer_url"],
        vault_address=result["vault_address"],
        platform_address=result["from_address"],
        amount_fet=result["amount_fet"],
        chain_id=result["chain_id"],
        network=result["network"],
    )
    return _settlement_view(updated or record)


@app.get("/escrow/info")
def escrow_info() -> dict:
    try:
        return {
            "platform_address": chain.platform_address(),
            "vault_address": chain.vault_address(),
            "balance_afet": chain.balance(),
            "chain_id": chain.NETWORK.chain_id,
            "network": "fetchai-dorado-testnet",
            "explorer": chain.EXPLORER_TX.format(hash="<hash>"),
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Static success page  (GET /app/escrow.html)
# --------------------------------------------------------------------------- #
@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/app/index.html")


_WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
os.makedirs(_WEB_DIR, exist_ok=True)
app.mount("/app", StaticFiles(directory=_WEB_DIR, html=True), name="app")
