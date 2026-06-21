"""Conversation controller connecting user messages to the orchestrator service."""

from __future__ import annotations

from orchestrator.extractor import ExtractionError, ShipmentExtractor
from orchestrator.models import OrchestratorSession, WorkflowStage
from orchestrator.service import OrchestratorService

NEW_SHIPMENT_COMMAND = "NEW SHIPMENT"

_STAGE_STATUS_MESSAGES: dict[WorkflowStage, str] = {
    WorkflowStage.READY_FOR_ECONOMIST: (
        "Your shipment details are complete and ready for economic analysis."
    ),
    WorkflowStage.CALLING_ECONOMIST: (
        "Cargo constraints are being analyzed. Please wait."
    ),
    WorkflowStage.CALLING_ROUTER: (
        "Routing options are being calculated. Please wait."
    ),
    WorkflowStage.CALLING_TREASURY: (
        "The final quote is being prepared. Please wait."
    ),
    WorkflowStage.EXECUTING_PAYMENT: (
        "Payment execution is in progress. Please wait."
    ),
}


class ConversationController:
    """Route user messages to extraction, collection, or workflow handling."""

    def __init__(
        self,
        service: OrchestratorService,
        extractor: ShipmentExtractor,
    ) -> None:
        self._service = service
        self._extractor = extractor

    def process_message(
        self,
        sender_address: str,
        user_message: str,
    ) -> tuple[OrchestratorSession, str]:
        """Process a user message and return the updated session and reply."""
        if not user_message.strip():
            session = self._service.get_or_create_session(sender_address)
            return session, "Please send a message with your shipment details."

        normalized_command = user_message.strip().upper()
        if normalized_command == NEW_SHIPMENT_COMMAND:
            return self._handle_new_shipment(sender_address)

        session = self._service.get_or_create_session(sender_address)

        if session.stage == WorkflowStage.COMPLETED:
            return session, (
                "This shipment workflow is complete. "
                "Type NEW SHIPMENT to begin another shipment."
            )

        if session.stage == WorkflowStage.AWAITING_CONFIRMATION:
            return self._service.handle_confirmation(sender_address, user_message)

        if session.stage in _STAGE_STATUS_MESSAGES:
            return session, _STAGE_STATUS_MESSAGES[session.stage]

        if session.stage in {WorkflowStage.COLLECTING_INPUT, WorkflowStage.FAILED}:
            return self._handle_collection(sender_address, user_message)

        session = self._service.get_or_create_session(sender_address)
        return session, "Unable to process your message in the current workflow state."

    def _handle_new_shipment(
        self,
        sender_address: str,
    ) -> tuple[OrchestratorSession, str]:
        session = self._service.get_or_create_session(sender_address)
        if session.stage == WorkflowStage.EXECUTING_PAYMENT:
            return session, (
                "Payment is currently being executed. "
                "Please wait until it finishes before starting a new shipment."
            )

        session = self._service.restart_session(sender_address)
        return session, (
            "Starting a new shipment. Please share the origin, destination, "
            "items, weight, volume, timeframe (SPEED or COST), and declared value."
        )

    def _handle_collection(
        self,
        sender_address: str,
        user_message: str,
    ) -> tuple[OrchestratorSession, str]:
        session = self._service.get_or_create_session(sender_address)
        try:
            extracted = self._extractor.extract(
                user_message=user_message,
                current_data=session.partial_data,
                conversation_history=session.collection_history,
            )
        except ExtractionError:
            current = self._service.get_or_create_session(sender_address)
            return current, (
                "I couldn't interpret that message. "
                "Please try again with your shipment details."
            )

        session, response = self._service.apply_extracted_data(
            sender_address,
            extracted,
        )
        session = self._service.append_collection_turns(
            sender_address,
            user_message=user_message,
            assistant_message=response,
        )
        return session, response
