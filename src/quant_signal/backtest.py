from __future__ import annotations

from datetime import UTC, datetime
from math import sqrt
from statistics import fmean, pstdev

from quant_signal.domain.models import (
    BacktestReport,
    BacktestSpec,
    Bar,
    BrokerBranchFlow,
    EquityPoint,
    InstitutionalFlow,
    MarketObservation,
    MarketRegime,
)
from quant_signal.quant.chip_flow import ChipFlowEngine
from quant_signal.quant.engine import QuantSignalEngine
from quant_signal.quant.market_environment import MarketEnvironmentEngine
from quant_signal.quant.point_in_time import select_latest_records


class BacktestEngine:
    """Long/cash next-open backtester for deterministic signal strategies."""

    def __init__(self, signal_engine: QuantSignalEngine | None = None) -> None:
        self.signal_engine = signal_engine or QuantSignalEngine()

    def run(
        self,
        bars: list[Bar],
        spec: BacktestSpec,
        *,
        benchmark_bars: list[Bar] | None = None,
        market_observations: list[MarketObservation] | None = None,
        institutional_flows: list[InstitutionalFlow] | None = None,
        branch_flows: list[BrokerBranchFlow] | None = None,
    ) -> BacktestReport:
        started_at = datetime.now(UTC)
        all_bars = select_latest_records(bars)
        period_bars = [bar for bar in all_bars if spec.start <= bar.trading_date <= spec.end]
        if len(all_bars) < self.signal_engine.minimum_observations + 2:
            raise ValueError("not enough data to run a point-in-time backtest")
        if len(period_bars) < 2:
            raise ValueError("backtest period must contain at least two bars")

        cost_rate = (spec.fee_bps + spec.slippage_bps) / 10_000
        equity = spec.initial_capital
        position = 0.0
        trades = 0
        equity_curve: list[EquityPoint] = []
        first_period_index = all_bars.index(period_bars[0])
        for index in range(first_period_index, len(all_bars) - 1):
            execution_bar = all_bars[index]
            next_bar = all_bars[index + 1]
            if execution_bar.trading_date > spec.end or next_bar.trading_date > spec.end:
                break

            # Only bars through T-1 may determine the position executed at T open.
            history = all_bars[:index]
            if len(history) < self.signal_engine.minimum_observations:
                continue
            signal = self.signal_engine.analyze(
                history,
                as_of=history[-1].trading_date,
                strategy_version=spec.strategy_version,
                benchmark_bars=benchmark_bars,
            )
            market_environment = None
            if spec.market:
                if not market_observations:
                    raise ValueError(
                        f"market observations are required for market {spec.market}"
                    )
                market_environment = MarketEnvironmentEngine().analyze(
                    market_observations,
                    as_of=history[-1].trading_date,
                )
            target_position = position
            market_allows_risk = (
                market_environment is None
                or market_environment.regime != MarketRegime.RISK_OFF
            )
            chip_score = None
            if spec.use_chip_filter:
                if not institutional_flows:
                    raise ValueError(
                        "institutional flows are required when use_chip_filter is true"
                    )
                try:
                    chip_score = ChipFlowEngine().analyze(
                        institutional_flows,
                        as_of=history[-1].trading_date,
                        bars=history,
                        branch_flows=branch_flows,
                    ).score
                except ValueError:
                    chip_score = None
            chip_allows_entry = (
                not spec.use_chip_filter
                or (
                    chip_score is not None
                    and chip_score >= spec.chip_entry_score
                )
            )
            chip_requires_exit = (
                spec.use_chip_filter
                and (
                    chip_score is None
                    or chip_score < spec.chip_exit_score
                )
            )
            if (
                signal.score >= spec.entry_score
                and market_allows_risk
                and chip_allows_entry
            ):
                target_position = 1.0
            elif (
                signal.score < spec.exit_score
                or not market_allows_risk
                or chip_requires_exit
            ):
                target_position = 0.0

            turnover = abs(target_position - position)
            if turnover:
                equity *= 1 - turnover * cost_rate
                trades += 1
            position = target_position

            execution_open = execution_bar.open * execution_bar.adjustment_factor
            next_open = next_bar.open * next_bar.adjustment_factor
            open_return = next_open / execution_open - 1
            equity *= 1 + position * open_return
            equity_curve.append(
                EquityPoint(
                    trading_date=next_bar.trading_date,
                    equity=round(equity, 4),
                    position=position,
                )
            )

        if len(equity_curve) < 2:
            raise ValueError("backtest produced fewer than two equity observations")

        equity_values = [point.equity for point in equity_curve]
        daily_returns = [
            current / previous - 1
            for previous, current in zip(
                equity_values[:-1],
                equity_values[1:],
                strict=True,
            )
        ]
        total_return = equity_values[-1] / spec.initial_capital - 1
        years = max(len(equity_curve) / 252, 1 / 252)
        annualized_return = (equity_values[-1] / spec.initial_capital) ** (1 / years) - 1
        annualized_volatility = pstdev(daily_returns) * sqrt(252) if len(daily_returns) > 1 else 0
        sharpe = (
            fmean(daily_returns) / pstdev(daily_returns) * sqrt(252)
            if len(daily_returns) > 1 and pstdev(daily_returns) > 0
            else None
        )
        running_peak = equity_values[0]
        max_drawdown = 0.0
        for value in equity_values:
            running_peak = max(running_peak, value)
            max_drawdown = min(max_drawdown, value / running_peak - 1)

        return BacktestReport(
            spec=spec,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            observations=len(equity_curve),
            trades=trades,
            total_return=round(total_return, 6),
            annualized_return=round(annualized_return, 6),
            annualized_volatility=round(annualized_volatility, 6),
            sharpe=round(sharpe, 6) if sharpe is not None else None,
            max_drawdown=round(max_drawdown, 6),
            equity_curve=equity_curve,
        )
