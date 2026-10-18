"""Configuration models, loaded from a single YAML file."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator


def _parse_clock(value: object) -> int | None:
    """Parse "HH:MM" (or an int hour) into minutes after midnight.

    Hours up to 47 are allowed so arrivals after midnight can be expressed,
    e.g. latest_arrival: "25:30" means 01:30 on the following day.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("time must be 'HH:MM' or an hour number")
    if isinstance(value, int):
        hours, minutes = value, 0
    else:
        m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*", str(value))
        if not m:
            raise ValueError(f"invalid time {value!r}, expected 'HH:MM'")
        hours, minutes = int(m.group(1)), int(m.group(2) or 0)
    if not (0 <= hours <= 47 and 0 <= minutes <= 59):
        raise ValueError(f"time out of range: {value!r}")
    return hours * 60 + minutes


Clock = Annotated[int | None, BeforeValidator(_parse_clock)]


def _iata(value: object) -> str:
    code = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError(f"invalid IATA airport code: {value!r}")
    return code


Iata = Annotated[str, BeforeValidator(_iata)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TimeWindow(_Strict):
    """Local-time constraints for one flight. Arrival is relative to the departure date."""

    earliest_departure: Clock = None
    latest_departure: Clock = None
    earliest_arrival: Clock = None
    latest_arrival: Clock = None


class LegTimeRules(_Strict):
    default: TimeWindow = TimeWindow()
    by_date: dict[date, TimeWindow] = {}


class TimeRules(_Strict):
    outbound: LegTimeRules = LegTimeRules()
    return_: LegTimeRules = Field(default=LegTimeRules(), alias="return")


class Filters(_Strict):
    max_stops: int | None = None
    max_duration_hours: float | None = None
    min_layover_minutes: int | None = None
    max_layover_minutes: int | None = None
    carry_on_bags: int | None = None
    checked_bags: int | None = None
    hide_self_transfer: bool | None = None
    self_transfer_min_layover_minutes: int | None = None
    exclude_basic_economy: bool | None = None
    seat: Literal["economy", "premium-economy", "business", "first"] | None = None


def _flex(value: object) -> tuple[int, int]:
    """N means ±N days; [a, b] means from a to b days relative to the date (e.g. [0, 1] = same day or one later)."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise ValueError("flexibility must be >= 0, or a [min, max] range like [-1, 0]")
        return (-value, value)
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, int) for v in value):
        lo, hi = value
        if lo > hi:
            raise ValueError(f"flexibility range {list(value)}: min is greater than max")
        return (lo, hi)
    raise ValueError(f"invalid flexibility {value!r}: use a number (±N days) or [min, max]")


Flex = Annotated[tuple[int, int], BeforeValidator(_flex)]


class Trip(_Strict):
    outbound_date: date
    return_date: date
    flex_out: Flex = (0, 0)
    flex_ret: Flex = (0, 0)
    min_nights: int = Field(0, ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> Trip:
        if self.return_date < self.outbound_date:
            raise ValueError("return_date is before outbound_date")
        return self


class City(_Strict):
    name: str
    airports: list[Iata] = Field(min_length=1)


class Traveler(_Strict):
    name: str
    from_: list[Iata] = Field(alias="from", min_length=1)
    to: list[Iata] = Field(min_length=1)
    time_rules: TimeRules = TimeRules()
    filters: Filters = Filters()


class ScoreWeights(_Strict):
    eur_per_travel_hour: float = 10.0
    eur_per_stop: float = 20.0
    eur_per_hour_together: float = 5.0


class Ranking(_Strict):
    sort_by: Literal["total_price", "score"] = "total_price"
    score_weights: ScoreWeights = ScoreWeights()
    max_arrival_gap_hours: float | None = None
    max_departure_gap_hours: float | None = None
    options_per_leg: int = Field(5, ge=1)
    top_combos_per_city: int = Field(3, ge=1)
    csv_combos_per_city: int = Field(50, ge=1)
    summary_top_overall: int = Field(30, ge=1)
    summary_overall_max_per_city_dates: int = Field(3, ge=1)
    summary_top_per_city: int = Field(5, ge=1)


class Search(_Strict):
    delay_seconds: tuple[float, float] = (2.0, 4.0)
    cache_ttl_hours: float = 6.0
    retries: int = Field(3, ge=0)
    # When Google blocks us: wait these minutes before each new attempt (last value repeats),
    # and give up (writing partial results) after this much total waiting.
    block_wait_minutes: list[float] = Field(default=[2, 5, 10, 15], min_length=1)
    block_max_wait_minutes: float = 90
    # Google returns only a short list per search. Splitting each day into departure-time
    # bands (one search each, merged) finds 2-3x more flights. null = one search per day.
    time_bands: list[tuple[int, int]] | None = [(0, 9), (9, 13), (13, 17), (17, 23)]

    @field_validator("time_bands")
    @classmethod
    def _bands(cls, v):
        for lo, hi in v or []:
            if not (0 <= lo <= hi <= 23):
                raise ValueError(f"time band {[lo, hi]}: hours must satisfy 0 <= start <= end <= 23")
        return v
    language: str = "en-GB"
    proxy: str | None = None

    @field_validator("delay_seconds", mode="before")
    @classmethod
    def _delay(cls, v: object) -> object:
        if isinstance(v, (int, float)):
            return (v, v)
        return v


class Config(_Strict):
    currency: str = "EUR"
    trip: Trip
    cities: list[City] = Field(min_length=1)
    travelers: list[Traveler] = Field(min_length=1)
    time_rules: TimeRules = TimeRules()
    filters: Filters = Filters()
    ranking: Ranking = Ranking()
    search: Search = Search()
    output_dir: str = "results"

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _unique_names(self) -> Config:
        for label, names in (("traveler", [t.name for t in self.travelers]), ("city", [c.name for c in self.cities])):
            if len(set(names)) != len(names):
                raise ValueError(f"{label} names must be unique")
        return self


def load_config(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Config.model_validate(data)
