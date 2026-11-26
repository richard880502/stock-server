from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from quant_signal.domain.models import MarketEvent, MonthlyRevenue, RiskFlag
from quant_signal.infrastructure.db.models import Base
from quant_signal.infrastructure.memory import MemoryQuantRepository

D1 = datetime(2026, 11, 10, 2, 0, tzinfo=UTC)
D2 = D1 + timedelta(days=5)
D3 = D1 + timedelta(days=10)


def event(key="k1", *, at=D1, title="公告本公司董事會決議買回庫藏股", symbol="2330.TW", **kw):
    return MarketEvent(
        symbol=symbol,
        kind=kw.pop("kind", "material"),
        external_key=key,
        announced_at=kw.pop("announced_at", at),
        title=title,
        source="twse_material",
        available_at=at,
        **kw,
    )


def flag(start=date(2026, 11, 9), end=date(2026, 11, 13), *, at=D1, symbol="2330.TW", **kw):
    return RiskFlag(
        symbol=symbol,
        kind=kw.pop("kind", "disposition"),
        start_date=start,
        end_date=end,
        reason=kw.pop("reason", "連續三次"),
        source="twse_punish",
        available_at=at,
    )


def revenue(period="2026-10", value=1000.0, *, at=D1, symbol="2330.TW", yoy=10.0):
    return MonthlyRevenue(
        symbol=symbol,
        period=period,
        revenue=value,
        yoy_pct=yoy,
        source="twse_revenue",
        available_at=at,
    )


# --- idempotency and revisions ------------------------------------------------------


async def test_event_upsert_is_idempotent() -> None:
    repo = MemoryQuantRepository()
    assert await repo.upsert_market_events([event()]) == 1
    assert await repo.upsert_market_events([event(at=D2)]) == 0  # same content, later fetch
    assert len(repo.market_events) == 1
    assert repo.market_events[0].available_at == D1  # first knowledge is preserved


async def test_changed_revenue_becomes_a_new_revision() -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_monthly_revenue([revenue(value=1000, at=D1)])
    await repo.upsert_monthly_revenue([revenue(value=1000, at=D2)])  # unchanged: no-op
    assert await repo.upsert_monthly_revenue([revenue(value=1100, at=D3)]) == 1
    assert sorted(r.revision for r in repo.revenue_rows) == [1, 2]


async def test_revenue_pit_reads_see_the_revision_known_on_that_day() -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_monthly_revenue([revenue(value=1000, at=D1)])
    await repo.upsert_monthly_revenue([revenue(value=1100, at=D3)])

    assert await repo.monthly_revenue("2330.TW", D1 - timedelta(seconds=1)) == []
    assert (await repo.monthly_revenue("2330.TW", D2))[0].revenue == 1000
    assert (await repo.monthly_revenue("2330.TW", D3))[0].revenue == 1100


async def test_monthly_revenue_returns_trailing_months_oldest_first() -> None:
    repo = MemoryQuantRepository()
    periods = [f"2025-{m:02d}" for m in range(1, 13)] + ["2026-01", "2026-02"]
    await repo.upsert_monthly_revenue([revenue(p) for p in periods])
    rows = await repo.monthly_revenue("2330.TW", D3, months=13)
    assert [r.period for r in rows] == periods[-13:]


async def test_latest_monthly_revenue_is_one_row_per_company() -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_monthly_revenue(
        [
            revenue("2026-09", symbol="2330.TW"),
            revenue("2026-10", symbol="2330.TW"),
            revenue("2026-10", symbol="6488.TWO"),
            revenue("2026-10", symbol="2317.TW", at=D3),  # fetched later than the cutoff
        ]
    )
    rows = await repo.latest_monthly_revenue(D2)
    assert [(r.symbol, r.period) for r in rows] == [("2330.TW", "2026-10"), ("6488.TWO", "2026-10")]
    assert len(await repo.latest_monthly_revenue(D3)) == 3


async def test_risk_flag_extension_is_a_revision_and_pit_visible() -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_risk_flags([flag(end=date(2026, 11, 13), at=D1)])
    assert await repo.upsert_risk_flags([flag(end=date(2026, 11, 27), at=D3)]) == 1

    on_14th_known_early = await repo.active_risk_flags(datetime(2026, 11, 14, 5, tzinfo=UTC))
    assert on_14th_known_early == []  # as we knew it then, the flag had ended on the 13th
    late = await repo.active_risk_flags(datetime(2026, 11, 20, 5, tzinfo=UTC))
    assert [f.end_date for f in late] == [date(2026, 11, 27)]


async def test_active_risk_flags_window_and_symbol_filter() -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_risk_flags(
        [
            flag(date(2026, 11, 9), date(2026, 11, 13), symbol="2330.TW"),
            flag(date(2026, 11, 9), None, symbol="6488.TWO", kind="attention"),
            flag(date(2026, 11, 16), date(2026, 11, 20), symbol="2317.TW"),  # not started yet
        ]
    )
    at = datetime(2026, 11, 12, 6, tzinfo=UTC)
    assert {f.symbol for f in await repo.active_risk_flags(at)} == {"2330.TW", "6488.TWO"}
    assert [f.symbol for f in await repo.active_risk_flags(at, ["6488.TWO"])] == ["6488.TWO"]
    assert await repo.active_risk_flags(at, []) == []
    # The end date is inclusive in Taipei time: 23:00 on the 13th is in, 00:30 on the 14th is out.
    in_window = await repo.active_risk_flags(datetime(2026, 11, 13, 15, 0, tzinfo=UTC))
    assert "2330.TW" in {f.symbol for f in in_window}
    after = await repo.active_risk_flags(datetime(2026, 11, 13, 16, 30, tzinfo=UTC))
    assert "2330.TW" not in {f.symbol for f in after}


async def test_list_market_events_pit_window_and_ex_rights_schedule() -> None:
    repo = MemoryQuantRepository()
    ex = event(
        "2330.TW:2026-12-20:除息",
        kind="ex_rights",
        at=D1 - timedelta(days=60),
        effective_date=date(2026, 12, 20),
    )
    await repo.upsert_market_events(
        [
            event("a", at=D1, announced_at=D1 - timedelta(hours=1)),
            event("old", at=D1 - timedelta(days=90), title="舊訊息"),
            event("late", at=D3, announced_at=D1 + timedelta(days=1)),  # fetched after cutoff
            event("other", symbol="6488.TWO", at=D1),
            ex,
        ]
    )
    since = D2 - timedelta(days=30)
    visible = await repo.list_market_events(["2330.TW"], since, D2)
    assert {e.external_key for e in visible} == {"a", "2330.TW:2026-12-20:除息"}
    assert {e.external_key for e in await repo.list_market_events(None, since, D2)} >= {"other"}
    # After the late fetch it becomes visible; before it, it is not.
    assert "late" in {e.external_key for e in await repo.list_market_events(["2330.TW"], since, D3)}


async def test_changed_ex_rights_keeps_first_seen_announced_at() -> None:
    repo = MemoryQuantRepository()
    key = "2330.TW:2026-12-20:除息"
    first = event(key, kind="ex_rights", at=D1, effective_date=date(2026, 12, 20), title="v1")
    second = event(key, kind="ex_rights", at=D3, effective_date=date(2026, 12, 20), title="v2")
    await repo.upsert_market_events([first])
    assert await repo.upsert_market_events([second]) == 1
    latest = max(repo.market_events, key=lambda e: e.revision)
    assert latest.revision == 2 and latest.announced_at == D1 and latest.title == "v2"


# --- migration mirrors the ORM -------------------------------------------------------

MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0015_market_events.py"
TABLES = ("market_events", "market_risk_flags", "monthly_revenue")


class _Recorder:
    def __init__(self) -> None:
        self.tables: dict[str, list] = {}
        self.indexes: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.dropped_tables: list[str] = []

    def create_table(self, name, *items):
        self.tables[name] = list(items)

    def create_index(self, name, table, columns):
        self.indexes[name] = (table, tuple(columns))

    def drop_index(self, name, table_name):
        self.indexes.pop(name, None)

    def drop_table(self, name):
        self.dropped_tables.append(name)


@pytest.fixture
def migration():
    spec = importlib.util.spec_from_file_location("migration_0015", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recorder = _Recorder()
    module.op = recorder
    return module, recorder


def test_migration_chain_ids(migration) -> None:
    module, _ = migration
    assert module.revision == "0015_market_events"
    assert module.down_revision == "0014_news_available_at"


def test_migration_matches_orm_tables(migration) -> None:
    module, recorder = migration
    module.upgrade()
    assert set(recorder.tables) == set(TABLES)
    for name in TABLES:
        orm = Base.metadata.tables[name]
        items = recorder.tables[name]
        columns = {item.name: item for item in items if isinstance(item, sa.Column)}
        assert set(columns) == {c.name for c in orm.columns}, name
        for column in orm.columns:
            mine = columns[column.name]
            assert type(mine.type) is type(column.type), (name, column.name)
            assert mine.nullable == column.nullable or column.primary_key, (name, column.name)
            if isinstance(column.type, sa.String):
                assert mine.type.length == column.type.length, (name, column.name)
        pk = next(i for i in items if isinstance(i, sa.PrimaryKeyConstraint))
        assert list(pk._pending_colargs) == [c.name for c in orm.primary_key.columns], name
    orm_indexes = {
        index.name: (table.name, tuple(c.name for c in index.columns))
        for table in (Base.metadata.tables[n] for n in TABLES)
        for index in table.indexes
    }
    assert recorder.indexes == orm_indexes


def test_migration_downgrade_drops_everything(migration) -> None:
    module, recorder = migration
    module.upgrade()
    module.downgrade()
    assert set(recorder.dropped_tables) == set(TABLES)
    assert recorder.indexes == {}


# --- the Postgres statements at least compile (no database in unit tests) -----------


class _Result:
    rowcount = 0

    def scalars(self):
        return self

    def all(self):
        return []

    def __iter__(self):
        return iter(())


class _StubSession:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, statement):
        from sqlalchemy.dialects import postgresql

        self.sql.append(str(statement.compile(dialect=postgresql.dialect())))
        return _Result()

    async def commit(self) -> None:
        pass


async def test_postgres_statements_compile_with_point_in_time_filters() -> None:
    from quant_signal.infrastructure.db.repository import PostgresQuantRepository

    session = _StubSession()
    repo = PostgresQuantRepository(session)  # type: ignore[arg-type]
    await repo.list_market_events(["2330.TW"], D1, D2)
    await repo.active_risk_flags(D2, ["2330.TW"])
    await repo.monthly_revenue("2330.TW", D2)
    await repo.latest_monthly_revenue(D2)
    await repo.upsert_market_events([event()])
    await repo.upsert_risk_flags([flag()])
    await repo.upsert_monthly_revenue([revenue()])

    reads = session.sql[:4]
    assert all("available_at <=" in sql and "row_number()" in sql for sql in reads)
    assert any("ON CONFLICT DO NOTHING" in sql for sql in session.sql)
