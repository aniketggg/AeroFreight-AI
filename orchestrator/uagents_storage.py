"""Context-backed session storage for uAgents."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import ValidationError

from orchestrator.models import OrchestratorSession

_SESSION_PREFIX = "aerofreight_session_"


def _storage_key(sender_address: str) -> str:
    digest = hashlib.sha256(sender_address.encode("utf-8")).hexdigest()
    return f"{_SESSION_PREFIX}{digest}"


class ContextSessionStore:
    """Persist orchestrator sessions in uAgents context storage."""

    def __init__(self, storage: Any) -> None:
        self._storage = storage

    def get(self, sender_address: str) -> OrchestratorSession | None:
        key = _storage_key(sender_address)
        stored_value = self._storage.get(key)
        if stored_value is None:
            return None
        try:
            return OrchestratorSession.model_validate(stored_value)
        except ValidationError as exc:
            raise ValueError(
                f"Stored session data for sender is invalid or corrupted."
            ) from exc

    def save(self, session: OrchestratorSession) -> None:
        key = _storage_key(session.sender_address)
        self._storage.set(key, session.model_dump(mode="json"))

    def delete(self, sender_address: str) -> None:
        key = _storage_key(sender_address)
        if hasattr(self._storage, "remove"):
            self._storage.remove(key)
        else:
            self._storage.set(key, None)
