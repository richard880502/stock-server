from __future__ import annotations

from collections import defaultdict
from datetime import date

from quant_signal.domain.models import (
    Alert,
    AlertSeverity,
    Bar,
    BranchRanking,
    BrokerBranchFlow,
    ChipFlowMetrics,
    ChipFlowSnapshot,
    InstitutionalFlow,
)
from quant_signal.quant.point_in_time import select_latest_records


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _streak(values: list[int]) -> int:
    if not values or values[-1] == 0:
        return 0
    sign = 1 if values[-1] > 0 else -1
    count = 0
    for value in reversed(values):
        if value == 0 or (1 if value > 0 else -1) != sign:
            break
        count += 1
    return sign * count


class ChipFlowEngine:
    """Point-in-time stock-level institutional and licensed branch analysis."""

    minimum_observations = 5

    def analyze(
        self,
        flows: list[InstitutionalFlow],
        *,
        as_of: date,
        bars: list[Bar] | None = None,
        branch_flows: list[BrokerBranchFlow] | None = None,
        strategy_version: str = "chip_flow_v1",
    ) -> ChipFlowSnapshot:
        history = select_latest_records(flows, as_of=as_of)
        if len(history) < self.minimum_observations:
            raise ValueError(
                f"not enough institutional observations: need {self.minimum_observations}"
            )
        symbol = history[-1].symbol
        if any(item.symbol != symbol for item in history):
            raise ValueError("chip-flow analysis accepts one symbol at a time")

        latest_5 = history[-5:]
        latest_20 = history[-20:]
        volume_by_date = {
            item.trading_date: item.volume
            for item in select_latest_records(bars or [], as_of=as_of)
        }
        volume_5d = sum(volume_by_date.get(item.trading_date, 0) for item in latest_5)
        total_net_5d = sum(item.total_net for item in latest_5)
        flow_volume_ratio = total_net_5d / volume_5d if volume_5d else 0.0

        holding_points = [
            item.foreign_holding_ratio
            for item in latest_5
            if item.foreign_holding_ratio is not None
        ]
        holding_change = (
            holding_points[-1] - holding_points[0]
            if len(holding_points) >= 2
            else None
        )

        branches = [
            item
            for item in (branch_flows or [])
            if item.symbol == symbol and item.trading_date <= as_of
        ]
        recent_dates = {item.trading_date for item in history[-5:]}
        recent_branches = [item for item in branches if item.trading_date in recent_dates]
        branch_metrics, top_buyers, top_sellers = self._branch_metrics(
            recent_branches,
            branches,
            bars or [],
        )

        foreign_ratio = sum(item.foreign_net for item in latest_5) / max(volume_5d, 1)
        trust_ratio = sum(item.investment_trust_net for item in latest_5) / max(
            volume_5d, 1
        )
        dealer_ratio = sum(item.dealer_net for item in latest_5) / max(volume_5d, 1)
        foreign_score = _clamp(50 + foreign_ratio * 500)
        trust_score = _clamp(50 + trust_ratio * 750)
        dealer_score = _clamp(50 + dealer_ratio * 400)
        persistence = _clamp(
            50
            + _streak([item.foreign_net for item in history]) * 5
            + _streak([item.investment_trust_net for item in history]) * 7
        )
        institutional_score = (
            foreign_score * 0.42
            + trust_score * 0.30
            + dealer_score * 0.10
            + persistence * 0.18
        )
        branch_score = self._branch_score(branch_metrics)
        score = (
            institutional_score * 0.72 + branch_score * 0.28
            if branch_score is not None
            else institutional_score
        )

        metrics = ChipFlowMetrics(
            foreign_net_5d=sum(item.foreign_net for item in latest_5),
            investment_trust_net_5d=sum(
                item.investment_trust_net for item in latest_5
            ),
            dealer_net_5d=sum(item.dealer_net for item in latest_5),
            total_net_20d=sum(item.total_net for item in latest_20),
            flow_volume_ratio_5d=round(flow_volume_ratio, 6),
            foreign_streak=_streak([item.foreign_net for item in history]),
            investment_trust_streak=_streak(
                [item.investment_trust_net for item in history]
            ),
            foreign_holding_ratio=holding_points[-1] if holding_points else None,
            foreign_holding_change_5d=(
                round(holding_change, 4) if holding_change is not None else None
            ),
            **branch_metrics,
        )
        alerts = self._alerts(metrics, history, bars or [])
        return ChipFlowSnapshot(
            symbol=symbol,
            as_of=history[-1].trading_date,
            strategy_version=strategy_version,
            score=round(score, 2),
            confidence=round(
                min(len(history) / 60, 1) * (1.0 if recent_branches else 0.82),
                3,
            ),
            institutional_observations=len(history),
            branch_observations=len(branches),
            branch_data_available=bool(branches),
            branch_data_status=(
                "licensed_data_loaded"
                if branches
                else "not_licensed_or_not_imported"
            ),
            metrics=metrics,
            latest=history[-1],
            top_buyers=top_buyers,
            top_sellers=top_sellers,
            alerts=alerts,
            data_available_at=max(item.available_at for item in history),
        )

    @staticmethod
    def _branch_metrics(
        recent: list[BrokerBranchFlow],
        all_branches: list[BrokerBranchFlow],
        bars: list[Bar],
    ) -> tuple[dict[str, float | None], list[BranchRanking], list[BranchRanking]]:
        if not recent:
            empty = {
                "branch_buy_concentration_5d": None,
                "branch_sell_concentration_5d": None,
                "branch_estimated_cost": None,
                "branch_day_trade_ratio": None,
            }
            return empty, [], []
        grouped: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {"buy": 0, "sell": 0, "buy_amount": 0, "sell_amount": 0}
        )
        day_trade = 0
        for item in recent:
            key = (item.branch_code, item.branch_name)
            grouped[key]["buy"] += item.buy_shares
            grouped[key]["sell"] += item.sell_shares
            grouped[key]["buy_amount"] += item.buy_amount
            grouped[key]["sell_amount"] += item.sell_amount
            if (
                item.day_trade_buy_shares is not None
                and item.day_trade_sell_shares is not None
            ):
                day_trade += min(
                    item.day_trade_buy_shares,
                    item.day_trade_sell_shares,
                )
        ranked = sorted(
            (
                (code, name, int(values["buy"] - values["sell"]), values)
                for (code, name), values in grouped.items()
            ),
            key=lambda item: item[2],
            reverse=True,
        )
        gross_buy = sum(values["buy"] for values in grouped.values())
        gross_sell = sum(values["sell"] for values in grouped.values())
        top_buy = sum(max(item[2], 0) for item in ranked[:5])
        top_sell = sum(abs(min(item[2], 0)) for item in ranked[-5:])
        net_buy_amount = sum(
            values["buy_amount"] - values["sell_amount"]
            for _, _, net, values in ranked
            if net > 0
        )
        net_buy_shares = sum(max(net, 0) for _, _, net, _ in ranked)
        win_rates = ChipFlowEngine._branch_win_rates(all_branches, bars)

        def ranking(item: tuple[str, str, int, dict[str, float]]) -> BranchRanking:
            code, name, net, values = item
            amount = (
                values["buy_amount"] if net >= 0 else values["sell_amount"]
            )
            shares = values["buy"] if net >= 0 else values["sell"]
            return BranchRanking(
                branch_code=code,
                branch_name=name,
                net_shares=net,
                estimated_cost=round(amount / shares, 2) if shares else None,
                win_rate=win_rates.get(code),
            )

        top_buyers = [ranking(item) for item in ranked[:5] if item[2] > 0]
        top_sellers = [ranking(item) for item in reversed(ranked[-5:]) if item[2] < 0]
        return (
            {
                "branch_buy_concentration_5d": round(top_buy / gross_buy, 4)
                if gross_buy
                else 0,
                "branch_sell_concentration_5d": round(top_sell / gross_sell, 4)
                if gross_sell
                else 0,
                "branch_estimated_cost": round(net_buy_amount / net_buy_shares, 2)
                if net_buy_shares
                else None,
                "branch_day_trade_ratio": round(
                    day_trade / max(gross_buy + gross_sell, 1),
                    4,
                ),
            },
            top_buyers,
            top_sellers,
        )

    @staticmethod
    def _branch_win_rates(
        branches: list[BrokerBranchFlow],
        bars: list[Bar],
    ) -> dict[str, float]:
        prices = select_latest_records(bars)
        index_by_date = {item.trading_date: index for index, item in enumerate(prices)}
        outcomes: dict[str, list[bool]] = defaultdict(list)
        for item in branches:
            index = index_by_date.get(item.trading_date)
            if index is None or index + 5 >= len(prices) or item.net_shares == 0:
                continue
            future_return = prices[index + 5].close / prices[index].close - 1
            outcomes[item.branch_code].append(
                future_return > 0 if item.net_shares > 0 else future_return < 0
            )
        return {
            code: round(sum(values) / len(values), 4)
            for code, values in outcomes.items()
            if len(values) >= 5
        }

    @staticmethod
    def _branch_score(metrics: dict[str, float | None]) -> float | None:
        buy = metrics["branch_buy_concentration_5d"]
        sell = metrics["branch_sell_concentration_5d"]
        if buy is None or sell is None:
            return None
        return _clamp(50 + (buy - sell) * 100)

    @staticmethod
    def _alerts(
        metrics: ChipFlowMetrics,
        history: list[InstitutionalFlow],
        bars: list[Bar],
    ) -> list[Alert]:
        alerts: list[Alert] = []
        if abs(metrics.foreign_streak) >= 3:
            alerts.append(
                Alert(
                    code="foreign_flow_streak",
                    severity=AlertSeverity.MEDIUM,
                    title=(
                        f"外資連續買超 {metrics.foreign_streak} 日"
                        if metrics.foreign_streak > 0
                        else f"外資連續賣超 {abs(metrics.foreign_streak)} 日"
                    ),
                    evidence={"streak": metrics.foreign_streak},
                )
            )
        if abs(metrics.investment_trust_streak) >= 3:
            alerts.append(
                Alert(
                    code="trust_flow_streak",
                    severity=AlertSeverity.MEDIUM,
                    title=(
                        f"投信連續買超 {metrics.investment_trust_streak} 日"
                        if metrics.investment_trust_streak > 0
                        else f"投信連續賣超 {abs(metrics.investment_trust_streak)} 日"
                    ),
                    evidence={"streak": metrics.investment_trust_streak},
                )
            )
        prices = select_latest_records(bars, as_of=history[-1].trading_date)
        if len(prices) >= 6:
            price_return = prices[-1].close / prices[-6].close - 1
            if price_return > 0.03 and metrics.foreign_net_5d < 0:
                alerts.append(
                    Alert(
                        code="price_flow_divergence",
                        severity=AlertSeverity.HIGH,
                        title="股價上漲但外資五日賣超，出現價籌背離",
                        evidence={
                            "price_return_5d": round(price_return, 4),
                            "foreign_net_5d": metrics.foreign_net_5d,
                        },
                    )
                )
        if (metrics.branch_buy_concentration_5d or 0) >= 0.35:
            alerts.append(
                Alert(
                    code="branch_buy_concentration",
                    severity=AlertSeverity.MEDIUM,
                    title="買方分點集中度偏高",
                    evidence={
                        "ratio": metrics.branch_buy_concentration_5d or 0,
                    },
                )
            )
        if (metrics.branch_day_trade_ratio or 0) >= 0.25:
            alerts.append(
                Alert(
                    code="branch_day_trade_activity",
                    severity=AlertSeverity.MEDIUM,
                    title="分點短線／當沖活動偏高",
                    evidence={"ratio": metrics.branch_day_trade_ratio or 0},
                )
            )
        return alerts
