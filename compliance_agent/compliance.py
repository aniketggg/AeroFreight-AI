"""Step 4 — Compliance & Document logic (Owner: Aniket).

Pure, framework-agnostic business logic for the Compliance & Document Agent.
It takes the orchestrator's accumulated *Global State* (a
:class:`ComplianceRequest` = Inputs + Ashwin's :class:`EconData` + Riya's
:class:`RouteData`) and produces a :class:`DocTemplates`, exactly per the
AeroFreight workflow spec:

  * Pick the **required form names** for this shipment, keyed off
        - the chosen transport mode      (AIR -> Air Waybill, SHIP -> Bill of Lading)
        - the countries on the route      (origin / trade-agreement certificates)
        - the cargo + value               (CBP entry forms, FDA/TTB/DG declarations)
  * Retrieve the **blank JSON structures** for each of those forms via the
    "browser-based retrieval" layer in :mod:`compliance_agent.retrieval`
    (simulated by default, optional live search/fetch).

This module has NO uAgents dependency so it can be unit-tested and reused
standalone; ``agent.py`` is the thin transport wrapper around it. The form
*selection rules* and blank *skeletons* live here (the analog of Ashwin's duty
table); *how we obtain them* (browser / search) lives in ``retrieval.py``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

# Make the repo-root `shared_models.py` importable no matter the working dir.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared_models import ComplianceRequest, DocTemplates  # noqa: E402

from compliance_agent.retrieval import retrieve_blank_form  # noqa: E402

# --------------------------------------------------------------------------- #
# Tunable constants
# --------------------------------------------------------------------------- #

# CBP requires a *formal* entry (Form 7501 + bond) once the shipment value
# exceeds this threshold; below it an informal entry suffices. This is the same
# $2,500 line Ashwin uses for ``is_high_value`` — kept here so the document set
# agrees with the economic baseline.
FORMAL_ENTRY_THRESHOLD_USD = 2500.0


# --------------------------------------------------------------------------- #
# Cargo keyword groups (drive the hazmat / regulated-goods declarations).
# Mirrors the keyword style of Ashwin's duty table so the two agents classify
# the same cargo consistently.
# --------------------------------------------------------------------------- #
_HAZMAT_KEYWORDS: Tuple[str, ...] = (
    "lithium", "li-ion", "li ion", "battery", "batteries", "accumulator",
    "power bank", "powerbank", "aerosol", "flammable", "explosive", "corrosive",
    "hazardous", "hazmat", "compressed gas", "magnetized",
)
_FDA_KEYWORDS: Tuple[str, ...] = (
    "pharmaceutical", "pharma", "medicine", "medicament", "drug", "vaccine",
    "antibiotic", "insulin", "food", "beverage", "supplement", "dietary",
    "cosmetic", "perfume", "medical device", "perishable", "produce",
)
_ALCOHOL_KEYWORDS: Tuple[str, ...] = (
    "wine", "champagne", "beer", "spirits", "whisky", "whiskey", "vodka",
    "liquor", "alcohol", "rum", "tequila", "brandy", "cognac",
)
_RF_KEYWORDS: Tuple[str, ...] = (
    "phone", "smartphone", "router", "modem", "wireless", "bluetooth", "radio",
    "transmitter", "wifi", "telecom", "drone", "rf module",
)

# Origin countries that ship under USMCA (US-Mexico-Canada Agreement) and so
# use the USMCA certificate of origin instead of a generic one.
_USMCA_ORIGINS: Tuple[str, ...] = ("CA", "MX")


# --------------------------------------------------------------------------- #
# Decision context — the small, flat view of Global State the rules key off.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ComplianceContext:
    mode: str                  # "AIR" | "SHIP"  (Riya's selected_mode)
    origin_country: str        # ISO-ish code, uppercased
    destination_country: str
    countries: Tuple[str, ...]  # countries_visited, uppercased
    declared_value_usd: float
    is_high_value: bool
    is_luxury: bool
    item_text: str             # lowercased "name + category" of all line items

    @property
    def is_formal_entry(self) -> bool:
        return self.declared_value_usd > FORMAL_ENTRY_THRESHOLD_USD

    @property
    def is_foreign_origin(self) -> bool:
        return bool(self.origin_country) and self.origin_country != "US"

    def cargo_matches(self, keywords: Tuple[str, ...]) -> bool:
        return any(kw in self.item_text for kw in keywords)


def build_context(req: ComplianceRequest) -> ComplianceContext:
    """Flatten the nested Global State into the fields the rules need."""
    item_text = " ".join(
        f"{i.name} {i.category}" for i in req.shipment.items
    ).lower()
    countries = tuple(
        str(c).upper() for c in (req.route.countries_visited or [])
    )
    origin = str(req.shipment.origin.get("country", "")).upper()
    destination = str(req.shipment.destination.get("country", "")).upper()
    return ComplianceContext(
        mode=req.route.selected_mode,
        origin_country=origin,
        destination_country=destination,
        countries=countries,
        declared_value_usd=req.shipment.declared_value_usd,
        is_high_value=req.econ.is_high_value,
        is_luxury=req.econ.is_luxury,
        item_text=item_text,
    )


# --------------------------------------------------------------------------- #
# Form catalog — each form is selected by a predicate over ComplianceContext,
# and carries the canonical source URL + the blank JSON skeleton Neel will fill.
# Ordered the way a broker assembles a packet: commercial docs, transport doc,
# advance security filing, CBP entry forms, origin certificates, then any
# regulated-cargo declarations.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FormSpec:
    key: str
    name: str
    agency: str
    source_url: str
    blank_structure: dict
    applies: Callable[[ComplianceContext], bool]


FORM_CATALOG: List[FormSpec] = [
    # --- Universal commercial documents (every cross-border shipment) -------
    FormSpec(
        "commercial_invoice", "Commercial Invoice", "Seller / Exporter",
        "https://www.cbp.gov/trade/basic-import-export",
        {
            "seller": {"name": "", "address": "", "country": ""},
            "buyer": {"name": "", "address": "", "country": ""},
            "invoice_number": "", "invoice_date": "",
            "incoterms": "", "currency": "USD",
            "line_items": [
                {"description": "", "hts_code": "", "quantity": "",
                 "unit_price": "", "total": ""}
            ],
            "total_value": "", "country_of_origin": "",
        },
        applies=lambda c: True,
    ),
    FormSpec(
        "packing_list", "Packing List", "Shipper",
        "https://www.trade.gov/packing-list",
        {
            "shipper": "", "consignee": "",
            "packages": [
                {"marks": "", "description": "", "quantity": "",
                 "net_weight_kg": "", "gross_weight_kg": "", "dimensions_cm": ""}
            ],
            "total_packages": "", "total_gross_weight_kg": "",
            "total_volume_cbm": "",
        },
        applies=lambda c: True,
    ),
    # --- Transport document (mode-specific) --------------------------------
    FormSpec(
        "air_waybill", "Air Waybill (AWB)", "IATA / Carrier",
        "https://www.iata.org/en/programs/cargo/e/awb/",
        {
            "awb_number": "",
            "shipper": {"name": "", "account": "", "address": ""},
            "consignee": {"name": "", "account": "", "address": ""},
            "issuing_carrier": "", "airport_of_departure": "",
            "airport_of_destination": "", "routing": [],
            "number_of_pieces": "", "gross_weight_kg": "",
            "chargeable_weight_kg": "", "nature_and_quantity_of_goods": "",
            "declared_value_for_carriage": "",
            "declared_value_for_customs": "", "currency": "USD",
        },
        applies=lambda c: c.mode == "AIR",
    ),
    FormSpec(
        "bill_of_lading", "Bill of Lading (B/L)", "Carrier / FMC",
        "https://www.fmc.gov/resources-services/",
        {
            "bl_number": "", "booking_number": "",
            "shipper": "", "consignee": "", "notify_party": "",
            "vessel": "", "voyage_number": "",
            "port_of_loading": "", "port_of_discharge": "",
            "place_of_receipt": "", "place_of_delivery": "",
            "containers": [
                {"container_number": "", "seal_number": "", "marks": "",
                 "description": "", "packages": "", "gross_weight_kg": "",
                 "measurement_cbm": ""}
            ],
            "freight_terms": "", "number_of_originals": "",
        },
        applies=lambda c: c.mode == "SHIP",
    ),
    # --- Advance cargo security filing (mode-specific, U.S. import) ---------
    FormSpec(
        "isf_10_2", "Importer Security Filing (ISF 10+2)", "U.S. CBP",
        "https://www.cbp.gov/border-security/ports-entry/cargo-security/"
        "importer-security-filing-102",
        {
            "importer_of_record_number": "", "consignee_number": "",
            "seller": "", "buyer": "", "ship_to_party": "",
            "manufacturer_supplier": "", "country_of_origin": "",
            "hts_number": "", "container_stuffing_location": "",
            "consolidator": "", "booking_number": "",
            "bill_of_lading_number": "",
        },
        applies=lambda c: c.mode == "SHIP" and c.destination_country == "US",
    ),
    FormSpec(
        "acas", "Air Cargo Advance Screening (ACAS)", "U.S. CBP",
        "https://www.cbp.gov/border-security/ports-entry/cargo-security/acas",
        {
            "awb_number": "", "shipper_name_address": "",
            "consignee_name_address": "", "cargo_description": "",
            "total_pieces": "", "total_weight_kg": "",
            "origin_airport": "", "destination_airport": "",
        },
        applies=lambda c: c.mode == "AIR" and c.destination_country == "US",
    ),
    # --- CBP entry forms (formal entry: value > $2,500) --------------------
    FormSpec(
        "cbp_3461", "CBP Form 3461 – Entry/Immediate Delivery", "U.S. CBP",
        "https://www.cbp.gov/document/forms/form-3461-entryimmediate-delivery",
        {
            "entry_number": "", "port_code": "", "entry_type": "",
            "importer_of_record": "", "consignee": "", "carrier_code": "",
            "bill_of_lading_or_awb": "", "manifest_quantity": "",
            "country_of_origin": "", "description_of_merchandise": "",
            "hts_number": "",
        },
        applies=lambda c: c.is_formal_entry and c.destination_country == "US",
    ),
    FormSpec(
        "cbp_7501", "CBP Form 7501 – Entry Summary", "U.S. CBP",
        "https://www.cbp.gov/document/forms/form-7501-entry-summary",
        {
            "entry_number": "", "entry_type": "", "summary_date": "",
            "port_code": "", "entry_date": "",
            "importer_of_record": {"name": "", "number": ""},
            "consignee": {"name": "", "number": ""},
            "country_of_origin": "", "importing_carrier": "",
            "mode_of_transport": "",
            "line_items": [
                {"description": "", "hts_number": "", "gross_weight_kg": "",
                 "entered_value_usd": "", "duty_rate": "", "duty_usd": ""}
            ],
            "total_entered_value_usd": "", "mpf_usd": "", "hmf_usd": "",
            "total_duties_taxes_fees_usd": "",
        },
        applies=lambda c: c.is_formal_entry and c.destination_country == "US",
    ),
    FormSpec(
        "cbp_301", "CBP Form 301 – Customs Bond", "U.S. CBP",
        "https://www.cbp.gov/document/forms/form-301-customs-bond",
        {
            "bond_number": "", "bond_type": "", "activity_code": "",
            "principal": {"name": "", "address": "", "importer_number": ""},
            "surety": {"name": "", "code": ""},
            "bond_amount_usd": "", "effective_date": "",
        },
        applies=lambda c: c.is_formal_entry and c.destination_country == "US",
    ),
    # --- Origin certificates (route / trade-agreement driven) --------------
    FormSpec(
        "usmca_cert", "USMCA Certificate of Origin", "U.S. CBP / Trade",
        "https://www.cbp.gov/trade/priority-issues/trade-agreements/"
        "free-trade-agreements/USMCA",
        {
            "certifier_type": "",
            "certifier": {"name": "", "address": "", "country": ""},
            "exporter": "", "producer": "", "importer": "",
            "goods": [
                {"description": "", "hts_classification": "",
                 "origin_criterion": ""}
            ],
            "blanket_period": {"from": "", "to": ""},
            "authorized_signature": "", "date": "",
        },
        applies=lambda c: c.origin_country in _USMCA_ORIGINS,
    ),
    FormSpec(
        "certificate_of_origin", "Certificate of Origin", "Chamber of Commerce",
        "https://www.trade.gov/certificate-origin",
        {
            "exporter": "", "producer": "", "importer": "",
            "description_of_goods": "", "hts_number": "",
            "country_of_origin": "", "transport_details": "",
            "certifying_authority": "", "signature": "", "date": "",
        },
        # Generic CofO when goods are foreign-origin but NOT covered by USMCA
        # (USMCA shipments use the dedicated certificate above instead).
        applies=lambda c: c.is_foreign_origin
        and c.origin_country not in _USMCA_ORIGINS,
    ),
    # --- Regulated-cargo declarations (keyword driven) ---------------------
    FormSpec(
        "dgd_air", "Shipper's Declaration for Dangerous Goods (IATA)",
        "IATA",
        "https://www.iata.org/en/programs/cargo/dgr/",
        {
            "shipper": "", "consignee": "", "awb_number": "",
            "aircraft_limitation": "",
            "dangerous_goods": [
                {"un_number": "", "proper_shipping_name": "",
                 "class_or_division": "", "packing_group": "",
                 "quantity_and_type_of_packing": "", "packing_instruction": ""}
            ],
            "additional_handling_information": "", "signatory": "",
            "place_and_date": "",
        },
        applies=lambda c: c.mode == "AIR" and c.cargo_matches(_HAZMAT_KEYWORDS),
    ),
    FormSpec(
        "dgd_sea", "Multimodal Dangerous Goods Form (IMO IMDG)", "IMO",
        "https://www.imo.org/en/OurWork/Safety/Pages/DangerousGoods-default.aspx",
        {
            "shipper": "", "consignee": "", "booking_number": "", "carrier": "",
            "dangerous_goods": [
                {"un_number": "", "proper_shipping_name": "", "imdg_class": "",
                 "packing_group": "", "marine_pollutant": "", "flashpoint_c": "",
                 "number_and_kind_of_packages": "", "net_mass_kg": ""}
            ],
            "container_number": "", "emergency_contact": "", "signatory": "",
            "date": "",
        },
        applies=lambda c: c.mode == "SHIP" and c.cargo_matches(_HAZMAT_KEYWORDS),
    ),
    FormSpec(
        "fda_prior_notice", "FDA Prior Notice", "U.S. FDA",
        "https://www.fda.gov/food/importing-food-products-united-states/"
        "prior-notice-imported-foods",
        {
            "submission_type": "", "prior_notice_confirmation_number": "",
            "submitter": "", "transmitter": "",
            "product": {"fda_product_code": "", "trade_name": "",
                        "quantity": "", "manufacturer": "", "grower": ""},
            "country_of_production": "", "country_of_shipment": "",
            "arrival": {"port": "", "estimated_date": "", "estimated_time": ""},
        },
        applies=lambda c: c.cargo_matches(_FDA_KEYWORDS),
    ),
    FormSpec(
        "ttb_permit", "TTB Import Permit (Alcohol)", "U.S. TTB",
        "https://www.ttb.gov/importers",
        {
            "permit_number": "", "importer_name": "", "importer_address": "",
            "product": {"class_type": "", "brand_name": "",
                        "alcohol_content": "", "net_contents": "",
                        "quantity": ""},
            "country_of_origin": "", "foreign_producer": "", "cola_id": "",
        },
        applies=lambda c: c.cargo_matches(_ALCOHOL_KEYWORDS),
    ),
    FormSpec(
        "fcc_740", "FCC Form 740 (RF Device Declaration)", "U.S. FCC",
        "https://www.fcc.gov/general/equipment-authorization",
        {
            "importer_name": "", "importer_address": "",
            "device": {"description": "", "model_number": "", "fcc_id": "",
                       "quantity": ""},
            "import_condition": "", "port_of_entry": "", "date_of_entry": "",
        },
        applies=lambda c: c.destination_country == "US"
        and c.cargo_matches(_RF_KEYWORDS),
    ),
]


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def select_forms(ctx: ComplianceContext) -> List[FormSpec]:
    """Return the catalog entries whose predicate matches this shipment."""
    return [form for form in FORM_CATALOG if form.applies(ctx)]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def compute_doc_templates(
    req: ComplianceRequest, *, live: bool = None, logger=None
) -> DocTemplates:
    """Run the full Step-4 retrieval: ComplianceRequest -> DocTemplates.

    For each applicable form we call the browser-based retrieval layer to fetch
    the blank JSON structure (simulated by default; live search/fetch when
    ``live=True`` or ``AEROFREIGHT_COMPLIANCE_LIVE=true``). The returned
    structures are fresh copies, so Neel (Step 5) can fill them in place.
    """
    ctx = build_context(req)
    forms = select_forms(ctx)
    required_form_names: List[str] = [form.name for form in forms]
    blank_form_structures: Dict[str, dict] = {
        form.name: retrieve_blank_form(form, live=live, logger=logger)
        for form in forms
    }
    return DocTemplates(
        required_form_names=required_form_names,
        blank_form_structures=blank_form_structures,
    )


def explain(req: ComplianceRequest) -> dict:
    """Verbose, JSON-serializable view of the decision — handy for logs/UI."""
    ctx = build_context(req)
    forms = select_forms(ctx)
    return {
        "mode": ctx.mode,
        "origin_country": ctx.origin_country,
        "destination_country": ctx.destination_country,
        "countries_visited": list(ctx.countries),
        "is_formal_entry": ctx.is_formal_entry,
        "is_high_value": ctx.is_high_value,
        "is_luxury": ctx.is_luxury,
        "required_form_names": [f.name for f in forms],
        "form_sources": {f.name: f.source_url for f in forms},
        "form_agencies": {f.name: f.agency for f in forms},
    }
