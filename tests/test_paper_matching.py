"""Hand-computed cases for the pure paper-trading maths (matching, fees, ticks, sessions)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from paper_support import FRI, MON, at

from quant_signal.domain.tw_trading import is_on_tick, move_ticks, tick_down, tick_up
from quant_signal.quant.paper_calendar import (
    in_session,
    is_trading_day,
    latest_session_day,
    next_trading_day,
    previous_trading_day,
    session_for_order,
)
from quant_signal.quant.paper_matching import (
    Observation,
    OrderSpec,
    compute_fill,
    day_trade_shares,
    estimate_price,
    limit_price_problem,
    limits_for,
    match_order,
    reserve_for_buy,
    resolve_reference,
)

D = Decimal
DOWN, UP = D("90.0"), D("110.0")  # reference 100


# --- ticks, every band ------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "etf", "up", "down"),
    [
        ("5.00", False, "5.01", "4.99"),
        ("9.99", False, "10.00", "9.98"),
        ("10.00", False, "10.05", "9.99"),
        ("49.95", False, "50.00", "49.90"),
        ("50.00", False, "50.10", "49.95"),
        ("99.90", False, "100.00", "99.80"),
        ("100.00", False, "100.50", "99.90"),
        ("499.50", False, "500.00", "499.00"),
        ("500.00", False, "501.00", "499.50"),
        ("999.00", False, "1000.00", "998.00"),
        ("1000.00", False, "1005.00", "999.00"),
        ("2150.00", False, "2155.00", "2145.00"),
        ("49.99", True, "50.00", "49.98"),
        ("50.00", True, "50.05", "49.99"),
        ("150.00", True, "150.05", "149.95"),
    ],
)
def test_one_tick_up_and_down_crosses_bands(price, etf, up, down) -> None:
    assert tick_up(D(price), etf=etf) == D(up)
    assert tick_down(D(price), etf=etf) == D(down)


def test_two_ticks_and_grid_checks() -> None:
    assert move_ticks(D("99.90"), 2, "up") == D("100.50")  # 99.9 -> 100.0 -> 100.5
    assert move_ticks(D("100.50"), 2, "down") == D("99.90")
    assert move_ticks(D("100"), 0, "up") == D("100")
    assert is_on_tick(D("100.50")) and not is_on_tick(D("100.30"))
    assert is_on_tick(D("99.90")) and not is_on_tick(D("99.95"))
    assert is_on_tick(D("0.05"), etf=True) is True


def test_price_limits_are_rounded_inward() -> None:
    assert limits_for(D("100"), False) == (D("90.0"), D("110.0"))
    # 55.5 +/-10% = 49.95 .. 61.05: 49.95 is on the 0.05 band; 61.05 rounds down to 61.0
    assert limits_for(D("55.5"), False) == (D("49.95"), D("61.0"))
    assert limits_for(D("100.3"), False) == (D("90.3"), D("110.0"))


# --- sessions ---------------------------------------------------------------------


def test_trading_calendar_and_order_session() -> None:
    assert is_trading_day(FRI) and not is_trading_day(date(2026, 11, 28))
    assert next_trading_day(FRI) == MON and previous_trading_day(MON) == FRI
    assert session_for_order(at(FRI, 13, 29, 59)) == (FRI, False)
    assert session_for_order(at(FRI, 13, 30)) == (MON, True)
    assert session_for_order(at(FRI, 20, 0)) == (MON, True)
    assert session_for_order(at(date(2026, 11, 28), 11)) == (MON, True)  # Saturday
    assert session_for_order(at(MON, 8, 0)) == (MON, False)  # pre-open: valid today
    assert in_session(at(FRI, 9, 0)) and in_session(at(FRI, 13, 30))
    assert not in_session(at(FRI, 8, 59)) and not in_session(at(FRI, 13, 31))
    assert not in_session(at(date(2026, 11, 28), 10))
    assert latest_session_day(at(MON, 8, 0)) == FRI and latest_session_day(at(MON, 9, 0)) == MON


# --- market / limit matching from observations --------------------------------------


def _spec(side, kind="market", limit=None, created=None, session=FRI, etf=False):
    return OrderSpec(
        side=side,
        order_type=kind,
        limit_price=None if limit is None else D(limit),
        created_at=created or at(FRI, 10, 0, 0),
        session_date=session,
        etf=etf,
    )


def _tick(hh, mm, ss, price, source="tick", day=FRI):
    return Observation(time=at(day, hh, mm, ss), source=source, price=D(price))


def _match(spec, observations, slippage=1):
    return match_order(spec, observations, limit_down=DOWN, limit_up=UP, slippage_ticks=slippage)


def test_observation_must_be_strictly_after_creation_and_inside_the_session() -> None:
    spec = _spec("buy")
    assert _match(spec, [_tick(10, 0, 0, "101")]) is None  # same instant: not after
    assert _match(spec, [_tick(9, 59, 59, "101")]) is None
    assert _match(spec, [_tick(10, 0, 1, "101", day=MON)]) is None  # another day
    assert _match(_spec("buy", created=at(FRI, 7)), [_tick(8, 59, 0, "101")]) is None
    assert _match(_spec("buy", created=at(FRI, 7)), [_tick(13, 35, 1, "101")]) is None
    # the closing-auction print carries 13:30 but arrives a moment later: still counts
    closing = _match(_spec("buy", created=at(FRI, 7)), [_tick(13, 30, 1, "101")])
    assert closing is not None and closing.price == D("101.50")
    assert _match(_spec("buy", created=at(FRI, 7)), [_tick(13, 35, 0, "101")]) is not None
    found = _match(spec, [_tick(10, 0, 1, "101")])
    assert found is not None and found.price == D("101.50") and found.source == "tick"


def test_market_buy_pays_one_tick_and_never_fills_locked_limit_up() -> None:
    assert _match(_spec("buy"), [_tick(10, 0, 5, "100")]).price == D("100.50")
    assert _match(_spec("buy"), [_tick(10, 0, 5, "99.9")]).price == D("100.00")  # band cross
    assert _match(_spec("buy"), [_tick(10, 0, 5, "110")]) is None  # locked
    later = _match(_spec("buy"), [_tick(10, 0, 5, "110"), _tick(10, 0, 9, "109.5")])
    assert later.price == D("110.0") and later.time == at(FRI, 10, 0, 9)  # capped at limit-up
    assert _match(_spec("buy"), [_tick(10, 0, 5, "100")], slippage=0).price == D("100")
    assert _match(_spec("buy"), [_tick(10, 0, 5, "100")], slippage=2).price == D("101.00")


def test_market_sell_mirrors_and_respects_locked_limit_down() -> None:
    assert _match(_spec("sell"), [_tick(10, 0, 5, "101")]).price == D("100.50")
    assert _match(_spec("sell"), [_tick(10, 0, 5, "100")]).price == D("99.90")
    assert _match(_spec("sell"), [_tick(10, 0, 5, "90")]) is None
    floored = _match(_spec("sell"), [_tick(10, 0, 5, "90.1")])
    assert floored.price == D("90.0")  # 90.1 - 0.1, floored at limit-down


def test_limit_buy_fills_at_most_the_limit() -> None:
    spec = _spec("buy", "limit", "100")
    assert _match(spec, [_tick(10, 0, 5, "101")]) is None
    assert _match(spec, [_tick(10, 0, 5, "100")]).price == D("100")  # min(100, 100.5)
    assert _match(spec, [_tick(10, 0, 5, "99")]).price == D("99.1")  # price + 1 tick
    assert _match(spec, [_tick(10, 0, 5, "99.9")]).price == D("100.0")  # capped at limit
    first = _match(spec, [_tick(10, 0, 9, "99"), _tick(10, 0, 5, "101"), _tick(10, 0, 6, "100")])
    assert first.time == at(FRI, 10, 0, 6)  # input order does not matter


def test_limit_sell_fills_at_least_the_limit() -> None:
    spec = _spec("sell", "limit", "102")
    assert _match(spec, [_tick(10, 0, 5, "101")]) is None
    assert _match(spec, [_tick(10, 0, 5, "102")]).price == D("102")  # max(102, 101.5)
    assert _match(spec, [_tick(10, 0, 5, "103")]).price == D("102.5")
    assert _match(spec, [_tick(10, 0, 5, "102.5")]).price == D("102.0")  # floor at limit


def test_limit_order_at_the_limit_up_price_still_waits_while_locked() -> None:
    spec = _spec("buy", "limit", "110")
    assert _match(spec, [_tick(10, 0, 5, "110")]) is None
    assert _match(spec, [_tick(10, 0, 6, "109")]).price == D("109.5")


def test_etf_slippage_uses_the_etf_tick() -> None:
    spec = _spec("buy", etf=True)
    assert _match(spec, [_tick(10, 0, 5, "49.99")]).price == D("50.00")
    assert _match(spec, [_tick(10, 0, 5, "50.00")]).price == D("50.05")


def test_queued_order_meets_the_open_price_first() -> None:
    queued = _spec("buy", "limit", "101", created=at(FRI, 20, 0), session=MON)
    obs = [
        Observation(time=at(FRI, 14, 0), source="tick", price=D("99")),  # previous session
        Observation(time=at(MON, 9, 0), source="open", price=D("100")),
        _tick(9, 5, 0, "99", day=MON),
    ]
    found = _match(queued, obs)
    assert found.source == "open" and found.time == at(MON, 9, 0)
    assert found.price == D("100.5")  # min(limit 101, open 100 + 1 tick)
    no_open = _match(queued, obs[:1] + obs[2:])  # without the open print: the 99 tick
    assert no_open.source == "tick" and no_open.price == D("99.1")


# --- minute bars --------------------------------------------------------------------


def _bar(hh, mm, o, h, low, day=FRI):
    return Observation(time=at(day, hh, mm), source="minute_bar", price=D(o), high=D(h), low=D(low))


def test_bar_must_start_after_the_order_was_created() -> None:
    spec = _spec("buy")
    assert _match(spec, [_bar(10, 0, "100", "100", "100")]) is None  # starts at 10:00:00
    assert _match(spec, [_bar(10, 1, "100", "100", "100")]).price == D("100.5")


def test_limit_buy_from_bars() -> None:
    spec = _spec("buy", "limit", "100")
    assert _match(spec, [_bar(10, 1, "101", "102", "100.5")]) is None  # low above limit
    # opened above the limit but traded down to it: fills at the limit
    assert _match(spec, [_bar(10, 1, "101", "102", "99.9")]).price == D("100")
    # opened at/below the limit: open + 1 tick, capped at the limit
    assert _match(spec, [_bar(10, 1, "99", "99.5", "98")]).price == D("99.1")
    assert _match(spec, [_bar(10, 1, "100", "100", "99")]).price == D("100")


def test_limit_sell_from_bars() -> None:
    spec = _spec("sell", "limit", "102")
    assert _match(spec, [_bar(10, 1, "101", "101.5", "100")]) is None
    assert _match(spec, [_bar(10, 1, "101", "102.5", "100")]).price == D("102")
    assert _match(spec, [_bar(10, 1, "103", "104", "102")]).price == D("102.5")
    assert _match(spec, [_bar(10, 1, "102", "102", "101")]).price == D("102")


def test_market_orders_from_bars_use_open_and_respect_locks() -> None:
    assert _match(_spec("buy"), [_bar(10, 1, "101", "103", "100")]).price == D("101.5")
    assert _match(_spec("sell"), [_bar(10, 1, "101", "103", "100")]).price == D("100.5")
    assert _match(_spec("buy"), [_bar(10, 1, "110", "110", "110")]) is None  # locked up
    assert _match(_spec("sell"), [_bar(10, 1, "90", "90", "90")]) is None  # locked down
    # a bar that opened at limit-up but traded lower is not locked: buy capped at limit-up
    assert _match(_spec("buy"), [_bar(10, 1, "110", "110", "109")]).price == D("110.0")


def test_a_later_tick_wins_over_a_later_bar_only_by_time() -> None:
    spec = _spec("buy", "limit", "100")
    obs = [_bar(10, 5, "99", "99", "99"), _tick(10, 2, 0, "100")]
    assert _match(spec, obs).source == "tick"


# --- fees and taxes (whole NT$, floored; min NT$20) ----------------------------------


def test_commission_minimum_and_discount() -> None:
    small = compute_fill("buy", 1000, D("5"), symbol="1101.TW")
    assert (small.amount, small.commission, small.tax) == (5000, 20, 0)  # 7.125 -> min 20
    big = compute_fill("buy", 1000, D("1000"), symbol="1101.TW")
    assert big.amount == 1_000_000 and big.commission == 1425
    discounted = compute_fill(
        "buy", 1000, D("1000"), symbol="1101.TW", commission_discount=D("0.6")
    )
    assert discounted.commission == 855
    floor_case = compute_fill("buy", 1000, D("100.5"), symbol="1101.TW")
    assert floor_case.commission == 143  # 143.2125 floored
    below_min = compute_fill("buy", 1000, D("10"), symbol="1101.TW", commission_discount=D("0.3"))
    assert below_min.commission == 20  # 10,000 x 0.001425 x 0.3 = 4.275 -> min


def test_sell_tax_stock_etf_and_day_trade() -> None:
    stock = compute_fill("sell", 1000, D("1000"), symbol="2330.TW")
    assert (stock.amount, stock.commission, stock.tax) == (1_000_000, 1425, 3000)
    etf = compute_fill("sell", 1000, D("1000"), symbol="0050.TW")
    assert etf.tax == 1000
    day_trade = compute_fill("sell", 1000, D("1000"), symbol="2330.TW", day_trade_qty=1000)
    assert day_trade.tax == 1500 and day_trade.day_trade is True
    etf_day_trade = compute_fill("sell", 1000, D("1000"), symbol="0050.TW", day_trade_qty=1000)
    assert etf_day_trade.tax == 1000  # ETFs keep their own rate
    mixed = compute_fill("sell", 2000, D("100"), symbol="1101.TW", day_trade_qty=1000)
    assert mixed.amount == 200_000 and mixed.tax == 150 + 300  # half at 0.15%, half at 0.3%
    assert (
        compute_fill("sell", 1000, D("100"), symbol="1101.TW", day_trade_qty=0).day_trade is False
    )


def test_amount_rounds_half_up_to_whole_dollars() -> None:
    fill = compute_fill("buy", 1000, D("12.345"), symbol="1101.TW")
    assert fill.amount == 12345  # 1000 x 12.345 is exact; shares are whole lots
    assert compute_fill("buy", 500, D("12.345"), symbol="1101.TW").amount == 6173  # 6172.5 -> up


def test_day_trade_share_split() -> None:
    assert day_trade_shares(1000, bought_today=1000, sold_today=0) == 1000
    assert day_trade_shares(2000, bought_today=1000, sold_today=0) == 1000
    assert day_trade_shares(1000, bought_today=1000, sold_today=1000) == 0
    assert day_trade_shares(1000, bought_today=0, sold_today=0) == 0


# --- reserve, estimates, validation ---------------------------------------------------


def test_reserve_for_buys() -> None:
    # 1 lot at limit 100: 100,000 + floor(142.5) = 100,142
    assert reserve_for_buy(1000, D("100"), D("1")) == 100_142
    # market order: reserved at the limit-up price (110): 110,000 + 156
    assert reserve_for_buy(1000, D("110"), D("1")) == 110_156
    assert reserve_for_buy(1000, D("100"), D("0.6")) == 100_085  # floor(85.5)


def test_estimate_price_for_previews() -> None:
    kwargs = {"etf": False, "slippage_ticks": 1, "limit_down": DOWN, "limit_up": UP}
    assert estimate_price("buy", "market", None, D("100"), **kwargs) == D("100.5")
    assert estimate_price("buy", "market", None, D("110"), **kwargs) == D("110.0")
    assert estimate_price("buy", "limit", D("100"), D("99"), **kwargs) == D("99.1")
    assert estimate_price("buy", "limit", D("100"), D("105"), **kwargs) == D("100")
    assert estimate_price("sell", "market", None, D("100"), **kwargs) == D("99.9")
    assert estimate_price("sell", "limit", D("102"), D("101"), **kwargs) == D("102")
    assert estimate_price("buy", "market", None, None, **kwargs) == UP


def test_limit_price_validation_messages() -> None:
    ok = {"etf": False, "limit_down": DOWN, "limit_up": UP}
    assert limit_price_problem(D("100.5"), **ok) is None
    assert "升降單位" in limit_price_problem(D("100.3"), **ok)
    assert "升降單位" in limit_price_problem(D("99.95"), **ok)
    assert "漲跌幅" in limit_price_problem(D("110.5"), **ok)
    assert "漲跌幅" in limit_price_problem(D("89.9"), **ok)
    assert "大於 0" in limit_price_problem(D("0"), **ok)
    assert limit_price_problem(D("50.05"), etf=True, limit_down=D("45"), limit_up=D("55")) is None


def test_reference_price_resolution() -> None:
    kwargs = {"official_close": D("100"), "official_date": date(2026, 11, 26)}
    # same-session quote with the exchange reference wins
    assert resolve_reference(
        FRI, quote_reference=D("101"), quote_price=D("103"), quote_date=FRI, **kwargs
    ) == D("101")
    # next session: yesterday's last trade is newer than the official row
    assert resolve_reference(
        MON, quote_reference=D("101"), quote_price=D("103"), quote_date=FRI, **kwargs
    ) == D("103")
    # the official close of that day is already in: it is not older than the quote
    assert resolve_reference(
        MON,
        quote_reference=D("101"),
        quote_price=D("103"),
        quote_date=FRI,
        official_close=D("103"),
        official_date=FRI,
    ) == D("103")
    assert resolve_reference(
        FRI, quote_reference=None, quote_price=None, quote_date=None, **kwargs
    ) == D("100")
    assert (
        resolve_reference(
            FRI,
            quote_reference=None,
            quote_price=None,
            quote_date=None,
            official_close=None,
            official_date=None,
        )
        is None
    )
