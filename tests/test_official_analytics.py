from __future__ import annotations

from datetime import UTC, datetime

import pytest

from quant_signal.domain.models import MonthlyRevenue
from quant_signal.quant.event_classifier import classify_material
from quant_signal.quant.revenue import revenue_metrics

AT = datetime(2026, 11, 1, tzinfo=UTC)


def month(index: int, revenue: float, yoy: float | None, *, revision: int = 1, **kw):
    year, number = divmod(index, 12)
    return MonthlyRevenue(
        symbol="2330.TW",
        period=f"{2025 + year}-{number + 1:02d}",
        revenue=revenue,
        yoy_pct=yoy,
        mom_pct=kw.get("mom"),
        cumulative_yoy_pct=kw.get("cum"),
        source="twse_revenue",
        available_at=AT,
        revision=revision,
    )


def test_metrics_for_an_accelerating_company() -> None:
    # 14 months: 2025-01 .. 2026-02; YoY climbs from 5 to 20.
    rows = [month(i, 100 + i, 5 + i * 1.0, mom=1.5, cum=12.0) for i in range(14)]
    metrics = revenue_metrics(rows)
    assert metrics is not None
    assert metrics.period == "2026-02"
    assert metrics.yoy_pct == 18 and metrics.mom_pct == 1.5 and metrics.cumulative_yoy_pct == 12
    # latest YoY 18 minus mean(15, 16, 17) = 2
    assert metrics.acceleration_pct == pytest.approx(2.0)
    assert metrics.consecutive_positive_yoy == 14
    assert metrics.is_12m_high is True
    assert metrics.months_available == 14


def test_streak_stops_at_a_negative_month_and_at_gaps() -> None:
    rows = [month(0, 100, 5), month(1, 90, -3), month(2, 95, 4), month(3, 99, 6)]
    assert revenue_metrics(rows).consecutive_positive_yoy == 2

    gap = [month(0, 100, 5), month(1, 100, 5), month(3, 100, 5)]  # month 2 missing
    metrics = revenue_metrics(gap)
    assert metrics.consecutive_positive_yoy == 1
    assert metrics.acceleration_pct is None  # needs three consecutive prior months


def test_twelve_month_high_requires_full_history_and_the_max() -> None:
    short = [month(i, 100 + i, 5) for i in range(6)]
    assert revenue_metrics(short).is_12m_high is None

    rows = [month(i, 100 + i, 5) for i in range(12)]
    rows[11] = month(11, 90, 5)  # latest dips below earlier months
    assert revenue_metrics(rows).is_12m_high is False


def test_missing_yoy_gives_no_acceleration_or_streak() -> None:
    metrics = revenue_metrics([month(0, 100, None), month(1, 100, None)])
    assert metrics.yoy_pct is None and metrics.acceleration_pct is None
    assert metrics.consecutive_positive_yoy == 0


def test_newest_revision_wins_and_empty_input_is_none() -> None:
    old = month(0, 100, 5, revision=1)
    restated = month(0, 120, 9, revision=2)
    metrics = revenue_metrics([restated, old])
    assert metrics.revenue == 120 and metrics.yoy_pct == 9 and metrics.months_available == 1
    assert revenue_metrics([]) is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("公告本公司董事會決議買回庫藏股", ("buyback", "positive")),
        ("公告本公司董事會決議處分庫藏股", ("buyback", "neutral")),
        ("公告本公司買回庫藏股期間屆滿", ("buyback", "neutral")),
        ("公告本公司董事會決議辦理現金增資發行新股", ("capital_increase", "negative")),
        ("公告本公司辦理私募普通股", ("capital_increase", "negative")),
        ("公告本公司董事會決議對子公司增資", ("capital_increase", "neutral")),
        (
            "公告本公司第115年度海外第一次無擔保可轉換公司債完成定價",
            ("capital_increase", "neutral"),
        ),
        ("公告本公司董事會決議與甲公司合併", ("m_and_a", "neutral")),
        ("公告本公司終止公開收購乙公司", ("m_and_a", "negative")),
        ("公告本公司取得重大訂單並簽署供貨合約", ("major_contract", "positive")),
        ("公告本公司與丙公司終止合作合約", ("major_contract", "negative")),
        ("公告本公司與丁公司簽署合作備忘錄", ("major_contract", "positive")),
        ("公告本公司上修115年度財務預測", ("guidance", "positive")),
        ("公告本公司下修115年度財測", ("guidance", "negative")),
        ("澄清媒體報導", ("clarification", "neutral")),
        ("公告本公司就媒體報導之說明", ("clarification", "neutral")),
        ("公告本公司涉及專利侵權訴訟", ("litigation", "negative")),
        ("公告本公司專利訴訟獲得勝訴判決", ("litigation", "positive")),
        ("代重要子公司陽明(新加坡)有限公司公告董事會決議發放股利", ("dividend", "positive")),
        ("代重要子公司陽明(新加坡)有限公司公告現金股利除息基準日", ("dividend", "neutral")),
        ("公告本公司董事會決議不分派股利", ("dividend", "negative")),
        ("公告本公司董事會決議盈餘轉增資配股", ("dividend", "positive")),
        ("公告本公司董事長異動", ("executive_change", "neutral")),
        ("公告本公司總經理辭任及新任總經理就任", ("executive_change", "neutral")),
        ("公告本公司董事會通過115年第三季合併財務報告", ("other", "neutral")),
        ("本公司受邀參加台新證券舉辦之法人說明會", ("other", "neutral")),
        ("公告本公司從事衍生性金融商品交易單筆達交易合約10%損失上限", ("other", "negative")),
        ("公告本公司董事會決議辦理減資彌補虧損", ("other", "negative")),
        ("", ("other", "neutral")),
    ],
)
def test_classify_material(title: str, expected) -> None:
    assert classify_material(title, "第51款") == expected


def test_classifier_is_deterministic_and_normalises_width() -> None:
    assert classify_material("公告本公司董事會決議買回庫藏股") == classify_material(
        "公告本公司董事會決議買回庫藏股"
    )
    assert classify_material("ＭＯＵ簽署") == ("major_contract", "positive")
