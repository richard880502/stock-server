"""Read side of valuation, revenue trend and quarterly earnings for one stock."""

from __future__ import annotations

from datetime import date

from quant_signal.application.official_queries import as_of_cutoff
from quant_signal.application.ports import QuantRepository
from quant_signal.application.stocks import normalize_listed_symbol
from quant_signal.domain.fundamentals import (
    PERCENTILE_WINDOW,
    EarningsSection,
    RevenueSection,
    StockFundamentals,
    ValuationPoint,
    ValuationSection,
)
from quant_signal.infrastructure.providers.fundamentals import MOPS_REVENUE_SOURCE
from quant_signal.quant.quarterly import earnings_metrics, quarter_points
from quant_signal.quant.revenue_trend import revenue_trend
from quant_signal.quant.valuation import weekly_sample

HISTORY_POINTS = 60
REVENUE_FETCH_MONTHS = 48  # 36 shown plus the year-earlier twins for the rolling windows

NOTE_VALUATION = (
    "本益比、股價淨值比、殖利率為證交所／櫃買中心每日公布值（本益比以近四季稅後淨利計算，"
    "虧損公司無本益比）；百分位為該股自身最近 250 個交易日的相對位置（100 代表最貴／殖利率最高），"
    "觀察值不足 60 筆時不提供。"
)
NOTE_VALUATION_SHORT = (
    "本系統的估值歷史自啟用日起逐步回補，尚不足 250 個交易日，百分位的觀察期較短。"
)
NOTE_REVENUE_BACKFILL = (
    "較早月份的月營收取自公開資訊觀測站彙總表，可得時點以法定申報期限（次月 10 日）估計，"
    "實際公告可能更早；數值為目前最新（可能含更正）。"
)
NOTE_REVENUE_UNITS = "月營收單位為新台幣仟元；年增率／月增率為公告值，缺漏時由同月／上月營收推算。"
NOTE_EARNINGS_CUMULATIVE = (
    "財報損益為年初至當季的累計值，單季數字由相鄰累計值相減推得（EPS 亦同，與公司公告的單季 EPS "
    "可能因股數變動略有差異）；金額單位為新台幣仟元，EPS 為元。"
)
NOTE_EARNINGS_BACKFILL = (
    "較早季度財報取自公開資訊觀測站，可得時點以法定申報期限"
    "（Q1 5/15、Q2 8/14、Q3 11/14、年報 3/31）估計，實際公告可能更早；"
    "數值為目前最新（可能含更正）。"
)
NOTE_EARNINGS_FINANCIAL = (
    "金融保險類（銀行、金控、保險、證券期貨、異業）財報科目不同，僅提供 EPS、淨利與權益相關指標，"
    "不計算毛利率、營業利益率與營收。"
)
NOTE_ROE = "ROE 為近四季歸屬母公司業主淨利除以最新期末母公司業主權益（非平均權益）。"


class StockFundamentalsService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def get(self, raw_symbol: str, *, as_of: date) -> StockFundamentals:
        symbol = await normalize_listed_symbol(self.repository, raw_symbol)
        cutoff = as_of_cutoff(as_of)
        notes: list[str] = []

        valuation: ValuationSection | None = None
        metrics = (await self.repository.latest_valuation_metrics(cutoff, [symbol])).get(symbol)
        if metrics is not None:
            days = await self.repository.valuation_history(symbol, cutoff, 300)
            valuation = ValuationSection(
                date=metrics.trading_date,
                pe_ratio=metrics.pe_ratio,
                pb_ratio=metrics.pb_ratio,
                dividend_yield_pct=metrics.dividend_yield_pct,
                pe_percentile_250d=metrics.pe_percentile_250d,
                pb_percentile_250d=metrics.pb_percentile_250d,
                yield_percentile_250d=metrics.yield_percentile_250d,
                observations=metrics.observations,
                history=[
                    ValuationPoint(
                        date=day.trading_date,
                        pe_ratio=day.pe_ratio,
                        pb_ratio=day.pb_ratio,
                        dividend_yield_pct=day.dividend_yield_pct,
                    )
                    for day in weekly_sample(days, limit=HISTORY_POINTS)
                ],
            )
            notes.append(NOTE_VALUATION)
            if len(days) < PERCENTILE_WINDOW:
                notes.append(NOTE_VALUATION_SHORT)

        revenue: RevenueSection | None = None
        rows = await self.repository.monthly_revenue(symbol, cutoff, REVENUE_FETCH_MONTHS)
        trend = revenue_trend(rows)
        if trend is not None:
            revenue = RevenueSection(
                months=trend.months,
                yoy_3m_pct=trend.yoy_3m_pct,
                yoy_12m_pct=trend.yoy_12m_pct,
                is_12m_high=trend.is_12m_high,
                growth_streak_months=trend.growth_streak_months,
            )
            notes.append(NOTE_REVENUE_UNITS)
            if any(row.source == MOPS_REVENUE_SOURCE for row in rows):
                notes.append(NOTE_REVENUE_BACKFILL)

        earnings: EarningsSection | None = None
        statements = await self.repository.quarterly_financials(symbol, cutoff)
        summary = earnings_metrics(statements)
        if summary is not None:
            earnings = EarningsSection(
                quarters=quarter_points(statements, 8),
                eps_ttm=summary.eps_ttm,
                eps_yoy_pct=summary.eps_yoy_pct,
                roe_ttm_pct=summary.roe_ttm_pct,
            )
            notes.append(NOTE_EARNINGS_CUMULATIVE)
            notes.append(NOTE_ROE)
            if summary.statement_kind == "financial":
                notes.append(NOTE_EARNINGS_FINANCIAL)
            if any(row.source.startswith("mops") for row in statements):
                notes.append(NOTE_EARNINGS_BACKFILL)

        if valuation is None and revenue is None and earnings is None:
            notes.append("尚無此股票的估值、營收與財報資料（資料自系統啟用日起累積）。")
        return StockFundamentals(
            symbol=symbol,
            as_of=as_of,
            valuation=valuation,
            revenue=revenue,
            earnings=earnings,
            notes=notes,
        )
