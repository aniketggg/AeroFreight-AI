"""Geo + route-graph loading for the Freight-Router vertical.

This module turns the raw OpenFlights open-data CSVs in ``draft/data/`` into the
in-memory structures the pricing/routing engine needs:

    * ``AIRPORTS``  : IATA -> :class:`Airport` (name, city, country, lat, lon, US?)
    * ``ROUTE_GRAPH``: IATA -> { dest IATA -> set(operating 2-letter airline IATA) }
    * ``AIRLINES``  : 2-letter IATA -> carrier display name

Everything is loaded exactly once, at import time, off ``os.path`` paths relative
to this file so the engine works regardless of the process CWD. A missing data
file raises a clear :class:`FileNotFoundError` rather than silently degrading.

The CSVs are parsed with the :mod:`csv` module on purpose: airport names contain
commas (e.g. "Dallas Fort Worth International Airport"), so a naive ``split(",")``
would corrupt every field after the name. OpenFlights uses the literal two-char
sequence ``\\N`` for "no value" (e.g. an airport with no IATA code); we treat that,
plus blanks and ``-``, as "missing".
"""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from typing import Dict, Set


# --------------------------------------------------------------------------- #
# Paths — resolved relative to this module so CWD never matters.
# --------------------------------------------------------------------------- #
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_AIRPORTS_FILE = os.path.join(_DATA_DIR, "airports.dat")
_ROUTES_FILE = os.path.join(_DATA_DIR, "routes.dat")
_AIRLINES_FILE = os.path.join(_DATA_DIR, "airlines.dat")

# OpenFlights "no value" sentinel plus the other empties we want to ignore.
_MISSING = {"", "-", "\\N", "N/A"}


# --------------------------------------------------------------------------- #
# Airport record
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Airport:
    """A single airport node with the fields the router actually uses."""

    iata: str
    name: str
    city: str
    country: str
    lat: float
    lon: float

    @property
    def is_us(self) -> bool:
        """True for US airports (the import gateways we route freight through)."""
        return self.country == "United States"


# --------------------------------------------------------------------------- #
# Great-circle distance
# --------------------------------------------------------------------------- #
_EARTH_RADIUS_KM = 6371.0088  # IUGG mean Earth radius


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two (lat, lon) points.

    Standard haversine formula on a spherical Earth — accurate to well within a
    percent at the inter-continental distances we care about, and the only
    distance signal we have (OpenFlights ships coordinates, not road networks).
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def _require(path: str) -> None:
    """Fail fast, with a clear message, if a required data file is absent."""
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"Required OpenFlights data file is missing: {path}. "
            "Expected airports.dat, routes.dat and airlines.dat under draft/data/."
        )


def _load_airports() -> Dict[str, Airport]:
    """Parse airports.dat -> {IATA: Airport}.

    Column layout (0-indexed): 0 AirportID, 1 Name, 2 City, 3 Country, 4 IATA,
    5 ICAO, 6 Lat, 7 Lon, 8 Alt, 9 Tz, 10 DST, 11 TzDb, 12 Type, 13 Source.
    Rows without a usable IATA code or with non-numeric coordinates are skipped.
    """
    _require(_AIRPORTS_FILE)
    airports: Dict[str, Airport] = {}
    with open(_AIRPORTS_FILE, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 8:
                continue
            iata = row[4].strip().upper()
            if iata in _MISSING:
                continue
            try:
                lat = float(row[6])
                lon = float(row[7])
            except ValueError:
                continue  # malformed coordinates -> unusable as a routing node
            # First definition wins; OpenFlights occasionally repeats an IATA on
            # decommissioned entries, and the canonical airport tends to come first.
            if iata not in airports:
                airports[iata] = Airport(
                    iata=iata,
                    name=row[1].strip(),
                    city=row[2].strip(),
                    country=row[3].strip(),
                    lat=lat,
                    lon=lon,
                )
    return airports


def _load_airlines() -> Dict[str, str]:
    """Parse airlines.dat -> {2-letter IATA: airline name}.

    Column layout (0-indexed): 0 AirlineID, 1 Name, 2 Alias, 3 IATA, 4 ICAO,
    5 Callsign, 6 Country, 7 Active.

    IATA codes are *not* unique in this file (e.g. "OZ" is both active Asiana and
    a defunct US carrier). We prefer the ``Active == "Y"`` record so the carrier
    name shown on a quote is the airline actually flying the lane today; a code is
    only filled from an inactive record if no active one exists.
    """
    _require(_AIRLINES_FILE)
    airlines: Dict[str, str] = {}
    seen_active: Set[str] = set()
    with open(_AIRLINES_FILE, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 8:
                continue
            iata = row[3].strip().upper()
            name = row[1].strip()
            if iata in _MISSING or name in _MISSING:
                continue
            active = row[7].strip().upper() == "Y"
            if active:
                airlines[iata] = name  # active record always wins
                seen_active.add(iata)
            elif iata not in airlines:
                airlines[iata] = name  # provisional, until/unless an active one shows up
    return airlines


def _load_route_graph(airports: Dict[str, Airport]) -> Dict[str, Dict[str, Set[str]]]:
    """Parse routes.dat into a directed adjacency map.

    Column layout (0-indexed): 0 Airline(2-letter IATA), 1 AirlineID, 2 Source,
    3 SourceID, 4 Dest, 5 DestID, 6 Codeshare, 7 Stops, 8 Equipment.

    Returns ``{source_iata: {dest_iata: {airline_iata, ...}}}``. We only keep
    direct hops (``Stops == 0``) between airports we actually have coordinates for,
    and we record every operating airline code on each edge so the router can name
    a real carrier for the leg.
    """
    _require(_ROUTES_FILE)
    graph: Dict[str, Dict[str, Set[str]]] = {}
    with open(_ROUTES_FILE, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 8:
                continue
            airline = row[0].strip().upper()
            src = row[2].strip().upper()
            dst = row[4].strip().upper()
            stops = row[7].strip()
            if stops not in ("", "0"):
                continue  # only non-stop legs; multi-stop rows would distort distance
            if src in _MISSING or dst in _MISSING or src == dst:
                continue
            # Require both endpoints to be geocoded airports so distance/pricing work.
            if src not in airports or dst not in airports:
                continue
            graph.setdefault(src, {}).setdefault(dst, set())
            if airline not in _MISSING:
                graph[src][dst].add(airline)
    return graph


# --------------------------------------------------------------------------- #
# Module-level singletons (loaded once at import).
# --------------------------------------------------------------------------- #
AIRPORTS: Dict[str, Airport] = _load_airports()
AIRLINES: Dict[str, str] = _load_airlines()
ROUTE_GRAPH: Dict[str, Dict[str, Set[str]]] = _load_route_graph(AIRPORTS)


# --------------------------------------------------------------------------- #
# Convenience accessors
# --------------------------------------------------------------------------- #
def get_airport(iata: str) -> Airport | None:
    """Look up an :class:`Airport` by IATA code (case-insensitive)."""
    if not iata:
        return None
    return AIRPORTS.get(iata.strip().upper())


def airline_name(code: str) -> str:
    """Map a 2-letter airline IATA code to a display name, or echo the code.

    If the code is unknown (not in airlines.dat) we return the raw code rather
    than an empty string, so the leg still carries a meaningful carrier label.
    """
    code = (code or "").strip().upper()
    return AIRLINES.get(code, code)
