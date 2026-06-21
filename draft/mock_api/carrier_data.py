"""Freight routing + pricing engine for the Freight-Router vertical.

This is the *real* engine: it routes and prices an international air-freight move
over the OpenFlights open-data network (``draft/data/{airports,routes,airlines}.dat``,
loaded once via :mod:`mock_api.geo`) instead of a hand-tuned lookup table.

Pipeline for ``quote(origin, destination, weight_kg, deadline_iso)``
-------------------------------------------------------------------
1. Resolve the origin/destination IATA codes to real geocoded airports.
2. AIR routing: BFS the real routes graph from the origin out to <= 2 air hops,
   collecting every reachable US airport (the import "gateway"). For each
   candidate gateway, score it by
       air great-circle distance (origin -> gateway)
     + ground great-circle distance (gateway -> destination)
   and pick the gateway that minimises that total. If the destination *itself* is
   reachable by air it competes as a zero-ground-distance gateway, so we fly
   direct whenever that is the best end-to-end option.
3. GROUND: if the chosen gateway is not the destination, add one trucking leg
   gateway -> destination, priced/timed off the haversine road-proxy distance.
4. PRICING: a transparent, documented estimate model (constants below). No live
   spot rates and no dimensional weight are available, so chargeable weight is the
   gross weight floored at 1 kg.
5. TIME: air flight time from distance + per-hop handling; ground days from a
   daily-drive distance; plus a one-day customs buffer.
6. FALLBACK: if the origin can reach *no* US airport within the hop budget, emit a
   single generic great-circle air leg priced/timed by the same model.

The returned dict matches the frozen ``FreightResponse`` contract exactly:
    {"legs": [{"mode","carrier","service","from_node","to_node"}...],
     "total_cost_usd", "transit_days", "eta_iso", "meets_deadline"}
"""

from __future__ import annotations

import datetime
import math
from collections import deque
from typing import Dict, List, Optional, Tuple

from mock_api import geo


# --------------------------------------------------------------------------- #
# PRICING MODEL — transparent, documented constants.
#
# This is an *estimate* model, not a live-spot-rate feed. The constants are
# calibrated so the canonical 200 kg SZX -> AUS move (≈13,100 air-km via an Asian
# hub + ≈300 ground-km) lands in a realistic low-thousands-USD range, while every
# number still scales continuously with the real great-circle distances.
#
# No cargo dimensions are available in OpenFlights, so we cannot compute volumetric
# / dimensional weight; chargeable weight is therefore the gross weight, floored at
# 1 kg (documented here and at the call site).
# --------------------------------------------------------------------------- #

# Air freight: a per-kg base plus a per-kg surcharge that grows with distance.
# 200 kg over ~13,100 km -> 200 * (3.50 + 0.75 * 13.1) ≈ $2,665 of air cost.
AIR_RATE_PER_KG_BASE = 3.50          # USD per kg, distance-independent component
AIR_RATE_PER_KG_PER_1000KM = 0.75    # USD per kg added for every 1,000 km flown

# Ground trucking: a flat dispatch fee, a per-km haul charge, and a small per-kg
# handling charge. ~306 km + 200 kg -> 120 + 0.90*306 + 200*0.12 ≈ $419.
GROUND_BASE = 120.0                  # USD flat dispatch / drayage fee
GROUND_PER_KM = 0.90                 # USD per kilometre hauled
GROUND_PER_KG = 0.12                 # USD per kg handled

# --------------------------------------------------------------------------- #
# TIME MODEL constants.
# --------------------------------------------------------------------------- #
AIR_SPEED_KMH = 850.0                # effective cruise speed for ETA estimation
AIR_HANDLING_HOURS_PER_HOP = 2.0     # load/connect/unload buffer per air hop
GROUND_KM_PER_DAY = 700.0            # realistic single-driver daily haul distance
CUSTOMS_BUFFER_DAYS = 1              # fixed import-clearance allowance

# --------------------------------------------------------------------------- #
# Routing constants.
# --------------------------------------------------------------------------- #
MAX_AIR_HOPS = 2                     # cap on air hops origin -> US gateway (e.g. SZX->ICN->DFW)

# Generic charter carrier used only when no air path to the US exists at all.
_FALLBACK_CARRIER = "AeroFreight Consolidators"
_FALLBACK_SERVICE = "AF-CHARTER"
# Ground carrier labels (no real road-carrier dataset exists in OpenFlights).
_GROUND_CARRIER = "Regional Trucking"
_GROUND_SERVICE = "Bonded Drayage"


# --------------------------------------------------------------------------- #
# Distance helper
# --------------------------------------------------------------------------- #
def _dist_km(a: geo.Airport, b: geo.Airport) -> float:
    """Great-circle (haversine) distance in km between two airports."""
    return geo.haversine_km(a.lat, a.lon, b.lat, b.lon)


# --------------------------------------------------------------------------- #
# AIR routing — BFS to US gateways within the hop budget.
# --------------------------------------------------------------------------- #
def _find_air_paths_to_us(origin: str) -> Dict[str, List[str]]:
    """BFS the real routes graph from ``origin`` out to ``MAX_AIR_HOPS`` hops.

    Returns ``{us_iata: [origin, ..., us_iata]}`` — for every US airport reachable
    within the hop budget, the shortest (fewest-hops) IATA path that reaches it.
    BFS guarantees the first path found to any node is a minimum-hop path, which is
    exactly what we want before distance is even considered.
    """
    reachable: Dict[str, List[str]] = {}
    # Queue of (current_node, path_so_far). path includes the current node.
    queue: deque[Tuple[str, List[str]]] = deque([(origin, [origin])])
    visited = {origin}

    while queue:
        node, path = queue.popleft()
        if len(path) - 1 >= MAX_AIR_HOPS:
            continue  # already at the hop limit; do not expand further
        for dest in geo.ROUTE_GRAPH.get(node, {}):
            if dest in visited:
                continue
            visited.add(dest)
            new_path = path + [dest]
            airport = geo.AIRPORTS.get(dest)
            if airport is not None and airport.is_us:
                # First time we reach this US airport == fewest-hop path to it.
                reachable.setdefault(dest, new_path)
            queue.append((dest, new_path))

    return reachable


def _edge_carrier(src: str, dst: str) -> str:
    """Pick a real operating-airline display name for the directed edge src->dst.

    The graph records every airline IATA code operating the lane; we pick one
    deterministically (alphabetically by resolved name) so the same lane always
    reports the same carrier, then resolve it to its airlines.dat display name.
    """
    codes = geo.ROUTE_GRAPH.get(src, {}).get(dst, set())
    if not codes:
        return _FALLBACK_CARRIER
    # Resolve every code to a name and choose deterministically.
    names = sorted(geo.airline_name(c) for c in codes)
    return names[0]


def _air_legs_for_path(path: List[str]) -> Tuple[List[Dict[str, str]], float, int]:
    """Turn an IATA path into wire air legs + total air distance + hop count.

    Each consecutive (src, dst) pair becomes one ``mode="air"`` leg whose carrier
    is the real operating airline and whose service is the route shorthand
    (e.g. "SZX-ICN"), since OpenFlights does not carry individual flight numbers.
    """
    legs: List[Dict[str, str]] = []
    total_km = 0.0
    for src, dst in zip(path, path[1:]):
        src_ap = geo.AIRPORTS[src]
        dst_ap = geo.AIRPORTS[dst]
        total_km += _dist_km(src_ap, dst_ap)
        legs.append(
            {
                "mode": "air",
                "carrier": _edge_carrier(src, dst),
                "service": f"{src}-{dst}",
                "from_node": src,
                "to_node": dst,
            }
        )
    return legs, total_km, len(path) - 1


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #
def _air_cost(chargeable_kg: float, dist_km: float) -> float:
    """Cost of an air movement of ``dist_km`` km at the documented air rates."""
    rate_per_kg = AIR_RATE_PER_KG_BASE + AIR_RATE_PER_KG_PER_1000KM * (dist_km / 1000.0)
    return chargeable_kg * rate_per_kg


def _ground_cost(chargeable_kg: float, dist_km: float) -> float:
    """Cost of a ground trucking leg of ``dist_km`` km at the documented rates."""
    return GROUND_BASE + GROUND_PER_KM * dist_km + chargeable_kg * GROUND_PER_KG


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #
def _air_hours(dist_km: float, hops: int) -> float:
    """Flight time for ``dist_km`` over ``hops`` legs incl. per-hop handling."""
    return dist_km / AIR_SPEED_KMH + AIR_HANDLING_HOURS_PER_HOP * max(hops, 1)


def _ground_days(dist_km: float) -> int:
    """Whole driving days to cover ``dist_km`` (at least 1 if there is a leg)."""
    if dist_km <= 0:
        return 0
    return max(1, math.ceil(dist_km / GROUND_KM_PER_DAY))


def _transit_days(air_hours: float, ground_days: int) -> int:
    """Combine air time, ground time and the fixed customs buffer into days."""
    return math.ceil(air_hours / 24.0) + ground_days + CUSTOMS_BUFFER_DAYS


# --------------------------------------------------------------------------- #
# Response assembly
# --------------------------------------------------------------------------- #
def _eta_iso(transit_days: int) -> str:
    """ETA as today + ``transit_days``, formatted YYYY-MM-DD."""
    return (datetime.date.today() + datetime.timedelta(days=transit_days)).isoformat()


def _meets_deadline(eta_iso: str, deadline_iso: str) -> bool:
    """ISO dates sort lexicographically, so a string compare is correct.

    An empty/missing deadline is treated optimistically as "met" rather than
    failing the whole quote.
    """
    if not deadline_iso:
        return True
    return eta_iso <= deadline_iso[:10]


def _build_response(
    legs: List[Dict[str, str]],
    total_cost: float,
    air_hours: float,
    ground_days: int,
    deadline_iso: str,
) -> Dict:
    """Assemble the frozen ``FreightResponse``-shaped dict."""
    transit_days = _transit_days(air_hours, ground_days)
    eta = _eta_iso(transit_days)
    return {
        "legs": legs,
        "total_cost_usd": round(total_cost, 2),
        "transit_days": transit_days,
        "eta_iso": eta,
        "meets_deadline": _meets_deadline(eta, deadline_iso),
    }


def _fallback_quote(
    origin_ap: geo.Airport,
    dest_ap: geo.Airport,
    chargeable_kg: float,
    deadline_iso: str,
) -> Dict:
    """Single generic great-circle air leg for routes with no graph path to the US.

    Priced and timed by the very same model as a real lane (just on the direct
    origin->destination great-circle distance), and flagged with a generic
    charter carrier so the orchestrator can narrate it honestly.
    """
    dist = _dist_km(origin_ap, dest_ap)
    leg = {
        "mode": "air",
        "carrier": _FALLBACK_CARRIER,
        "service": _FALLBACK_SERVICE,
        "from_node": origin_ap.iata,
        "to_node": dest_ap.iata,
    }
    cost = _air_cost(chargeable_kg, dist)
    return _build_response([leg], cost, _air_hours(dist, hops=1), ground_days=0,
                           deadline_iso=deadline_iso)


# --------------------------------------------------------------------------- #
# Public API (called by mock_api/server.py -> POST /freight/quote)
# --------------------------------------------------------------------------- #
def quote(origin: str, destination: str, weight_kg: float,
          deadline_iso: str) -> dict:
    """Route + price a freight move over the real OpenFlights network.

    See the module docstring for the full pipeline. The returned dict matches the
    frozen ``FreightResponse`` contract exactly.
    """
    origin = (origin or "").strip().upper()
    destination = (destination or "").strip().upper()

    origin_ap = geo.get_airport(origin)
    dest_ap = geo.get_airport(destination)

    # No dimensional data -> chargeable weight is gross weight, floored at 1 kg.
    chargeable_kg = max(float(weight_kg), 1.0) if weight_kg else 1.0

    # If either endpoint is unknown we cannot geocode/route it. Emit a generic
    # great-circle leg using whatever endpoints we do have (or the raw codes),
    # so the contract is still honoured rather than raising into the server.
    if origin_ap is None or dest_ap is None:
        o_lat = origin_ap.lat if origin_ap else 0.0
        o_lon = origin_ap.lon if origin_ap else 0.0
        d_lat = dest_ap.lat if dest_ap else 0.0
        d_lon = dest_ap.lon if dest_ap else 0.0
        dist = geo.haversine_km(o_lat, o_lon, d_lat, d_lon)
        leg = {
            "mode": "air",
            "carrier": _FALLBACK_CARRIER,
            "service": _FALLBACK_SERVICE,
            "from_node": origin,
            "to_node": destination,
        }
        return _build_response([leg], _air_cost(chargeable_kg, dist),
                               _air_hours(dist, hops=1), 0, deadline_iso)

    # ----------------------------------------------------------------------- #
    # AIR: enumerate every US gateway reachable within the hop budget, plus the
    # destination itself if it is air-reachable (zero ground distance).
    # ----------------------------------------------------------------------- #
    reachable_us = _find_air_paths_to_us(origin)

    if not reachable_us:
        # No air path to any US airport -> generic charter great-circle leg.
        return _fallback_quote(origin_ap, dest_ap, chargeable_kg, deadline_iso)

    # Score each gateway by air(origin->gateway) + ground(gateway->destination)
    # great-circle distance and keep the minimiser. The destination, when it is a
    # reachable US airport, naturally wins ties via its zero ground distance.
    best_gateway: Optional[str] = None
    best_path: List[str] = []
    best_score = math.inf
    for gw, path in reachable_us.items():
        gw_ap = geo.AIRPORTS[gw]
        _legs, air_km, _hops = _air_legs_for_path(path)
        ground_km = _dist_km(gw_ap, dest_ap)
        score = air_km + ground_km
        if score < best_score:
            best_score = score
            best_gateway = gw
            best_path = path

    # Build the air legs for the winning path.
    legs, air_km, hops = _air_legs_for_path(best_path)
    total_cost = _air_cost(chargeable_kg, air_km)
    air_hours = _air_hours(air_km, hops)

    # ----------------------------------------------------------------------- #
    # GROUND: add a trucking leg gateway -> destination unless we already landed
    # at the destination airport.
    # ----------------------------------------------------------------------- #
    ground_days = 0
    if best_gateway != destination:
        gw_ap = geo.AIRPORTS[best_gateway]
        ground_km = _dist_km(gw_ap, dest_ap)
        total_cost += _ground_cost(chargeable_kg, ground_km)
        ground_days = _ground_days(ground_km)
        legs.append(
            {
                "mode": "ground",
                "carrier": _GROUND_CARRIER,
                "service": _GROUND_SERVICE,
                "from_node": best_gateway,
                "to_node": destination,
            }
        )

    return _build_response(legs, total_cost, air_hours, ground_days, deadline_iso)


# --------------------------------------------------------------------------- #
# Manual smoke test: python -m mock_api.carrier_data
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import json

    for o, d, w in (("SZX", "AUS", 200.0), ("FRA", "JFK", 80.0)):
        print(f"=== {o} -> {d}, {w} kg ===")
        print(json.dumps(quote(o, d, w, "2026-12-31"), indent=2))
