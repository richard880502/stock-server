"""Compact, dated official-data blocks that go into the AI analyst's evidence bundle.

Each block describes one stock (or the derivatives market) as of the same cutoff the rest
of the context uses (end of ``as_of`` in Taipei time, applied to ``available_at``), carries
its own data date so the model can see staleness, and is ``None`` when the underlying feed
has nothing for the symbol. A missing block means "unknown", never zero or neutral.
Values are computed facts only.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from quant_signal.application.official_queries import as_of_cutoff
from quant_signal.application.ports import QuantRepository
from quant_signal.quant.derivatives import build_market_days
from quant_signal.quant.screener import EX_KINDS, holder_metrics

BLOCK_KEYS = (
    "valuation",
    "revenue_trend",
    "earnings",
    "holders",
    "short_interest",
    "stock_futures",
    "upcoming_events",
    "market_derivatives",
    "screener",
)
EVENT_WINDOW_DAYS = 60


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Drop absent values so the payload stays small (a missing key reads as unknown)."""
    return {
        key: round(value, 4) if isinstance(value, float) else value
        for key, value in data.items()
        if value is not None
    }


def _has_values(block: dict[str, Any], *skip: str) -> bool:
    return any(value is not None for key, value in block.items() if key not in skip)


async def build_official_blocks(
    repository: QuantRepository, symbol: str, as_of: date
) -> dict[str, dict[str, Any] | None]:
    """All evidence blocks for ``symbol``; every key of :data:`BLOCK_KEYS` is present."""
    symbol = symbol.strip().upper()
    cutoff = as_of_cutoff(as_of)
    blocks: dict[str, dict[str, Any] | None] = dict.fromkeys(BLOCK_KEYS)

    valuation = (await repository.latest_valuation_metrics(cutoff, [symbol])).get(symbol)
    if valuation is not None:
        blocks["valuation"] = _clean(
            {
                "date": valuation.trading_date.isoformat(),
                "pe_ratio": valuation.pe_ratio,
                "pb_ratio": valuation.pb_ratio,
                "dividend_yield_pct": valuation.dividend_yield_pct,
                "pe_percentile_250d": valuation.pe_percentile_250d,
                "pb_percentile_250d": valuation.pb_percentile_250d,
                "yield_percentile_250d": valuation.yield_percentile_250d,
                "fiscal_period": valuation.fiscal_period,
            }
        )

    trend = (await repository.revenue_trends(cutoff, [symbol], include_months=True)).get(symbol)
    if trend is not None:
        latest = trend.months[-1] if trend.months else None
        blocks["revenue_trend"] = _clean(
            {
                "period": trend.latest_period,
                "revenue_yoy_pct": latest.yoy_pct if latest else None,
                "revenue_mom_pct": latest.mom_pct if latest else None,
                "yoy_3m_pct": trend.yoy_3m_pct,
                "yoy_12m_pct": trend.yoy_12m_pct,
                "is_12m_high": trend.is_12m_high,
                "growth_streak_months": trend.growth_streak_months,
            }
        )

    earnings = (await repository.earnings_summaries(cutoff, [symbol])).get(symbol)
    if earnings is not None:
        blocks["earnings"] = _clean(
            {
                "period": earnings.period,
                "published": earnings.available_at.date().isoformat(),
                "eps_quarter": earnings.eps,
                "eps_ttm": earnings.eps_ttm,
                "eps_yoy_pct": earnings.eps_yoy_pct,
                "gross_margin_pct": earnings.gross_margin_pct,
                "operating_margin_pct": earnings.operating_margin_pct,
                "gross_margin_yoy_pp": earnings.gross_margin_yoy_pp,
                "operating_margin_yoy_pp": earnings.operating_margin_yoy_pp,
                "roe_ttm_pct": earnings.roe_ttm_pct,
            }
        )

    pair = (await repository.latest_shareholding_pairs(cutoff)).get(symbol)
    foreign = await repository.latest_foreign_holding(symbol, cutoff)
    pledge = (await repository.latest_insider_pledges(cutoff)).get(symbol)
    held = holder_metrics(pair, foreign)
    holders = _clean(
        {
            "tdcc_date": held["holders_data_date"],
            "big_holder_pct_ge400_lots": held["big_holder_pct"],
            "big_holder_pct_change": held["big_holder_pct_change"],
            "compare_days": held["holders_weeks_span"],
            "whale_pct_ge1000_lots": held["whale_pct"],
            "foreign_holding_pct": held["foreign_holding_pct"],
            "foreign_holding_date": foreign.trading_date.isoformat() if foreign else None,
            "pledge_ratio_pct": pledge.pledge_ratio_pct if pledge else None,
            "pledge_period": pledge.period if pledge and pledge.pledge_ratio_pct else None,
        }
    )
    blocks["holders"] = holders or None

    sbl = (await repository.latest_short_lending_metrics(cutoff)).get(symbol)
    day_trade = (await repository.latest_day_trade_metrics(cutoff)).get(symbol)
    if sbl is not None or day_trade is not None:
        blocks["short_interest"] = _clean(
            {
                "sbl_date": sbl.trading_date.isoformat() if sbl else None,
                "sbl_pct_of_shares": sbl.sbl_pct_of_shares if sbl else None,
                "sbl_change_5d_pct": sbl.sbl_change_5d_pct if sbl else None,
                "sbl_change_20d_pct": sbl.sbl_change_20d_pct if sbl else None,
                "days_to_cover": sbl.days_to_cover if sbl else None,
                "day_trade_date": day_trade.trading_date.isoformat() if day_trade else None,
                "day_trade_ratio_pct": day_trade.day_trade_ratio_pct if day_trade else None,
                "day_trade_ratio_20d_pct": (
                    day_trade.day_trade_ratio_20d_pct if day_trade else None
                ),
            }
        )

    futures = (await repository.latest_stock_futures_metrics(cutoff)).get(symbol)
    if futures is not None:
        blocks["stock_futures"] = _clean(
            {
                "date": futures.data_date.isoformat(),
                "open_interest": futures.open_interest,
                "oi_change_1d_pct": futures.oi_change_1d,
                "oi_change_5d_pct": futures.oi_change_5d,
                "basis_pct": futures.basis_pct,
                "near_month": futures.near_month,
            }
        )

    event = (await repository.next_dividend_events(cutoff, EVENT_WINDOW_DAYS)).get(symbol)
    if event is not None and event.kind in EX_KINDS:
        blocks["upcoming_events"] = _clean(
            {
                "as_of": as_of.isoformat(),
                "next_ex_date": event.date.isoformat(),
                "kind": event.kind,
                "days_ahead": (event.date - as_of).days,
                "cash_dividend_per_share": event.cash_dividend,
                "stock_dividend_per_share": event.stock_dividend,
            }
        )

    derivatives = await repository.latest_market_derivatives(cutoff)
    if derivatives is not None:
        days = build_market_days(await repository.derivatives_rows(cutoff, 12), days=12)
        series = [d for d in days if d.foreign_tx_equiv_net_oi is not None]
        change_5 = None
        if (
            derivatives.foreign_tx_equiv_net_oi is not None
            and len(series) >= 6
            and series[-1].date == derivatives.date
        ):
            change_5 = round(
                derivatives.foreign_tx_equiv_net_oi - series[-6].foreign_tx_equiv_net_oi, 2
            )
        block = _clean(
            {
                "date": derivatives.date.isoformat(),
                "foreign_tx_equiv_net_oi": derivatives.foreign_tx_equiv_net_oi,
                "foreign_tx_equiv_net_oi_change_5d": change_5,
                "pc_oi_ratio_pct": derivatives.pc_oi_ratio,
                "vix": derivatives.vix,
            }
        )
        blocks["market_derivatives"] = block if _has_values(block, "date") else None

    blocks["screener"] = await _screener_block(repository, symbol, as_of)
    return blocks


async def _screener_block(
    repository: QuantRepository, symbol: str, as_of: date
) -> dict[str, Any] | None:
    from quant_signal.application.screener import ScreenerService

    result = await ScreenerService(repository).stock(symbol, as_of=as_of)
    entry = result.entry
    if entry is None or result.data_through is None:
        return None
    return _clean(
        {
            "date": result.data_through.isoformat(),
            "rank": entry.rank,
            "universe": result.universe,
            "composite": entry.composite,
            "scores": {key: value for key, value in entry.scores.items() if value is not None},
            "highlights": entry.highlights or None,
            "flags": entry.flags or None,
        }
    )
