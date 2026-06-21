"""Step 2 — Economic & Constraints logic (Owner: Ashwin).

Pure, framework-agnostic business logic for the Economic & Constraints Agent.
It takes the orchestrator's :class:`ShipmentRequest` and produces an
:class:`EconData`, exactly per the AeroFreight workflow spec:

  * High-value classification        -> ``is_high_value``
  * Luxury classification            -> ``is_luxury``
  * Transport-mode constraint        -> ``transport_preference`` (AIR/SHIP/EITHER)
  * U.S. entry tax (MPF + tariffs)   -> ``base_entry_tax_usd``

This module has NO uAgents dependency so it can be unit-tested and reused
standalone; ``agent.py`` is the thin transport wrapper around it.
"""

from __future__ import annotations

import os
import sys
from typing import List, Tuple

# Make the repo-root `shared_models.py` importable no matter the working dir.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared_models import EconData, Item, ShipmentRequest  # noqa: E402

# --------------------------------------------------------------------------- #
# Tunable business constants (single source of truth for the spec's numbers)
# --------------------------------------------------------------------------- #

# A shipment is "high value" above this declared value (spec: > $2,500).
HIGH_VALUE_THRESHOLD_USD = 2500.0

# Transport-mode weight bands (spec: X = 500 kg, Y = 2000 kg).
AIR_ONLY_MAX_KG = 500.0       # weight <= X            -> AIR only
SHIP_PREFERRED_MIN_KG = 2000.0  # weight > Y (+ COST)   -> SHIP only
# In between (X < weight <= Y) -> EITHER, so Riya's router can compare.

# --- U.S. Merchandise Processing Fee (MPF), CBP/COBRA FY2025 figures. -------
# These are set by CBP and change every fiscal year — update annually.
# https://www.cbp.gov/trade/trade-community/programs-outreach/cobra
MPF_AD_VALOREM_RATE = 0.003464   # 0.3464% of declared value (formal entries)
MPF_MIN_USD = 32.71              # floor for formal entries (FY2025)
MPF_MAX_USD = 634.62             # cap for formal entries (FY2025)
INFORMAL_MPF_USD = 2.62          # flat fee for informal entries (<= $2,500)
# CBP requires a formal entry once the shipment value exceeds this threshold.
FORMAL_ENTRY_THRESHOLD_USD = 2500.0

# A shipment is treated as luxury (for routing/security) if per-unit declared
# value is very high, even when no luxury keyword matches.
LUXURY_PER_UNIT_USD = 5000.0

# Fallback ad-valorem duty when no category keyword matches ("general rate").
DEFAULT_DUTY_RATE_PCT = 3.0
DEFAULT_DUTY_LABEL = "General merchandise (default rate)"


# --------------------------------------------------------------------------- #
# Category -> ad-valorem duty table.
#
# Each rule: (keywords, HS label, General/MFN ad-valorem rate %). Mirrors the
# swarm's tariff classifier headings so Ashwin's baseline agrees with the live
# USITC HTS lookups used downstream. Ordered narrow -> broad: component groups
# (chips, batteries) precede the broad "electronics" group so they win first.
# --------------------------------------------------------------------------- #
_DUTY_RULES: List[Tuple[Tuple[str, ...], str, float]] = [
    (("semiconductor", "chip", "microchip", "integrated circuit", "wafer",
      "transistor", "diode", "led", "photovoltaic", "solar cell"),
     "HS 8541 Semiconductor devices", 0.0),
    (("lithium", "li-ion", "battery", "batteries", "accumulator",
      "power bank", "powerbank"),
     "HS 8507.60 Electric storage batteries", 3.4),
    (("pharmaceutical", "pharma", "medicine", "medicament", "drug",
      "vaccine", "antibiotic", "insulin"),
     "HS 3004 Medicaments", 0.0),
    (("jewelry", "jewellery", "diamond", "gemstone", "gold ", "necklace",
      "bracelet", "watch", "rolex"),
     "HS 7113 Articles of jewelry", 5.5),
    (("t-shirt", "tshirt", "shirt", "apparel", "garment", "clothing",
      "clothes", "textile", "knitwear", "cotton"),
     "HS 6109 Apparel / knitted garments", 16.5),
    (("footwear", "shoe", "shoes", "sneaker", "boot", "sandal"),
     "HS 6403 Footwear", 8.5),
    (("steel", "iron", "rebar", "stainless", "flat-rolled", "alloy steel"),
     "HS 7208 Flat-rolled iron / steel (general; Section 232 may apply)", 0.0),
    (("machinery", "machine", "engine", "pump", "turbine", "compressor",
      "gearbox", "robot", "mechanical"),
     "HS 8479 Machines / mechanical appliances", 2.5),
    (("electronics", "electronic", "phone", "smartphone", "telephone",
      "router", "modem", "telecom", "laptop", "computer", "television", "tv"),
     "HS 8517 / 84-85 Electronics", 0.0),
    (("furniture", "chair", "table", "desk", "sofa"),
     "HS 9403 Furniture", 0.0),
    (("toy", "toys", "game", "games"),
     "HS 9503 Toys", 0.0),
]

# Goods whose nature makes them "luxury" regardless of declared value.
_LUXURY_KEYWORDS: Tuple[str, ...] = (
    "luxury", "jewelry", "jewellery", "diamond", "gemstone", "gold",
    "watch", "rolex", "gucci", "prada", "hermes", "chanel", "louis vuitton",
    "designer", "handbag", "art", "painting", "sculpture", "antique",
    "wine", "champagne", "caviar", "perfume", "fur", "cashmere", "silk",
)


def _haystack(item: Item) -> str:
    """Lowercased 'name + category' text used for all keyword matching."""
    return f"{item.name} {item.category}".lower()


# --------------------------------------------------------------------------- #
# Duty classification
# --------------------------------------------------------------------------- #
def classify_item_duty(item: Item) -> Tuple[str, float]:
    """Return (HS label, ad-valorem duty rate %) for a single line item."""
    text = _haystack(item)
    for keywords, label, rate in _DUTY_RULES:
        if any(kw in text for kw in keywords):
            return label, rate
    return DEFAULT_DUTY_LABEL, DEFAULT_DUTY_RATE_PCT


def effective_duty(items: List[Item]) -> Tuple[str, float]:
    """Pick the governing duty rate for a mixed shipment.

    Without a per-item value split we take the *highest* applicable rate — the
    conservative customs estimate — and report which category drove it.
    """
    if not items:
        return DEFAULT_DUTY_LABEL, DEFAULT_DUTY_RATE_PCT
    best_label, best_rate = DEFAULT_DUTY_LABEL, -1.0
    for item in items:
        label, rate = classify_item_duty(item)
        if rate > best_rate:
            best_label, best_rate = label, rate
    return best_label, best_rate


# --------------------------------------------------------------------------- #
# Merchandise Processing Fee
# --------------------------------------------------------------------------- #
def merchandise_processing_fee(declared_value_usd: float) -> float:
    """U.S. CBP Merchandise Processing Fee for the shipment.

    Formal entries (value > $2,500): 0.3464% of value, clamped to the annual
    [min, max] band. Informal entries (<= $2,500): a flat fee.
    """
    value = max(0.0, declared_value_usd)
    if value <= FORMAL_ENTRY_THRESHOLD_USD:
        return INFORMAL_MPF_USD
    fee = value * MPF_AD_VALOREM_RATE
    return round(min(max(fee, MPF_MIN_USD), MPF_MAX_USD), 2)


# --------------------------------------------------------------------------- #
# Entry tax = MPF + category tariffs
# --------------------------------------------------------------------------- #
def compute_entry_tax(items: List[Item], declared_value_usd: float) -> Tuple[float, dict]:
    """Base U.S. entry tax: MPF + ad-valorem duty on the declared value.

    Returns (total_entry_tax_usd, breakdown) where ``breakdown`` is a
    JSON-serializable dict for logging / the orchestrator's cost display.
    """
    value = max(0.0, declared_value_usd)
    mpf = merchandise_processing_fee(value)
    duty_label, duty_rate_pct = effective_duty(items)
    duty_usd = round(value * duty_rate_pct / 100.0, 2)
    total = round(mpf + duty_usd, 2)
    breakdown = {
        "declared_value_usd": value,
        "merchandise_processing_fee_usd": mpf,
        "duty_rate_pct": duty_rate_pct,
        "duty_classification": duty_label,
        "duty_usd": duty_usd,
        "base_entry_tax_usd": total,
    }
    return total, breakdown


# --------------------------------------------------------------------------- #
# Luxury + transport-mode constraints
# --------------------------------------------------------------------------- #
def is_luxury_shipment(items: List[Item], declared_value_usd: float) -> bool:
    """True if any item is a luxury good, or per-unit value is very high."""
    for item in items:
        if any(kw in _haystack(item) for kw in _LUXURY_KEYWORDS):
            return True
    total_qty = sum(max(0, item.quantity) for item in items)
    if total_qty > 0:
        per_unit = max(0.0, declared_value_usd) / total_qty
        if per_unit >= LUXURY_PER_UNIT_USD:
            return True
    return False


def decide_transport(total_weight_kg: float, timeframe: str, is_luxury: bool) -> str:
    """Apply the spec's transport-mode constraint (precedence matters).

      1. weight <= 500  OR luxury  OR timeframe == "SPEED"  -> AIR
      2. weight > 2000  AND timeframe == "COST"             -> SHIP
      3. otherwise (500 < weight <= 2000)                   -> EITHER
    """
    if total_weight_kg <= AIR_ONLY_MAX_KG or is_luxury or timeframe == "SPEED":
        return "AIR"
    if total_weight_kg > SHIP_PREFERRED_MIN_KG and timeframe == "COST":
        return "SHIP"
    return "EITHER"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def compute_econ_data(req: ShipmentRequest) -> EconData:
    """Run the full Step-2 calculation: ShipmentRequest -> EconData."""
    is_high_value = req.declared_value_usd > HIGH_VALUE_THRESHOLD_USD
    luxury = is_luxury_shipment(req.items, req.declared_value_usd)
    transport = decide_transport(req.total_weight_kg, req.timeframe, luxury)
    entry_tax, _breakdown = compute_entry_tax(req.items, req.declared_value_usd)

    return EconData(
        transport_preference=transport,
        is_high_value=is_high_value,
        is_luxury=luxury,
        base_entry_tax_usd=entry_tax,
    )


def explain(req: ShipmentRequest) -> dict:
    """Verbose, JSON-serializable view of the decision — handy for logs/UI."""
    luxury = is_luxury_shipment(req.items, req.declared_value_usd)
    entry_tax, breakdown = compute_entry_tax(req.items, req.declared_value_usd)
    return {
        "is_high_value": req.declared_value_usd > HIGH_VALUE_THRESHOLD_USD,
        "is_luxury": luxury,
        "transport_preference": decide_transport(
            req.total_weight_kg, req.timeframe, luxury
        ),
        "entry_tax_breakdown": breakdown,
        "base_entry_tax_usd": entry_tax,
    }
