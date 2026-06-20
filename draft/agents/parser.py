"""Rule-based intent parser: natural language -> ShipmentSpec.

No LLM / no API key — pure regex + a small known-airport/commodity map, so the
orchestrator stays fully offline and deterministic. Designed to be forgiving:
every field has a fallback so a slightly-different prompt never crashes the swarm.
"""

from __future__ import annotations

import datetime
import re
from typing import Optional, Tuple

from agents.config import DEFAULT_DECLARED_VALUE_USD
from agents.messages import ShipmentSpec

# IATA codes we recognize when they appear bare or in parentheses.
KNOWN_AIRPORTS = {
    "SZX", "AUS", "LAX", "HKG", "DFW", "SFO", "JFK", "ORD", "SEA",
    "PVG", "PEK", "CAN", "ATL", "MIA", "EWR", "LHR", "FRA", "NRT", "ICN",
}

# City / region -> IATA fallback (when codes aren't given in parentheses).
CITY_TO_IATA = {
    "shenzhen": "SZX", "austin": "AUS", "los angeles": "LAX", "hong kong": "HKG",
    "dallas": "DFW", "san francisco": "SFO", "new york": "JFK", "chicago": "ORD",
    "seattle": "SEA", "shanghai": "PVG", "beijing": "PEK", "guangzhou": "CAN",
}

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Commodity keyword -> canonical description (used when the "of X" phrase is messy).
_COMMODITY_KEYWORDS = {
    "semiconductor": "semiconductor components",
    "chip": "semiconductor components",
    "ic ": "semiconductor components",
    "battery": "lithium batteries",
    "pharmaceutical": "pharmaceuticals",
    "textile": "textiles",
    "apparel": "apparel",
    "steel": "steel products",
    "machinery": "machinery",
    "electronics": "consumer electronics",
}


def _parse_airports(text: str) -> Tuple[str, str]:
    """Return (origin, destination) IATA codes. Prefers codes in parentheses."""
    # 1) Parenthesized codes: "Shenzhen (SZX) ... Austin, TX (AUS)"
    paren = re.findall(r"\(([A-Za-z]{3})\)", text)
    paren = [c.upper() for c in paren if c.upper() in KNOWN_AIRPORTS]
    if len(paren) >= 2:
        return paren[0], paren[1]

    # 2) "from <origin> ... to <destination>" using bare codes or city names.
    origin = destination = None
    m_from = re.search(r"from\s+([A-Za-z ,]+?)(?:\s+to\b|\s*\()", text, re.I)
    m_to = re.search(r"\bto\s+([A-Za-z ,]+?)(?:\s+by\b|\.|\s*\(|$)", text, re.I)
    if m_from:
        origin = _resolve_place(m_from.group(1))
    if m_to:
        destination = _resolve_place(m_to.group(1))

    # 3) Any bare known codes in order, as a last resort.
    if not (origin and destination):
        bare = [c.upper() for c in re.findall(r"\b([A-Za-z]{3})\b", text)
                if c.upper() in KNOWN_AIRPORTS]
        if len(bare) >= 2:
            origin = origin or bare[0]
            destination = destination or bare[1]

    return (origin or "SZX", destination or "AUS")


def _resolve_place(fragment: str) -> Optional[str]:
    frag = fragment.strip().lower()
    # bare IATA inside the fragment
    for tok in re.findall(r"[A-Za-z]{3}", frag.upper()):
        if tok in KNOWN_AIRPORTS:
            return tok
    for city, code in CITY_TO_IATA.items():
        if city in frag:
            return code
    return None


def _parse_weight(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|kgs|kilograms?)\b", text, re.I)
    return float(m.group(1)) if m else 100.0


def _parse_budget(text: str) -> float:
    # Prefer a dollar amount near budget/under/max; else any $ amount.
    m = re.search(r"(?:budget|under|max(?:imum)?|below)[^$]*\$?\s*([\d,]+(?:\.\d+)?)",
                  text, re.I)
    if not m:
        m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", text)
    return float(m.group(1).replace(",", "")) if m else 5000.0


def _parse_declared_value(text: str) -> float:
    m = re.search(r"(?:declared|goods|cargo)\s+value[^$]*\$?\s*([\d,]+(?:\.\d+)?)",
                  text, re.I)
    return float(m.group(1).replace(",", "")) if m else DEFAULT_DECLARED_VALUE_USD


def _parse_commodity(text: str) -> str:
    # "<n>kg of <commodity> from ..." is the cleanest signal.
    m = re.search(r"of\s+([A-Za-z][A-Za-z \-]+?)\s+(?:from|to|by)\b", text, re.I)
    if m:
        candidate = m.group(1).strip()
        if 3 <= len(candidate) <= 60:
            return candidate
    low = text.lower()
    for kw, canonical in _COMMODITY_KEYWORDS.items():
        if kw.strip() in low:
            return canonical
    return "general cargo"


def _parse_deadline(text: str, today: Optional[datetime.date] = None) -> str:
    """Return an ISO date for phrases like 'by next Thursday' / 'by 2026-07-02'."""
    today = today or datetime.date.today()

    # Explicit ISO date wins.
    m_iso = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m_iso:
        return m_iso.group(1)

    low = text.lower()
    for name, target_wd in _WEEKDAYS.items():
        if name in low:
            days_ahead = (target_wd - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # "this <weekday>" meaning the next occurrence
            if "next" in low:
                days_ahead += 7  # "next <weekday>" -> the following week
            return (today + datetime.timedelta(days=days_ahead)).isoformat()

    # "in N days"
    m_days = re.search(r"in\s+(\d+)\s+days?", low)
    if m_days:
        return (today + datetime.timedelta(days=int(m_days.group(1)))).isoformat()

    # Fallback: one week out.
    return (today + datetime.timedelta(days=7)).isoformat()


def parse_request(text: str) -> ShipmentSpec:
    """Parse a free-text logistics request into a structured ShipmentSpec."""
    origin, destination = _parse_airports(text)
    return ShipmentSpec(
        origin=origin,
        destination=destination,
        weight_kg=_parse_weight(text),
        commodity=_parse_commodity(text),
        deadline_iso=_parse_deadline(text),
        budget_usd=_parse_budget(text),
        declared_value_usd=_parse_declared_value(text),
    )


if __name__ == "__main__":
    demo = (
        "I have an emergency. I need to air-freight 200kg of semiconductor "
        "components from Shenzhen (SZX) to our warehouse in Austin, TX (AUS). "
        "They must arrive by next Thursday. My maximum budget is $3,500."
    )
    print(parse_request(demo).model_dump())
