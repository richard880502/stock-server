"""Read side of the short-lending / day-trade / pledge / dividend data (one stock)."""

from __future__ import annotations

from datetime import date, timedelta

from quant_signal.application.official_queries import as_of_cutoff
from quant_signal.application.ports import QuantRepository
from quant_signal.application.stocks import normalize_listed_symbol
from quant_signal.domain.short_data import (
    DividendEvent,
    InsiderPledge,
    ShortDay,
    StockShortInterest,
    UpcomingEvent,
)
from quant_signal.infrastructure.short_data_reads import day_trade_metrics, short_lending_metrics

EVENT_DAYS = 60
SHARES_PER_LOT = 1000
KIND_LABEL = {"ex_dividend": "除息", "ex_rights": "除權", "ex_rights_dividend": "除權息"}

NOTES = [
    "借券賣出餘額、當日賣出與還券為交易所公告（TWSE TWT93U / TPEx 融券借券賣出餘額），"
    "單位為股，本回應換算為張（1張=1,000股）；融券餘額取自 market_stock_daily，僅供對照。",
    "當沖比 = 現股當沖成交股數 / 當日總成交股數；5日、20日為成交量加權；"
    "當沖成交股數於 T+1、T+2 日可能被交易所更新，系統保留修訂版本；"
    "上櫃個股官方未公布當沖個股量，故為空值。",
    "回補天數 = 借券賣出餘額 / 近20個交易日平均成交量；"
    "借券餘額占發行股數以外資持股表之發行股數計算。",
    "董監質設比 = 董事、監察人、經理人及大股東設質股數 / 其持股合計"
    "（同一人多職稱只計一次），月資料。",
    "除權息資料為交易所預告表，金額以發行公司公告為準；股票股利為每股配發新股數。"
    "法人說明會無公開全市場結構化資料，未收錄。",
]


def event_label(event: DividendEvent) -> str:
    parts = [KIND_LABEL[event.kind]]
    if event.cash_dividend_per_share:
        parts.append(f"現金股利 {event.cash_dividend_per_share:g} 元/股")
    if event.stock_dividend_per_share:
        parts.append(f"股票股利 {event.stock_dividend_per_share:g} 股/股")
    if event.cash_capital_increase_ratio:
        parts.append(f"現金增資配股率 {event.cash_capital_increase_ratio:g}")
    return " ".join(parts)


def _lots(shares: int | None) -> float | None:
    return None if shares is None else shares / SHARES_PER_LOT


class StockShortInterestService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def get(self, raw_symbol: str, *, as_of: date, days: int = 60) -> StockShortInterest:
        repo = self.repository
        symbol = await normalize_listed_symbol(repo, raw_symbol)
        cutoff = as_of_cutoff(as_of)
        sessions_desc = await repo.list_market_trading_dates(None, as_of=as_of, limit=days + 25)
        sessions = sorted(sessions_desc)
        since = sessions[0] if sessions else as_of - timedelta(days=days * 2)
        stock_rows = {
            row.trading_date: row for row in await repo.list_market_stock_rows([symbol], sessions)
        }
        volumes = {d: row.volume for d, row in stock_rows.items()}
        sbl = {r.trading_date: r for r in await repo.sd_sbl_rows(since, cutoff, symbol)}
        dtr = {r.trading_date: r for r in await repo.sd_day_trade_rows(since, cutoff, symbol)}

        shown = sessions[-days:]
        points = [
            ShortDay(
                date=day,
                sbl_balance_lots=_lots(sbl[day].sbl_balance) if day in sbl else None,
                sbl_sold_lots=_lots(sbl[day].sbl_sold) if day in sbl else None,
                sbl_returned_lots=_lots(sbl[day].sbl_returned) if day in sbl else None,
                margin_short_balance_lots=stock_rows[day].short_balance
                if day in stock_rows
                else None,
                day_trade_ratio_pct=(
                    day_trade_metrics(
                        symbol, day, {day: dtr[day]}, volumes, sessions
                    ).day_trade_ratio_pct
                    if day in dtr
                    else None
                ),
            )
            for day in shown
        ]

        lend = day_trade = None
        if sbl:
            outstanding = await repo.latest_foreign_holding(symbol, cutoff)
            lend = short_lending_metrics(
                symbol,
                max(sbl),
                sbl,
                volumes,
                sessions,
                outstanding.shares_outstanding if outstanding else None,
            )
        if dtr:
            day_trade = day_trade_metrics(symbol, max(dtr), dtr, volumes, sessions)

        insiders = None
        found = await repo.sd_insider_rows(cutoff, symbol)
        if found:
            row = found[0]
            insiders = InsiderPledge(
                symbol=symbol,
                period=row.period,
                pledge_ratio_pct=row.pledge_ratio_pct,
                holding_shares=row.holding_shares,
                pledged_shares=row.pledged_shares,
            )
        events = await repo.list_dividend_events(
            [symbol], as_of, as_of + timedelta(days=EVENT_DAYS), cutoff
        )
        return StockShortInterest(
            symbol=symbol,
            as_of=as_of,
            days=points,
            sbl_change_5d_pct=lend.sbl_change_5d_pct if lend else None,
            sbl_change_20d_pct=lend.sbl_change_20d_pct if lend else None,
            sbl_pct_of_shares=lend.sbl_pct_of_shares if lend else None,
            days_to_cover=lend.days_to_cover if lend else None,
            day_trade_ratio_5d_pct=day_trade.day_trade_ratio_5d_pct if day_trade else None,
            day_trade_ratio_20d_pct=day_trade.day_trade_ratio_20d_pct if day_trade else None,
            insiders=insiders,
            upcoming_events=[
                UpcomingEvent(
                    date=event.ex_date,
                    kind=event.kind,
                    label=event_label(event),
                    cash_dividend=event.cash_dividend_per_share,
                    stock_dividend=event.stock_dividend_per_share,
                )
                for event in events
            ],
            notes=NOTES,
        )
