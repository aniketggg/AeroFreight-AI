"""Tests for workflow coordinator end-to-end behavior."""

from __future__ import annotations

import asyncio

from shared_models import EconData, RouteData, SettlementStatus

from orchestrator.conversation import ConversationController
from orchestrator.coordinator import WorkflowCoordinator
from orchestrator.mock_agents import (
    MockEconomistAgent,
    MockRoutingAgent,
    MockTreasuryAgent,
)
from orchestrator.models import PartialItem, PartialShipmentData, WorkflowStage
from orchestrator.service import OrchestratorService
from orchestrator.session_store import InMemorySessionStore


class FakeExtractor:
    def __init__(
        self,
        responses: list[PartialShipmentData] | None = None,
    ) -> None:
        self.responses = list(responses or [])

    def extract(
        self,
        user_message: str,
        current_data: PartialShipmentData,
    ) -> PartialShipmentData:
        if self.responses:
            return self.responses.pop(0)
        return PartialShipmentData()


class FakeEconomist:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def analyze(self, shipment):
        self.calls += 1
        if self.fail:
            raise RuntimeError("economist unavailable")
        return MockEconomistAgent().analyze(shipment)


class AsyncFakeEconomist:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def analyze(self, shipment):
        self.calls += 1
        if self.fail:
            raise RuntimeError("economist unavailable")
        return MockEconomistAgent().analyze(shipment)


class FakeRouter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def route(self, shipment, econ_data):
        self.calls += 1
        if self.fail:
            raise RuntimeError("router unavailable")
        return MockRoutingAgent().route(shipment, econ_data)


class FakeTreasury:
    def __init__(
        self,
        *,
        quote_fail: bool = False,
        payment_fail: bool = False,
    ) -> None:
        self.quote_fail = quote_fail
        self.payment_fail = payment_fail
        self.quote_calls = 0
        self.payment_calls = 0
        self._delegate = MockTreasuryAgent()

    def prepare_quote(self, shipment, econ_data, route_data):
        self.quote_calls += 1
        if self.quote_fail:
            raise RuntimeError("treasury quote unavailable")
        return self._delegate.prepare_quote(shipment, econ_data, route_data)

    def execute_payment(self, shipment, route_data):
        self.payment_calls += 1
        if self.payment_fail:
            raise RuntimeError("treasury payment unavailable")
        return self._delegate.execute_payment(shipment, route_data)


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


def _build_coordinator(
    *,
    economist: FakeEconomist | None = None,
    router: FakeRouter | None = None,
    treasury: FakeTreasury | None = None,
    extractor_responses: list[PartialShipmentData] | None = None,
) -> WorkflowCoordinator:
    store = InMemorySessionStore()
    service = OrchestratorService(store)
    conversation = ConversationController(
        service,
        FakeExtractor(extractor_responses or [_complete_partial()]),
    )
    return WorkflowCoordinator(
        conversation=conversation,
        service=service,
        economist=economist or FakeEconomist(),
        router=router or FakeRouter(),
        treasury=treasury or FakeTreasury(),
    )


def test_complete_shipment_reaches_awaiting_confirmation_with_quote():
    coordinator = _build_coordinator()
    session, response = coordinator.handle_user_message("user-1", "ship widgets")
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION
    assert "## AeroFreight AI Shipment Quote" in response
    assert session.settlement_status is not None
    assert session.settlement_status.payment_hash is None


def test_full_workflow_to_completed():
    coordinator = _build_coordinator()
    coordinator.handle_user_message("user-1", "ship widgets")
    session, response = coordinator.handle_user_message("user-1", "CONFIRM")
    assert session.stage == WorkflowStage.COMPLETED
    assert session.settlement_status is not None
    assert session.settlement_status.payment_hash.startswith("SIMULATED_")
    assert "simulated payment completed" in response.lower()


def test_yes_does_not_execute_payment():
    coordinator = _build_coordinator()
    coordinator.handle_user_message("user-1", "ship widgets")
    session, _ = coordinator.handle_user_message("user-1", "yes")
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION
    assert session.settlement_status.payment_hash is None


def test_confirm_now_does_not_execute_payment():
    coordinator = _build_coordinator()
    coordinator.handle_user_message("user-1", "ship widgets")
    session, _ = coordinator.handle_user_message("user-1", "confirm now")
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION


def test_lowercase_confirm_executes_payment():
    coordinator = _build_coordinator()
    coordinator.handle_user_message("user-1", "ship widgets")
    session, _ = coordinator.handle_user_message("user-1", "confirm")
    assert session.stage == WorkflowStage.COMPLETED


def test_new_shipment_resets_workflow():
    coordinator = _build_coordinator()
    coordinator.handle_user_message("user-1", "ship widgets")
    session, _ = coordinator.handle_user_message("user-1", "NEW SHIPMENT")
    assert session.stage == WorkflowStage.COLLECTING_INPUT
    assert session.shipment_request is None


def test_two_senders_remain_independent():
    coordinator = _build_coordinator(
        extractor_responses=[_complete_partial(), _complete_partial()]
    )
    coordinator.handle_user_message("user-a", "ship a")
    coordinator.handle_user_message("user-b", "ship b")
    session_a = coordinator.service.get_or_create_session("user-a")
    session_b = coordinator.service.get_or_create_session("user-b")
    assert session_a.session_id != session_b.session_id
    assert session_a.stage == WorkflowStage.AWAITING_CONFIRMATION
    assert session_b.stage == WorkflowStage.AWAITING_CONFIRMATION


def test_economist_failure_moves_session_to_failed():
    coordinator = _build_coordinator(economist=FakeEconomist(fail=True))
    session, response = coordinator.handle_user_message("user-1", "ship widgets")
    assert session.stage == WorkflowStage.FAILED
    assert session.shipment_request is not None
    assert "economic analysis" in response.lower()


def test_routing_failure_moves_session_to_failed():
    coordinator = _build_coordinator(router=FakeRouter(fail=True))
    session, response = coordinator.handle_user_message("user-1", "ship widgets")
    assert session.stage == WorkflowStage.FAILED
    assert session.econ_data is not None
    assert "routing" in response.lower()


def test_treasury_quote_failure_moves_session_to_failed():
    coordinator = _build_coordinator(treasury=FakeTreasury(quote_fail=True))
    session, response = coordinator.handle_user_message("user-1", "ship widgets")
    assert session.stage == WorkflowStage.FAILED
    assert session.route_data is not None
    assert "quote" in response.lower()


def test_treasury_payment_failure_moves_session_to_failed():
    coordinator = _build_coordinator(treasury=FakeTreasury(payment_fail=True))
    coordinator.handle_user_message("user-1", "ship widgets")
    session, response = coordinator.handle_user_message("user-1", "CONFIRM")
    assert session.stage == WorkflowStage.FAILED
    assert session.route_data is not None
    assert session.econ_data is not None
    assert "payment" in response.lower()


def test_later_failure_preserves_previous_successful_data():
    coordinator = _build_coordinator(router=FakeRouter(fail=True))
    coordinator.handle_user_message("user-1", "ship widgets")
    session = coordinator.service.get_or_create_session("user-1")
    assert session.shipment_request is not None
    assert session.econ_data is not None
    assert session.route_data is None


def test_coordinator_uses_protocol_compatible_fake_clients():
    economist = FakeEconomist()
    router = FakeRouter()
    treasury = FakeTreasury()
    coordinator = _build_coordinator(
        economist=economist,
        router=router,
        treasury=treasury,
    )
    coordinator.handle_user_message("user-1", "ship widgets")
    assert economist.calls == 1
    assert router.calls == 1
    assert treasury.quote_calls == 1


def test_existing_conversation_tests_still_pass():
    service = OrchestratorService(InMemorySessionStore())
    controller = ConversationController(service, FakeExtractor([_complete_partial()]))
    session, _ = controller.process_message("user-1", "ship widgets")
    assert session.stage == WorkflowStage.READY_FOR_ECONOMIST


def _run_async(coro):
    return asyncio.run(coro)


def test_async_economist_client_completes_quote_workflow():
    economist = AsyncFakeEconomist()
    router = FakeRouter()
    treasury = FakeTreasury()
    coordinator = _build_coordinator(
        economist=economist,
        router=router,
        treasury=treasury,
    )

    session, response = _run_async(
        coordinator.handle_user_message_async("user-1", "ship widgets")
    )

    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION
    assert "## AeroFreight AI Shipment Quote" in response
    assert economist.calls == 1
    assert router.calls == 1
    assert treasury.quote_calls == 1


def test_async_path_still_uses_sync_mock_router_and_treasury():
    economist = AsyncFakeEconomist()
    router = FakeRouter()
    treasury = FakeTreasury()
    coordinator = _build_coordinator(
        economist=economist,
        router=router,
        treasury=treasury,
    )

    _run_async(coordinator.handle_user_message_async("user-1", "ship widgets"))
    session, response = _run_async(
        coordinator.handle_user_message_async("user-1", "CONFIRM")
    )

    assert session.stage == WorkflowStage.COMPLETED
    assert router.calls == 1
    assert treasury.quote_calls == 1
    assert treasury.payment_calls == 1
    assert "simulated payment completed" in response.lower()


def test_async_economist_failure_moves_session_to_failed():
    coordinator = _build_coordinator(economist=AsyncFakeEconomist(fail=True))
    session, response = _run_async(
        coordinator.handle_user_message_async("user-1", "ship widgets")
    )

    assert session.stage == WorkflowStage.FAILED
    assert session.shipment_request is not None
    assert "economic analysis" in response.lower()


def test_sync_mock_clients_still_work_after_async_addition():
    coordinator = _build_coordinator()
    session, response = coordinator.handle_user_message("user-1", "ship widgets")
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION
    assert "## AeroFreight AI Shipment Quote" in response

    session, response = coordinator.handle_user_message("user-1", "CONFIRM")
    assert session.stage == WorkflowStage.COMPLETED
    assert "simulated payment completed" in response.lower()


def test_async_confirmation_behavior_matches_sync():
    coordinator = _build_coordinator()
    _run_async(coordinator.handle_user_message_async("user-1", "ship widgets"))
    session, _ = _run_async(
        coordinator.handle_user_message_async("user-1", "yes")
    )
    assert session.stage == WorkflowStage.AWAITING_CONFIRMATION

    session, _ = _run_async(
        coordinator.handle_user_message_async("user-1", "confirm")
    )
    assert session.stage == WorkflowStage.COMPLETED
