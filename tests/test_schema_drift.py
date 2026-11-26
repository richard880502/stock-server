"""Fails when the ORM models and the migrated database disagree.

Skipped unless ``QUANT_TEST_DATABASE_URL`` points at a database that ran
``alembic upgrade head``. Compares every ORM table and column with the real schema
(existence, nullability, primary key) and lists migrated tables/columns no model knows.
"""

from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from quant_signal.infrastructure.db.models import Base

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")

# Tables that legitimately have no ORM model.
ALLOWED_EXTRA_TABLES = {"alembic_version"}
# Monthly partition children of minute_bars (created by the migration and ingest).
PARTITION_CHILD = re.compile(r"^minute_bars_\d{4}_\d{2}$")


def _reflect(sync_conn) -> dict[str, dict]:
    inspector = inspect(sync_conn)
    tables: dict[str, dict] = {}
    for name in inspector.get_table_names():
        tables[name] = {
            "columns": {c["name"]: c["nullable"] for c in inspector.get_columns(name)},
            "pk": set(inspector.get_pk_constraint(name)["constrained_columns"]),
        }
    return tables


def compare(database: dict[str, dict]) -> list[str]:
    problems: list[str] = []
    for table in Base.metadata.sorted_tables:
        actual = database.get(table.name)
        if actual is None:
            problems.append(f"table {table.name}: in ORM, missing from database")
            continue
        for column in table.columns:
            if column.name not in actual["columns"]:
                problems.append(f"{table.name}.{column.name}: in ORM, missing from database")
            elif actual["columns"][column.name] != column.nullable:
                problems.append(
                    f"{table.name}.{column.name}: nullable is {column.nullable} in ORM "
                    f"but {actual['columns'][column.name]} in database"
                )
        for name in sorted(actual["columns"].keys() - {c.name for c in table.columns}):
            problems.append(f"{table.name}.{name}: in database, missing from ORM")
        orm_pk = {c.name for c in table.primary_key.columns}
        if orm_pk != actual["pk"]:
            problems.append(
                f"table {table.name}: primary key is {sorted(orm_pk)} in ORM "
                f"but {sorted(actual['pk'])} in database"
            )
    known = set(Base.metadata.tables) | ALLOWED_EXTRA_TABLES
    for name in sorted(database.keys() - known):
        if not PARTITION_CHILD.fullmatch(name):
            problems.append(f"table {name}: in database, no ORM model and not allow-listed")
    return problems


async def test_orm_models_match_migrated_schema() -> None:
    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    try:
        async with engine.connect() as connection:
            database = await connection.run_sync(_reflect)
    finally:
        await engine.dispose()
    problems = compare(database)
    assert not problems, "ORM and migrations drifted:\n  " + "\n  ".join(problems)
