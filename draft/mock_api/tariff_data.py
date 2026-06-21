"""HS-code classification + duty calculation backed by LIVE USITC HTS data.

This module is the data layer behind ``POST /tariff/classify`` in the FastAPI
mock server. It is *not* a static fixture: duty rates are fetched at runtime
from the official United States International Trade Commission (USITC)
Harmonized Tariff Schedule (HTS) REST API and parsed into ad-valorem rates.

Real data source
----------------
    GET https://hts.usitc.gov/reststop/exportList?from={FROM}&to={TO}&format=JSON&styles=false

The endpoint returns a JSON array of HTS rows. Each row of interest has:

    * ``htsno``       - the HTS number, e.g. "8541.10.00" or "8507.60.00.10".
                        Heading rows are 4 digits ("8541"); statistical suffix
                        rows are 10 digits; many intermediate rows carry the
                        legally operative duty rate.
    * ``description`` - plain-language product text for that row.
    * ``general``     - the General (Column 1, MFN) duty rate as published,
                        e.g. "Free", "", "2.5%", "3.4%", "16.5%", "1.5¢/kg".
    * ``indent``      - nesting depth in the tariff tree (string int).
    * ``units``       - statistical units (list).

Important quirk: querying ``from=H&to=H`` for a 4-digit heading ``H`` returns
ONLY the bare heading row (whose ``general`` is empty). To enumerate the
subheadings that actually carry duty rates, we must request the half-open
range ``[H, H+1)`` i.e. ``from=H&to=H+1``.

Classification strategy
-----------------------
1. Map the free-text commodity to a real 4-digit HS *heading* via a curated
   keyword table (``KEYWORD_HEADINGS``). Optional finer "hint" keywords can
   bias selection toward a specific subheading prefix within the heading
   (e.g. "lithium" -> prefer ``8507.60``).
2. Fetch (read-through cached) the heading's rows and pick the most specific
   subheading row that publishes a non-empty ``general`` rate, honoring any
   subheading hint. Rows with an empty ``general`` (bare headings / pure
   statistical suffixes) are skipped because they carry no operative rate.
3. Parse the ``general`` string:
        "Free" / ""      -> 0.0
        "<num>%"         -> float(num)            (ad-valorem)
        "x¢/kg" etc.     -> 0.0, raw rate noted in the description (we cannot
                            compute a specific/compound duty without quantity).
4. ``duty_usd = round(duty_rate_pct / 100 * declared_value_usd, 2)``.

Caching + offline resilience
----------------------------
Each heading's parsed pick is written through to ``data/hts_cache.json`` keyed
by heading. On network failure we serve the cached value; if a heading was
never cached we fall back to the default heading's cached value, and finally to
a documented duty-free (0.0) result so the caller always gets a complete,
JSON-serializable response.

Pure stdlib only (``urllib`` + ``json``); short network timeouts.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple, TypedDict

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Official USITC HTS REST endpoint. ``styles=false`` strips presentational
# markup, leaving clean text fields.
HTS_EXPORT_URL = (
    "https://hts.usitc.gov/reststop/exportList"
    "?from={frm}&to={to}&format=JSON&styles=false"
)

# Network timeout (seconds). Kept short so a slow/unreachable USITC host fails
# fast and we fall back to cache instead of blocking the request thread.
HTTP_TIMEOUT_S = 12.0

# A polite, browser-like User-Agent. The USITC host rejects some default
# urllib agents, so we always send this.
_USER_AGENT = "AeroFreight-AI/1.0 (+tariff-classifier; stdlib-urllib)"

# Read-through cache of parsed heading picks. Lives alongside the other data
# files so it ships with the repo and provides offline resilience.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
CACHE_PATH = os.path.join(_REPO_ROOT, "data", "hts_cache.json")

# Default heading used when no keyword matches. 8479 ("Machines and mechanical
# appliances having individual functions, n.e.s.") is a broad industrial
# catch-all heading and a defensible generic classification.
DEFAULT_HEADING = "8479"
DEFAULT_DESCRIPTION = "Machinery / mechanical appliances (general)"


# --------------------------------------------------------------------------- #
# Keyword -> real HS heading table
# --------------------------------------------------------------------------- #
class HeadingRule(TypedDict, total=False):
    """One commodity-routing rule.

    ``keywords``    : lowercase substrings; any hit selects this heading.
    ``heading``     : the real 4-digit HS heading to fetch.
    ``label``       : clean short label used as the response ``description``.
    ``sub_hints``   : optional list of (keyword, htsno_prefix) pairs. If a hint
                      keyword is present in the commodity text, selection is
                      biased toward the first rated row whose htsno starts with
                      the given prefix (e.g. "lithium" -> "8507.60").
    """

    keywords: List[str]
    heading: str
    label: str
    sub_hints: List[Tuple[str, str]]


# Order matters: narrow/component groups precede broad finished-goods groups so
# that, e.g., a "lithium battery" routes to 8507 before the generic electronics
# group could ever claim it. The first rule with a keyword hit wins.
KEYWORD_HEADINGS: List[HeadingRule] = [
    {
        # Semiconductor devices: diodes, transistors, ICs, wafers, LEDs, PV
        # cells. USITC heading 8541. (Note: bare ICs are 8542, but the demo
        # contract maps the chip/IC family to 8541, which is duty-Free.)
        "keywords": [
            "semiconductor", "chip", "microchip", "integrated circuit",
            " ic ", "ic ", " ic", "wafer", "transistor", "diode",
            "thyristor", "led", "photovoltaic", "solar cell",
        ],
        "heading": "8541",
        "label": "Semiconductor devices",
    },
    {
        # Electric storage batteries (incl. lithium-ion). Heading 8507.
        "keywords": [
            "lithium", "li-ion", "lithium-ion", "battery", "batteries",
            "battery pack", "power bank", "powerbank", "accumulator",
            "cell pack", "lead-acid", "nickel",
        ],
        "heading": "8507",
        "label": "Electric storage batteries",
        # Bias common chemistries to their real subheadings.
        "sub_hints": [
            ("lithium", "8507.60"),   # Lithium-ion batteries (3.4%)
            ("li-ion", "8507.60"),
            ("nickel-metal", "8507.50"),
            ("nimh", "8507.50"),
            ("nickel", "8507.30"),
            ("lead", "8507.10"),
        ],
    },
    {
        # Medicaments put up in measured doses / for retail sale. Heading 3004.
        # Most finished drugs enter Free under the WTO Pharma Agreement.
        "keywords": [
            "pharmaceutical", "pharma", "medicine", "medicament", "drug",
            "vaccine", "antibiotic", "insulin", "tablet", "capsule",
        ],
        "heading": "3004",
        "label": "Medicaments (packaged for retail)",
    },
    {
        # T-shirts, singlets and other vests, knitted or crocheted. Heading
        # 6109. A classic high-tariff apparel line.
        "keywords": [
            "textile", "apparel", "garment", "clothing", "clothes",
            "t-shirt", "tshirt", "shirt", "knitwear", "knitted", "wear",
        ],
        "heading": "6109",
        "label": "Apparel / knitted garments",
        "sub_hints": [
            ("cotton", "6109.10"),    # of cotton (16.5%)
        ],
    },
    {
        # Flat-rolled products of iron / non-alloy steel. Heading 7208.
        "keywords": [
            "steel", "iron", "rebar", "stainless", "alloy steel",
            "steel coil", "steel sheet", "flat-rolled", "metal beam",
        ],
        "heading": "7208",
        "label": "Flat-rolled iron / steel products",
    },
    {
        # Machines & mechanical appliances having individual functions, n.e.s.
        # Heading 8479 (also the global default).
        "keywords": [
            "machinery", "machine", "engine", "pump", "turbine",
            "compressor", "gearbox", "industrial equipment", "mechanical",
            "appliance", "robot",
        ],
        "heading": "8479",
        "label": "Machines / mechanical appliances",
    },
    {
        # Telephone sets & apparatus for transmission/reception of voice/data
        # (incl. smartphones, base stations, networking). Heading 8517. Listed
        # AFTER the narrow chip/battery groups so components route first.
        "keywords": [
            "consumer electronics", "electronics", "electronic", "phone",
            "smartphone", "telephone", "cellphone", "router", "modem",
            "network", "telecom", "base station",
        ],
        "heading": "8517",
        "label": "Telephone / telecom apparatus",
    },
]


# --------------------------------------------------------------------------- #
# Return shape — mirrors the TariffResponse wire model EXACTLY.
# --------------------------------------------------------------------------- #
class ClassificationResult(TypedDict):
    hs_code: str
    description: str
    duty_rate_pct: float
    duty_usd: float


# Shape of one cached heading entry (the chosen subheading + its parsed rate).
class _CachedPick(TypedDict):
    htsno: str
    general: str          # raw "general" string as published
    duty_rate_pct: float
    note: str             # extra note (e.g. raw specific-duty rate), may be ""


# --------------------------------------------------------------------------- #
# Rate parsing
# --------------------------------------------------------------------------- #
# Matches a leading ad-valorem percentage, e.g. "2.5%", "16.5 %", "32%".
_PCT_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*%")


def _parse_general_rate(general: Optional[str]) -> Tuple[float, str]:
    """Parse a USITC ``general`` rate string into (pct, note).

    Returns the ad-valorem percentage as a float and an optional human note.

      * "Free" or empty            -> (0.0, "")
      * "<num>%"                   -> (float(num), "")
      * non-ad-valorem ("x¢/kg",   -> (0.0, "specific/compound rate: <raw>")
        compound, "x¢/kg + y%", …)    because a specific or compound duty
                                       cannot be computed without quantity.
    """
    if not general:
        return 0.0, ""

    text = general.strip()
    if not text or text.lower() == "free":
        return 0.0, ""

    m = _PCT_RE.match(text)
    if m:
        # Pure ad-valorem percentage (the common, computable case). We take the
        # leading percentage; compound rates like "2.5% + 1.5¢/kg" are reduced
        # to their ad-valorem component with the raw rate preserved in the note.
        try:
            pct = float(m.group(1))
        except ValueError:  # pragma: no cover - regex guarantees a number
            return 0.0, ""
        if "+" in text or "¢" in text or "cent" in text.lower():
            return pct, f"compound rate (ad-valorem component used): {text}"
        return pct, ""

    # Specific or otherwise non-ad-valorem duty (e.g. "1.5¢/kg"): we cannot
    # turn this into a value-based duty without a quantity, so we report 0.0
    # ad-valorem and surface the published rate in the note for transparency.
    return 0.0, f"specific/compound rate (not value-based): {text}"


# --------------------------------------------------------------------------- #
# Cache I/O
# --------------------------------------------------------------------------- #
def _load_cache() -> Dict[str, _CachedPick]:
    """Load the heading cache from disk; return {} if missing/corrupt."""
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def _save_cache(cache: Dict[str, _CachedPick]) -> None:
    """Persist the heading cache, creating the data dir if needed."""
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, sort_keys=True, ensure_ascii=False)
    except OSError:
        # A read-only filesystem must not break classification; we simply skip
        # persistence and continue serving the freshly-fetched value.
        pass


# --------------------------------------------------------------------------- #
# Live USITC fetch + subheading selection
# --------------------------------------------------------------------------- #
def _next_heading(heading: str) -> str:
    """Return the next 4-digit heading string (e.g. "8541" -> "8542").

    Used to build the half-open export range ``[heading, heading+1)`` so the
    API returns the heading's subheadings, not just the bare heading row.
    """
    try:
        return f"{int(heading) + 1:04d}"
    except ValueError:
        return heading


def _fetch_heading_rows(heading: str) -> List[dict]:
    """Fetch all HTS rows for a 4-digit heading from the live USITC API.

    Raises ``urllib.error.URLError`` / ``OSError`` on network failure so the
    caller can fall back to cache.
    """
    url = HTS_EXPORT_URL.format(frm=heading, to=_next_heading(heading))
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError(f"USITC export for {heading} was not a JSON array")
    return rows


def _select_pick(
    rows: List[dict], sub_prefix: Optional[str]
) -> Optional[_CachedPick]:
    """Choose the best rated subheading row from a heading's export.

    Selection rules:
      * Only consider rows whose ``general`` field is non-empty (bare headings
        and pure statistical-suffix rows have no operative rate).
      * If ``sub_prefix`` is given (a subheading hint like "8507.60"), prefer
        the first rated row whose htsno starts with that prefix.
      * Otherwise prefer the first rated row that is a *fully specified*
        subheading (htsno contains a dot), which is the most representative
        general rate for the heading.

    Returns a parsed ``_CachedPick`` or ``None`` if no rated row exists.
    """
    rated: List[dict] = [r for r in rows if (r.get("general") or "").strip()]
    if not rated:
        return None

    chosen: Optional[dict] = None

    # 1) Honor an explicit subheading hint when present.
    if sub_prefix:
        for r in rated:
            htsno = (r.get("htsno") or "").strip()
            if htsno.startswith(sub_prefix):
                chosen = r
                break

    # 2) Otherwise take the first rated, fully-specified (dotted) subheading.
    if chosen is None:
        for r in rated:
            htsno = (r.get("htsno") or "").strip()
            if "." in htsno:
                chosen = r
                break

    # 3) Last resort: the first rated row of any form.
    if chosen is None:
        chosen = rated[0]

    htsno = (chosen.get("htsno") or "").strip()
    general = (chosen.get("general") or "").strip()
    pct, note = _parse_general_rate(general)
    return {
        "htsno": htsno,
        "general": general,
        "duty_rate_pct": pct,
        "note": note,
    }


def _get_heading_pick(heading: str, sub_prefix: Optional[str]) -> Tuple[_CachedPick, bool]:
    """Return (pick, from_cache) for a heading, fetching live + caching.

    Read-through: if the heading is already cached we serve the cache. On a
    cache miss we hit the live USITC API, persist the parsed pick, and return
    it. On network failure we return any stale cache for the heading, else a
    documented duty-free placeholder.

    The boolean second element reports whether the value came from cache (vs a
    fresh network fetch) — useful for the smoke test / observability.
    """
    cache = _load_cache()

    # NOTE: the cache key includes the subheading hint so that, e.g.,
    # "8507::8507.60" and "8507::" can coexist with different rates.
    cache_key = f"{heading}::{sub_prefix or ''}"

    if cache_key in cache:
        return cache[cache_key], True

    try:
        rows = _fetch_heading_rows(heading)
        pick = _select_pick(rows, sub_prefix)
        if pick is None:
            # Heading exists but has no rated rows; treat as duty-free and note.
            pick = {
                "htsno": heading,
                "general": "",
                "duty_rate_pct": 0.0,
                "note": "no rated subheading found; treated as duty-free",
            }
        cache[cache_key] = pick
        _save_cache(cache)
        return pick, False
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        # ---- Offline / failure path -------------------------------------- #
        # 1) Any cached value for this exact heading (ignoring hint)?
        for key, val in cache.items():
            if key.startswith(f"{heading}::"):
                return val, True
        # 2) Fall back to the default heading's cached value, if any.
        for key, val in cache.items():
            if key.startswith(f"{DEFAULT_HEADING}::"):
                return val, True
        # 3) Documented last-resort: duty-free, clearly flagged.
        return (
            {
                "htsno": heading,
                "general": "",
                "duty_rate_pct": 0.0,
                "note": "offline and no cache available; defaulted to duty-free",
            },
            True,
        )


# --------------------------------------------------------------------------- #
# Commodity -> heading routing
# --------------------------------------------------------------------------- #
def _route_commodity(commodity: str) -> Tuple[str, str, Optional[str]]:
    """Map free-text commodity to (heading, label, sub_prefix).

    Falls back to the default heading when nothing matches. ``sub_prefix`` is
    an optional htsno prefix to bias subheading selection.
    """
    needle = (commodity or "").lower()

    for rule in KEYWORD_HEADINGS:
        if any(kw in needle for kw in rule["keywords"]):
            sub_prefix: Optional[str] = None
            for hint_kw, prefix in rule.get("sub_hints", []):
                if hint_kw in needle:
                    sub_prefix = prefix
                    break
            return rule["heading"], rule["label"], sub_prefix

    return DEFAULT_HEADING, DEFAULT_DESCRIPTION, None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def classify(commodity: str, declared_value_usd: float) -> ClassificationResult:
    """Classify a commodity to a real HTS code and compute the import duty.

    Pipeline: route the commodity text to a 4-digit HS heading, fetch (cached)
    that heading's subheadings from the live USITC HTS API, select the most
    specific rated subheading, parse its General (MFN) rate, and apply it to
    the declared value.

    Args:
        commodity: Free-text goods description (e.g. "semiconductor components").
        declared_value_usd: Customs declared value used as the duty base.

    Returns:
        Dict with EXACTLY the keys ``hs_code``, ``description``,
        ``duty_rate_pct`` and ``duty_usd`` (matching ``TariffResponse``).
    """
    heading, label, sub_prefix = _route_commodity(commodity)
    pick, _from_cache = _get_heading_pick(heading, sub_prefix)

    # The chosen HTS number, e.g. "8541.10.00". Fall back to the heading itself
    # if a pick somehow lacks an htsno.
    hs_code = pick.get("htsno") or heading
    duty_rate_pct = float(pick.get("duty_rate_pct", 0.0))

    # Build a clean short description. If the rate is a non-computable
    # specific/compound duty, surface that in the description note so the BoL/UI
    # makes the limitation explicit.
    description = label
    note = pick.get("note") or ""
    if note:
        description = f"{label} ({note})"

    # Duty = ad-valorem rate% of the declared value. Guard negatives, round to
    # cents for currency display.
    base_value = max(0.0, float(declared_value_usd))
    duty_usd = round(duty_rate_pct / 100.0 * base_value, 2)

    return {
        "hs_code": hs_code,
        "description": description,
        "duty_rate_pct": duty_rate_pct,
        "duty_usd": duty_usd,
    }


# --------------------------------------------------------------------------- #
# Manual smoke test / cache pre-population.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import pprint

    for desc, val in [
        ("semiconductor components", 2800.0),
        ("lithium batteries", 5000.0),
    ]:
        print(f"\nclassify({desc!r}, {val}) ->")
        pprint.pprint(classify(desc, val))
