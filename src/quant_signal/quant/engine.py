from __future__ import annotations

from datetime import date
from math import sqrt
from statistics import fmean, pstdev

from quant_signal.domain.models import (
    Alert,
    AlertSeverity,
    Bar,
    FactorScores,
    MarketRegime,
    SignalAction,
    SignalSnapshot,
)
from quant_signal.quant.point_in_time import select_latest_records


def _clamp(value: float, lower: float = 0, upper: float = 100) -> float:
    return max(lower, min(upper, value))


def _sma(values: list[float], window: int) -> float:
    sample = values[-window:]
    return fmean(sample)


def _rsi(values: list[float], window: int = 14) -> float:
    changes = [
        current - previous
        for previous, current in zip(
            values[-window - 1 : -1],
            values[-window:],
            strict=True,
        )
    ]
    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]
    average_gain = fmean(gains)
    average_loss = fmean(losses)
    if average_loss == 0:
        return 100 if average_gain > 0 else 50
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def _returns(values: list[float]) -> list[float]:
    return [
        current / previous - 1
        for previous, current in zip(values[:-1], values[1:], strict=True)
        if previous > 0
    ]


class QuantSignalEngine:
    """Deterministic daily-bar signal engine.

    The engine never fetches data and never calls an LLM. The caller is responsible
    for providing bars that were available at the requested decision time.
    """

    minimum_observations = 60

    def analyze(
        self,
        bars: list[Bar],
        *,
        as_of: date,
        strategy_version: str = "regime_v1",
        benchmark_bars: list[Bar] | None = None,
    ) -> SignalSnapshot:
        eligible = select_latest_records(
            [bar for bar in bars if bar.available_at.date() <= as_of],
            as_of=as_of,
        )
        if len(eligible) < self.minimum_observations:
            raise ValueError(
                f"at least {self.minimum_observations} point-in-time bars are required"
            )

        closes = [bar.adjusted_close or bar.close for bar in eligible]
        volumes = [bar.volume for bar in eligible]
        trend = self._trend_score(closes)
        momentum = self._momentum_score(closes)
        volume = self._volume_score(volumes, closes)
        risk_quality = self._risk_quality_score(closes)
        relative_strength = self._relative_strength_score(
            closes,
            benchmark_bars,
            as_of,
        )

        factors = FactorScores(
            trend=round(trend, 2),
            momentum=round(momentum, 2),
            volume=round(volume, 2),
            relative_strength=round(relative_strength, 2),
            risk_quality=round(risk_quality, 2),
        )
        score = round(
            trend * 0.25
            + momentum * 0.20
            + volume * 0.15
            + relative_strength * 0.20
            + risk_quality * 0.20,
            2,
        )
        action, regime = self._policy(score)
        alerts = self._detect_alerts(eligible, closes, volumes)
        confidence = self._confidence(factors, len(eligible), alerts)

        return SignalSnapshot(
            symbol=eligible[-1].symbol,
            as_of=eligible[-1].trading_date,
            strategy_version=strategy_version,
            score=score,
            action=action,
            regime=regime,
            confidence=confidence,
            factors=factors,
            alerts=alerts,
            data_available_at=max(bar.available_at for bar in eligible),
            observations=len(eligible),
        )

    @staticmethod
    def _trend_score(closes: list[float]) -> float:
        current = closes[-1]
        ma20 = _sma(closes, 20)
        ma60 = _sma(closes, 60)
        prior_ma20 = fmean(closes[-25:-5])
        score = 50.0
        score += 18 if current > ma20 else -18
        score += 18 if ma20 > ma60 else -18
        score += 14 if ma20 > prior_ma20 else -14
        return _clamp(score)

    @staticmethod
    def _momentum_score(closes: list[float]) -> float:
        return_20 = closes[-1] / closes[-21] - 1
        rsi = _rsi(closes)
        return_component = _clamp(50 + return_20 * 350)
        rsi_component = 100 - abs(rsi - 60) * 2.5
        return _clamp(return_component * 0.65 + _clamp(rsi_component) * 0.35)

    @staticmethod
    def _volume_score(volumes: list[int], closes: list[float]) -> float:
        recent_average = fmean(volumes[-20:])
        prior_average = fmean(volumes[-60:-20])
        ratio = recent_average / prior_average if prior_average else 1
        direction = 1 if closes[-1] >= closes[-20] else -1
        return _clamp(50 + direction * (ratio - 1) * 50)

    @staticmethod
    def _risk_quality_score(closes: list[float]) -> float:
        daily_returns = _returns(closes[-61:])
        volatility = pstdev(daily_returns) * sqrt(252) if len(daily_returns) > 1 else 0
        peak = max(closes[-60:])
        current_drawdown = closes[-1] / peak - 1
        score = 100 - volatility * 150 + current_drawdown * 100
        return _clamp(score)

    @staticmethod
    def _relative_strength_score(
        closes: list[float],
        benchmark_bars: list[Bar] | None,
        as_of: date,
    ) -> float:
        if not benchmark_bars:
            return 50
        benchmark = select_latest_records(
            [
                bar
                for bar in benchmark_bars
                if bar.available_at.date() <= as_of
            ],
            as_of=as_of,
        )
        if len(benchmark) < 21:
            return 50
        benchmark_closes = [bar.adjusted_close or bar.close for bar in benchmark]
        asset_return = closes[-1] / closes[-21] - 1
        benchmark_return = benchmark_closes[-1] / benchmark_closes[-21] - 1
        return _clamp(50 + (asset_return - benchmark_return) * 500)

    @staticmethod
    def _policy(score: float) -> tuple[SignalAction, MarketRegime]:
        if score >= 70:
            return SignalAction.INCREASE, MarketRegime.RISK_ON
        if score >= 50:
            return SignalAction.HOLD, MarketRegime.NEUTRAL
        if score >= 35:
            return SignalAction.REDUCE, MarketRegime.RISK_OFF
        return SignalAction.AVOID, MarketRegime.RISK_OFF

    @staticmethod
    def _detect_alerts(
        bars: list[Bar],
        closes: list[float],
        volumes: list[int],
    ) -> list[Alert]:
        alerts: list[Alert] = []
        volume_window = volumes[-60:]
        volume_mean = fmean(volume_window)
        volume_std = pstdev(volume_window)
        volume_z = (volumes[-1] - volume_mean) / volume_std if volume_std else 0
        if abs(volume_z) >= 2.5:
            alerts.append(
                Alert(
                    code="abnormal_volume",
                    severity=AlertSeverity.HIGH if abs(volume_z) >= 3.5 else AlertSeverity.MEDIUM,
                    title="成交量顯著偏離近 60 日分布",
                    evidence={"z_score": round(volume_z, 2), "volume": volumes[-1]},
                )
            )

        previous_close = closes[-2]
        adjusted_open = bars[-1].open * bars[-1].adjustment_factor
        gap = adjusted_open / previous_close - 1
        ranges = [(bar.high - bar.low) / bar.close for bar in bars[-20:]]
        average_range = fmean(ranges)
        if average_range and abs(gap) > average_range * 1.5:
            alerts.append(
                Alert(
                    code="opening_gap",
                    severity=AlertSeverity.MEDIUM,
                    title="開盤跳空幅度高於近期常態",
                    evidence={
                        "gap_pct": round(gap * 100, 2),
                        "average_range_pct": round(average_range * 100, 2),
                    },
                )
            )

        if closes[-1] >= max(closes[-20:]) and volumes[-1] < fmean(volumes[-20:]) * 0.75:
            alerts.append(
                Alert(
                    code="price_volume_divergence",
                    severity=AlertSeverity.MEDIUM,
                    title="價格創近期高點但成交量未確認",
                    evidence={"volume_ratio": round(volumes[-1] / fmean(volumes[-20:]), 2)},
                )
            )
        return alerts

    @staticmethod
    def _confidence(
        factors: FactorScores,
        observations: int,
        alerts: list[Alert],
    ) -> float:
        values = list(factors.model_dump().values())
        agreement = 1 - min(pstdev(values) / 50, 1)
        history = min(observations / 252, 1)
        alert_penalty = min(len(alerts) * 0.05, 0.2)
        return round(_clamp(agreement * 0.6 + history * 0.4 - alert_penalty, 0, 1), 3)
