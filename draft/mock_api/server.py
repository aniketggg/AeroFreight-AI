"""AeroFreight mock data API (FastAPI).

One process serves both the agent-facing data endpoints and the static
Bill-of-Lading / escrow success page (mounted at ``/app``).

Routes
------
POST /tariff/classify   -> mock_api.tariff_data.classify(...)
POST /freight/quote     -> mock_api.carrier_data.quote(...)
POST /bol               -> register a full Bill-of-Lading record (by contract_id)
GET  /bol/{contract_id} -> fetch a registered BoL record (consumed by escrow.html)
GET  /health            -> liveness probe (used by run_demo to wait for readiness)
GET  /app/escrow.html   -> the static success page
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mock_api import carrier_data, tariff_data

app = FastAPI(title="AeroFreight Mock Data API", version="1.0.0")

# The static page fetches /bol/{cid} from the same origin, so CORS isn't strictly
# required — but allowing it keeps things painless if the page is opened elsewhere.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory Bill-of-Lading store (demo only; resets each run).
_BOL_STORE: dict[str, dict] = {}


# --------------------------------------------------------------------------- #
# Tariff / customs
# --------------------------------------------------------------------------- #
class TariffIn(BaseModel):
    commodity: str
    declared_value_usd: float


@app.post("/tariff/classify")
def tariff_classify(body: TariffIn) -> dict:
    return tariff_data.classify(body.commodity, body.declared_value_usd)


# --------------------------------------------------------------------------- #
# Freight routing
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
# Bill of Lading (assembled by the orchestrator, rendered by escrow.html)
# --------------------------------------------------------------------------- #
@app.post("/bol")
def save_bol(record: dict) -> dict:
    contract_id = record.get("contract_id")
    if not contract_id:
        raise HTTPException(status_code=400, detail="record must include 'contract_id'")
    _BOL_STORE[contract_id] = record
    return {"ok": True, "contract_id": contract_id}


@app.get("/bol/{contract_id}")
def get_bol(contract_id: str) -> dict:
    record = _BOL_STORE.get(contract_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Bill of Lading not found")
    return record


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "bols": len(_BOL_STORE)}


# --------------------------------------------------------------------------- #
# Static success page  (GET /app/escrow.html)
# --------------------------------------------------------------------------- #
_WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
os.makedirs(_WEB_DIR, exist_ok=True)  # ensure the mount target exists at import time
app.mount("/app", StaticFiles(directory=_WEB_DIR, html=True), name="app")
