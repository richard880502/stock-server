"""Length checks the in-memory repositories share with the Postgres schema.

Postgres rejects a value longer than its ``varchar(n)`` column; a dict does not. Running the
same check on in-memory writes makes that class of bug fail in unit tests instead of in
production (a 23-character coverage key once silently truncated a whole evening slot).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import String


def column_limits(row_cls: Any) -> dict[str, int]:
    """``{column name: max length}`` for the string columns of an ORM class."""
    return {
        column.name: column.type.length
        for column in row_cls.__table__.columns
        if isinstance(column.type, String) and column.type.length
    }


def enforce_column_limits(row_cls: Any, values: Mapping[str, Any]) -> None:
    """Raise ``ValueError`` when a string value does not fit its column (as Postgres would)."""
    for name, limit in column_limits(row_cls).items():
        value = values.get(name)
        if isinstance(value, str) and len(value) > limit:
            raise ValueError(
                f"{row_cls.__tablename__}.{name} is varchar({limit}); "
                f"value of length {len(value)} would not fit: {value!r}"
            )
