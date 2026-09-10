from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

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
    NewsItem,
    SignalSnapshot,
)


class MemoryQuantRepository:
    """Test and demo repository with the same contract as PostgreSQL."""

    def __init__(self) -> None:
        self.bars: dict[str, list[Bar]] = {}
        self.signals: list[SignalSnapshot] = []
        self.market_observations: dict[str, list[MarketObservation]] = {}
        self.market_environments: list[MarketEnvironmentSnapshot] = []
        self.institutional_flows: dict[str, list[InstitutionalFlow]] = {}
        self.branch_flows: dict[str, list[BrokerBranchFlow]] = {}
        self.chip_flows: list[ChipFlowSnapshot] = []
        self.jobs: dict[UUID, BacktestJob] = {}
        self.api_keys: dict[UUID, ApiKey] = {}
        self._api_key_hashes: dict[UUID, str] = {}
        self.instruments: dict[str, Instrument] = {}
        self.news_items: dict[str, list[NewsItem]] = {}

    async def get_data_status(self) -> DataStatus:
        bar_series: list[DataSeriesStatus] = []
        for symbol, bars in self.bars.items():
            sources = sorted({bar.source for bar in bars})
            for source in sources:
                subset = [bar for bar in bars if bar.source == source]
                bar_series.append(
                    DataSeriesStatus(
                        key=symbol,
                        source=source,
                        first_date=min(bar.trading_date for bar in subset),
                        latest_date=max(bar.trading_date for bar in subset),
                        observations=len(subset),
                        adjusted_observations=sum(
                            bar.adjusted_close is not None for bar in subset
                        ),
                        is_real=source != "synthetic_demo",
                    )
                )
        market_series: list[DataSeriesStatus] = []
        for market, observations in self.market_observations.items():
            sources = sorted({item.source for item in observations})
            for source in sources:
                subset = [item for item in observations if item.source == source]
                market_series.append(
                    DataSeriesStatus(
                        key=market,
                        source=source,
                        first_date=min(item.trading_date for item in subset),
                        latest_date=max(item.trading_date for item in subset),
                        observations=len(subset),
                        is_real=source != "synthetic_demo",
                    )
                )
        return DataStatus(
            generated_at=datetime.now(UTC),
            real_data_ready=any(
                item.is_real and item.observations >= 60 for item in bar_series
            ),
            bar_series=bar_series,
            market_series=market_series,
            institutional_series=self._series_status(self.institutional_flows),
            branch_series=self._series_status(self.branch_flows),
        )

    async def upsert_bars(
        self,
        bars: list[Bar],
        *,
        instrument_name: str | None = None,
        market: str | None = None,
    ) -> int:
        if bars and (instrument_name is not None or market is not None):
            symbol = bars[0].symbol.upper()
            existing = self.instruments.get(symbol)
            self.instruments[symbol] = Instrument(
                symbol=symbol,
                name=instrument_name if instrument_name is not None else (
                    existing.name if existing else None
                ),
                market=market if market is not None else (existing.market if existing else None),
            )
        for bar in bars:
            symbol_bars = self.bars.setdefault(bar.symbol.upper(), [])
            symbol_bars = [
                existing
                for existing in symbol_bars
                if not (
                    existing.trading_date == bar.trading_date
                    and existing.source == bar.source
                    and existing.revision == bar.revision
                )
            ]
            symbol_bars.append(bar)
            self.bars[bar.symbol.upper()] = symbol_bars
        return len(bars)

    async def list_bars(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[Bar]:
        result = self.bars.get(symbol.upper(), [])
        if start:
            result = [bar for bar in result if bar.trading_date >= start]
        if end:
            result = [bar for bar in result if bar.trading_date <= end]
        return sorted(result, key=lambda bar: bar.trading_date)

    async def save_signal(self, snapshot: SignalSnapshot) -> None:
        self.signals.append(snapshot)

    async def upsert_institutional_flows(
        self,
        flows: list[InstitutionalFlow],
    ) -> int:
        for item in flows:
            existing = self.institutional_flows.setdefault(item.symbol.upper(), [])
            existing = [
                row
                for row in existing
                if not (
                    row.trading_date == item.trading_date
                    and row.source == item.source
                    and row.revision == item.revision
                )
            ]
            self.institutional_flows[item.symbol.upper()] = [*existing, item]
        return len(flows)

    async def list_institutional_flows(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[InstitutionalFlow]:
        result = self.institutional_flows.get(symbol.upper(), [])
        if start:
            result = [item for item in result if item.trading_date >= start]
        if end:
            result = [item for item in result if item.trading_date <= end]
        return sorted(result, key=lambda item: item.trading_date)

    async def upsert_broker_branch_flows(
        self,
        flows: list[BrokerBranchFlow],
    ) -> int:
        for item in flows:
            existing = self.branch_flows.setdefault(item.symbol.upper(), [])
            existing = [
                row
                for row in existing
                if not (
                    row.trading_date == item.trading_date
                    and row.branch_code == item.branch_code
                    and row.source == item.source
                    and row.revision == item.revision
                )
            ]
            self.branch_flows[item.symbol.upper()] = [*existing, item]
        return len(flows)

    async def list_broker_branch_flows(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[BrokerBranchFlow]:
        result = self.branch_flows.get(symbol.upper(), [])
        if start:
            result = [item for item in result if item.trading_date >= start]
        if end:
            result = [item for item in result if item.trading_date <= end]
        return sorted(result, key=lambda item: (item.trading_date, item.branch_code))

    async def save_chip_flow(self, snapshot: ChipFlowSnapshot) -> None:
        self.chip_flows.append(snapshot)

    async def upsert_market_observations(
        self,
        observations: list[MarketObservation],
    ) -> int:
        for observation in observations:
            market_items = self.market_observations.setdefault(
                observation.market.upper(),
                [],
            )
            market_items = [
                existing
                for existing in market_items
                if not (
                    existing.trading_date == observation.trading_date
                    and existing.source == observation.source
                    and existing.revision == observation.revision
                )
            ]
            market_items.append(observation)
            self.market_observations[observation.market.upper()] = market_items
        return len(observations)

    async def list_market_observations(
        self,
        market: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[MarketObservation]:
        result = self.market_observations.get(market.upper(), [])
        if start:
            result = [item for item in result if item.trading_date >= start]
        if end:
            result = [item for item in result if item.trading_date <= end]
        return sorted(result, key=lambda item: item.trading_date)

    async def save_market_environment(
        self,
        snapshot: MarketEnvironmentSnapshot,
    ) -> None:
        self.market_environments.append(snapshot)

    async def create_backtest_job(self, spec: BacktestSpec) -> BacktestJob:
        job = BacktestJob(
            id=uuid4(),
            status=JobStatus.QUEUED,
            spec=spec,
            created_at=datetime.now(UTC),
        )
        self.jobs[job.id] = job
        return job

    async def get_backtest_job(self, job_id: UUID) -> BacktestJob | None:
        return self.jobs.get(job_id)

    async def claim_next_backtest_job(self) -> BacktestJob | None:
        queued = next(
            (job for job in self.jobs.values() if job.status == JobStatus.QUEUED),
            None,
        )
        if not queued:
            return None
        updated = queued.model_copy(
            update={
                "status": JobStatus.RUNNING,
                "started_at": datetime.now(UTC),
            }
        )
        self.jobs[updated.id] = updated
        return updated

    async def complete_backtest_job(
        self,
        job_id: UUID,
        report: BacktestReport,
    ) -> None:
        current = self.jobs[job_id]
        self.jobs[job_id] = current.model_copy(
            update={
                "status": JobStatus.COMPLETED,
                "result": report,
                "completed_at": datetime.now(UTC),
            }
        )

    async def fail_backtest_job(self, job_id: UUID, error: str) -> None:
        current = self.jobs[job_id]
        self.jobs[job_id] = current.model_copy(
            update={
                "status": JobStatus.FAILED,
                "error": error,
                "completed_at": datetime.now(UTC),
            }
        )

    async def create_api_key(
        self,
        *,
        name: str,
        key_hash: str,
        key_prefix: str,
    ) -> ApiKey:
        key = ApiKey(
            id=uuid4(),
            name=name,
            key_prefix=key_prefix,
            created_at=datetime.now(UTC),
        )
        self._api_key_hashes[key.id] = key_hash
        self.api_keys[key.id] = key
        return key

    async def get_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        for key_id, stored_hash in self._api_key_hashes.items():
            if stored_hash == key_hash:
                return self.api_keys.get(key_id)
        return None

    async def list_api_keys(self) -> list[ApiKey]:
        return sorted(
            self.api_keys.values(), key=lambda key: key.created_at, reverse=True
        )

    async def revoke_api_key(self, key_id: UUID) -> bool:
        key = self.api_keys.get(key_id)
        if key is None or key.revoked_at is not None:
            return False
        self.api_keys[key_id] = key.model_copy(update={"revoked_at": datetime.now(UTC)})
        return True

    async def touch_api_key_last_used(self, key_id: UUID) -> None:
        key = self.api_keys.get(key_id)
        if key is None:
            return
        self.api_keys[key_id] = key.model_copy(
            update={"last_used_at": datetime.now(UTC)}
        )

    async def search_instruments(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[Instrument]:
        normalized = query.strip()
        if not normalized:
            return []
        needle = normalized.upper()

        def rank(instrument: Instrument) -> tuple[int, str] | None:
            symbol = instrument.symbol.upper()
            name = instrument.name or ""
            if needle not in symbol and normalized not in name:
                return None
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

        scored = [
            (rank(instrument), instrument)
            for instrument in self.instruments.values()
        ]
        matches = sorted(
            (item for item in scored if item[0] is not None),
            key=lambda item: item[0],
        )
        return [instrument for _, instrument in matches[:limit]]

    async def list_news_items(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[NewsItem]:
        result = self.news_items.get(symbol.upper(), [])
        if start:
            start_dt = datetime.combine(start, datetime.min.time(), UTC)
            result = [item for item in result if item.published_at >= start_dt]
        if end:
            end_dt = datetime.combine(end, datetime.max.time(), UTC)
            result = [item for item in result if item.published_at <= end_dt]
        return sorted(result, key=lambda item: item.published_at, reverse=True)

    async def upsert_news_items(self, items: list[NewsItem]) -> int:
        for item in items:
            existing = self.news_items.setdefault(item.symbol.upper(), [])
            existing = [
                row
                for row in existing
                if not (
                    row.url == item.url
                    and row.source == item.source
                    and row.revision == item.revision
                )
            ]
            self.news_items[item.symbol.upper()] = [*existing, item]
        return len(items)

    @staticmethod
    def _series_status(
        records: dict[str, list[InstitutionalFlow] | list[BrokerBranchFlow]],
    ) -> list[DataSeriesStatus]:
        result: list[DataSeriesStatus] = []
        for symbol, items in records.items():
            for source in sorted({item.source for item in items}):
                subset = [item for item in items if item.source == source]
                result.append(
                    DataSeriesStatus(
                        key=symbol,
                        source=source,
                        first_date=min(item.trading_date for item in subset),
                        latest_date=max(item.trading_date for item in subset),
                        observations=len(subset),
                        is_real=source != "synthetic_demo",
                    )
                )
        return result
