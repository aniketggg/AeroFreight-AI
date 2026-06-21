"""Tests for in-memory session storage."""

from orchestrator.models import OrchestratorSession, PartialShipmentData, WorkflowStage
from orchestrator.session_store import InMemorySessionStore


def test_save_and_retrieve():
    store = InMemorySessionStore()
    session = OrchestratorSession(sender_address="user-a")
    store.save(session)
    retrieved = store.get("user-a")
    assert retrieved is not None
    assert retrieved.session_id == session.session_id


def test_replace_session():
    store = InMemorySessionStore()
    first = OrchestratorSession(sender_address="user-a")
    store.save(first)
    second = OrchestratorSession(
        sender_address="user-a",
        stage=WorkflowStage.READY_FOR_ECONOMIST,
    )
    store.save(second)
    retrieved = store.get("user-a")
    assert retrieved is not None
    assert retrieved.stage == WorkflowStage.READY_FOR_ECONOMIST


def test_delete_session():
    store = InMemorySessionStore()
    session = OrchestratorSession(sender_address="user-a")
    store.save(session)
    store.delete("user-a")
    assert store.get("user-a") is None


def test_delete_missing_is_safe():
    store = InMemorySessionStore()
    store.delete("missing-user")


def test_separate_sessions_for_separate_users():
    store = InMemorySessionStore()
    store.save(OrchestratorSession(sender_address="user-a"))
    store.save(
        OrchestratorSession(
            sender_address="user-b",
            partial_data=PartialShipmentData(total_weight_kg=99.0),
        )
    )
    user_b = store.get("user-b")
    assert user_b is not None
    user_b.partial_data.total_weight_kg = 1.0
    stored = store.get("user-b")
    assert stored is not None
    assert stored.partial_data.total_weight_kg == 99.0


def test_no_accidental_outside_mutation_of_stored_sessions():
    store = InMemorySessionStore()
    session = OrchestratorSession(sender_address="user-a")
    store.save(session)
    retrieved = store.get("user-a")
    assert retrieved is not None
    retrieved.stage = WorkflowStage.COMPLETED
    again = store.get("user-a")
    assert again is not None
    assert again.stage == WorkflowStage.COLLECTING_INPUT
