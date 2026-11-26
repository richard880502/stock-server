from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from conftest import make_bars

from quant_signal.backtest import BacktestEngine
from quant_signal.domain.models import BacktestSpec, Bar
from quant_signal.live_bar import TAIPEI, MinuteQuote, build_provisional_bar

DAY = date(2026, 11, 26)


def minute(hh, mm, o, h, low, c, first, last):
    start = datetime(2026, 11, 26, hh, mm, tzinfo=TAIPEI)
    return MinuteQuote(start, start + timedelta(seconds=50), o, h, low, c, first, last)


def test_provisional_bar_construction_and_point_in_time():
    minutes = [
        minute(9, 1, 101, 103, 100, 102, 50, 80),
        minute(9, 0, 100, 101, 99, 101, 0, 50),  # unsorted on purpose
        minute(9, 2, 102, 108, 101, 107, 80, 120),  # after as_of: ignored
    ]
    as_of = datetime(2026, 11, 26, 9, 1, 59, tzinfo=TAIPEI)
    bar, stamp = build_provisional_bar("2330.TW", DAY, minutes, as_of=as_of)
    assert (bar.open, bar.high, bar.low, bar.close) == (100, 103, 99, 102)
    assert bar.volume == (50 + 30) * 1000
    assert bar.source == "live_provisional" and bar.available_at == stamp
    assert stamp == datetime(2026, 11, 26, 9, 1, 50, tzinfo=TAIPEI)


def test_provisional_bar_none_without_minutes_or_other_day():
    as_of = datetime(2026, 11, 26, 12, 0, tzinfo=TAIPEI)
    one = [minute(9, 0, 1, 1, 1, 1, 0, 1)]
    assert build_provisional_bar("2330.TW", DAY, [], as_of=as_of) is None
    early = datetime(2026, 11, 26, 8, 0, tzinfo=TAIPEI)
    assert build_provisional_bar("2330.TW", DAY, one, as_of=early) is None
    assert build_provisional_bar("2330.TW", date(2026, 11, 25), one, as_of=as_of) is None


def test_engine_marks_provisional_and_uses_it():
    bars = make_bars(symbol="0050.TW", count=200)
    last = bars[-1]
    stamp = datetime(2026, 11, 26, 10, 30, tzinfo=TAIPEI)
    prov = Bar(
        symbol="0050.TW",
        trading_date=last.trading_date + timedelta(days=1),
        open=last.close * 1.01,
        high=last.close * 1.02,
        low=last.close,
        close=last.close * 1.015,
        volume=1000,
        available_at=stamp,
        source="live_provisional",
    )
    spec = BacktestSpec(
        symbol="0050.TW",
        start=bars[80].trading_date,
        end=prov.trading_date,
        entry_score=55,
        exit_score=40,
    )
    base = BacktestEngine().run(bars, spec)
    live = BacktestEngine().run([*bars, prov], spec, provisional_as_of=stamp)
    assert live.provisional_bar and live.as_of_time == stamp
    assert any("含今日盤中暫定 K 棒（截至 10:30）" in w for w in live.warnings)
    assert live.observations == base.observations + 1
    assert not base.provisional_bar and base.as_of_time is None


def test_default_cost_model_before_after():
    """Documented effect: legacy 5 bps fee / 0 tax vs Taiwan rules on one sample."""

    class Engine:
        minimum_observations = 60

        def analyze(self, history, **kwargs):
            return SimpleNamespace(score=100 if len(history) < 90 else 0)

    bars = make_bars(symbol="2330.TW", count=200)
    period = {"start": bars[80].trading_date, "end": bars[110].trading_date}
    engine = BacktestEngine(Engine())
    old = engine.run(bars, BacktestSpec(symbol="2330.TW", **period, fee_bps=5, sell_tax_bps=0))
    new = engine.run(bars, BacktestSpec(symbol="2330.TW", **period))
    etf_bars = make_bars(symbol="0050.TW", count=200)
    etf = engine.run(etf_bars, BacktestSpec(symbol="0050.TW", **period))
    assert old.cost_model == "custom" and new.cost_model == "tw"
    # buy: 1,000,000 * 0.1425% = 1425 + 5 bps slippage 500
    assert round(new.transactions[0].cost) == 1925
    assert new.transactions[1].cost > 4000
    assert new.total_return < old.total_return
    assert etf.transactions[1].cost < new.transactions[1].cost  # ETF tax 0.1% < 0.3%
    print("old", old.total_return, "new", new.total_return, "etf", etf.total_return)
    print("old costs", [t.cost for t in old.transactions])
    print("new costs", [t.cost for t in new.transactions])


def test_ex_rights_guard():
    from quant_signal.live_bar import ex_rights_suspected

    as_of = datetime(2026, 11, 26, 9, 5, tzinfo=TAIPEI)

    def bar_at(price):
        minutes = [minute(9, 0, price, price, price, price, 0, 5)]
        return build_provisional_bar("2330.TW", DAY, minutes, as_of=as_of)[0]

    assert not ex_rights_suspected(bar_at(105), 100)
    assert not ex_rights_suspected(bar_at(110), 100)  # limit-up is legal
    assert ex_rights_suspected(bar_at(80), 100)  # -20% gap: impossible without ex-date


def test_engine_surfaces_note_when_provisional_skipped():
    bars = make_bars(symbol="0050.TW", count=200)
    spec = BacktestSpec(symbol="0050.TW", start=bars[80].trading_date, end=bars[-1].trading_date)
    report = BacktestEngine().run(bars, spec, provisional_note="今日可能為除權息日")
    assert not report.provisional_bar and "今日可能為除權息日" in report.warnings
