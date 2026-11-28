from __future__ import annotations

from datetime import date
from typing import Protocol


class VersionedMarketRecord(Protocol):
    trading_date: date
    source: str
    revision: int


def source_priority(source: str) -> int:
    """Prefer production observations when demo data overlaps the same date."""
    if source == "synthetic_demo":
        return 0
    return 1


def select_latest_records[RecordT: VersionedMarketRecord](
    records: list[RecordT],
    *,
    as_of: date | None = None,
) -> list[RecordT]:
    candidates = [
        record
        for record in records
        if as_of is None or record.trading_date <= as_of
    ]
    production_dates = [
        record.trading_date
        for record in candidates
        if source_priority(record.source) > 0
    ]
    production_start = min(production_dates) if production_dates else None
    production_end = max(production_dates) if production_dates else None
    production_is_sufficient = len(set(production_dates)) >= 60
    selected: dict[date, RecordT] = {}
    for record in candidates:
        if (
            record.source == "synthetic_demo"
            and (
                production_is_sufficient
                or (
                    production_start is not None
                    and production_end is not None
                    and production_start <= record.trading_date <= production_end
                )
            )
        ):
            continue
        current = selected.get(record.trading_date)
        if current is None or (
            source_priority(record.source),
            record.revision,
        ) > (
            source_priority(current.source),
            current.revision,
        ):
            selected[record.trading_date] = record
    return sorted(selected.values(), key=lambda item: item.trading_date)
