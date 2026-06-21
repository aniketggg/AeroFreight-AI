"""Workflow coordinator connecting conversation handling to teammate agents."""

from __future__ import annotations

import inspect
from typing import Any

from shared_models import DocTemplates, SettlementStatus

from orchestrator.agent_interfaces import (
    EconomistAgentClient,
    PaymentSetupResult,
    RoutingAgentClient,
    TreasuryAgentClient,
    TreasuryPaymentClient,
)
from orchestrator.conversation import ConversationController
from orchestrator.models import OrchestratorSession, WorkflowStage
from orchestrator.service import OrchestratorService


_REMOTE_PAYMENT_PROMPT = (
    "Your shipment analysis is ready. Complete the Stripe payment shown above "
    "to unlock the quote and invoice."
)
_REMOTE_PAYMENT_RESEND_PROMPT = (
    "Your payment is still pending. Complete the Stripe payment shown above "
    "to unlock the quote and invoice."
)


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class WorkflowCoordinator:
    """Run mock teammate workflows after collection and confirmation."""

    def __init__(
        self,
        conversation: ConversationController,
        service: OrchestratorService,
        economist: EconomistAgentClient,
        router: RoutingAgentClient,
        treasury: TreasuryAgentClient,
        treasury_payment_client: TreasuryPaymentClient | None = None,
    ) -> None:
        self.conversation = conversation
        self.service = service
        self._economist = economist
        self._router = router
        self._treasury = treasury
        self._treasury_payment_client = treasury_payment_client

    def handle_user_message(
        self,
        sender_address: str,
        user_message: str,
    ) -> tuple[OrchestratorSession, str]:
        session, response = self.conversation.process_message(
            sender_address,
            user_message,
        )

        if session.stage == WorkflowStage.READY_FOR_ECONOMIST:
            return self._run_quote_pipeline(sender_address)

        if session.stage == WorkflowStage.EXECUTING_PAYMENT:
            return self._run_payment_pipeline(sender_address)

        return session, response

    async def handle_user_message_async(
        self,
        sender_address: str,
        user_message: str,
    ) -> tuple[OrchestratorSession, str, PaymentSetupResult | None]:
        """Async variant supporting remote agents with awaitable analyze/route methods."""
        session, response = self.conversation.process_message(
            sender_address,
            user_message,
        )

        if session.stage == WorkflowStage.READY_FOR_ECONOMIST:
            return await self._run_quote_pipeline_async(sender_address)

        if session.stage == WorkflowStage.AWAITING_PAYMENT:
            return session, _REMOTE_PAYMENT_RESEND_PROMPT, None

        if session.stage == WorkflowStage.EXECUTING_PAYMENT:
            return await self._run_payment_pipeline_async(sender_address)

        return session, response, None

    def _run_quote_pipeline(
        self,
        sender_address: str,
    ) -> tuple[OrchestratorSession, str]:
        session = self.service.get_or_create_session(sender_address)
        if session.shipment_request is None:
            return session, "Shipment details are missing. Please try again."

        shipment = session.shipment_request

        try:
            self.service.begin_economic_analysis(sender_address)
            econ_result = self._economist.analyze(shipment)
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Economic analysis failed.",
            )
            return session, (
                "Something went wrong during economic analysis. "
                "Please try again or start a NEW SHIPMENT."
            )

        try:
            self.service.record_econ_result(sender_address, econ_result)
            route_result = self._router.route(shipment, econ_result)
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Routing analysis failed.",
            )
            return session, (
                "Something went wrong while calculating routing options. "
                "Please try again or start a NEW SHIPMENT."
            )

        try:
            self.service.record_route_result(sender_address, route_result)
            quote_result = self._treasury.prepare_quote(
                shipment,
                econ_result,
                route_result,
            )
            session = self.service.record_quote_result(sender_address, quote_result)
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Quote preparation failed.",
            )
            return session, (
                "Something went wrong while preparing your quote. "
                "Please try again or start a NEW SHIPMENT."
            )

        return session, quote_result.final_user_prompt

    async def _run_quote_pipeline_async(
        self,
        sender_address: str,
    ) -> tuple[OrchestratorSession, str, PaymentSetupResult | None]:
        if self._treasury_payment_client is not None:
            return await self._run_remote_quote_and_payment_async(sender_address)

        session = self.service.get_or_create_session(sender_address)
        if session.shipment_request is None:
            return session, "Shipment details are missing. Please try again.", None

        shipment = session.shipment_request

        try:
            self.service.begin_economic_analysis(sender_address)
            econ_result = await _await_if_needed(self._economist.analyze(shipment))
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Economic analysis failed.",
            )
            return session, (
                "Something went wrong during economic analysis. "
                "Please try again or start a NEW SHIPMENT."
            ), None

        try:
            self.service.record_econ_result(sender_address, econ_result)
            route_result = await _await_if_needed(
                self._router.route(shipment, econ_result)
            )
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Routing analysis failed.",
            )
            return session, (
                "Something went wrong while calculating routing options. "
                "Please try again or start a NEW SHIPMENT."
            ), None

        try:
            self.service.record_route_result(sender_address, route_result)
            quote_result = await _await_if_needed(
                self._treasury.prepare_quote(
                    shipment,
                    econ_result,
                    route_result,
                )
            )
            session = self.service.record_quote_result(sender_address, quote_result)
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Quote preparation failed.",
            )
            return session, (
                "Something went wrong while preparing your quote. "
                "Please try again or start a NEW SHIPMENT."
            ), None

        return session, quote_result.final_user_prompt, None

    async def _run_remote_quote_and_payment_async(
        self,
        sender_address: str,
    ) -> tuple[OrchestratorSession, str, PaymentSetupResult | None]:
        session = self.service.get_or_create_session(sender_address)
        if session.shipment_request is None:
            return session, "Shipment details are missing. Please try again.", None

        shipment = session.shipment_request

        try:
            self.service.begin_economic_analysis(sender_address)
            econ_result = await _await_if_needed(self._economist.analyze(shipment))
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Economic analysis failed.",
            )
            return session, (
                "Something went wrong during economic analysis. "
                "Please try again or start a NEW SHIPMENT."
            ), None

        try:
            self.service.record_econ_result(sender_address, econ_result)
            route_result = await _await_if_needed(
                self._router.route(shipment, econ_result)
            )
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Routing analysis failed.",
            )
            return session, (
                "Something went wrong while calculating routing options. "
                "Please try again or start a NEW SHIPMENT."
            ), None

        try:
            self.service.record_route_result(sender_address, route_result)
            quote_result = await _await_if_needed(
                self._treasury.prepare_quote(
                    shipment,
                    econ_result,
                    route_result,
                )
            )
            session = self.service.begin_awaiting_payment(sender_address, quote_result)
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Quote preparation failed.",
            )
            return session, (
                "Something went wrong while preparing your quote. "
                "Please try again or start a NEW SHIPMENT."
            ), None

        filled_documents = session.settlement_status.filled_documents
        doc_templates = DocTemplates(
            required_form_names=list(filled_documents.keys()),
            blank_form_structures=filled_documents,
        )

        try:
            setup = await self._treasury_payment_client.prepare_payment(
                user_address=sender_address,
                session_id=session.session_id,
                shipment=session.shipment_request,
                econ_data=session.econ_data,
                route_data=session.route_data,
                doc_templates=doc_templates,
            )
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Remote payment setup failed.",
            )
            return session, (
                "Something went wrong while setting up payment. "
                "Please try again or start a NEW SHIPMENT."
            ), None

        return session, _REMOTE_PAYMENT_PROMPT, setup

    def _run_payment_pipeline(
        self,
        sender_address: str,
    ) -> tuple[OrchestratorSession, str]:
        session = self.service.get_or_create_session(sender_address)
        if session.shipment_request is None or session.route_data is None:
            return session, "Payment details are missing. Please try again."

        shipment = session.shipment_request
        route_data = session.route_data

        try:
            payment_result = self._treasury.execute_payment(shipment, route_data)
            session = self.service.record_payment_result(sender_address, payment_result)
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Simulated payment execution failed.",
            )
            return session, (
                "Something went wrong while executing the simulated payment. "
                "Please try again or contact support."
            )

        payment_hash = payment_result.payment_hash or "unknown"
        response = (
            "Simulated payment completed successfully. "
            f"No real payment occurred. Reference: {payment_hash}. "
            "Type NEW SHIPMENT to begin another workflow."
        )
        return session, response

    async def _run_payment_pipeline_async(
        self,
        sender_address: str,
    ) -> tuple[OrchestratorSession, str, PaymentSetupResult | None]:
        session = self.service.get_or_create_session(sender_address)
        if session.shipment_request is None or session.route_data is None:
            return session, "Payment details are missing. Please try again.", None

        shipment = session.shipment_request
        route_data = session.route_data

        try:
            payment_result = await _await_if_needed(
                self._treasury.execute_payment(shipment, route_data)
            )
            session = self.service.record_payment_result(sender_address, payment_result)
        except Exception:
            session = self.service.mark_failed(
                sender_address,
                "Simulated payment execution failed.",
            )
            return session, (
                "Something went wrong while executing the simulated payment. "
                "Please try again or contact support."
            ), None

        payment_hash = payment_result.payment_hash or "unknown"
        response = (
            "Simulated payment completed successfully. "
            f"No real payment occurred. Reference: {payment_hash}. "
            "Type NEW SHIPMENT to begin another workflow."
        )
        return session, response, None
