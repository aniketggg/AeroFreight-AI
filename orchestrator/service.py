"""Orchestrator workflow service."""

from __future__ import annotations

from shared_models import EconData, RouteData, SettlementStatus

from orchestrator.models import OrchestratorSession, PartialShipmentData, WorkflowStage
from orchestrator.session_store import SessionStore
from orchestrator.validation import (
    build_shipment_request,
    get_missing_fields,
    make_follow_up_question,
    merge_partial_data,
    validate_business_rules,
)


class OrchestratorService:
    """Core orchestrator state machine for shipment collection and agent coordination."""

    def __init__(self, session_store: SessionStore) -> None:
        self._session_store = session_store

    def get_or_create_session(self, sender_address: str) -> OrchestratorSession:
        session = self._session_store.get(sender_address)
        if session is not None:
            return session
        session = OrchestratorSession(sender_address=sender_address)
        self._session_store.save(session)
        return session

    def apply_extracted_data(
        self,
        sender_address: str,
        incoming: PartialShipmentData,
    ) -> tuple[OrchestratorSession, str]:
        """Merge extracted partial data and advance collection when complete."""
        session = self.get_or_create_session(sender_address)

        if session.stage not in {WorkflowStage.COLLECTING_INPUT, WorkflowStage.FAILED}:
            raise ValueError(
                f"Cannot apply extracted data while workflow stage is {session.stage}."
            )

        merged = merge_partial_data(session.partial_data, incoming)
        session.partial_data = merged
        missing = get_missing_fields(merged)
        validation_errors = validate_business_rules(merged)

        if missing or validation_errors:
            session.stage = WorkflowStage.COLLECTING_INPUT
            session.touch()
            self._session_store.save(session)
            return session, make_follow_up_question(missing, validation_errors)

        try:
            session.shipment_request = build_shipment_request(merged)
        except ValueError as exc:
            session.stage = WorkflowStage.COLLECTING_INPUT
            session.last_error = str(exc)
            session.touch()
            self._session_store.save(session)
            return session, make_follow_up_question([], [str(exc)])

        session.last_error = None
        session.stage = WorkflowStage.READY_FOR_ECONOMIST
        session.touch()
        self._session_store.save(session)

        request = session.shipment_request
        assert request is not None
        origin_city = request.origin.get("city", "unknown origin")
        dest_city = request.destination.get("city", "unknown destination")
        summary = (
            f"Shipment from {origin_city} to {dest_city} is ready "
            f"({request.total_weight_kg} kg, {len(request.items)} item(s), "
            f"{request.timeframe} priority)."
        )
        return session, summary

    def begin_economic_analysis(self, sender_address: str) -> OrchestratorSession:
        session = self._require_session(sender_address)
        self._ensure_transition(session.stage, WorkflowStage.READY_FOR_ECONOMIST)
        session.stage = WorkflowStage.CALLING_ECONOMIST
        session.touch()
        self._session_store.save(session)
        return session.model_copy(deep=True)

    def record_econ_result(
        self,
        sender_address: str,
        result: EconData,
    ) -> OrchestratorSession:
        session = self._require_session(sender_address)
        self._ensure_transition(session.stage, WorkflowStage.CALLING_ECONOMIST)
        session.econ_data = result
        session.stage = WorkflowStage.CALLING_ROUTER
        session.touch()
        self._session_store.save(session)
        return session.model_copy(deep=True)

    def record_route_result(
        self,
        sender_address: str,
        result: RouteData,
    ) -> OrchestratorSession:
        session = self._require_session(sender_address)
        self._ensure_transition(session.stage, WorkflowStage.CALLING_ROUTER)
        session.route_data = result
        session.stage = WorkflowStage.CALLING_TREASURY
        session.touch()
        self._session_store.save(session)
        return session.model_copy(deep=True)

    def record_quote_result(
        self,
        sender_address: str,
        result: SettlementStatus,
    ) -> OrchestratorSession:
        session = self._require_session(sender_address)
        self._ensure_transition(session.stage, WorkflowStage.CALLING_TREASURY)
        if result.payment_hash:
            raise ValueError("Quote result must not include a payment hash.")
        session.settlement_status = result
        session.stage = WorkflowStage.AWAITING_CONFIRMATION
        session.touch()
        self._session_store.save(session)
        return session.model_copy(deep=True)

    def handle_confirmation(
        self,
        sender_address: str,
        user_message: str,
    ) -> tuple[OrchestratorSession, str]:
        """Accept exact CONFIRM and transition to payment execution."""
        session = self._require_session(sender_address)
        if session.stage != WorkflowStage.AWAITING_CONFIRMATION:
            raise ValueError(
                f"Cannot handle confirmation while workflow stage is {session.stage}."
            )

        normalized = user_message.strip().upper()
        if normalized == "CONFIRM":
            session.stage = WorkflowStage.EXECUTING_PAYMENT
            session.touch()
            self._session_store.save(session)
            return session, "Confirmation received. Executing payment."

        return session, "Please type exactly CONFIRM to execute payment."

    def record_payment_result(
        self,
        sender_address: str,
        result: SettlementStatus,
    ) -> OrchestratorSession:
        session = self._require_session(sender_address)
        self._ensure_transition(session.stage, WorkflowStage.EXECUTING_PAYMENT)
        if not result.payment_hash or not str(result.payment_hash).strip():
            raise ValueError("Payment result must include a payment hash.")
        session.settlement_status = result
        session.stage = WorkflowStage.COMPLETED
        session.touch()
        self._session_store.save(session)
        return session.model_copy(deep=True)

    def mark_failed(
        self,
        sender_address: str,
        error_message: str,
    ) -> OrchestratorSession:
        session = self._require_session(sender_address)
        session.stage = WorkflowStage.FAILED
        session.last_error = error_message
        session.retry_count += 1
        session.touch()
        self._session_store.save(session)
        return session.model_copy(deep=True)

    def restart_session(self, sender_address: str) -> OrchestratorSession:
        self._session_store.delete(sender_address)
        session = OrchestratorSession(sender_address=sender_address)
        self._session_store.save(session)
        return session.model_copy(deep=True)

    def _require_session(self, sender_address: str) -> OrchestratorSession:
        session = self._session_store.get(sender_address)
        if session is None:
            raise ValueError(f"No session found for sender {sender_address}.")
        return session

    @staticmethod
    def _ensure_transition(current: WorkflowStage, expected: WorkflowStage) -> None:
        if current != expected:
            raise ValueError(
                f"Invalid workflow transition from {current} (expected {expected})."
            )
