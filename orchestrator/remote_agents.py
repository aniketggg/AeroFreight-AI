"""Remote uAgents clients for orchestrator teammate integration."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from economic_agent.messages import EconomistError, EconomistRequest, EconomistResponse
from shared_models import (
    DocTemplates,
    EconData,
    RouteData,
    SettlementStatus,
    ShipmentRequest,
)
from step3_riya.agent import RouteRequestMessage, RouteResponseMessage
from treasury_agent.messages import (
    PaymentFinalizeRequestMessage,
    PaymentFinalizeResponseMessage,
    PaymentSetupRequestMessage,
    PaymentSetupResponseMessage,
)

from orchestrator.agent_interfaces import PaymentSetupResult
from orchestrator.payment_trace import payment_trace, summarize_checkout, normalize_fetch_checkout_metadata


class RemoteEconomistError(RuntimeError):
    """Raised when the remote Economist agent cannot be reached or reply is invalid."""


class UAgentsEconomistClient:
    """Call Ashwin's Economist uAgent via Context.send_and_receive."""

    def __init__(
        self,
        context: Any,
        destination: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.context = context
        self.destination = destination
        self.timeout_seconds = timeout_seconds

    async def analyze(self, shipment: ShipmentRequest) -> EconData:
        """Send ShipmentRequest JSON and return parsed EconData from the remote agent."""
        request = EconomistRequest(shipment_json=shipment.model_dump_json())

        try:
            reply, status = await self.context.send_and_receive(
                self.destination,
                request,
                response_type={EconomistResponse, EconomistError},
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            logger = getattr(self.context, "logger", None)
            if logger is not None:
                logger.error(
                    "Remote Economist communication failed: %s",
                    type(exc).__name__,
                )
            raise RemoteEconomistError(
                "The remote Economist agent could not be reached."
            ) from exc

        if reply is None:
            logger = getattr(self.context, "logger", None)
            if logger is not None and status is not None:
                detail = getattr(status, "detail", None)
                if detail and isinstance(detail, str):
                    logger.error("Remote Economist returned no reply")
            raise RemoteEconomistError(
                "The remote Economist agent did not respond."
            )

        if isinstance(reply, EconomistError):
            raise RemoteEconomistError(
                "The Economist agent could not process the shipment request."
            )

        if not isinstance(reply, EconomistResponse):
            raise RemoteEconomistError(
                "The remote Economist agent returned an unexpected response."
            )

        try:
            return EconData.model_validate_json(reply.econ_data_json)
        except ValidationError as exc:
            raise RemoteEconomistError(
                "The remote Economist agent returned invalid data."
            ) from exc


class RemoteRoutingError(RuntimeError):
    """Raised when the remote Router cannot be reached or returns invalid data."""


class UAgentsRoutingClient:
    """Call Riya's Router uAgent via Context.send_and_receive."""

    def __init__(
        self,
        context: Any,
        destination: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.context = context
        self.destination = destination
        self.timeout_seconds = timeout_seconds

    async def route(
        self,
        shipment: ShipmentRequest,
        econ_data: EconData,
    ) -> RouteData:
        """Send shipment and econ data and return parsed RouteData from the remote agent."""
        request = RouteRequestMessage(
            shipment=shipment.model_dump(),
            econ=econ_data.model_dump(),
        )

        try:
            reply, status = await self.context.send_and_receive(
                self.destination,
                request,
                response_type=RouteResponseMessage,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            logger = getattr(self.context, "logger", None)
            if logger is not None:
                logger.error(
                    "Remote Router communication failed: %s",
                    type(exc).__name__,
                )
            raise RemoteRoutingError(
                "The remote Router agent could not be reached."
            ) from exc

        if reply is None:
            logger = getattr(self.context, "logger", None)
            if logger is not None and status is not None:
                detail = getattr(status, "detail", None)
                if detail and isinstance(detail, str):
                    logger.error("Remote Router returned no reply")
            raise RemoteRoutingError(
                "The remote Router agent did not respond."
            )

        if not isinstance(reply, RouteResponseMessage):
            raise RemoteRoutingError(
                "The remote Router agent returned an unexpected response."
            )

        if not reply.ok:
            raise RemoteRoutingError(
                "The Router agent could not process the shipment request."
            )

        try:
            return RouteData.model_validate(reply.route_data)
        except ValidationError as exc:
            raise RemoteRoutingError(
                "The remote Router agent returned invalid data."
            ) from exc


class RemoteTreasuryError(RuntimeError):
    """Raised when the remote Treasury agent cannot be reached."""


class UAgentsTreasuryPaymentClient:
    """Remote Treasury payment setup and finalization via send_and_receive."""

    def __init__(
        self,
        context: Any,
        destination: str,
        timeout_seconds: int = 60,
    ) -> None:
        self.context = context
        self.destination = destination
        self.timeout_seconds = timeout_seconds

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
        request = PaymentSetupRequestMessage(
            user_address=user_address,
            session_id=session_id,
            shipment=shipment.model_dump(),
            econ_data=econ_data.model_dump(),
            route_data=route_data.model_dump(),
            doc_templates=doc_templates.model_dump(),
        )

        logger = getattr(self.context, "logger", None)
        payment_trace(
            logger,
            "orchestrator.treasury_setup.send",
            session_id=session_id,
            treasury_destination=self.destination,
        )

        try:
            reply, status = await self.context.send_and_receive(
                self.destination,
                request,
                response_type=PaymentSetupResponseMessage,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            payment_trace(
                logger,
                "orchestrator.treasury_setup.failure",
                session_id=session_id,
                treasury_destination=self.destination,
                exception_class=type(exc).__name__,
            )
            if logger is not None:
                logger.error(
                    "Remote Treasury setup communication failed: %s",
                    type(exc).__name__,
                )
            raise RemoteTreasuryError(
                "The remote Treasury agent could not be reached."
            ) from exc

        status_summary = {
            "status_class": type(status).__name__ if status is not None else None,
        }
        if status is not None:
            for attr in ("status", "detail", "value"):
                if hasattr(status, attr):
                    value = getattr(status, attr)
                    if value is not None:
                        status_summary[attr] = str(value)

        checkout_summary = summarize_checkout(
            reply.checkout if reply is not None else None
        )
        payment_trace(
            logger,
            "orchestrator.treasury_setup.receive",
            session_id=session_id,
            treasury_destination=self.destination,
            reply_class=type(reply).__name__ if reply is not None else None,
            reply_ok=getattr(reply, "ok", None),
            checkout_key_names=checkout_summary["checkout_key_names"],
            ui_mode=checkout_summary["ui_mode"],
            has_client_secret=checkout_summary["has_client_secret"],
            has_id=checkout_summary["has_id"],
            has_checkout_session_id=checkout_summary["has_checkout_session_id"],
            id_aliases_match=checkout_summary["id_aliases_match"],
            has_all_required_keys=checkout_summary["has_all_required_keys"],
            **status_summary,
        )

        if reply is None or not isinstance(reply, PaymentSetupResponseMessage):
            raise RemoteTreasuryError(
                "The remote Treasury agent did not respond to payment setup."
            )

        if not reply.ok or not reply.checkout or reply.fee_usd is None:
            raise RemoteTreasuryError(
                reply.error or "The remote Treasury agent could not set up payment."
            )

        normalized_checkout, normalization_changes = normalize_fetch_checkout_metadata(
            reply.checkout
        )
        if normalization_changes.get("changed"):
            payment_trace(
                logger,
                "orchestrator.treasury_setup.checkout_normalized",
                session_id=session_id,
                ui_mode_from=normalization_changes.get("ui_mode_from"),
            )

        return PaymentSetupResult(
            checkout=normalized_checkout or reply.checkout,
            fee_usd=reply.fee_usd,
        )

    async def finalize_payment(
        self,
        *,
        user_address: str,
        session_id: str,
        checkout_session_id: str,
        transaction_id: str,
    ) -> SettlementStatus:
        request = PaymentFinalizeRequestMessage(
            user_address=user_address,
            session_id=session_id,
            checkout_session_id=checkout_session_id,
            transaction_id=transaction_id,
        )

        try:
            reply, _status = await self.context.send_and_receive(
                self.destination,
                request,
                response_type=PaymentFinalizeResponseMessage,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            logger = getattr(self.context, "logger", None)
            if logger is not None:
                logger.error(
                    "Remote Treasury finalize communication failed: %s",
                    type(exc).__name__,
                )
            raise RemoteTreasuryError(
                "The remote Treasury agent could not be reached."
            ) from exc

        if reply is None or not isinstance(reply, PaymentFinalizeResponseMessage):
            raise RemoteTreasuryError(
                "The remote Treasury agent did not respond to payment finalization."
            )

        if not reply.ok or not reply.settlement_status:
            raise RemoteTreasuryError(
                reply.error or "Payment could not be verified."
            )

        try:
            return SettlementStatus.model_validate(reply.settlement_status)
        except ValidationError as exc:
            raise RemoteTreasuryError(
                "The remote Treasury agent returned invalid settlement data."
            ) from exc
