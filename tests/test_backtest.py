from __future__ import annotations

import pytest

from quant_signal.backtest import BacktestEngine
from quant_signal.domain.models import BacktestSpec, Bar


def test_backtest_produces_equity_and_costed_trades(
    trending_bars: list[Bar],
) -> None:
    spec = BacktestSpec(
        symbol="TEST",
        start=trending_bars[80].trading_date,
        end=trending_bars[-1].trading_date,
        entry_score=55,
        exit_score=40,
        fee_bps=5,
        slippage_bps=5,
    )

    report = BacktestEngine().run(trending_bars, spec)

    assert report.observations > 100
    assert report.trades >= 1
    assert report.total_return > 0
    assert report.max_drawdown <= 0
    assert report.equity_curve[0].trading_date > spec.start


def test_backtest_rejects_insufficient_history(trending_bars: list[Bar]) -> None:
    spec = BacktestSpec(
        symbol="TEST",
        start=trending_bars[0].trading_date,
        end=trending_bars[20].trading_date,
    )

    with pytest.raises(ValueError, match="not enough data"):
        BacktestEngine().run(trending_bars[:21], spec)
