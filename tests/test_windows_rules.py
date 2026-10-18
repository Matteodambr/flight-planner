from datetime import date

import pytest
from pydantic import ValidationError

from flight_planner.config import Config, Trip
from flight_planner.rules import build_query, resolve, violations
from flight_planner.windows import build_windows

from .conftest import dt, flight


def test_grid_covers_separate_and_joint_shifts():
    t = Trip(outbound_date=date(2026, 11, 1), return_date=date(2026, 11, 3), flex_out=1, flex_ret=1)
    ws = build_windows(t)
    assert len(ws) == 9
    pairs = {(w.out.day, w.ret.day) for w in ws}
    assert {(31, 2), (1, 3), (2, 4)} <= pairs  # same length, shifted together
    assert {(1, 4), (2, 3)} <= pairs  # one end shifted
    assert [w.nights for w in ws] == sorted(w.nights for w in ws)
    assert sum(w.is_nominal(t) for w in ws) == 1
    assert sum(w.keeps_duration for w in ws) == 3


def test_min_nights_drops_short_windows():
    t = Trip(outbound_date=date(2026, 11, 1), return_date=date(2026, 11, 2), flex_out=1, flex_ret=1, min_nights=1)
    assert all(w.nights >= 1 for w in build_windows(t))


def test_by_date_overrides_default(cfg):
    a = cfg.travelers[0]
    assert resolve(cfg, a, "outbound", date(2026, 11, 1)).window.earliest_departure == 17 * 60 + 30
    assert resolve(cfg, a, "outbound", date(2026, 11, 2)).window.earliest_departure == 6 * 60
    assert resolve(cfg, a, "return", date(2026, 11, 3)).window.earliest_departure is None


def test_traveler_filters_override_global(cfg):
    cfg.travelers[1].filters = cfg.travelers[1].filters.model_validate({"max_stops": 0})
    assert resolve(cfg, cfg.travelers[1], "outbound", date(2026, 11, 1)).filters.max_stops == 0
    assert resolve(cfg, cfg.travelers[0], "outbound", date(2026, 11, 1)).filters.max_stops == 1


def test_hours_rounded_outward_and_checked_exactly(cfg):
    a = cfg.travelers[0]
    day = date(2026, 11, 1)
    rules = resolve(cfg, a, "outbound", day)
    q = build_query(cfg, rules, "FCO", "BCN", day)
    assert q.earliest_departure_hour == 17  # 17:30 floored for Google
    assert violations(flight("FCO", "BCN", dt(1, 17, 15), dt(1, 19), 50), rules, day) == ["departs too early"]
    assert violations(flight("FCO", "BCN", dt(1, 17, 45), dt(1, 19, 30), 50), rules, day) == []


def test_latest_arrival_next_day_and_layover():
    cfg = Config.model_validate({
        "trip": {"outbound_date": date(2026, 11, 1), "return_date": date(2026, 11, 3)},
        "cities": [{"name": "X", "airports": ["BCN"]}],
        "travelers": [{"name": "A", "from": ["FCO"], "to": ["FCO"],
                       "time_rules": {"outbound": {"default": {"latest_arrival": "25:00"}}}}],
        "filters": {"min_layover_minutes": 60},
    })
    day = date(2026, 11, 1)
    rules = resolve(cfg, cfg.travelers[0], "outbound", day)
    assert violations(flight("FCO", "BCN", dt(1, 22), dt(2, 0, 30), 1), rules, day) == []
    assert "arrives too late" in violations(flight("FCO", "BCN", dt(1, 23), dt(2, 1, 30), 1), rules, day)
    short = flight("FCO", "BCN", dt(1, 8), dt(1, 12), 1, via=("MXP", dt(1, 9), dt(1, 9, 30)))
    assert violations(short, rules, day) == ["layover too short"]


def test_config_validation():
    with pytest.raises(ValidationError):
        Config.model_validate({"trip": {"outbound_date": "2026-11-03", "return_date": "2026-11-01"},
                               "cities": [{"name": "X", "airports": ["BCN"]}],
                               "travelers": [{"name": "A", "from": ["FCO"], "to": ["FCO"]}]})
    with pytest.raises(ValidationError):
        Config.model_validate({"trip": {"outbound_date": "2026-11-01", "return_date": "2026-11-03"},
                               "cities": [{"name": "X", "airports": ["BARCELONA"]}],
                               "travelers": [{"name": "A", "from": ["FCO"], "to": ["FCO"]}]})


def test_example_config_loads():
    from pathlib import Path
    from flight_planner.config import load_config
    load_config(Path(__file__).parent.parent / "config.yaml")


def test_one_sided_flex_and_min_nights():
    t = Trip(outbound_date=date(2026, 12, 4), return_date=date(2026, 12, 7), flex_out=[0, 1], flex_ret=[-1, 0], min_nights=2)
    assert {(w.out.day, w.ret.day) for w in build_windows(t)} == {(4, 6), (4, 7), (5, 7)}


def test_self_transfer_needs_longer_layover():
    from flight_planner.models import FlightOption, Segment
    cfg = Config.model_validate({
        "trip": {"outbound_date": date(2026, 12, 4), "return_date": date(2026, 12, 7)},
        "cities": [{"name": "X", "airports": ["BCN"]}],
        "travelers": [{"name": "A", "from": ["BRE"], "to": ["BRE"]}],
        "filters": {"min_layover_minutes": 60, "hide_self_transfer": False, "self_transfer_min_layover_minutes": 120},
    })
    day = date(2026, 12, 4)
    rules = resolve(cfg, cfg.travelers[0], "outbound", day)

    def opt(first_carrier, layover_end):
        return FlightOption(price=1, airlines=("x",), segments=(
            Segment("BRE", "STN", dt(4, 8).replace(month=12), dt(4, 9).replace(month=12), 60, f"{first_carrier} 1"),
            Segment("STN", "BCN", layover_end.replace(month=12), dt(4, 15).replace(month=12), 120, "VY 2"),
        ))

    assert opt("FR", dt(4, 10, 30)).self_transfer
    assert violations(opt("FR", dt(4, 10, 30)), rules, day) == ["self-transfer layover too short"]
    assert violations(opt("FR", dt(4, 11)), rules, day) == []
    assert violations(opt("LH", dt(4, 10, 30)), rules, day) == []  # protected connection: 1h30 is fine
    rules_hidden = resolve(cfg.model_copy(update={"filters": cfg.filters.model_copy(update={"hide_self_transfer": True})}),
                           cfg.travelers[0], "outbound", day)
    assert violations(opt("FR", dt(4, 11)), rules_hidden, day) == ["self-transfer"]


def test_time_bands_clip_to_leg_window(cfg):
    from flight_planner.search import split_by_time_bands
    day = date(2026, 11, 1)
    q = build_query(cfg, resolve(cfg, cfg.travelers[0], "outbound", day), "FCO", "BCN", day)  # earliest 17:30
    parts = split_by_time_bands(q, [(0, 9), (9, 13), (13, 17), (17, 23)])
    assert [(p.earliest_departure_hour, p.latest_departure_hour) for p in parts] == [(17, 23)]  # 17-17 is inside 17-23
    assert split_by_time_bands(q, None) == [q]
