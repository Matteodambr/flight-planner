from __future__ import annotations

from typing import Protocol

from ..models import FlightOption, LegQuery


class ProviderError(Exception):
    """A search failed in a way that may succeed on retry (bad page, network)."""


class BlockedError(ProviderError):
    """The site is rate-limiting us (HTTP 429, captcha page). Waiting, not retrying fast, is the fix."""


class Provider(Protocol):
    name: str

    def search_oneway(self, query: LegQuery) -> list[FlightOption]: ...

    def url(self, query: LegQuery) -> str: ...
