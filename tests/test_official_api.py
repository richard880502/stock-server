from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application.official_queries import MarketRiskFlagService, StockEventsService
from quant_signal.domain.models import MarketEvent, MonthlyRevenue, RiskFlag
from quant_signal.infrastructure.memory import MemoryQuantRepository

TAIPEI = ZoneInfo("Asia/Taipei")
FETCHED = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)


def material(key: str, title: str, *, at: datetime, announced: datetime, symbol="2330.TW"):
    return MarketEvent(
        symbol=symbol,
        kind="material",
        external_key=key,
        announced_at=announced,
        title=title,
        clause="第51款",
        source="twse_material",
        available_at=at,
    )


@pytest.fixture
async def repo() -> MemoryQuantRepository:
    repository = MemoryQuantRepository()
    await repository.upsert_market_events(
        [
            material(
                "1",
                "公告本公司董事會決議買回庫藏股",
                at=FETCHED,
                announced=datetime(2026, 11, 25, 13, 43, tzinfo=TAIPEI),
            ),
            material(
                "2",
                "公告本公司涉及專利侵權訴訟",
                at=FETCHED,
                announced=datetime(2026, 11, 20, 9, 0, tzinfo=TAIPEI),
            ),
            material(
                "3",
                "澄清媒體報導",
                at=LATER,
                announced=datetime(2026, 11, 26, 20, 0, tzinfo=TAIPEI),
            ),
            material(
                "4",
                "公告本公司董事長異動",
                at=FETCHED,
                announced=datetime(2026, 11, 25, 9, 0, tzinfo=TAIPEI),
                symbol="6488.TWO",
            ),
            MarketEvent(
                symbol="2330.TW",
                kind="ex_rights",
                external_key="2330.TW:2026-12-11:除息",
                announced_at=FETCHED,
                title="台積電 2026-12-11 除息預告：現金股利 5 元",
                clause="除息",
                effective_date=date(2026, 12, 11),
                source="twse_exrights",
                available_at=FETCHED,
            ),
        ]
    )
    await repository.upsert_risk_flags(
        [
            RiskFlag(
                symbol="2330.TW",
                kind="disposition",
                start_date=date(2026, 11, 24),
                end_date=date(2026, 12, 7),
                reason="連續三次",
                source="twse_punish",
                available_at=FETCHED,
            ),
            RiskFlag(
                symbol="6488.TWO",
                kind="attention",
                start_date=date(2026, 11, 25),
                end_date=date(2026, 11, 26),
                reason="漲幅過大",
                source="tpex_warning",
                available_at=FETCHED,
            ),
            RiskFlag(
                symbol="2317.TW",
                kind="disposition",
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 14),
                reason="已結束",
                source="twse_punish",
                available_at=FETCHED - timedelta(days=50),
            ),
        ]
    )
    rows = []
    for index in range(13):  # 2025-10 .. 2026-10
        year, month = divmod(9 + index, 12)
        rows.append(
            MonthlyRevenue(
                symbol="2330.TW",
                period=f"{2025 + year}-{month + 1:02d}",
                revenue=100 + index,
                yoy_pct=10 + index,
                mom_pct=2.0,
                cumulative_yoy_pct=15.0,
                source="twse_revenue",
                available_at=FETCHED,
            )
        )
    await repository.upsert_monthly_revenue(rows)
    return repository


async def test_stock_events_service_is_point_in_time(repo) -> None:
    service = StockEventsService(repo)
    result = await service.get("2330.TW", as_of=date(2026, 11, 26), days=30)

    assert [e.title for e in result.events if e.kind == "material"] == [
        "公告本公司董事會決議買回庫藏股",
        "公告本公司涉及專利侵權訴訟",
    ]  # "澄清媒體報導" was only fetched on the 27th
    by_title = {e.title: e for e in result.events}
    assert (
        by_title["公告本公司董事會決議買回庫藏股"].category,
        by_title["公告本公司董事會決議買回庫藏股"].polarity,
    ) == ("buyback", "positive")
    assert by_title["公告本公司涉及專利侵權訴訟"].polarity == "negative"
    assert any(
        e.kind == "ex_rights" and e.effective_date == date(2026, 12, 11) for e in result.events
    )
    assert [f.kind for f in result.risk_flags] == ["disposition"]
    assert result.revenue and result.revenue.period == "2026-10"
    assert result.revenue.consecutive_positive_yoy == 13 and result.revenue.is_12m_high is True

    later = await service.get("2330.TW", as_of=date(2026, 11, 27), days=30)
    assert "澄清媒體報導" in {e.title for e in later.events}

    before = await service.get("2330.TW", as_of=date(2026, 11, 24), days=30)
    assert before.events == [] and before.risk_flags == [] and before.revenue is None


async def test_days_window_limits_material_events(repo) -> None:
    result = await StockEventsService(repo).get("2330.TW", as_of=date(2026, 11, 26), days=3)
    assert [e.kind for e in result.events].count("material") == 1


async def test_market_risk_flags_lists_only_active_flags(repo) -> None:
    result = await MarketRiskFlagService(repo).get(as_of=date(2026, 11, 26))
    assert {f.symbol for f in result.flags} == {"2330.TW", "6488.TWO"}
    assert result.count == 2
    assert (await MarketRiskFlagService(repo).get(as_of=date(2026, 11, 27))).count == 1


@pytest.fixture
def client(repo):
    async def override():
        yield repo

    app.dependency_overrides[get_repository] = override
    app.dependency_overrides[require_principal] = lambda: Principal("anonymous")
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_http_stock_events(client) -> None:
    response = client.get("/api/v1/stocks/2330.TW/events", params={"as_of": "2026-11-26"})
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "2330.TW" and body["as_of"] == "2026-11-26" and body["days"] == 30
    assert {e["category"] for e in body["events"]} >= {"buyback", "litigation"}
    assert body["risk_flags"][0]["reason"] == "連續三次"
    assert body["revenue"]["yoy_pct"] == 22
    assert "累積" in body["note"]


def test_http_validation_and_unknown_stock(client) -> None:
    assert client.get("/api/v1/stocks/2330.TW/events", params={"days": 0}).status_code == 422
    assert client.get("/api/v1/stocks/2330.TW/events", params={"days": 400}).status_code == 422
    assert client.get("/api/v1/stocks/9999/events").status_code == 404  # bare code, not listed


def test_http_market_risk_flags(client) -> None:
    response = client.get("/api/v1/market/risk-flags", params={"as_of": "2026-11-26"})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert {f["kind"] for f in body["flags"]} == {"disposition", "attention"}
