"""Tests for the uAgents layer of Step 4 (Aniket) — `compliance_agent/agent.py`.

These complement `test_compliance.py` (which tests the pure selection logic).
Here we test the *agent* itself:

  * the `@on_message` handler replies with the correct `DocTemplates` to the
    sender (driven through a lightweight fake `Context`, no network/Bureau);
  * the agent is wired correctly: deterministic address, registered handler,
    declared reply type;
  * the shared models produce valid uAgents schema digests (regression guard
    for the pydantic-v1-vs-v2 `Field` bug that breaks message routing).

Runnable two ways:
    pytest compliance_agent/test_agent.py
    python -m compliance_agent.test_agent      # no pytest needed
"""

import asyncio
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from uagents import Model  # noqa: E402
from uagents.crypto import Identity  # noqa: E402

from compliance_agent.agent import (  # noqa: E402
    COMPLIANCE_SEED,
    compliance_agent,
    handle_compliance_request,
)
from compliance_agent.compliance import compute_doc_templates  # noqa: E402
from shared_models import (  # noqa: E402
    ComplianceRequest,
    DocTemplates,
    EconData,
    Item,
    RouteData,
    ShipmentRequest,
    dump,
)

# A realistic-looking bech32 agent address to play "the orchestrator".
SENDER = "agent1qtestsender0000000000000000000000000000000000000000000000000"


# --------------------------------------------------------------------------- #
# Test doubles — a minimal Context that records what the handler sends.
# --------------------------------------------------------------------------- #
class _FakeLogger:
    def info(self, *a, **k):
        pass

    debug = warning = error = exception = info


class FakeContext:
    """Stand-in for uagents.Context: captures ctx.send(...) calls."""

    def __init__(self):
        self.sent = []  # list of (destination, message)
        self.logger = _FakeLogger()

    async def send(self, destination, message, *args, **kwargs):
        self.sent.append((destination, message))


def _invoke(msg: ComplianceRequest, sender: str = SENDER) -> FakeContext:
    """Run the async on_message handler to completion; return the FakeContext."""
    ctx = FakeContext()
    asyncio.run(handle_compliance_request(ctx, sender, msg))
    return ctx


def _req(items, *, mode="AIR", countries=("CN", "US"), origin="CN",
         destination="US", value=2800.0, high_value=True, luxury=False):
    shipment = ShipmentRequest(
        origin={"country": origin, "state": "X", "city": "X"},
        destination={"country": destination, "state": "Y", "city": "Y"},
        items=items,
        total_weight_kg=200.0,
        total_volume_cbm=5.0,
        timeframe="SPEED",
        declared_value_usd=value,
    )
    econ = EconData(
        transport_preference="AIR" if mode == "AIR" else "SHIP",
        is_high_value=high_value, is_luxury=luxury, base_entry_tax_usd=100.0,
    )
    route = RouteData(
        selected_mode=mode, optimal_route_nodes=list(countries),
        countries_visited=list(countries), freight_and_toll_cost_usd=1000.0,
        total_landed_cost_usd=value + 1100.0,
    )
    return ComplianceRequest(shipment=shipment, econ=econ, route=route)


def _item(name="widget", qty=1, category="general"):
    return Item(name=name, quantity=qty, category=category)


# --------------------------------------------------------------------------- #
# Handler behaviour
# --------------------------------------------------------------------------- #
def test_handler_sends_exactly_one_reply():
    ctx = _invoke(_req([_item()]))
    assert len(ctx.sent) == 1


def test_handler_replies_to_the_sender():
    ctx = _invoke(_req([_item()]), sender=SENDER)
    destination, _msg = ctx.sent[0]
    assert destination == SENDER


def test_handler_reply_is_doctemplates():
    _dest, msg = _invoke(_req([_item()])).sent[0]
    assert isinstance(msg, DocTemplates)


def test_handler_matches_pure_logic():
    """Agent layer must not diverge from compute_doc_templates."""
    req = _req([_item("lithium battery", 300, "battery")], mode="AIR")
    _dest, reply = _invoke(req).sent[0]
    assert dump(reply) == dump(compute_doc_templates(req))


def test_handler_air_semis_packet():
    req = _req([_item("semiconductor components", 500, "electronics")],
               mode="AIR", countries=("CN", "HK", "US"), value=2800.0)
    _dest, r = _invoke(req).sent[0]
    assert "Air Waybill (AWB)" in r.required_form_names
    assert "CBP Form 7501 – Entry Summary" in r.required_form_names
    assert set(r.required_form_names) == set(r.blank_form_structures.keys())


def test_handler_ship_hazmat_packet():
    req = _req([_item("lithium battery", 300, "battery")], mode="SHIP")
    _dest, r = _invoke(req).sent[0]
    assert "Bill of Lading (B/L)" in r.required_form_names
    assert "Importer Security Filing (ISF 10+2)" in r.required_form_names
    assert "Multimodal Dangerous Goods Form (IMO IMDG)" in r.required_form_names


# --------------------------------------------------------------------------- #
# Agent wiring / configuration
# --------------------------------------------------------------------------- #
def test_agent_name():
    assert compliance_agent.name == "compliance-document-agent"


def test_agent_address_is_deterministic_from_seed():
    """Stable address lets the orchestrator resolve us from config, no handshake."""
    assert compliance_agent.address == Identity.from_seed(COMPLIANCE_SEED, 0).address
    assert compliance_agent.address.startswith("agent1")


def test_compliancerequest_handler_is_registered():
    proto = compliance_agent._protocol
    cr_digest = Model.build_schema_digest(ComplianceRequest)
    assert cr_digest in proto.signed_message_handlers
    assert proto.models[cr_digest] is ComplianceRequest


def test_doctemplates_is_declared_as_reply():
    proto = compliance_agent._protocol
    cr_digest = Model.build_schema_digest(ComplianceRequest)
    dt_digest = Model.build_schema_digest(DocTemplates)
    assert proto.replies[cr_digest].get(dt_digest) is DocTemplates


# --------------------------------------------------------------------------- #
# Contract / wire-model sanity (regression guard for the v1/v2 Field bug)
# --------------------------------------------------------------------------- #
def test_models_are_uagents_models():
    assert issubclass(ComplianceRequest, Model)
    assert issubclass(DocTemplates, Model)


def test_schema_digests_build_cleanly():
    # If a pydantic-v2 Field leaks into a v1 uagents.Model, this raises
    # "FieldInfo is not JSON serializable" — the bug we want to catch.
    for model in (ComplianceRequest, DocTemplates):
        digest = Model.build_schema_digest(model)
        assert digest.startswith("model:")


# --------------------------------------------------------------------------- #
# No-pytest runner
# --------------------------------------------------------------------------- #
def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {t.__name__}: {exc!r}")
        else:
            passed += 1
            print(f"ok   {t.__name__}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
