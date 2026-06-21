"""Local interactive CLI for AeroFreight AI demonstration."""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from orchestrator.conversation import ConversationController
from orchestrator.coordinator import WorkflowCoordinator
from orchestrator.extractor import ClaudeShipmentExtractor, ExtractorConfigurationError
from orchestrator.extractor import ExtractionError
from orchestrator.mock_agents import (
    MockEconomistAgent,
    MockRoutingAgent,
    MockTreasuryAgent,
)
from orchestrator.service import OrchestratorService
from orchestrator.session_store import InMemorySessionStore

LOCAL_SENDER = "local-demo-user"

WELCOME = """
AeroFreight AI — Local Demo
===========================
This CLI runs a local shipment workflow with simulated freight, tax, routing,
documents, and payment values. Claude is used only to extract shipment details.

Commands:
  EXIT          — quit
  NEW SHIPMENT  — reset and start over
  CONFIRM       — execute simulated payment after receiving a quote
"""


def main() -> None:
    load_dotenv()

    try:
        extractor = ClaudeShipmentExtractor()
    except ExtractorConfigurationError:
        print(
            "ANTHROPIC_API_KEY is not configured.\n"
            "Create a local .env file:\n"
            "  cp .env.example .env\n"
            "Then edit .env and set ANTHROPIC_API_KEY to your own key.",
            file=sys.stderr,
        )
        sys.exit(1)

    store = InMemorySessionStore()
    service = OrchestratorService(store)
    conversation = ConversationController(service, extractor)
    coordinator = WorkflowCoordinator(
        conversation=conversation,
        service=service,
        economist=MockEconomistAgent(),
        router=MockRoutingAgent(),
        treasury=MockTreasuryAgent(),
    )

    print(WELCOME.strip())

    while True:
        try:
            user_message = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_message:
            continue

        if user_message.upper() == "EXIT":
            print("Goodbye.")
            break

        try:
            _, response = coordinator.handle_user_message(LOCAL_SENDER, user_message)
        except ExtractionError as exc:
            print(f"\nOrchestrator: {exc}")
            continue

        print(f"\nOrchestrator: {response}")


if __name__ == "__main__":
    main()
