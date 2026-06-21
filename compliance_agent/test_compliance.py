"""Tests for Step 4 (Aniket) compliance logic.

Runnable two ways:
    pytest compliance_agent/test_compliance.py
    python -m compliance_agent.test_compliance      # no pytest needed
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from compliance_agent.compliance import (  # noqa: E402
    FORM_CATALOG,
    build_context,
    compute_doc_templates,
    explain,
)
from compliance_agent.retrieval import retrieve_blank_form, search_form_source  # noqa: E402
from shared_models import (  # noqa: E402
    ComplianceRequest,
    DocTemplates,
    EconData,
    Item,
    RouteData,
    ShipmentRequest,
)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _req(
    items,
    *,
    mode="AIR",
    countries=("CN", "US"),
    origin="CN",
    destination="US",
    value=2800.0,
    high_value=True,
    luxury=False,
    weight=200.0,
):
    shipment = ShipmentRequest(
        origin={"country": origin, "state": "X", "city": "X"},
        destination={"country": destination, "state": "Y", "city": "Y"},
        items=items,
        total_weight_kg=weight,
        total_volume_cbm=5.0,
        timeframe="SPEED",
        declared_value_usd=value,
    )
    econ = EconData(
        transport_preference=mode if mode in ("AIR", "SHIP") else "EITHER",
        is_high_value=high_value,
        is_luxury=luxury,
        base_entry_tax_usd=100.0,
    )
    route = RouteData(
        selected_mode=mode,
        optimal_route_nodes=list(countries),
        countries_visited=list(countries),
        freight_and_toll_cost_usd=1000.0,
        total_landed_cost_usd=value + 1100.0,
    )
    return ComplianceRequest(shipment=shipment, econ=econ, route=route)


def _item(name="widget", qty=1, category="general"):
    return Item(name=name, quantity=qty, category=category)


def _names(req) -> list:
    return compute_doc_templates(req).required_form_names


# --------------------------------------------------------------------------- #
# Universal documents
# --------------------------------------------------------------------------- #
def test_commercial_invoice_and_packing_list_always_present():
    for mode in ("AIR", "SHIP"):
        names = _names(_req([_item()], mode=mode))
        assert "Commercial Invoice" in names
        assert "Packing List" in names


# --------------------------------------------------------------------------- #
# Transport document is mode-specific
# --------------------------------------------------------------------------- #
def test_air_gets_air_waybill_not_bill_of_lading():
    names = _names(_req([_item()], mode="AIR"))
    assert "Air Waybill (AWB)" in names
    assert "Bill of Lading (B/L)" not in names


def test_ship_gets_bill_of_lading_not_air_waybill():
    names = _names(_req([_item()], mode="SHIP"))
    assert "Bill of Lading (B/L)" in names
    assert "Air Waybill (AWB)" not in names


# --------------------------------------------------------------------------- #
# Advance security filing is mode-specific (U.S. import)
# --------------------------------------------------------------------------- #
def test_ship_gets_isf_air_gets_acas():
    air = _names(_req([_item()], mode="AIR"))
    ship = _names(_req([_item()], mode="SHIP"))
    assert "Air Cargo Advance Screening (ACAS)" in air
    assert "Importer Security Filing (ISF 10+2)" not in air
    assert "Importer Security Filing (ISF 10+2)" in ship
    assert "Air Cargo Advance Screening (ACAS)" not in ship


def test_no_us_security_filing_for_non_us_destination():
    names = _names(_req([_item()], mode="SHIP", destination="DE", countries=("CN", "DE")))
    assert "Importer Security Filing (ISF 10+2)" not in names


# --------------------------------------------------------------------------- #
# CBP entry forms gate on formal entry ($2,500)
# --------------------------------------------------------------------------- #
def test_formal_entry_includes_cbp_entry_forms():
    names = _names(_req([_item()], value=2800.0))   # > $2,500 -> formal
    assert "CBP Form 7501 – Entry Summary" in names
    assert "CBP Form 3461 – Entry/Immediate Delivery" in names
    assert "CBP Form 301 – Customs Bond" in names


def test_informal_entry_omits_cbp_entry_forms():
    names = _names(_req([_item()], value=2000.0))   # <= $2,500 -> informal
    assert "CBP Form 7501 – Entry Summary" not in names
    assert "CBP Form 301 – Customs Bond" not in names
    # but the basic commercial docs are still there
    assert "Commercial Invoice" in names


def test_formal_entry_threshold_is_strict():
    assert "CBP Form 7501 – Entry Summary" not in _names(_req([_item()], value=2500.0))
    assert "CBP Form 7501 – Entry Summary" in _names(_req([_item()], value=2500.01))


# --------------------------------------------------------------------------- #
# Origin certificates (route / trade-agreement driven)
# --------------------------------------------------------------------------- #
def test_foreign_origin_gets_generic_certificate_of_origin():
    names = _names(_req([_item()], origin="CN", countries=("CN", "US")))
    assert "Certificate of Origin" in names
    assert "USMCA Certificate of Origin" not in names


def test_usmca_origin_gets_usmca_cert_instead_of_generic():
    for origin in ("MX", "CA"):
        names = _names(_req([_item()], origin=origin, countries=(origin, "US")))
        assert "USMCA Certificate of Origin" in names
        assert "Certificate of Origin" not in names


def test_us_origin_gets_no_origin_certificate():
    names = _names(_req([_item()], origin="US", countries=("US",)))
    assert "Certificate of Origin" not in names
    assert "USMCA Certificate of Origin" not in names


# --------------------------------------------------------------------------- #
# Regulated-cargo declarations (keyword driven)
# --------------------------------------------------------------------------- #
def test_lithium_battery_by_air_needs_iata_dgd():
    names = _names(_req([_item("lithium-ion battery pack", 1, "battery")], mode="AIR"))
    assert "Shipper's Declaration for Dangerous Goods (IATA)" in names
    assert "Multimodal Dangerous Goods Form (IMO IMDG)" not in names


def test_lithium_battery_by_sea_needs_imdg_form():
    names = _names(_req([_item("lithium battery", 1, "battery")], mode="SHIP"))
    assert "Multimodal Dangerous Goods Form (IMO IMDG)" in names
    assert "Shipper's Declaration for Dangerous Goods (IATA)" not in names


def test_non_hazmat_cargo_has_no_dangerous_goods_form():
    names = _names(_req([_item("cotton t-shirts", 1, "apparel")], mode="AIR"))
    assert "Shipper's Declaration for Dangerous Goods (IATA)" not in names


def test_food_pharma_triggers_fda_prior_notice():
    assert "FDA Prior Notice" in _names(_req([_item("frozen food", 1, "food")]))
    assert "FDA Prior Notice" in _names(_req([_item("insulin vials", 1, "pharmaceutical")]))


def test_alcohol_triggers_ttb_permit():
    assert "TTB Import Permit (Alcohol)" in _names(_req([_item("red wine", 1, "alcohol")]))


def test_rf_device_triggers_fcc_740():
    names = _names(_req([_item("5G smartphone", 1, "electronics")], destination="US"))
    assert "FCC Form 740 (RF Device Declaration)" in names


# --------------------------------------------------------------------------- #
# Output shape / DocTemplates contract
# --------------------------------------------------------------------------- #
def test_returns_doctemplates_with_aligned_keys():
    req = _req([_item("lithium battery", 1, "battery")], mode="AIR")
    docs = compute_doc_templates(req)
    assert isinstance(docs, DocTemplates)
    # every required form has a blank structure, and vice-versa
    assert set(docs.required_form_names) == set(docs.blank_form_structures.keys())
    assert len(docs.required_form_names) == len(docs.blank_form_structures)


def test_blank_structures_are_actually_blank():
    """Every leaf in a blank skeleton is empty/zero — nothing pre-filled."""
    docs = compute_doc_templates(_req([_item()], mode="AIR"))

    def _all_blank(value):
        if isinstance(value, dict):
            return all(_all_blank(v) for v in value.values())
        if isinstance(value, list):
            return all(_all_blank(v) for v in value)
        # "USD" currency default is an allowed non-empty constant.
        return value in ("", "USD")

    for structure in docs.blank_form_structures.values():
        assert _all_blank(structure)


def test_retrieval_returns_independent_copies():
    """Mutating a returned skeleton must not corrupt the shared catalog."""
    req = _req([_item()], mode="AIR")
    first = compute_doc_templates(req).blank_form_structures["Commercial Invoice"]
    first["seller"]["name"] = "ACME Corp"     # mutate the copy
    second = compute_doc_templates(req).blank_form_structures["Commercial Invoice"]
    assert second["seller"]["name"] == ""     # fresh copy, unaffected


def test_form_order_follows_catalog():
    """Required-form ordering is deterministic (broker packet order)."""
    req = _req([_item("lithium battery", 1, "battery")], mode="AIR")
    names = compute_doc_templates(req).required_form_names
    catalog_order = [f.name for f in FORM_CATALOG]
    positions = [catalog_order.index(n) for n in names]
    assert positions == sorted(positions)


# --------------------------------------------------------------------------- #
# Context + explain + simulated retrieval helpers
# --------------------------------------------------------------------------- #
def test_build_context_uppercases_and_flattens():
    req = _req([_item("Gold Watch", 1, "Jewelry")], origin="mx", countries=("mx", "us"))
    ctx = build_context(req)
    assert ctx.origin_country == "MX"
    assert ctx.countries == ("MX", "US")
    assert "gold watch" in ctx.item_text   # lowercased


def test_explain_lists_sources_for_every_required_form():
    req = _req([_item("lithium battery", 1, "battery")], mode="AIR")
    info = explain(req)
    assert set(info["form_sources"].keys()) == set(info["required_form_names"])
    assert all(url.startswith("http") for url in info["form_sources"].values())


def test_simulated_search_returns_catalog_url_offline():
    spec = FORM_CATALOG[0]
    # live=False must never touch the network and returns the curated URL.
    assert search_form_source(spec.name, spec.source_url, live=False) == spec.source_url


def test_retrieve_blank_form_deep_copies():
    spec = next(f for f in FORM_CATALOG if f.key == "commercial_invoice")
    blank = retrieve_blank_form(spec, live=False)
    blank["seller"]["name"] = "x"
    assert spec.blank_structure["seller"]["name"] == ""   # catalog untouched


# --------------------------------------------------------------------------- #
# End-to-end: the spec's canonical Shenzhen -> Austin example
# --------------------------------------------------------------------------- #
def test_end_to_end_semiconductors_air():
    req = _req(
        [_item("semiconductor components", 500, "electronics")],
        mode="AIR", countries=("CN", "HK", "US"), value=2800.0,
    )
    docs = compute_doc_templates(req)
    names = docs.required_form_names
    assert "Air Waybill (AWB)" in names          # AIR transport doc
    assert "CBP Form 7501 – Entry Summary" in names  # formal entry > $2,500
    assert "Certificate of Origin" in names      # foreign (CN) origin
    assert "Bill of Lading (B/L)" not in names   # not an ocean shipment


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
