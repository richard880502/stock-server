"""A small in-memory cache for market-wide daily reports that never change."""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def is_settled(trading_date: date) -> bool:
    """A report is final once its trading day is over in Taipei."""
    return trading_date < datetime.now(UTC).astimezone(TAIPEI).date()


class ReportCache:
    """Bounded LRU keyed by request; the payloads are large, so keep few."""

    def __init__(self, max_entries: int = 6) -> None:
        self.max_entries = max_entries
        self._items: OrderedDict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = (
            OrderedDict()
        )

    @staticmethod
    def _key(path: str, params: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
        return path, tuple(sorted(params.items()))

    def get(self, path: str, params: dict[str, str]) -> dict[str, Any] | None:
        key = self._key(path, params)
        payload = self._items.get(key)
        if payload is not None:
            self._items.move_to_end(key)
        return payload

    def put(self, path: str, params: dict[str, str], payload: dict[str, Any]) -> None:
        key = self._key(path, params)
        self._items[key] = payload
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)
