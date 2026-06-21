# AeroFreight AI ✈️

> An autonomous **air-freight logistics swarm** built on **Fetch.ai uAgents**, driven entirely from the **ASI:One** chat interface. One natural-language sentence in → an orchestrated multi-agent plan + a ready-to-sign smart-escrow contract out.

*UC Berkeley AI Hackathon.*

Sarah, a supply-chain manager, types:

> *"I need to air-freight 200kg of semiconductor components from Shenzhen (SZX) to Austin (AUS). They must arrive by next Thursday. My max budget is $3,500. Handle route optimization, customs compliance, and give me a ready-to-sign contract."*

…and an **Orchestrator** agent parses it, fans out to a **Tariff** agent and a **Freight-Router** agent in parallel, synthesizes the numbers, asks an **Escrow** agent to mint a contract, and replies with a ready-to-sign plan linking a live Bill-of-Lading page — where authorizing settles a **real on-chain transaction** on the Fetch.ai testnet.

```
✅ Logistics Plan Ready! We beat your deadline and budget.
Itinerary (3 days):  ✈️ SZX→ICN Asiana Airlines  ✈️ ICN→DFW American Airlines  🚚 DFW→AUS Regional Trucking
Compliance:          📋 HS 8541.10.00 · 💰 0% duty (real USITC rate — semiconductors are duty-free)
Financials:          Freight $3,074.47 + Duty $0.00 = $3,074.47  (Budget $3,500 — Saved $425.53)
🔗 Review & authorize → real Fetch.ai testnet escrow tx → carriers dispatched.
```

> Everything above is computed from **real, open-source data**: routes/carriers/distances from
> [OpenFlights](https://openflights.org/data.html), duty rates from the live
> [USITC HTS](https://hts.usitc.gov/) API, and escrow settled by a real transaction on the
> **Fetch.ai dorado testnet** (cosmpy). No hardcoded prices, no fake contract — see [What's real](#whats-real).

---

## Architecture

```
ASI:One ──ChatMessage──▶ Orchestrator ──┬─▶ Tariff Agent  ──▶ /tariff/classify  ──▶ USITC HTS API (real duty)
   (Agent Chat Protocol)                ├─▶ Freight Agent ──▶ /freight/quote     ──▶ OpenFlights (real routes)
                                        └─▶ Escrow Agent  ──▶ mints contract + link
                                              │
                                              └─▶ POST /bol ──▶ SQLite ──▶ web/escrow.html
                                                                              │  Authorize
                                                                              ▼
                                                              POST /escrow/{id}/authorize ──▶ cosmpy
                                                                              ▼
                                                              REAL tx on Fetch.ai dorado testnet
```

Four uAgents + a FastAPI data/settlement service + a static success page, wired together by a `Bureau` for
local dev. Sub-agents talk to the orchestrator over the uAgents message bus; the orchestrator hosts the
**ASI:One Agent Chat Protocol** so it's discoverable and usable from the ASI:One chat UI. The data is real
(USITC + OpenFlights), records persist in SQLite, and escrow authorization broadcasts a real on-chain
transaction.

Full design, message contracts, and a 4-person task split are in **[DESIGN.md](DESIGN.md)**.

## <a name="whats-real"></a>What's real

| Component | Real data source / network |
|---|---|
| **Customs duty** | Live **USITC HTS** REST API — real US MFN duty rates (cached to `data/hts_cache.json`) |
| **Freight routing** | **OpenFlights** `airports.dat` / `routes.dat` / `airlines.dat` — real airports, real operating airlines, great-circle distances; transparent published-style rate + transit model |
| **Persistence** | **SQLite** (`data/aerofreight.db`) — Bills of Lading survive restarts |
| **Escrow settlement** | **Real on-chain transaction** on the **Fetch.ai dorado testnet** via cosmpy — explorer-verifiable tx hash |
| **Agent framework** | Real **Fetch.ai uAgents** + ASI:One Agent Chat Protocol |
| **Intent parsing** | Deterministic rule-based parser (offline, no key) |

> The freight **price/time** figures come from a transparent estimate model over real distances (live
> air-cargo spot rates require commercial carrier accounts); everything else is live real data.

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

# 2. Run the whole swarm — boots the data/settlement API + Bureau + a "Sarah" tester client
cd draft
../.venv/bin/python run_demo.py
```

You'll see the request stream through the swarm in real time (real data):

```
Sarah (via ASI:One) → I have an emergency. I need to air-freight 200kg ...
[orchestrator] Parsed -> SZX->AUS 200.0kg 'semiconductor components' budget $3,500 by 2026-07-02
[tariff-agent] Classified -> HS Code: 8541.10.00 | Duty: 0.0% = $0.00   (real USITC rate)
[freight-router-agent] Best route: Asiana Airlines SZX->ICN + American Airlines ICN->DFW + Regional Trucking DFW->AUS | $3,074.47, 3d
[escrow-payment-agent] Minted escrow contract fetch1escrow... holding $3,074.47
AeroFreight Orchestrator → ✅ Logistics Plan Ready! ... 🔗 http://127.0.0.1:8080/app/escrow.html?cid=...
```

Open the printed `🔗` link to see the **Bill of Lading** and click **Authorize & Fund Escrow** — the backend
signs and broadcasts a **real Fetch.ai testnet transaction** and the page shows the tx hash + explorer link
(see [On-chain settlement](#on-chain-settlement) for the one-time wallet funding). (`Ctrl+C` stops the demo.)

## <a name="on-chain-settlement"></a>On-chain settlement (Fetch.ai testnet)

Authorizing an escrow calls `POST /escrow/{contract_id}/authorize`, which uses **cosmpy** to sign a real
transfer into a deterministic escrow vault on the **dorado-1 testnet** — a real, explorer-verifiable tx.
A persistent platform wallet (`data/platform_wallet.key`, gitignored) signs it; it needs **testnet FET** once:

```bash
# See the platform wallet address + balance:
curl -s http://127.0.0.1:8080/escrow/info

# Fund it once from the public faucet (the faucet rate-limits per IP):
curl -X POST https://faucet-dorado.fetch.ai/api/v3/claims \
     -H 'Content-Type: application/json' \
     -d '{"address":"<platform_address from /escrow/info>"}'
```

Until the wallet is funded, **Authorize** degrades gracefully: it returns a clear "settlement unavailable"
message and re-enables the button (no fake success). Once funded, authorization returns a real
`tx_hash` + `https://explore-dorado.fetch.ai/transactions/<hash>` link, and the BoL flips to `funded` (in SQLite).
Set `AEROFREIGHT_WALLET_KEY` (hex) to use your own funded signer instead of the generated one.

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
  escrow_agent.py    Mints the escrow contract id + payment link
mock_api/            (the data + settlement service — now real, not mock)
  server.py          FastAPI: tariff/freight/bol + POST /escrow/{id}/authorize + static /app
  tariff_data.py     REAL USITC HTS duty lookup (live API + cache)
  carrier_data.py    REAL OpenFlights routing + transparent pricing/transit model
  geo.py             OpenFlights loader (airports/routes/airlines) + haversine
  store.py           SQLite persistence for Bills of Lading
  chain.py           On-chain escrow on Fetch.ai testnet (cosmpy) — real tx
data/                Real datasets: airports.dat, routes.dat, airlines.dat, hts_cache.json,
                     aerofreight.db (SQLite), platform_wallet.key (gitignored)
web/
  escrow.html        Bill of Lading + escrow page → real tx hash + explorer link
run_demo.py          One-command end-to-end demo (API + Bureau + tester client)
requirements.txt
DESIGN.md            Architecture, contracts, tech stack, 4-person task split
```

---

## Deploying to Agentverse / ASI:One

The local demo runs every agent in one `Bureau` (no network needed). To make the **Orchestrator**
discoverable and usable from the live ASI:One chat interface:

1. **Run the data API** somewhere reachable (e.g. a small host / tunnel) and point `agents/config.py`
   `API_BASE_URL` / `WEB_BASE_URL` at it.
2. **Run the sub-agents** (Tariff/Freight/Escrow). Either keep them in one `Bureau` with the
   orchestrator (simplest — they resolve in-process), **or** run them as separate processes each with
   a mailbox so the out-of-process orchestrator can reach them across the network. All three honor the
   same `AEROFREIGHT_MAILBOX` env flag:

   ```bash
   # from draft/ — each in its own process, mailbox enabled
   AEROFREIGHT_MAILBOX=true ../.venv/bin/python -m agents.tariff_agent
   AEROFREIGHT_MAILBOX=true ../.venv/bin/python -m agents.freight_agent
   AEROFREIGHT_MAILBOX=true ../.venv/bin/python -m agents.escrow_agent
   ```

3. **Run the orchestrator with a mailbox + published manifest** so ASI:One can find and message it:

   ```bash
   AEROFREIGHT_MAILBOX=true AEROFREIGHT_PUBLISH_MANIFEST=true ../.venv/bin/python -m agents.orchestrator
   ```

   These env flags flip `Agent(mailbox=True)` (on the orchestrator **and** each sub-agent) and
   `include(chat_proto, publish_manifest=True)`. Register the printed agent addresses /
   mailboxes on [Agentverse](https://agentverse.ai), then query the orchestrator from
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
