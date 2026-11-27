"""Whole-market multi-factor screener: snapshot computation and read side.

The snapshot (``market_factor_daily``) is written only by the sync worker, after market
syncs and by an upkeep catch-up; API and MCP requests only read it. Everything is a
deterministic ranking of official data, not a recommendation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from quant_signal.application.official_queries import as_of_cutoff
from quant_signal.application.ports import QuantRepository
from quant_signal.application.stocks import StockNotFound, normalize_listed_symbol
from quant_signal.domain.models import MarketFactorSnapshot, SyncJobKind
from quant_signal.quant import screener as math
from quant_signal.quant.screener import (
    BOOL_KEYS,
    COMPOSITE_WEIGHTS,
    FACTOR_LABELS,
    FAMILY_INPUTS,
    GROUPS,
    HISTORY_SESSIONS,
    METRIC_BY_KEY,
    METRICS,
    NUMERIC_KEYS,
    SCORE_KEYS,
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
        "columns": ["composite", "momentum", "flow", "fundamental", "stability"],
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
        "label": "低調吸籌",
        "description": "法人 60 日持續買超、近 20 日股價漲跌幅在 ±8% 內，依低調吸籌分數排序。",
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
        "description": "最新月營收年增率達 20% 以上，依營收分數排序。",
        "sort": "fundamental",
        "order": "desc",
        "filters": [{"key": "revenue_yoy_pct", "min": 20}, _LIQUID_1E7],
        "columns": [
            "fundamental",
            "revenue_yoy_pct",
            "revenue_mom_pct",
            "revenue_cum_yoy_pct",
            "ret_20d",
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
    entries: list[ScreenerEntry]


class StockFactors(BaseModel):
    version: str = VERSION
    symbol: str
    as_of: date | None
    data_through: date | None
    flow_through: date | None
    universe: int
    entry: ScreenerEntry | None


class ScreenFilter(BaseModel):
    key: str
    min: float | None = None
    max: float | None = None


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


class ScreenerSnapshotService:
    """Computes and stores the factor snapshot. Run by the sync worker only."""

    def __init__(
        self,
        repository: QuantRepository,
        *,
        retention_sessions: int = 60,
        backfill_sessions: int = 5,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.retention = retention_sessions
        self.backfill = backfill_sessions
        self._clock = clock or (lambda: datetime.now(UTC))

    async def compute(self, trading_date: date) -> int:
        """(Re)build the snapshot for ``trading_date`` from history up to that date."""
        repo = self.repository
        cutoff = as_of_cutoff(trading_date)
        industries = {item.symbol: item for item in await repo.list_market_industries()}
        revenue = {item.symbol: item for item in await repo.latest_monthly_revenue(cutoff)}
        risk: dict[str, set[str]] = {}
        for flag in await repo.active_risk_flags(cutoff):
            risk.setdefault(flag.symbol, set()).add(flag.kind)
        flow_through = await repo.market_flow_through(trading_date)

        staged: list[tuple[str, str, str, float, dict[str, Any], list[str], date | None]] = []
        async for symbol, name, market, rows in repo.iter_market_histories(
            trading_date, HISTORY_SESSIONS
        ):
            if not rows or rows[-1][0] != trading_date:
                continue  # not traded on the snapshot date
            through = flow_through.get(market)
            metrics = math.compute_metrics(
                math.history_from_rows(symbol, name, market, rows), through
            )
            if metrics is None:
                continue
            item = revenue.get(symbol)
            metrics["revenue_period"] = item.period if item else None
            metrics["revenue_yoy_pct"] = item.yoy_pct if item else None
            metrics["revenue_mom_pct"] = item.mom_pct if item else None
            metrics["revenue_cum_yoy_pct"] = item.cumulative_yoy_pct if item else None
            flags = sorted(risk.get(symbol, ()))
            staged.append((symbol, name, market, rows[-1][1], metrics, flags, through))

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
        return written

    async def refresh(self, *, recompute_latest: bool = True) -> list[date]:
        """Catch up on missing dates and (optionally) rebuild the newest one.

        First run builds the last ``backfill_sessions`` dates; afterwards only dates newer
        than the newest snapshot. Returns the dates computed, oldest first.
        """
        dates = await self.repository.list_market_trading_dates(
            None, as_of=None, limit=self.backfill
        )
        if not dates:
            return []
        newest = await self.repository.latest_market_factor_date(VERSION)
        targets = {d for d in dates if newest is None or d > newest}
        if recompute_latest:
            targets.add(dates[0])
        done: list[date] = []
        for day in sorted(targets):
            await self.compute(day)
            done.append(day)
        return done


async def refresh_screener(
    repository: QuantRepository,
    *,
    enabled: bool,
    retention_sessions: int,
    backfill_sessions: int,
    recompute_latest: bool = True,
) -> list[date]:
    """Worker entry point: never raises, so a factor failure cannot fail a sync job."""
    if not enabled:
        return []
    try:
        return await ScreenerSnapshotService(
            repository,
            retention_sessions=retention_sessions,
            backfill_sessions=backfill_sessions,
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
            "group": m.group,
            "unit": m.unit,
            "decimals": m.decimals,
            "description": m.description,
            "higher_is_better": m.higher_is_better,
            "kind": m.kind,
        }
        for m in METRICS
    ]


class ScreenerService:
    """Reads one snapshot date and filters / sorts / paginates it in Python."""

    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

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
            "groups": [{"key": key, "label": label} for key, label in GROUPS],
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
                snapshot_pending=True,
                entries=[],
            )
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
            flow_through=max(flow_dates, default=None),
            preset=preset,
            sort=sort_key,
            order=direction,
            universe=universe,
            matched=len(matched),
            limit=limit,
            offset=offset,
            snapshot_pending=False,
            entries=[_entry(row, offset + i + 1) for i, row in enumerate(page)],
        )

    async def stock(self, raw_symbol: str, *, as_of: date | None = None) -> StockFactors:
        try:
            symbol = await normalize_listed_symbol(self.repository, raw_symbol)
        except StockNotFound:
            symbol = raw_symbol.strip().upper()
        day, rows = await self._load(as_of)
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
            flow_through=target.flow_through if target else max(flow_dates, default=None),
            universe=len(rows),
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
