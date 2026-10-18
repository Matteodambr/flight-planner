"""Build the grid of (outbound date, return date) windows from the flexibility settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .config import Trip


@dataclass(frozen=True, order=True)
class Window:
    out: date
    ret: date
    nominal_nights: int

    @property
    def nights(self) -> int:
        return (self.ret - self.out).days

    @property
    def nights_delta(self) -> int:
        return self.nights - self.nominal_nights

    @property
    def keeps_duration(self) -> bool:
        return self.nights_delta == 0

    def is_nominal(self, trip: Trip) -> bool:
        return self.out == trip.outbound_date and self.ret == trip.return_date


def build_windows(trip: Trip) -> list[Window]:
    nominal = (trip.return_date - trip.outbound_date).days
    windows = []
    for i in range(trip.flex_out[0], trip.flex_out[1] + 1):
        for j in range(trip.flex_ret[0], trip.flex_ret[1] + 1):
            out = trip.outbound_date + timedelta(days=i)
            ret = trip.return_date + timedelta(days=j)
            if (ret - out).days < trip.min_nights:
                continue
            windows.append(Window(out=out, ret=ret, nominal_nights=nominal))
    return sorted(windows, key=lambda w: (w.nights, w.out))
