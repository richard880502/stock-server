"""Whole-market multi-factor screener: snapshot computation and read side.

The snapshot (``market_factor_daily``) is written only by the sync worker, after market
syncs and by an upkeep catch-up; API and MCP requests only read it. Everything is a
deterministic ranking of official data, not a recommendation.
"""

from __future__ import annotations

import logging
import math as stdmath
import zlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from quant_signal.application.official_queries import as_of_cutoff
from quant_signal.application.ports import QuantRepository
from quant_signal.application.stocks import StockNotFound, normalize_listed_symbol
from quant_signal.domain.feed_stats import FEEDS, FeedDayStat
from quant_signal.domain.models import (
    FetchCoverage,
    MarketFactorSnapshot,
    MarketFactorState,
    MarketSession,
    SyncJobKind,
)
from quant_signal.quant import screener as math
from quant_signal.quant.screener import (
    BOOL_KEYS,
    COMPOSITE_WEIGHTS,
    FACTOR_LABELS,
    FAMILY_INPUTS,
    GROUP_NOTES,
    GROUPS,
    HISTORY_SESSIONS,
    METRIC_BY_KEY,
    METRICS,
    NUMERIC_KEYS,
    PLEDGE_FLAG_MIN,
    SCORE_KEYS,
    SHORT_LABELS,
    VERSION,
)

logger = logging.getLogger(__name__)

MAX_LIMIT = 200
SORT_KEYS = frozenset({"composite", *SCORE_KEYS, *NUMERIC_KEYS})
FILTER_KEYS = SORT_KEYS

# ------------------------------------------------------------------------- presets

_LIQUID_2E7 = {"key": "avg_turnover_20d", "min": 20_000_000}
_LIQUID_1E7 = {"key": "avg_turnover_20d", "min": 10_000_000}
_LIQUID_5E7 = {"key": "avg_turnover_20d", "min": 50_000_000}

PRESETS: list[dict[str, Any]] = [
    {
        "key": "balanced",
        "label": "綜合評分",
        "description": "依綜合分數排序，僅含近 20 日均成交值達 2,000 萬元以上的股票。",
        "sort": "composite",
        "order": "desc",
        "filters": [_LIQUID_2E7],
        "columns": ["composite", "momentum", "flow", "fundamental", "value", "stability"],
    },
    {
        "key": "money_flow",
        "label": "資金流入",
        "description": "法人 20 日淨買超佔成交量為正，依資金流向分數排序。",
        "sort": "flow",
        "order": "desc",
        "filters": [{"key": "inst_flow_ratio_20d", "min": 0.0001}, _LIQUID_2E7],
        "columns": [
            "flow",
            "total_net_20d",
            "inst_flow_ratio_20d",
            "foreign_streak",
            "trust_streak",
            "margin_change_20d_pct",
        ],
    },
    {
        "key": "quiet_accumulation",
        "label": "資金緩步流入",
        "description": (
            "法人近 60 個交易日有 55% 以上的日子淨買超、60 日合計淨買超，"
            "且近 20 日股價漲跌幅在 ±8% 內，依緩步流入分數排序。"
        ),
        "sort": "accumulation",
        "order": "desc",
        "filters": [
            {"key": "inst_flow_ratio_60d", "min": 0.0001},
            {"key": "inst_buy_days_60d", "min": 55},
            {"key": "ret_20d", "min": -8, "max": 8},
            _LIQUID_1E7,
        ],
        "columns": [
            "accumulation",
            "inst_flow_ratio_60d",
            "inst_buy_days_60d",
            "ret_20d",
            "ret_60d",
            "cmf_20",
            "big_holder_pct_change",
        ],
    },
    {
        "key": "big_holder_increase",
        "label": "大戶增持",
        "description": (
            "400 張以上大戶持股比例較比較週上升，依增加幅度排序。集保股權分散表為週資料，"
            "歷史自 2026-11-20 起才開始累積，第二週資料出現前此清單為空。"
        ),
        "sort": "big_holder_pct_change",
        "order": "desc",
        "filters": [{"key": "big_holder_pct_change", "min": 0.0001}, _LIQUID_1E7],
        "columns": [
            "big_holder_pct_change",
            "big_holder_pct",
            "whale_pct_change",
            "retail_holders_change_pct",
            "inst_flow_ratio_20d",
            "accumulation",
        ],
    },
    {
        "key": "momentum",
        "label": "強勢動能",
        "description": "均線多頭排列（收盤價 > 20 日 > 60 日 > 120 日均線），依動能分數排序。",
        "sort": "momentum",
        "order": "desc",
        "filters": [{"key": "trend_aligned", "equals": True}, _LIQUID_5E7],
        "columns": [
            "momentum",
            "trend",
            "ret_20d",
            "ret_60d",
            "high_52w_gap_pct",
            "volume_ratio_5_60",
        ],
    },
    {
        "key": "revenue_growth",
        "label": "營收成長",
        "description": "最新月營收年增率達 20% 以上，依最新月營收年增率排序。",
        "sort": "revenue_yoy_pct",
        "order": "desc",
        "filters": [{"key": "revenue_yoy_pct", "min": 20}, _LIQUID_1E7],
        "columns": [
            "revenue_yoy_pct",
            "revenue_yoy_3m_pct",
            "revenue_mom_pct",
            "revenue_cum_yoy_pct",
            "revenue_growth_streak_months",
            "ret_20d",
        ],
    },
    {
        "key": "value_yield",
        "label": "低估值高殖利率",
        "description": (
            "本益比位於自身近一年 30% 分位以下、殖利率 4% 以上、近四季 EPS 為正，"
            "且近 20 日均成交值 1,000 萬元以上，依估值分數排序。"
            "估值分數 = 本益比分位（反向）35% + 淨值比分位（反向）25%"
            " + 殖利率 25% + 本益比（反向）15%。"
        ),
        "sort": "value",
        "order": "desc",
        "filters": [
            {"key": "pe_percentile_250d", "max": 30},
            {"key": "dividend_yield_pct", "min": 4},
            {"key": "eps_ttm", "min": 0.0001},
            _LIQUID_1E7,
        ],
        "columns": [
            "value",
            "pe_ratio",
            "pe_percentile_250d",
            "dividend_yield_pct",
            "pb_ratio",
            "roe_ttm_pct",
        ],
    },
    {
        "key": "earnings_growth",
        "label": "獲利成長",
        "description": (
            "最新單季 EPS 年增 20% 以上、近 3 月營收年增 10% 以上、"
            "毛利率不低於去年同季，且近 20 日均成交值 1,000 萬元以上，依成長與獲利分數排序。"
        ),
        "sort": "fundamental",
        "order": "desc",
        "filters": [
            {"key": "eps_yoy_pct", "min": 20},
            {"key": "revenue_yoy_3m_pct", "min": 10},
            {"key": "gross_margin_change_pct", "min": 0},
            _LIQUID_1E7,
        ],
        "columns": [
            "fundamental",
            "eps_yoy_pct",
            "revenue_yoy_3m_pct",
            "gross_margin_change_pct",
            "roe_ttm_pct",
            "pe_ratio",
        ],
    },
    {
        "key": "sbl_high_inst_buy",
        "label": "借券偏高且法人買超",
        "description": (
            "借券賣出餘額佔股本 2% 以上、法人 20 日淨買超、20 日報酬為正，"
            "依借券佔股本排序。僅陳列資料，借券與法人方向不代表後勢。"
        ),
        "sort": "sbl_pct_of_shares",
        "order": "desc",
        "filters": [
            {"key": "sbl_pct_of_shares", "min": 2},
            {"key": "inst_flow_ratio_20d", "min": 0.0001},
            {"key": "ret_20d", "min": 0.0001},
        ],
        "columns": [
            "sbl_pct_of_shares",
            "sbl_change_20d_pct",
            "days_to_cover",
            "inst_flow_ratio_20d",
            "ret_20d",
            "flow",
        ],
    },
    {
        "key": "futures_oi_up",
        "label": "個股期貨未平倉增加",
        "description": "有個股期貨且全部契約未平倉量較 5 個交易日前增加 10% 以上，依增加幅度排序。",
        "sort": "futures_oi_change_5d_pct",
        "order": "desc",
        "filters": [
            {"key": "has_futures", "equals": True},
            {"key": "futures_oi_change_5d_pct", "min": 10},
        ],
        "columns": [
            "futures_oi_change_5d_pct",
            "futures_basis_pct",
            "ret_5d",
            "inst_flow_ratio_20d",
            "flow",
        ],
    },
    {
        "key": "chip_transfer",
        "label": "籌碼換手（法人買、融資退）",
        "description": "法人 20 日淨買超且融資餘額減少，依法人買超佔成交量排序。",
        "sort": "inst_flow_ratio_20d",
        "order": "desc",
        "filters": [{"key": "chip_transfer_20d", "equals": True}],
        "columns": [
            "inst_flow_ratio_20d",
            "total_net_20d",
            "margin_change_20d_pct",
            "ret_20d",
            "flow",
        ],
    },
    {
        "key": "stable",
        "label": "低波動穩健",
        "description": "近 60 日報酬不為負，依穩定度分數排序。",
        "sort": "stability",
        "order": "desc",
        "filters": [{"key": "ret_60d", "min": 0}, _LIQUID_2E7],
        "columns": [
            "stability",
            "volatility_20d",
            "max_drawdown_60d",
            "ret_60d",
            "avg_turnover_20d",
        ],
    },
]
for _preset in PRESETS:
    _preset["exclude_risk"] = True  # every preset leaves out 處置股 by default
    # The metrics its filters and sort depend on (scores are always computed): when every
    # stock has none of one of them yet, an empty result only means history is still filling.
    _preset.setdefault(
        "requires",
        list(
            dict.fromkeys(
                key
                for key in [*(item["key"] for item in _preset["filters"]), _preset["sort"]]
                if key in METRIC_BY_KEY
            )
        ),
    )
PRESET_BY_KEY = {preset["key"]: preset for preset in PRESETS}

# Metrics the MCP tool always includes next to a preset's own columns.
MCP_DEFAULT_COLUMNS = [
    "ret_20d",
    "total_net_20d",
    "inst_flow_ratio_20d",
    "foreign_streak",
    "trust_streak",
    "margin_change_20d_pct",
    "revenue_yoy_pct",
    "big_holder_pct",
    "big_holder_pct_change",
    "foreign_holding_pct",
    "market_cap",
    "pe_ratio",
    "pe_percentile_250d",
    "dividend_yield_pct",
    "eps_yoy_pct",
    "sbl_pct_of_shares",
    "day_trade_ratio_pct",
    "pledge_ratio_pct",
    "futures_oi_change_5d_pct",
    "next_ex_date",
]


class ScreenerQueryError(ValueError):
    """The caller asked for an unknown key, preset or malformed filter."""


# ---------------------------------------------------------------------- API models


class ScreenerEntry(BaseModel):
    rank: int | None = None
    symbol: str
    name: str
    market: str
    industry_code: str | None = None
    industry_name: str | None = None
    close: float
    change_pct: float | None = None
    avg_turnover_20d: float | None = None
    composite: float | None = None
    scores: dict[str, float | None]
    metrics: dict[str, Any]
    highlights: list[str]
    flags: list[str]


class ScreenerResult(BaseModel):
    version: str = VERSION
    market: str
    as_of: date | None
    data_through: date | None
    flow_through: date | None
    preset: str | None
    sort: str
    order: str
    universe: int
    matched: int
    limit: int
    offset: int
    snapshot_pending: bool
    notice: str | None = None
    # metric keys with no value for any stock of the snapshot (history still filling);
    # compared with a preset's ``requires`` by the UI to explain an empty result
    unavailable_metrics: list[str] = []
    entries: list[ScreenerEntry]


class StockFactors(BaseModel):
    version: str = VERSION
    symbol: str
    as_of: date | None
    data_through: date | None
    flow_through: date | None
    universe: int
    notice: str | None = None
    entry: ScreenerEntry | None


class ScreenFilter(BaseModel):
    key: str
    min: float | None = None
    max: float | None = None


def finite_bound(value: Any, raw: str = "") -> float | None:
    """A filter bound as a finite float (``None`` stays ``None``); anything else is an error."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ScreenerQueryError(f"filter bound must be a number: {raw or value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ScreenerQueryError(f"filter bound must be a number: {raw or value!r}") from None
    if not stdmath.isfinite(number):
        raise ScreenerQueryError(f"filter bound must be finite: {raw or value!r}")
    return number


def parse_filter_specs(items: list[Any] | None) -> tuple[list[ScreenFilter], list[str]]:
    """Validate MCP-style filters: ``{key, min?, max?}`` bounds and ``{key, equals: true}``.

    Returns ``(numeric bounds, boolean flag keys)``; raises :class:`ScreenerQueryError`
    with a readable message for anything malformed (``equals: false`` included).
    """
    numeric: list[ScreenFilter] = []
    flags: list[str] = []
    for index, item in enumerate(items or []):
        if not isinstance(item, dict):
            raise ScreenerQueryError(f"filter #{index + 1} must be an object with a key")
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ScreenerQueryError(f"filter #{index + 1} is missing its key")
        key = key.strip()
        if "equals" in item:
            if item["equals"] is not True:
                raise ScreenerQueryError(
                    f"filter {key}: only equals=true is supported; "
                    "omit the filter instead of using equals=false"
                )
            flags.append(key)
            continue
        low = finite_bound(item.get("min"))
        high = finite_bound(item.get("max"))
        if low is None and high is None:
            raise ScreenerQueryError(f"filter {key} needs min, max or equals=true")
        numeric.append(ScreenFilter(key=key, min=low, max=high))
    return numeric, flags


def _entry(row: MarketFactorSnapshot, rank: int | None = None) -> ScreenerEntry:
    return ScreenerEntry(
        rank=rank,
        symbol=row.symbol,
        name=row.name,
        market=row.market,
        industry_code=row.industry_code,
        industry_name=row.industry_name,
        close=row.close,
        change_pct=row.change_pct,
        avg_turnover_20d=row.avg_turnover_20d,
        composite=row.composite,
        scores={key: row.scores.get(key) for key in SCORE_KEYS},
        metrics=row.metrics,
        highlights=row.highlights,
        flags=row.flags,
    )


# ------------------------------------------------------------------- snapshot job


COMPLETE_RATIO = 0.6  # a market day needs this share of the previous session's rows
MARKETS = ("TW", "TWO")
# The official feeds a snapshot reads are fingerprinted per date; the fingerprint of the
# snapshot as built is kept in fetch_coverage (one row per date, ``row_count`` = fingerprint).
SIGNATURE_SOURCE = "screener_snapshot"
SIGNATURE_DATASET = "feed_signature"
FEED_LOOKBACK_DAYS = 45  # calendar days of feed history that can reach a 60-session metric
FEED_RECENT_DATES = 30  # newest visible dates per feed that enter the fingerprint


def _session(
    calendar: dict[str, list[MarketSession]], market: str, day: date
) -> tuple[MarketSession | None, MarketSession | None]:
    """``(session on day, the market's previous session)``."""
    previous: MarketSession | None = None
    for item in calendar.get(market, []):
        if item.trading_date == day:
            return item, previous
        if item.trading_date < day:
            previous = item
    return None, previous


def market_day_complete(calendar: dict[str, list[MarketSession]], day: date) -> bool:
    """Both markets have rows on ``day`` and each has at least 60% of its previous session."""
    for market in MARKETS:
        current, previous = _session(calendar, market, day)
        if current is None or current.rows <= 0:
            return False
        if previous is not None and current.rows < COMPLETE_RATIO * previous.rows:
            return False
    return True


def _source_flow(calendar: dict[str, list[MarketSession]], market: str, day: date) -> date | None:
    reported = [
        item.trading_date
        for item in calendar.get(market, [])
        if item.reported and item.trading_date <= day
    ]
    return max(reported, default=None)


def _signature(calendar: dict[str, list[MarketSession]], day: date) -> tuple:
    parts = []
    for market in MARKETS:
        current, _ = _session(calendar, market, day)
        parts.append(
            (
                market,
                current.rows if current else 0,
                current.available_at if current else None,
                _source_flow(calendar, market, day),
            )
        )
    return tuple(parts)


def feed_signature(stats: dict[str, list[FeedDayStat]], day: date) -> int:
    """Fingerprint of what the feeds show as of the end of ``day`` (what ``compute`` reads).

    Per feed, its newest visible trading dates with their row counts and last
    ``available_at``. It changes when rows appear, vanish or get a different ``available_at``
    within the visible range - including a re-stamp that *lowers* an ``available_at`` so a
    row becomes visible to this date, which a "written after the snapshot" test would miss.
    Rows of a date only partly visible at the cutoff contribute the cutoff, not their count,
    so later writes for that date do not change the fingerprint.
    """
    cutoff = as_of_cutoff(day)
    parts: list[tuple[str, tuple]] = []
    for name in FEEDS:
        seen = []
        for item in stats.get(name, []):
            if item.trading_date > day or item.first_available_at > cutoff:
                continue
            complete = item.last_available_at <= cutoff
            seen.append(
                (
                    item.trading_date.isoformat(),
                    item.rows if complete else None,
                    min(item.last_available_at, cutoff).isoformat(),
                )
            )
        parts.append((name, tuple(seen[-FEED_RECENT_DATES:])))
    return zlib.crc32(repr(parts).encode()) & 0x7FFFFFFF


def _is_stale(
    calendar: dict[str, list[MarketSession]], day: date, state: MarketFactorState
) -> bool:
    """The source changed after the snapshot was written (see ``refresh``)."""
    for market in MARKETS:
        current, _ = _session(calendar, market, day)
        if current is None:
            continue
        built = state.rows.get(market, 0)
        if current.available_at > state.available_at:
            return True
        # Young listings are legitimately absent from a snapshot, so allow a small gap.
        if built == 0 or current.rows - built > max(10, 0.02 * current.rows):
            return True
        source_flow = _source_flow(calendar, market, day)
        built_flow = state.flow_through.get(market)
        if source_flow is not None and (built_flow is None or source_flow > built_flow):
            return True
    return False


class ScreenerSnapshotService:
    """Computes and stores the factor snapshot. Run by the sync worker only."""

    def __init__(
        self,
        repository: QuantRepository,
        *,
        retention_sessions: int = 60,
        backfill_sessions: int = 5,
        clock: Callable[[], datetime] | None = None,
        settled: dict[date, tuple[tuple, bool]] | None = None,
    ) -> None:
        self.repository = repository
        self.retention = retention_sessions
        self.backfill = backfill_sessions
        self._clock = clock or (lambda: datetime.now(UTC))
        # date -> source signature already computed (published or empty); the upkeep does
        # not redo a date until its source rows change. Callers that outlive one call
        # (the worker) pass their own dict.
        self._settled = settled if settled is not None else {}

    async def _feed_signatures(self, days: list[date]) -> dict[date, int]:
        """One grouped query per feed for the whole range, then a fingerprint per date."""
        stats = await self.repository.feed_day_stats(
            min(days) - timedelta(days=FEED_LOOKBACK_DAYS), max(days)
        )
        return {day: feed_signature(stats, day) for day in days}

    async def compute(self, trading_date: date) -> int:
        """(Re)build the snapshot for ``trading_date``; returns the rows written.

        Returns 0 and writes nothing when the date is not publishable: a market is
        missing or far short of its previous session (a snapshot already stored for it
        is deleted), or no stock qualifies.
        """
        repo = self.repository
        calendar = await repo.market_session_calendar(trading_date, HISTORY_SESSIONS)
        if not market_day_complete(calendar, trading_date):
            removed = await repo.delete_market_factors(trading_date, VERSION)
            logger.info(
                "screener: %s not published, a market's rows are missing or incomplete%s",
                trading_date,
                " (stored snapshot removed)" if removed else "",
            )
            return 0
        # Fingerprint first: rows written while the snapshot is being built make the next
        # refresh see a different fingerprint and rebuild it.
        feed_signature_now = (await self._feed_signatures([trading_date]))[trading_date]
        sessions = {m: [i.trading_date for i in items] for m, items in calendar.items()}
        reported = {
            m: {i.trading_date for i in items if i.reported} for m, items in calendar.items()
        }
        cutoff = as_of_cutoff(trading_date)
        industries = {item.symbol: item for item in await repo.list_market_industries()}
        revenue = {item.symbol: item for item in await repo.latest_monthly_revenue(cutoff)}
        risk: dict[str, set[str]] = {}
        for flag in await repo.active_risk_flags(cutoff):
            risk.setdefault(flag.symbol, set()).add(flag.kind)
        flow_through = await repo.market_flow_through(trading_date)
        holders = await repo.latest_shareholding_pairs(cutoff)
        foreign = await repo.latest_foreign_holdings(cutoff)
        # One whole-market read per feed, all at the same cutoff (point-in-time).
        valuation = await repo.latest_valuation_metrics(cutoff)
        trends = await repo.revenue_trends(cutoff)
        earnings = await repo.earnings_summaries(cutoff)
        short_lending = await repo.latest_short_lending_metrics(cutoff)
        day_trade = await repo.latest_day_trade_metrics(cutoff)
        pledges = await repo.latest_insider_pledges(cutoff)
        futures = await repo.latest_stock_futures_metrics(cutoff)
        events = await repo.next_dividend_events(cutoff)

        staged: list[tuple[str, str, str, float, dict[str, Any], list[str], date | None]] = []
        async for symbol, name, market, rows in repo.iter_market_histories(
            trading_date, HISTORY_SESSIONS
        ):
            if not rows or rows[-1][0] != trading_date:
                continue  # not traded on the snapshot date
            close = rows[-1][1]
            rows = math.align_rows(rows, sessions.get(market, []), reported.get(market, set()))
            through = flow_through.get(market)
            metrics = math.compute_metrics(
                math.history_from_rows(symbol, name, market, rows),
                through,
                holders.get(symbol),
                foreign.get(symbol),
                valuation=valuation.get(symbol),
                trend=trends.get(symbol),
                earnings=earnings.get(symbol),
                short_lending=short_lending.get(symbol),
                day_trade=day_trade.get(symbol),
                pledge=pledges.get(symbol),
                futures=futures.get(symbol),
                has_futures=(symbol in futures) if futures else None,
                event=events.get(symbol),
            )
            if metrics is None:
                continue
            item = revenue.get(symbol)
            metrics["revenue_period"] = item.period if item else None
            metrics["revenue_yoy_pct"] = item.yoy_pct if item else None
            metrics["revenue_mom_pct"] = item.mom_pct if item else None
            metrics["revenue_cum_yoy_pct"] = item.cumulative_yoy_pct if item else None
            flag_set = set(risk.get(symbol, ()))
            pledge_ratio = metrics.get("pledge_ratio_pct")
            if pledge_ratio is not None and pledge_ratio >= PLEDGE_FLAG_MIN:
                flag_set.add("high_pledge")
            flags = sorted(flag_set)
            staged.append((symbol, name, market, close, metrics, flags, through))

        signature = (*_signature(calendar, trading_date), feed_signature_now)
        if not staged:
            self._settled[trading_date] = (signature, False)
            logger.info("screener: %s computed no rows, nothing stored", trading_date)
            return 0

        scored = math.score_universe([item[4] for item in staged])
        now = self._clock()
        snapshots: list[MarketFactorSnapshot] = []
        for (symbol, name, market, close, metrics, flags, through), (scores, composite) in zip(
            staged, scored, strict=True
        ):
            industry = industries.get(symbol)
            snapshots.append(
                MarketFactorSnapshot(
                    symbol=symbol,
                    trading_date=trading_date,
                    version=VERSION,
                    name=name,
                    market=market,  # type: ignore[arg-type]
                    industry_code=industry.industry_code if industry else None,
                    industry_name=industry.industry_name if industry else None,
                    close=close,
                    change_pct=metrics.get("change_pct"),
                    avg_turnover_20d=metrics.get("avg_turnover_20d"),
                    composite=composite,
                    scores=scores,
                    metrics=metrics,
                    highlights=math.build_highlights(metrics, flags),
                    flags=flags,
                    flow_through=through,
                    available_at=now,
                )
            )
        written = await repo.replace_market_factors(trading_date, VERSION, snapshots)
        await repo.prune_market_factors(VERSION, self.retention)
        await repo.delete_other_factor_versions(VERSION)  # old scoring versions are never served
        await repo.record_fetch_coverage(
            [
                FetchCoverage(
                    source=SIGNATURE_SOURCE,
                    dataset=SIGNATURE_DATASET,
                    symbol="*",
                    period=trading_date.isoformat(),
                    status="ok",
                    row_count=feed_signature_now,
                    fetched_at=now,
                )
            ]
        )
        self._settled[trading_date] = (signature, True)
        return written

    async def refresh(self, *, recompute_latest: bool = True) -> list[date]:
        """Catch up on missing dates, repair stale ones and (optionally) rebuild the newest.

        Targets among the newest ``backfill_sessions`` dates: dates newer than the newest
        snapshot; dates whose snapshot fails the both-markets-complete rule (``compute``
        deletes it); dates whose source rows changed after the snapshot was written
        (newer ``available_at``, per-market row counts or institutional report dates that
        differ), or whose valuation / 借券 / 當沖 / foreign-holding / stock-futures / TDCC
        inputs look different now than when it was built (``feed_signature``; a snapshot
        built before that fingerprint existed is rebuilt once); and the newest date when
        ``recompute_latest``. A date already computed
        for the same source rows is not retried. Returns the dates published, oldest first.
        """
        repo = self.repository
        dates = await repo.list_market_trading_dates(None, as_of=None, limit=self.backfill)
        if not dates:
            return []
        newest = await repo.latest_market_factor_date(VERSION)
        calendar = await repo.market_session_calendar(dates[0], HISTORY_SESSIONS)
        states = await repo.market_factor_state(VERSION, dates)
        feeds_now = await self._feed_signatures(dates)
        built_with = await repo.list_fetch_coverage(
            SIGNATURE_SOURCE, SIGNATURE_DATASET, "*", [day.isoformat() for day in dates]
        )
        targets: set[date] = set()
        for day in dates:
            state = states.get(day)
            signature = (*_signature(calendar, day), feeds_now[day])
            entry = built_with.get(day.isoformat())
            feeds_changed = entry is None or entry.row_count != feeds_now[day]
            seen = self._settled.get(day)
            fresh = seen is not None and seen[0] == signature
            if state is None:
                empty_before = seen == (signature, False)
                if (newest is None or day > newest) and not empty_before:
                    targets.add(day)
            elif not market_day_complete(calendar, day):
                targets.add(day)
            elif not fresh and (_is_stale(calendar, day, state) or feeds_changed):
                targets.add(day)
        if recompute_latest:
            targets.add(dates[0])
        done: list[date] = []
        for day in sorted(targets):
            if await self.compute(day):
                done.append(day)
        return done


async def refresh_screener(
    repository: QuantRepository,
    *,
    enabled: bool,
    retention_sessions: int,
    backfill_sessions: int,
    recompute_latest: bool = True,
    settled: dict[date, tuple[tuple, bool]] | None = None,
) -> list[date]:
    """Worker entry point: never raises, so a factor failure cannot fail a sync job."""
    if not enabled:
        return []
    try:
        return await ScreenerSnapshotService(
            repository,
            retention_sessions=retention_sessions,
            backfill_sessions=backfill_sessions,
            settled=settled,
        ).refresh(recompute_latest=recompute_latest)
    except Exception:
        logger.exception("screener snapshot refresh failed")
        return []


# ------------------------------------------------------------------------- read side


def screener_meta_factors() -> list[dict[str, Any]]:
    factors: list[dict[str, Any]] = []
    for key in ("composite", *SCORE_KEYS):
        label, description = FACTOR_LABELS[key]
        if key == "composite":
            inputs = [{"key": k, "weight": w} for k, w in COMPOSITE_WEIGHTS.items()]
        else:
            inputs = []
            for metric, weight, how in FAMILY_INPUTS[key]:
                item: dict[str, Any] = {"key": metric, "weight": weight}
                if how != "id":
                    item["invert"] = True  # lower raw value scores higher
                if how == "neg_abs":
                    item["absolute"] = True  # |value|: smaller magnitude scores higher
                inputs.append(item)
        factors.append(
            {
                "key": key,
                "label": label,
                "short": SHORT_LABELS[key],
                "kind": "score",
                "group": "score",
                "unit": "分",
                "description": description,
                "higher_is_better": True,
                "inputs": inputs,
            }
        )
    return factors


def screener_meta_metrics() -> list[dict[str, Any]]:
    return [
        {
            "key": m.key,
            "label": m.label,
            "short": SHORT_LABELS[m.key],
            "group": m.group,
            "unit": m.unit,
            "decimals": m.decimals,
            "description": m.description,
            "higher_is_better": m.higher_is_better,
            "kind": m.kind,
        }
        for m in METRICS
    ]


def unavailable_metrics(rows: list[MarketFactorSnapshot]) -> list[str]:
    """Metric keys that are None for every stock of the snapshot (one pass over loaded rows)."""
    present: set[str] = set()
    for row in rows:
        present.update(key for key, value in row.metrics.items() if value is not None)
    if not rows:
        return []
    return [metric.key for metric in METRICS if metric.key not in present]


class ScreenerService:
    """Reads one snapshot date and filters / sorts / paginates it in Python."""

    def __init__(self, repository: QuantRepository, *, retention_sessions: int = 60) -> None:
        self.repository = repository
        self.retention = retention_sessions

    async def _out_of_range(self, as_of: date | None) -> str | None:
        """A notice when snapshots exist but none is on or before ``as_of``."""
        if as_of is None or await self.repository.latest_market_factor_date(VERSION) is None:
            return None
        return f"這個日期超出選股資料的保留範圍（最近 {self.retention} 個交易日）"

    async def _load(self, as_of: date | None) -> tuple[date | None, list[MarketFactorSnapshot]]:
        day = await self.repository.latest_market_factor_date(VERSION, as_of)
        if day is None:
            return None, []
        return day, await self.repository.list_market_factors(day, VERSION)

    async def meta(self) -> dict[str, Any]:
        _, rows = await self._load(None)
        counts: dict[str, tuple[str, int]] = {}
        for row in rows:
            if row.industry_code:
                name, count = counts.get(row.industry_code, (row.industry_name or "", 0))
                counts[row.industry_code] = (name, count + 1)
        return {
            "version": VERSION,
            "factors": screener_meta_factors(),
            "metrics": screener_meta_metrics(),
            "groups": [
                {
                    "key": key,
                    "label": label,
                    **({"note": GROUP_NOTES[key]} if key in GROUP_NOTES else {}),
                }
                for key, label in GROUPS
            ],
            "presets": [
                {k: v for k, v in preset.items() if k != "exclude_risk"} for preset in PRESETS
            ],
            "industries": [
                {"code": code, "name": name, "count": count}
                for code, (name, count) in sorted(counts.items())
            ],
        }

    @staticmethod
    def _value(row: MarketFactorSnapshot, key: str) -> Any:
        if key == "composite":
            return row.composite
        if key in SCORE_KEYS:
            return row.scores.get(key)
        return row.metrics.get(key)

    async def screen(
        self,
        *,
        market: Literal["ALL", "TW", "TWO"] = "ALL",
        as_of: date | None = None,
        preset: str | None = None,
        sort: str | None = None,
        order: Literal["desc", "asc"] | None = None,
        filters: list[ScreenFilter] | None = None,
        flags: list[str] | None = None,
        industries: list[str] | None = None,
        exclude_risk: bool | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ScreenerResult:
        base = PRESET_BY_KEY.get(preset) if preset else None
        if preset and base is None:
            raise ScreenerQueryError(f"unknown preset: {preset}")
        sort_key = sort or (base["sort"] if base else "composite")
        direction = order or (base["order"] if base else "desc")
        if sort_key not in SORT_KEYS:
            raise ScreenerQueryError(f"unknown sort key: {sort_key}")
        bounds: dict[str, tuple[float | None, float | None]] = {}
        required: set[str] = set()
        for item in base["filters"] if base else []:
            if item.get("equals"):
                required.add(item["key"])
            else:
                bounds[item["key"]] = (item.get("min"), item.get("max"))
        for item in filters or []:
            if item.key not in FILTER_KEYS:
                raise ScreenerQueryError(f"unknown filter key: {item.key}")
            bounds[item.key] = (item.min, item.max)
        for key in flags or []:
            if key not in BOOL_KEYS:
                raise ScreenerQueryError(f"not a flag metric: {key}")
            required.add(key)
        drop_risk = True if exclude_risk is None else exclude_risk
        limit = max(1, min(limit, MAX_LIMIT))
        offset = max(0, offset)

        day, rows = await self._load(as_of)
        if day is None:
            notice = await self._out_of_range(as_of)
            return ScreenerResult(
                market=market,
                as_of=as_of,
                data_through=None,
                flow_through=None,
                preset=preset,
                sort=sort_key,
                order=direction,
                universe=0,
                matched=0,
                limit=limit,
                offset=offset,
                snapshot_pending=notice is None,
                notice=notice,
                entries=[],
            )
        unavailable = unavailable_metrics(rows)
        if market != "ALL":
            rows = [row for row in rows if row.market == market]
        universe = len(rows)
        wanted_industries = set(industries or [])
        needle = (q or "").strip().lower()

        def keep(row: MarketFactorSnapshot) -> bool:
            if drop_risk and "disposition" in row.flags:
                return False
            if wanted_industries and row.industry_code not in wanted_industries:
                return False
            if needle and needle not in row.symbol.lower() and needle not in row.name.lower():
                return False
            if any(self._value(row, key) is not True for key in required):
                return False
            for key, (low, high) in bounds.items():
                if low is None and high is None:
                    continue
                value = self._value(row, key)
                if value is None or isinstance(value, bool):
                    return False
                if low is not None and value < low:
                    return False
                if high is not None and value > high:
                    return False
            return True

        matched = [row for row in rows if keep(row)]
        matched.sort(key=lambda row: row.symbol)
        sortable = [isinstance(self._value(row, sort_key), (int, float)) for row in matched]
        known = [row for row, ok in zip(matched, sortable, strict=True) if ok]
        unknown = [row for row, ok in zip(matched, sortable, strict=True) if not ok]
        known.sort(key=lambda row: float(self._value(row, sort_key)), reverse=direction == "desc")
        ordered = known + unknown
        page = ordered[offset : offset + limit]
        flow_dates = [row.flow_through for row in rows if row.flow_through]
        return ScreenerResult(
            market=market,
            as_of=as_of or day,
            data_through=day,
            flow_through=min(flow_dates, default=None),
            preset=preset,
            sort=sort_key,
            order=direction,
            universe=universe,
            matched=len(matched),
            limit=limit,
            offset=offset,
            snapshot_pending=False,
            unavailable_metrics=unavailable,
            entries=[_entry(row, offset + i + 1) for i, row in enumerate(page)],
        )

    async def stock(self, raw_symbol: str, *, as_of: date | None = None) -> StockFactors:
        try:
            symbol = await normalize_listed_symbol(self.repository, raw_symbol)
        except StockNotFound:
            symbol = raw_symbol.strip().upper()
        day, rows = await self._load(as_of)
        notice = await self._out_of_range(as_of) if day is None else None
        flow_dates = [row.flow_through for row in rows if row.flow_through]
        target = next((row for row in rows if row.symbol == symbol), None)
        entry = None
        if target is not None:
            ranked = sorted(
                (row for row in rows if row.composite is not None),
                key=lambda row: (-(row.composite or 0.0), row.symbol),
            )
            position = next((i + 1 for i, row in enumerate(ranked) if row is target), None)
            entry = _entry(target, position)
        return StockFactors(
            symbol=symbol,
            as_of=as_of or day,
            data_through=day,
            flow_through=target.flow_through if target else min(flow_dates, default=None),
            universe=len(rows),
            notice=notice,
            entry=entry,
        )

    async def pending(self) -> bool:
        return await self.repository.has_active_sync_job(SyncJobKind.MARKET)


def analyst_view(result: ScreenerResult, columns: list[str]) -> dict[str, Any]:
    """The payload trimmed for MCP: scores, highlights and a handful of metrics per row."""
    wanted = [key for key in columns if key in METRIC_BY_KEY]
    payload = result.model_dump(mode="json")
    payload["entries"] = [
        {
            "rank": entry["rank"],
            "symbol": entry["symbol"],
            "name": entry["name"],
            "industry_name": entry["industry_name"],
            "close": entry["close"],
            "change_pct": entry["change_pct"],
            "composite": entry["composite"],
            "scores": entry["scores"],
            "metrics": {key: entry["metrics"].get(key) for key in wanted},
            "highlights": entry["highlights"],
            "flags": entry["flags"],
        }
        for entry in payload["entries"]
    ]
    payload["note"] = "確定性的歷史資料排名，非投資建議。"
    return payload
