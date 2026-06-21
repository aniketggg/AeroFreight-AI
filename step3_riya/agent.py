from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from openai import OpenAI
from pydantic.v1 import Field
from uagents import Agent, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

from route_logic import calculate_route
from routing_models import (
    EconData,
    Item,
    RoutingRequest,
    ShipmentRequest,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_NAME = "aerofreight-riya-routing"
AGENT_PORT = 8003

# Keep this false while developing locally so your current Agentverse
# certificate issue does not prevent the agent from starting.
ENABLE_MAILBOX = os.getenv("ENABLE_MAILBOX", "false").lower() == "true"

AGENT_SEED = os.getenv(
    "RIYA_AGENT_SEED",
    "aerofreight riya routing agent development seed phrase",
)


agent_options: dict[str, Any] = {
    "name": AGENT_NAME,
    "seed": AGENT_SEED,
    "port": AGENT_PORT,
}

# Enable this only when you are ready to connect to Agentverse/ASI:One.
if ENABLE_MAILBOX:
    agent_options["mailbox"] = True
    agent_options["publish_agent_details"] = True


riya_agent = Agent(**agent_options)


# ---------------------------------------------------------------------------
# Structured messages used by the central AeroFreight orchestrator
# ---------------------------------------------------------------------------

class RouteRequestMessage(Model):
    """
    Message sent by the central orchestrator to Riya.

    The payload must match the RoutingRequest Pydantic model.
    """

    payload: dict[str, Any]


class RouteResponseMessage(Model):
    """
    Message returned by Riya to the central orchestrator.
    """

    ok: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


routing_protocol = Protocol(
    name="AeroFreightRoutingProtocol",
    version="1.0.0",
)


@routing_protocol.on_message(
    model=RouteRequestMessage,
    replies=RouteResponseMessage,
)
async def handle_structured_route_request(
    ctx: Context,
    sender: str,
    msg: RouteRequestMessage,
) -> None:
    """
    Handle the normal orchestrator -> Riya workflow.

    This path does not call an LLM because the orchestrator has already
    collected and validated the shipment information.
    """

    ctx.logger.info(f"Received structured routing request from {sender}")

    try:
        request = RoutingRequest.model_validate(msg.payload)
        result = calculate_route(request)

        ctx.logger.info(
            "Selected %s route with total landed cost $%.2f",
            result.selected_mode,
            result.total_landed_cost_usd,
        )

        await ctx.send(
            sender,
            RouteResponseMessage(
                ok=True,
                payload=result.model_dump(),
            ),
        )

    except Exception as exc:
        ctx.logger.exception("Structured routing request failed")

        await ctx.send(
            sender,
            RouteResponseMessage(
                ok=False,
                error=str(exc),
            ),
        )


# ---------------------------------------------------------------------------
# ASI:One Chat Protocol
# ---------------------------------------------------------------------------

chat_protocol = Protocol(spec=chat_protocol_spec)


def get_asi_client() -> OpenAI:
    """
    Create an ASI:One API client.

    ASI:One exposes an OpenAI-compatible API.
    """

    api_key = os.getenv("ASI1_API_KEY")

    if not api_key:
        raise RuntimeError(
            "ASI1_API_KEY is not set. Export your ASI:One API key before "
            "sending natural-language chat requests."
        )

    return OpenAI(
        base_url="https://api.asi1.ai/v1",
        api_key=api_key,
    )


def extract_json_object(text: str) -> dict[str, Any]:
    """
    Parse JSON returned by ASI:One.

    This also handles responses surrounded by Markdown code fences.
    """

    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise ValueError(
                f"ASI:One did not return a valid JSON object: {cleaned}"
            )

        parsed = json.loads(cleaned[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("ASI:One response must be a JSON object.")

    return parsed


def convert_text_to_routing_request(user_text: str) -> dict[str, Any]:
    """
    Use ASI:One to convert natural language into RoutingRequest JSON.

    The actual route and prices are still calculated deterministically by
    route_logic.py rather than invented by the language model.
    """

    client = get_asi_client()

    system_prompt = """
You are the input adapter for AeroFreight AI's routing agent.

Convert the user's shipment description into the exact RoutingRequest
structure shown below.

If every required value is present, return only:

{
  "status": "complete",
  "data": {
    "shipment": {
      "origin": {
        "country": "two-letter country code",
        "state": "state or province",
        "city": "city"
      },
      "destination": {
        "country": "US",
        "state": "US state",
        "city": "US city"
      },
      "items": [
        {
          "name": "item name",
          "quantity": 1,
          "category": "category"
        }
      ],
      "total_weight_kg": 0.0,
      "total_volume_cbm": 0.0,
      "timeframe": "SPEED or COST",
      "declared_value_usd": 0.0
    },
    "econ": {
      "transport_preference": "AIR, SHIP, or EITHER",
      "is_high_value": false,
      "is_luxury": false,
      "base_entry_tax_usd": 0.0
    }
  }
}

If any required value is missing, return only:

{
  "status": "missing",
  "prompt": "A concise question asking for the missing information."
}

Rules:
- Do not calculate freight prices.
- Do not calculate the final route.
- Do not invent missing tax information.
- The destination country must be the United States.
- timeframe must be exactly SPEED or COST.
- transport_preference must be exactly AIR, SHIP, or EITHER.
- Return valid JSON only, with no Markdown or explanation.
""".strip()

    response = client.chat.completions.create(
        model="asi1",
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_text,
            },
        ],
        max_tokens=1500,
    )

    content = response.choices[0].message.content

    if not content:
        raise ValueError("ASI:One returned an empty response.")

    return extract_json_object(str(content))


def extract_chat_text(msg: ChatMessage) -> str:
    """
    Combine all text chunks in an incoming ChatMessage.
    """

    chunks: list[str] = []

    for item in msg.content:
        if isinstance(item, TextContent):
            chunks.append(item.text)

    return "\n".join(chunks).strip()


async def send_chat_response(
    ctx: Context,
    recipient: str,
    text: str,
) -> None:
    """
    Return a standard ASI:One-compatible ChatMessage.
    """

    await ctx.send(
        recipient,
        ChatMessage(
            timestamp=datetime.now(timezone.utc),
            msg_id=uuid4(),
            content=[
                TextContent(
                    type="text",
                    text=text,
                ),
                EndSessionContent(type="end-session"),
            ],
        ),
    )


@chat_protocol.on_message(ChatMessage)
async def handle_chat_message(
    ctx: Context,
    sender: str,
    msg: ChatMessage,
) -> None:
    """
    Handle a natural-language message from ASI:One Chat.

    ASI:One extracts the structured request. The deterministic routing engine
    then calculates the route and cost breakdown.
    """

    await ctx.send(
        sender,
        ChatAcknowledgement(
            timestamp=datetime.now(timezone.utc),
            acknowledged_msg_id=msg.msg_id,
        ),
    )

    user_text = extract_chat_text(msg)

    if not user_text:
        await send_chat_response(
            ctx,
            sender,
            "Please provide a shipment routing request.",
        )
        return

    ctx.logger.info(f"Received ASI:One chat request from {sender}")

    try:
        extraction = convert_text_to_routing_request(user_text)

        if extraction.get("status") == "missing":
            prompt = extraction.get(
                "prompt",
                "Additional shipment information is required.",
            )

            await send_chat_response(
                ctx,
                sender,
                str(prompt),
            )
            return

        if extraction.get("status") != "complete":
            raise ValueError(
                "ASI:One returned an unsupported extraction status."
            )

        request = RoutingRequest.model_validate(extraction.get("data"))
        result = calculate_route(request)

        result_text = (
            "AeroFreight routing completed successfully.\n\n"
            f"Selected mode: {result.selected_mode}\n"
            f"Route: {' → '.join(result.optimal_route_nodes)}\n"
            f"Countries visited: {', '.join(result.countries_visited)}\n\n"
            "Cost breakdown:\n"
            f"- Freight: ${result.freight_cost_usd:,.2f}\n"
            f"- Inland trucking: ${result.inland_trucking_cost_usd:,.2f}\n"
            f"- Tolls and route tariffs: "
            f"${result.tolls_and_route_tariffs_usd:,.2f}\n"
            f"- Entry tax: ${result.entry_tax_usd:,.2f}\n"
            f"- Total landed cost: "
            f"${result.total_landed_cost_usd:,.2f}"
        )

        await send_chat_response(
            ctx,
            sender,
            result_text,
        )

    except Exception as exc:
        ctx.logger.exception("ASI:One routing request failed")

        await send_chat_response(
            ctx,
            sender,
            f"I could not process the routing request: {exc}",
        )


@chat_protocol.on_message(ChatAcknowledgement)
async def handle_chat_acknowledgement(
    ctx: Context,
    sender: str,
    msg: ChatAcknowledgement,
) -> None:
    ctx.logger.debug(
        f"Received acknowledgement from {sender} "
        f"for message {msg.acknowledged_msg_id}"
    )


# Attach both protocols to the same agent.
riya_agent.include(
    routing_protocol,
    publish_manifest=ENABLE_MAILBOX,
)

riya_agent.include(
    chat_protocol,
    publish_manifest=ENABLE_MAILBOX,
)


# ---------------------------------------------------------------------------
# Startup and standalone demo
# ---------------------------------------------------------------------------

@riya_agent.on_event("startup")
async def startup(ctx: Context) -> None:
    mode = "Agentverse mailbox" if ENABLE_MAILBOX else "local development"

    ctx.logger.info(f"Riya routing agent address: {riya_agent.address}")
    ctx.logger.info(f"Running in {mode} mode")


def run_demo() -> None:
    """
    Test the deterministic routing engine without ASI:One or Agentverse.
    """

    request = RoutingRequest(
        shipment=ShipmentRequest(
            origin={
                "country": "CN",
                "state": "Guangdong",
                "city": "Shenzhen",
            },
            destination={
                "country": "US",
                "state": "TX",
                "city": "Austin",
            },
            items=[
                Item(
                    name="Electronics",
                    quantity=10,
                    category="electronics",
                )
            ],
            total_weight_kg=800,
            total_volume_cbm=4.2,
            timeframe="COST",
            declared_value_usd=5000,
        ),
        econ=EconData(
            transport_preference="EITHER",
            is_high_value=True,
            is_luxury=False,
            base_entry_tax_usd=350,
        ),
    )

    result = calculate_route(request)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
    else:
        riya_agent.run()
