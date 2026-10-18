from datetime import date, datetime

import pytest

from flight_planner.config import Config
from flight_planner.models import FlightOption, Segment


@pytest.fixture
def cfg() -> Config:
    return Config.model_validate({
        "trip": {"outbound_date": date(2026, 11, 1), "return_date": date(2026, 11, 3), "flex_out": 1, "flex_ret": 1},
        "cities": [{"name": "Barcelona", "airports": ["BCN"]}, {"name": "London", "airports": ["lgw", "STN"]}],
        "travelers": [
            {"name": "A", "from": ["FCO"], "to": ["FCO"],
             "time_rules": {"outbound": {"default": {"earliest_departure": "06:00"},
                                         "by_date": {date(2026, 11, 1): {"earliest_departure": "17:30"}}}}},
            {"name": "B", "from": ["MUC"], "to": ["NUE"]},
        ],
        "filters": {"max_stops": 1, "max_layover_minutes": 180},
        "search": {"delay_seconds": 0},
    })


def flight(origin, dest, dep, arr, price, via=None, airline="FR"):
    """dep/arr as datetimes; via=(airport, arr_at_via, dep_from_via) adds a connection."""
    if via:
        ap, a1, d2 = via
        segs = (Segment(origin, ap, dep, a1, int((a1 - dep).total_seconds() // 60)),
                Segment(ap, dest, d2, arr, int((arr - d2).total_seconds() // 60)))
    else:
        segs = (Segment(origin, dest, dep, arr, int((arr - dep).total_seconds() // 60)),)
    return FlightOption(price=price, airlines=(airline,), segments=segs)


def dt(day, hh, mm=0):
    return datetime(2026, 11, day, hh, mm)
