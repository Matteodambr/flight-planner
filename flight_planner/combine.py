"""Combine each traveler's outbound and return options per (window, city), then rank."""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .config import Config
from .links import skyscanner_url
from .models import FlightOption, LegQuery, LegResult
from .rules import violations
from .search import LegNeed
from .windows import Window


@dataclass(frozen=True)
class LegPick:
    option: FlightOption
    google_url: str

    @property
    def skyscanner_url(self) -> str:
        return skyscanner_url(self.option.origin, self.option.destination, self.option.departure.date())


@dataclass
class Combo:
    window: Window
    city: str
    legs: dict[str, tuple[LegPick, LegPick]]  # traveler -> (outbound, return)
    total_price: float
    travel_min: int
    stops: int
    hours_together: float
    arrival_gap_h: float
    departure_gap_h: float
    score: float = 0.0

    def traveler_price(self, name: str) -> float:
        o, r = self.legs[name]
        return o.option.price + r.option.price


@dataclass
class CityResult:
    city: str
    combos: list[Combo] = field(default_factory=list)
    problem: str | None = None  # why there are no combos

    @property
    def best(self) -> Combo | None:
        return self.combos[0] if self.combos else None


@dataclass
class WindowResult:
    window: Window
    cities: list[CityResult]  # ranked, cities without results last


LegKey = tuple[str, str, str, date]  # traveler, city, direction, day


def collect_leg_options(
    needs: list[LegNeed], results: dict[LegQuery, LegResult], k: int
) -> tuple[dict[LegKey, list[LegPick]], dict[LegKey, str]]:
    """Cheapest K valid options per traveler leg (across airport pairs), plus a reason for empty legs."""
    picks: dict[LegKey, list[LegPick]] = defaultdict(list)
    errors: dict[LegKey, list[str]] = defaultdict(list)
    searched: dict[LegKey, int] = defaultdict(int)
    raw: dict[LegKey, int] = defaultdict(int)  # flights Google returned before our checks
    for n in needs:
        key = (n.traveler, n.city, n.direction, n.day)
        res = results.get(n.query)
        if res is None:
            continue
        if res.error:
            errors[key].append(f"{n.from_airport}->{n.to_airport}: {res.error}")
            continue
        searched[key] += 1
        raw[key] += len(res.options)
        for opt in res.options:
            if not violations(opt, n.rules, n.day):
                picks[key].append(LegPick(opt, res.google_url))

    keys = set(picks) | set(errors) | set(searched)
    out: dict[LegKey, list[LegPick]] = {}
    problems: dict[LegKey, str] = {}
    for key in keys:
        seen, uniq = set(), []
        for p in sorted(picks.get(key, []), key=lambda p: (p.option.price, p.option.duration_min)):
            ident = (p.option.departure, p.option.arrival, p.option.origin, p.option.destination, p.option.airlines)
            if ident not in seen:
                seen.add(ident)
                uniq.append(p)
        out[key] = uniq[:k]
        if not uniq:
            if errors.get(key) and not searched.get(key):
                problems[key] = "search failed (" + "; ".join(errors[key]) + ")"
            elif not raw.get(key):
                problems[key] = "Google has no flights for this route/day within the stop, time and layover limits"
            else:
                problems[key] = f"{raw[key]} flight(s) found, none pass the time/layover rules"
    return out, problems


def _score(cfg: Config, c: Combo) -> float:
    w = cfg.ranking.score_weights
    return (
        c.total_price
        + w.eur_per_travel_hour * c.travel_min / 60
        + w.eur_per_stop * c.stops
        - w.eur_per_hour_together * c.hours_together
    )


def sort_key(cfg: Config):
    if cfg.ranking.sort_by == "score":
        return lambda c: (c.score, c.total_price)
    return lambda c: (c.total_price, -c.hours_together, c.travel_min)


def build_combos(cfg: Config, window: Window, city: str, legs: dict[LegKey, list[LegPick]]) -> list[Combo]:
    names = [t.name for t in cfg.travelers]
    per_traveler = []
    for n in names:
        outs = legs.get((n, city, "outbound", window.out), [])
        rets = legs.get((n, city, "return", window.ret), [])
        pairs = [(o, r) for o, r in itertools.product(outs, rets) if r.option.departure > o.option.arrival]
        if not pairs:
            return []
        per_traveler.append(pairs)

    rk = cfg.ranking
    combos = []
    for choice in itertools.product(*per_traveler):
        arrivals = [o.option.arrival for o, _ in choice]
        departures = [r.option.departure for _, r in choice]
        arrival_gap = (max(arrivals) - min(arrivals)).total_seconds() / 3600
        departure_gap = (max(departures) - min(departures)).total_seconds() / 3600
        if rk.max_arrival_gap_hours is not None and arrival_gap > rk.max_arrival_gap_hours:
            continue
        if rk.max_departure_gap_hours is not None and departure_gap > rk.max_departure_gap_hours:
            continue
        opts = [p.option for pair in choice for p in pair]
        c = Combo(
            window=window,
            city=city,
            legs=dict(zip(names, choice)),
            total_price=sum(o.price for o in opts),
            travel_min=sum(o.duration_min for o in opts),
            stops=sum(o.stops for o in opts),
            hours_together=max(0.0, (min(departures) - max(arrivals)).total_seconds() / 3600),
            arrival_gap_h=arrival_gap,
            departure_gap_h=departure_gap,
        )
        c.score = _score(cfg, c)
        combos.append(c)
    combos.sort(key=sort_key(cfg))
    return combos


def rank_window(
    cfg: Config, window: Window, legs: dict[LegKey, list[LegPick]], problems: dict[LegKey, str]
) -> WindowResult:
    cities = []
    for c in cfg.cities:
        combos = build_combos(cfg, window, c.name, legs)
        problem = None
        if not combos:
            reasons = []
            for t in cfg.travelers:
                for direction, day in (("outbound", window.out), ("return", window.ret)):
                    p = problems.get((t.name, c.name, direction, day))
                    if p:
                        reasons.append(f"{t.name} {direction}: {p}")
            problem = "; ".join(reasons) or "no valid combination (time gap limits or return before arrival)"
        cities.append(CityResult(c.name, combos, problem))
    key = sort_key(cfg)
    cities.sort(key=lambda cr: (cr.best is None, key(cr.best) if cr.best else ()))
    return WindowResult(window, cities)
