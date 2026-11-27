"""Quarterly earnings from Taiwan's year-to-date cumulative statements. Pure arithmetic.

A stored Q3 row holds January-September. Single-quarter figures are the difference
between consecutive cumulative rows of the same fiscal year (Q1 stands as is); a figure is
None whenever a needed row or value is missing -- never a guess. Trailing-four-quarter
figures use ``YTD + last fiscal year - same YTD a year ago`` so they only need three rows.
"""

from __future__ import annotations

from quant_signal.domain.fundamentals import EarningsMetrics, QuarterPoint, QuarterSingle
from quant_signal.domain.fundamentals import QuarterlyFinancial as Q

FLOW_FIELDS = ("revenue", "gross_profit", "operating_income", "net_income_parent", "eps")
BALANCE_FIELDS = ("equity_parent", "total_assets")
Key = tuple[int, int]


def latest_revisions(rows: list[Q]) -> dict[Key, Q]:
    """Highest revision per (fiscal_year, fiscal_quarter)."""
    best: dict[Key, Q] = {}
    for row in rows:
        key = (row.fiscal_year, row.fiscal_quarter)
        if key not in best or (row.revision, row.available_at) > (
            best[key].revision,
            best[key].available_at,
        ):
            best[key] = row
    return best


def _diff(current: float | None, previous: float | None) -> float | None:
    return None if current is None or previous is None else current - previous


def single_quarters(rows: list[Q]) -> list[QuarterSingle]:
    """Single-quarter values for every stored period, oldest first."""
    cumulative = latest_revisions(rows)
    singles: list[QuarterSingle] = []
    for (year, quarter), row in sorted(cumulative.items()):
        previous = cumulative.get((year, quarter - 1)) if quarter > 1 else None
        values: dict[str, float | None] = {}
        for name in FLOW_FIELDS:
            current = getattr(row, name)
            if quarter == 1:
                values[name] = current
            else:
                values[name] = _diff(current, getattr(previous, name) if previous else None)
        for name in BALANCE_FIELDS:
            values[name] = getattr(row, name)
        singles.append(
            QuarterSingle(
                symbol=row.symbol,
                fiscal_year=year,
                fiscal_quarter=quarter,
                statement_kind=row.statement_kind,
                available_at=row.available_at,
                **values,
            )
        )
    return singles


def _margin(part: float | None, revenue: float | None) -> float | None:
    if part is None or revenue is None or revenue <= 0:
        return None
    return round(part / revenue * 100, 2)


def _pp(current: float | None, prior: float | None) -> float | None:
    return None if current is None or prior is None else round(current - prior, 2)


def _ttm(cumulative: dict[Key, Q], key: Key, field: str) -> float | None:
    year, quarter = key
    current = cumulative.get(key)
    if current is None or getattr(current, field) is None:
        return None
    if quarter == 4:
        return getattr(current, field)
    annual, same = cumulative.get((year - 1, 4)), cumulative.get((year - 1, quarter))
    if annual is None or same is None:
        return None
    parts = (getattr(current, field), getattr(annual, field), getattr(same, field))
    if any(part is None for part in parts):
        return None
    return parts[0] + parts[1] - parts[2]


def quarter_points(rows: list[Q], count: int = 8) -> list[QuarterPoint]:
    """The newest ``count`` single quarters with margins, oldest first."""
    return [
        QuarterPoint(
            period=single.period,
            revenue=single.revenue,
            gross_margin_pct=_margin(single.gross_profit, single.revenue),
            operating_margin_pct=_margin(single.operating_income, single.revenue),
            net_margin_pct=_margin(single.net_income_parent, single.revenue),
            eps=None if single.eps is None else round(single.eps, 4),
        )
        for single in single_quarters(rows)[-count:]
    ]


def earnings_metrics(rows: list[Q]) -> EarningsMetrics | None:
    """Summary of the newest period in ``rows`` (any order, any revisions)."""
    cumulative = latest_revisions(rows)
    if not cumulative:
        return None
    key = max(cumulative)
    singles = {(s.fiscal_year, s.fiscal_quarter): s for s in single_quarters(rows)}
    current, prior = singles[key], singles.get((key[0] - 1, key[1]))
    gross = _margin(current.gross_profit, current.revenue)
    operating = _margin(current.operating_income, current.revenue)
    net = _margin(current.net_income_parent, current.revenue)
    eps_yoy: float | None = None
    if current.eps is not None and prior and prior.eps is not None and prior.eps > 0:
        eps_yoy = round((current.eps - prior.eps) / prior.eps * 100, 2)
    eps_ttm = _ttm(cumulative, key, "eps")
    income_ttm = _ttm(cumulative, key, "net_income_parent")
    equity = current.equity_parent
    roe = None
    if income_ttm is not None and equity is not None and equity > 0:
        roe = round(income_ttm / equity * 100, 2)
    return EarningsMetrics(
        symbol=current.symbol,
        period=current.period,
        statement_kind=current.statement_kind,
        revenue=current.revenue,
        eps=current.eps,
        eps_ttm=None if eps_ttm is None else round(eps_ttm, 4),
        eps_yoy_pct=eps_yoy,
        gross_margin_pct=gross,
        operating_margin_pct=operating,
        net_margin_pct=net,
        gross_margin_yoy_pp=_pp(
            gross, _margin(prior.gross_profit, prior.revenue) if prior else None
        ),
        operating_margin_yoy_pp=_pp(
            operating, _margin(prior.operating_income, prior.revenue) if prior else None
        ),
        net_margin_yoy_pp=_pp(
            net, _margin(prior.net_income_parent, prior.revenue) if prior else None
        ),
        net_income_ttm=income_ttm,
        equity_parent=equity,
        roe_ttm_pct=roe,
        available_at=current.available_at,
    )
