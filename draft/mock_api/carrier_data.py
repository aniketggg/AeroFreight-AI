"""Mock carrier / route dataset for the Freight-Router vertical.

The FastAPI server (``server.py``, owned by the integrator) routes
``POST /freight/quote`` to :func:`quote` below. We model a tiny but realistic
network of air freight legs (long-haul, port-to-port) and ground legs
(last-mile trucking) over a handful of hubs:

    SZX  Shenzhen Bao'an Intl (origin port for the canonical demo)
    HKG  Hong Kong Intl       (alternate Pearl-River-Delta gateway)
    LAX  Los Angeles Intl     (US west-coast import hub)
    DFW  Dallas/Fort Worth    (Texas inland hub)
    AUS  Austin-Bergstrom      (final destination for the demo)

The canonical SZX -> AUS shipment resolves to:
    Cathay Pacific Cargo CX086 (SZX -> LAX, air)  +  FedEx Priority (LAX -> AUS,
    ground), ~4 days total, ~$2,800 for 200 kg.

The data is intentionally hand-tuned so the cheapest deadline-meeting itinerary
matches that expected answer, while still exercising the cheapest-combination
search logic for other origin/destination pairs.
"""

from __future__ import annotations

import datetime
from typing import Dict, List


# --------------------------------------------------------------------------- #
# Cost model
# --------------------------------------------------------------------------- #
# Each leg has a flat handling/base cost plus a per-kg rate. Air legs are priced
# per-kg (chargeable weight dominates long-haul); ground legs are cheaper per-kg
# but still scale a little with weight. Total cost therefore scales with weight,
# as required, while the 200 kg reference case lands near the $2,800 target.
#
# A leg record carries everything the cost calc and the wire response need:
#   mode, carrier, service, from_node, to_node, base_usd, per_kg_usd, transit_days
# --------------------------------------------------------------------------- #

# Air legs: long-haul port-to-port flights.
AIR_LEGS: List[Dict] = [
    {
        "mode": "air",
        "carrier": "Cathay Pacific Cargo",
        "service": "CX086",
        "from_node": "SZX",
        "to_node": "LAX",
        "base_usd": 400.0,
        "per_kg_usd": 11.0,   # 200 kg -> 400 + 2200 = $2,600 for the air leg
        "transit_days": 2,
    },
    {
        # Slightly cheaper Hong Kong departure, but requires a HKG feeder first,
        # so it rarely wins for SZX origins once the feeder leg is added.
        "mode": "air",
        "carrier": "Cathay Pacific Cargo",
        "service": "CX880",
        "from_node": "HKG",
        "to_node": "LAX",
        "base_usd": 380.0,
        "per_kg_usd": 10.5,
        "transit_days": 2,
    },
    {
        # Premium express alternative on the SZX->LAX lane: faster but pricier,
        # so it only wins when a tighter deadline rules out CX086 + ground.
        "mode": "air",
        "carrier": "FedEx Express",
        "service": "FX5077",
        "from_node": "SZX",
        "to_node": "LAX",
        "base_usd": 600.0,
        "per_kg_usd": 14.0,
        "transit_days": 1,
    },
    {
        # Direct SZX -> DFW widebody, used by DFW-bound or DFW-via itineraries.
        "mode": "air",
        "carrier": "American Airlines Cargo",
        "service": "AA2188",
        "from_node": "SZX",
        "to_node": "DFW",
        "base_usd": 520.0,
        "per_kg_usd": 13.0,
        "transit_days": 3,
    },
]

# Short feeder/connector air hop (e.g. SZX -> HKG cross-border consolidation).
FEEDER_AIR_LEGS: List[Dict] = [
    {
        "mode": "air",
        "carrier": "Hong Kong Air Cargo",
        "service": "RH601",
        "from_node": "SZX",
        "to_node": "HKG",
        "base_usd": 120.0,
        "per_kg_usd": 1.5,
        "transit_days": 1,
    },
]

# Ground legs: last-mile / inland trucking from the import hub to destination.
GROUND_LEGS: List[Dict] = [
    {
        "mode": "ground",
        "carrier": "FedEx",
        "service": "Priority",
        "from_node": "LAX",
        "to_node": "AUS",
        "base_usd": 150.0,
        "per_kg_usd": 0.25,   # 200 kg -> 150 + 50 = $200 for the ground leg
        "transit_days": 2,
    },
    {
        # Slower economy ground option on the same lane. It is only marginally
        # cheaper per-kg, so for the canonical 200 kg move FedEx Priority wins
        # outright on cost AND speed; Ground only surfaces on very wide deadlines
        # or heavier loads where its lower per-kg rate eventually overtakes.
        "mode": "ground",
        "carrier": "FedEx",
        "service": "Ground",
        "from_node": "LAX",
        "to_node": "AUS",
        "base_usd": 180.0,
        "per_kg_usd": 0.20,
        "transit_days": 5,
    },
    {
        "mode": "ground",
        "carrier": "FedEx",
        "service": "Priority",
        "from_node": "DFW",
        "to_node": "AUS",
        "base_usd": 80.0,
        "per_kg_usd": 0.15,
        "transit_days": 1,
    },
    {
        "mode": "ground",
        "carrier": "FedEx",
        "service": "Priority",
        "from_node": "LAX",
        "to_node": "DFW",
        "base_usd": 130.0,
        "per_kg_usd": 0.22,
        "transit_days": 2,
    },
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _leg_cost(leg: Dict, weight_kg: float) -> float:
    """Total cost of a single leg for the given chargeable weight."""
    return leg["base_usd"] + leg["per_kg_usd"] * weight_kg


def _public_leg(leg: Dict) -> Dict[str, str]:
    """Project an internal leg record down to the frozen wire fields only."""
    return {
        "mode": leg["mode"],
        "carrier": leg["carrier"],
        "service": leg["service"],
        "from_node": leg["from_node"],
        "to_node": leg["to_node"],
    }


def _eta_iso(transit_days: int) -> str:
    """Compute the ETA as today + transit_days, formatted YYYY-MM-DD."""
    eta = datetime.date.today() + datetime.timedelta(days=transit_days)
    return eta.isoformat()


def _meets_deadline(eta_iso: str, deadline_iso: str) -> bool:
    """ISO dates sort lexicographically, so a string compare is correct here.

    If the deadline is missing/unparseable we optimistically assume it is met
    rather than failing the whole quote.
    """
    if not deadline_iso:
        return True
    # Normalize to the date portion in case a full timestamp was supplied.
    deadline_date = deadline_iso[:10]
    return eta_iso <= deadline_date


def _itinerary(legs: List[Dict], weight_kg: float, deadline_iso: str) -> Dict:
    """Assemble the frozen FreightResponse-shaped dict for a list of legs."""
    total_cost = round(sum(_leg_cost(leg, weight_kg) for leg in legs), 2)
    transit_days = sum(leg["transit_days"] for leg in legs)
    eta_iso = _eta_iso(transit_days)
    return {
        "legs": [_public_leg(leg) for leg in legs],
        "total_cost_usd": total_cost,
        "transit_days": transit_days,
        "eta_iso": eta_iso,
        "meets_deadline": _meets_deadline(eta_iso, deadline_iso),
    }


def _candidate_itineraries(origin: str, destination: str) -> List[List[Dict]]:
    """Enumerate plausible leg combinations connecting origin -> destination.

    We build:
      * direct air legs (origin -> destination), if any exist;
      * air-hub + ground combinations (origin -> hub -> destination);
      * feeder + air-hub + ground combinations (origin -> HKG -> hub -> dest).

    The caller scores each candidate and picks the cheapest deadline-meeting one.
    """
    candidates: List[List[Dict]] = []

    # 1) Direct single air leg origin -> destination (rare, but possible).
    for air in AIR_LEGS:
        if air["from_node"] == origin and air["to_node"] == destination:
            candidates.append([air])

    # 2) Air (origin -> hub) + ground (hub -> destination).
    for air in AIR_LEGS:
        if air["from_node"] != origin:
            continue
        hub = air["to_node"]
        for ground in GROUND_LEGS:
            if ground["from_node"] == hub and ground["to_node"] == destination:
                candidates.append([air, ground])

    # 3) Air (origin -> hub) + ground (hub -> mid) + ground (mid -> destination).
    #    Covers e.g. LAX -> DFW -> AUS multi-truck routings.
    for air in AIR_LEGS:
        if air["from_node"] != origin:
            continue
        hub = air["to_node"]
        for g1 in GROUND_LEGS:
            if g1["from_node"] != hub:
                continue
            mid = g1["to_node"]
            if mid == destination:
                continue
            for g2 in GROUND_LEGS:
                if g2["from_node"] == mid and g2["to_node"] == destination:
                    candidates.append([air, g1, g2])

    # 4) Feeder (origin -> HKG) + air (HKG -> hub) + ground (hub -> destination).
    for feeder in FEEDER_AIR_LEGS:
        if feeder["from_node"] != origin:
            continue
        feeder_hub = feeder["to_node"]
        for air in AIR_LEGS:
            if air["from_node"] != feeder_hub:
                continue
            hub = air["to_node"]
            for ground in GROUND_LEGS:
                if ground["from_node"] == hub and ground["to_node"] == destination:
                    candidates.append([feeder, air, ground])

    return candidates


def _generic_air_fallback(origin: str, destination: str, weight_kg: float,
                          deadline_iso: str) -> Dict:
    """Single generic air leg for routes not present in the dataset.

    Priced on the same per-kg model as the known lanes so totals stay sane, and
    flagged with a generic carrier/service so the orchestrator can narrate it.
    """
    leg = {
        "mode": "air",
        "carrier": "AeroFreight Consolidators",
        "service": "AF-CHARTER",
        "from_node": origin,
        "to_node": destination,
        "base_usd": 500.0,
        "per_kg_usd": 12.0,
        "transit_days": 5,   # conservative estimate for an unmodeled lane
    }
    return _itinerary([leg], weight_kg, deadline_iso)


# --------------------------------------------------------------------------- #
# Public API (called by the FastAPI server)
# --------------------------------------------------------------------------- #
def quote(origin: str, destination: str, weight_kg: float,
          deadline_iso: str) -> dict:
    """Return the cheapest deadline-meeting itinerary for the requested move.

    Strategy:
      1. Enumerate every modeled origin -> destination itinerary.
      2. Prefer itineraries that beat the deadline; among those, pick the
         cheapest. If none beat the deadline, fall back to the cheapest
         itinerary overall (still returned, just flagged meets_deadline=False).
      3. If the dataset has no path at all, synthesize a generic single air leg.

    The returned dict matches the frozen ``FreightResponse`` contract exactly:
        {"legs": [{"mode","carrier","service","from_node","to_node"}...],
         "total_cost_usd", "transit_days", "eta_iso", "meets_deadline"}
    """
    origin = (origin or "").strip().upper()
    destination = (destination or "").strip().upper()
    # Guard against absurd / missing weights so cost stays well-defined.
    weight_kg = float(weight_kg) if weight_kg and weight_kg > 0 else 1.0

    candidates = _candidate_itineraries(origin, destination)
    if not candidates:
        # No modeled route -> generic fallback air leg.
        return _generic_air_fallback(origin, destination, weight_kg, deadline_iso)

    # Score every candidate itinerary into a wire-shaped dict.
    scored = [_itinerary(legs, weight_kg, deadline_iso) for legs in candidates]

    # Partition into deadline-meeting vs. not, then pick cheapest of the best
    # available tier (meeting deadline wins over a marginally cheaper late one).
    on_time = [s for s in scored if s["meets_deadline"]]
    pool = on_time if on_time else scored
    best = min(pool, key=lambda s: s["total_cost_usd"])
    return best


# --------------------------------------------------------------------------- #
# Manual smoke test: python -m mock_api.carrier_data
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import json

    # Canonical demo: SZX -> AUS, 200 kg, generous deadline.
    far_deadline = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    demo = quote("SZX", "AUS", 200.0, far_deadline)
    print(json.dumps(demo, indent=2))
