"""Tiny JSON disk cache for search results, so re-runs (e.g. after tweaking ranking) are instant."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .models import FlightOption, LegQuery


class Cache:
    def __init__(self, directory: str | Path, ttl_hours: float, enabled: bool = True):
        self.dir = Path(directory)
        self.ttl = ttl_hours * 3600
        self.enabled = enabled

    def _path(self, q: LegQuery) -> Path:
        return self.dir / (hashlib.sha256(q.cache_key().encode()).hexdigest()[:32] + ".json")

    def get(self, q: LegQuery) -> list[FlightOption] | None:
        if not self.enabled:
            return None
        p = self._path(q)
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            return None
        if time.time() - data["fetched_at"] > self.ttl:
            return None
        return [FlightOption.from_dict(o) for o in data["options"]]

    def put(self, q: LegQuery, options: list[FlightOption]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": time.time(), "key": q.cache_key(), "options": [o.to_dict() for o in options]}
        self._path(q).write_text(json.dumps(payload))
