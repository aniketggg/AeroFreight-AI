from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


PORTS_FILE = Path(__file__).parent / "data" / "ports.csv"

COUNTRY_ALIASES = {
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "USA": "US",
    "CHINA": "CN",
    "PEOPLE'S REPUBLIC OF CHINA": "CN",
}


@dataclass(frozen=True)
class Port:
    code: str
    name: str
    alternate_name: str
    country: str
    latitude: float
    longitude: float
    first_port_of_entry: bool
    has_container_facility: bool


def normalize_country(value: Any) -> str:
    text = str(value or "").strip().upper()

    if len(text) == 2:
        return text

    return COUNTRY_ALIASES.get(text, text)


def _is_yes(value: Any) -> bool:
    return str(value or "").strip().casefold() in {
        "y",
        "yes",
        "true",
        "1",
    }


@lru_cache(maxsize=1)
def load_ports() -> tuple[Port, ...]:
    if not PORTS_FILE.exists():
        raise FileNotFoundError(
            f"Port dataset was not found at {PORTS_FILE}"
        )

    ports: list[Port] = []

    with PORTS_FILE.open(
        newline="",
        encoding="utf-8-sig",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            try:
                latitude = float(row["Latitude"])
                longitude = float(row["Longitude"])
            except (TypeError, ValueError, KeyError):
                continue

            name = str(
                row.get("Main Port Name") or ""
            ).strip()

            if not name:
                continue

            country = normalize_country(
                row.get("Country Code")
            )

            raw_code = str(
                row.get("UN/LOCODE") or ""
            ).strip().upper()

            code = "".join(
                character
                for character in raw_code
                if character.isalnum()
            )

            if not code:
                code = (
                    "WPI"
                    + str(
                        row.get("World Port Index Number")
                        or ""
                    ).strip()
                )

            ports.append(
                Port(
                    code=code,
                    name=name,
                    alternate_name=str(
                        row.get("Alternate Port Name")
                        or ""
                    ).strip(),
                    country=country,
                    latitude=latitude,
                    longitude=longitude,
                    first_port_of_entry=_is_yes(
                        row.get("First Port of Entry")
                    ),
                    has_container_facility=_is_yes(
                        row.get("Facilities - Container")
                    ),
                )
            )

    if not ports:
        raise RuntimeError(
            "No usable ports were loaded from ports.csv"
        )

    return tuple(ports)


def ports_in_country(country: Any) -> tuple[Port, ...]:
    normalized_country = normalize_country(country)

    return tuple(
        port
        for port in load_ports()
        if port.country == normalized_country
    )
