"""Deep links to check or book a leg on Skyscanner (the Google Flights link comes from the provider)."""

from __future__ import annotations

from datetime import date


def skyscanner_url(from_airport: str, to_airport: str, day: date, adults: int = 1) -> str:
    return (
        f"https://www.skyscanner.net/transport/flights/{from_airport.lower()}/{to_airport.lower()}/"
        f"{day:%y%m%d}/?adultsv2={adults}&cabinclass=economy&rtn=0"
    )
