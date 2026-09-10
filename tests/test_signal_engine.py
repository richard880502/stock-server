from __future__ import annotations

from datetime import UTC, datetime, timedelta

from quant_signal.domain.models import Bar
from quant_signal.quant.engine import QuantSignalEngine
from quant_signal.quant.point_in_time import select_latest_records


def test_signal_is_deterministic_and_bounded(trending_bars: list[Bar]) -> None:
    engine = QuantSignalEngine()
    as_of = trending_bars[-1].trading_date

    first = engine.analyze(trending_bars, as_of=as_of)
    second = engine.analyze(trending_bars, as_of=as_of)

    assert first == second
    assert 0 <= first.score <= 100
    assert 0 <= first.confidence <= 1
    assert first.observations == len(trending_bars)


def test_revision_available_in_future_is_excluded(trending_bars: list[Bar]) -> None:
    engine = QuantSignalEngine()
    as_of = trending_bars[-1].trading_date
    baseline = engine.analyze(trending_bars, as_of=as_of)
    original = trending_bars[-1]
    future_revision = Bar(
        symbol=original.symbol,
        trading_date=original.trading_date,
        open=original.open,
        high=original.close * 11,
        low=original.low,
        close=original.close * 10,
        adjusted_close=original.close * 10,
        volume=original.volume * 100,
        available_at=datetime.combine(
            as_of + timedelta(days=1),
            datetime.min.time(),
            tzinfo=UTC,
        ),
        source=original.source,
        revision=2,
    )

    with_future_revision = engine.analyze(
        [*trending_bars, future_revision],
        as_of=as_of,
    )

    assert with_future_revision == baseline


def test_benchmark_relative_strength_changes_factor(trending_bars: list[Bar]) -> None:
    engine = QuantSignalEngine()
    slow_benchmark = [
        bar.model_copy(
            update={
                "symbol": "BENCH",
                "close": 100 + index * 0.01,
                "adjusted_close": 100 + index * 0.01,
                "open": 100 + index * 0.01,
                "high": (100 + index * 0.01) * 1.005,
                "low": (100 + index * 0.01) * 0.995,
            }
        )
        for index, bar in enumerate(trending_bars)
    ]

    snapshot = engine.analyze(
        trending_bars,
        as_of=trending_bars[-1].trading_date,
        benchmark_bars=slow_benchmark,
    )

    assert snapshot.factors.relative_strength > 50


def test_real_bar_wins_when_synthetic_data_overlaps(trending_bars: list[Bar]) -> None:
    synthetic = trending_bars[-1].model_copy(update={"source": "synthetic_demo"})
    official = trending_bars[-1].model_copy(
        update={"source": "twse_official", "close": synthetic.close * 0.9}
    )

    selected = select_latest_records([synthetic, official])

    assert selected == [official]


def test_synthetic_dates_inside_real_coverage_are_removed(
    trending_bars: list[Bar],
) -> None:
    first = trending_bars[0].model_copy(update={"source": "twse_official"})
    last = trending_bars[2].model_copy(update={"source": "twse_official"})
    fake_holiday = trending_bars[1].model_copy(update={"source": "synthetic_demo"})

    selected = select_latest_records([first, fake_holiday, last])

    assert selected == [first, last]


def test_sufficient_real_history_removes_all_synthetic_data(
    trending_bars: list[Bar],
) -> None:
    synthetic = [
        bar.model_copy(update={"source": "synthetic_demo"})
        for bar in trending_bars[:10]
    ]
    official = [
        bar.model_copy(update={"source": "twse_official"})
        for bar in trending_bars[10:80]
    ]

    selected = select_latest_records([*synthetic, *official])

    assert selected == official
