# AeroFreight AI ✈️

> An autonomous **air-freight logistics swarm** built on **Fetch.ai uAgents**, driven entirely from the **ASI:One** chat interface. One natural-language sentence in → an orchestrated multi-agent plan + a ready-to-sign smart-escrow contract out.

*UC Berkeley AI Hackathon.*

Sarah, a supply-chain manager, types:

> *"I need to air-freight 200kg of semiconductor components from Shenzhen (SZX) to Austin (AUS). They must arrive by next Thursday. My max budget is $3,500. Handle route optimization, customs compliance, and give me a ready-to-sign contract."*

…and an **Orchestrator** agent parses it, fans out to a **Tariff** agent and a **Freight-Router** agent in parallel, synthesizes the numbers, asks an **Escrow** agent to mint a contract, and replies with a ready-to-sign plan linking a live Bill-of-Lading page.

```
✅ Logistics Plan Ready! We beat your deadline and budget.
Itinerary (4 days):  ✈️ SZX→LAX Cathay Pacific Cargo CX086   🚚 LAX→AUS FedEx Priority
Compliance:          📋 HS 8541.10 · 💰 2.5% duty cleared
Financials:          Freight $2,800 + Duty $70 = $2,870  (Budget $3,500 — Saved $630)
🔗 Review & authorize the escrow smart contract → carriers dispatch automatically.
```

---

## Architecture

```
ASI:One ──ChatMessage──▶ Orchestrator ──┬─▶ Tariff Agent  ──▶ /tariff/classify
   (Agent Chat Protocol)                ├─▶ Freight Agent ──▶ /freight/quote      (FastAPI mock data)
                                        └─▶ Escrow Agent  ──▶ mints contract + link
                                              │
                                              └─▶ POST /bol ──▶ web/escrow.html (Bill of Lading + "Funded / Dispatched")
```

Four uAgents + a FastAPI mock-data service + a static success page, wired together by a `Bureau` for local
dev. Sub-agents talk to the orchestrator over the uAgents message bus; the orchestrator hosts the **ASI:One
Agent Chat Protocol** so it's discoverable and usable from the ASI:One chat UI.

Full design, message contracts, and a 4-person task split are in **[DESIGN.md](DESIGN.md)**.

---

## Requirements

> ⚠️ **Python 3.12 only.** `uagents 0.25.2` calls `asyncio.get_event_loop()` at agent construction, which
> **breaks on Python 3.13/3.14** (agents construct but their startup handlers never fire). uagents officially
> targets 3.10–3.12. The steps below pin 3.12 with [`uv`](https://docs.astral.sh/uv/).

## Quickstart (local end-to-end demo)

> The project lives in `draft/`; the virtualenv is kept at the **repo root** (one level up).

```bash
# 1. From the repo root, create a Python 3.12 env (kept at the repo root) and install deps
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r draft/requirements.txt
#   (without uv:  python3.12 -m venv .venv && .venv/bin/pip install -r draft/requirements.txt)

# 2. Run the whole swarm offline — boots the mock API + Bureau + a "Sarah" tester client
cd draft
../.venv/bin/python run_demo.py
```

You'll see the request stream through the swarm in real time:

```
Sarah (via ASI:One) → I have an emergency. I need to air-freight 200kg ...
[orchestrator] Parsed -> SZX->AUS 200.0kg 'semiconductor components' budget $3,500 by 2026-07-02
[tariff-agent] Classified -> HS Code: 8541.10 | Duty: 2.5% = $70.00
[freight-router-agent] Best route: Cathay Pacific Cargo CX086 (air SZX->LAX) + FedEx Priority (ground LAX->AUS) | $2,800.00, 4d
[escrow-payment-agent] Minted escrow contract fetch1escrow... holding $2,870.00
AeroFreight Orchestrator → ✅ Logistics Plan Ready! ... 🔗 http://127.0.0.1:8080/app/escrow.html?cid=...
```

Open the printed `🔗` link in a browser to see the **Bill of Lading** and click **Authorize & Fund Escrow**
to trigger the "Escrow Funded — Carriers Dispatched" success screen. (`Ctrl+C` stops the demo.)

### Try other requests
The parser is rule-based and offline. Edit `SARAH_PROMPT` in [run_demo.py](run_demo.py), or import
`agents.parser.parse_request` directly:

```bash
cd draft && ../.venv/bin/python -m agents.parser   # prints the parsed ShipmentSpec for the demo prompt
```

---

## Project structure

```
agents/
  messages.py        Frozen message contracts (uagents.Model) — the integration spine
  config.py          Seeds, derived agent addresses, API/web URLs, business defaults
  parser.py          Rule-based NL -> ShipmentSpec (no LLM, fully offline)
  orchestrator.py    ASI:One Chat Protocol + parallel fan-out + synthesis + BoL registration
  tariff_agent.py    -> POST /tariff/classify
  freight_agent.py   -> POST /freight/quote
  escrow_agent.py    Mints a mock smart-contract id + payment link
mock_api/
  server.py          FastAPI: /tariff/classify, /freight/quote, /bol, static /app mount
  tariff_data.py     HS-code lookup table + duty calc
  carrier_data.py    Carrier dataset + cheapest-deadline-meeting route optimizer
web/
  escrow.html        Bill of Lading + escrow funding success page (self-contained)
run_demo.py          One-command offline end-to-end demo (API + Bureau + tester client)
requirements.txt
DESIGN.md            Architecture, contracts, tech stack, 4-person task split
```

---

## Deploying to Agentverse / ASI:One

The local demo runs every agent in one `Bureau` (no network needed). To make the **Orchestrator**
discoverable and usable from the live ASI:One chat interface:

1. **Run the data API** somewhere reachable (e.g. a small host / tunnel) and point `agents/config.py`
   `API_BASE_URL` / `WEB_BASE_URL` at it.
2. **Run the sub-agents** (Tariff/Freight/Escrow) — in a `Bureau` together, or each with its own
   `mailbox=True` so the orchestrator can reach them across processes.
3. **Run the orchestrator with a mailbox + published manifest** so ASI:One can find and message it:

   ```bash
   AEROFREIGHT_MAILBOX=true AEROFREIGHT_PUBLISH_MANIFEST=true .venv/bin/python -m agents.orchestrator
   ```

   These env flags flip `Agent(mailbox=True)` and `include(chat_proto, publish_manifest=True)` in
   [agents/orchestrator.py](agents/orchestrator.py). Register the printed agent address /
   mailbox on [Agentverse](https://agentverse.ai), then query it from
   [ASI:One](https://asi1.ai). The orchestrator already implements the standard Agent Chat Protocol
   (`ChatMessage` / `ChatAcknowledgement`), so it appears as a chat-capable agent.

The offline `run_demo.py` is the guaranteed-working fallback for judging if live registration is flaky.

---

## How it maps to the hackathon brief

| Step | Where |
|---|---|
| Natural-language intent | `parse_request` in [agents/parser.py](agents/parser.py) |
| Intent routing / chat | Agent Chat Protocol in [agents/orchestrator.py](agents/orchestrator.py) |
| Parallel agent swarm | `asyncio.gather` fan-out to Tariff + Freight |
| Data integration | [mock_api/](mock_api/) FastAPI endpoints |
| Financial synthesis | freight + duty math + budget/deadline checks in the orchestrator |
| Smart escrow | [agents/escrow_agent.py](agents/escrow_agent.py) + `/bol` registration |
| Tangible real-world action | [web/escrow.html](web/escrow.html) — fund escrow / dispatch carriers |
```
