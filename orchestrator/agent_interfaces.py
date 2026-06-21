"""Protocol interfaces for remote teammate agent clients."""

from __future__ import annotations

from typing import Protocol

from shared_models import EconData, RouteData, SettlementStatus, ShipmentRequest


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
