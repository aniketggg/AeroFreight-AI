"""Tests for context-backed session storage."""

import hashlib

import pytest

from orchestrator.models import PartialItem, PartialShipmentData, WorkflowStage
from orchestrator.models import OrchestratorSession
from orchestrator.service import OrchestratorService
from orchestrator.uagents_storage import ContextSessionStore, _storage_key


class FakeStorage:
    def __init__(self) -> None:
        self._data: dict = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def remove(self, key: str) -> None:
        self._data.pop(key, None)


def test_missing_session_returns_none():
    store = ContextSessionStore(FakeStorage())
    assert store.get("user-a") is None


def test_save_and_retrieve_complete_session():
    store = ContextSessionStore(FakeStorage())
    session = OrchestratorSession(
        sender_address="user-a",
        stage=WorkflowStage.AWAITING_CONFIRMATION,
        partial_data=PartialShipmentData(
            origin={"country": "CN", "city": "Shenzhen"},
            total_weight_kg=100.0,
        ),
    )
    store.save(session)
    retrieved = store.get("user-a")
    assert retrieved is not None
    assert retrieved.session_id == session.session_id
    assert retrieved.stage == WorkflowStage.AWAITING_CONFIRMATION
    assert retrieved.partial_data.total_weight_kg == 100.0


def test_datetimes_and_enums_reconstruct_correctly():
    store = ContextSessionStore(FakeStorage())
    session = OrchestratorSession(sender_address="user-a")
    store.save(session)
    retrieved = store.get("user-a")
    assert retrieved is not None
    assert retrieved.created_at.tzinfo is not None
    assert retrieved.stage == WorkflowStage.COLLECTING_INPUT


def test_save_replaces_existing_session():
    store = ContextSessionStore(FakeStorage())
    first = OrchestratorSession(sender_address="user-a")
    store.save(first)
    second = OrchestratorSession(
        sender_address="user-a",
        stage=WorkflowStage.COMPLETED,
    )
    store.save(second)
    retrieved = store.get("user-a")
    assert retrieved is not None
    assert retrieved.stage == WorkflowStage.COMPLETED


def test_delete_makes_get_return_none():
    store = ContextSessionStore(FakeStorage())
    session = OrchestratorSession(sender_address="user-a")
    store.save(session)
    store.delete("user-a")
    assert store.get("user-a") is None


def test_two_sender_addresses_remain_independent():
    storage = FakeStorage()
    store = ContextSessionStore(storage)
    store.save(
        OrchestratorSession(
            sender_address="user-a",
            partial_data=PartialShipmentData(total_weight_kg=10.0),
        )
    )
    store.save(
        OrchestratorSession(
            sender_address="user-b",
            partial_data=PartialShipmentData(total_weight_kg=99.0),
        )
    )
    user_a = store.get("user-a")
    user_b = store.get("user-b")
    assert user_a is not None
    assert user_b is not None
    assert user_a.partial_data.total_weight_kg == 10.0
    assert user_b.partial_data.total_weight_kg == 99.0


def test_storage_keys_never_contain_raw_sender_address():
    sender = "agent1qv..."
    key = _storage_key(sender)
    assert sender not in key
    assert key.startswith("aerofreight_session_")
    assert key == (
        "aerofreight_session_"
        + hashlib.sha256(sender.encode("utf-8")).hexdigest()
    )


def test_no_api_key_or_seed_is_stored():
    storage = FakeStorage()
    store = ContextSessionStore(storage)
    store.save(OrchestratorSession(sender_address="user-a"))
    for value in storage._data.values():
        dumped = str(value)
        assert "ANTHROPIC_API_KEY" not in dumped
        assert "AGENT_SEED" not in dumped


def test_invalid_stored_data_raises_readable_value_error():
    storage = FakeStorage()
    storage.set(_storage_key("user-a"), {"invalid": "session"})
    store = ContextSessionStore(storage)
    with pytest.raises(ValueError, match="invalid or corrupted"):
        store.get("user-a")


def test_restart_behavior_with_orchestrator_service():
    storage = FakeStorage()
    store = ContextSessionStore(storage)
    service = OrchestratorService(store)
    service.get_or_create_session("user-a")
    service.apply_extracted_data(
        "user-a",
        PartialShipmentData(origin={"country": "CN", "state": "GD", "city": "SZ"}),
    )
    service.restart_session("user-a")
    session = service.get_or_create_session("user-a")
    assert session.stage == WorkflowStage.COLLECTING_INPUT
    assert session.partial_data.origin is None
