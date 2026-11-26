"""Revision bookkeeping shared by the Postgres repository and the memory double.

Official feeds republish the same natural key (a restated monthly revenue, an
extended disposition, a changed dividend). Identical content is a no-op; changed
content becomes ``revision + 1`` with a fresh ``available_at`` so an as-of read can
still answer "what did we know on date D".
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from typing import Any

from pydantic import BaseModel

_ALWAYS_IGNORED = {"available_at", "revision"}


def plan_revisions[T: BaseModel](
    existing_latest: dict[Hashable, T],
    incoming: Iterable[T],
    key: Callable[[T], Hashable],
    *,
    ignore: set[str] | None = None,
    carry: tuple[str, ...] = (),
) -> list[T]:
    """Rows to insert: new keys at revision 1, changed content at latest + 1.

    ``ignore`` fields are left out of the "did it change" comparison; ``carry``
    fields keep the previous revision's value (e.g. a schedule's first-seen time).
    """
    skip = _ALWAYS_IGNORED | (ignore or set())
    batch: dict[Hashable, T] = {}
    for row in incoming:
        batch[key(row)] = row  # last one wins inside a batch
    planned: list[T] = []
    for row_key, row in batch.items():
        previous = existing_latest.get(row_key)
        if previous is None:
            planned.append(row.model_copy(update={"revision": 1}))
            continue
        if row.model_dump(exclude=skip) == previous.model_dump(exclude=skip):
            continue
        update: dict[str, Any] = {"revision": previous.revision}  # type: ignore[attr-defined]
        update["revision"] += 1
        update.update({name: getattr(previous, name) for name in carry})
        planned.append(row.model_copy(update=update))
    return planned


def latest_visible[T: BaseModel](
    rows: Iterable[T], key: Callable[[T], Hashable], as_of: Any
) -> dict[Hashable, T]:
    """Highest revision per key among rows whose ``available_at`` is not after ``as_of``."""
    best: dict[Hashable, T] = {}
    for row in rows:
        if row.available_at > as_of:  # type: ignore[attr-defined]
            continue
        current = best.get(key(row))
        if current is None or row.revision > current.revision:  # type: ignore[attr-defined]
            best[key(row)] = row
    return best
