"""Tests for orchestrator service workflow."""

import pytest

from shared_models import EconData, RouteData, SettlementStatus

from orchestrator.models import PartialItem, PartialShipmentData, WorkflowStage
from orchestrator.service import OrchestratorService
from orchestrator.session_store import InMemorySessionStore


def _service() -> OrchestratorService:
    return OrchestratorService(InMemorySessionStore())


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


def test_multi_message_partial_data_collection():
    service = _service()
    _, first = service.apply_extracted_data(
        "user-1",
        PartialShipmentData(
            origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
            destination={"country": "US", "state": "TX", "city": "Austin"},
        ),
    )
    assert "continue" in first.lower() or "provide" in first.lower()

    session, second = service.apply_extracted_data(
        "user-1",
        PartialShipmentData(
            items=[PartialItem(name="Widget", quantity=5, category="electronics")],
            total_weight_kg=120.0,
            total_volume_cbm=2.0,
            timeframe="SPEED",
            declared_value_usd=4000.0,
        ),
    )
    assert session.stage == WorkflowStage.READY_FOR_ECONOMIST
    assert session.shipment_request is not None


def test_complete_state_flow():
    service = _service()
    session, _ = service.apply_extracted_data("user-1", _complete_partial())
    assert session.stage == WorkflowStage.READY_FOR_ECONOMIST

    session = service.begin_economic_analysis("user-1")
    assert session.stage == WorkflowStage.CALLING_ECONOMIST

    econ = EconData(
        transport_preference="AIR",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=200.0,
    )
    session = service.record_econ_result("user-1", econ)
    assert session.stage == WorkflowStage.CALLING_ROUTER

    route = RouteData(
        selected_mode="AIR",
        optimal_route_nodes=["Shenzhen", "Austin"],
        countries_visited=["CN", "US"],
        freight_and_toll_cost_usd=1000.0,
        total_landed_cost_usd=1200.0,
    )
    session = service.record_route_result("user-1", route)
    assert session.stage == WorkflowStage.CALLING_TREASURY

    quote = SettlementStatus(
        filled_documents={},
        final_user_prompt="Total: $1200. Type CONFIRM to execute payment.",
    )
    session = service.record_quote_result("user-1", quote)
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION
    assert session.settlement_status is not None
    assert session.settlement_status.payment_hash is None

    session, confirm_msg = service.handle_confirmation("user-1", "confirm")
    assert session.stage == WorkflowStage.EXECUTING_PAYMENT
    assert "confirmation received" in confirm_msg.lower()

    payment = SettlementStatus(
        filled_documents={},
        final_user_prompt="Paid",
        payment_hash="SIMULATED_ABC123",
    )
    session = service.record_payment_result("user-1", payment)
    assert session.stage == WorkflowStage.COMPLETED
    assert session.settlement_status is not None
    assert session.settlement_status.payment_hash == "SIMULATED_ABC123"


def test_invalid_state_transition():
    service = _service()
    with pytest.raises(ValueError):
        service.begin_economic_analysis("user-1")


def test_exact_confirm():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    _advance_to_awaiting_confirmation(service, "user-1")
    session, _ = service.handle_confirmation("user-1", "CONFIRM")
    assert session.stage == WorkflowStage.EXECUTING_PAYMENT


def test_lowercase_confirm():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    _advance_to_awaiting_confirmation(service, "user-1")
    session, _ = service.handle_confirmation("user-1", "confirm")
    assert session.stage == WorkflowStage.EXECUTING_PAYMENT


def test_reject_yes():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    _advance_to_awaiting_confirmation(service, "user-1")
    session, response = service.handle_confirmation("user-1", "yes")
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION
    assert "CONFIRM" in response


def test_reject_confirm_now():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    _advance_to_awaiting_confirmation(service, "user-1")
    session, _ = service.handle_confirmation("user-1", "confirm now")
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION


def test_reject_confirm_payment():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    _advance_to_awaiting_confirmation(service, "user-1")
    session, _ = service.handle_confirmation("user-1", "confirm payment")
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION


def test_payment_result_missing_hash():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    _advance_to_awaiting_confirmation(service, "user-1")
    service.handle_confirmation("user-1", "CONFIRM")
    with pytest.raises(ValueError):
        service.record_payment_result(
            "user-1",
            SettlementStatus(filled_documents={}, final_user_prompt="Paid"),
        )


def test_begin_awaiting_payment_skips_confirmation_stage():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    service.begin_economic_analysis("user-1")
    service.record_econ_result(
        "user-1",
        EconData(
            transport_preference="AIR",
            is_high_value=False,
            is_luxury=False,
            base_entry_tax_usd=100.0,
        ),
    )
    service.record_route_result(
        "user-1",
        RouteData(
            selected_mode="AIR",
            optimal_route_nodes=["A", "B"],
            countries_visited=["CN", "US"],
            freight_and_toll_cost_usd=500.0,
            total_landed_cost_usd=600.0,
        ),
    )
    session = service.begin_awaiting_payment(
        "user-1",
        SettlementStatus(filled_documents={}, final_user_prompt="hidden"),
    )
    assert session.stage == WorkflowStage.AWAITING_PAYMENT
    assert session.settlement_status is not None


def test_mark_payment_pending_from_executing_payment():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    _advance_to_awaiting_confirmation(service, "user-1")
    service.handle_confirmation("user-1", "CONFIRM")
    session = service.mark_payment_pending("user-1")
    assert session.stage == WorkflowStage.AWAITING_PAYMENT


def test_payment_result_completes_from_awaiting_payment():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    _advance_to_awaiting_confirmation(service, "user-1")
    service.handle_confirmation("user-1", "CONFIRM")
    service.mark_payment_pending("user-1")
    session = service.record_payment_result(
        "user-1",
        SettlementStatus(
            filled_documents={},
            final_user_prompt="Paid",
            payment_hash="cs_test_123",
        ),
    )
    assert session.stage == WorkflowStage.COMPLETED
    assert session.settlement_status.payment_hash == "cs_test_123"


def test_payment_result_rejects_invalid_stage():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    _advance_to_awaiting_confirmation(service, "user-1")
    with pytest.raises(ValueError):
        service.record_payment_result(
            "user-1",
            SettlementStatus(
                filled_documents={},
                final_user_prompt="Paid",
                payment_hash="cs_test_123",
            ),
        )


def test_quote_result_rejects_payment_hash():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    service.begin_economic_analysis("user-1")
    service.record_econ_result(
        "user-1",
        EconData(
            transport_preference="AIR",
            is_high_value=False,
            is_luxury=False,
            base_entry_tax_usd=50.0,
        ),
    )
    service.record_route_result(
        "user-1",
        RouteData(
            selected_mode="AIR",
            optimal_route_nodes=["A"],
            countries_visited=["CN", "US"],
            freight_and_toll_cost_usd=100.0,
            total_landed_cost_usd=150.0,
        ),
    )
    with pytest.raises(ValueError):
        service.record_quote_result(
            "user-1",
            SettlementStatus(
                filled_documents={},
                final_user_prompt="Quote",
                payment_hash="too-early",
            ),
        )


def test_failure_preserves_previous_successful_data():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    service.begin_economic_analysis("user-1")
    session = service.mark_failed("user-1", "Network error")
    assert session.stage == WorkflowStage.FAILED
    assert session.retry_count == 1
    assert session.shipment_request is not None
    assert session.partial_data.origin["city"] == "Shenzhen"


def test_restart_creates_clean_session():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    service.begin_economic_analysis("user-1")
    session = service.restart_session("user-1")
    assert session.stage == WorkflowStage.COLLECTING_INPUT
    assert session.shipment_request is None
    assert session.econ_data is None


def test_two_senders_independent_workflows():
    service = _service()
    service.apply_extracted_data("user-a", _complete_partial())
    service.apply_extracted_data("user-b", _complete_partial())
    session_a = service.get_or_create_session("user-a")
    session_b = service.get_or_create_session("user-b")
    assert session_a.session_id != session_b.session_id
    service.begin_economic_analysis("user-a")
    session_b_after = service.get_or_create_session("user-b")
    assert session_b_after.stage == WorkflowStage.READY_FOR_ECONOMIST


def test_apply_extracted_data_rejected_outside_collection():
    service = _service()
    service.apply_extracted_data("user-1", _complete_partial())
    with pytest.raises(ValueError):
        service.apply_extracted_data("user-1", PartialShipmentData(total_weight_kg=1.0))


def _advance_to_awaiting_confirmation(
    service: OrchestratorService, sender_address: str
) -> None:
    service.begin_economic_analysis(sender_address)
    service.record_econ_result(
        sender_address,
        EconData(
            transport_preference="AIR",
            is_high_value=False,
            is_luxury=False,
            base_entry_tax_usd=100.0,
        ),
    )
    service.record_route_result(
        sender_address,
        RouteData(
            selected_mode="AIR",
            optimal_route_nodes=["A", "B"],
            countries_visited=["CN", "US"],
            freight_and_toll_cost_usd=500.0,
            total_landed_cost_usd=600.0,
        ),
    )
    service.record_quote_result(
        sender_address,
        SettlementStatus(filled_documents={}, final_user_prompt="Quote"),
    )
