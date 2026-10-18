"""Work out every one-way search the windows need, deduplicate, and run them."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, replace
from datetime import date

from .cache import Cache
from .config import Config, Traveler
from .models import LegQuery, LegResult
from .progress import Progress
from .providers import BlockedError, Provider, ProviderError
from .rules import Direction, LegRules, build_query, resolve
from .windows import Window


@dataclass(frozen=True)
class LegNeed:
    """A (traveler, direction, day, airport pair) the ranking needs, and the query that answers it."""

    traveler: str
    city: str
    direction: Direction
    day: date
    from_airport: str
    to_airport: str
    rules: LegRules
    query: LegQuery


def split_by_time_bands(q: LegQuery, bands: list[tuple[int, int]] | None) -> list[LegQuery]:
    """One query per departure-time band, clipped to the leg's own departure window.

    Bands share their boundary hours on purpose, so nothing falls between them whichever
    way Google rounds; duplicates are merged later.
    """
    if not bands:
        return [q]
    lo0 = q.earliest_departure_hour if q.earliest_departure_hour is not None else 0
    hi0 = q.latest_departure_hour if q.latest_departure_hour is not None else 23
    out = []
    for lo, hi in bands:
        a, b = max(lo, lo0), min(hi, hi0)
        if a <= b:
            out.append(replace(q, earliest_departure_hour=a, latest_departure_hour=b))
    spans = [(p.earliest_departure_hour, p.latest_departure_hour) for p in out]
    keep = [p for p, (a, b) in zip(out, spans)
            if not any((c <= a and b <= d) and (c, d) != (a, b) for c, d in spans)]
    return list(dict.fromkeys(keep)) or [q]


def plan_legs(cfg: Config, windows: list[Window]) -> list[LegNeed]:
    out_days = sorted({w.out for w in windows})
    ret_days = sorted({w.ret for w in windows})
    needs: list[LegNeed] = []

    def add(t: Traveler, city: str, direction: Direction, day: date, a: str, b: str) -> None:
        if a == b:
            return
        rules = resolve(cfg, t, direction, day)
        for q in split_by_time_bands(build_query(cfg, rules, a, b, day), cfg.search.time_bands):
            needs.append(LegNeed(t.name, city, direction, day, a, b, rules, q))

    for t in cfg.travelers:
        for c in cfg.cities:
            for ap in c.airports:
                for day in out_days:
                    for origin in t.from_:
                        add(t, c.name, "outbound", day, origin, ap)
                for day in ret_days:
                    for home in t.to:
                        add(t, c.name, "return", day, ap, home)
    return needs


def run_searches(
    queries: list[LegQuery], provider: Provider, cache: Cache, cfg: Config, progress: Progress | None = None,
    sleep=time.sleep,
) -> dict[LegQuery, LegResult]:
    unique = list(dict.fromkeys(queries))
    progress = progress or Progress(len(unique))
    sc = cfg.search
    results: dict[LegQuery, LegResult] = {}
    lo, hi = sc.delay_seconds
    fetched_any = False
    blocked_waited = 0.0  # total minutes spent waiting out blocks this run
    gave_up = False

    for q in unique:
        label = f"{q.from_airport}->{q.to_airport} {q.day:%d %b}"
        res = LegResult(query=q, google_url=provider.url(q))
        cached = cache.get(q)
        if cached is not None:
            res.options, res.from_cache = cached, True
            results[q] = res
            progress.step(label, cached=True, detail=f"{len(cached)} flights (cached)")
            continue
        if gave_up:
            res.error = "skipped: Google was blocking requests"
            results[q] = res
            progress.step(label, failed=True, detail="SKIPPED (blocked)")
            continue

        attempt = 0
        block_round = 0
        while True:
            if fetched_any:
                sleep(random.uniform(lo, hi) * (2**attempt))
            fetched_any = True
            try:
                res.options = provider.search_oneway(q)
                res.error = None
                cache.put(q, res.options)
                if block_round:
                    progress.message("Unblocked, continuing.")
                break
            except BlockedError as e:
                res.error = str(e)
                waits = sc.block_wait_minutes
                wait = waits[min(block_round, len(waits) - 1)]
                if blocked_waited + wait > sc.block_max_wait_minutes:
                    gave_up = True
                    progress.message(
                        f"Still blocked after waiting {blocked_waited:.0f} min in total; giving up. "
                        "Results found so far are kept (and cached) - run again later to fill in the rest."
                    )
                    break
                block_round += 1
                blocked_waited += wait
                progress.message(f"Blocked by Google ({e}). Waiting {wait:g} min before retry #{block_round}...")
                progress.wait(wait * 60, f"blocked, retry #{block_round} in", sleep=sleep)
            except ProviderError as e:
                res.error = str(e)
                attempt += 1
                if attempt > sc.retries:
                    break
                progress.message(f"{label}: attempt {attempt} failed: {e}")
        results[q] = res
        progress.step(label, failed=bool(res.error),
                      detail=f"FAILED ({res.error})" if res.error else f"{len(res.options)} flights")
    progress.close()
    return results
