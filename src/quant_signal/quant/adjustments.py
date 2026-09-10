from __future__ import annotations

from quant_signal.domain.models import Bar, CorporateAction


def apply_forward_adjustments(
    bars: list[Bar],
    actions: list[CorporateAction],
) -> list[Bar]:
    """Return a point-in-time-safe, forward-adjusted bar series.

    The factor is 1 before an event and changes on the ex-date. A historical
    signal therefore never contains an adjustment caused by a later event.
    """

    if not bars:
        return []
    symbol = bars[0].symbol.upper()
    if any(bar.symbol.upper() != symbol for bar in bars):
        raise ValueError("one adjustment call may contain only one symbol")

    applicable = sorted(
        (action for action in actions if action.symbol.upper() == symbol),
        key=lambda item: (item.ex_date, item.revision, item.source),
    )

    factor = 1.0
    event_index = 0
    adjusted: list[Bar] = []
    for bar in sorted(bars, key=lambda item: item.trading_date):
        while (
            event_index < len(applicable)
            and applicable[event_index].ex_date <= bar.trading_date
        ):
            action = applicable[event_index]
            factor *= action.previous_close / action.reference_price
            event_index += 1
        adjusted.append(
            bar.model_copy(
                update={
                    "adjustment_factor": factor,
                    "adjusted_close": bar.close * factor,
                }
            )
        )
    return adjusted
