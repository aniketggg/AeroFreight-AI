# AeroFreight AI — Technical Design & Team Plan

> Autonomous air-freight logistics swarm on **Fetch.ai uAgents**, driven entirely from the **ASI:One** chat interface. Natural-language intent → multi-agent orchestration → data integration → a signable escrow contract.

This doc is the single source of truth for the build. It contains the architecture, the **frozen message contracts** every person codes against, the exact tech stack (already validated on this machine), and a **4-person task split** with clear ownership and "definition of done".

---

## 0. TL;DR

Sarah types one sentence into ASI:One. Our **Orchestrator** agent (discoverable on Agentverse via the Chat Protocol) parses it, fans out to a **Tariff** agent and a **Freight-Router** agent in parallel, both of which hit a mock **FastAPI** data service, then synthesizes the numbers and calls an **Escrow** agent that mints a mock smart-contract + a link to a static **Bill-of-Lading / "carriers dispatched"** success page.

```
4 uAgents  +  1 FastAPI mock-data service  +  1 static HTML page  +  1 Bureau runner
```

---

## 1. Validated environment (DO NOT skip — this already bit us)

I installed and smoke-tested the stack on this machine. Findings you must respect:

| Item | Decision | Why |
|---|---|---|
| **Python** | **3.12.x** (use `uv python install 3.12`) | `uagents 0.25.2` calls `asyncio.get_event_loop()` at construction. **Python 3.13/3.14 removed the implicit main-thread loop → agents fail to start / startup handlers never fire.** Confirmed broken on 3.14, working on 3.12. System `python3` here is 3.9.6 (too old). uagents officially supports 3.10–3.12. |
| **Package manager** | `uv` (already installed at `~/.local/bin/uv`) | Fast, reproducible, manages the 3.12 toolchain without touching system Python. |
| **Inter-agent transport** | **`Bureau`** for local dev | Confirmed: a `Bureau` routes messages between local agents **in-process** ("Message dispatched locally") — **no ports/endpoints/mailbox needed for local dev**. |
| **Orchestrator ↔ sub-agent call** | `await ctx.send_and_receive(addr, Msg, response_type=Resp, timeout=30)` | Confirmed working; returns `(reply | None, MsgStatus)`. Lets the orchestrator call sub-agents inline and `asyncio.gather` them for parallelism. |
| **ASI:One discovery** | `mailbox=True` + Chat Protocol, registered on Agentverse | Only the Orchestrator needs a mailbox; sub-agents stay internal. |

**Already done for you:** `.venv` (Python 3.12.13) exists at repo root with `uagents`, `fastapi`, `uvicorn[standard]`, `httpx`, `requests` installed. Activate with `source .venv/bin/activate`.

---

## 2. Architecture

```
                       ┌─────────────────────────────┐
   Sarah  ──text──▶    │      ASI:One Chat UI         │
                       └──────────────┬──────────────┘
                                      │ ChatMessage (Agent Chat Protocol)
                                      ▼
                       ┌─────────────────────────────┐
                       │   ORCHESTRATOR AGENT         │  (mailbox=True, Chat Protocol)
                       │   • ack + parse (regex)      │
                       │   • fan-out (asyncio.gather) │
                       │   • synthesize + budget math │
                       │   • compile Markdown reply   │
                       └───┬───────────┬──────────┬───┘
              send_and_receive   send_and_receive  send_and_receive
                       │           │              │
            ┌──────────▼───┐ ┌─────▼────────┐ ┌───▼──────────┐
            │ TARIFF AGENT │ │ FREIGHT AGENT│ │ ESCROW AGENT │
            │ HS classify  │ │ route optimize│ │ mint contract│
            │ + duty calc  │ │ + deadline   │ │ + BoL link   │
            └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
                   │ httpx          │ httpx          │ (in-proc)
                   ▼                ▼                ▼
            ┌──────────────────────────────┐   ┌─────────────────────┐
            │  MOCK DATA API (FastAPI)      │   │ static escrow.html  │
            │  POST /tariff/classify        │   │ Bill of Lading +    │
            │  POST /freight/quote          │   │ "Escrow Funded /    │
            │  GET  /bol/{contract_id}      │◀──│  Carriers Dispatched"│
            └──────────────────────────────┘   └─────────────────────┘
```

Flow (matches the user-story script):
1. ASI:One routes Sarah's text to the Orchestrator as a `ChatMessage`.
2. Orchestrator `ack`s, regex-parses → `ShipmentSpec`.
3. Orchestrator `asyncio.gather(tariff, freight)` — **parallel**.
4. Orchestrator computes `total = freight + duty`, checks budget + deadline.
5. Orchestrator calls Escrow → `contract_id` + `payment_link`.
6. Orchestrator replies with one Markdown `ChatMessage` (itinerary, compliance, financials, link).
7. Sarah clicks link → static success page.

---

## 3. The integration spine — frozen message contracts

**This file is written FIRST and frozen. Everyone codes against it.** Put in `agents/messages.py`. All wire types are `uagents.Model` (pydantic-backed).

```python
from uagents import Model

# ---- Parsed intent (shared struct) ----
class ShipmentSpec(Model):
    origin: str               # IATA code, e.g. "SZX"
    destination: str          # IATA code, e.g. "AUS"
    weight_kg: float
    commodity: str            # free text, e.g. "semiconductor components"
    deadline_iso: str         # normalized "YYYY-MM-DD"
    budget_usd: float
    declared_value_usd: float  # goods value for duty calc (parser default if absent)

# ---- Tariff agent  (Person 2) ----
class TariffRequest(Model):
    commodity: str
    declared_value_usd: float

class TariffResponse(Model):
    hs_code: str              # "8541.10"
    description: str          # "Semiconductor devices"
    duty_rate_pct: float      # 2.5
    duty_usd: float           # rate% * declared_value

# ---- Freight-Router agent  (Person 3) ----
class FreightLeg(Model):
    mode: str                 # "air" | "ground"
    carrier: str              # "Cathay Pacific Cargo"
    service: str              # "CX086" | "FedEx Priority"
    from_node: str            # "SZX"
    to_node: str              # "LAX"

class FreightRequest(Model):
    origin: str
    destination: str
    weight_kg: float
    deadline_iso: str

class FreightResponse(Model):
    legs: list[FreightLeg]
    total_cost_usd: float
    transit_days: int
    eta_iso: str              # "YYYY-MM-DD"
    meets_deadline: bool

# ---- Escrow agent  (Person 4) ----
class EscrowRequest(Model):
    total_usd: float
    vendor: str               # e.g. "Cathay Pacific Cargo"
    shipment_ref: str         # short id, e.g. "SZX-AUS-200KG"

class EscrowResponse(Model):
    contract_id: str          # "fetch1escrow..." (mock)
    payment_link: str         # "http://localhost:8080/escrow.html?cid=..."
    status: str               # "pending_authorization"
```

> **Rule:** if you need to change a contract, announce it — it's a breaking change for the integrator (Person 1).

### Mock API contract (FastAPI — owned by Person 2 & Person 3)

```
POST /tariff/classify   body {commodity, declared_value_usd}
                        → {hs_code, description, duty_rate_pct, duty_usd}

POST /freight/quote     body {origin, destination, weight_kg, deadline_iso}
                        → {legs:[{mode,carrier,service,from_node,to_node}],
                           total_cost_usd, transit_days, eta_iso, meets_deadline}

GET  /bol/{contract_id} → {contract_id, shipment_ref, legs, total_usd, status}   # for the web page
```

---

## 4. Component design

### 4.1 Orchestrator (Person 1)
- `Agent(name="aerofreight-orchestrator", seed=..., mailbox=True)`.
- Hosts **Agent Chat Protocol** (the ASI:One contract):

```python
from uagents import Protocol
from uagents_core.contrib.protocols.chat import (
    ChatMessage, ChatAcknowledgement, TextContent, chat_protocol_spec,
)
chat = Protocol(spec=chat_protocol_spec)

@chat.on_message(ChatMessage)
async def on_chat(ctx, sender, msg):
    await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))  # required
    spec = parse_request(msg.text())                       # Person 1's regex parser
    tariff, freight = await asyncio.gather(                 # PARALLEL fan-out
        call(ctx, TARIFF_ADDR, TariffRequest(...), TariffResponse),
        call(ctx, FREIGHT_ADDR, FreightRequest(...), FreightResponse),
    )
    total = freight.total_cost_usd + tariff.duty_usd
    escrow = await call(ctx, ESCROW_ADDR, EscrowRequest(...), EscrowResponse)
    await ctx.send(sender, ChatMessage(content=[TextContent(text=render_md(...))]))

orchestrator.include(chat, publish_manifest=True)          # publishes manifest to Agentverse
```

- **Rule-based parser** (`agents/parser.py`): regex for IATA codes (`\b[A-Z]{3}\b` + a known-airport allowlist), `(\d+)\s*kg`, `\$?([\d,]+)` for budget, weekday/"next <day>" → ISO date via `datetime` math, commodity = remainder/keyword match. No LLM, no API key.
- **Markdown renderer** (`render_md`): the final "✅ Logistics Plan Ready" block from the user story.

### 4.2 Tariff Agent (Person 2)
- `@agent.on_message(TariffRequest)` → `httpx.post(API/tariff/classify)` → `TariffResponse`.
- Owns the **HS-code dataset** (small dict): semiconductors → `8541.10 @ 2.5%`, plus a default fallback. Duty = `round(rate/100 * declared_value, 2)`.

### 4.3 Freight-Router Agent (Person 3)
- `@agent.on_message(FreightRequest)` → `httpx.post(API/freight/quote)` → `FreightResponse`.
- Owns the **mock carrier dataset** + route logic: pick cheapest air-leg + ground-leg combo that beats the deadline. Compute `eta_iso = today + transit_days`, `meets_deadline = eta <= deadline`. Reference route: `SZX→LAX` Cathay CX086 (air) + `LAX→AUS` FedEx (ground), ~4 days, ~$2,800.

### 4.4 Escrow Agent + Frontend (Person 4)
- `@agent.on_message(EscrowRequest)` → mint mock `contract_id` (`"fetch1escrow" + uuid4().hex[:12]`), build `payment_link` pointing at the static page with `?cid=...`. Persist a BoL record the `/bol/{cid}` endpoint can serve.
- **Static `web/escrow.html`**: reads `cid` from query string, optionally `fetch('/bol/{cid}')`, renders Bill of Lading + animated **"Escrow Funded ✓ / Carriers Dispatched ✓"** success screen. Served via `python -m http.server 8080` or a FastAPI `StaticFiles` mount.

### 4.5 Runner (Person 1)
- `run_demo.py`: starts mock API (subprocess/uvicorn), builds a `Bureau` with all 4 agents + a **tester client** agent that injects Sarah's prompt on startup and prints the orchestrator's final Markdown. This is the end-to-end demo that runs offline.

---

## 5. Tech stack (exact, validated)

| Layer | Choice | Version (installed) |
|---|---|---|
| Language / runtime | Python | **3.12.13** (via `uv`) |
| Agent framework | `uagents` | 0.25.2 |
| Chat/ASI:One protocol | `uagents_core.contrib.protocols.chat` | uagents-core 0.4.7 |
| Mock data API | `fastapi` + `uvicorn[standard]` | 0.x / 0.49 |
| HTTP client (agents→API) | `httpx` | 0.28.1 |
| Frontend | static HTML/CSS/JS (no framework) | — |
| Local multi-agent runtime | `uagents.Bureau` | — |
| Deploy target | Agentverse (mailbox) + ASI:One discovery | — |
| Pkg/runtime mgr | `uv` | 0.11.x |

Import cheat-sheet (verified against installed pkg):
```python
from uagents import Agent, Bureau, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatMessage, ChatAcknowledgement, TextContent, chat_protocol_spec,
)
# ChatMessage(content=[TextContent(text="hi")]) ; incoming: msg.text()
# ChatAcknowledgement(acknowledged_msg_id=msg.msg_id)
```

---

## 6. Four-person task split

Coupling is minimized: **everyone depends only on §3 (message contracts) + the API spec.** Person 1 integrates. Sub-agents are independently testable by `send_and_receive` from a one-off script.

### 👤 Person 1 — Orchestrator & ASI:One Integration *(critical path / lead)*
**Owns:** `agents/messages.py` (writes + freezes it first), `agents/orchestrator.py`, `agents/parser.py`, `run_demo.py`, Agentverse deployment, README run steps.
**Deliverables**
- [ ] Freeze `messages.py` and share addresses/config in hour 1 (unblocks everyone).
- [ ] Rule-based NL parser → `ShipmentSpec` (handle the exact demo prompt + 2 variants).
- [ ] Chat Protocol handler: ack → parse → `asyncio.gather` fan-out → synthesize → reply.
- [ ] Budget/deadline decision logic + Markdown renderer (the final plan block).
- [ ] `Bureau` runner with tester client; `mailbox=True` path + Agentverse registration.
**Done when:** `python run_demo.py` prints the full ✅ plan offline, and the orchestrator answers from ASI:One.

### 👤 Person 2 — Tariff & Customs Compliance
**Owns:** `agents/tariff_agent.py`, `/tariff/classify` route + HS-code dataset (`mock_api/tariff_data.py`).
**Deliverables**
- [ ] HS-code lookup table (semiconductors `8541.10 @ 2.5%` + ≥5 commodities + default).
- [ ] FastAPI `/tariff/classify` returning the contract shape.
- [ ] Tariff agent: consume `TariffRequest` → call API → `TariffResponse` with `duty_usd`.
- [ ] Standalone test script proving the agent answers via `send_and_receive`.
**Done when:** sending `TariffRequest(commodity="semiconductor components", declared_value_usd=2800)` returns `hs_code=8541.10, duty_rate_pct=2.5, duty_usd=70.0`.

### 👤 Person 3 — Freight Routing & Optimization
**Owns:** `agents/freight_agent.py`, `/freight/quote` route + carrier dataset (`mock_api/carrier_data.py`).
**Deliverables**
- [ ] Mock carrier dataset (air + ground legs, costs, transit times) for SZX/LAX/AUS hubs.
- [ ] Route optimizer: cheapest air+ground combo that beats `deadline_iso`; compute `eta_iso`, `meets_deadline`.
- [ ] FastAPI `/freight/quote` + Freight agent consuming `FreightRequest` → `FreightResponse`.
- [ ] Standalone test proving a multi-leg itinerary + ETA.
**Done when:** `FreightRequest(SZX,AUS,200,deadline)` returns 2 legs (Cathay air + FedEx ground), `total_cost_usd≈2800`, `transit_days=4`, `meets_deadline=True`.

### 👤 Person 4 — Escrow Agent & Frontend (the "tangible outcome")
**Owns:** `agents/escrow_agent.py`, `/bol/{contract_id}` route, `web/escrow.html` (+ CSS/JS), visual polish.
**Deliverables**
- [ ] Escrow agent: `EscrowRequest` → mock `contract_id` + `payment_link` → persist BoL → `EscrowResponse`.
- [ ] `/bol/{contract_id}` endpoint serving the BoL record.
- [ ] Static success page: reads `?cid=`, renders Bill of Lading + "Escrow Funded / Carriers Dispatched" screen. Make it demo-pretty.
- [ ] Serve it (`http.server` or FastAPI `StaticFiles`).
**Done when:** clicking the orchestrator's `payment_link` opens a polished BoL + funded/dispatched confirmation for that `cid`.

### Shared / parallelizable
- `mock_api/server.py` (FastAPI app) is co-owned by P2+P3+P4; one person scaffolds the app + `uvicorn` entry in hour 1, others add their routes.
- `requirements.txt` / `pyproject.toml` — P1.

---

## 7. Milestones (hackathon-paced)

| T | Milestone | Owner |
|---|---|---|
| **H+1** | `messages.py` frozen, FastAPI app skeleton up, agent seeds/addresses shared | P1 (+P2/3/4 scaffold) |
| **H+3** | Each sub-agent passes its standalone `send_and_receive` test against its mock route | P2, P3, P4 |
| **H+4** | Orchestrator fan-out wired; end-to-end `run_demo.py` prints the plan offline | P1 |
| **H+5** | `payment_link` → static success page works for a real `cid` | P4 |
| **H+6** | Orchestrator on Agentverse (`mailbox=True`), answers a live query from ASI:One | P1 |
| **H+7** | Polish: 3 demo prompts, error/timeout handling, README, screen-recording backup | all |

**Integration tip:** sub-agents are testable in isolation *before* the orchestrator exists — write a 20-line `test_<agent>.py` that spins a `Bureau` with your agent + a sender that does one `send_and_receive`. Don't wait on P1.

---

## 8. Risks & gotchas

- **Python version is non-negotiable: 3.12 (10–12 OK), NOT 3.13/3.14.** Verified failure mode on 3.14: agents construct but startup handlers never fire. Use `uv venv --python 3.12`.
- **Sub-agents need no endpoints in a Bureau** — but the moment you run an agent standalone (not in a Bureau) it needs a `mailbox`/`endpoint` to be reachable. Keep local dev in the Bureau.
- **`declared_value_usd` (goods value for duty) isn't in Sarah's prompt.** Decide a default in the parser (the demo implies ~$2,800 → $70 duty). Flag it in the BoL as "declared value."
- **`send_and_receive` timeout** defaults to 30s; sub-agent must reply or the orchestrator gets `None` — handle gracefully (partial plan / retry message).
- **Chat Protocol ack is mandatory** — always send `ChatAcknowledgement` first or ASI:One marks the agent unresponsive.
- **Agentverse registration needs network + (for mainnet) a funded/almanac-registered agent**; budget time for first-deploy auth. Have the offline `run_demo.py` as the guaranteed-working fallback for judging.

---

## 9. Repo layout (target)

```
AeroFreight-AI/
├── agents/
│   ├── messages.py          # P1 — frozen contracts (§3)
│   ├── config.py            # P1 — seeds, addresses, API base URL
│   ├── parser.py            # P1 — NL → ShipmentSpec
│   ├── orchestrator.py      # P1 — Chat Protocol + fan-out + render
│   ├── tariff_agent.py      # P2
│   ├── freight_agent.py     # P3
│   └── escrow_agent.py      # P4
├── mock_api/
│   ├── server.py            # shared FastAPI app
│   ├── tariff_data.py       # P2
│   └── carrier_data.py      # P3
├── web/
│   └── escrow.html          # P4
├── run_demo.py              # P1 — Bureau + tester client (offline E2E)
├── requirements.txt
├── DESIGN.md                # this file
└── README.md
```
