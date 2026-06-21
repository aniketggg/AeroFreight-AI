from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Iterable, Literal

from routing_models import RouteData, RoutingRequest

Mode = Literal["AIR", "SHIP"]

TRUCKING_RATE_USD_PER_MILE = 3.00
ROAD_DISTANCE_FACTOR = 1.18
AIR_VOLUMETRIC_KG_PER_CBM = 167.0


class UnsupportedLocationError(ValueError):
    """Raised when the demo location registry cannot resolve a city."""


@dataclass(frozen=True)
class Hub:
    code: str
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class CandidateQuote:
    mode: Mode
    hub: Hub
    route_nodes: list[str]
    countries_visited: list[str]
    freight_cost_usd: float
    inland_trucking_cost_usd: float
    tolls_and_route_tariffs_usd: float

    @property
    def transport_subtotal_usd(self) -> float:
        return (
            self.freight_cost_usd
            + self.inland_trucking_cost_usd
            + self.tolls_and_route_tariffs_usd
        )


CITY_COORDINATES: dict[tuple[str, str], tuple[float, float]] = {
    ("CN", "shenzhen"): (22.5431, 114.0579),
    ("CN", "shanghai"): (31.2304, 121.4737),
    ("CN", "beijing"): (39.9042, 116.4074),
    ("IN", "mumbai"): (19.0760, 72.8777),
    ("IN", "delhi"): (28.6139, 77.2090),
    ("JP", "tokyo"): (35.6762, 139.6503),
    ("DE", "frankfurt"): (50.1109, 8.6821),
    ("GB", "london"): (51.5072, -0.1276),
    ("US", "austin"): (30.2672, -97.7431),
    ("US", "los angeles"): (34.0522, -118.2437),
    ("US", "san francisco"): (37.7749, -122.4194),
    ("US", "new york"): (40.7128, -74.0060),
    ("US", "chicago"): (41.8781, -87.6298),
    ("US", "houston"): (29.7604, -95.3698),
    ("US", "miami"): (25.7617, -80.1918),
    ("US", "seattle"): (47.6062, -122.3321),
    ("US", "atlanta"): (33.7490, -84.3880),
}

AIR_HUBS = (
    Hub("LAX", "Los Angeles International Airport", 33.9416, -118.4085),
    Hub("DFW", "Dallas/Fort Worth International Airport", 32.8998, -97.0403),
    Hub("JFK", "John F. Kennedy International Airport", 40.6413, -73.7781),
    Hub("ORD", "O'Hare International Airport", 41.9742, -87.9073),
    Hub("IAH", "George Bush Intercontinental Airport", 29.9902, -95.3368),
)

SHIP_HUBS = (
    Hub("USLAX", "Port of Los Angeles", 33.7405, -118.2775),
    Hub("USNYC", "Port of New York and New Jersey", 40.6840, -74.1500),
    Hub("USHOU", "Port Houston", 29.7300, -95.2600),
    Hub("USSAV", "Port of Savannah", 32.0809, -81.0912),
    Hub("USSEA", "Port of Seattle", 47.6026, -122.3393),
)


def _normalize_country(value: object) -> str:
    return str(value or "").strip().upper()


def _normalize_city(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def resolve_coordinates(location: dict) -> tuple[float, float]:
    country = _normalize_country(location.get("country"))
    city = _normalize_city(location.get("city"))
    coordinates = CITY_COORDINATES.get((country, city))
    if coordinates is None:
        raise UnsupportedLocationError(
            f"No coordinates configured for city={location.get('city')!r}, "
            f"country={location.get('country')!r}."
        )
    return coordinates


def haversine_km(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    lat1, lon1 = map(radians, first)
    lat2, lon2 = map(radians, second)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    a = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )
    return 2 * 6371.0088 * asin(sqrt(a))


def _road_miles(distance_km: float) -> float:
    return distance_km * 0.621371 * ROAD_DISTANCE_FACTOR


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _route_metadata(request: RoutingRequest, hub: Hub) -> tuple[list[str], list[str]]:
    shipment = request.shipment
    origin_city = str(shipment.origin.get("city", "Origin"))
    destination_city = str(shipment.destination.get("city", "Destination"))
    origin_country = _normalize_country(shipment.origin.get("country"))
    destination_country = _normalize_country(shipment.destination.get("country"))

    return (
        [origin_city, hub.code, destination_city],
        _unique([origin_country, destination_country]),
    )


def build_air_quote(request: RoutingRequest) -> CandidateQuote:
    shipment = request.shipment
    origin = resolve_coordinates(shipment.origin)
    destination = resolve_coordinates(shipment.destination)
    chargeable_weight_kg = max(
        shipment.total_weight_kg,
        shipment.total_volume_cbm * AIR_VOLUMETRIC_KG_PER_CBM,
    )

    candidates: list[CandidateQuote] = []
    for hub in AIR_HUBS:
        international_km = haversine_km(origin, (hub.latitude, hub.longitude))
        inland_km = haversine_km((hub.latitude, hub.longitude), destination)

        # Simulated hackathon pricing, not a live carrier quote.
        freight = 125.0 + chargeable_weight_kg * (
            1.10 + 0.00018 * international_km
        )
        route_charges = 95.0 + 0.02 * chargeable_weight_kg
        inland = _road_miles(inland_km) * TRUCKING_RATE_USD_PER_MILE
        route_nodes, countries = _route_metadata(request, hub)

        candidates.append(
            CandidateQuote(
                mode="AIR",
                hub=hub,
                route_nodes=route_nodes,
                countries_visited=countries,
                freight_cost_usd=round(freight, 2),
                inland_trucking_cost_usd=round(inland, 2),
                tolls_and_route_tariffs_usd=round(route_charges, 2),
            )
        )

    return min(candidates, key=lambda quote: quote.transport_subtotal_usd)


def build_ship_quote(request: RoutingRequest) -> CandidateQuote:
    shipment = request.shipment
    origin = resolve_coordinates(shipment.origin)
    destination = resolve_coordinates(shipment.destination)
    chargeable_units = max(
        shipment.total_volume_cbm,
        shipment.total_weight_kg / 1000.0,
    )

    candidates: list[CandidateQuote] = []
    for hub in SHIP_HUBS:
        international_km = haversine_km(origin, (hub.latitude, hub.longitude))
        inland_km = haversine_km((hub.latitude, hub.longitude), destination)

        # Simulated hackathon pricing, not a live ocean-carrier quote.
        freight = 350.0 + chargeable_units * (45.0 + 0.017 * international_km)
        route_charges = 240.0 + 15.0 * chargeable_units
        inland = _road_miles(inland_km) * TRUCKING_RATE_USD_PER_MILE
        route_nodes, countries = _route_metadata(request, hub)

        candidates.append(
            CandidateQuote(
                mode="SHIP",
                hub=hub,
                route_nodes=route_nodes,
                countries_visited=countries,
                freight_cost_usd=round(freight, 2),
                inland_trucking_cost_usd=round(inland, 2),
                tolls_and_route_tariffs_usd=round(route_charges, 2),
            )
        )

    return min(candidates, key=lambda quote: quote.transport_subtotal_usd)


def calculate_route(request: RoutingRequest) -> RouteData:
    preference = request.econ.transport_preference

    if preference == "AIR":
        selected = build_air_quote(request)
    elif preference == "SHIP":
        selected = build_ship_quote(request)
    else:
        air_quote = build_air_quote(request)
        ship_quote = build_ship_quote(request)
        selected = min(
            (air_quote, ship_quote),
            key=lambda quote: quote.transport_subtotal_usd,
        )

    entry_tax = round(request.econ.base_entry_tax_usd, 2)
    transport_subtotal = round(selected.transport_subtotal_usd, 2)
    total_landed_cost = round(transport_subtotal + entry_tax, 2)

    return RouteData(
        selected_mode=selected.mode,
        optimal_route_nodes=selected.route_nodes,
        countries_visited=selected.countries_visited,
        freight_cost_usd=selected.freight_cost_usd,
        inland_trucking_cost_usd=selected.inland_trucking_cost_usd,
        tolls_and_route_tariffs_usd=selected.tolls_and_route_tariffs_usd,
        entry_tax_usd=entry_tax,
        freight_and_toll_cost_usd=transport_subtotal,
        total_landed_cost_usd=total_landed_cost,
    )
