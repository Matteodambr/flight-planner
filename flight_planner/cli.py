"""Command line entry point: python -m flight_planner [-c config.yaml] [--dry-run] [--no-cache]"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .cache import Cache
from .combine import collect_leg_options, rank_window
from .config import load_config
from .providers import GoogleFastFlights
from .report import length_folder, short_label, write_all
from .search import plan_legs, run_searches
from .windows import build_windows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flight_planner", description=__doc__)
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true", help="show windows and number of searches, then stop")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached results and fetch everything again")
    ap.add_argument("--cache-dir", default=".cache/flights")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ValidationError, ValueError) as e:
        print(f"Config error in {args.config}:\n{e}", file=sys.stderr)
        return 2

    windows = build_windows(cfg.trip)
    if not windows:
        print("No date windows left after applying min_nights.", file=sys.stderr)
        return 2
    needs = plan_legs(cfg, windows)
    queries = list(dict.fromkeys(n.query for n in needs))
    cache = Cache(args.cache_dir, cfg.search.cache_ttl_hours, enabled=not args.no_cache)
    cached = sum(cache.get(q) is not None for q in queries)

    print(f"{len(windows)} date windows × {len(cfg.cities)} cities × {len(cfg.travelers)} travelers")
    for w in windows:
        print(f"  {short_label(w, cfg):<34} -> {length_folder(w)}/")
    to_fetch = len(queries) - cached
    lo, hi = cfg.search.delay_seconds
    print(f"{len(queries)} unique one-way searches ({cached} cached, {to_fetch} to fetch, "
          f"~{to_fetch * ((lo + hi) / 2 + 2) / 60:.0f} min)")
    if args.dry_run:
        return 0

    try:
        results = run_searches(queries, GoogleFastFlights(proxy=cfg.search.proxy), cache, cfg)
    except KeyboardInterrupt:
        print("\nStopped. Searches finished so far are cached; run the same command again to continue.")
        return 130
    legs, problems = collect_leg_options(needs, results, cfg.ranking.options_per_leg)
    ranked = [rank_window(cfg, w, legs, problems) for w in windows]
    failed = [r for r in results.values() if r.error]

    out = Path(cfg.output_dir) / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    write_all(ranked, cfg, out, failed)
    print(f"\nDone. {len(failed)} failed searches. Open {out / 'SUMMARY.md'}")
    return 0
