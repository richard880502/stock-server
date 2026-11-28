"""Pure derived measures over the TAIFEX derivatives rows (no I/O).

Positioning data, not a forecast: net open interest says how the named investor class is
positioned, not where the index goes next.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from quant_signal.domain.derivatives import (
    DerivativesRows,
    FuturesInstitutionalDay,
    MarketDerivativesDay,
    MarketDerivativesLatest,
    OptionsInstitutionalDay,
    StockFuturesDay,
    StockFuturesMetrics,
    StockFuturesPoint,
)

# TX = NT$200 per index point, MTX = NT$50, TMF = NT$10, so MTX counts 1/4 and TMF 1/20 of
# a TX contract (the exchange states the large-trader TX the same way: TX+MTX/4+TMF/20).
TX_EQUIVALENT_FACTOR = {"TX": 1.0, "MTX": 0.25, "TMF": 0.05}


def net_oi(
    rows: Iterable[FuturesInstitutionalDay], *, investor: str, contract: str
) -> dict[date, int]:
    """Net open interest (long - short, contracts) per date for one investor and contract."""
    return {
        row.trading_date: row.net_oi_contracts
        for row in rows
        if row.investor == investor and row.contract == contract
    }


def tx_equivalent_net_oi(
    rows: Iterable[FuturesInstitutionalDay], investor: str
) -> dict[date, float]:
    """Net OI in TX-equivalent contracts: TX + MTX/4 + TMF/20, per date.

    TX is required (no TX row, no value). MTX and TMF rows missing for a date count as
    zero, which is exact for a product that did not exist or that the investor holds
    nothing in; the scheduler only stores days where the whole table was published.
    """
    rows = list(rows)
    tx = net_oi(rows, investor=investor, contract="TX")
    mtx = net_oi(rows, investor=investor, contract="MTX")
    tmf = net_oi(rows, investor=investor, contract="TMF")
    return {
        day: round(
            value
            + mtx.get(day, 0) * TX_EQUIVALENT_FACTOR["MTX"]
            + tmf.get(day, 0) * TX_EQUIVALENT_FACTOR["TMF"],
            2,
        )
        for day, value in tx.items()
    }


def day_over_day(series: dict[date, float]) -> dict[date, float | None]:
    """Change versus the previous date present in ``series`` (None for the first)."""
    out: dict[date, float | None] = {}
    previous: float | None = None
    for day in sorted(series):
        out[day] = None if previous is None else round(series[day] - previous, 2)
        previous = series[day]
    return out


def _options_net(
    rows: Iterable[OptionsInstitutionalDay], *, investor: str, call_put: str
) -> dict[date, int]:
    return {
        row.trading_date: row.net_oi_contracts
        for row in rows
        if row.investor == investor and row.call_put == call_put
    }


def build_market_days(rows: DerivativesRows, *, days: int) -> list[MarketDerivativesDay]:
    """One entry per date that any dataset covers, oldest first, at most the newest ``days``."""
    foreign_tx = net_oi(rows.futures, investor="foreign", contract="TX")
    equiv = tx_equivalent_net_oi(rows.futures, "foreign")
    change = day_over_day(equiv)
    trust = net_oi(rows.futures, investor="trust", contract="TX")
    dealer = net_oi(rows.futures, investor="dealer", contract="TX")
    foreign_call = _options_net(rows.options, investor="foreign", call_put="CALL")
    foreign_put = _options_net(rows.options, investor="foreign", call_put="PUT")
    pc = {row.trading_date: row for row in rows.pc_ratio}
    large = {row.trading_date: row for row in rows.large_traders if row.trader_type == "all"}
    vix = {row.trading_date: row.vix_close for row in rows.vix}
    dates = sorted(
        set(foreign_tx) | set(pc) | set(foreign_call) | set(large) | set(vix) | set(trust)
    )[-days:]
    out: list[MarketDerivativesDay] = []
    for day in dates:
        big = large.get(day)
        ratio = pc.get(day)
        out.append(
            MarketDerivativesDay(
                date=day,
                foreign_tx_net_oi=foreign_tx.get(day),
                foreign_tx_equiv_net_oi=equiv.get(day),
                foreign_net_oi_change=change.get(day),
                trust_tx_net_oi=trust.get(day),
                dealer_tx_net_oi=dealer.get(day),
                pc_volume_ratio=ratio.volume_ratio_pct if ratio else None,
                pc_oi_ratio=ratio.oi_ratio_pct if ratio else None,
                foreign_call_net_oi=foreign_call.get(day),
                foreign_put_net_oi=foreign_put.get(day),
                top5_net_oi=(big.top5_long_contracts - big.top5_short_contracts if big else None),
                top10_net_oi=(
                    big.top10_long_contracts - big.top10_short_contracts if big else None
                ),
                vix=vix.get(day),
            )
        )
    return out


def latest_market_summary(
    rows: DerivativesRows, *, days: int = 60
) -> MarketDerivativesLatest | None:
    """The newest day plus the nearest monthly and weekly TXO open-interest strikes."""
    built = build_market_days(rows, days=days)
    if not built:
        return None
    latest = built[-1]
    levels = [level for level in rows.oi_levels if level.trading_date == latest.date]
    monthly = next((level for level in levels if level.kind == "monthly"), None)
    weekly = next((level for level in levels if level.kind == "weekly"), None)
    return MarketDerivativesLatest(
        **latest.model_dump(),
        max_call_oi_strike=monthly.max_call_strike if monthly else None,
        max_put_oi_strike=monthly.max_put_strike if monthly else None,
        max_call_oi_expiry=monthly.expiry if monthly else None,
        max_put_oi_expiry=monthly.expiry if monthly else None,
        max_call_oi_contracts=monthly.max_call_oi_contracts if monthly else None,
        max_put_oi_contracts=monthly.max_put_oi_contracts if monthly else None,
        weekly_max_call_oi_strike=weekly.max_call_strike if weekly else None,
        weekly_max_put_oi_strike=weekly.max_put_strike if weekly else None,
        weekly_max_call_oi_contracts=weekly.max_call_oi_contracts if weekly else None,
        weekly_max_put_oi_contracts=weekly.max_put_oi_contracts if weekly else None,
        weekly_expiry=weekly.expiry if weekly else None,
    )


# --- single-stock futures -----------------------------------------------------------------

ONE_DAY_MAX_GAP = timedelta(days=6)  # long weekend plus a holiday
FIVE_DAY_MAX_GAP = timedelta(days=12)


def pct_change(current: float | None, previous: float | None) -> float | None:
    """Percent change; None when either side is missing or zero (an unreported open
    interest must never read as a -100% / +infinite move)."""
    if current is None or previous is None or previous <= 0 or current <= 0:
        return None
    return round((current - previous) / previous * 100.0, 2)


def basis_pct(near_settlement: float | None, spot_close: float | None) -> float | None:
    """(near-month settlement - spot close) / spot close, in percent; None if either is missing."""
    if near_settlement is None or spot_close is None or spot_close <= 0:
        return None
    return round((near_settlement - spot_close) / spot_close * 100.0, 2)


def oi_changes(history: list[StockFuturesDay]) -> tuple[float | None, float | None]:
    """Open-interest change (percent) of the newest row versus 1 and 5 sessions earlier.

    ``history`` is oldest first. A comparison row further back in time than a plausible
    1/5-session gap (a data hole) yields None rather than a misleading number.
    """
    if not history:
        return None, None
    last = history[-1]

    def versus(back: int, limit: timedelta) -> float | None:
        if len(history) <= back:
            return None
        base = history[-1 - back]
        if last.trading_date - base.trading_date > limit:
            return None
        return pct_change(last.total_open_interest, base.total_open_interest)

    return versus(1, ONE_DAY_MAX_GAP), versus(5, FIVE_DAY_MAX_GAP)


def stock_futures_points(
    history: list[StockFuturesDay], spot: dict[date, float]
) -> list[StockFuturesPoint]:
    return [
        StockFuturesPoint(
            date=row.trading_date,
            open_interest=row.total_open_interest,
            volume=row.total_volume,
            near_month=row.near_month,
            near_settlement=row.near_settlement,
            basis_pct=basis_pct(row.near_settlement, spot.get(row.trading_date)),
        )
        for row in history
    ]


def stock_futures_metrics(
    history: list[StockFuturesDay], spot_close: float | None
) -> StockFuturesMetrics | None:
    if not history:
        return None
    last = history[-1]
    one, five = oi_changes(history)
    return StockFuturesMetrics(
        symbol=last.symbol,
        data_date=last.trading_date,
        open_interest=last.total_open_interest,
        volume=last.total_volume,
        oi_change_1d=one,
        oi_change_5d=five,
        near_month=last.near_month,
        basis_pct=basis_pct(last.near_settlement, spot_close),
    )
