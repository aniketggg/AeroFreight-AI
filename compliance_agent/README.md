# Step 4 — Compliance & Document Agent (Owner: Aniket)

Acts as the legal entity of the swarm: given the chosen route and cargo, it
retrieves the **required forms** and their **blank templates**.

```
Orchestrator --ComplianceRequest--> [compliance-document-agent] --DocTemplates--> Orchestrator
```

- **Input:** `ComplianceRequest` (from `shared_models.py`) — the accumulated
  *Global State*: `shipment` (Step 1) + `econ` (Ashwin, Step 2) + `route`
  (Riya, Step 3).
- **Output:** `DocTemplates` (from `shared_models.py`) — `required_form_names`
  plus `blank_form_structures` (the empty JSON skeletons Neel fills in Step 5).

> `ComplianceRequest` is an **additive** model added to the shared spine — it
> nests the three frozen upstream artifacts and changes none of them.

## What it does

1. **Selects** the paperwork for the shipment from a rule-based form catalog,
   keyed off transport mode, route countries, declared value, and cargo type.
2. **Retrieves** each form's blank structure through a *browser-based retrieval*
   layer (simulated by default, optional live web search) — the spec's
   "automated tool (WebBaseLoader / Tavily / simulated browser script)".

### Form selection rules

| Trigger | Form(s) |
|---|---|
| Always (any cross-border shipment) | Commercial Invoice, Packing List |
| `mode == "AIR"` | Air Waybill (AWB), Air Cargo Advance Screening (ACAS)¹ |
| `mode == "SHIP"` | Bill of Lading (B/L), Importer Security Filing (ISF 10+2)¹ |
| Formal entry (`declared_value > $2,500`)¹ | CBP **7501** (Entry Summary), CBP **3461** (Entry/ID), CBP **301** (Customs Bond) |
| Foreign origin (non-US), not USMCA | Certificate of Origin |
| Origin `MX`/`CA` | USMCA Certificate of Origin (instead of the generic one) |
| Cargo: lithium/battery/hazmat + AIR | Shipper's Declaration for Dangerous Goods (IATA) |
| Cargo: lithium/battery/hazmat + SHIP | Multimodal Dangerous Goods Form (IMO IMDG) |
| Cargo: food/pharma/cosmetic | FDA Prior Notice |
| Cargo: alcohol/wine/spirits | TTB Import Permit |
| Cargo: RF device (phone/router/wireless)¹ | FCC Form 740 |

¹ U.S.-import forms apply only when `destination.country == "US"`. The
`$2,500` formal-entry line is the same threshold Ashwin uses for
`is_high_value`, so the document set agrees with the economic baseline.

Required forms come back in a deterministic broker-packet order; every name in
`required_form_names` has a matching skeleton in `blank_form_structures`.

### Browser-based retrieval (`retrieval.py`)

- **Simulated (default):** no network. Returns the curated blank skeleton and
  logs the canonical source URL it "fetched" from. Keeps the agent, demo, and
  tests fully offline and deterministic.
- **Live** (`AEROFREIGHT_COMPLIANCE_LIVE=true`): confirms the current official
  source URL via the **Tavily Search API** (`TAVILY_API_KEY`) or, failing that,
  an `httpx` reachability check against the catalog URL (WebBaseLoader-style).
  Any failure degrades gracefully back to simulated, so a missing key never
  breaks the pipeline. Government forms are PDFs, so the field *skeleton* is
  always the curated one; live mode refreshes/verifies provenance.

## Layout
- `compliance.py` — pure, framework-agnostic logic (no uAgents dependency).
  Form catalog + selection rules + blank skeletons. Entry point:
  `compute_doc_templates(req) -> DocTemplates`. `explain(req)` returns a verbose
  breakdown (sources, agencies, reasons) for logs/UI.
- `retrieval.py` — the browser-based retrieval layer (simulated / live).
- `agent.py` — thin uAgents wrapper: `@on_message(ComplianceRequest, replies=DocTemplates)`.
- `demo.py` — runs four sample shipments through the pure logic and prints the
  required-forms packet + a sample blank skeleton (no agent stack needed).
- `run_local.py` — live uAgents round-trip: a stub orchestrator sends a
  `ComplianceRequest`, the real agent replies with `DocTemplates` over the transport.
- `test_compliance.py` — 26 tests for the selection rules + retrieval helpers.
- `test_agent.py` — 12 tests for the uAgents layer (handler replies correct
  `DocTemplates` to sender via a fake `Context`; deterministic address;
  handler/reply registration; schema-digest regression guard).

## Run

```bash
# from repo root
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # uagents + pydantic (+ httpx, pytest)

# all tests (38) — no network needed
python -m compliance_agent.test_compliance  # pure logic   (or: pytest compliance_agent/test_compliance.py)
python -m compliance_agent.test_agent       # uAgents layer (or: pytest compliance_agent/test_agent.py)
pytest compliance_agent/                    # both at once

# see outputs
python -m compliance_agent.demo            # sample DocTemplates via pure logic
python -m compliance_agent.run_local       # live agent round-trip → prints DocTemplates JSON

# run just the agent (prints its address for the orchestrator to wire up)
python -m compliance_agent.agent
```

### Config (env vars)
- `AEROFREIGHT_COMPLIANCE_SEED` — deterministic seed → stable address (default `aerofreight-compliance-seed-v1`).
- `AEROFREIGHT_COMPLIANCE_PORT` — agent port (default `8004`).
- `AEROFREIGHT_COMPLIANCE_LIVE` — `true` to do real web search/fetch for form sources (default simulated/offline).
- `TAVILY_API_KEY` — optional; enables Tavily search in live mode (falls back to an `httpx` reachability check without it).
- `AEROFREIGHT_MAILBOX` — `true` to run cross-process (Agentverse); default in-process.
