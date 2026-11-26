from __future__ import annotations

from decimal import Decimal

import pytest

from quant_signal.domain import tw_trading as tw


def test_minimum_commission_and_floor():
    assert tw.commission(1_000) == 20  # 1.425 -> min 20
    assert tw.commission(100_000) == 142  # 142.5 floored
    assert tw.commission(0) == 0
    cfg = tw.TWCostConfig(commission_discount=Decimal("0.6"))
    assert tw.commission(100_000, cfg) == 85  # 85.5 floored
    assert tw.commission(10_000, cfg) == 20


def test_stock_etf_and_day_trade_tax():
    assert tw.transaction_tax(1_000_000, symbol="2330.TW") == 3000
    assert tw.transaction_tax(1_000_000, symbol="0050.TW") == 1000
    assert tw.transaction_tax(1_000_000, symbol="2330.TW", day_trade=True) == 1500
    # day-trade discount is stocks only
    assert tw.transaction_tax(1_000_000, symbol="00878.TW", day_trade=True) == 1000
    assert tw.is_etf("006208.TW") and tw.is_etf("00631L.TW") and not tw.is_etf("2330.TW")
    assert tw.fill_cost("buy", 1_000_000, symbol="2330.TW") == 1425
    assert tw.fill_cost("sell", 1_000_000, symbol="2330.TW") == 1425 + 3000


@pytest.mark.parametrize(
    ("price", "tick"),
    [(9.99, "0.01"), (10, "0.05"), (49.95, "0.05"), (50, "0.1"), (99.9, "0.1"),
     (100, "0.5"), (499.5, "0.5"), (500, "1"), (999, "1"), (1000, "5"), (2500, "5")],
)
def test_tick_bands(price, tick):
    assert tw.tick_size(price) == Decimal(tick)


def test_etf_tick_and_rounding():
    assert tw.tick_size(49.99, etf=True) == Decimal("0.01")
    assert tw.tick_size(50, etf=True) == Decimal("0.05")
    assert tw.round_to_tick_down(100.7) == Decimal("100.5")
    assert tw.round_to_tick_up(100.1) == Decimal("100.5")
    assert tw.round_to_tick_down(9.999) == Decimal("9.99")


def test_price_limits_round_inward():
    down, up = tw.price_limits(100)
    assert (down, up) == (Decimal("90"), Decimal("110"))
    # 55*1.1=60.5 -> 60.5 (tick .1); 55*.9=49.5 -> 49.5 (tick .05)
    down, up = tw.price_limits(55)
    assert (down, up) == (Decimal("49.5"), Decimal("60.5"))
    # 33.3*1.1=36.63 -> 36.60 ; 33.3*.9=29.97 -> 30.00
    down, up = tw.price_limits(33.3)
    assert (down, up) == (Decimal("30.00"), Decimal("36.60"))
    # 1000*1.1=1100; 1010: 1111 -> 1110 ; 909 legal (tick 1)
    down, up = tw.price_limits(1010)
    assert (down, up) == (Decimal("909"), Decimal("1110"))


def test_limit_lock_and_lots():
    assert tw.is_limit_locked(110, 110, 110, 110, 90)
    assert tw.limit_lock_direction(90, 90, 90, 110, 90) == "down"
    assert not tw.is_limit_locked(108, 110, 108, 110, 90)
    assert tw.round_lots(2_999) == 2_000
