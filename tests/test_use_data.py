"""Official data in the screener (v2 metrics, scores, presets) and in the AI evidence."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from quant_signal.application.llm_analysis import (
    AnalysisContextService,
    DebateAnalysisService,
    prompt_context,
)
from quant_signal.application.official_evidence import BLOCK_KEYS, build_official_blocks
from quant_signal.application.screener import (
    PRESET_BY_KEY,
    ScreenerService,
    ScreenerSnapshotService,
    analyst_view,
    screener_meta_metrics,
)
from quant_signal.domain.derivatives import StockFuturesDay, VixDay
from quant_signal.domain.fundamentals import (
    EarningsMetrics,
    QuarterlyFinancial,
    RevenueTrend,
    ValuationDay,
    ValuationMetrics,
)
from quant_signal.domain.models import (
    ForeignHolding,
    MarketStockDay,
    MonthlyRevenue,
    ShareholdingDistribution,
)
from quant_signal.domain.short_data import (
    DayTradeDay,
    DayTradeMetrics,
    DividendEvent,
    InsiderHolding,
    InsiderPledge,
    NextEvent,
    ShortLendingDay,
    ShortLendingMetrics,
)
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.quant import screener as s
from quant_signal.quant.screener import VERSION

TAIPEI = ZoneInfo("Asia/Taipei")
LAST = date(2026, 11, 27)
EARLY = datetime(2026, 11, 27, 6, tzinfo=UTC)  # 14:00 Taipei, before the cutoff
LATE = datetime(2026, 11, 28, 6, tzinfo=UTC)  # published after the 11-27 cutoff


def _weekdays(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


DAYS = _weekdays(LAST, 40)


def _stock(symbol: str, drift: float = 0.002) -> list[MarketStockDay]:
    rows, previous = [], 100.0
    for day in DAYS:
        close = round(previous * (1 + drift), 4)
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
                turnover_value=5e8,
                change=close - previous,
                foreign_net=1000,
                trust_net=1000,
                dealer_net=0,
                total_net=2000,
                available_at=datetime.combine(day, time(10), tzinfo=UTC),
                source="twse_official",
            )
        )
        previous = close
    return rows


def _valuation(symbol: str, *, at: datetime | None = None) -> list[ValuationDay]:
    days = _weekdays(LAST, 70)
    return [
        ValuationDay(
            symbol=symbol,
            trading_date=day,
            source="twse_bwibbu",
            pe_ratio=10.0 + i * 0.1,  # newest is the cheapest ... no: rising, newest is richest
            pb_ratio=1.0 + i * 0.01,
            dividend_yield_pct=5.3,
            dividend_year=2025,
            fiscal_period="2026Q3",
            available_at=at or datetime.combine(day, time(11), tzinfo=UTC),
        )
        for i, day in enumerate(reversed(days))  # the newest day gets i = 0 (cheapest)
    ]


def _foreign(symbol: str, *, at: datetime = EARLY) -> ForeignHolding:
    return ForeignHolding(
        symbol=symbol,
        trading_date=LAST,
        shares_outstanding=100_000_000,
        foreign_shares=30_000_000,
        foreign_holding_pct=30.0,
        source="twse",
        available_at=at,
    )


async def _seed_full(repo: MemoryQuantRepository, symbol: str, *, at: datetime = EARLY) -> None:
    await repo.upsert_foreign_holding([_foreign(symbol, at=at)])
    await repo.upsert_valuation_daily(_valuation(symbol, at=None if at == EARLY else at))
    await repo.upsert_monthly_revenue(
        [
            MonthlyRevenue(
                symbol=symbol,
                period=f"2026-{month:02d}",
                revenue=100.0 + month,
                yoy_pct=10.0 + month,
                mom_pct=1.0,
                source="test",
                available_at=at,
            )
            for month in range(1, 11)
        ]
    )
    await repo.upsert_quarterly_financials(
        [
            QuarterlyFinancial(
                symbol=symbol,
                fiscal_year=year,
                fiscal_quarter=quarter,
                revenue=revenue,
                gross_profit=gross,
                operating_income=gross / 2,
                net_income_parent=gross / 4,
                eps=eps,
                equity_parent=500.0,
                total_assets=900.0,
                source="test",
                available_at=at,
            )
            for year, quarter, revenue, gross, eps in (
                (2025, 1, 90.0, 36.0, 0.8),
                (2025, 2, 190.0, 72.0, 1.6),
                (2025, 3, 290.0, 110.0, 2.4),
                (2025, 4, 400.0, 150.0, 3.2),
                (2026, 1, 100.0, 40.0, 1.0),
                (2026, 2, 210.0, 84.0, 2.2),
                (2026, 3, 330.0, 140.0, 3.6),
            )
        ]
    )
    sbl_days = _weekdays(LAST, 8)
    await repo.upsert_short_lending(
        [
            ShortLendingDay(
                symbol=symbol,
                trading_date=day,
                source="twse",
                sbl_sold=1000,
                sbl_balance=2_000_000 + i * 100_000,
                available_at=at,
            )
            for i, day in enumerate(sbl_days)
        ]
    )
    await repo.upsert_day_trade(
        [
            DayTradeDay(
                symbol=symbol,
                trading_date=day,
                source="twse",
                day_trade_volume=620_000,
                day_trade_buy_amount=1,
                day_trade_sell_amount=2,
                available_at=at,
            )
            for day in sbl_days
        ]
    )
    await repo.upsert_insider_holdings(
        [
            InsiderHolding(
                symbol=symbol,
                period="2026-10",
                source="twse",
                insider_count=5,
                holding_shares=1_000_000,
                pledged_shares=550_000,
                pledge_ratio_pct=55.0,
                available_at=at,
            )
        ]
    )
    await repo.upsert_dividend_events(
        [
            DividendEvent(
                symbol=symbol,
                ex_date=LAST + timedelta(days=5),
                kind="ex_dividend",
                source="twse",
                cash_dividend_per_share=2.5,
                available_at=at,
            )
        ]
    )
    await repo.upsert_stock_futures(
        [
            StockFuturesDay(
                symbol=symbol,
                trading_date=day,
                underlying_code="CDF",
                source="taifex",
                total_open_interest=1000.0 + i * 100,
                total_volume=50.0,
                open_interest_shares=None,
                volume_shares=0,
                near_month="202612",
                near_settlement=None,
                near_volume=None,
                near_open_interest=None,
                product_count=1,
                available_at=at,
            )
            for i, day in enumerate(_weekdays(LAST, 8))
        ]
    )
    await repo.upsert_vix_daily(
        [
            VixDay(
                trading_date=LAST,
                vix_close=18.5,
                vix_last_minute_avg=None,
                source="taifex",
                available_at=at,
            )
        ]
    )
    await repo.upsert_shareholding_distribution(
        [
            ShareholdingDistribution(
                symbol=symbol,
                data_date=day,
                pct_ge400=60.0 + step,
                pct_ge1000=40.0,
                total_shares=100_000_000,
                holders_ge400=50,
                holders_ge1000=10,
                retail_pct=10.0,
                retail_holders=1000,
                total_holders=2000,
                source="tdcc",
                available_at=at,
            )
            for step, day in enumerate([date(2026, 11, 13), date(2026, 11, 20)])
        ]
    )


@pytest.fixture
async def seeded() -> MemoryQuantRepository:
    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days(
        [*_stock("AAA.TW"), *_stock("BBB.TW", -0.001), *_stock("CCC.TWO")]
    )
    await _seed_full(repo, "AAA.TW")
    return repo


# ------------------------------------------------------------------------ pure maths


def test_official_metrics_maps_every_feed_and_leaves_missing_inputs_none() -> None:
    out = s.official_metrics(
        LAST,
        valuation=ValuationMetrics(
            symbol="X",
            trading_date=LAST,
            pe_ratio=12.0,
            pb_ratio=1.5,
            dividend_yield_pct=4.2,
            pe_percentile_250d=11.0,
        ),
        trend=RevenueTrend(
            symbol="X",
            latest_period="2026-10",
            yoy_3m_pct=18.0,
            growth_streak_months=6,
            is_12m_high=True,
        ),
        earnings=EarningsMetrics(
            symbol="X",
            period="2026Q3",
            statement_kind="general",
            eps_ttm=9.5,
            eps_yoy_pct=38.0,
            gross_margin_yoy_pp=2.1,
            roe_ttm_pct=15.0,
            available_at=EARLY,
        ),
        short_lending=ShortLendingMetrics(
            symbol="X", trading_date=LAST, sbl_pct_of_shares=3.2, sbl_change_20d_pct=-4.0
        ),
        day_trade=DayTradeMetrics(symbol="X", trading_date=LAST, day_trade_ratio_pct=62.0),
        pledge=InsiderPledge(
            symbol="X", period="2026-10", pledge_ratio_pct=55.0, holding_shares=1, pledged_shares=1
        ),
        has_futures=False,
        event=NextEvent(
            symbol="X", date=LAST + timedelta(days=5), kind="ex_dividend", cash_dividend=2.5
        ),
    )
    assert set(out) == set(s.OFFICIAL_KEYS)
    assert out["pe_ratio"] == 12.0 and out["revenue_yoy_3m_pct"] == 18.0
    assert out["gross_margin_change_pct"] == 2.1 and out["has_futures"] is False
    assert out["days_to_ex"] == 5 and out["next_ex_date"] == "2026-12-02"
    assert out["pb_percentile_250d"] is None and out["revenue_yoy_12m_pct"] is None
    empty = s.official_metrics(LAST)
    assert all(value is None for value in empty.values())
    # an investor conference is not an ex-date
    conference = NextEvent(symbol="X", date=LAST, kind="investor_conference")
    assert s.official_metrics(LAST, event=conference)["next_ex_date"] is None


def test_catalogue_has_the_new_groups_and_short_labels_fit() -> None:
    groups = [key for key, _ in s.GROUPS]
    assert groups.index("valuation") == groups.index("holders") + 1
    assert groups.index("short") == groups.index("credit") + 1
    assert {"futures", "events"} <= set(groups)
    assert dict(s.GROUPS)["fundamental"] == "營收與獲利"
    for metric in screener_meta_metrics():
        assert metric["group"] in groups
        assert len(metric["short"]) <= 8, metric["key"]
        assert metric["description"]
    assert s.METRIC_BY_KEY["pledge_ratio_pct"].higher_is_better is False
    assert s.METRIC_BY_KEY["has_futures"].kind == "bool"
    assert s.METRIC_BY_KEY["next_ex_date"].kind == "text"
    # short-interest and day-trade metrics feed no score
    scored = {key for inputs in s.FAMILY_INPUTS.values() for key, _, _ in inputs}
    assert not scored & {"sbl_pct_of_shares", "days_to_cover", "day_trade_ratio_pct"}
    assert "不計入" in s.GROUP_NOTES["short"]


def test_weights_sum_to_one_and_value_score_follows_the_table() -> None:
    assert sum(s.COMPOSITE_WEIGHTS.values()) == pytest.approx(1.0)
    for family in ("fundamental", "value"):
        assert sum(w for _, w, _ in s.FAMILY_INPUTS[family]) == pytest.approx(1.0)
    metrics = [
        {
            "pe_percentile_250d": 10.0,
            "pb_percentile_250d": 20.0,
            "dividend_yield_pct": 6.0,
            "pe_ratio": 9.0,
        },
        {
            "pe_percentile_250d": 90.0,
            "pb_percentile_250d": 80.0,
            "dividend_yield_pct": 1.0,
            "pe_ratio": 40.0,
        },
        {
            "pe_percentile_250d": None,
            "pb_percentile_250d": None,
            "dividend_yield_pct": 3.0,
            "pe_ratio": None,
        },
    ]
    (cheap, _), (rich, _), (thin, _) = s.score_universe(metrics)
    assert cheap["value"] == 100.0 and rich["value"] == 0.0
    # only yield (.25 of 1.0) is available: under half the weight, so no score at all
    assert thin["value"] is None


def test_highlights_cover_official_data_only_when_notable_and_cap_at_four() -> None:
    notable = {
        "pe_percentile_250d": 12.0,
        "dividend_yield_pct": 5.3,
        "eps_yoy_pct": 38.0,
        "gross_margin_change_pct": 2.1,
        "revenue_growth_streak_months": 6,
        "sbl_pct_of_shares": 3.2,
        "day_trade_ratio_pct": 62.0,
        "days_to_ex": 5,
        "next_cash_dividend": 2.5,
        "pledge_ratio_pct": 55.0,
    }
    out = s.build_highlights(notable, [])
    assert len(out) == 4 and out == s.build_highlights(notable, [])
    assert out[0] == "董監質押 55%"  # strongest first, then 5 日後除息
    assert "5 日後除息 2.5 元" in out
    each = {
        "本益比位於近一年 12% 分位": {"pe_percentile_250d": 12.0},
        "殖利率 5.3%": {"dividend_yield_pct": 5.3},
        "單季 EPS 年增 38%": {"eps_yoy_pct": 38.0},
        "毛利率年增 2.1 個百分點": {"gross_margin_change_pct": 2.1},
        "營收連 6 個月年增": {"revenue_growth_streak_months": 6},
        "借券餘額佔股本 3.2%": {"sbl_pct_of_shares": 3.2},
        "當沖比 62%": {"day_trade_ratio_pct": 62.0},
    }
    for text, metrics in each.items():
        assert s.build_highlights(metrics, []) == [text]
    quiet = {
        "pe_percentile_250d": 40.0,
        "dividend_yield_pct": 2.0,
        "eps_yoy_pct": 5.0,
        "gross_margin_change_pct": 0.5,
        "revenue_growth_streak_months": 3,
        "sbl_pct_of_shares": 0.5,
        "day_trade_ratio_pct": 20.0,
        "days_to_ex": 30,
        "pledge_ratio_pct": 10.0,
    }
    assert s.build_highlights(quiet, []) == []


# --------------------------------------------------------------------- snapshot job


async def test_snapshot_carries_official_metrics_flag_and_drops_old_versions(seeded) -> None:
    stale = await seeded.list_market_factors(LAST, VERSION)  # nothing yet
    assert stale == []
    from quant_signal.domain.models import MarketFactorSnapshot

    old = MarketFactorSnapshot(
        symbol="AAA.TW",
        trading_date=LAST,
        version="screener_v1",
        name="x",
        market="TW",
        close=1.0,
        scores={},
        metrics={},
        highlights=[],
        flags=[],
        available_at=EARLY,
    )
    await seeded.replace_market_factors(LAST, "screener_v1", [old])
    assert VERSION == "screener_v2"
    await ScreenerSnapshotService(seeded).compute(LAST)
    assert await seeded.list_market_factors(LAST, "screener_v1") == []
    rows = {r.symbol: r for r in await seeded.list_market_factors(LAST, VERSION)}
    a, b = rows["AAA.TW"], rows["BBB.TW"]
    m = a.metrics
    assert m["pe_ratio"] == 10.0 and m["dividend_yield_pct"] == 5.3
    assert m["pe_percentile_250d"] is not None and m["pe_percentile_250d"] < 5
    assert m["revenue_yoy_3m_pct"] is None  # only ten months, no year-earlier twins
    assert m["revenue_growth_streak_months"] == 10
    assert m["sbl_pct_of_shares"] == pytest.approx(2.7, abs=0.2)  # 2.7M shares of 100M
    assert m["day_trade_ratio_pct"] == 62.0
    assert m["pledge_ratio_pct"] == 55.0 and "high_pledge" in a.flags
    assert m["has_futures"] is True and m["futures_oi_change_5d_pct"] is not None
    assert m["next_ex_date"] == "2026-12-02" and m["days_to_ex"] == 5
    assert m["next_cash_dividend"] == 2.5
    assert "董監質押 55%" in a.highlights and len(a.highlights) <= 4
    assert set(a.scores) == {
        "momentum",
        "trend",
        "flow",
        "accumulation",
        "fundamental",
        "value",
        "stability",
    }
    # a stock with no feed rows: everything official is unknown, the futures flag is False
    assert b.metrics["pe_ratio"] is None and b.metrics["pledge_ratio_pct"] is None
    assert b.metrics["has_futures"] is False and "high_pledge" not in b.flags
    assert b.metrics["sbl_pct_of_shares"] is None and b.scores["value"] is None


async def test_snapshot_ignores_data_published_after_the_cutoff() -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days([*_stock("AAA.TW"), *_stock("CCC.TWO")])
    await _seed_full(repo, "AAA.TW", at=LATE)
    await ScreenerSnapshotService(repo).compute(LAST)
    row = next(r for r in await repo.list_market_factors(LAST, VERSION) if r.symbol == "AAA.TW")
    for key in s.OFFICIAL_KEYS:
        assert row.metrics[key] is None, key
    assert row.metrics["foreign_holding_pct"] is None  # existing feeds keep the same rule


async def test_new_presets_filter_and_sort_on_the_official_metrics(seeded) -> None:
    await ScreenerSnapshotService(seeded).compute(LAST)
    service = ScreenerService(seeded)
    meta = await service.meta()
    assert {"value_yield", "earnings_growth", "sbl_high_inst_buy", "futures_oi_up"} <= {
        p["key"] for p in meta["presets"]
    }
    short_group = next(g for g in meta["groups"] if g["key"] == "short")
    assert "不計入" in short_group["note"]
    assert "value" in {f["key"] for f in meta["factors"]}
    for key in ("value_yield", "earnings_growth", "sbl_high_inst_buy", "futures_oi_up"):
        assert PRESET_BY_KEY[key]["exclude_risk"] is True
        result = await service.screen(preset=key)
        assert result.version == "screener_v2"
    sbl = await service.screen(preset="sbl_high_inst_buy")
    assert [e.symbol for e in sbl.entries] == ["AAA.TW"]
    futures = await service.screen(preset="futures_oi_up")
    assert [e.symbol for e in futures.entries] == ["AAA.TW"]  # BBB has no futures
    only = await service.screen(flags=["has_futures"], sort="pe_ratio", order="asc")
    assert [e.symbol for e in only.entries] == ["AAA.TW"]
    trimmed = analyst_view(sbl, PRESET_BY_KEY["sbl_high_inst_buy"]["columns"])
    assert trimmed["entries"][0]["metrics"]["sbl_pct_of_shares"] is not None


async def test_mcp_default_columns_include_the_new_metrics() -> None:
    from quant_signal.application.screener import MCP_DEFAULT_COLUMNS

    assert {
        "pe_ratio",
        "pe_percentile_250d",
        "dividend_yield_pct",
        "eps_yoy_pct",
        "sbl_pct_of_shares",
        "day_trade_ratio_pct",
        "pledge_ratio_pct",
        "futures_oi_change_5d_pct",
        "next_ex_date",
    } <= set(MCP_DEFAULT_COLUMNS)


# ----------------------------------------------------------------------- AI evidence


async def test_blocks_full_partial_and_empty(seeded) -> None:
    await ScreenerSnapshotService(seeded).compute(LAST)
    full = await build_official_blocks(seeded, "aaa.tw", LAST)
    assert set(full) == set(BLOCK_KEYS)
    assert full["valuation"]["date"] == "2026-11-27" and full["valuation"]["pe_ratio"] == 10.0
    assert full["revenue_trend"]["period"] == "2026-10"
    earnings = full["earnings"]
    assert earnings["period"] == "2026Q3" and earnings["eps_quarter"] == 1.4
    assert earnings["eps_yoy_pct"] == 75.0 and earnings["gross_margin_yoy_pp"] == pytest.approx(
        8.67
    )
    assert earnings["eps_ttm"] == pytest.approx(4.4) and earnings["roe_ttm_pct"] is not None
    assert full["holders"]["tdcc_date"] == "2026-11-20"
    assert full["holders"]["foreign_holding_pct"] == 30.0
    assert full["holders"]["pledge_ratio_pct"] == 55.0
    assert full["short_interest"]["sbl_date"] == "2026-11-27"
    assert full["short_interest"]["day_trade_ratio_pct"] == 62.0
    assert full["stock_futures"]["date"] == "2026-11-27"
    assert full["upcoming_events"]["days_ahead"] == 5
    assert full["market_derivatives"] == {"date": "2026-11-27", "vix": 18.5}
    shot = full["screener"]
    assert shot["date"] == "2026-11-27" and shot["universe"] == 3 and shot["rank"] in (1, 2, 3)
    assert "value" not in shot["scores"] or isinstance(shot["scores"]["value"], float)

    partial = await build_official_blocks(seeded, "BBB.TW", LAST)
    assert partial["screener"] is not None
    assert [k for k in BLOCK_KEYS if partial[k] is None and k != "market_derivatives"] == [
        "valuation",
        "revenue_trend",
        "earnings",
        "holders",
        "short_interest",
        "stock_futures",
        "upcoming_events",
    ]
    nothing = await build_official_blocks(MemoryQuantRepository(), "ZZZ.TW", LAST)
    assert all(value is None for value in nothing.values())


async def test_blocks_are_point_in_time() -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days([*_stock("AAA.TW"), *_stock("CCC.TWO")])
    await _seed_full(repo, "AAA.TW", at=LATE)
    blocks = await build_official_blocks(repo, "AAA.TW", LAST)
    assert all(value is None for value in blocks.values())
    later = await build_official_blocks(repo, "AAA.TW", LAST + timedelta(days=1))
    assert later["valuation"] is not None and later["short_interest"] is not None


async def test_prompt_payload_lists_blocks_and_names_missing_ones(
    trending_bars, healthy_market
) -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_bars(trending_bars)
    await repo.upsert_market_observations(healthy_market)
    as_of = trending_bars[-1].trading_date
    await repo.upsert_valuation_daily(
        [
            ValuationDay(
                symbol="TEST",
                trading_date=as_of,
                source="twse_bwibbu",
                pe_ratio=15.0,
                pb_ratio=2.0,
                dividend_yield_pct=3.0,
                fiscal_period="2026Q3",
                available_at=datetime.combine(as_of, time(18), TAIPEI),
            )
        ]
    )
    context = await AnalysisContextService(repo).build(
        symbol="TEST", as_of=as_of, market="TW", benchmark="^TWII", strategy_version="regime_v1"
    )
    assert context.valuation == {
        "date": as_of.isoformat(),
        "pe_ratio": 15.0,
        "pb_ratio": 2.0,
        "dividend_yield_pct": 3.0,
        "fiscal_period": "2026Q3",
    }
    assert context.earnings is None
    payload = prompt_context(context, [])
    assert payload["valuation"]["pe_ratio"] == 15.0
    assert "earnings" not in payload and "earnings" in payload["official_blocks_missing"]
    assert "valuation" not in payload["official_blocks_missing"]
    assert len(json.dumps(payload["valuation"], ensure_ascii=False)) < 200


async def test_debate_cites_blocks_and_keeps_the_guard_in_every_prompt(
    trending_bars, healthy_market
) -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_bars(trending_bars)
    await repo.upsert_market_observations(healthy_market)
    as_of = trending_bars[-1].trading_date
    await repo.upsert_valuation_daily(
        [
            ValuationDay(
                symbol="TEST",
                trading_date=as_of,
                source="twse_bwibbu",
                pe_ratio=15.0,
                available_at=datetime.combine(as_of, time(18), TAIPEI),
            )
        ]
    )
    seen: list[tuple[str, dict]] = []

    class Client:
        model_name = "fake"

        async def complete_json(self, system_prompt: str, payload: dict) -> dict:
            seen.append((system_prompt, payload))
            if "多方（看漲）" in system_prompt or "空方（看跌）" in system_prompt:
                stance = "bullish" if "多方（看漲）" in system_prompt else "bearish"
                return {"stance": stance, "thesis": "t", "key_evidence": ["e"]}
            if "裁決者" not in system_prompt:
                return {"summary": "s", "evidence_refs": ["valuation", "invented"]}
            return {
                "headline": "h",
                "stance": "neutral",
                "operation_guide": "g",
                "key_evidence": ["valuation 2026"],
                "risk_warnings": [],
                "scenarios": [],
                "limitations": [],
                "evidence_refs": ["valuation"],
            }

    report = await DebateAnalysisService(repo, Client()).analyze(symbol="TEST", as_of=as_of)
    assert report.judgement.evidence_refs == ["valuation"]
    assert report.risk_review is not None and report.risk_review.evidence_refs == ["valuation"]
    assert any(e.id == "valuation" and e.as_of == as_of for e in report.evidence)
    assert len(seen) == 4
    for prompt, payload in seen:
        assert "official_blocks_missing" in prompt or "不是中性也不是零" in prompt
        assert "不得單獨用來推論多空方向" in prompt
        assert payload["valuation"]["pe_ratio"] == 15.0
        assert "short_interest" in payload["official_blocks_missing"]
