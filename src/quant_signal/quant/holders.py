"""Pure helpers for the weekly TDCC concentration series."""

from __future__ import annotations

from datetime import timedelta

from quant_signal.domain.models import ShareholdingPair, ShareholdingSnapshot

COMPARISON_GAP = timedelta(days=28)
# How far behind the newest week a screener read looks for the comparison row.
PAIR_WINDOW = timedelta(weeks=14)


def pair_snapshots(rows: list[ShareholdingSnapshot]) -> ShareholdingPair | None:
    """Newest row plus the newest row at least four weeks older (else the previous week).

    ``rows`` are one symbol's rows in any order; None when there are none.
    """
    if not rows:
        return None
    ordered = sorted(rows, key=lambda row: row.data_date, reverse=True)
    latest, older = ordered[0], ordered[1:]
    if not older:
        return ShareholdingPair(latest=latest)
    cutoff = latest.data_date - COMPARISON_GAP
    prior = next((row for row in older if row.data_date <= cutoff), older[0])
    return ShareholdingPair(latest=latest, prior=prior)
