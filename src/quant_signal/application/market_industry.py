"""Weekly refresh of the official industry classification (產業別)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from quant_signal.application.data_sync import TAIPEI
from quant_signal.domain.models import FetchCoverage, MarketIndustry
from quant_signal.infrastructure.providers.industry import (
    TPEX_INDUSTRY_URL,
    TWSE_INDUSTRY_URL,
    fetch_json_list,
    parse_tpex_industries,
    parse_twse_industries,
)

logger = logging.getLogger(__name__)

INDUSTRY_SOURCE = "industry_classification"
INDUSTRY_DATASET = "market_industries"


class _HasClient(Protocol):
    client: httpx.AsyncClient


class IndustryRepository(Protocol):
    async def upsert_market_industries(self, rows: list[MarketIndustry]) -> int: ...

    async def list_fetch_coverage(
        self, source: str, dataset: str, symbol: str, periods: list[str]
    ) -> dict[str, FetchCoverage]: ...

    async def record_fetch_coverage(self, entries: list[FetchCoverage]) -> None: ...


def _week_period(now: datetime) -> str:
    year, week, _ = now.astimezone(TAIPEI).isocalendar()
    return f"{year}-W{week:02d}"


async def refresh_industries(
    repository: IndustryRepository,
    twse: _HasClient,
    tpex: _HasClient,
    *,
    now: datetime | None = None,
    min_rows: int = 500,
) -> int:
    """Fetch both classifications at most once per ISO week; returns rows written.

    Nothing is stored unless both exchanges answered with a plausible list, so a
    half-failed run never replaces a good classification with a partial one.
    """
    moment = now or datetime.now(UTC)
    period = _week_period(moment)
    done = await repository.list_fetch_coverage(INDUSTRY_SOURCE, INDUSTRY_DATASET, "*", [period])
    if period in done:
        return 0
    twse_payload: list[Any] = await fetch_json_list(twse.client, TWSE_INDUSTRY_URL)
    tpex_payload: list[Any] = await fetch_json_list(tpex.client, TPEX_INDUSTRY_URL)
    rows = [
        *parse_twse_industries(twse_payload, available_at=moment),
        *parse_tpex_industries(tpex_payload, available_at=moment),
    ]
    if len(rows) < min_rows:
        raise RuntimeError(f"industry classification looks incomplete: {len(rows)} rows")
    written = await repository.upsert_market_industries(rows)
    await repository.record_fetch_coverage(
        [
            FetchCoverage(
                source=INDUSTRY_SOURCE,
                dataset=INDUSTRY_DATASET,
                symbol="*",
                period=period,
                status="ok",
                row_count=len(rows),
                fetched_at=moment,
            )
        ]
    )
    logger.info("industry classification refreshed: %s rows (%s)", len(rows), period)
    return written
