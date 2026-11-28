"""One rule for stamping ``available_at`` on rows of a past trading day.

A row for trading date D fetched at time T is stamped ``min(T, publication_time(D))``.
Rows fetched on D itself before the feed's publication time keep T (we did not know them
earlier); rows fetched later - a back-fill, or the first deploy on a weekend - are stamped
with the documented publication time, so an as-of read for D sees them instead of the
previous day's row. Documented publication times (Taipei):

- valuation (本益比/殖利率/股價淨值比): 18:00 on D
- 借券 (SBL): 22:35 on D
- 當沖: 23:00 on D+2 (weekdays)
- foreign holding: 21:00 on D
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

VALUATION_PUBLISHED = time(18, 0)
SBL_PUBLISHED = time(22, 35)
DAYTRADE_PUBLISHED = time(23, 0)  # on D+2
FOREIGN_PUBLISHED = time(21, 0)


def publication_moment(day: date, at: time) -> datetime:
    """``at`` Taipei time on ``day`` as a timezone-aware UTC datetime."""
    return datetime.combine(day, at, TAIPEI).astimezone(UTC)


def stamp_available_at(publication: datetime, fetched_at: datetime) -> datetime:
    """``min(fetched_at, publication)``, both timezone-aware (returned in UTC)."""
    return min(fetched_at, publication).astimezone(UTC)
