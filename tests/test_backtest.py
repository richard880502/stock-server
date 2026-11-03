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


def test_buy_hold_and_sell_tax(trending_bars):
    from types import SimpleNamespace

    class Engine:
        minimum_observations = 60

        def analyze(self, history, **kwargs):
            return SimpleNamespace(score=100 if len(history) < 90 else 0)

    spec = BacktestSpec(
        symbol="TEST",
        start=trending_bars[80].trading_date,
        end=trending_bars[110].trading_date,
        fee_bps=0,
        slippage_bps=0,
    )
    engine = BacktestEngine(Engine())
    free = engine.run(trending_bars, spec)
    taxed = engine.run(trending_bars, spec.model_copy(update={"sell_tax_bps": 30}))
    assert [t.side for t in taxed.transactions] == ["buy", "sell"]
    assert taxed.transactions[0].cost == 0
    assert taxed.transactions[1].cost > 0
    assert taxed.total_return < free.total_return
    assert taxed.buy_hold_return == free.buy_hold_return
    assert len(taxed.buy_hold_curve) == len(taxed.equity_curve)
    assert taxed.excess_return == pytest.approx(
        taxed.total_return - taxed.buy_hold_return, abs=1e-6
    )
