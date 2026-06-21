"""Workflow coordinator connecting conversation handling to teammate agents."""

from __future__ import annotations

from orchestrator.agent_interfaces import (
    EconomistAgentClient,
    RoutingAgentClient,
    TreasuryAgentClient,
)
from orchestrator.conversation import ConversationController
from orchestrator.models import OrchestratorSession, WorkflowStage
from orchestrator.service import OrchestratorService


class WorkflowCoordinator:
    """Run mock teammate workflows after collection and confirmation."""

    def __init__(
        self,
        conversation: ConversationController,
        service: OrchestratorService,
        economist: EconomistAgentClient,
        router: RoutingAgentClient,
        treasury: TreasuryAgentClient,
    ) -> None:
        self.conversation = conversation
        self.service = service
        self._economist = economist
        self._router = router
        self._treasury = treasury

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
