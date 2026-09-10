from __future__ import annotations

from datetime import date
from statistics import fmean, pstdev

from quant_signal.domain.models import (
    Alert,
    AlertSeverity,
    MarketEnvironmentFactors,
    MarketEnvironmentSnapshot,
    MarketObservation,
    MarketRegime,
)
from quant_signal.quant.point_in_time import select_latest_records


def _clamp(value: float, lower: float = 0, upper: float = 100) -> float:
    return max(lower, min(upper, value))


def _z_score(value: float, history: list[float]) -> float:
    if len(history) < 2:
        return 0
    standard_deviation = pstdev(history)
    return (value - fmean(history)) / standard_deviation if standard_deviation else 0


class MarketEnvironmentEngine:
    """Deterministic point-in-time market breadth and capital-flow model."""

    minimum_observations = 60

    def analyze(
        self,
        observations: list[MarketObservation],
        *,
        as_of: date,
        strategy_version: str = "market_env_v1",
    ) -> MarketEnvironmentSnapshot:
        eligible = select_latest_records(
            [
                observation
                for observation in observations
                if observation.available_at.date() <= as_of
            ],
            as_of=as_of,
        )
        if len(eligible) < self.minimum_observations:
            raise ValueError(
                f"at least {self.minimum_observations} point-in-time market "
                "observations are required"
            )

        participation = self._participation(eligible[-1])
        advance_decline = self._advance_decline(eligible)
        breadth_momentum = self._breadth_momentum(eligible)
        volume_breadth = self._volume_breadth(eligible)
        institutional_flow = self._institutional_flow(eligible)
        leverage_quality = self._leverage_quality(eligible)
        factors = MarketEnvironmentFactors(
            participation=round(participation, 2),
            advance_decline=round(advance_decline, 2),
            breadth_momentum=round(breadth_momentum, 2),
            volume_breadth=round(volume_breadth, 2),
            institutional_flow=round(institutional_flow, 2),
            leverage_quality=round(leverage_quality, 2),
        )
        score = round(
            participation * 0.25
            + advance_decline * 0.20
            + breadth_momentum * 0.15
            + volume_breadth * 0.15
            + institutional_flow * 0.15
            + leverage_quality * 0.10,
            2,
        )
        regime = self._regime(score)
        alerts = self._detect_alerts(eligible)
        confidence = self._confidence(factors, len(eligible), alerts)
        latest = eligible[-1]
        return MarketEnvironmentSnapshot(
            market=latest.market,
            as_of=latest.trading_date,
            strategy_version=strategy_version,
            score=score,
            regime=regime,
            confidence=confidence,
            factors=factors,
            alerts=alerts,
            latest=latest,
            data_available_at=max(item.available_at for item in eligible),
            observations=len(eligible),
        )

    @staticmethod
    def _participation(latest: MarketObservation) -> float:
        above_ma20 = latest.above_ma20_issues / latest.total_issues * 100
        above_ma60 = latest.above_ma60_issues / latest.total_issues * 100
        return _clamp(above_ma20 * 0.55 + above_ma60 * 0.45)

    @staticmethod
    def _advance_decline(observations: list[MarketObservation]) -> float:
        ratios: list[float] = []
        for item in observations[-5:]:
            directional = item.advancing_issues + item.declining_issues
            ratios.append(
                item.advancing_issues / directional * 100 if directional else 50
            )
        return _clamp(ratios[-1] * 0.6 + fmean(ratios) * 0.4)

    @staticmethod
    def _breadth_momentum(observations: list[MarketObservation]) -> float:
        latest = observations[-1]
        prior = observations[-6]
        net_high_low = (
            latest.new_high_52w_issues - latest.new_low_52w_issues
        ) / latest.total_issues
        participation_change = (
            latest.above_ma20_issues / latest.total_issues
            - prior.above_ma20_issues / prior.total_issues
        )
        return _clamp(50 + net_high_low * 400 + participation_change * 120)

    @staticmethod
    def _volume_breadth(observations: list[MarketObservation]) -> float:
        ratios: list[float] = []
        for item in observations[-5:]:
            total_volume = item.up_volume + item.down_volume
            ratios.append(item.up_volume / total_volume * 100 if total_volume else 50)
        return _clamp(ratios[-1] * 0.65 + fmean(ratios) * 0.35)

    @staticmethod
    def _institutional_flow(observations: list[MarketObservation]) -> float:
        cash_ratios = [
            (
                item.foreign_net_flow
                + item.investment_trust_net_flow
                + item.dealer_net_flow
            )
            / item.turnover_value
            for item in observations[-60:]
        ]
        futures_positions = [
            item.futures_net_open_interest for item in observations[-60:]
        ]
        cash_z = _z_score(cash_ratios[-1], cash_ratios)
        futures_z = _z_score(futures_positions[-1], futures_positions)
        return _clamp(50 + cash_z * 12 * 0.7 + futures_z * 12 * 0.3)

    @staticmethod
    def _leverage_quality(observations: list[MarketObservation]) -> float:
        recent = observations[-60:]
        margin_changes = [
            current.margin_balance / previous.margin_balance - 1
            for previous, current in zip(recent[:-1], recent[1:], strict=True)
            if previous.margin_balance
        ]
        short_changes = [
            current.short_balance / previous.short_balance - 1
            for previous, current in zip(recent[:-1], recent[1:], strict=True)
            if previous.short_balance
        ]
        margin_z = _z_score(margin_changes[-1], margin_changes)
        short_z = _z_score(short_changes[-1], short_changes)
        return _clamp(55 - max(margin_z, 0) * 12 - max(short_z, 0) * 8)

    @staticmethod
    def _regime(score: float) -> MarketRegime:
        if score >= 65:
            return MarketRegime.RISK_ON
        if score >= 45:
            return MarketRegime.NEUTRAL
        return MarketRegime.RISK_OFF

    @staticmethod
    def _detect_alerts(
        observations: list[MarketObservation],
    ) -> list[Alert]:
        alerts: list[Alert] = []
        latest = observations[-1]
        above_ma20 = latest.above_ma20_issues / latest.total_issues * 100
        five_day_prior = observations[-6]
        prior_above_ma20 = (
            five_day_prior.above_ma20_issues / five_day_prior.total_issues * 100
        )
        participation_change = above_ma20 - prior_above_ma20
        index_is_high = latest.index_close >= max(
            item.index_close for item in observations[-20:]
        )
        if index_is_high and above_ma20 < 45:
            alerts.append(
                Alert(
                    code="narrow_leadership",
                    severity=AlertSeverity.HIGH,
                    title="指數位於近期高點，但市場參與度偏低",
                    evidence={
                        "above_ma20_pct": round(above_ma20, 2),
                        "index_close": latest.index_close,
                    },
                )
            )
        if participation_change <= -15:
            alerts.append(
                Alert(
                    code="breadth_breakdown",
                    severity=AlertSeverity.HIGH,
                    title="五日市場寬度快速惡化",
                    evidence={
                        "above_ma20_change_pp": round(participation_change, 2),
                    },
                )
            )

        advance_ratios = [
            item.advancing_issues
            / max(item.advancing_issues + item.declining_issues, 1)
            for item in observations[-3:]
        ]
        if fmean(advance_ratios) >= 0.65 and above_ma20 >= 55:
            alerts.append(
                Alert(
                    code="breadth_thrust",
                    severity=AlertSeverity.INFO,
                    title="多數成分股同步轉強，形成市場寬度推進",
                    evidence={
                        "three_day_advance_ratio_pct": round(
                            fmean(advance_ratios) * 100, 2
                        ),
                        "above_ma20_pct": round(above_ma20, 2),
                    },
                )
            )

        cash_ratios = [
            (
                item.foreign_net_flow
                + item.investment_trust_net_flow
                + item.dealer_net_flow
            )
            / item.turnover_value
            for item in observations[-60:]
        ]
        cash_z = _z_score(cash_ratios[-1], cash_ratios)
        if cash_z <= -2:
            alerts.append(
                Alert(
                    code="institutional_outflow",
                    severity=AlertSeverity.HIGH if cash_z <= -3 else AlertSeverity.MEDIUM,
                    title="法人淨流出顯著偏離近期分布",
                    evidence={
                        "net_flow_z_score": round(cash_z, 2),
                        "net_flow": round(
                            latest.foreign_net_flow
                            + latest.investment_trust_net_flow
                            + latest.dealer_net_flow,
                            2,
                        ),
                    },
                )
            )

        margin_changes = [
            current.margin_balance / previous.margin_balance - 1
            for previous, current in zip(
                observations[-60:-1],
                observations[-59:],
                strict=True,
            )
            if previous.margin_balance
        ]
        margin_z = _z_score(margin_changes[-1], margin_changes)
        if margin_z >= 2 and above_ma20 < 50:
            alerts.append(
                Alert(
                    code="leverage_overheat",
                    severity=AlertSeverity.MEDIUM,
                    title="融資擴張但市場參與度不足",
                    evidence={
                        "margin_change_z_score": round(margin_z, 2),
                        "above_ma20_pct": round(above_ma20, 2),
                    },
                )
            )
        return alerts

    @staticmethod
    def _confidence(
        factors: MarketEnvironmentFactors,
        observations: int,
        alerts: list[Alert],
    ) -> float:
        values = list(factors.model_dump().values())
        agreement = 1 - min(pstdev(values) / 50, 1)
        history = min(observations / 252, 1)
        high_alerts = sum(
            alert.severity == AlertSeverity.HIGH for alert in alerts
        )
        return round(
            _clamp(agreement * 0.6 + history * 0.4 - high_alerts * 0.05, 0, 1),
            3,
        )
