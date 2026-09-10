from __future__ import annotations

from datetime import timedelta

from quant_signal.domain.models import MarketObservation, MarketRegime
from quant_signal.quant.market_environment import MarketEnvironmentEngine


def test_market_environment_uses_breadth_and_capital_inputs(
    healthy_market: list[MarketObservation],
) -> None:
    result = MarketEnvironmentEngine().analyze(
        healthy_market,
        as_of=healthy_market[-1].trading_date,
    )

    assert result.market == "TW"
    assert result.regime == MarketRegime.RISK_ON
    assert result.factors.participation > 60
    assert result.factors.advance_decline > 60
    assert result.factors.volume_breadth > 60
    assert result.latest.foreign_net_flow > 0


def test_market_environment_excludes_future_revisions(
    healthy_market: list[MarketObservation],
) -> None:
    as_of = healthy_market[-1].trading_date
    unavailable_revision = healthy_market[-1].model_copy(
        update={
            "above_ma20_issues": 10,
            "revision": 2,
            "available_at": healthy_market[-1].available_at + timedelta(days=1),
        }
    )

    engine = MarketEnvironmentEngine()
    expected = engine.analyze(healthy_market, as_of=as_of)
    actual = engine.analyze(
        [*healthy_market, unavailable_revision],
        as_of=as_of,
    )

    assert actual.factors == expected.factors
    assert actual.regime == expected.regime


def test_market_environment_detects_narrow_leadership(
    healthy_market: list[MarketObservation],
) -> None:
    weak_latest = healthy_market[-1].model_copy(
        update={
            "above_ma20_issues": 320,
            "above_ma60_issues": 300,
            "index_close": max(item.index_close for item in healthy_market) * 1.01,
        }
    )
    result = MarketEnvironmentEngine().analyze(
        [*healthy_market[:-1], weak_latest],
        as_of=weak_latest.trading_date,
    )

    assert "narrow_leadership" in {alert.code for alert in result.alerts}
