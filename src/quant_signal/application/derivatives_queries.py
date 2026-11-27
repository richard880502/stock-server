"""Read side of the TAIFEX derivatives feed (REST and MCP share these services)."""

from __future__ import annotations

from datetime import date, timedelta

from quant_signal.application.official_queries import as_of_cutoff
from quant_signal.application.ports import QuantRepository
from quant_signal.application.stocks import normalize_listed_symbol
from quant_signal.domain.derivatives import MarketDerivatives, StockFutures
from quant_signal.quant.derivatives import (
    build_market_days,
    latest_market_summary,
    oi_changes,
    stock_futures_points,
)

HISTORY_NOTE = (
    "官方日報表；本系統自啟用日起累積，回補約 120 個交易日。as_of 之前尚未取得的日期不會出現。"
)
DERIVATIVES_NOTES = [
    "期貨未平倉為各法人身份別於收盤後的多空未平倉口數；淨額＝多方－空方，僅描述部位，不是預測。",
    "外資臺指期等值淨未平倉＝臺股期貨＋小型臺指/4＋微型臺指/20（口）；契約乘數 200/50/10 元。",
    "Put/Call 比與買賣權比為期交所公布的百分比（賣權量/買權量×100）。",
    "前五/十大交易人淨額＝買方－賣方部位（全部月份，TX 為臺指期貨等值口數，含特定法人）。",
    "VIX 為 13:45 收盤時的臺指選擇權波動率指數；最大未平倉履約價取自最近月選擇權，僅當日起累積。",
    HISTORY_NOTE,
]
STOCK_FUTURES_NOTES = [
    "未平倉量與成交量以「標準契約等值口」計：小型契約（100 股/1,000 單位）依股數折算。",
    "近月基差％＝(近月結算價－現貨收盤價)/現貨收盤價×100；現貨或結算價缺漏時為空。",
    "未平倉變化為百分比（相較前 1 / 5 個交易日）；資料斷層超過合理間隔時為空。",
    HISTORY_NOTE,
]


class MarketDerivativesService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def get(self, *, as_of: date, days: int = 60) -> MarketDerivatives:
        # One extra session so the first day's day-over-day change is available.
        rows = await self.repository.derivatives_rows(as_of_cutoff(as_of), days + 1)
        built = build_market_days(rows, days=days)
        latest = latest_market_summary(rows, days=days)
        notes = list(DERIVATIVES_NOTES)
        if not built:
            notes.insert(0, "尚無衍生品資料（尚未抓取或 as_of 太早）。")
        return MarketDerivatives(
            as_of=as_of,
            data_through=built[-1].date if built else None,
            days=built,
            latest=latest,
            notes=notes,
        )


class StockFuturesService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def get(self, raw_symbol: str, *, as_of: date, days: int = 60) -> StockFutures:
        symbol = await normalize_listed_symbol(self.repository, raw_symbol)
        # 5 sessions of look-back beyond the window for the change columns.
        history = await self.repository.stock_futures_history(symbol, as_of_cutoff(as_of), days + 5)
        notes = list(STOCK_FUTURES_NOTES)
        if not history:
            has_any = await self.repository.has_stock_futures(symbol)
            notes.insert(
                0,
                "此標的目前沒有已取得的個股期貨資料（as_of 之前尚未累積）。"
                if has_any
                else "此標的不是個股期貨標的，或尚未取得資料。",
            )
            return StockFutures(symbol=symbol, has_futures=has_any, days=[], notes=notes)
        window = history[-days:]
        spot = await self.repository.stock_spot_closes(
            symbol, window[0].trading_date - timedelta(days=1), window[-1].trading_date
        )
        one, five = oi_changes(history)
        return StockFutures(
            symbol=symbol,
            has_futures=True,
            days=stock_futures_points(window, spot),
            oi_change_1d=one,
            oi_change_5d=five,
            notes=notes,
        )
