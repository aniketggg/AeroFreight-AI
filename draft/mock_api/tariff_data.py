"""Mock HS-code classification + duty calculation for the TARIFF vertical.

This module is the data layer behind ``POST /tariff/classify`` in the FastAPI
mock server. It performs a lightweight, keyword-based Harmonized System (HS)
lookup: each commodity description is matched against a curated table of
keyword groups, and the first matching group supplies the HS code, a human
readable description, and an ad-valorem duty rate.

The numbers are representative of real US HTS general (MFN) duty rates as of
the mid-2020s, but this is a demo fixture, not a customs ruling. Pure stdlib;
no external dependencies.
"""

from typing import List, Tuple, TypedDict

# Default duty rate (percent) applied when no keyword group matches. A modest
# 3.0% mirrors the rough average MFN rate across miscellaneous manufactured
# goods, so an unclassified shipment still gets a plausible duty estimate.
DEFAULT_HS_CODE = "9999.99"
DEFAULT_DESCRIPTION = "Unclassified merchandise (general rate)"
DEFAULT_DUTY_RATE_PCT = 3.0


class HSEntry(TypedDict):
    """One row of the HS lookup table."""

    keywords: List[str]      # lowercase substrings; any hit selects this row
    hs_code: str             # HS / HTS heading, e.g. "8541.10"
    description: str         # plain-language description for the BoL / UI
    duty_rate_pct: float     # ad-valorem duty rate in percent


# Ordered lookup table. Order matters: more specific / higher-value groups are
# listed first so that, e.g., a "lithium battery pack" matches batteries before
# the broader "electronics" group. The first row whose keyword appears in the
# (lowercased) commodity string wins.
HS_TABLE: List[HSEntry] = [
    {
        # Semiconductors / integrated circuits. Required exact mapping for the
        # demo: chips classify to 8541.10 at 2.5% ($70 duty on $2,800).
        "keywords": [
            "semiconductor", "chip", "microchip", "integrated circuit",
            "ic ", " ic", "wafer", "transistor", "diode", "processor", "cpu",
        ],
        "hs_code": "8541.10",
        "description": "Semiconductor devices",
        "duty_rate_pct": 2.5,
    },
    {
        # Lithium-ion cells / battery packs (power banks, EV/laptop cells).
        "keywords": [
            "lithium", "li-ion", "lithium-ion", "battery", "batteries",
            "battery pack", "power bank", "powerbank", "accumulator",
        ],
        "hs_code": "8507.60",
        "description": "Lithium-ion accumulators (batteries)",
        "duty_rate_pct": 3.4,
    },
    {
        # Pharmaceuticals / medicaments. Many finished drugs enter duty-free
        # under the WTO Pharmaceutical Agreement, hence a 0% rate.
        "keywords": [
            "pharmaceutical", "pharma", "medicine", "medicament", "drug",
            "vaccine", "antibiotic", "insulin", "tablet", "capsule",
        ],
        "hs_code": "3004.90",
        "description": "Medicaments, packaged for retail sale",
        "duty_rate_pct": 0.0,
    },
    {
        # Apparel & textiles. Notoriously high tariff lines; 12% is a typical
        # blended rate for man-made-fibre garments.
        "keywords": [
            "textile", "apparel", "garment", "clothing", "clothes", "fabric",
            "cotton", "shirt", "dress", "jacket", "trouser", "knitwear", "wear",
        ],
        "hs_code": "6109.10",
        "description": "Textiles and apparel (knitted/woven garments)",
        "duty_rate_pct": 12.0,
    },
    {
        # Steel & primary iron/steel products. Flat-rolled steel base rate.
        "keywords": [
            "steel", "iron", "rebar", "stainless", "alloy steel",
            "steel coil", "steel sheet", "metal beam",
        ],
        "hs_code": "7208.39",
        "description": "Flat-rolled iron or non-alloy steel products",
        "duty_rate_pct": 0.0,
    },
    {
        # Industrial machinery & mechanical appliances (pumps, engines, tools).
        "keywords": [
            "machinery", "machine", "engine", "pump", "turbine", "compressor",
            "motor", "gearbox", "industrial equipment", "mechanical",
        ],
        "hs_code": "8479.89",
        "description": "Machines and mechanical appliances, n.e.s.",
        "duty_rate_pct": 2.5,
    },
    {
        # Consumer electronics / finished electronic goods. Listed AFTER the
        # narrow chip and battery groups so components route correctly first.
        "keywords": [
            "electronics", "electronic", "laptop", "computer", "phone",
            "smartphone", "tablet device", "monitor", "television", "tv",
            "camera", "headphone", "speaker", "router", "gadget",
        ],
        "hs_code": "8517.13",
        "description": "Consumer electronics and telecom equipment",
        "duty_rate_pct": 0.0,
    },
]


class ClassificationResult(TypedDict):
    """Return shape — mirrors the TariffResponse wire model exactly."""

    hs_code: str
    description: str
    duty_rate_pct: float
    duty_usd: float


def classify(commodity: str, declared_value_usd: float) -> ClassificationResult:
    """Classify a commodity to an HS code and compute the import duty.

    The match is a case-insensitive substring scan over :data:`HS_TABLE`; the
    first group with a keyword present in ``commodity`` wins. If nothing
    matches, the sensible DEFAULT line (3.0%) is used so the caller always
    receives a complete, JSON-serializable result.

    Args:
        commodity: Free-text description of the goods (e.g. "semiconductor chips").
        declared_value_usd: Customs declared value used as the duty base.

    Returns:
        Dict with keys ``hs_code``, ``description``, ``duty_rate_pct`` and
        ``duty_usd`` (rate% x declared value, rounded to cents).
    """
    needle = (commodity or "").lower()

    # Resolve the matching HS line, falling back to the DEFAULT.
    hs_code, description, duty_rate_pct = (
        DEFAULT_HS_CODE,
        DEFAULT_DESCRIPTION,
        DEFAULT_DUTY_RATE_PCT,
    )
    for entry in HS_TABLE:
        if any(keyword in needle for keyword in entry["keywords"]):
            hs_code = entry["hs_code"]
            description = entry["description"]
            duty_rate_pct = entry["duty_rate_pct"]
            break

    # Duty = rate% of the declared value. Guard against negative inputs and
    # round to cents for clean currency display.
    base_value = max(0.0, float(declared_value_usd))
    duty_usd = round(duty_rate_pct / 100.0 * base_value, 2)

    return {
        "hs_code": hs_code,
        "description": description,
        "duty_rate_pct": duty_rate_pct,
        "duty_usd": duty_usd,
    }
