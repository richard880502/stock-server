from datetime import date

from quant_signal.infrastructure.providers.tpex import TpexProvider


def test_parse_tpex_monthly_stock_converts_thousand_shares() -> None:
    payload = {
        "stat": "ok",
        "tables": [
            {
                "data": [
                    [
                        "113/07/01",
                        "4,261",
                        "2,391,292",
                        "542.00",
                        "572.00",
                        "540.00",
                        "565.00",
                        "26.00",
                        "5,982",
                    ]
                ]
            }
        ],
    }

    bars = TpexProvider.parse_monthly_stock(payload, "6488.TWO")

    assert bars[0].trading_date == date(2024, 7, 1)
    assert bars[0].close == 565
    assert bars[0].volume == 4_261_000
    assert bars[0].source == "tpex_official"


def test_parse_tpex_corporate_action() -> None:
    payload = {
        "stat": "ok",
        "tables": [
            {
                "data": [
                    [
                        "113/07/18",
                        "6488",
                        "環球晶",
                        "576.00",
                        "565.00",
                        "0",
                        "11",
                        "11",
                        "除息",
                    ]
                ]
            }
        ],
    }

    actions = TpexProvider.parse_corporate_actions(payload, "6488.TWO")

    assert actions[0].ex_date == date(2024, 7, 18)
    assert actions[0].previous_close == 576
    assert actions[0].reference_price == 565


def test_parse_tpex_market_filters_common_stocks() -> None:
    payload = {
        "stat": "ok",
        "date": "20240731",
        "tables": [
            {
                "data": [
                    [
                        "6488",
                        "環球晶",
                        "500",
                        "+5",
                        "496",
                        "505",
                        "495",
                        "500",
                        "1,000,000",
                        "500,000,000",
                    ],
                    [
                        "006201",
                        "ETF",
                        "20",
                        "+0.1",
                        "20",
                        "20",
                        "20",
                        "20",
                        "1,000",
                        "20,000",
                    ],
                ]
            }
        ],
    }

    market = TpexProvider.parse_daily_market(payload, index_close=262.3)

    assert market is not None
    assert len(market.quotes) == 1
    assert market.quotes[0].symbol == "6488.TWO"
    assert market.turnover_value == 500_000_000
