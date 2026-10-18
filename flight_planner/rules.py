"""Resolve the effective time windows and filters for a traveler's leg, and check flights against them.

Precedence (later wins, field by field):
  global default < traveler default < global by_date < traveler by_date
Filters: global filters < traveler filters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal

from .config import Config, Filters, LegTimeRules, TimeRules, TimeWindow, Traveler
from .models import FlightOption, LegQuery

Direction = Literal["outbound", "return"]

FILTER_DEFAULTS = Filters(
    max_stops=None,
    max_duration_hours=None,
    min_layover_minutes=None,
    max_layover_minutes=None,
    carry_on_bags=0,
    checked_bags=0,
    hide_self_transfer=True,
    self_transfer_min_layover_minutes=None,
    exclude_basic_economy=False,
    seat="economy",
)


@dataclass(frozen=True)
class LegRules:
    window: TimeWindow
    filters: Filters


def _merge(base: TimeWindow | Filters, *overrides: TimeWindow | Filters | None):
    data = base.model_dump()
    for o in overrides:
        if o is not None:
            data.update({k: v for k, v in o.model_dump(exclude_unset=True).items() if v is not None})
    return type(base).model_construct(**data)  # values are already validated


def _leg(rules: TimeRules, direction: Direction) -> LegTimeRules:
    return rules.outbound if direction == "outbound" else rules.return_


def resolve(cfg: Config, traveler: Traveler, direction: Direction, day: date) -> LegRules:
    g, t = _leg(cfg.time_rules, direction), _leg(traveler.time_rules, direction)
    window = _merge(TimeWindow(), g.default, t.default, g.by_date.get(day), t.by_date.get(day))
    filters = _merge(FILTER_DEFAULTS, cfg.filters, traveler.filters)
    return LegRules(window=window, filters=filters)


def _floor_hour(minutes: int | None) -> int | None:
    if minutes is None:
        return None
    return min(minutes // 60, 23)


def _ceil_hour(minutes: int | None) -> int | None:
    # Google only accepts whole hours on the same day; anything past 23:00 is left to the local check.
    if minutes is None or minutes >= 23 * 60:
        return None
    return math.ceil(minutes / 60)


def build_query(cfg: Config, rules: LegRules, from_airport: str, to_airport: str, day: date) -> LegQuery:
    """Rounds time bounds outward so Google never drops a flight that the exact local check would keep."""
    w, f = rules.window, rules.filters
    return LegQuery(
        from_airport=from_airport,
        to_airport=to_airport,
        day=day,
        earliest_departure_hour=_floor_hour(w.earliest_departure),
        latest_departure_hour=_ceil_hour(w.latest_departure),
        earliest_arrival_hour=_floor_hour(w.earliest_arrival) if (w.earliest_arrival or 0) < 24 * 60 else None,
        latest_arrival_hour=_ceil_hour(w.latest_arrival),
        max_stops=f.max_stops,
        max_duration_minutes=math.ceil(f.max_duration_hours * 60) if f.max_duration_hours else None,
        min_layover_minutes=f.min_layover_minutes,
        max_layover_minutes=f.max_layover_minutes,
        carry_on_bags=f.carry_on_bags or 0,
        checked_bags=f.checked_bags or 0,
        hide_self_transfer=bool(f.hide_self_transfer),
        exclude_basic_economy=bool(f.exclude_basic_economy),
        seat=f.seat or "economy",
        currency=cfg.currency,
        language=cfg.search.language,
    )


def _minutes_from(day: date, dt) -> int:
    return (dt.date() - day).days * 24 * 60 + dt.hour * 60 + dt.minute


def violations(option: FlightOption, rules: LegRules, day: date) -> list[str]:
    """Exact local re-check of every rule; returns human-readable reasons (empty = OK)."""
    w, f = rules.window, rules.filters
    dep, arr = _minutes_from(day, option.departure), _minutes_from(day, option.arrival)
    out = []
    if dep < 0 or dep >= 24 * 60:
        out.append("departs on a different day")
    if w.earliest_departure is not None and dep < w.earliest_departure:
        out.append("departs too early")
    if w.latest_departure is not None and dep > w.latest_departure:
        out.append("departs too late")
    if w.earliest_arrival is not None and arr < w.earliest_arrival:
        out.append("arrives too early")
    if w.latest_arrival is not None and arr > w.latest_arrival:
        out.append("arrives too late")
    if f.max_stops is not None and option.stops > f.max_stops:
        out.append("too many stops")
    if f.max_duration_hours is not None and option.duration_min > f.max_duration_hours * 60:
        out.append("too long")
    lays = option.layovers_min
    if f.min_layover_minutes is not None and any(x < f.min_layover_minutes for x in lays):
        out.append("layover too short")
    if f.max_layover_minutes is not None and any(x > f.max_layover_minutes for x in lays):
        out.append("layover too long")
    if option.self_transfer:
        if f.hide_self_transfer:
            out.append("self-transfer")
        elif f.self_transfer_min_layover_minutes is not None and any(
            x < f.self_transfer_min_layover_minutes for x in lays
        ):
            out.append("self-transfer layover too short")
    return out
