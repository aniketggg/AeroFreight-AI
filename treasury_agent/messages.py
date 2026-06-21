"""uAgents wire envelopes for Treasury settlement communication."""

from __future__ import annotations

from typing import Any, Optional

from uagents import Model


class SettlementRequestMessage(Model):
    """Legacy wire request from the orchestrator after user confirmation."""

    user_address: str
    session_id: str
    shipment: dict[str, Any]
    econ_data: dict[str, Any]
    route_data: dict[str, Any]
    doc_templates: dict[str, Any]


class SettlementResultMessage(Model):
    """Legacy wire response carrying a central SettlementStatus dictionary."""

    ok: bool
    session_id: str
    settlement_status: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class PaymentSetupRequestMessage(Model):
    """Request Stripe Checkout setup from the orchestrator."""

    user_address: str
    session_id: str
    shipment: dict[str, Any]
    econ_data: dict[str, Any]
    route_data: dict[str, Any]
    doc_templates: dict[str, Any]


class PaymentSetupResponseMessage(Model):
    """Immediate checkout setup response for the orchestrator payment wall."""

    ok: bool
    session_id: str
    checkout: Optional[dict[str, Any]] = None
    fee_usd: Optional[float] = None
    error: Optional[str] = None


class PaymentFinalizeRequestMessage(Model):
    """Request post-payment verification, invoice generation, and settlement."""

    user_address: str
    session_id: str
    checkout_session_id: str
    transaction_id: str


class PaymentFinalizeResponseMessage(Model):
    """Final settlement response after Stripe verification."""

    ok: bool
    session_id: str
    settlement_status: Optional[dict[str, Any]] = None
    error: Optional[str] = None
