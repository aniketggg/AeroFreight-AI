from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


AIRPORTS_CSV = Path(__file__).parent / "data" / "airports.csv"


@dataclass(frozen=True)
class Airport:
    code: str
    name: str
    city: str
    country: str
    latitude: float
    longitude: float


@lru_cache(maxsize=1)
def load_airports() -> tuple[Airport, ...]:
    if not AIRPORTS_CSV.exists():
        raise FileNotFoundError(
            f"Airport CSV was not found at {AIRPORTS_CSV}"
        )

    airports: list[Airport] = []

    with AIRPORTS_CSV.open(
        newline="",
        encoding="utf-8",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            airport_type = (row.get("type") or "").strip()
            scheduled = (
                row.get("scheduled_service") or ""
            ).strip().lower()
            code = (row.get("iata_code") or "").strip().upper()
            country = (
                row.get("iso_country") or ""
            ).strip().upper()
            latitude = (row.get("latitude_deg") or "").strip()
            longitude = (row.get("longitude_deg") or "").strip()

            if airport_type not in {
                "large_airport",
                "medium_airport",
            }:
                continue

            if scheduled != "yes":
                continue

            if not all([code, country, latitude, longitude]):
                continue

            try:
                airports.append(
                    Airport(
                        code=code,
                        name=(row.get("name") or code).strip(),
                        city=(
                            row.get("municipality") or ""
                        ).strip(),
                        country=country,
                        latitude=float(latitude),
                        longitude=float(longitude),
                    )
                )
            except ValueError:
                continue

    if not airports:
        raise RuntimeError(
            "No usable airports were loaded from airports.csv"
        )

    return tuple(airports)


def airports_in_country(country: str) -> tuple[Airport, ...]:
    country = country.strip().upper()

    return tuple(
        airport
        for airport in load_airports()
        if airport.country == country
    )
