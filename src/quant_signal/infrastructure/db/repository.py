from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quant_signal.domain.models import (
    ApiKey,
    BacktestJob,
    BacktestReport,
    BacktestSpec,
    Bar,
    BrokerBranchFlow,
    ChipFlowSnapshot,
    DataSeriesStatus,
    DataStatus,
    InstitutionalFlow,
    Instrument,
    JobStatus,
    MarketEnvironmentSnapshot,
    MarketObservation,
    SignalSnapshot,
)
from quant_signal.infrastructure.db.models import (
    ApiKeyRow,
    BacktestJobRow,
    BrokerBranchFlowRow,
    ChipFlowSnapshotRow,
    DailyBarRow,
    InstitutionalFlowRow,
    InstrumentRow,
    MarketEnvironmentSnapshotRow,
    MarketObservationRow,
    SignalSnapshotRow,
)


class PostgresQuantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_data_status(self) -> DataStatus:
        bar_statement = (
            select(
                InstrumentRow.symbol,
                DailyBarRow.source,
                func.min(DailyBarRow.trading_date),
                func.max(DailyBarRow.trading_date),
                func.count(DailyBarRow.id),
                func.count(DailyBarRow.adjusted_close),
            )
            .join(InstrumentRow, DailyBarRow.instrument_id == InstrumentRow.id)
            .group_by(InstrumentRow.symbol, DailyBarRow.source)
            .order_by(InstrumentRow.symbol, DailyBarRow.source)
        )
        market_statement = (
            select(
                MarketObservationRow.market,
                MarketObservationRow.source,
                func.min(MarketObservationRow.trading_date),
                func.max(MarketObservationRow.trading_date),
                func.count(MarketObservationRow.id),
            )
            .group_by(MarketObservationRow.market, MarketObservationRow.source)
            .order_by(MarketObservationRow.market, MarketObservationRow.source)
        )
        institutional_statement = (
            select(
                InstrumentRow.symbol,
                InstitutionalFlowRow.source,
                func.min(InstitutionalFlowRow.trading_date),
                func.max(InstitutionalFlowRow.trading_date),
                func.count(InstitutionalFlowRow.id),
            )
            .join(
                InstrumentRow,
                InstitutionalFlowRow.instrument_id == InstrumentRow.id,
            )
            .group_by(InstrumentRow.symbol, InstitutionalFlowRow.source)
            .order_by(InstrumentRow.symbol, InstitutionalFlowRow.source)
        )
        branch_statement = (
            select(
                InstrumentRow.symbol,
                BrokerBranchFlowRow.source,
                func.min(BrokerBranchFlowRow.trading_date),
                func.max(BrokerBranchFlowRow.trading_date),
                func.count(BrokerBranchFlowRow.id),
            )
            .join(
                InstrumentRow,
                BrokerBranchFlowRow.instrument_id == InstrumentRow.id,
            )
            .group_by(InstrumentRow.symbol, BrokerBranchFlowRow.source)
            .order_by(InstrumentRow.symbol, BrokerBranchFlowRow.source)
        )
        bar_rows = (await self.session.execute(bar_statement)).all()
        market_rows = (await self.session.execute(market_statement)).all()
        institutional_rows = (
            await self.session.execute(institutional_statement)
        ).all()
        branch_rows = (await self.session.execute(branch_statement)).all()
        bars = [
            DataSeriesStatus(
                key=key,
                source=source,
                first_date=first_date,
                latest_date=latest_date,
                observations=count,
                adjusted_observations=adjusted_count,
                is_real=source != "synthetic_demo",
            )
            for key, source, first_date, latest_date, count, adjusted_count in bar_rows
        ]
        markets = [
            DataSeriesStatus(
                key=key,
                source=source,
                first_date=first_date,
                latest_date=latest_date,
                observations=count,
                is_real=source != "synthetic_demo",
            )
            for key, source, first_date, latest_date, count in market_rows
        ]
        institutions = [
            DataSeriesStatus(
                key=key,
                source=source,
                first_date=first_date,
                latest_date=latest_date,
                observations=count,
                is_real=source != "synthetic_demo",
            )
            for key, source, first_date, latest_date, count in institutional_rows
        ]
        branches = [
            DataSeriesStatus(
                key=key,
                source=source,
                first_date=first_date,
                latest_date=latest_date,
                observations=count,
                is_real=source != "synthetic_demo",
            )
            for key, source, first_date, latest_date, count in branch_rows
        ]
        return DataStatus(
            generated_at=datetime.now(UTC),
            real_data_ready=any(
                item.is_real and item.observations >= 60 for item in bars
            ),
            bar_series=bars,
            market_series=markets,
            institutional_series=institutions,
            branch_series=branches,
        )

    async def list_bars(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[Bar]:
        statement: Select[tuple[DailyBarRow, str]] = (
            select(DailyBarRow, InstrumentRow.symbol)
            .join(InstrumentRow, DailyBarRow.instrument_id == InstrumentRow.id)
            .where(InstrumentRow.symbol == symbol.upper())
            .order_by(DailyBarRow.trading_date, DailyBarRow.revision)
        )
        if start:
            statement = statement.where(DailyBarRow.trading_date >= start)
        if end:
            statement = statement.where(DailyBarRow.trading_date <= end)
        rows = (await self.session.execute(statement)).all()
        return [
            Bar(
                symbol=row_symbol,
                trading_date=row.trading_date,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                adjusted_close=row.adjusted_close,
                adjustment_factor=row.adjustment_factor,
                volume=row.volume,
                available_at=row.available_at,
                source=row.source,
                revision=row.revision,
            )
            for row, row_symbol in rows
        ]

    async def upsert_bars(
        self,
        bars: list[Bar],
        *,
        instrument_name: str | None = None,
        market: str | None = None,
    ) -> int:
        if not bars:
            return 0
        symbol = bars[0].symbol.upper()
        if any(bar.symbol.upper() != symbol for bar in bars):
            raise ValueError("one upsert_bars call may contain only one symbol")

        instrument_updates: dict[str, str | None] = {"market": market}
        if instrument_name is not None:
            instrument_updates["name"] = instrument_name
        instrument_statement = (
            insert(InstrumentRow)
            .values(symbol=symbol, name=instrument_name, market=market)
            .on_conflict_do_update(
                index_elements=[InstrumentRow.symbol],
                set_=instrument_updates,
            )
            .returning(InstrumentRow.id)
        )
        instrument_id = (await self.session.execute(instrument_statement)).scalar_one()

        inserted = 0
        for bar in bars:
            raw = bar.model_dump(mode="json")
            payload_hash = hashlib.sha256(
                json.dumps(raw, sort_keys=True).encode("utf-8")
            ).hexdigest()
            statement = (
                insert(DailyBarRow)
                .values(
                    instrument_id=instrument_id,
                    trading_date=bar.trading_date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    adjusted_close=bar.adjusted_close,
                    adjustment_factor=bar.adjustment_factor,
                    volume=bar.volume,
                    available_at=bar.available_at,
                    source=bar.source,
                    revision=bar.revision,
                    payload_hash=payload_hash,
                )
                .on_conflict_do_update(
                    constraint="uq_bars_daily_version",
                    set_={
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "adjusted_close": bar.adjusted_close,
                        "adjustment_factor": bar.adjustment_factor,
                        "volume": bar.volume,
                        "available_at": bar.available_at,
                        "payload_hash": payload_hash,
                    },
                )
            )
            result = await self.session.execute(statement)
            inserted += result.rowcount or 0
        await self.session.commit()
        return inserted

    async def save_signal(self, snapshot: SignalSnapshot) -> None:
        payload = snapshot.model_dump(mode="json")
        statement = (
            insert(SignalSnapshotRow)
            .values(
                symbol=snapshot.symbol,
                as_of=snapshot.as_of,
                strategy_version=snapshot.strategy_version,
                score=snapshot.score,
                action=snapshot.action.value,
                regime=snapshot.regime.value,
                confidence=snapshot.confidence,
                payload=payload,
            )
            .on_conflict_do_update(
                constraint="uq_signal_snapshot",
                set_={
                    "score": snapshot.score,
                    "action": snapshot.action.value,
                    "regime": snapshot.regime.value,
                    "confidence": snapshot.confidence,
                    "payload": payload,
                },
            )
        )
        await self.session.execute(statement)
        await self.session.commit()

    async def list_institutional_flows(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[InstitutionalFlow]:
        statement = (
            select(InstitutionalFlowRow, InstrumentRow.symbol)
            .join(
                InstrumentRow,
                InstitutionalFlowRow.instrument_id == InstrumentRow.id,
            )
            .where(InstrumentRow.symbol == symbol.upper())
            .order_by(
                InstitutionalFlowRow.trading_date,
                InstitutionalFlowRow.revision,
            )
        )
        if start:
            statement = statement.where(InstitutionalFlowRow.trading_date >= start)
        if end:
            statement = statement.where(InstitutionalFlowRow.trading_date <= end)
        rows = (await self.session.execute(statement)).all()
        return [
            InstitutionalFlow(
                symbol=row_symbol,
                trading_date=row.trading_date,
                foreign_buy=row.foreign_buy,
                foreign_sell=row.foreign_sell,
                foreign_net=row.foreign_net,
                investment_trust_buy=row.investment_trust_buy,
                investment_trust_sell=row.investment_trust_sell,
                investment_trust_net=row.investment_trust_net,
                dealer_buy=row.dealer_buy,
                dealer_sell=row.dealer_sell,
                dealer_net=row.dealer_net,
                total_net=row.total_net,
                foreign_holding_ratio=row.foreign_holding_ratio,
                available_at=row.available_at,
                source=row.source,
                revision=row.revision,
            )
            for row, row_symbol in rows
        ]

    async def upsert_institutional_flows(
        self,
        flows: list[InstitutionalFlow],
    ) -> int:
        if not flows:
            return 0
        symbol = flows[0].symbol.upper()
        if any(item.symbol.upper() != symbol for item in flows):
            raise ValueError("one institutional upsert may contain only one symbol")
        instrument_id = await self._ensure_instrument(symbol)
        inserted = 0
        for item in flows:
            values = item.model_dump(mode="python", exclude={"symbol"})
            payload_hash = self._payload_hash(item.model_dump(mode="json"))
            statement = (
                insert(InstitutionalFlowRow)
                .values(
                    instrument_id=instrument_id,
                    **values,
                    payload_hash=payload_hash,
                )
                .on_conflict_do_update(
                    constraint="uq_institutional_flow_version",
                    set_={
                        **values,
                        "payload_hash": payload_hash,
                    },
                )
            )
            result = await self.session.execute(statement)
            inserted += result.rowcount or 0
        await self.session.commit()
        return inserted

    async def list_broker_branch_flows(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[BrokerBranchFlow]:
        statement = (
            select(BrokerBranchFlowRow, InstrumentRow.symbol)
            .join(
                InstrumentRow,
                BrokerBranchFlowRow.instrument_id == InstrumentRow.id,
            )
            .where(InstrumentRow.symbol == symbol.upper())
            .order_by(
                BrokerBranchFlowRow.trading_date,
                BrokerBranchFlowRow.branch_code,
                BrokerBranchFlowRow.revision,
            )
        )
        if start:
            statement = statement.where(BrokerBranchFlowRow.trading_date >= start)
        if end:
            statement = statement.where(BrokerBranchFlowRow.trading_date <= end)
        rows = (await self.session.execute(statement)).all()
        return [
            BrokerBranchFlow(
                symbol=row_symbol,
                trading_date=row.trading_date,
                branch_code=row.branch_code,
                branch_name=row.branch_name,
                buy_shares=row.buy_shares,
                sell_shares=row.sell_shares,
                buy_amount=row.buy_amount,
                sell_amount=row.sell_amount,
                day_trade_buy_shares=row.day_trade_buy_shares,
                day_trade_sell_shares=row.day_trade_sell_shares,
                available_at=row.available_at,
                source=row.source,
                revision=row.revision,
            )
            for row, row_symbol in rows
        ]

    async def upsert_broker_branch_flows(
        self,
        flows: list[BrokerBranchFlow],
    ) -> int:
        if not flows:
            return 0
        symbol = flows[0].symbol.upper()
        if any(item.symbol.upper() != symbol for item in flows):
            raise ValueError("one branch upsert may contain only one symbol")
        instrument_id = await self._ensure_instrument(symbol)
        inserted = 0
        for item in flows:
            values = item.model_dump(mode="python", exclude={"symbol"})
            payload_hash = self._payload_hash(item.model_dump(mode="json"))
            statement = (
                insert(BrokerBranchFlowRow)
                .values(
                    instrument_id=instrument_id,
                    **values,
                    payload_hash=payload_hash,
                )
                .on_conflict_do_update(
                    constraint="uq_broker_branch_flow_version",
                    set_={
                        **values,
                        "payload_hash": payload_hash,
                    },
                )
            )
            result = await self.session.execute(statement)
            inserted += result.rowcount or 0
        await self.session.commit()
        return inserted

    async def save_chip_flow(self, snapshot: ChipFlowSnapshot) -> None:
        payload = snapshot.model_dump(mode="json")
        statement = (
            insert(ChipFlowSnapshotRow)
            .values(
                symbol=snapshot.symbol,
                as_of=snapshot.as_of,
                strategy_version=snapshot.strategy_version,
                score=snapshot.score,
                confidence=snapshot.confidence,
                payload=payload,
            )
            .on_conflict_do_update(
                constraint="uq_chip_flow_snapshot",
                set_={
                    "score": snapshot.score,
                    "confidence": snapshot.confidence,
                    "payload": payload,
                },
            )
        )
        await self.session.execute(statement)
        await self.session.commit()

    async def list_market_observations(
        self,
        market: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[MarketObservation]:
        statement = (
            select(MarketObservationRow)
            .where(MarketObservationRow.market == market.upper())
            .order_by(
                MarketObservationRow.trading_date,
                MarketObservationRow.revision,
            )
        )
        if start:
            statement = statement.where(MarketObservationRow.trading_date >= start)
        if end:
            statement = statement.where(MarketObservationRow.trading_date <= end)
        rows = (await self.session.execute(statement)).scalars().all()
        return [
            MarketObservation(
                market=row.market,
                trading_date=row.trading_date,
                index_close=row.index_close,
                total_issues=row.total_issues,
                advancing_issues=row.advancing_issues,
                declining_issues=row.declining_issues,
                unchanged_issues=row.unchanged_issues,
                above_ma20_issues=row.above_ma20_issues,
                above_ma60_issues=row.above_ma60_issues,
                new_high_52w_issues=row.new_high_52w_issues,
                new_low_52w_issues=row.new_low_52w_issues,
                up_volume=row.up_volume,
                down_volume=row.down_volume,
                turnover_value=row.turnover_value,
                foreign_net_flow=row.foreign_net_flow,
                investment_trust_net_flow=row.investment_trust_net_flow,
                dealer_net_flow=row.dealer_net_flow,
                futures_net_open_interest=row.futures_net_open_interest,
                margin_balance=row.margin_balance,
                short_balance=row.short_balance,
                available_at=row.available_at,
                source=row.source,
                revision=row.revision,
            )
            for row in rows
        ]

    async def upsert_market_observations(
        self,
        observations: list[MarketObservation],
    ) -> int:
        if not observations:
            return 0
        market = observations[0].market.upper()
        if any(item.market.upper() != market for item in observations):
            raise ValueError(
                "one upsert_market_observations call may contain only one market"
            )
        inserted = 0
        for item in observations:
            raw = item.model_dump(mode="json")
            values = item.model_dump(mode="python")
            values["market"] = market
            payload_hash = hashlib.sha256(
                json.dumps(raw, sort_keys=True).encode("utf-8")
            ).hexdigest()
            statement = (
                insert(MarketObservationRow)
                .values(
                    **values,
                    payload_hash=payload_hash,
                )
                .on_conflict_do_nothing(
                    constraint="uq_market_observation_version",
                )
            )
            result = await self.session.execute(statement)
            inserted += result.rowcount or 0
        await self.session.commit()
        return inserted

    async def save_market_environment(
        self,
        snapshot: MarketEnvironmentSnapshot,
    ) -> None:
        payload = snapshot.model_dump(mode="json")
        statement = (
            insert(MarketEnvironmentSnapshotRow)
            .values(
                market=snapshot.market,
                as_of=snapshot.as_of,
                strategy_version=snapshot.strategy_version,
                score=snapshot.score,
                regime=snapshot.regime.value,
                confidence=snapshot.confidence,
                payload=payload,
            )
            .on_conflict_do_update(
                constraint="uq_market_environment_snapshot",
                set_={
                    "score": snapshot.score,
                    "regime": snapshot.regime.value,
                    "confidence": snapshot.confidence,
                    "payload": payload,
                },
            )
        )
        await self.session.execute(statement)
        await self.session.commit()

    async def create_backtest_job(self, spec: BacktestSpec) -> BacktestJob:
        row = BacktestJobRow(
            status=JobStatus.QUEUED.value,
            spec=spec.model_dump(mode="json"),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._job_from_row(row)

    async def get_backtest_job(self, job_id: UUID) -> BacktestJob | None:
        row = await self.session.get(BacktestJobRow, job_id)
        return self._job_from_row(row) if row else None

    async def claim_next_backtest_job(self) -> BacktestJob | None:
        statement = (
            select(BacktestJobRow)
            .where(BacktestJobRow.status == JobStatus.QUEUED.value)
            .order_by(BacktestJobRow.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        row.status = JobStatus.RUNNING.value
        row.started_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(row)
        return self._job_from_row(row)

    async def complete_backtest_job(
        self,
        job_id: UUID,
        report: BacktestReport,
    ) -> None:
        row = await self.session.get(BacktestJobRow, job_id)
        if row is None:
            raise LookupError(f"backtest job {job_id} not found")
        row.status = JobStatus.COMPLETED.value
        row.result = report.model_dump(mode="json")
        row.completed_at = datetime.now(UTC)
        await self.session.commit()

    async def fail_backtest_job(self, job_id: UUID, error: str) -> None:
        row = await self.session.get(BacktestJobRow, job_id)
        if row is None:
            raise LookupError(f"backtest job {job_id} not found")
        row.status = JobStatus.FAILED.value
        row.error = error[:4000]
        row.completed_at = datetime.now(UTC)
        await self.session.commit()

    async def create_api_key(
        self,
        *,
        name: str,
        key_hash: str,
        key_prefix: str,
    ) -> ApiKey:
        row = ApiKeyRow(name=name, key_hash=key_hash, key_prefix=key_prefix)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._api_key_from_row(row)

    async def get_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        statement = select(ApiKeyRow).where(ApiKeyRow.key_hash == key_hash)
        row = (await self.session.execute(statement)).scalar_one_or_none()
        return self._api_key_from_row(row) if row else None

    async def list_api_keys(self) -> list[ApiKey]:
        statement = select(ApiKeyRow).order_by(ApiKeyRow.created_at.desc())
        rows = (await self.session.execute(statement)).scalars().all()
        return [self._api_key_from_row(row) for row in rows]

    async def revoke_api_key(self, key_id: UUID) -> bool:
        row = await self.session.get(ApiKeyRow, key_id)
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = datetime.now(UTC)
        await self.session.commit()
        return True

    async def touch_api_key_last_used(self, key_id: UUID) -> None:
        row = await self.session.get(ApiKeyRow, key_id)
        if row is None:
            return
        row.last_used_at = datetime.now(UTC)
        await self.session.commit()

    async def search_instruments(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[Instrument]:
        normalized = query.strip()
        if not normalized:
            return []
        pattern = f"%{normalized}%"
        statement = (
            select(InstrumentRow)
            .where(
                or_(
                    InstrumentRow.symbol.ilike(pattern),
                    InstrumentRow.name.ilike(pattern),
                )
            )
            .limit(max(limit, 1) * 5)
        )
        rows = (await self.session.execute(statement)).scalars().all()

        def rank(row: InstrumentRow) -> tuple[int, str]:
            symbol = row.symbol.upper()
            name = row.name or ""
            needle = normalized.upper()
            if symbol == needle:
                score = 0
            elif symbol.startswith(needle):
                score = 1
            elif name == normalized:
                score = 2
            elif name.startswith(normalized):
                score = 3
            else:
                score = 4
            return (score, symbol)

        ranked = sorted(rows, key=rank)[:limit]
        return [
            Instrument(symbol=row.symbol, name=row.name, market=row.market)
            for row in ranked
        ]

    @staticmethod
    def _api_key_from_row(row: ApiKeyRow) -> ApiKey:
        return ApiKey(
            id=row.id,
            name=row.name,
            key_prefix=row.key_prefix,
            created_at=row.created_at,
            revoked_at=row.revoked_at,
            last_used_at=row.last_used_at,
        )

    @staticmethod
    def _job_from_row(row: BacktestJobRow) -> BacktestJob:
        return BacktestJob(
            id=row.id,
            status=JobStatus(row.status),
            spec=BacktestSpec.model_validate(row.spec),
            result=BacktestReport.model_validate(row.result) if row.result else None,
            error=row.error,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )

    async def _ensure_instrument(self, symbol: str) -> UUID:
        statement = (
            insert(InstrumentRow)
            .values(symbol=symbol, name=None, market=None)
            .on_conflict_do_update(
                index_elements=[InstrumentRow.symbol],
                set_={"symbol": symbol},
            )
            .returning(InstrumentRow.id)
        )
        return (await self.session.execute(statement)).scalar_one()

    @staticmethod
    def _payload_hash(payload: dict) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
