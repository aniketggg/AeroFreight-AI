"""End-to-end local demo of the AeroFreight swarm — one command, fully offline.

Boots the mock data API (FastAPI/uvicorn) in a background thread, then runs a
``Bureau`` containing the Orchestrator + Tariff + Freight + Escrow agents plus a
"Sarah" tester client that injects the natural-language request and prints the
orchestrator's streamed replies (mimicking the ASI:One chat interface).

    python run_demo.py
"""

from __future__ import annotations

import asyncio
import threading

import requests
import uvicorn
from uagents import Agent, Bureau, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

from agents.config import API_BASE_URL, API_HOST, API_PORT, ORCH_ADDRESS, TESTER_SEED
from mock_api.server import app

# Importing the agent modules registers their handlers and gives us the objects.
from agents.escrow_agent import escrow_agent
from agents.freight_agent import freight_agent
from agents.orchestrator import orchestrator
from agents.tariff_agent import tariff_agent

SARAH_PROMPT = (
    "I have an emergency. I need to air-freight 200kg of semiconductor components "
    "from Shenzhen (SZX) to our warehouse in Austin, TX (AUS). They must arrive by "
    "next Thursday. My maximum budget is $3,500. Please handle the route "
    "optimization, customs compliance, and give me a ready-to-sign contract."
)


def _serve_api() -> None:
    """Run uvicorn in a worker thread (no signal handlers off the main thread)."""
    config = uvicorn.Config(app, host=API_HOST, port=API_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    asyncio.run(server.serve())


def _wait_for_api(timeout: float = 15.0) -> bool:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{API_BASE_URL}/health", timeout=1).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.25)
    return False


# --------------------------------------------------------------------------- #
# "Sarah" — a tester client that talks to the orchestrator over the chat protocol
# --------------------------------------------------------------------------- #
tester = Agent(name="sarah-client", seed=TESTER_SEED)
tester_chat = Protocol(spec=chat_protocol_spec)


@tester_chat.on_message(ChatMessage)
async def on_orchestrator_reply(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
    print("\n" + "─" * 74)
    print("AeroFreight Orchestrator →\n")
    print(msg.text())
    print("─" * 74, flush=True)


@tester_chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


@tester.on_event("startup")
async def kickoff(ctx: Context):
    await asyncio.sleep(2.0)  # let the bureau + API settle
    print("\n" + "=" * 74)
    print("Sarah (via ASI:One) →\n")
    print(SARAH_PROMPT)
    print("=" * 74, flush=True)
    await ctx.send(ORCH_ADDRESS, ChatMessage(content=[TextContent(text=SARAH_PROMPT)]))


tester.include(tester_chat)


def main() -> None:
    threading.Thread(target=_serve_api, daemon=True).start()
    if not _wait_for_api():
        raise SystemExit("Mock API failed to start on " + API_BASE_URL)
    print(f"[demo] Mock data API ready at {API_BASE_URL}")
    print(f"[demo] Orchestrator address: {ORCH_ADDRESS}")

    bureau = Bureau(agents=[orchestrator, tariff_agent, freight_agent, escrow_agent, tester])
    bureau.run()


if __name__ == "__main__":
    main()
