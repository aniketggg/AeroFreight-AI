from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


CITIES_FILE = Path(__file__).parent / "data" / "cities1000.txt"

COUNTRY_ALIASES = {
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "USA": "US",
    "U.S.": "US",
    "CHINA": "CN",
    "PEOPLE'S REPUBLIC OF CHINA": "CN",
    "INDIA": "IN",
    "CANADA": "CA",
    "MEXICO": "MX",
    "UNITED KINGDOM": "GB",
    "UK": "GB",
    "GERMANY": "DE",
    "FRANCE": "FR",
    "JAPAN": "JP",
    "SOUTH KOREA": "KR",
    "REPUBLIC OF KOREA": "KR",
    "SINGAPORE": "SG",
    "AUSTRALIA": "AU",
    "BRAZIL": "BR",
}

US_STATE_ALIASES = {
    "ALABAMA": "AL",
    "ALASKA": "AK",
    "ARIZONA": "AZ",
    "ARKANSAS": "AR",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "CONNECTICUT": "CT",
    "DELAWARE": "DE",
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "HAWAII": "HI",
    "IDAHO": "ID",
    "ILLINOIS": "IL",
    "INDIANA": "IN",
    "IOWA": "IA",
    "KANSAS": "KS",
    "KENTUCKY": "KY",
    "LOUISIANA": "LA",
    "MAINE": "ME",
    "MARYLAND": "MD",
    "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI",
    "MINNESOTA": "MN",
    "MISSISSIPPI": "MS",
    "MISSOURI": "MO",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM",
    "NEW YORK": "NY",
    "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND",
    "OHIO": "OH",
    "OKLAHOMA": "OK",
    "OREGON": "OR",
    "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "UTAH": "UT",
    "VERMONT": "VT",
    "VIRGINIA": "VA",
    "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC",
}


@dataclass(frozen=True)
class City:
    name: str
    ascii_name: str
    country: str
    admin1: str
    latitude: float
    longitude: float
    population: int


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def normalize_country(value: Any) -> str:
    text = str(value or "").strip()
    upper = text.upper()

    if len(upper) == 2:
        return upper

    return COUNTRY_ALIASES.get(upper, upper)


def _normalize_state(value: Any, country: str) -> str:
    upper = str(value or "").strip().upper()

    if country == "US":
        return US_STATE_ALIASES.get(upper, upper)

    return upper


@lru_cache(maxsize=1)
def _load_city_index() -> dict[tuple[str, str], tuple[City, ...]]:
    if not CITIES_FILE.exists():
        raise FileNotFoundError(
            f"City dataset not found at {CITIES_FILE}"
        )

    index: dict[tuple[str, str], list[City]] = {}

    with CITIES_FILE.open(
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.reader(file, delimiter="\t")

        for row in reader:
            if len(row) < 15:
                continue

            try:
                city = City(
                    name=row[1].strip(),
                    ascii_name=row[2].strip(),
                    latitude=float(row[4]),
                    longitude=float(row[5]),
                    country=row[8].strip().upper(),
                    admin1=row[10].strip().upper(),
                    population=int(row[14] or 0),
                )
            except (ValueError, IndexError):
                continue

            names = {
                _normalize_text(city.name),
                _normalize_text(city.ascii_name),
            }

            for name in names:
                if not name:
                    continue

                index.setdefault(
                    (name, city.country),
                    [],
                ).append(city)

    return {
        key: tuple(values)
        for key, values in index.items()
    }


def find_city(location: dict[str, Any]) -> City:
    city_name = _normalize_text(location.get("city"))
    country = normalize_country(location.get("country"))
    state = _normalize_state(
        location.get("state"),
        country,
    )

    if not city_name:
        raise ValueError("The location must include a city.")

    if len(country) != 2:
        raise ValueError(
            f"Could not normalize country: "
            f"{location.get('country')!r}"
        )

    candidates = list(
        _load_city_index().get(
            (city_name, country),
            (),
        )
    )

    if not candidates:
        raise ValueError(
            f"City not found in cities1000.txt: "
            f"{location.get('city')}, {country}"
        )

    if state:
        state_matches = [
            city
            for city in candidates
            if city.admin1 == state
        ]

        if state_matches:
            candidates = state_matches

    return max(
        candidates,
        key=lambda city: city.population,
    )


def find_city_coordinates(
    location: dict[str, Any],
) -> tuple[float, float]:
    city = find_city(location)
    return city.latitude, city.longitude
