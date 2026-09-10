from __future__ import annotations

import asyncio
import re
from datetime import date
from typing import Any

import httpx

from quant_signal.domain.models import Bar, CorporateAction, InstitutionalFlow
from quant_signal.infrastructure.providers.twse import (
    TwseDailyMarket,
    TwseSecurityQuote,
    _available_at,
    _integer,
    _is_common_stock,
    _month_starts,
    _number,
    _roc_date,
)

TPEX_BASE_URL = "https://www.tpex.org.tw"
TPEX_SOURCE = "tpex_official"


class TpexProvider:
    """Taipei Exchange official after-market data adapter."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        request_interval_seconds: float = 0.35,
    ) -> None:
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=TPEX_BASE_URL,
            timeout=30,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "quant-signal-server/0.1 (+research; respectful-rate-limit)",
            },
        )
        self.request_interval_seconds = request_interval_seconds
        self.instrument_names: dict[str, str] = {}
        self._index_month_cache: dict[date, dict[date, float]] = {}

    async def __aenter__(self) -> TpexProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(7):
            try:
                response = await self.client.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("TPEx returned a non-object response")
                await asyncio.sleep(self.request_interval_seconds)
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                await asyncio.sleep(min(1.5 * (2**attempt), 15))
        raise RuntimeError(f"TPEx request failed after retries: {path}") from last_error

    async def fetch_symbol_bars(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> list[Bar]:
        code = symbol.strip().upper().removesuffix(".TWO")
        if len(code) != 4 or not code.isdigit():
            raise ValueError("TPEx stock symbols must be four digits, e.g. 6488.TWO")
        bars: list[Bar] = []
        for month in _month_starts(start, end):
            payload = await self._get_json(
                "/www/zh-tw/afterTrading/tradingStock",
                {
                    "code": code,
                    "date": month.strftime("%Y/%m/%d"),
                    "response": "json",
                },
            )
            if str(payload.get("stat", "ok")).lower() != "ok":
                continue
            tables = payload.get("tables") or []
            if not tables:
                continue
            subtitle = str(tables[0].get("subtitle") or "")
            name_match = re.search(rf"\b{re.escape(code)}\s+(.+?)\s+\d+年", subtitle)
            if name_match:
                self.instrument_names[f"{code}.TWO"] = name_match.group(1).strip()
            bars.extend(self.parse_monthly_stock(payload, f"{code}.TWO"))
        return [bar for bar in bars if start <= bar.trading_date <= end]

    async def fetch_index_bars(self, *, start: date, end: date) -> list[Bar]:
        bars: list[Bar] = []
        for month in _month_starts(start, end):
            payload = await self._get_json(
                "/www/zh-tw/indexInfo/inx",
                {"date": month.strftime("%Y/%m/%d"), "response": "json"},
            )
            bars.extend(self.parse_monthly_index(payload))
        return [bar for bar in bars if start <= bar.trading_date <= end]

    async def fetch_corporate_actions(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> list[CorporateAction]:
        code = symbol.strip().upper().removesuffix(".TWO")
        payload = await self._get_json(
            "/www/zh-tw/bulletin/exDailyQ",
            {
                "startDate": start.strftime("%Y/%m/%d"),
                "endDate": end.strftime("%Y/%m/%d"),
                "response": "json",
            },
        )
        actions = self.parse_corporate_actions(payload, f"{code}.TWO")
        return [action for action in actions if start <= action.ex_date <= end]

    async def fetch_market_quotes(self, trading_date: date) -> TwseDailyMarket | None:
        payload = await self._get_json(
            "/www/zh-tw/afterTrading/dailyQuotes",
            {"date": trading_date.strftime("%Y/%m/%d"), "response": "json"},
        )
        index_close = await self._index_close(trading_date)
        if index_close is None:
            return None
        return self.parse_daily_market(payload, index_close=index_close)

    async def fetch_market_quotes_range(
        self,
        trading_dates: list[date],
        *,
        concurrency: int = 4,
    ) -> dict[date, TwseDailyMarket | None]:
        """Fetch independent daily quote reports concurrently with a small limit."""

        for month in sorted({item.replace(day=1) for item in trading_dates}):
            await self._index_close(month)
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_one(trading_date: date) -> tuple[date, TwseDailyMarket | None]:
            async with semaphore:
                payload = await self._get_json(
                    "/www/zh-tw/afterTrading/dailyQuotes",
                    {
                        "date": trading_date.strftime("%Y/%m/%d"),
                        "response": "json",
                    },
                )
            index_close = self._index_month_cache[
                trading_date.replace(day=1)
            ].get(trading_date)
            if index_close is None:
                return trading_date, None
            return trading_date, self.parse_daily_market(
                payload,
                index_close=index_close,
            )

        return dict(await asyncio.gather(*(fetch_one(item) for item in trading_dates)))

    async def fetch_market_day(self, trading_date: date) -> TwseDailyMarket | None:
        parsed = await self.fetch_market_quotes(trading_date)
        if parsed is None:
            return None
        return await self.enrich_market_day(parsed)

    async def fetch_institutional_flow(
        self,
        symbol: str,
        trading_date: date,
    ) -> InstitutionalFlow | None:
        code = symbol.strip().upper().removesuffix(".TWO")
        payload = await self._get_json(
            "/www/zh-tw/insti/dailyTrade",
            {
                "type": "Daily",
                "sect": "EW",
                "date": trading_date.strftime("%Y/%m/%d"),
                "response": "json",
            },
        )
        return self.parse_stock_institutional_flow(
            payload,
            f"{code}.TWO",
            trading_date=trading_date,
        )

    async def fetch_institutional_flows(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
        concurrency: int = 4,
    ) -> list[InstitutionalFlow]:
        semaphore = asyncio.Semaphore(concurrency)
        dates: list[date] = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                dates.append(current)
            current = date.fromordinal(current.toordinal() + 1)

        async def fetch_one(item: date) -> InstitutionalFlow | None:
            async with semaphore:
                return await self.fetch_institutional_flow(symbol, item)

        result = await asyncio.gather(*(fetch_one(item) for item in dates))
        return [item for item in result if item is not None]

    async def enrich_market_day(self, parsed: TwseDailyMarket) -> TwseDailyMarket:
        requested = parsed.trading_date.strftime("%Y/%m/%d")
        flow_payload = await self._get_json(
            "/www/zh-tw/insti/summary",
            {
                "type": "Daily",
                "prod": "1",
                "date": requested,
                "response": "json",
            },
        )
        margin_payload = await self._get_json(
            "/www/zh-tw/margin/balance",
            {"date": requested, "response": "json"},
        )
        try:
            foreign, trust, dealer = self.parse_institutional_flows(flow_payload)
            margin, short = self.parse_margin_balances(margin_payload)
            complete = True
        except (KeyError, TypeError, ValueError):
            foreign = trust = dealer = margin = short = 0.0
            complete = False
        return TwseDailyMarket(
            trading_date=parsed.trading_date,
            index_close=parsed.index_close,
            quotes=parsed.quotes,
            turnover_value=parsed.turnover_value,
            foreign_net_flow=foreign,
            investment_trust_net_flow=trust,
            dealer_net_flow=dealer,
            margin_balance=margin,
            short_balance=short,
            capital_data_complete=complete,
        )

    async def _index_close(self, trading_date: date) -> float | None:
        month = trading_date.replace(day=1)
        if month not in self._index_month_cache:
            payload = await self._get_json(
                "/www/zh-tw/indexInfo/inx",
                {"date": month.strftime("%Y/%m/%d"), "response": "json"},
            )
            self._index_month_cache[month] = {
                bar.trading_date: bar.close for bar in self.parse_monthly_index(payload)
            }
        return self._index_month_cache[month].get(trading_date)

    @staticmethod
    def parse_monthly_stock(payload: dict[str, Any], symbol: str) -> list[Bar]:
        tables = payload.get("tables") or []
        rows = (tables[0].get("data") or []) if tables else []
        result: list[Bar] = []
        for row in rows:
            try:
                trading_date = _roc_date(str(row[0]))
                result.append(
                    Bar(
                        symbol=symbol.upper(),
                        trading_date=trading_date,
                        open=_number(row[3]),
                        high=_number(row[4]),
                        low=_number(row[5]),
                        close=_number(row[6]),
                        volume=_integer(row[1]) * 1_000,
                        available_at=_available_at(trading_date),
                        source=TPEX_SOURCE,
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        return result

    @staticmethod
    def parse_monthly_index(payload: dict[str, Any]) -> list[Bar]:
        tables = payload.get("tables") or []
        rows = (tables[0].get("data") or []) if tables else []
        result: list[Bar] = []
        for row in rows:
            try:
                trading_date = date.fromisoformat(str(row[0]).replace("/", "-"))
                close = _number(row[4])
                result.append(
                    Bar(
                        symbol="^TWOII",
                        trading_date=trading_date,
                        open=_number(row[1]),
                        high=_number(row[2]),
                        low=_number(row[3]),
                        close=close,
                        adjusted_close=close,
                        volume=0,
                        available_at=_available_at(trading_date),
                        source=TPEX_SOURCE,
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        return result

    @staticmethod
    def parse_corporate_actions(
        payload: dict[str, Any],
        symbol: str,
    ) -> list[CorporateAction]:
        code = symbol.upper().removesuffix(".TWO")
        tables = payload.get("tables") or []
        rows = (tables[0].get("data") or []) if tables else []
        result: list[CorporateAction] = []
        for row in rows:
            try:
                if str(row[1]).strip() != code:
                    continue
                ex_date = _roc_date(str(row[0]))
                result.append(
                    CorporateAction(
                        symbol=f"{code}.TWO",
                        ex_date=ex_date,
                        previous_close=_number(row[3]),
                        reference_price=_number(row[4]),
                        event_type=str(row[8]).strip(),
                        available_at=_available_at(ex_date),
                        source=TPEX_SOURCE,
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        return result

    @staticmethod
    def parse_daily_market(
        payload: dict[str, Any],
        *,
        index_close: float,
    ) -> TwseDailyMarket | None:
        requested = str(payload.get("date") or "")
        if len(requested) != 8:
            return None
        trading_date = date.fromisoformat(
            f"{requested[:4]}-{requested[4:6]}-{requested[6:]}"
        )
        tables = payload.get("tables") or []
        rows = (tables[0].get("data") or []) if tables else []
        quotes: list[TwseSecurityQuote] = []
        turnover = 0.0
        for row in rows:
            try:
                code = str(row[0]).strip()
                if not _is_common_stock(code):
                    continue
                change = str(row[3]).strip()
                direction = 1 if change.startswith("+") else -1 if change.startswith("-") else 0
                value = _number(row[9])
                turnover += value
                quotes.append(
                    TwseSecurityQuote(
                        symbol=f"{code}.TWO",
                        name=str(row[1]).strip(),
                        trading_date=trading_date,
                        open=_number(row[4]),
                        high=_number(row[5]),
                        low=_number(row[6]),
                        close=_number(row[2]),
                        volume=_integer(row[8]),
                        turnover_value=value,
                        direction=direction,
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        if not quotes or turnover <= 0:
            return None
        return TwseDailyMarket(
            trading_date=trading_date,
            index_close=index_close,
            quotes=tuple(quotes),
            turnover_value=turnover,
            foreign_net_flow=0,
            investment_trust_net_flow=0,
            dealer_net_flow=0,
            margin_balance=0,
            short_balance=0,
            capital_data_complete=False,
        )

    @staticmethod
    def parse_institutional_flows(
        payload: dict[str, Any],
    ) -> tuple[float, float, float]:
        tables = payload.get("tables") or []
        rows = (tables[0].get("data") or []) if tables else []
        values: dict[str, float] = {}
        for row in rows:
            name = str(row[0]).strip()
            if "外資及陸資(不含自營商)" in name:
                values["foreign"] = _number(row[3])
            elif name == "投信":
                values["trust"] = _number(row[3])
            elif name == "自營商合計":
                values["dealer"] = _number(row[3])
        if values.keys() != {"foreign", "trust", "dealer"}:
            raise ValueError("TPEx institutional flow report fields changed")
        return values["foreign"], values["trust"], values["dealer"]

    @staticmethod
    def parse_stock_institutional_flow(
        payload: dict[str, Any],
        symbol: str,
        *,
        trading_date: date,
    ) -> InstitutionalFlow | None:
        code = symbol.upper().removesuffix(".TWO")
        tables = payload.get("tables") or []
        rows = (tables[0].get("data") or []) if tables else []
        for row in rows:
            try:
                if str(row[0]).strip() != code:
                    continue
                return InstitutionalFlow(
                    symbol=f"{code}.TWO",
                    trading_date=trading_date,
                    foreign_buy=_integer(row[2]),
                    foreign_sell=_integer(row[3]),
                    foreign_net=_integer(row[4]),
                    investment_trust_buy=_integer(row[11]),
                    investment_trust_sell=_integer(row[12]),
                    investment_trust_net=_integer(row[13]),
                    dealer_buy=_integer(row[20]),
                    dealer_sell=_integer(row[21]),
                    dealer_net=_integer(row[22]),
                    total_net=_integer(row[23]),
                    available_at=_available_at(trading_date),
                    source=TPEX_SOURCE,
                )
            except (IndexError, TypeError, ValueError):
                return None
        return None

    @staticmethod
    def parse_margin_balances(payload: dict[str, Any]) -> tuple[float, float]:
        tables = payload.get("tables") or []
        summary = (tables[0].get("summary") or []) if tables else []
        if len(summary) < 2:
            raise ValueError("TPEx margin summary unavailable")
        margin = _number(summary[1][6]) * 1_000
        short = _number(summary[0][14])
        return margin, short
