"""AeroFreight orchestrator uAgent with Agent Chat Protocol support."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

from orchestrator.conversation import ConversationController
from orchestrator.coordinator import WorkflowCoordinator
from orchestrator.extractor import ClaudeShipmentExtractor
from orchestrator.mock_agents import (
    MockEconomistAgent,
    MockRoutingAgent,
    MockTreasuryAgent,
)
from orchestrator.remote_agents import UAgentsEconomistClient, UAgentsRoutingClient
from orchestrator.service import OrchestratorService
from orchestrator.uagents_storage import ContextSessionStore

DEFAULT_AGENT_NAME = "aerofreight-orchestrator"
DEFAULT_AGENT_PORT = 8001

SAFE_PROCESSING_ERROR = (
    "AeroFreight AI encountered a temporary processing error. Please try again."
)
EMPTY_TEXT_RESPONSE = "Please send a text message describing your shipment."


class AgentConfigurationError(Exception):
    """Raised when the uAgent is not configured for startup."""


def extract_text_content(message: ChatMessage) -> str:
    """Collect and join text blocks from a chat message."""
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
            continue
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text = getattr(block, "text", "")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def strip_leading_agent_mention(text: str) -> str:
    """Remove one leading ASI:One agent mention from incoming text."""
    return re.sub(
        r"^\s*@agent1[a-z0-9]+\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    ).strip()


def _load_agent_settings() -> tuple[str, str, int]:
    load_dotenv()

    agent_seed = os.getenv("AGENT_SEED", "").strip()
    if not agent_seed:
        raise AgentConfigurationError(
            "AGENT_SEED is not configured. Set it in your environment or .env file."
        )

    agent_name = os.getenv("AGENT_NAME", DEFAULT_AGENT_NAME).strip() or DEFAULT_AGENT_NAME

    port_raw = os.getenv("AGENT_PORT", str(DEFAULT_AGENT_PORT)).strip()
    try:
        agent_port = int(port_raw)
    except ValueError as exc:
        raise AgentConfigurationError(
            f"AGENT_PORT must be a valid integer, got {port_raw!r}."
        ) from exc

    return agent_seed, agent_name, agent_port


def _resolve_economist(ctx: Context, economist_override=None):
    """Use injected economist, remote client, or mock fallback."""
    if economist_override is not None:
        return economist_override

    economist_address = os.getenv("ECONOMIST_AGENT_ADDRESS", "").strip()
    if economist_address:
        timeout_raw = os.getenv("ECONOMIST_AGENT_TIMEOUT_SECONDS", "30").strip()
        try:
            timeout_seconds = int(timeout_raw)
        except ValueError:
            timeout_seconds = 30
        ctx.logger.info("Using remote Economist agent from configuration")
        return UAgentsEconomistClient(
            ctx,
            economist_address,
            timeout_seconds=timeout_seconds,
        )

    ctx.logger.info(
        "Mock Economist mode active (ECONOMIST_AGENT_ADDRESS not set)"
    )
    return MockEconomistAgent()


def _resolve_router(ctx: Context, router_override=None):
    """Use injected router, remote client, or mock fallback."""
    if router_override is not None:
        return router_override

    router_address = os.getenv("ROUTER_AGENT_ADDRESS", "").strip()
    if router_address:
        timeout_raw = os.getenv("ROUTER_AGENT_TIMEOUT_SECONDS", "30").strip()
        try:
            timeout_seconds = int(timeout_raw)
        except ValueError:
            timeout_seconds = 30
        ctx.logger.info("Using remote Router agent from configuration")
        return UAgentsRoutingClient(
            ctx,
            router_address,
            timeout_seconds=timeout_seconds,
        )

    ctx.logger.info(
        "Mock Router mode active (ROUTER_AGENT_ADDRESS not set)"
    )
    return MockRoutingAgent()


def _build_response_message(response_text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=response_text)],
    )


async def process_chat_message(
    ctx: Context,
    sender: str,
    msg: ChatMessage,
    extractor,
    economist=None,
    router=None,
    treasury=None,
) -> None:
    """Acknowledge, process, and respond to an incoming chat message."""
    await ctx.send(
        sender,
        ChatAcknowledgement(
            timestamp=datetime.now(timezone.utc),
            acknowledged_msg_id=msg.msg_id,
        ),
    )

    user_text = strip_leading_agent_mention(extract_text_content(msg))
    if not user_text:
        await ctx.send(sender, _build_response_message(EMPTY_TEXT_RESPONSE))
        return

    try:
        session_store = ContextSessionStore(ctx.storage)
        service = OrchestratorService(session_store)
        conversation = ConversationController(service, extractor)
        coordinator = WorkflowCoordinator(
            conversation=conversation,
            service=service,
            economist=_resolve_economist(ctx, economist),
            router=_resolve_router(ctx, router),
            treasury=treasury or MockTreasuryAgent(),
        )
        _, response = await coordinator.handle_user_message_async(
            sender_address=sender,
            user_message=user_text,
        )
    except Exception as exc:
        ctx.logger.error("Chat processing failed: %s", type(exc).__name__)
        response = SAFE_PROCESSING_ERROR

    await ctx.send(sender, _build_response_message(response))


def create_agent(
    *,
    extractor=None,
    economist=None,
    router=None,
    treasury=None,
) -> Agent:
    """Create and configure the AeroFreight orchestrator uAgent."""
    agent_seed, agent_name, agent_port = _load_agent_settings()

    if extractor is None:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise AgentConfigurationError(
                "ANTHROPIC_API_KEY is not configured. "
                "Set it in your environment or .env file."
            )
        extractor = ClaudeShipmentExtractor()

    treasury = treasury or MockTreasuryAgent()

    agent = Agent(
        name=agent_name,
        seed=agent_seed,
        port=agent_port,
        mailbox=True,
        publish_agent_details=True,
        readme_path="README.md",
    )

    chat_protocol = Protocol(spec=chat_protocol_spec)

    @chat_protocol.on_message(ChatMessage)
    async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage):
        await process_chat_message(
            ctx,
            sender,
            msg,
            extractor,
            economist,
            router,
            treasury,
        )

    @chat_protocol.on_message(ChatAcknowledgement)
    async def handle_acknowledgement(
        ctx: Context,
        sender: str,
        msg: ChatAcknowledgement,
    ):
        ctx.logger.debug(
            "Received acknowledgement from %s for message %s",
            sender,
            msg.acknowledged_msg_id,
        )

    agent.include(chat_protocol, publish_manifest=True)
    return agent


def main() -> None:
    agent = create_agent()
    print(f"AeroFreight agent address: {agent.address}")
    agent.run()


if __name__ == "__main__":
    main()
