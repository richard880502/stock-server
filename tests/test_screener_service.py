"""Snapshot job, read service, REST routes and the worker hook (in-memory repository)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.application import sync_jobs
from quant_signal.application.data_sync import DataSyncReport
from quant_signal.application.screener import (
    ScreenerQueryError,
    ScreenerService,
    ScreenerSnapshotService,
    ScreenFilter,
    analyst_view,
    refresh_screener,
)
from quant_signal.application.sync_jobs import process_next_job
from quant_signal.domain.models import (
    MarketIndustry,
    MarketStockDay,
    MonthlyRevenue,
    RiskFlag,
    SyncJobKind,
)
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.quant.screener import VERSION

SESSIONS = 40


def _weekdays(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


LAST = date(2026, 11, 27)  # a Friday
DAYS = _weekdays(LAST, SESSIONS)


def _stock(
    symbol: str,
    *,
    drift: float = 0.0,
    net: int | None = 0,
    margin_step: int | None = None,
    turnover: float = 5e8,
    days: list[date] | None = None,
    net_from: date | None = None,
) -> list[MarketStockDay]:
    rows: list[MarketStockDay] = []
    previous = 100.0
    margin = 10_000
    for day in days or DAYS:
        close = round(previous * (1 + drift), 4)
        has_net = net is not None and (net_from is None or day >= net_from)
        rows.append(
            MarketStockDay(
                symbol=symbol,
                name=f"股{symbol[:4]}",
                market="TWO" if symbol.endswith(".TWO") else "TW",
                trading_date=day,
                open=close,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                volume=1_000_000,
                turnover_value=turnover,
                change=close - previous,
                foreign_net=net if has_net else None,
                trust_net=net if has_net else None,
                dealer_net=0 if has_net else None,
                total_net=(net * 2 if net else net) if has_net else None,
                margin_balance=margin if margin_step is not None else None,
                margin_change=margin_step,
                short_balance=500 if margin_step is not None else None,
                short_change=0 if margin_step is not None else None,
                available_at=datetime.combine(day, time(10), tzinfo=UTC),
                source="twse_official",
            )
        )
        margin += margin_step or 0
        previous = close
    return rows


@pytest.fixture
async def repository() -> MemoryQuantRepository:
    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days(
        [
            *_stock("1101.TW", drift=0.004, net=200_000, margin_step=-20),  # accumulating
            *_stock("2330.TW", drift=0.002, net=50_000, margin_step=10, turnover=9e10),
            *_stock("2412.TW", drift=-0.002, net=-80_000, margin_step=30),
            *_stock("3008.TW", drift=0.0, net=None),  # no institutional report at all
            *_stock("6488.TWO", drift=0.003, net=100_000, margin_step=-5),
            *_stock("8299.TWO", drift=0.001, net=10_000, turnover=1e6),  # illiquid
        ]
    )
    await repo.upsert_market_industries(
        [
            MarketIndustry(
                symbol=symbol,
                market="TWO" if symbol.endswith("TWO") else "TW",
                industry_code=code,
                industry_name=name,
                source="test",
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            for symbol, code, name in [
                ("1101.TW", "01", "水泥工業"),
                ("2330.TW", "24", "半導體業"),
                ("2412.TW", "26", "通信網路業"),
                ("6488.TWO", "24", "半導體業"),
            ]
        ]
    )
    await repo.upsert_monthly_revenue(
        [
            MonthlyRevenue(
                symbol="1101.TW",
                period="2026-10",
                revenue=100.0,
                yoy_pct=35.2,
                mom_pct=3.0,
                cumulative_yoy_pct=20.0,
                source="test",
                available_at=datetime(2026, 11, 10, tzinfo=UTC),
            ),
            MonthlyRevenue(
                symbol="2330.TW",
                period="2026-10",
                revenue=100.0,
                yoy_pct=10.0,
                mom_pct=1.0,
                cumulative_yoy_pct=12.0,
                source="test",
                available_at=datetime(2026, 11, 10, tzinfo=UTC),
            ),
        ]
    )
    await repo.upsert_risk_flags(
        [
            RiskFlag(
                symbol="2412.TW",
                kind="disposition",
                start_date=LAST - timedelta(days=3),
                end_date=LAST + timedelta(days=5),
                reason="test",
                source="test",
                available_at=datetime(2026, 11, 1, tzinfo=UTC),
            )
        ]
    )
    return repo


def _report() -> DataSyncReport:
    now = datetime(2026, 11, 27, 8, tzinfo=UTC)
    return DataSyncReport(
        provider="test",
        started_at=now,
        completed_at=now,
        requested_start=LAST,
        requested_end=LAST,
        bars_received=0,
        bars_inserted=0,
    )


def _snapshots(repo: MemoryQuantRepository, **kwargs) -> ScreenerSnapshotService:
    return ScreenerSnapshotService(repo, **kwargs)


async def _computed(repo: MemoryQuantRepository) -> MemoryQuantRepository:
    await _snapshots(repo).compute(LAST)
    return repo


# ------------------------------------------------------------------- snapshot job


async def test_compute_writes_one_row_per_traded_stock_with_industry_flags_and_revenue(
    repository,
) -> None:
    written = await _snapshots(repository).compute(LAST)
    rows = {r.symbol: r for r in await repository.list_market_factors(LAST, VERSION)}

    assert written == len(rows) == 6
    assert rows["2330.TW"].industry_name == "半導體業"
    assert rows["8299.TWO"].industry_code is None
    assert rows["2412.TW"].flags == ["disposition"] and "處置股" in rows["2412.TW"].highlights
    assert rows["1101.TW"].metrics["revenue_yoy_pct"] == 35.2
    assert rows["1101.TW"].metrics["revenue_period"] == "2026-10"
    assert rows["3008.TW"].metrics["revenue_yoy_pct"] is None
    assert rows["1101.TW"].flow_through == LAST
    assert rows["3008.TW"].flow_through == LAST  # market-level: other TW stocks reported
    assert rows["1101.TW"].avg_turnover_20d == pytest.approx(5e8)
    assert rows["1101.TW"].change_pct == pytest.approx(0.4, abs=1e-3)


async def test_missing_reports_are_none_not_zero(repository) -> None:
    await _snapshots(repository).compute(LAST)
    rows = {r.symbol: r for r in await repository.list_market_factors(LAST, VERSION)}
    quiet = rows["3008.TW"]
    assert quiet.metrics["total_net_20d"] is None
    assert quiet.metrics["margin_change_20d_pct"] is None
    assert quiet.scores["flow"] is None
    assert quiet.metrics["ret_60d"] is None  # only 40 sessions of history


async def test_scores_rank_the_accumulating_stock_above_the_selling_one(repository) -> None:
    await _snapshots(repository).compute(LAST)
    rows = {r.symbol: r for r in await repository.list_market_factors(LAST, VERSION)}
    assert rows["1101.TW"].scores["flow"] > rows["2330.TW"].scores["flow"]
    assert rows["2330.TW"].scores["flow"] > rows["2412.TW"].scores["flow"]
    assert rows["1101.TW"].composite > rows["2412.TW"].composite
    assert rows["1101.TW"].metrics["chip_transfer_20d"] is True
    assert rows["2412.TW"].metrics["chip_transfer_20d"] is False
    assert "法人買、融資退（籌碼轉強）" in rows["1101.TW"].highlights


async def test_compute_is_idempotent_and_replaces_the_date(repository) -> None:
    service = _snapshots(repository)
    await service.compute(LAST)
    first = {r.symbol: r.composite for r in await repository.list_market_factors(LAST, VERSION)}
    await service.compute(LAST)
    second = await repository.list_market_factors(LAST, VERSION)
    assert len(second) == 6
    assert {r.symbol: r.composite for r in second} == first


async def test_snapshot_for_a_date_uses_only_history_up_to_that_date(repository) -> None:
    earlier = DAYS[-6]
    await _snapshots(repository).compute(earlier)
    rows = {r.symbol: r for r in await repository.list_market_factors(earlier, VERSION)}
    assert rows["1101.TW"].metrics["sessions"] == SESSIONS - 5
    assert rows["1101.TW"].trading_date == earlier
    assert rows["1101.TW"].close < 100 * 1.004 ** (SESSIONS - 4)  # not the later close


async def test_stock_not_traded_on_the_date_or_too_young_is_left_out(repository) -> None:
    await repository.upsert_market_stock_days(_stock("9999.TW", days=DAYS[-10:]))  # 10 sessions
    await repository.upsert_market_stock_days(_stock("9998.TW", days=DAYS[:-1]))  # delisted
    await _snapshots(repository).compute(LAST)
    symbols = {r.symbol for r in await repository.list_market_factors(LAST, VERSION)}
    assert "9999.TW" not in symbols and "9998.TW" not in symbols


async def test_flow_through_is_the_newest_reported_date_per_market(repository) -> None:
    # TW: report missing for the last session
    rows = [
        r.model_copy(update={"foreign_net": None, "trust_net": None, "total_net": None})
        if r.trading_date == LAST
        else r
        for r in list(repository.market_stock_days.values())
        if r.market == "TW"
    ]
    await repository.upsert_market_stock_days(rows)
    await _snapshots(repository).compute(LAST)
    by_symbol = {r.symbol: r for r in await repository.list_market_factors(LAST, VERSION)}
    assert by_symbol["1101.TW"].flow_through == DAYS[-2]
    assert by_symbol["6488.TWO"].flow_through == LAST
    # the window ends on the last reported session
    assert by_symbol["1101.TW"].metrics["foreign_streak"] == SESSIONS - 1


async def test_retention_prunes_old_dates(repository) -> None:
    service = _snapshots(repository, retention_sessions=2)
    for day in DAYS[-3:]:
        await service.compute(day)
    kept = sorted({d for (_, d) in repository.market_factors})
    assert kept == DAYS[-2:]


async def test_refresh_backfills_first_then_only_new_dates(repository) -> None:
    service = _snapshots(repository, backfill_sessions=3)
    assert await service.refresh() == DAYS[-3:]
    # nothing new: just rebuild the newest date (later reports may have arrived)
    assert await service.refresh() == [LAST]
    assert await service.refresh(recompute_latest=False) == []

    repository.market_factors.pop((VERSION, LAST))
    assert await service.refresh(recompute_latest=False) == [LAST]


async def test_refresh_on_an_empty_database_does_nothing() -> None:
    assert await _snapshots(MemoryQuantRepository()).refresh() == []


async def test_refresh_screener_swallows_failures_and_honours_the_switch(
    repository, monkeypatch
) -> None:
    assert (
        await refresh_screener(
            repository, enabled=False, retention_sessions=60, backfill_sessions=5
        )
        == []
    )

    async def boom(self, trading_date):
        raise RuntimeError("db down")

    monkeypatch.setattr(ScreenerSnapshotService, "compute", boom)
    assert (
        await refresh_screener(repository, enabled=True, retention_sessions=60, backfill_sessions=5)
        == []
    )


# ------------------------------------------------------------------- worker hook


async def test_market_job_completion_triggers_the_hook_only_after_the_last_job(
    monkeypatch,
) -> None:
    async def fake_job(job, repository, twse, tpex, *, today=None):
        return _report()

    monkeypatch.setattr(sync_jobs, "run_sync_job", fake_job)
    repo = MemoryQuantRepository()
    await repo.enqueue_sync_job(SyncJobKind.MARKET, "TW", lookback_days=7)
    await repo.enqueue_sync_job(SyncJobKind.MARKET, "TWO", lookback_days=7)
    calls: list[int] = []

    async def hook(repository) -> None:
        calls.append(1)

    outcomes = [
        await process_next_job(repo, None, None, max_attempts=3, after_market_sync=hook)  # type: ignore[arg-type]
        for _ in range(2)
    ]
    assert outcomes == [True, True]
    assert calls == [1]


async def test_a_failing_hook_never_fails_the_sync_job(monkeypatch) -> None:
    async def fake_job(job, repository, twse, tpex, *, today=None):
        return _report()

    monkeypatch.setattr(sync_jobs, "run_sync_job", fake_job)
    repo = MemoryQuantRepository()
    job, _ = await repo.enqueue_sync_job(SyncJobKind.MARKET, "TW", lookback_days=7)

    async def hook(repository) -> None:
        raise RuntimeError("factor failure")

    outcome = await process_next_job(
        repo,
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        max_attempts=3,
        after_market_sync=hook,
    )
    assert outcome is True
    assert repo.sync_jobs[job.id].status.value == "completed"


async def test_symbol_jobs_do_not_trigger_the_hook(monkeypatch) -> None:
    async def fake_job(job, repository, twse, tpex, *, today=None):
        return _report()

    monkeypatch.setattr(sync_jobs, "run_sync_job", fake_job)
    repo = MemoryQuantRepository()
    await repo.enqueue_sync_job(SyncJobKind.SYMBOL, "2330.TW")
    calls: list[int] = []

    async def hook(repository) -> None:
        calls.append(1)

    await process_next_job(repo, None, None, max_attempts=3, after_market_sync=hook)  # type: ignore[arg-type]
    assert calls == []


# ------------------------------------------------------------------ read service


async def test_screen_sorts_filters_and_paginates(repository) -> None:
    await _computed(repository)
    service = ScreenerService(repository)

    everything = await service.screen(exclude_risk=False)
    assert everything.universe == 6 and everything.matched == 6
    assert everything.data_through == LAST and everything.snapshot_pending is False
    assert [e.rank for e in everything.entries] == [1, 2, 3, 4, 5, 6]
    composites = [e.composite for e in everything.entries if e.composite is not None]
    assert composites == sorted(composites, reverse=True)

    default = await service.screen()
    assert "2412.TW" not in {e.symbol for e in default.entries}  # disposition excluded

    tw = await service.screen(market="TWO")
    assert {e.market for e in tw.entries} == {"TWO"} and tw.universe == 2

    page = await service.screen(limit=2, offset=1, exclude_risk=False)
    assert [e.rank for e in page.entries] == [2, 3] and page.matched == 6

    ascending = await service.screen(sort="flow", order="asc", exclude_risk=False)
    flows = [e.scores["flow"] for e in ascending.entries if e.scores["flow"] is not None]
    assert flows == sorted(flows)
    # stocks without the sort value go last whatever the direction
    assert ascending.entries[-1].scores["flow"] is None


async def test_bounds_flags_industry_and_text_filters(repository) -> None:
    await _computed(repository)
    service = ScreenerService(repository)

    liquid = await service.screen(
        filters=[ScreenFilter(key="avg_turnover_20d", min=1e8)], exclude_risk=False
    )
    assert "8299.TWO" not in {e.symbol for e in liquid.entries}

    revenue = await service.screen(filters=[ScreenFilter(key="revenue_yoy_pct", min=20)])
    assert [e.symbol for e in revenue.entries] == ["1101.TW"]

    ranged = await service.screen(filters=[ScreenFilter(key="ret_20d", min=-100, max=0.01)])
    assert all(e.metrics["ret_20d"] <= 0.01 for e in ranged.entries)  # None never matches

    chips = await service.screen(flags=["chip_transfer_20d"])
    assert {e.symbol for e in chips.entries} == {"1101.TW", "6488.TWO"}

    semis = await service.screen(industries=["24"])
    assert {e.symbol for e in semis.entries} == {"2330.TW", "6488.TWO"}

    named = await service.screen(q="2330")
    assert [e.symbol for e in named.entries] == ["2330.TW"]


async def test_presets_apply_their_filters_and_explicit_params_override(repository) -> None:
    await _computed(repository)
    service = ScreenerService(repository)

    money = await service.screen(preset="money_flow")
    assert money.preset == "money_flow" and money.sort == "flow"
    assert "8299.TWO" not in {e.symbol for e in money.entries}  # liquidity floor 2e7
    assert all(e.metrics["inst_flow_ratio_20d"] > 0 for e in money.entries)

    # an explicit bound for the same key replaces the preset's own
    relaxed = await service.screen(
        preset="money_flow", filters=[ScreenFilter(key="avg_turnover_20d", min=0)]
    )
    assert "8299.TWO" in {e.symbol for e in relaxed.entries}
    resorted = await service.screen(preset="money_flow", sort="composite", order="asc")
    assert resorted.sort == "composite" and resorted.order == "asc"

    revenue = await service.screen(preset="revenue_growth")
    assert [e.symbol for e in revenue.entries] == ["1101.TW"]
    transfer = await service.screen(preset="chip_transfer")
    assert {e.symbol for e in transfer.entries} == {"1101.TW", "6488.TWO"}


async def test_unknown_keys_are_rejected(repository) -> None:
    await _computed(repository)
    service = ScreenerService(repository)
    for kwargs in (
        {"preset": "nope"},
        {"sort": "nope"},
        {"filters": [ScreenFilter(key="nope", min=1)]},
        {"flags": ["ret_20d"]},
    ):
        with pytest.raises(ScreenerQueryError):
            await service.screen(**kwargs)


async def test_no_snapshot_reports_pending_with_no_entries() -> None:
    result = await ScreenerService(MemoryQuantRepository()).screen()
    assert result.snapshot_pending is True and result.data_through is None
    assert result.entries == [] and result.universe == 0
    factors = await ScreenerService(MemoryQuantRepository()).stock("2330.TW")
    assert factors.entry is None and factors.data_through is None


async def test_as_of_picks_the_latest_snapshot_on_or_before_it(repository) -> None:
    service = _snapshots(repository)
    await service.compute(DAYS[-3])
    await service.compute(LAST)
    reader = ScreenerService(repository)
    assert (await reader.screen(as_of=DAYS[-2])).data_through == DAYS[-3]
    assert (await reader.screen()).data_through == LAST
    assert (await reader.screen(as_of=DAYS[0])).snapshot_pending is True


async def test_stock_factors_returns_the_entry_or_null(repository) -> None:
    await _computed(repository)
    service = ScreenerService(repository)
    found = await service.stock("2330")
    assert found.symbol == "2330.TW" and found.entry is not None
    assert found.entry.symbol == "2330.TW" and found.universe == 6
    assert found.data_through == LAST and found.flow_through == LAST
    assert found.entry.rank is not None and found.entry.rank >= 1
    missing = await service.stock("0000.TW")
    assert missing.entry is None and missing.universe == 6


async def test_meta_lists_factors_metrics_presets_and_industries(repository) -> None:
    await _computed(repository)
    meta = await ScreenerService(repository).meta()
    assert meta["version"] == VERSION
    factors = {f["key"]: f for f in meta["factors"]}
    assert set(factors) == {
        "composite",
        "momentum",
        "trend",
        "flow",
        "accumulation",
        "fundamental",
        "stability",
    }
    assert factors["flow"]["inputs"][0] == {"key": "inst_flow_ratio_20d", "weight": 0.3}
    assert factors["flow"]["kind"] == "score" and factors["flow"]["group"] == "score"
    keys = {m["key"] for m in meta["metrics"]}
    assert {"inst_flow_ratio_20d", "trend_aligned", "cmf_20", "revenue_yoy_pct"} <= keys
    assert [g["key"] for g in meta["groups"]][0] == "score"
    presets = {p["key"]: p for p in meta["presets"]}
    assert list(presets) == [
        "balanced",
        "money_flow",
        "quiet_accumulation",
        "momentum",
        "revenue_growth",
        "chip_transfer",
        "stable",
    ]
    assert presets["balanced"]["filters"] == [{"key": "avg_turnover_20d", "min": 20000000}]
    for preset in presets.values():
        for column in preset["columns"]:
            assert column in keys or column in factors
        assert preset["sort"] in keys | set(factors)
    assert {"code": "24", "name": "半導體業", "count": 2} in meta["industries"]


async def test_analyst_view_trims_to_scores_highlights_and_chosen_metrics(repository) -> None:
    await _computed(repository)
    result = await ScreenerService(repository).screen(preset="money_flow", limit=2)
    payload = analyst_view(result, ["total_net_20d", "not_a_metric"])
    entry = payload["entries"][0]
    assert set(entry["metrics"]) == {"total_net_20d"}
    assert {"scores", "highlights", "composite", "symbol"} <= set(entry)
    assert "close" in entry and "ma20_gap_pct" not in entry["metrics"]
    assert "非投資建議" in payload["note"]


# --------------------------------------------------------------------------- REST


@pytest.fixture
def client(repository):
    async def override():
        return repository

    app.dependency_overrides[get_repository] = override
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


async def test_rest_routes_return_the_contract_shapes(repository, client) -> None:
    await _computed(repository)

    meta = client.get("/api/v1/market/screener/meta")
    assert meta.status_code == 200
    assert {"version", "factors", "metrics", "groups", "presets", "industries"} <= set(meta.json())

    response = client.get(
        "/api/v1/market/screener",
        params=[
            ("preset", "money_flow"),
            ("f", "ret_20d::"),
            ("f", "revenue_yoy_pct:20:"),
            ("limit", "5"),
        ],
    )
    assert response.status_code == 200
    body = response.json()
    assert {
        "version",
        "market",
        "as_of",
        "data_through",
        "flow_through",
        "preset",
        "sort",
        "order",
        "universe",
        "matched",
        "limit",
        "offset",
        "snapshot_pending",
        "entries",
    } <= set(body)
    assert body["data_through"] == LAST.isoformat() and body["preset"] == "money_flow"
    assert [e["symbol"] for e in body["entries"]] == ["1101.TW"]
    entry = body["entries"][0]
    assert {
        "rank",
        "symbol",
        "name",
        "market",
        "industry_code",
        "industry_name",
        "close",
        "change_pct",
        "avg_turnover_20d",
        "composite",
        "scores",
        "metrics",
        "highlights",
        "flags",
    } == set(entry)
    assert set(entry["scores"]) == {
        "momentum",
        "trend",
        "flow",
        "accumulation",
        "fundamental",
        "stability",
    }

    ranged = client.get(
        "/api/v1/market/screener",
        params=[("f", "ret_20d:-100:100"), ("flag", "chip_transfer_20d"), ("industry", "24")],
    )
    assert [e["symbol"] for e in ranged.json()["entries"]] == ["6488.TWO"]

    single = client.get("/api/v1/stocks/2330.TW/factors")
    assert single.status_code == 200
    assert single.json()["entry"]["symbol"] == "2330.TW"
    assert {"universe", "data_through", "flow_through"} <= set(single.json())
    assert client.get("/api/v1/stocks/0000.TW/factors").json()["entry"] is None


async def test_rest_rejects_bad_input_with_422(repository, client) -> None:
    await _computed(repository)
    for params in (
        {"sort": "nope"},
        {"f": "nope:1:2"},
        {"f": "ret_20d:abc:2"},
        {"f": "ret_20d"},
        {"flag": "ret_20d"},
        {"preset": "nope"},
        {"limit": "500"},
        {"market": "XX"},
    ):
        assert client.get("/api/v1/market/screener", params=params).status_code == 422, params


def test_rest_before_any_snapshot_is_pending(client) -> None:
    body = client.get("/api/v1/market/screener").json()
    assert body["snapshot_pending"] is True and body["entries"] == []
    assert body["data_through"] is None


# -------------------------------------------------------------------- migration


def test_migration_0016_matches_the_orm_table() -> None:
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa

    from quant_signal.infrastructure.db.models import Base

    path = Path(__file__).parent.parent / "migrations/versions/0016_market_factor_daily.py"
    spec = importlib.util.spec_from_file_location("migration_0016", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    assert module.revision == "0016_market_factor_daily"
    assert module.down_revision == "0015_market_events"

    created: dict[str, list] = {}
    indexes: dict[str, tuple] = {}

    class Recorder:
        def create_table(self, name, *items):
            created[name] = list(items)

        def create_index(self, name, table, columns):
            indexes[name] = (table, tuple(columns))

    module.op = Recorder()
    module.upgrade()
    orm = Base.metadata.tables["market_factor_daily"]
    columns = {i.name: i for i in created["market_factor_daily"] if isinstance(i, sa.Column)}
    assert set(columns) == {c.name for c in orm.columns}
    for column in orm.columns:
        assert type(columns[column.name].type) is type(column.type), column.name
        assert columns[column.name].nullable == column.nullable or column.primary_key
    pk = next(i for i in created["market_factor_daily"] if isinstance(i, sa.PrimaryKeyConstraint))
    assert list(pk._pending_colargs) == [c.name for c in orm.primary_key.columns]
    assert indexes == {
        index.name: ("market_factor_daily", tuple(c.name for c in index.columns))
        for index in orm.indexes
    }
