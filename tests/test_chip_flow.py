from datetime import UTC, date, datetime, timedelta

from quant_signal.domain.models import Bar, BrokerBranchFlow, InstitutionalFlow
from quant_signal.quant.chip_flow import ChipFlowEngine


def _available(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 10, tzinfo=UTC)


def test_chip_flow_scores_streaks_and_licensed_branch_metrics() -> None:
    start = date(2026, 7, 1)
    flows: list[InstitutionalFlow] = []
    bars: list[Bar] = []
    branches: list[BrokerBranchFlow] = []
    for index in range(20):
        day = start + timedelta(days=index)
        flows.append(
            InstitutionalFlow(
                symbol="2330.TW",
                trading_date=day,
                foreign_buy=2_000_000,
                foreign_sell=1_000_000,
                foreign_net=1_000_000,
                investment_trust_buy=600_000,
                investment_trust_sell=100_000,
                investment_trust_net=500_000,
                dealer_buy=300_000,
                dealer_sell=200_000,
                dealer_net=100_000,
                total_net=1_600_000,
                foreign_holding_ratio=72 + index * 0.01,
                available_at=_available(day),
                source="twse_official",
            )
        )
        bars.append(
            Bar(
                symbol="2330.TW",
                trading_date=day,
                open=100 + index,
                high=102 + index,
                low=99 + index,
                close=101 + index,
                volume=20_000_000,
                available_at=_available(day),
                source="twse_official",
            )
        )
        branches.append(
            BrokerBranchFlow(
                symbol="2330.TW",
                trading_date=day,
                branch_code="9200",
                branch_name="測試分點",
                buy_shares=1_000_000,
                sell_shares=200_000,
                buy_amount=101_000_000,
                sell_amount=20_200_000,
                day_trade_buy_shares=100_000,
                day_trade_sell_shares=100_000,
                available_at=_available(day),
                source="licensed_test",
            )
        )

    snapshot = ChipFlowEngine().analyze(
        flows,
        as_of=flows[-1].trading_date,
        bars=bars,
        branch_flows=branches,
    )

    assert snapshot.score > 50
    assert snapshot.metrics.foreign_streak == 20
    assert snapshot.metrics.investment_trust_streak == 20
    assert snapshot.metrics.foreign_holding_change_5d == 0.04
    assert snapshot.branch_data_available is True
    assert snapshot.metrics.branch_buy_concentration_5d is not None
    assert snapshot.top_buyers[0].branch_code == "9200"


def test_twse_and_tpex_stock_institutional_parsers() -> None:
    from quant_signal.infrastructure.providers.tpex import TpexProvider
    from quant_signal.infrastructure.providers.twse import TwseProvider

    day = date(2026, 7, 30)
    twse_row = [
        "2330",
        "台積電",
        "10,000",
        "12,000",
        "-2,000",
        "0",
        "0",
        "0",
        "5,000",
        "3,000",
        "2,000",
        "1,000",
        "2,000",
        "1,000",
        "1,000",
        "1,500",
        "1,500",
        "0",
        "1,000",
    ]
    twse = TwseProvider.parse_stock_institutional_flow(
        {"data": [twse_row]},
        "2330.TW",
        trading_date=day,
        foreign_holding_ratio=72.5,
    )
    assert twse is not None
    assert twse.foreign_net == -2_000
    assert twse.dealer_buy == 3_500
    assert twse.foreign_holding_ratio == 72.5

    tpex_row = ["0"] * 24
    tpex_row[0] = "6488"
    for index, value in {
        2: "10,000",
        3: "12,000",
        4: "-2,000",
        11: "5,000",
        12: "3,000",
        13: "2,000",
        20: "4,000",
        21: "3,000",
        22: "1,000",
        23: "1,000",
    }.items():
        tpex_row[index] = value
    tpex = TpexProvider.parse_stock_institutional_flow(
        {"tables": [{"data": [tpex_row]}]},
        "6488.TWO",
        trading_date=day,
    )
    assert tpex is not None
    assert tpex.investment_trust_net == 2_000
    assert tpex.total_net == 1_000
