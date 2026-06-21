"""Protocol interfaces for remote teammate agent clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from shared_models import (
    DocTemplates,
    EconData,
    RouteData,
    SettlementStatus,
    ShipmentRequest,
)


class EconomistAgentClient(Protocol):
    def analyze(self, shipment: ShipmentRequest) -> EconData:
        ...


class RoutingAgentClient(Protocol):
    def route(self, shipment: ShipmentRequest, econ_data: EconData) -> RouteData:
        ...


class TreasuryAgentClient(Protocol):
    def prepare_quote(
        self,
        shipment: ShipmentRequest,
        econ_data: EconData,
        route_data: RouteData,
    ) -> SettlementStatus:
        ...

    def execute_payment(
        self,
        shipment: ShipmentRequest,
        route_data: RouteData,
    ) -> SettlementStatus:
        ...


@dataclass(frozen=True)
class PaymentSetupResult:
    checkout: dict[str, Any]
    fee_usd: float


class TreasuryPaymentClient(Protocol):
    async def prepare_payment(
        self,
        *,
        user_address: str,
        session_id: str,
        shipment: ShipmentRequest,
        econ_data: EconData,
        route_data: RouteData,
        doc_templates: DocTemplates,
    ) -> PaymentSetupResult:
        ...

    async def finalize_payment(
        self,
        *,
        user_address: str,
        session_id: str,
        checkout_session_id: str,
        transaction_id: str,
    ) -> SettlementStatus:
        ...
