from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Select, case, func, or_, select, text, update
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
    FetchCoverage,
    InstitutionalFlow,
    Instrument,
    JobStatus,
    MarketDataStatus,
    MarketEnvironmentSnapshot,
    MarketFlowDay,
    MarketObservation,
    MarketStockDay,
    NewsItem,
    SignalSnapshot,
    SyncJob,
    SyncJobKind,
    User,
)
from quant_signal.infrastructure.db.models import (
    ApiKeyRow,
    BacktestJobRow,
    BrokerBranchFlowRow,
    ChipFlowSnapshotRow,
    DailyBarRow,
    FetchCoverageRow,
    InstitutionalFlowRow,
    InstrumentRow,
    MarketEnvironmentSnapshotRow,
    MarketObservationRow,
    MarketStockDayRow,
    NewsItemRow,
    SignalSnapshotRow,
    SyncJobRow,
    UsageCounterRow,
    UserRow,
    UserSessionRow,
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

    async def list_news_items(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[NewsItem]:
        statement = (
            select(NewsItemRow, InstrumentRow.symbol)
            .join(InstrumentRow, NewsItemRow.instrument_id == InstrumentRow.id)
            .where(InstrumentRow.symbol == symbol.upper())
            .order_by(NewsItemRow.published_at.desc())
        )
        if start:
            statement = statement.where(
                NewsItemRow.published_at >= datetime.combine(start, datetime.min.time(), UTC)
            )
        if end:
            statement = statement.where(
                NewsItemRow.published_at
                <= datetime.combine(end, datetime.max.time(), UTC)
            )
        rows = (await self.session.execute(statement)).all()
        return [
            NewsItem(
                symbol=row_symbol,
                headline=row.headline,
                url=row.url,
                source=row.source,
                published_at=row.published_at,
                sentiment_score=row.sentiment_score,
                sentiment_label=row.sentiment_label,
                retrieved_at=row.retrieved_at,
                revision=row.revision,
            )
            for row, row_symbol in rows
        ]

    async def upsert_news_items(self, items: list[NewsItem]) -> int:
        if not items:
            return 0
        symbol = items[0].symbol.upper()
        if any(item.symbol.upper() != symbol for item in items):
            raise ValueError("one news upsert may contain only one symbol")
        instrument_id = await self._ensure_instrument(symbol)
        inserted = 0
        for item in items:
            values = item.model_dump(mode="python", exclude={"symbol"})
            statement = (
                insert(NewsItemRow)
                .values(instrument_id=instrument_id, **values)
                .on_conflict_do_update(
                    constraint="uq_news_item_version",
                    set_=values,
                )
            )
            result = await self.session.execute(statement)
            inserted += result.rowcount or 0
        await self.session.commit()
        return inserted

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

    async def list_fetch_coverage(
        self,
        source: str,
        dataset: str,
        symbol: str,
        periods: list[str],
    ) -> dict[str, FetchCoverage]:
        if not periods:
            return {}
        statement = select(FetchCoverageRow).where(
            FetchCoverageRow.source == source,
            FetchCoverageRow.dataset == dataset,
            FetchCoverageRow.symbol == symbol.upper(),
            FetchCoverageRow.period.in_(periods),
        )
        rows = (await self.session.execute(statement)).scalars().all()
        return {
            row.period: FetchCoverage(
                source=row.source,
                dataset=row.dataset,
                symbol=row.symbol,
                period=row.period,
                status=row.status,  # type: ignore[arg-type]
                row_count=row.row_count,
                fetched_at=row.fetched_at,
            )
            for row in rows
        }

    async def record_fetch_coverage(self, entries: list[FetchCoverage]) -> None:
        if not entries:
            return
        for entry in entries:
            values = {
                "source": entry.source,
                "dataset": entry.dataset,
                "symbol": entry.symbol.upper(),
                "period": entry.period,
                "status": entry.status,
                "row_count": entry.row_count,
                "fetched_at": entry.fetched_at,
            }
            statement = (
                insert(FetchCoverageRow)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["source", "dataset", "symbol", "period"],
                    set_={
                        "status": entry.status,
                        "row_count": entry.row_count,
                        "fetched_at": entry.fetched_at,
                    },
                )
            )
            await self.session.execute(statement)
        await self.session.commit()

    async def enqueue_sync_job(
        self,
        kind: SyncJobKind,
        symbol: str,
        *,
        lookback_days: int = 500,
    ) -> tuple[SyncJob, bool]:
        normalized = symbol.upper()
        statement = (
            insert(SyncJobRow)
            .values(
                kind=kind.value,
                symbol=normalized,
                status=JobStatus.QUEUED.value,
                lookback_days=lookback_days,
            )
            .on_conflict_do_nothing(
                index_elements=["kind", "symbol"],
                index_where=text("status IN ('queued', 'running')"),
            )
            .returning(SyncJobRow.id)
        )
        created_id = (await self.session.execute(statement)).scalar_one_or_none()
        await self.session.commit()
        if created_id is not None:
            row = await self.session.get(SyncJobRow, created_id)
            assert row is not None
            return self._sync_job_from_row(row), True
        existing = (
            await self.session.execute(
                select(SyncJobRow).where(
                    SyncJobRow.kind == kind.value,
                    SyncJobRow.symbol == normalized,
                    SyncJobRow.status.in_(
                        [JobStatus.QUEUED.value, JobStatus.RUNNING.value]
                    ),
                )
            )
        ).scalar_one()
        return self._sync_job_from_row(existing), False

    async def claim_next_sync_job(self) -> SyncJob | None:
        statement = (
            select(SyncJobRow)
            .where(SyncJobRow.status == JobStatus.QUEUED.value)
            .order_by(SyncJobRow.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        row.status = JobStatus.RUNNING.value
        row.started_at = datetime.now(UTC)
        row.attempts += 1
        await self.session.commit()
        await self.session.refresh(row)
        return self._sync_job_from_row(row)

    async def complete_sync_job(self, job_id: UUID) -> None:
        row = await self.session.get(SyncJobRow, job_id)
        if row is None:
            raise LookupError(f"sync job {job_id} not found")
        row.status = JobStatus.COMPLETED.value
        row.error = None
        row.completed_at = datetime.now(UTC)
        await self.session.commit()

    async def fail_sync_job(self, job_id: UUID, error: str, *, retry: bool) -> None:
        row = await self.session.get(SyncJobRow, job_id)
        if row is None:
            raise LookupError(f"sync job {job_id} not found")
        row.error = error[:4000]
        if retry:
            row.status = JobStatus.QUEUED.value
        else:
            row.status = JobStatus.FAILED.value
            row.completed_at = datetime.now(UTC)
        await self.session.commit()

    async def requeue_stale_sync_jobs(self, *, older_than_seconds: float) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        result = await self.session.execute(
            update(SyncJobRow)
            .where(
                SyncJobRow.status == JobStatus.RUNNING.value,
                SyncJobRow.started_at < cutoff,
            )
            .values(status=JobStatus.QUEUED.value)
        )
        await self.session.commit()
        return result.rowcount or 0

    async def list_tracked_symbols(self) -> list[str]:
        statement = (
            select(InstrumentRow.symbol)
            .where(InstrumentRow.market.in_(["TW", "TWO"]))
            .order_by(InstrumentRow.symbol)
        )
        return list((await self.session.execute(statement)).scalars().all())

    async def upsert_market_stock_days(self, rows: list[MarketStockDay]) -> int:
        if not rows:
            return 0
        written = 0
        for offset in range(0, len(rows), 500):
            chunk = rows[offset : offset + 500]
            statement = insert(MarketStockDayRow).values(
                [item.model_dump(mode="python") for item in chunk]
            )
            excluded = statement.excluded
            statement = statement.on_conflict_do_update(
                index_elements=["symbol", "trading_date", "source"],
                set_={
                    column: getattr(excluded, column)
                    for column in MarketStockDay.model_fields
                    if column not in ("symbol", "trading_date", "source")
                },
            )
            result = await self.session.execute(statement)
            written += result.rowcount or 0
        await self.session.commit()
        return written

    @staticmethod
    def _market_filters(market: str | None, as_of: date | None) -> list:
        filters: list = []
        if market and market != "ALL":
            filters.append(MarketStockDayRow.market == market)
        if as_of is not None:
            cutoff = datetime.combine(as_of, time.max, ZoneInfo("Asia/Taipei"))
            filters.append(MarketStockDayRow.trading_date <= as_of)
            filters.append(MarketStockDayRow.available_at <= cutoff)
        return filters

    async def list_market_trading_dates(
        self,
        market: str | None,
        *,
        as_of: date | None,
        limit: int,
        require: str | None = None,
    ) -> list[date]:
        statement = (
            select(MarketStockDayRow.trading_date)
            .where(*self._market_filters(market, as_of))
            .group_by(MarketStockDayRow.trading_date)
            .order_by(MarketStockDayRow.trading_date.desc())
            .limit(limit)
        )
        if require is not None:
            # Keep only sessions on which this report column is published.
            column = getattr(MarketStockDayRow, require)
            statement = statement.having(
                func.count(func.distinct(case((column.is_not(None), MarketStockDayRow.market))))
                == func.count(func.distinct(MarketStockDayRow.market))
            )
        return list((await self.session.execute(statement)).scalars().all())

    async def list_market_stock_days(
        self,
        dates: list[date],
        market: str | None,
        *,
        as_of: date | None = None,
    ) -> list[MarketStockDay]:
        if not dates:
            return []
        statement = select(MarketStockDayRow).where(
            MarketStockDayRow.trading_date.in_(dates),
            *self._market_filters(market, as_of),
        )
        rows = (await self.session.execute(statement)).scalars().all()
        return [
            MarketStockDay.model_validate(
                {column: getattr(row, column) for column in MarketStockDay.model_fields}
            )
            for row in rows
        ]

    @staticmethod
    def _reported(column) -> object:
        """True when every market present that day has published this column."""
        return func.count(
            func.distinct(case((column.is_not(None), MarketStockDayRow.market)))
        ) == func.count(func.distinct(MarketStockDayRow.market))

    async def market_flow_totals(
        self,
        dates: list[date],
        market: str | None,
        *,
        as_of: date | None = None,
    ) -> list[MarketFlowDay]:
        if not dates:
            return []
        row = MarketStockDayRow

        def shares(column) -> object:
            return func.coalesce(func.sum(column), 0)

        def value(column) -> object:
            return func.coalesce(func.sum(column * row.close), 0.0)

        statement = (
            select(
                row.trading_date,
                value(row.foreign_net),
                value(row.trust_net),
                value(row.dealer_net),
                shares(row.foreign_net),
                shares(row.trust_net),
                shares(row.dealer_net),
                func.coalesce(func.sum(row.turnover_value), 0.0),
                shares(row.margin_balance),
                shares(row.short_balance),
                func.coalesce(func.sum(case((row.change > 0, 1), else_=0)), 0),
                func.coalesce(func.sum(case((row.change < 0, 1), else_=0)), 0),
                func.coalesce(func.sum(case((row.change == 0, 1), else_=0)), 0),
                self._reported(row.total_net),
                self._reported(row.margin_balance),
            )
            .where(row.trading_date.in_(dates), *self._market_filters(market, as_of))
            .group_by(row.trading_date)
            .order_by(row.trading_date)
        )
        return [
            MarketFlowDay(
                trading_date=r[0],
                foreign_net_value=float(r[1]),
                trust_net_value=float(r[2]),
                dealer_net_value=float(r[3]),
                foreign_net_shares=int(r[4]),
                trust_net_shares=int(r[5]),
                dealer_net_shares=int(r[6]),
                turnover_value=float(r[7]),
                margin_balance=int(r[8]),
                short_balance=int(r[9]),
                advancing=int(r[10]),
                declining=int(r[11]),
                unchanged=int(r[12]),
                institutional_reported=bool(r[13]),
                credit_reported=bool(r[14]),
            )
            for r in (await self.session.execute(statement)).all()
        ]

    async def market_data_status(self) -> list[MarketDataStatus]:
        row = MarketStockDayRow
        summary = (
            await self.session.execute(
                select(
                    row.market,
                    func.min(row.trading_date),
                    func.max(row.trading_date),
                    func.count(func.distinct(row.trading_date)),
                ).group_by(row.market)
            )
        ).all()
        result: list[MarketDataStatus] = []
        for market, first, last, days in summary:
            latest = (
                await self.session.execute(
                    select(
                        func.count(),
                        func.count(row.total_net),
                        func.count(row.margin_balance),
                    ).where(row.market == market, row.trading_date == last)
                )
            ).one()
            total = max(int(latest[0]), 1)
            result.append(
                MarketDataStatus(
                    market=market,
                    first_date=first,
                    last_date=last,
                    trading_days=int(days),
                    stocks_on_last_day=int(latest[0]),
                    institutional_coverage=latest[1] / total,
                    credit_coverage=latest[2] / total,
                )
            )
        return sorted(result, key=lambda item: item.market)

    async def list_market_closes(
        self, dates: list[date], market: str
    ) -> list[tuple[str, date, float, float, int]]:
        """(symbol, date, close, change, volume) without building full models."""
        if not dates:
            return []
        row = MarketStockDayRow
        statement = select(
            row.symbol, row.trading_date, row.close, row.change, row.volume
        ).where(row.trading_date.in_(dates), row.market == market)
        return [
            (r[0], r[1], float(r[2]), float(r[3]), int(r[4]))
            for r in (await self.session.execute(statement)).all()
        ]

    async def has_active_sync_job(self, kind: SyncJobKind) -> bool:
        statement = (
            select(func.count())
            .select_from(SyncJobRow)
            .where(
                SyncJobRow.kind == kind.value,
                SyncJobRow.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            )
        )
        return bool((await self.session.execute(statement)).scalar_one())

    async def upsert_google_user(
        self,
        *,
        google_sub: str,
        email: str,
        name: str | None,
        picture: str | None,
    ) -> User:
        now = datetime.now(UTC)
        statement = (
            insert(UserRow)
            .values(
                google_sub=google_sub,
                email=email,
                name=name,
                picture=picture,
                last_login_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_users_google_sub",
                set_={
                    "email": email,
                    "name": name,
                    "picture": picture,
                    "last_login_at": now,
                },
            )
            .returning(UserRow.id)
        )
        user_id = (await self.session.execute(statement)).scalar_one()
        await self.session.commit()
        row = await self.session.get(UserRow, user_id)
        assert row is not None
        await self.session.refresh(row)
        return self._user_from_row(row)

    async def create_user_session(
        self, *, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> None:
        self.session.add(
            UserSessionRow(token_hash=token_hash, user_id=user_id, expires_at=expires_at)
        )
        # Opportunistic cleanup keeps the table from growing without a scheduler.
        await self.session.execute(
            text("DELETE FROM user_sessions WHERE expires_at < now() - interval '1 day'")
        )
        await self.session.commit()

    async def get_user_by_session(self, token_hash: str, *, now: datetime) -> User | None:
        statement = (
            select(UserRow)
            .join(UserSessionRow, UserSessionRow.user_id == UserRow.id)
            .where(
                UserSessionRow.token_hash == token_hash,
                UserSessionRow.expires_at > now,
                UserRow.is_active.is_(True),
            )
        )
        row = (await self.session.execute(statement)).scalar_one_or_none()
        return self._user_from_row(row) if row else None

    async def delete_user_session(self, token_hash: str) -> None:
        await self.session.execute(
            text("DELETE FROM user_sessions WHERE token_hash = :h"), {"h": token_hash}
        )
        await self.session.commit()

    async def consume_usage(
        self, user_id: UUID, action: str, day: date, *, limit: int
    ) -> tuple[bool, int]:
        """Atomically count one use; refuses (without counting) once ``limit`` is reached."""
        statement = (
            insert(UsageCounterRow)
            .values(user_id=user_id, action=action, day=day, count=1)
            .on_conflict_do_update(
                index_elements=["user_id", "action", "day"],
                set_={"count": UsageCounterRow.count + 1},
                where=UsageCounterRow.count < limit,
            )
            .returning(UsageCounterRow.count)
        )
        if limit <= 0:
            return False, 0
        count = (await self.session.execute(statement)).scalar_one_or_none()
        await self.session.commit()
        if count is not None:
            return True, count
        current = await self.session.get(UsageCounterRow, (user_id, action, day))
        return False, current.count if current else 0

    @staticmethod
    def _user_from_row(row: UserRow) -> User:
        return User(
            id=row.id,
            google_sub=row.google_sub,
            email=row.email,
            name=row.name,
            picture=row.picture,
            is_active=row.is_active,
            created_at=row.created_at,
            last_login_at=row.last_login_at,
        )

    @staticmethod
    def _sync_job_from_row(row: SyncJobRow) -> SyncJob:
        return SyncJob(
            id=row.id,
            kind=SyncJobKind(row.kind),
            symbol=row.symbol,
            status=JobStatus(row.status),
            lookback_days=row.lookback_days,
            attempts=row.attempts,
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
