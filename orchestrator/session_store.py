"""Session storage abstraction for orchestrator conversations."""

from __future__ import annotations

from typing import Protocol

from orchestrator.models import OrchestratorSession


class SessionStore(Protocol):
    def get(self, sender_address: str) -> OrchestratorSession | None:
        ...

    def save(self, session: OrchestratorSession) -> None:
        ...

    def delete(self, sender_address: str) -> None:
        ...


class InMemorySessionStore:
    """In-memory session store keyed by sender address."""

    def __init__(self) -> None:
        self._sessions: dict[str, OrchestratorSession] = {}

    def get(self, sender_address: str) -> OrchestratorSession | None:
        session = self._sessions.get(sender_address)
        if session is None:
            return None
        return session.model_copy(deep=True)

    def save(self, session: OrchestratorSession) -> None:
        self._sessions[session.sender_address] = session.model_copy(deep=True)

    def delete(self, sender_address: str) -> None:
        self._sessions.pop(sender_address, None)
