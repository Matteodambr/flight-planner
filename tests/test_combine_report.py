import io
from datetime import date

from flight_planner.cache import Cache
from flight_planner.combine import collect_leg_options, rank_window
from flight_planner.models import LegResult
from flight_planner.progress import Progress
from flight_planner.report import length_folder, window_stem, write_all
from flight_planner.search import plan_legs, run_searches
from flight_planner.windows import build_windows

from .conftest import dt, flight


class FakeProvider:
    """Deterministic flights: London is cheaper but B can only get there late; NUE return to London fails."""

    name = "fake"

    def url(self, q):
        return f"https://example.test/{q.from_airport}-{q.to_airport}-{q.day}"

    def search_oneway(self, q):
        d = q.day.day if q.day.month == 11 else 0
        if (q.from_airport, q.to_airport) == ("STN", "NUE"):
            from flight_planner.providers import ProviderError
            raise ProviderError("blocked")
        base = {"BCN": 60, "LGW": 40, "STN": 30}
        city_ap = q.to_airport if q.to_airport in base else q.from_airport
        price = base[city_ap] + (q.day.day % 3) * 10
        day = q.day
        mk = lambda hh, p: flight(q.from_airport, q.to_airport,
                                  dt(day.day, hh) if day.month == 11 else dt(1, hh).replace(month=10, day=31),
                                  (dt(day.day, hh + 2) if day.month == 11 else dt(1, hh + 2).replace(month=10, day=31)), p)
        return [mk(7, price + 50), mk(18, price), mk(12, price + 20)]


def _run(cfg, tmp_path):
    windows = build_windows(cfg.trip)
    needs = plan_legs(cfg, windows)
    queries = [n.query for n in needs]
    results = run_searches(queries, FakeProvider(), Cache(tmp_path / "c", 1), cfg, progress=Progress(1, stream=io.StringIO(), enabled=False))
    legs, problems = collect_leg_options(needs, results, cfg.ranking.options_per_leg)
    return windows, results, [rank_window(cfg, w, legs, problems) for w in windows]


def test_ranking_totals_and_constraints(cfg, tmp_path):
    windows, results, ranked = _run(cfg, tmp_path)
    nominal = next(r for r in ranked if r.window.is_nominal(cfg.trip))
    for cr in nominal.cities:
        if cr.best:
            b = cr.best
            assert b.total_price == sum(p.option.price for pair in b.legs.values() for p in pair)
            # A's outbound on 1 Nov must respect 17:30 earliest departure
            assert b.legs["A"][0].option.departure.hour >= 18
            assert b.hours_together >= 0
            prices = [c.total_price for c in cr.combos]
            assert prices == sorted(prices)
    totals = [cr.best.total_price for cr in nominal.cities if cr.best]
    assert totals == sorted(totals)


def test_arrival_gap_limit(cfg, tmp_path):
    cfg.ranking.max_arrival_gap_hours = 0
    _, _, ranked = _run(cfg, tmp_path)
    for wr in ranked:
        for cr in wr.cities:
            for c in cr.combos:
                assert c.arrival_gap_h == 0


def test_failed_leg_falls_back_to_other_airport_and_reports(cfg, tmp_path):
    windows, results, ranked = _run(cfg, tmp_path)
    assert any(r.error for r in results.values())
    for wr in ranked:
        london = next(cr for cr in wr.cities if cr.city == "London")
        if london.best:  # B must have returned via LGW since STN->NUE failed
            assert london.best.legs["B"][1].option.origin == "LGW"


def test_reports_written_with_length_folders(cfg, tmp_path):
    windows, results, ranked = _run(cfg, tmp_path)
    out = tmp_path / "out"
    write_all(ranked, cfg, out, [r for r in results.values() if r.error])
    folders = sorted(p.name for p in out.iterdir() if p.is_dir())
    assert folders == ["00n_minus2", "01n_minus1", "02n_nominal", "03n_plus1", "04n_plus2"]
    nominal = out / "02n_nominal" / "02n__out-2026-11-01_Sun__ret-2026-11-03_Tue__NOMINAL.md"
    assert nominal.exists()
    text = nominal.read_text()
    assert "same length as requested" in text and "skyscanner.net" in text
    assert len(list((out / "02n_nominal").glob("*.md"))) == 3
    summary = (out / "SUMMARY.md").read_text()
    assert "Price matrix" in summary and "Searches that failed" in summary and "Top 5 by city" in summary
    assert (out / "all_combos.csv").read_text().count("\n") > 1


def test_cache_roundtrip(cfg, tmp_path):
    _run(cfg, tmp_path)
    windows = build_windows(cfg.trip)
    queries = [n.query for n in plan_legs(cfg, windows)]
    res = run_searches(queries, FakeProvider(), Cache(tmp_path / "c", 1), cfg, progress=Progress(1, stream=io.StringIO(), enabled=False))
    assert all(r.from_cache for r in res.values() if not r.error)


class BlockingProvider(FakeProvider):
    def __init__(self, blocks):
        self.blocks = blocks
        self.calls = 0

    def search_oneway(self, q):
        from flight_planner.providers import BlockedError
        self.calls += 1
        if self.calls <= self.blocks:
            raise BlockedError("HTTP 429")
        return [] if (q.from_airport, q.to_airport) == ("STN", "NUE") else super().search_oneway(q)


def _search(cfg, tmp_path, provider):
    queries = [n.query for n in plan_legs(cfg, build_windows(cfg.trip))]
    slept = []
    res = run_searches(queries, provider, Cache(tmp_path / "c", 1), cfg,
                       progress=Progress(1, stream=io.StringIO(), enabled=False), sleep=slept.append)
    return res, slept


def test_waits_out_a_block_then_continues(cfg, tmp_path):
    cfg.search.retries = 0  # block waits must not consume normal retries
    res, slept = _search(cfg, tmp_path, BlockingProvider(blocks=2))
    assert not any(r.error for r in res.values())
    assert 120 in slept and 300 in slept  # 2 min, then 5 min


def test_gives_up_after_max_block_wait(cfg, tmp_path):
    cfg.search.block_max_wait_minutes = 10
    res, slept = _search(cfg, tmp_path, BlockingProvider(blocks=10**6))
    assert all(r.error for r in res.values())
    assert sum(s for s in slept if s >= 60) == 7 * 60  # 2 + 5; the next 10 would exceed the cap
    assert sum("skipped" in r.error for r in res.values()) == len(res) - 1
