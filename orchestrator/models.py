from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shared_models import (
    EconData,
    RouteData,
    SettlementStatus,
    ShipmentRequest,
)


class WorkflowStage(StrEnum):
    COLLECTING_INPUT = "COLLECTING_INPUT"
    READY_FOR_ECONOMIST = "READY_FOR_ECONOMIST"
    CALLING_ECONOMIST = "CALLING_ECONOMIST"
    CALLING_ROUTER = "CALLING_ROUTER"
    CALLING_TREASURY = "CALLING_TREASURY"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    EXECUTING_PAYMENT = "EXECUTING_PAYMENT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PartialItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    quantity: int | None = None
    category: str | None = None


class PartialShipmentData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: dict | None = None
    destination: dict | None = None
    items: list[PartialItem] | None = None
    total_weight_kg: float | None = None
    total_volume_cbm: float | None = None
    timeframe: Literal["SPEED", "COST"] | None = None
    declared_value_usd: float | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OrchestratorSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender_address: str
    stage: WorkflowStage = WorkflowStage.COLLECTING_INPUT
    partial_data: PartialShipmentData = Field(default_factory=PartialShipmentData)
    shipment_request: ShipmentRequest | None = None
    econ_data: EconData | None = None
    route_data: RouteData | None = None
    settlement_status: SettlementStatus | None = None
    last_error: str | None = None
    retry_count: int = 0
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def touch(self) -> None:
        """Update the session timestamp to the current timezone-aware UTC time."""
        self.updated_at = _utc_now()
