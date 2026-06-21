"""Tests for Step 2 (Ashwin) economics logic.

Runnable two ways:
    pytest economic_agent/test_economics.py
    python -m economic_agent.test_economics      # no pytest needed
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from economic_agent.economics import (  # noqa: E402
    DEFAULT_DUTY_RATE_PCT,
    INFORMAL_MPF_USD,
    MPF_MAX_USD,
    MPF_MIN_USD,
    classify_item_duty,
    compute_econ_data,
    compute_entry_tax,
    decide_transport,
    effective_duty,
    is_luxury_shipment,
    merchandise_processing_fee,
)
from shared_models import EconData, Item, ShipmentRequest  # noqa: E402


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


# --------------------------------------------------------------------------- #
# Transport-mode constraint (X = 500 kg, Y = 2000 kg)
# --------------------------------------------------------------------------- #
def test_transport_air_when_light():
    assert decide_transport(500.0, "COST", False) == "AIR"      # weight <= 500
    assert decide_transport(10.0, "COST", False) == "AIR"


def test_transport_either_in_middle_band():
    assert decide_transport(500.01, "COST", False) == "EITHER"  # just over X
    assert decide_transport(2000.0, "COST", False) == "EITHER"  # exactly Y
    assert decide_transport(1500.0, "COST", False) == "EITHER"


def test_transport_ship_when_heavy_and_cost():
    assert decide_transport(2000.01, "COST", False) == "SHIP"   # just over Y
    assert decide_transport(5000.0, "COST", False) == "SHIP"


def test_speed_forces_air_even_when_heavy():
    assert decide_transport(5000.0, "SPEED", False) == "AIR"
    assert decide_transport(1500.0, "SPEED", False) == "AIR"


def test_luxury_forces_air_even_when_heavy_and_cost():
    assert decide_transport(5000.0, "COST", True) == "AIR"


# --------------------------------------------------------------------------- #
# High-value classification (> $2,500)
# --------------------------------------------------------------------------- #
def test_high_value_threshold_is_strict():
    assert compute_econ_data(_req([_item()], 100, "COST", 2500.0)).is_high_value is False
    assert compute_econ_data(_req([_item()], 100, "COST", 2500.01)).is_high_value is True


# --------------------------------------------------------------------------- #
# Luxury detection
# --------------------------------------------------------------------------- #
def test_luxury_by_keyword():
    assert is_luxury_shipment([_item("Gold necklace", 1, "jewelry")], 1000.0) is True
    assert is_luxury_shipment([_item("Rolex watch", 1, "accessory")], 1000.0) is True


def test_luxury_by_high_per_unit_value():
    # 2 units, $20k total -> $10k/unit -> luxury by value heuristic.
    assert is_luxury_shipment([_item("server", 2, "electronics")], 20000.0) is True
    # 100 units, $20k total -> $200/unit -> not luxury.
    assert is_luxury_shipment([_item("server", 100, "electronics")], 20000.0) is False


# --------------------------------------------------------------------------- #
# Duty classification
# --------------------------------------------------------------------------- #
def test_duty_by_category():
    assert classify_item_duty(_item("microchips", 1, "semiconductor"))[1] == 0.0
    assert classify_item_duty(_item("cotton t-shirts", 1, "apparel"))[1] == 16.5
    assert classify_item_duty(_item("lithium battery pack", 1, "battery"))[1] == 3.4


def test_duty_default_when_unknown():
    assert classify_item_duty(_item("mystery good", 1, "misc"))[1] == DEFAULT_DUTY_RATE_PCT


def test_effective_duty_takes_max_across_items():
    items = [_item("microchips", 1, "semiconductor"),   # 0.0%
             _item("cotton shirts", 1, "apparel")]      # 16.5%
    label, rate = effective_duty(items)
    assert rate == 16.5
    assert "Apparel" in label


# --------------------------------------------------------------------------- #
# Merchandise Processing Fee
# --------------------------------------------------------------------------- #
def test_mpf_informal_flat_fee():
    assert merchandise_processing_fee(1000.0) == INFORMAL_MPF_USD
    assert merchandise_processing_fee(2500.0) == INFORMAL_MPF_USD


def test_mpf_formal_min_clamp():
    # 0.3464% of $3,000 = $10.39, below the floor -> clamped to min.
    assert merchandise_processing_fee(3000.0) == MPF_MIN_USD


def test_mpf_formal_midrange():
    # 0.3464% of $100,000 = $346.40, between min and max.
    assert merchandise_processing_fee(100000.0) == 346.40


def test_mpf_formal_max_clamp():
    # 0.3464% of $1,000,000 = $3,464, above the cap -> clamped to max.
    assert merchandise_processing_fee(1000000.0) == MPF_MAX_USD


# --------------------------------------------------------------------------- #
# Entry tax = MPF + duty
# --------------------------------------------------------------------------- #
def test_entry_tax_combines_mpf_and_duty():
    # $50,000 of cotton apparel (16.5%): MPF = 0.3464% * 50,000 = $173.20,
    # duty = 16.5% * 50,000 = $8,250 -> total $8,423.20.
    total, breakdown = compute_entry_tax([_item("cotton shirts", 1, "apparel")], 50000.0)
    assert breakdown["merchandise_processing_fee_usd"] == 173.20
    assert breakdown["duty_usd"] == 8250.0
    assert total == 8423.20


# --------------------------------------------------------------------------- #
# End-to-end: the spec's canonical Shenzhen -> Austin example
# --------------------------------------------------------------------------- #
def test_end_to_end_semiconductors():
    req = _req(
        items=[_item("semiconductor components", 500, "electronics")],
        weight=200.0, timeframe="SPEED", value=2800.0,
    )
    econ = compute_econ_data(req)
    assert isinstance(econ, EconData)
    assert econ.transport_preference == "AIR"   # light + SPEED
    assert econ.is_high_value is True           # $2,800 > $2,500
    assert econ.is_luxury is False
    # Semiconductors duty-free (0%); informal-vs-formal: $2,800 > $2,500 ->
    # formal MPF clamped to the floor; duty $0 -> entry tax == MPF floor.
    assert econ.base_entry_tax_usd == MPF_MIN_USD


def test_returns_valid_econdata_literals():
    for tf in ("SPEED", "COST"):
        econ = compute_econ_data(_req([_item()], 1500, tf, 1000.0))
        assert econ.transport_preference in ("AIR", "SHIP", "EITHER")


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
