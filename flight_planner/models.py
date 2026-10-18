"""Provider-independent flight data."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

# Airlines that don't sell connecting tickets: a connection involving them is a self-transfer
# (separate tickets, you collect bags and re-check in; a missed connection is your problem).
NO_CONNECTION_CARRIERS = frozenset({"FR", "RK", "AL", "W6", "W4", "W9", "W1", "U2", "EC", "DS", "LS", "V7", "5O", "HG"})


@dataclass(frozen=True)
class Segment:
    from_airport: str
    to_airport: str
    departure: datetime  # local time at from_airport
    arrival: datetime  # local time at to_airport
    duration_min: int
    flight_number: str = ""  # e.g. "FR 1234"
    operator: str = ""  # airline actually flying it, e.g. "KLM Cityhopper"


@dataclass(frozen=True)
class FlightOption:
    price: float
    airlines: tuple[str, ...]
    segments: tuple[Segment, ...]
    self_transfer_flag: bool | None = None  # set when the provider reports it explicitly

    @property
    def self_transfer(self) -> bool:
        if self.self_transfer_flag is not None:
            return self.self_transfer_flag
        if self.stops == 0:
            return False
        return any(s.flight_number.split(" ")[0] in NO_CONNECTION_CARRIERS for s in self.segments)

    @property
    def operators(self) -> list[str]:
        """Operating airlines in flight order, without repeats (falls back to the marketing airlines)."""
        ops = list(dict.fromkeys(s.operator for s in self.segments if s.operator))
        return ops or list(self.airlines)

    @property
    def flight_numbers(self) -> list[str]:
        return [s.flight_number for s in self.segments if s.flight_number]

    @property
    def origin(self) -> str:
        return self.segments[0].from_airport

    @property
    def destination(self) -> str:
        return self.segments[-1].to_airport

    @property
    def departure(self) -> datetime:
        return self.segments[0].departure

    @property
    def arrival(self) -> datetime:
        return self.segments[-1].arrival

    @property
    def stops(self) -> int:
        return len(self.segments) - 1

    @property
    def layovers_min(self) -> list[int]:
        return [
            int((b.departure - a.arrival).total_seconds() // 60)
            for a, b in zip(self.segments, self.segments[1:])
        ]

    @property
    def connection_airports(self) -> list[str]:
        return [s.to_airport for s in self.segments[:-1]]

    @property
    def duration_min(self) -> int:
        # Sum of flight times plus layovers; avoids cross-timezone clock arithmetic.
        return sum(s.duration_min for s in self.segments) + sum(self.layovers_min)

    def to_dict(self) -> dict:
        d = asdict(self)
        for s in d["segments"]:
            s["departure"] = s["departure"].isoformat()
            s["arrival"] = s["arrival"].isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> FlightOption:
        return cls(
            price=d["price"],
            airlines=tuple(d["airlines"]),
            self_transfer_flag=d.get("self_transfer_flag"),
            segments=tuple(
                Segment(
                    from_airport=s["from_airport"],
                    to_airport=s["to_airport"],
                    departure=datetime.fromisoformat(s["departure"]),
                    arrival=datetime.fromisoformat(s["arrival"]),
                    duration_min=s["duration_min"],
                    flight_number=s.get("flight_number", ""),
                    operator=s.get("operator", ""),
                )
                for s in d["segments"]
            ),
        )


@dataclass(frozen=True)
class LegQuery:
    """One one-way search. Equal queries are searched once and shared."""

    from_airport: str
    to_airport: str
    day: date
    earliest_departure_hour: int | None
    latest_departure_hour: int | None
    earliest_arrival_hour: int | None
    latest_arrival_hour: int | None
    max_stops: int | None
    max_duration_minutes: int | None
    min_layover_minutes: int | None
    max_layover_minutes: int | None
    carry_on_bags: int
    checked_bags: int
    hide_self_transfer: bool
    exclude_basic_economy: bool
    seat: str
    currency: str
    language: str

    def cache_key(self) -> str:
        return "v4|" + "|".join(str(v) for v in asdict(self).values())  # bump when parsing changes


@dataclass
class LegResult:
    query: LegQuery
    options: list[FlightOption] = field(default_factory=list)
    error: str | None = None
    from_cache: bool = False
    google_url: str = ""
