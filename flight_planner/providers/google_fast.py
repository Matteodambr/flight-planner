"""Google Flights via the unofficial fast-flights library."""

from __future__ import annotations

import json
from datetime import datetime

from fast_flights import FlightQuery, Passengers, create_query
from fast_flights.parser import _parse_time
from primp import Client
from selectolax.lexbor import LexborHTMLParser

from ..models import FlightOption, LegQuery, Segment
from .base import BlockedError, ProviderError

URL = "https://www.google.com/travel/flights"
# Pre-answered cookie banner; without it EU visitors get the consent.google.com page instead of results.
CONSENT_COOKIE = "SOCS=CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg; CONSENT=YES+"


class GoogleFastFlights:
    name = "google-flights"

    def __init__(self, proxy: str | None = None):
        self._client = Client(
            impersonate="chrome_145",
            impersonate_os="macos",
            referer=True,
            proxy=proxy,
            cookie_store=True,
        )

    def _query(self, q: LegQuery):
        return create_query(
            flights=[
                FlightQuery(
                    date=q.day.isoformat(),
                    from_airport=q.from_airport,
                    to_airport=q.to_airport,
                    max_stops=q.max_stops,
                    earliest_departure_hour=q.earliest_departure_hour,
                    latest_departure_hour=q.latest_departure_hour,
                    earliest_arrival_hour=q.earliest_arrival_hour,
                    latest_arrival_hour=q.latest_arrival_hour,
                    max_duration_minutes=q.max_duration_minutes,
                    min_layover_minutes=q.min_layover_minutes,
                    max_layover_minutes=q.max_layover_minutes,
                )
            ],
            seat=q.seat,
            trip="one-way",
            passengers=Passengers(adults=1),
            language=q.language,
            currency=q.currency,
            carry_on_bags=q.carry_on_bags,
            checked_bags=q.checked_bags,
            hide_separate_and_self_transfer=q.hide_self_transfer,
            exclude_basic_economy=q.exclude_basic_economy,
        )

    def url(self, q: LegQuery) -> str:
        return self._query(q).url()

    def search_oneway(self, q: LegQuery) -> list[FlightOption]:
        try:
            res = self._client.get(URL, params=self._query(q).params(), headers={"Cookie": CONSENT_COOKIE})
        except Exception as e:  # network errors from primp
            raise ProviderError(f"request failed: {e}") from e
        if res.status_code in (429, 503) or _looks_blocked(str(getattr(res, "url", "")), res.text):
            raise BlockedError(f"HTTP {res.status_code}, Google is rate-limiting this IP")
        if res.status_code != 200:
            raise ProviderError(f"HTTP {res.status_code}")
        return parse_options(res.text)


def _looks_blocked(url: str, html: str) -> bool:
    # Google's rate-limit page lives under /sorry/ and shows a captcha.
    if "/sorry/" in url:
        return True
    head = html[:20000].lower()
    return "unusual traffic" in head or "captcha-form" in head or "g-recaptcha" in head


def parse_options(html: str) -> list[FlightOption]:
    """Parse both result lists on the page.

    fast_flights.parser only reads payload[3] ("other flights"); Google puts the
    "best flights" list, often including the cheapest fare, in payload[2].
    Field indices follow fast_flights.parser.parse_js.
    """
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise ProviderError("unexpected page (consent wall, captcha or layout change)")
    data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        return []  # Google's "no flights found"
    try:
        payload = json.loads(data)
        options = []
        for idx in (2, 3):
            block = payload[idx] if len(payload) > idx else None
            for item in (block[0] if block and block[0] else []):
                opt = _parse_item(item)
                if opt is not None:
                    options.append(opt)
    except (ValueError, IndexError, TypeError, KeyError) as e:
        raise ProviderError(f"could not parse results: {e}") from e
    return options


def _parse_item(item) -> FlightOption | None:
    flight, price_info = item[0], item[1]
    try:
        price = price_info[0][1]
    except (IndexError, TypeError):
        return None  # Google lists some itineraries without a price
    if not price or not flight[2]:
        return None
    segments = []
    for sf in flight[2]:
        dep_h, dep_m = _parse_time(sf[8])
        arr_h, arr_m = _parse_time(sf[10])
        fn = sf[22] if len(sf) > 22 and isinstance(sf[22], list) else None
        flight_number = f"{fn[0]} {fn[1]}" if fn and fn[0] and fn[1] else ""
        # sf[2] is set when another airline operates the flight; otherwise the seller operates it.
        operator = (sf[2] if len(sf) > 2 and isinstance(sf[2], str) and sf[2] else None) or (
            fn[3] if fn and len(fn) > 3 and isinstance(fn[3], str) else "")
        segments.append(
            Segment(
                from_airport=sf[3],
                to_airport=sf[6],
                departure=datetime(*sf[20], dep_h, dep_m),
                arrival=datetime(*sf[21], arr_h, arr_m),
                duration_min=int(sf[11] or 0),
                flight_number=flight_number,
                operator=operator,
            )
        )
    return FlightOption(price=float(price), airlines=tuple(flight[1] or ()), segments=tuple(segments))
