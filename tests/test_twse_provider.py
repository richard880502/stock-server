from datetime import date

from quant_signal.infrastructure.providers.twse import TwseProvider


def test_parse_monthly_stock_uses_roc_date_and_unadjusted_price() -> None:
    payload = {
        "stat": "OK",
        "data": [
            [
                "115/07/30",
                "51,372,177",
                "114,098,819,878",
                "2,205.00",
                "2,260.00",
                "2,190.00",
                "2,205.00",
                "+5.00",
                "177,492",
                "",
            ]
        ],
    }

    bars = TwseProvider.parse_monthly_stock(payload, "2330.TW")

    assert len(bars) == 1
    assert bars[0].trading_date == date(2026, 7, 30)
    assert bars[0].close == 2205
    assert bars[0].adjusted_close is None
    assert bars[0].volume == 51_372_177
    assert bars[0].source == "twse_official"


def test_parse_daily_market_filters_to_common_stocks() -> None:
    payload = {
        "stat": "OK",
        "date": "20260730",
        "tables": [
            {
                "fields": ["指數", "收盤指數"],
                "data": [["發行量加權股價指數", "39,933.30"]],
            },
            {
                "fields": ["成交統計", "成交金額(元)"],
                "data": [["證券合計(1+6+14+15)", "1,028,253,211,257"]],
            },
            {
                "fields": [
                    "證券代號",
                    "證券名稱",
                    "成交股數",
                    "成交筆數",
                    "成交金額",
                    "開盤價",
                    "最高價",
                    "最低價",
                    "收盤價",
                    "漲跌(+/-)",
                    "漲跌價差",
                ],
                "data": [
                    [
                        "2330",
                        "台積電",
                        "51,372,177",
                        "177,492",
                        "114,098,819,878",
                        "2,205.00",
                        "2,260.00",
                        "2,190.00",
                        "2,205.00",
                        "<p style= color:red>+</p>",
                        "5.00",
                    ],
                    [
                        "0050",
                        "元大台灣50",
                        "1,000",
                        "10",
                        "100,000",
                        "100",
                        "101",
                        "99",
                        "100",
                        "-",
                        "1",
                    ],
                ],
            },
        ],
    }

    market = TwseProvider.parse_daily_market(payload)

    assert market is not None
    assert market.index_close == 39_933.3
    assert market.turnover_value == 1_028_253_211_257
    assert len(market.quotes) == 1
    assert market.quotes[0].symbol == "2330.TW"
    assert market.quotes[0].direction == 1


def test_parse_capital_reports() -> None:
    flows = {
        "stat": "OK",
        "data": [
            ["自營商(自行買賣)", "0", "0", "-500"],
            ["自營商(避險)", "0", "0", "-1,500"],
            ["投信", "0", "0", "3,000"],
            ["外資及陸資(不含外資自營商)", "0", "0", "-8,000"],
        ],
    }
    margin = {
        "stat": "OK",
        "tables": [
            {
                "data": [
                    ["融券(交易單位)", "0", "0", "0", "200", "180"],
                    ["融資金額(仟元)", "0", "0", "0", "500,000", "490,000"],
                ]
            }
        ],
    }

    assert TwseProvider.parse_institutional_flows(flows) == (-8_000, 3_000, -2_000)
    assert TwseProvider.parse_margin_balances(margin) == (490_000_000, 180)


def test_parse_twse_corporate_action() -> None:
    payload = {
        "stat": "OK",
        "data": [
            [
                "113年03月18日",
                "2330",
                "台積電",
                "753.00",
                "749.50",
                "3.499789",
                "息",
            ]
        ],
    }

    actions = TwseProvider.parse_corporate_actions(payload, "2330.TW")

    assert len(actions) == 1
    assert actions[0].ex_date == date(2024, 3, 18)
    assert actions[0].previous_close == 753
    assert actions[0].reference_price == 749.5
