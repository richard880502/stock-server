"""Pure screener maths with hand-computed expectations."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from quant_signal.quant import screener as s

START = date(2026, 1, 5)


def _days(count: int) -> list[date]:
    days: list[date] = []
    cursor = START
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _history(
    closes: list[float],
    *,
    changes: list[float] | None = None,
    volume: list[float] | float = 10_000,
    total: list[float | None] | None = None,
    foreign: list[float | None] | None = None,
    margin_balance: list[float | None] | None = None,
    margin_change: list[float | None] | None = None,
    short_balance: list[float | None] | None = None,
    short_change: list[float | None] | None = None,
    high: list[float] | None = None,
    low: list[float] | None = None,
) -> s.StockHistory:
    n = len(closes)
    if changes is None:
        changes = [closes[0] * 0.0] + [closes[i] - closes[i - 1] for i in range(1, n)]
    vols = volume if isinstance(volume, list) else [volume] * n
    blank: list[float | None] = [None] * n
    rows = []
    for i, day in enumerate(_days(n)):
        rows.append(
            (
                day,
                closes[i],
                changes[i],
                high[i] if high else closes[i],
                low[i] if low else closes[i],
                vols[i],
                vols[i] * closes[i],
                (foreign or blank)[i],
                blank[i],
                blank[i],
                (total or blank)[i],
                (margin_balance or blank)[i],
                (margin_change or blank)[i],
                (short_balance or blank)[i],
                (short_change or blank)[i],
            )
        )
    return s.history_from_rows("2330.TW", "TSMC", "TW", rows)


# ------------------------------------------------------------- returns and chaining


def test_daily_return_uses_the_reference_price_not_the_previous_close() -> None:
    close = np.array([90.0, 91.8])
    change = np.array([-2.0, 1.8])  # day 1 reference was 92 (ex-dividend of 10 from 102)
    returns = s.daily_returns(close, change)
    assert returns[0] == pytest.approx(-2 / 92)
    assert returns[1] == pytest.approx(0.02)


def test_adjusted_levels_remove_an_ex_dividend_gap() -> None:
    # closes 100, 102, (ex-div 10) 90, 91.8; changes vs the reference price
    close = np.array([100.0, 102.0, 90.0, 91.8])
    change = np.array([0.0, 2.0, -2.0, 1.8])
    returns = s.daily_returns(close, change)
    adjusted, reference = s.adjusted_levels(close, returns)
    # chained back from 91.8: 90, 92, 90.1961
    assert adjusted[-1] == pytest.approx(91.8)
    assert adjusted[-2] == pytest.approx(90.0)
    assert adjusted[-3] == pytest.approx(92.0)
    assert adjusted[0] == pytest.approx(92.0 / 1.02)
    assert reference == pytest.approx(adjusted[0])  # first day's return is 0
    # the real two-day move is +2%, not the -10% a raw close comparison would show
    assert adjusted[-1] / adjusted[-3] - 1 == pytest.approx(91.8 / 92 - 1)


def test_adjusted_series_stops_at_an_unknown_reference_price() -> None:
    close = np.array([10.0, 11.0, 12.0, 13.2])
    change = np.array([1.0, 1.0, 12.0, 1.2])  # day 2: close - change = 0 -> unknown
    returns = s.daily_returns(close, change)
    adjusted, reference = s.adjusted_levels(close, returns)
    assert math.isnan(returns[2])
    assert len(adjusted) == 2  # days 2 and 3 only
    assert adjusted[0] == pytest.approx(12.0)
    assert reference is None


def test_price_metrics_on_a_linear_uptrend() -> None:
    closes = [100.0 + i for i in range(130)]
    m = s.compute_metrics(_history(closes), None)
    assert m is not None
    assert m["sessions"] == 130
    assert m["change_pct"] == pytest.approx(1 / 228 * 100, abs=1e-3)
    assert m["ret_5d"] == pytest.approx((229 / 224 - 1) * 100, abs=1e-3)
    assert m["ret_20d"] == pytest.approx((229 / 209 - 1) * 100, abs=1e-3)
    assert m["ret_120d"] == pytest.approx((229 / 109 - 1) * 100, abs=1e-3)
    assert m["ma20_gap_pct"] == pytest.approx((229 / 219.5 - 1) * 100, abs=1e-3)
    assert m["trend_aligned"] is True
    assert m["high_52w_gap_pct"] is None  # fewer than 250 sessions
    assert m["max_drawdown_60d"] == 0.0


def test_52_week_gap_and_flat_ma_alignment() -> None:
    closes = [100.0 + i for i in range(255)]
    closes[-1] = closes[-2] - 4.0  # pulls back from the high of 353
    m = s.compute_metrics(_history(closes), None)
    assert m is not None
    assert m["high_52w_gap_pct"] == pytest.approx((349 / 353 - 1) * 100, abs=1e-3)
    flat = s.compute_metrics(_history([50.0] * 130), None)
    assert flat is not None and flat["trend_aligned"] is False  # not strictly increasing


def test_returns_need_enough_history_and_twenty_sessions_minimum() -> None:
    assert s.compute_metrics(_history([10.0 + i for i in range(19)]), None) is None
    m = s.compute_metrics(_history([10.0 + i for i in range(30)]), None)
    assert m is not None
    assert m["ret_20d"] is not None
    assert m["ret_60d"] is None and m["ma60_gap_pct"] is None and m["ret_120d"] is None
    assert m["volume_ratio_5_60"] is None


def test_volatility_is_annualised_sample_stdev() -> None:
    price = 100.0
    closes = [price]
    for i in range(1, 30):
        price *= 1.01 if i % 2 else 0.99
        closes.append(price)
    returns = [0.01, -0.01] * 10  # the last 20 daily returns
    expected = math.sqrt(sum(r * r for r in returns) / 19) * math.sqrt(252) * 100
    m = s.compute_metrics(_history(closes), None)
    assert m is not None
    assert m["volatility_20d"] == pytest.approx(expected, rel=1e-3)


def test_max_drawdown_is_measured_on_the_last_60_sessions() -> None:
    closes = [100.0] * 20 + [100.0 + i * 2 for i in range(20)] + [138.0 - i for i in range(20)]
    # peak 138 then falls to 119 -> -13.77%
    m = s.compute_metrics(_history(closes), None)
    assert m is not None
    assert m["max_drawdown_60d"] == pytest.approx((119 / 138 - 1) * 100, abs=1e-3)


def test_volume_ratio_and_turnover() -> None:
    volume = [100.0] * 125 + [400.0] * 5
    m = s.compute_metrics(_history([10.0] * 130, volume=volume), None)
    assert m is not None
    assert m["volume_ratio_5_60"] == pytest.approx(400 / ((55 * 100 + 5 * 400) / 60), abs=1e-3)
    assert m["avg_turnover_20d"] == pytest.approx((15 * 100 + 5 * 400) / 20 * 10, abs=1e-3)


# -------------------------------------------------------------------- money flow


def _flow_history() -> s.StockHistory:
    total: list[float | None] = [1000.0] * 21 + [-3000.0] + [2000.0] * 3
    return _history([100.0] * 25, total=total, foreign=total)


def test_institutional_windows_streaks_and_ratios() -> None:
    history = _flow_history()
    m = s.compute_metrics(history, history.dates[-1])
    assert m is not None
    assert m["total_net_5d"] == pytest.approx(4.0)  # 1000 - 3000 + 3 * 2000 shares = 4 lots
    assert m["total_net_20d"] == pytest.approx(19.0)
    assert m["foreign_net_5d"] == pytest.approx(4.0)
    assert m["inst_flow_ratio_20d"] == pytest.approx(9.5)  # 19,000 / (20 * 10,000)
    assert m["foreign_flow_ratio_20d"] == pytest.approx(9.5)
    assert m["inst_buy_days_20d"] == pytest.approx(95.0)
    assert m["foreign_streak"] == 3
    assert m["total_net_60d"] is None and m["inst_buy_days_60d"] is None  # 25 sessions only


def test_flow_windows_end_at_flow_through() -> None:
    history = _flow_history()
    m = s.compute_metrics(history, history.dates[-3])  # report for the last 2 days not out
    assert m is not None
    assert m["total_net_5d"] == pytest.approx(2.0)  # 1000*3 - 3000 + 2000
    assert m["foreign_streak"] == 1  # only index 22 (2000) before the -3000 day


def test_selling_streak_is_negative_and_no_report_gives_none() -> None:
    total: list[float | None] = [500.0] * 20 + [-100.0] * 5
    history = _history([100.0] * 25, total=total)
    m = s.compute_metrics(history, history.dates[-1])
    assert m is not None and m["total_net_5d"] == pytest.approx(-0.5)
    unreported = s.compute_metrics(_history([100.0] * 25), None)
    assert unreported is not None
    assert unreported["total_net_20d"] is None and unreported["foreign_streak"] is None
    assert unreported["inst_flow_ratio_20d"] is None and unreported["chip_transfer_20d"] is None


def test_flow_ratio_ignores_sessions_without_a_report() -> None:
    # 8 sessions unreported, 12 reported at +1000 shares, volume 10,000 each
    total: list[float | None] = [None] * 13 + [1000.0] * 12
    history = _history([100.0] * 25, total=total)
    m = s.compute_metrics(history, history.dates[-1])
    assert m is not None
    assert m["total_net_20d"] == pytest.approx(12.0)
    # window index 5..24 holds 12 reports: 12,000 / (12 * 10,000) = 10%
    assert m["inst_flow_ratio_20d"] == pytest.approx(10.0)
    # fewer than half the window reported -> not computed
    sparse = _history([100.0] * 25, total=[None] * 20 + [1000.0] * 5)
    assert s.compute_metrics(sparse, sparse.dates[-1])["total_net_20d"] is None  # type: ignore[index]


def test_chaikin_money_flow_with_and_without_a_range() -> None:
    cmf = s.chaikin_money_flow(
        np.array([12.0, 10.0]),
        np.array([8.0, 10.0]),
        np.array([11.0, 10.0]),
        np.array([100.0, 300.0]),
    )
    # day 1 multiplier (3 - 1) / 4 = 0.5; day 2 has no range -> 0; (0.5 * 100) / 400
    assert cmf == pytest.approx(0.125)
    assert (
        s.chaikin_money_flow(np.array([1.0]), np.array([1.0]), np.array([1.0]), np.array([0.0]))
        is None
    )


def test_cmf_and_up_volume_in_metrics() -> None:
    closes = [105.0] * 25
    changes = [0.0] + [1.0, -1.0] * 12
    m = s.compute_metrics(
        _history(closes, changes=changes, high=[110.0] * 25, low=[90.0] * 25), None
    )
    assert m is not None
    assert m["cmf_20"] == pytest.approx(0.5)  # ((15) - (5)) / 20 on every day
    # last 20 changes: indices 5..24 -> 10 ups (odd indices) and 10 downs, equal volume
    assert m["up_volume_ratio_20d"] == pytest.approx(50.0)


def test_credit_changes_and_chip_transfer() -> None:
    n = 25
    changes: list[float | None] = [None] * 5 + [-5.0] * 20
    balance: list[float | None] = [None] * 24 + [1100.0]
    shorts: list[float | None] = [None] * 24 + [60.0]
    short_changes: list[float | None] = [None] * 5 + [1.0] * 20
    total: list[float | None] = [1000.0] * n
    history = _history(
        [100.0] * n,
        total=total,
        margin_balance=balance,
        margin_change=changes,
        short_balance=shorts,
        short_change=short_changes,
    )
    m = s.compute_metrics(history, history.dates[-1])
    assert m is not None
    # start = 1100 - (20 * -5) = 1200 -> -100 / 1200
    assert m["margin_change_20d_pct"] == pytest.approx(-100 / 1200 * 100, abs=1e-3)
    assert m["short_change_20d_pct"] == pytest.approx(20 / 40 * 100)
    assert m["chip_transfer_20d"] is True


def test_credit_is_none_when_stale_or_missing() -> None:
    n = 25
    stale = _history(
        [100.0] * n,
        total=[1000.0] * n,
        margin_balance=[1000.0] * 20 + [None] * 5,
        margin_change=[1.0] * 20 + [None] * 5,
    )
    m = s.compute_metrics(stale, stale.dates[-1])
    assert m is not None and m["margin_change_20d_pct"] is None
    assert m["chip_transfer_20d"] is None
    rising = _history(
        [100.0] * n,
        total=[1000.0] * n,
        margin_balance=[1000.0] * n,
        margin_change=[5.0] * n,
    )
    flat_out = s.compute_metrics(rising, rising.dates[-1])
    assert flat_out is not None and flat_out["chip_transfer_20d"] is False


# ------------------------------------------------------------------- cross-section


def test_percentile_ranks_share_the_average_rank_for_ties_and_skip_none() -> None:
    assert s.percentile_ranks([10, 20, None, 20, 30]) == [0.0, 50.0, None, 50.0, 100.0]
    assert s.percentile_ranks([5.0]) == [50.0]
    assert s.percentile_ranks([None, None]) == [None, None]
    assert s.percentile_ranks([1, 1, 1]) == [50.0, 50.0, 50.0]


def test_family_score_needs_at_least_half_of_its_inputs() -> None:
    assert s.family_score([(100.0, 0.3), (50.0, 0.3), (None, 0.2), (None, 0.2)]) == 75.0
    assert s.family_score([(100.0, 0.3), (None, 0.3), (None, 0.2), (None, 0.2)]) is None
    assert s.family_score([(80.0, 0.6), (None, 0.4)]) == 80.0  # one of two is enough
    assert s.family_score([]) is None


def test_family_score_needs_half_of_the_weight_not_half_of_the_inputs() -> None:
    # one heavy input (0.6 of the weight) is enough even though 1 of 3 inputs is available
    assert s.family_score([(80.0, 0.6), (None, 0.2), (None, 0.2)]) == 80.0
    # two light inputs carrying only 0.4 are not
    assert s.family_score([(80.0, 0.2), (60.0, 0.2), (None, 0.6)]) is None
    assert s.family_score([(80.0, 0.5), (None, 0.5)]) == 80.0  # exactly half passes


def test_new_holder_weights_and_scores_survive_missing_holder_inputs() -> None:
    flow = {k: w for k, w, _ in s.FAMILY_INPUTS["flow"]}
    assert flow["inst_flow_to_shares_20d"] == 0.15
    assert flow["inst_flow_ratio_20d"] == pytest.approx(0.30 * 0.85)
    acc = {k: (w, h) for k, w, h in s.FAMILY_INPUTS["accumulation"]}
    assert acc["big_holder_pct_change"] == (0.25, "id")
    assert acc["retail_holders_change_pct"] == (0.10, "neg")
    assert acc["inst_flow_ratio_60d"][0] == pytest.approx(0.45 * 0.65)
    metrics = [
        {
            "inst_flow_ratio_60d": 1.0,
            "inst_buy_days_60d": 60.0,
            "ret_20d": 1.0,
            "inst_flow_ratio_20d": 1.0,
            "inst_buy_days_20d": 60.0,
            "cmf_20": 0.1,
            "margin_change_20d_pct": 1.0,
        },
        {
            "inst_flow_ratio_60d": 5.0,
            "inst_buy_days_60d": 70.0,
            "ret_20d": 2.0,
            "inst_flow_ratio_20d": 5.0,
            "inst_buy_days_20d": 70.0,
            "cmf_20": 0.2,
            "margin_change_20d_pct": -1.0,
        },
    ]  # no holder inputs anywhere
    out = s.score_universe(metrics)
    assert out[0][0]["accumulation"] is not None and out[1][0]["flow"] is not None
    assert out[1][0]["accumulation"] > out[0][0]["accumulation"]


def test_composite_renormalises_over_available_families() -> None:
    scores = {
        "momentum": 80.0,
        "trend": None,
        "flow": 60.0,
        "fundamental": None,
        "stability": 100.0,
        "accumulation": 10.0,  # not part of the composite
    }
    assert s.composite_score(scores) == pytest.approx((80 * 0.25 + 60 * 0.30 + 100 * 0.15) / 0.70)
    assert s.composite_score({"momentum": None, "flow": None, "trend": 90.0}) is None
    assert s.composite_score({"momentum": None, "flow": 40.0}) == pytest.approx(40.0)


def test_weights_are_one_table_and_sum_to_one() -> None:
    assert sum(s.COMPOSITE_WEIGHTS.values()) == pytest.approx(1.0)
    for inputs in s.FAMILY_INPUTS.values():
        assert sum(weight for _, weight, _ in inputs) == pytest.approx(1.0)


def test_score_universe_inverts_volatility_and_uses_magnitude_for_accumulation() -> None:
    metrics = [
        {"volatility_20d": 10.0, "max_drawdown_60d": -5.0, "ret_20d": 1.0},
        {"volatility_20d": 20.0, "max_drawdown_60d": -10.0, "ret_20d": -6.0},
        {"volatility_20d": 30.0, "max_drawdown_60d": -20.0, "ret_20d": 15.0},
    ]
    out = s.score_universe(metrics)
    assert [scores["stability"] for scores, _ in out] == [100.0, 50.0, 0.0]
    # accumulation only has ret_20d (1 of 3 inputs) -> not enough inputs
    assert all(scores["accumulation"] is None for scores, _ in out)
    assert all(composite is None for _, composite in out)  # no flow and no momentum

    with_flow = [
        {"inst_flow_ratio_60d": 1.0, "inst_buy_days_60d": 60.0, "ret_20d": 1.0},
        {"inst_flow_ratio_60d": 3.0, "inst_buy_days_60d": 70.0, "ret_20d": -10.0},
    ]
    acc = [scores["accumulation"] for scores, _ in s.score_universe(with_flow)]
    # |ret_20d| = 1 vs 10: the quieter stock gets the higher percentile on that input
    assert acc == [pytest.approx(25.0), pytest.approx(75.0)]


def test_score_universe_composite_is_a_weighted_mean() -> None:
    metrics = [
        {
            "ret_20d": 1.0,
            "ret_60d": 1.0,
            "inst_flow_ratio_20d": 2.0,
            "inst_flow_ratio_60d": 2.0,
            "inst_buy_days_20d": 80.0,
        },
        {
            "ret_20d": 2.0,
            "ret_60d": 2.0,
            "inst_flow_ratio_20d": 1.0,
            "inst_flow_ratio_60d": 1.0,
            "inst_buy_days_20d": 50.0,
        },
    ]
    (low_scores, low), (high_scores, high) = s.score_universe(metrics)
    assert low_scores["momentum"] == 0.0 and low_scores["flow"] == 100.0
    # momentum 0 (weight .25) and flow 100 (weight .30) only
    assert low == pytest.approx(100 * 0.30 / 0.55, abs=0.01)
    assert high == pytest.approx(100 * 0.25 / 0.55, abs=0.01)
    assert high_scores["momentum"] == 100.0


# ---------------------------------------------------------------------- highlights

ADVICE = ("買進", "賣出", "建議", "推薦", "目標價")


def test_highlights_pick_the_strongest_signals_with_real_numbers() -> None:
    metrics = {
        "chip_transfer_20d": True,
        "trust_streak": 8,
        "foreign_streak": -12,
        "inst_flow_ratio_20d": 6.2,
        "margin_change_20d_pct": -4.1,
        "revenue_yoy_pct": 35.2,
        "trend_aligned": True,
    }
    out = s.build_highlights(metrics, [])
    assert len(out) == 4
    assert out[0] == "法人買、融資退（籌碼轉強）"
    assert "投信連買 8 日" in out
    assert "外資連賣 12 日" in out  # negative facts are surfaced too
    assert "法人 20 日買超佔成交量 6.2%" in out
    for text in out:
        assert not any(word in text for word in ADVICE)


def test_highlights_cover_weaker_facts_when_nothing_stronger_exists() -> None:
    out = s.build_highlights(
        {"margin_change_20d_pct": -4.1, "revenue_yoy_pct": 35.2, "high_52w_gap_pct": -1.8}, []
    )
    assert out == ["距 52 週高點 1.8%", "營收年增 35.2%", "融資 20 日減 4.1%"]
    assert s.build_highlights({}, []) == []
    assert s.build_highlights({"inst_flow_ratio_20d": 1.0, "trust_streak": 2}, []) == []
    assert s.build_highlights({}, ["disposition"]) == ["處置股"]
    assert s.build_highlights({"inst_flow_ratio_20d": -5.0}, []) == ["法人 20 日賣超佔成交量 5.0%"]


def test_metric_catalogue_is_consistent() -> None:
    keys = [metric.key for metric in s.METRICS]
    assert len(keys) == len(set(keys))
    for inputs in s.FAMILY_INPUTS.values():
        for key, _, _ in inputs:
            assert key in s.NUMERIC_KEYS
    groups = {key for key, _ in s.GROUPS}
    assert {metric.group for metric in s.METRICS} <= groups


# ------------------------------------------------------------------- holder metrics


def _pair(prior: bool = True):
    from datetime import UTC, datetime

    from quant_signal.domain.models import ShareholdingPair, ShareholdingSnapshot

    def snap(day: date, pct400: float, pct1000: float, retail: int) -> ShareholdingSnapshot:
        return ShareholdingSnapshot(
            symbol="2330.TW",
            data_date=day,
            total_holders=1000,
            total_shares=1_000_000,
            holders_ge400=10,
            pct_ge400=pct400,
            holders_ge1000=5,
            pct_ge1000=pct1000,
            retail_holders=retail,
            retail_pct=5.0,
            source="t",
            available_at=datetime(2026, 12, 1, tzinfo=UTC),
        )

    return ShareholdingPair(
        latest=snap(date(2026, 11, 20), 45.1, 30.5, 969),
        prior=snap(date(2026, 10, 23), 43.9, 30.0, 1000) if prior else None,
    )


def test_holder_metrics_hand_computed_and_none_cases() -> None:
    m = s.holder_metrics(_pair(), None)
    assert m["big_holder_pct"] == 45.1 and m["whale_pct"] == 30.5
    assert m["big_holder_pct_change"] == pytest.approx(1.2)
    assert m["whale_pct_change"] == pytest.approx(0.5)
    assert m["retail_holders_change_pct"] == pytest.approx(-3.1)
    assert m["holders_weeks_span"] == 28 and m["holders_data_date"] == "2026-11-20"
    assert m["foreign_holding_pct"] is None
    no_prior = s.holder_metrics(_pair(prior=False), None)
    assert no_prior["big_holder_pct"] == 45.1
    assert no_prior["big_holder_pct_change"] is None and no_prior["holders_weeks_span"] is None
    assert set(s.holder_metrics(None, None).values()) == {None}


def test_share_based_metrics_use_shares_outstanding() -> None:
    from datetime import UTC, datetime

    from quant_signal.domain.models import ForeignHolding

    def held(shares: int) -> ForeignHolding:
        return ForeignHolding(
            symbol="X",
            trading_date=date(2026, 11, 20),
            shares_outstanding=shares,
            foreign_shares=0,
            foreign_holding_pct=42.3,
            source="t",
            available_at=datetime(2026, 11, 20, tzinfo=UTC),
        )

    history = _flow_history()  # close 100, volume 10,000, 19 lots net over 20 days
    m = s.compute_metrics(history, history.dates[-1], None, held(1_000_000))
    assert m is not None
    assert m["market_cap"] == pytest.approx(1e8)
    assert m["turnover_rate_20d"] == pytest.approx(20.0)  # 200,000 / 1,000,000
    assert m["inst_flow_to_shares_20d"] == pytest.approx(1.9)  # 19,000 / 1,000,000
    assert m["inst_flow_to_shares_60d"] is None and m["foreign_holding_pct"] == 42.3
    zero = s.compute_metrics(history, history.dates[-1], None, held(0))
    assert zero is not None
    assert zero["market_cap"] is None and zero["turnover_rate_20d"] is None
    assert zero["inst_flow_to_shares_20d"] is None
    none = s.compute_metrics(history, history.dates[-1])
    assert none is not None and none["market_cap"] is None


def test_holder_highlights_state_the_real_span_without_advice() -> None:
    base = {
        "holders_weeks_span": 28,
        "big_holder_pct_change": 1.2,
        "retail_holders_change_pct": -3.1,
    }
    out = s.build_highlights({**base, "foreign_holding_pct": 42.3}, [])
    assert "400 張以上大戶持股 4 週增 1.2 個百分點" in out
    assert "散戶人數 4 週減 3.1%" in out and "外資持股 42.3%" in out
    one_week = s.build_highlights({**base, "holders_weeks_span": 7}, [])
    assert "400 張以上大戶持股 1 週增 1.2 個百分點" in one_week
    assert s.build_highlights({"big_holder_pct_change": 1.2}, []) == []  # no span, no claim
    assert s.build_highlights(
        {**base, "big_holder_pct_change": 0.1, "holders_weeks_span": 7}, []
    ) == ["散戶人數 1 週減 3.1%"]
    for text in out:
        assert not any(word in text for word in ADVICE)
