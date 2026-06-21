"""Tests for conversation controller."""

from __future__ import annotations

import pytest

from shared_models import EconData, RouteData, SettlementStatus

from orchestrator.conversation import ConversationController
from orchestrator.extractor import ExtractionError
from orchestrator.models import PartialItem, PartialShipmentData, WorkflowStage
from orchestrator.service import OrchestratorService
from orchestrator.session_store import InMemorySessionStore


class FakeExtractor:
    def __init__(
        self,
        responses: list[PartialShipmentData] | None = None,
        *,
        raise_error: bool = False,
    ) -> None:
        self.responses = list(responses or [])
        self.raise_error = raise_error
        self.call_count = 0
        self.calls: list[tuple[str, PartialShipmentData]] = []

    def extract(
        self,
        user_message: str,
        current_data: PartialShipmentData,
    ) -> PartialShipmentData:
        self.call_count += 1
        self.calls.append((user_message, current_data))
        if self.raise_error:
            raise ExtractionError("Simulated extraction failure.")
        if self.responses:
            return self.responses.pop(0)
        return PartialShipmentData()


def _complete_partial() -> PartialShipmentData:
    return PartialShipmentData(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[PartialItem(name="Widget", quantity=5, category="electronics")],
        total_weight_kg=120.0,
        total_volume_cbm=2.0,
        timeframe="SPEED",
        declared_value_usd=4000.0,
    )


def _controller(extractor: FakeExtractor | None = None) -> ConversationController:
    service = OrchestratorService(InMemorySessionStore())
    return ConversationController(service, extractor or FakeExtractor())


def test_collection_stage_messages_call_extractor():
    extractor = FakeExtractor([PartialShipmentData(origin={"country": "CN"})])
    controller = _controller(extractor)
    controller.process_message("user-1", "From China")
    assert extractor.call_count == 1


def test_extracted_data_passed_into_service():
    extractor = FakeExtractor([_complete_partial()])
    controller = _controller(extractor)
    session, _ = controller.process_message("user-1", "Complete shipment")
    assert session.stage == WorkflowStage.READY_FOR_ECONOMIST
    assert session.shipment_request is not None


def test_multi_message_collection_preserves_prior_information():
    extractor = FakeExtractor(
        [
            PartialShipmentData(
                origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
                destination={"country": "US", "state": "TX", "city": "Austin"},
            ),
            PartialShipmentData(
                items=[PartialItem(name="Widget", quantity=5, category="electronics")],
                total_weight_kg=120.0,
                total_volume_cbm=2.0,
                timeframe="SPEED",
                declared_value_usd=4000.0,
            ),
        ]
    )
    controller = _controller(extractor)
    controller.process_message("user-1", "From Shenzhen to Austin")
    session, _ = controller.process_message(
        "user-1", "5 widgets, 120kg, 2 cbm, speed, $4000"
    )
    assert session.stage == WorkflowStage.READY_FOR_ECONOMIST
    assert extractor.calls[1][1].origin["city"] == "Shenzhen"


def test_extractor_failure_preserves_partial_data():
    extractor = FakeExtractor(
        [
            PartialShipmentData(
                origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
            )
        ]
    )
    controller = _controller(extractor)
    controller.process_message("user-1", "From Shenzhen")
    extractor.raise_error = True
    session, response = controller.process_message("user-1", "broken message")
    assert "couldn't interpret" in response.lower()
    assert session.partial_data.origin["city"] == "Shenzhen"
    assert session.retry_count == 0


def test_blank_messages_do_not_call_extractor():
    extractor = FakeExtractor()
    controller = _controller(extractor)
    session, response = controller.process_message("user-1", "   ")
    assert extractor.call_count == 0
    assert session.stage == WorkflowStage.COLLECTING_INPUT
    assert "please send" in response.lower()


def test_new_shipment_restarts_workflow():
    extractor = FakeExtractor([_complete_partial()])
    controller = _controller(extractor)
    controller.process_message("user-1", "Complete shipment")
    calls_before = extractor.call_count
    session, response = controller.process_message("user-1", "NEW SHIPMENT")
    assert extractor.call_count == calls_before
    assert session.stage == WorkflowStage.COLLECTING_INPUT
    assert session.shipment_request is None
    assert "new shipment" in response.lower()


def test_new_shipment_accepted_case_insensitively():
    extractor = FakeExtractor()
    controller = _controller(extractor)
    session, _ = controller.process_message("user-1", "  new shipment  ")
    assert session.stage == WorkflowStage.COLLECTING_INPUT


def test_longer_phrase_not_treated_as_new_shipment_command():
    extractor = FakeExtractor([PartialShipmentData(origin={"country": "CN"})])
    controller = _controller(extractor)
    controller.process_message("user-1", "I want to start a new shipment please")
    assert extractor.call_count == 1


def test_new_shipment_rejected_during_executing_payment():
    extractor = FakeExtractor([_complete_partial()])
    service = OrchestratorService(InMemorySessionStore())
    controller = ConversationController(service, extractor)
    controller.process_message("user-1", "Complete shipment")
    _advance_to_executing_payment(service, "user-1")
    session, response = controller.process_message("user-1", "NEW SHIPMENT")
    assert session.stage == WorkflowStage.EXECUTING_PAYMENT
    assert "payment" in response.lower()


def test_confirmation_stage_bypasses_extractor():
    extractor = FakeExtractor([_complete_partial()])
    service = OrchestratorService(InMemorySessionStore())
    controller = ConversationController(service, extractor)
    controller.process_message("user-1", "Complete shipment")
    _advance_to_awaiting_confirmation(service, "user-1")
    calls_before = extractor.call_count
    session, _ = controller.process_message("user-1", "confirm")
    assert extractor.call_count == calls_before
    assert session.stage == WorkflowStage.EXECUTING_PAYMENT


def test_exact_confirm_reaches_confirmation_method():
    extractor = FakeExtractor([_complete_partial()])
    service = OrchestratorService(InMemorySessionStore())
    controller = ConversationController(service, extractor)
    controller.process_message("user-1", "Complete shipment")
    _advance_to_awaiting_confirmation(service, "user-1")
    session, response = controller.process_message("user-1", "CONFIRM")
    assert session.stage == WorkflowStage.EXECUTING_PAYMENT
    assert "confirmation received" in response.lower()


def test_active_teammate_stages_bypass_extractor():
    extractor = FakeExtractor([_complete_partial()])
    service = OrchestratorService(InMemorySessionStore())
    controller = ConversationController(service, extractor)
    controller.process_message("user-1", "Complete shipment")
    service.begin_economic_analysis("user-1")
    calls_before = extractor.call_count
    session, response = controller.process_message("user-1", "status?")
    assert extractor.call_count == calls_before
    assert session.stage == WorkflowStage.CALLING_ECONOMIST
    assert "analyzed" in response.lower()


def test_completed_workflow_bypasses_extractor():
    extractor = FakeExtractor([_complete_partial()])
    service = OrchestratorService(InMemorySessionStore())
    controller = ConversationController(service, extractor)
    controller.process_message("user-1", "Complete shipment")
    _advance_to_completed(service, "user-1")
    calls_before = extractor.call_count
    session, response = controller.process_message("user-1", "hello again")
    assert extractor.call_count == calls_before
    assert session.stage == WorkflowStage.COMPLETED
    assert "NEW SHIPMENT" in response


def test_two_sender_addresses_independent():
    extractor = FakeExtractor(
        [
            PartialShipmentData(origin={"country": "CN", "city": "Shenzhen"}),
            PartialShipmentData(origin={"country": "JP", "city": "Tokyo"}),
        ]
    )
    controller = _controller(extractor)
    controller.process_message("user-a", "From China")
    controller.process_message("user-b", "From Japan")
    session_a = controller._service.get_or_create_session("user-a")
    session_b = controller._service.get_or_create_session("user-b")
    assert session_a.partial_data.origin["city"] == "Shenzhen"
    assert session_b.partial_data.origin["city"] == "Tokyo"


def test_existing_foundation_service_behavior_unchanged():
    service = OrchestratorService(InMemorySessionStore())
    session, _ = service.apply_extracted_data("user-1", _complete_partial())
    assert session.stage == WorkflowStage.READY_FOR_ECONOMIST


def _advance_to_awaiting_confirmation(service: OrchestratorService, sender: str) -> None:
    service.begin_economic_analysis(sender)
    service.record_econ_result(
        sender,
        EconData(
            transport_preference="AIR",
            is_high_value=False,
            is_luxury=False,
            base_entry_tax_usd=100.0,
        ),
    )
    service.record_route_result(
        sender,
        RouteData(
            selected_mode="AIR",
            optimal_route_nodes=["A", "B"],
            countries_visited=["CN", "US"],
            freight_and_toll_cost_usd=500.0,
            total_landed_cost_usd=600.0,
        ),
    )
    service.record_quote_result(
        sender,
        SettlementStatus(filled_documents={}, final_user_prompt="Quote"),
    )


def _advance_to_executing_payment(service: OrchestratorService, sender: str) -> None:
    _advance_to_awaiting_confirmation(service, sender)
    service.handle_confirmation(sender, "CONFIRM")


def _advance_to_completed(service: OrchestratorService, sender: str) -> None:
    _advance_to_executing_payment(service, sender)
    service.record_payment_result(
        sender,
        SettlementStatus(
            filled_documents={},
            final_user_prompt="Paid",
            payment_hash="SIMULATED_ABC",
        ),
    )
