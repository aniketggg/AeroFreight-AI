"""uAgents wire envelopes for Treasury settlement communication."""

from __future__ import annotations

from typing import Any, Optional

from uagents import Model


class SettlementRequestMessage(Model):
    """Wire request from the orchestrator after user confirmation."""

    user_address: str
    session_id: str
    shipment: dict[str, Any]
    econ_data: dict[str, Any]
    route_data: dict[str, Any]
    doc_templates: dict[str, Any]


class SettlementResultMessage(Model):
    """Wire response carrying a central SettlementStatus dictionary."""

    ok: bool
    session_id: str
    settlement_status: Optional[dict[str, Any]] = None
    error: Optional[str] = None
