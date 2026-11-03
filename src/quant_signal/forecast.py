"""Reproducible price scenarios and strictly chronological baseline evaluation."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import httpx
import numpy as np
from pydantic import BaseModel, Field

from quant_signal.domain.models import Bar
from quant_signal.quant.point_in_time import select_latest_records


class ForecastRequest(BaseModel):
    symbol: str
    as_of: date
    horizon: Literal[1, 5, 20] = 5
    lookback: int = Field(default=120, ge=60, le=512)
    model: Literal["bootstrap", "kronos"] = "bootstrap"


class ForecastPoint(BaseModel):
    trading_date: date
    median: float = Field(gt=0)
    lower: float = Field(gt=0)
    upper: float = Field(gt=0)


class ForecastEvaluation(BaseModel):
    model: str = "bootstrap"
    windows: int
    mae_pct: float | None = None
    naive_mae_pct: float | None = None
    direction_accuracy: float | None = None
    interval_coverage: float | None = None


class ForecastReport(BaseModel):
    symbol: str
    as_of: date
    data_through: date
    model: str
    lookback: int
    horizon: int
    last_close: float
    expected_return: float
    history: list[dict]
    points: list[ForecastPoint]
    evaluation: ForecastEvaluation
    warnings: list[str]


def eligible_bars(bars: list[Bar], as_of: date) -> list[Bar]:
    cutoff = datetime.combine(as_of, time.max, ZoneInfo("Asia/Taipei"))
    return select_latest_records(
        [b for b in bars if b.available_at <= cutoff],
        as_of=as_of,
    )


def price_history(bars: list[Bar]) -> np.ndarray:
    # Anchor all adjusted OHLC to the latest raw-price scale.
    anchor = bars[-1].adjustment_factor
    return np.array([b.close * b.adjustment_factor / anchor for b in bars])


def bootstrap_paths(prices: np.ndarray, horizon: int) -> np.ndarray:
    returns = np.diff(np.log(prices))
    rng = np.random.default_rng(42)
    draws = rng.choice(returns, size=(512, horizon), replace=True)
    return prices[-1] * np.exp(np.cumsum(draws, axis=1))


def evaluate_baseline(
    records: list[Bar],
    lookback: int,
    horizon: int,
    as_of: date,
) -> ForecastEvaluation:
    bars = eligible_bars(records, as_of)
    errors, naive_errors, directions, coverage = [], [], [], []
    # Non-overlapping future windows; each origin independently filters availability/revisions.
    for origin in range(len(bars) - horizon - 1, lookback - 2, -horizon):
        past = eligible_bars(records, bars[origin].trading_date)[-lookback:]
        if len(past) < lookback:
            continue
        paths = bootstrap_paths(price_history(past), horizon)[:, -1]
        actual = (
            bars[origin + horizon].close
            * bars[origin + horizon].adjustment_factor
            / past[-1].adjustment_factor
        )
        last = past[-1].close
        lower, median, upper = np.quantile(paths, [0.1, 0.5, 0.9])
        errors.append(abs(median - actual) / last)
        naive_errors.append(abs(last - actual) / last)
        directions.append(bool(np.sign(median - last) == np.sign(actual - last)))
        coverage.append(bool(lower <= actual <= upper))
        if len(errors) == 24:
            break
    if not errors:
        return ForecastEvaluation(windows=0)
    return ForecastEvaluation(
        windows=len(errors),
        mae_pct=float(np.mean(errors)),
        naive_mae_pct=float(np.mean(naive_errors)),
        direction_accuracy=float(np.mean(directions)),
        interval_coverage=float(np.mean(coverage)),
    )


def future_sessions(last: date, horizon: int) -> list[date]:
    calendar = xcals.get_calendar(
        "XTAI", start=last - timedelta(days=10), end=last + timedelta(days=180)
    )
    sessions = [session.date() for session in calendar.sessions if session.date() > last]
    return sessions[:horizon]


async def forecast(
    bars: list[Bar],
    request: ForecastRequest,
    *,
    kronos_url: str | None = None,
) -> ForecastReport:
    if not (request.symbol.endswith((".TW", ".TWO")) or request.symbol in ("^TWII", "^TWOII")):
        raise ValueError("目前預測僅支援台股、台股 ETF 與上市櫃指數")
    eligible = eligible_bars(bars, request.as_of)
    if len(eligible) < request.lookback:
        raise ValueError(
            f"需要至少 {request.lookback} 根可用日 K，目前只有 {len(eligible)} 根，請先同步歷史資料"
        )
    history = eligible[-request.lookback :]
    dates = future_sessions(history[-1].trading_date, request.horizon)
    prices = price_history(history)
    warnings = [
        "區間為模型情境的第 10–90 百分位，尚未校準，不代表保證涵蓋率。",
        "交易日依 XTAI 日曆，臨時停市及未來除權息可能影響日期與價格。",
    ]
    if any(b.source == "synthetic_demo" for b in history):
        warnings.append("輸入包含合成示範資料，結果僅供功能展示。")
    if (request.as_of - history[-1].trading_date).days > 7:
        warnings.append("資料截止日距查詢日超過七天；預測從最後一根資料往後計算。")
    if request.model == "kronos":
        if not kronos_url:
            raise ValueError("Kronos 尚未啟用，請先啟動模型服務並設定 KRONOS_URL")
        anchor = history[-1].adjustment_factor
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{kronos_url.rstrip('/')}/predict",
                json={
                    "bars": [
                        {
                            "date": b.trading_date.isoformat(),
                            **{
                                k: getattr(b, k) * b.adjustment_factor / anchor
                                for k in ("open", "high", "low", "close")
                            },
                            "volume": b.volume,
                        }
                        for b in history
                    ],
                    "dates": [d.isoformat() for d in dates],
                },
            )
            response.raise_for_status()
            paths = np.asarray(response.json()["paths"], dtype=float)
        if (
            paths.shape != (16, request.horizon)
            or not np.isfinite(paths).all()
            or (paths <= 0).any()
        ):
            raise ValueError("Kronos 回傳無效的預測路徑")
        warnings.append("Kronos 為實驗模型；下方歷史驗證屬統計基準模型，不代表 Kronos 績效。")
    else:
        paths = bootstrap_paths(prices, request.horizon)
    lower, median, upper = np.quantile(paths, [0.1, 0.5, 0.9], axis=0)
    return ForecastReport(
        symbol=request.symbol,
        as_of=request.as_of,
        data_through=history[-1].trading_date,
        model=request.model,
        lookback=len(history),
        horizon=request.horizon,
        last_close=history[-1].close,
        expected_return=float(median[-1] / history[-1].close - 1),
        history=[
            {"trading_date": b.trading_date, "close": float(p)}
            for b, p in zip(history[-40:], prices[-40:], strict=True)
        ],
        points=[
            ForecastPoint(trading_date=d, lower=float(lo), median=float(mid), upper=float(hi))
            for d, lo, mid, hi in zip(dates, lower, median, upper, strict=True)
        ],
        evaluation=evaluate_baseline(bars, request.lookback, request.horizon, request.as_of),
        warnings=warnings,
    )
