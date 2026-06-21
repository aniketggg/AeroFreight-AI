# AeroFreight AI

AeroFreight AI is an autonomous multi-agent freight-forwarding system built with Fetch.ai uAgents. A central **Orchestrator** collects shipment details from natural language, coordinates teammate agents, presents a quote, and executes payment only after explicit user confirmation.

## Current local architecture

```text
User message (CLI or Agent Chat Protocol)
  → ConversationController
  → ClaudeShipmentExtractor (natural-language extraction only)
  → OrchestratorService + validation (deterministic Python)
  → WorkflowCoordinator
  → MockEconomistAgent / MockRoutingAgent / MockTreasuryAgent
  → Quote → exact CONFIRM → simulated payment → COMPLETED
```

**Claude performs natural-language extraction only.** Deterministic Python in `orchestrator/validation.py` and `orchestrator/service.py` validates data and drives workflow transitions.

The mock teammate agents in `orchestrator/mock_agents.py` implement the protocols in `orchestrator/agent_interfaces.py`. The actual Economist, Router, and Treasury agents will be deployed separately on Agentverse. These mock clients will later be replaced by remote Fetch.ai adapters without changing the coordinator's core logic.

**Warning:** All freight costs, tariffs, routes, documents, and payments in this repository are **simulated demo values**. They are not current market prices, legal customs assessments, or real financial transactions.

## Python setup

Requires **Python 3.11+**. Use the project virtual environment in the parent directory:

```bash
source ../.venv/bin/activate
python --version
python -m pip install -r requirements.txt
```

## Local environment

```bash
cp .env.example .env
```

Edit `.env` and add your own credentials:

```text
ANTHROPIC_API_KEY=<your own key>
ANTHROPIC_MODEL=claude-sonnet-4-6
AGENT_SEED=<your own private random seed>
AGENT_NAME=aerofreight-orchestrator
AGENT_PORT=8001
```

Never commit a real API key or agent seed. `.env` is ignored by Git.

**Never publish your `.env` file or `AGENT_SEED`.** Anyone with the seed can impersonate your agent identity.

## Run tests

```bash
python -m pytest -q
```

Unit tests use fake Anthropic clients and **do not consume Claude credits** or make real network requests.

## Run the local CLI

```bash
python -m orchestrator.cli
```

The CLI uses sender address `local-demo-user`, runs the full simulated workflow in memory, and accepts:

| Command       | Action                                |
|---------------|---------------------------------------|
| `CONFIRM`     | Execute simulated payment after quote |
| `NEW SHIPMENT`| Start a fresh workflow                |
| `EXIT`        | Quit                                  |

### Example shipment message

```text
Ship 500 kilograms of semiconductors from Shenzhen, Guangdong, China to Austin, Texas. The cargo is 3 cubic meters, worth $100,000, contains 200 units, and speed is the priority.
```

## Run the local uAgent (Agentverse Mailbox)

The uAgent exposes the same orchestrator workflow through the **Agent Chat Protocol**. The process still runs locally on your machine — Claude uses your local `.env` Anthropic key, and the Economist, Router, and Treasury remain mocks.

**Important:** Your terminal must remain running while the agent is active. Stopping the process disconnects the local Mailbox bridge.

```bash
python -m orchestrator.agent
```

The command prints:

```text
AeroFreight agent address: agent1q...
```

### Connect via Agentverse Inspector

1. Start the agent with `python -m orchestrator.agent`.
2. Open the **Inspector URL** printed in the terminal output.
3. Select **Connect** → **Mailbox**.
4. Keep the local agent process running.

### Test with ASI:One

Once Mailbox connectivity is established through the Inspector, you can chat with the agent from ASI:One using the agent's public address. Multi-turn workflows are supported — you can provide partial shipment details across messages and type `CONFIRM` when you receive a quote.

The current Economist, Router, and Treasury responses are local mocks. Teammates' remote Agentverse agents will replace these mock clients in a later phase.

## Project layout

```text
shared_models.py              # Inter-agent contracts
orchestrator/
  models.py                   # Session and partial shipment models
  validation.py               # Deterministic validation
  session_store.py            # In-memory session storage
  uagents_storage.py          # ctx.storage-backed sessions
  service.py                  # Workflow state machine
  extractor.py                # Claude shipment extraction
  conversation.py             # User message routing
  agent_interfaces.py         # Protocols for teammate agents
  mock_agents.py              # Local deterministic mock agents
  coordinator.py              # End-to-end workflow coordinator
  cli.py                      # Interactive local demo
  agent.py                    # uAgent + Agent Chat Protocol
tests/
```

## Next phase

- Register and publish the orchestrator on Agentverse for persistent discovery
- Remote Fetch.ai adapters implementing `EconomistAgentClient`, `RoutingAgentClient`, and `TreasuryAgentClient`
- Replace mock agents with teammates' live Agentverse agents
- Deeper ASI:One integration and production session persistence
