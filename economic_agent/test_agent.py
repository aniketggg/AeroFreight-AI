"""Tests for the uAgents layer of Step 2 (Ashwin) — `economic_agent/agent.py`.

These complement `test_economics.py` (which tests the pure math). Here we test
the *agent* wire protocol:

  * Pydantic v2 business models serialize through uAgents wire wrappers
  * the handler replies with EconomistResponse or EconomistError
  * the agent is wired correctly with deterministic address and handlers

Runnable two ways:
    pytest economic_agent/test_agent.py
    python -m economic_agent.test_agent      # no pytest needed
"""

import asyncio
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from uagents import Model  # noqa: E402
from uagents.crypto import Identity  # noqa: E402

from economic_agent.agent import (  # noqa: E402
    ECONOMIC_SEED,
    economic_agent,
    handle_shipment_request,
)
from economic_agent.economics import compute_econ_data  # noqa: E402
from economic_agent.messages import (  # noqa: E402
    EconomistError,
    EconomistRequest,
    EconomistResponse,
)
from shared_models import EconData, Item, ShipmentRequest  # noqa: E402

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


def _req(items, weight, timeframe, value, volume=5.0):
    return ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=items,
        total_weight_kg=weight,
        total_volume_cbm=volume,
        timeframe=timeframe,
        declared_value_usd=value,
    )


def _item(name="widget", qty=1, category="general"):
    return Item(name=name, quantity=qty, category=category)


def _wire_request(shipment: ShipmentRequest) -> EconomistRequest:
    return EconomistRequest(shipment_json=shipment.model_dump_json())


def _invoke(msg: ShipmentRequest, sender: str = SENDER) -> FakeContext:
    """Run the async on_message handler to completion; return the FakeContext."""
    ctx = FakeContext()
    asyncio.run(handle_shipment_request(ctx, sender, _wire_request(msg)))
    return ctx


def _parse_econ_response(ctx: FakeContext) -> EconData:
    _dest, msg = ctx.sent[0]
    assert isinstance(msg, EconomistResponse)
    return EconData.model_validate_json(msg.econ_data_json)


# --------------------------------------------------------------------------- #
# Wire model types
# --------------------------------------------------------------------------- #
def test_economist_request_is_uagents_model():
    assert issubclass(EconomistRequest, Model)


def test_economist_response_is_uagents_model():
    assert issubclass(EconomistResponse, Model)


def test_economist_error_is_uagents_model():
    assert issubclass(EconomistError, Model)


def test_shipment_request_serializes_to_economist_request():
    shipment = _req([_item()], 100, "COST", 1000.0)
    wire = EconomistRequest(shipment_json=shipment.model_dump_json())
    restored = ShipmentRequest.model_validate_json(wire.shipment_json)
    assert restored == shipment


def test_econ_data_serializes_to_economist_response():
    req = _req([_item("cotton shirts", 8000, "apparel")], 4200, "COST", 60000)
    econ = compute_econ_data(req)
    wire = EconomistResponse(econ_data_json=econ.model_dump_json())
    restored = EconData.model_validate_json(wire.econ_data_json)
    assert restored == econ


def test_invalid_shipment_json_returns_error_response():
    ctx = FakeContext()
    bad = EconomistRequest(shipment_json='{"not": "a shipment"}')
    asyncio.run(handle_shipment_request(ctx, SENDER, bad))
    assert len(ctx.sent) == 1
    _dest, msg = ctx.sent[0]
    assert isinstance(msg, EconomistError)
    assert msg.error_message == (
        "The Economist agent could not process the shipment request."
    )
    assert "stack" not in msg.error_message.lower()
    assert "secret" not in msg.error_message.lower()


# --------------------------------------------------------------------------- #
# Handler behaviour
# --------------------------------------------------------------------------- #
def test_handler_sends_exactly_one_reply():
    ctx = _invoke(_req([_item()], 100, "COST", 1000.0))
    assert len(ctx.sent) == 1


def test_handler_replies_to_the_sender():
    ctx = _invoke(_req([_item()], 100, "COST", 1000.0), sender=SENDER)
    destination, _msg = ctx.sent[0]
    assert destination == SENDER


def test_handler_reply_is_economist_response():
    ctx = _invoke(_req([_item()], 100, "COST", 1000.0))
    _dest, msg = ctx.sent[0]
    assert isinstance(msg, EconomistResponse)


def test_handler_matches_pure_logic():
    """Agent layer must not diverge from compute_econ_data."""
    req = _req([_item("cotton shirts", 8000, "apparel")], 4200, "COST", 60000)
    reply = _parse_econ_response(_invoke(req))
    assert reply.model_dump() == compute_econ_data(req).model_dump()


def test_handler_air_light_speed_semis():
    req = _req([_item("semiconductor components", 500, "electronics")],
               200, "SPEED", 2800)
    r = _parse_econ_response(_invoke(req))
    assert r.transport_preference == "AIR"
    assert r.is_high_value is True
    assert r.is_luxury is False
    assert r.base_entry_tax_usd == 32.71   # MPF floor; semis duty-free


def test_handler_ship_heavy_cost_apparel():
    req = _req([_item("cotton t-shirts", 8000, "apparel")], 4200, "COST", 60000)
    r = _parse_econ_response(_invoke(req))
    assert r.transport_preference == "SHIP"
    assert r.base_entry_tax_usd == 10107.84  # 207.84 MPF + 9900 duty (16.5%)


def test_handler_either_in_middle_band():
    req = _req([_item()], 1500, "COST", 1000.0)
    r = _parse_econ_response(_invoke(req))
    assert r.transport_preference == "EITHER"
    assert r.is_high_value is False


def test_handler_luxury_forces_air():
    req = _req([_item("gold necklaces", 50, "jewelry")], 1200, "COST", 400000)
    r = _parse_econ_response(_invoke(req))
    assert r.is_luxury is True
    assert r.transport_preference == "AIR"


# --------------------------------------------------------------------------- #
# Agent wiring / configuration
# --------------------------------------------------------------------------- #
def test_agent_name():
    assert economic_agent.name == "economic-constraints-agent"


def test_agent_address_is_deterministic_from_seed():
    """Stable address lets the orchestrator resolve us from config, no handshake."""
    assert economic_agent.address == Identity.from_seed(ECONOMIC_SEED, 0).address
    assert economic_agent.address.startswith("agent1")


def test_economist_request_handler_is_registered():
    proto = economic_agent._protocol
    req_digest = Model.build_schema_digest(EconomistRequest)
    assert req_digest in proto.signed_message_handlers
    assert proto.models[req_digest] is EconomistRequest


def test_economist_response_and_error_are_declared_as_replies():
    proto = economic_agent._protocol
    req_digest = Model.build_schema_digest(EconomistRequest)
    resp_digest = Model.build_schema_digest(EconomistResponse)
    err_digest = Model.build_schema_digest(EconomistError)
    assert proto.replies[req_digest].get(resp_digest) is EconomistResponse
    assert proto.replies[req_digest].get(err_digest) is EconomistError


def test_wire_model_schema_digests_build_cleanly():
    for model in (EconomistRequest, EconomistResponse, EconomistError):
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
