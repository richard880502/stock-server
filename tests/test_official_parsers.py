"""Parsers run on payloads recorded from the live TWSE / TPEx OpenAPI (2026-11-26)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant_signal.infrastructure.providers import official_events as src

FIXTURES = Path(__file__).parent / "fixtures" / "official"
TAIPEI = ZoneInfo("Asia/Taipei")
FETCHED = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)


def load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


# --- value helpers --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1151125", date(2026, 11, 25)),
        ("115/11/25", date(2026, 11, 25)),
        ("１１５／１１／２５", date(2026, 11, 25)),  # full-width digits and slashes
        ("民國115年11月25日", date(2026, 11, 25)),
        ("20261125", date(2026, 11, 25)),  # TPEx warning-note uses Gregorian
        ("990101", date(2010, 1, 1)),  # two-digit ROC year
        ("1150230", None),  # impossible day
        ("", None),
        ("-", None),
        ("N/A", None),
        (None, None),
    ],
)
def test_parse_roc_date(raw, expected) -> None:
    assert src.parse_roc_date(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("63944", time(6, 39, 44)),  # TWSE drops the leading zero
        ("135728", time(13, 57, 28)),
        ("9:30:00", time(9, 30)),
        ("", None),
        ("abc", None),
        ("256000", None),
    ],
)
def test_parse_roc_time(raw, expected) -> None:
    assert src.parse_roc_time(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("11510", "2026-10"), ("11601", "2027-01"), ("9912", "2010-12"), ("11513", None), ("x", None)],
)
def test_parse_roc_period(raw, expected) -> None:
    assert src.parse_roc_period(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.5", 1234.5),
        ("１２３", 123.0),
        ("-19.38", -19.38),
        ("12.5%", 12.5),
        ("", None),
        ("-", None),
        ("不適用", None),
        ("N/A", None),
        ("註4", None),
        (None, None),
    ],
)
def test_parse_number(raw, expected) -> None:
    assert src.parse_number(raw) == expected


def test_next_weekday_skips_the_weekend() -> None:
    assert src.next_weekday(date(2026, 11, 26)) == date(2026, 11, 27)  # Thursday
    assert src.next_weekday(date(2026, 11, 27)) == date(2026, 11, 30)  # Friday -> Monday


# --- 重大訊息 -------------------------------------------------------------------


def test_twse_material() -> None:
    events = src.parse_twse_material(load("twse_material"), available_at=FETCHED)
    assert len(events) == 5
    foxconn = next(e for e in events if e.symbol == "2317.TW")
    assert foxconn.kind == "material"
    assert foxconn.announced_at == datetime(2026, 11, 25, 6, 39, 44, tzinfo=TAIPEI)
    assert foxconn.announced_at.utcoffset() is not None
    assert (
        foxconn.title == "公告本公司第115年度海外第一次無擔保可轉換公司債完成定價"
    )  # wrap removed
    assert foxconn.clause == "第51款"
    assert foxconn.detail and len(foxconn.detail) <= src.DETAIL_LIMIT
    assert foxconn.available_at == FETCHED
    assert foxconn.source == "twse_material"
    assert len(foxconn.external_key) <= 64


def test_material_detail_is_truncated() -> None:
    payload = load("twse_material")
    payload[0]["說明"] = "字" * 5000
    event = src.parse_twse_material(payload[:1], available_at=FETCHED)[0]
    assert len(event.detail) == src.DETAIL_LIMIT and event.detail.endswith("…")


def test_material_key_is_stable_and_distinguishes_announcements() -> None:
    first = src.parse_twse_material(load("twse_material"), available_at=FETCHED)
    again = src.parse_twse_material(
        load("twse_material"), available_at=datetime(2026, 11, 27, tzinfo=UTC)
    )
    assert [e.external_key for e in first] == [e.external_key for e in again]
    assert len({e.external_key for e in first}) == len(first)


def test_tpex_material_uses_two_suffix() -> None:
    events = src.parse_tpex_material(load("tpex_material"), available_at=FETCHED)
    assert {e.symbol for e in events} == {"3489.TWO", "6474.TWO", "6690.TWO"}
    assert all(e.source == "tpex_material" and e.clause == "第53款" or e.clause for e in events)
    assert events[0].announced_at == datetime(2026, 11, 25, 7, 0, 4, tzinfo=TAIPEI)


def test_material_skips_rows_without_code_date_or_title() -> None:
    payload = [{"公司代號": "", "發言日期": "1151125", "主旨": "x"}, {"公司代號": "2330"}, "junk"]
    assert src.parse_twse_material(payload, available_at=FETCHED) == []
    with pytest.raises(ValueError):
        src.parse_twse_material({"oops": 1}, available_at=FETCHED)


# --- 除權息 ---------------------------------------------------------------------


def test_twse_exrights_keeps_common_stocks_only() -> None:
    events = src.parse_twse_exrights(load("twse_exrights"), available_at=FETCHED)
    assert len(events) == 3  # the ETF (00400A) is dropped
    first = events[0]
    assert first.kind == "ex_rights" and first.symbol == "1437.TW"
    assert first.effective_date == date(2026, 12, 3)
    assert first.announced_at == FETCHED  # a schedule has no announcement time
    assert "現金股利 0.624652 元" in first.title
    assert first.clause == "除息"


def test_tpex_exrights_labels_and_placeholder_prices() -> None:
    events = src.parse_tpex_exrights(load("tpex_exrights"), available_at=FETCHED)
    assert len(events) == 3  # bond ETF dropped
    by_symbol = {e.symbol: e for e in events}
    assert by_symbol["6523.TWO"].clause == "除息"
    assert by_symbol["6523.TWO"].effective_date == date(2026, 11, 26)
    rights = by_symbol["6160.TWO"]
    assert rights.clause == "除權"
    assert "註4" not in (rights.detail or "")  # unparsable price -> omitted
    assert rights.external_key == "6160.TWO:2026-12-03:除權"


# --- 處置 / 注意 -----------------------------------------------------------------


def test_twse_punish() -> None:
    flags = src.parse_twse_punish(load("twse_punish"), available_at=FETCHED)
    assert len(flags) == 2  # the warrant is dropped
    flag = next(f for f in flags if f.symbol == "2305.TW")
    assert flag.kind == "disposition"
    assert (flag.start_date, flag.end_date) == (date(2026, 11, 23), date(2026, 11, 27))
    assert "連續三次" in flag.reason and "第一次處置" in flag.reason
    assert flag.source == "twse_punish"


def test_tpex_disposal() -> None:
    flags = src.parse_tpex_disposal(load("tpex_disposal"), available_at=FETCHED)
    assert len(flags) == 2
    flag = next(f for f in flags if f.symbol == "4577.TWO")
    assert (flag.start_date, flag.end_date) == (date(2026, 11, 26), date(2026, 12, 2))
    assert flag.reason == "連續3個營業日"


def test_disposition_falls_back_to_announcement_date_without_period() -> None:
    rows = [{"Code": "2330", "Date": "1151124", "DispositionPeriod": ""}]
    flag = src.parse_twse_punish(rows, available_at=FETCHED)[0]
    assert flag.start_date == date(2026, 11, 24) and flag.end_date is None


def test_twse_notice_is_empty_when_nothing_is_flagged() -> None:
    assert src.parse_twse_notice(load("twse_notice_empty"), available_at=FETCHED) == []


def test_twse_notice_parses_a_flagged_stock() -> None:
    # Synthetic row built from the swagger field names (the live list was empty).
    rows = [
        {
            "Number": "1",
            "Code": "2344",
            "Name": "華邦電",
            "NumberOfAnnouncement": "1",
            "TradingInfoForAttention": "最近六個營業日累積收盤價漲幅達32.1%",
            "Date": "1151125",
            "ClosingPrice": "30.1",
            "PE": "N/A",
        }
    ]
    flag = src.parse_twse_notice(rows, available_at=FETCHED)[0]
    assert flag.kind == "attention" and flag.symbol == "2344.TW"
    assert flag.start_date == date(2026, 11, 25)
    assert flag.end_date == date(2026, 11, 26)


def test_notetrans_is_a_snapshot_of_the_fetch_day() -> None:
    flags = src.parse_twse_notetrans(
        load("twse_notetrans"), available_at=FETCHED, as_of=date(2026, 11, 26)
    )
    assert [f.symbol for f in flags] == ["7827.TW"]  # warrants dropped
    assert flags[0].start_date == date(2026, 11, 26)
    assert flags[0].reason.startswith("累計達注意標準")


def test_tpex_warning_and_note() -> None:
    flags = src.parse_tpex_warning(load("tpex_warning"), available_at=FETCHED)
    assert len(flags) == 3
    flag = next(f for f in flags if f.symbol == "3259.TWO")
    assert flag.kind == "attention" and flag.source == "tpex_warning"
    assert flag.start_date == date(2026, 11, 25) and "跌幅達47.88%" in flag.reason
    notes = src.parse_tpex_warning_note(load("tpex_warning_note"), available_at=FETCHED)
    assert {f.symbol for f in notes} == {"3259.TWO", "7856.TWO"}  # 5-digit codes dropped
    assert notes[0].start_date == date(2026, 11, 25)  # 8-digit Gregorian date parsed


# --- 月營收 ---------------------------------------------------------------------


def test_twse_revenue() -> None:
    rows = src.parse_twse_revenue(load("twse_revenue"), available_at=FETCHED)
    assert len(rows) == 4  # 912000 (a depositary receipt) is dropped
    taicement = next(r for r in rows if r.symbol == "1101.TW")
    assert taicement.period == "2026-10"
    assert taicement.revenue == 16357176
    assert taicement.mom_pct == pytest.approx(14.213679016442068)
    assert taicement.yoy_pct == pytest.approx(17.828005954078908)
    assert taicement.cumulative_revenue == 129405701
    assert taicement.cumulative_yoy_pct == pytest.approx(4.934421916477907)
    assert taicement.available_at == FETCHED


def test_revenue_blank_percentages_become_none() -> None:
    rows = src.parse_twse_revenue(load("twse_revenue"), available_at=FETCHED)
    base_zero = next(r for r in rows if r.symbol == "6467.TW")  # last year's month was 0
    assert base_zero.yoy_pct is None and base_zero.revenue == 10
    no_revenue = next(r for r in rows if r.symbol == "6838.TW")
    assert no_revenue.revenue == 0 and no_revenue.mom_pct is None


def test_tpex_revenue() -> None:
    rows = src.parse_tpex_revenue(load("tpex_revenue"), available_at=FETCHED)
    assert [r.symbol for r in rows] == ["1240.TWO", "1259.TWO"]
    assert rows[0].yoy_pct == pytest.approx(-2.9900219134948833)
    assert rows[0].source == "tpex_revenue"
