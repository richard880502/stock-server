from datetime import UTC, date, datetime

import pytest

from quant_signal.domain.models import Bar, CorporateAction
from quant_signal.quant.adjustments import apply_forward_adjustments


def _bar(day: int, close: float) -> Bar:
    trading_date = date(2024, 1, day)
    return Bar(
        symbol="TEST.TW",
        trading_date=trading_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000,
        available_at=datetime(2024, 1, day, 10, tzinfo=UTC),
        source="test",
    )


def test_forward_adjustment_changes_only_on_and_after_event() -> None:
    bars = [_bar(2, 100), _bar(3, 95), _bar(4, 96)]
    action = CorporateAction(
        symbol="TEST.TW",
        ex_date=date(2024, 1, 3),
        previous_close=100,
        reference_price=95,
        event_type="息",
        available_at=datetime(2024, 1, 3, 10, tzinfo=UTC),
        source="test",
    )

    adjusted = apply_forward_adjustments(bars, [action])

    assert adjusted[0].adjustment_factor == 1
    assert adjusted[0].adjusted_close == 100
    assert adjusted[1].adjusted_close == pytest.approx(100)
    assert adjusted[2].adjusted_close == pytest.approx(96 * 100 / 95)


def test_action_before_requested_bar_range_keeps_factor_stable() -> None:
    bars = [_bar(3, 95), _bar(4, 96)]
    action = CorporateAction(
        symbol="TEST.TW",
        ex_date=date(2024, 1, 2),
        previous_close=100,
        reference_price=95,
        event_type="息",
        available_at=datetime(2024, 1, 2, 10, tzinfo=UTC),
        source="test",
    )

    adjusted = apply_forward_adjustments(bars, [action])

    assert adjusted[0].adjustment_factor == pytest.approx(100 / 95)
    assert adjusted[1].adjustment_factor == pytest.approx(100 / 95)
