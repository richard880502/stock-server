from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

import httpx

from quant_signal.domain.models import Bar, CorporateAction, InstitutionalFlow

TWSE_BASE_URL = "https://www.twse.com.tw"
TWSE_SOURCE = "twse_official"
TAIPEI_CLOSE_AVAILABLE_UTC = time(hour=10)


def _number(value: Any) -> float:
    text = str(value or "").strip().replace(",", "")
    if text in {"", "--", "---", "除權", "除息"}:
        raise ValueError(f"not a numeric TWSE value: {value!r}")
    return float(text)


def _integer(value: Any) -> int:
    return int(_number(value))


def _roc_date(value: str) -> date:
    normalized = (
        value.strip()
        .replace("年", "/")
        .replace("月", "/")
        .replace("日", "")
        .replace("-", "/")
    )
    parts = normalized.split("/")
    if len(parts) != 3:
        raise ValueError(f"invalid ROC date: {value!r}")
    return date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))


def _available_at(trading_date: date) -> datetime:
    # Official after-market reports are treated as available after the close.
    return datetime.combine(trading_date, TAIPEI_CLOSE_AVAILABLE_UTC, tzinfo=UTC)


def _month_starts(start: date, end: date) -> list[date]:
    current = start.replace(day=1)
    months: list[date] = []
    while current <= end:
        months.append(current)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def _is_common_stock(code: str) -> bool:
    return len(code) == 4 and code.isdigit() and not code.startswith("0")


def _is_listed_code(code: str) -> bool:
    return len(code) == 4 and code.isdigit()


@dataclass(frozen=True)
class TwseSecurityQuote:
    symbol: str
    name: str
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover_value: float
    direction: int


@dataclass(frozen=True)
class TwseDailyMarket:
    trading_date: date
    index_close: float
    quotes: tuple[TwseSecurityQuote, ...]
    turnover_value: float
    foreign_net_flow: float
    investment_trust_net_flow: float
    dealer_net_flow: float
    margin_balance: float
    short_balance: float
    capital_data_complete: bool


class TwseProvider:
    """Taiwan Stock Exchange official after-market data adapter."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        request_interval_seconds: float = 0.35,
    ) -> None:
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=TWSE_BASE_URL,
            timeout=30,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "quant-signal-server/0.1 (+research; respectful-rate-limit)",
            },
        )
        self.request_interval_seconds = request_interval_seconds
        self.instrument_names: dict[str, str] = {}

    async def __aenter__(self) -> TwseProvider:
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
                    raise ValueError("TWSE returned a non-object response")
                await asyncio.sleep(self.request_interval_seconds)
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                await asyncio.sleep(min(1.5 * (2**attempt), 15))
        raise RuntimeError(f"TWSE request failed after retries: {path}") from last_error

    async def fetch_symbol_bars(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> list[Bar]:
        code = symbol.strip().upper().removesuffix(".TW")
        if not _is_listed_code(code):
            raise ValueError("TWSE listed stock symbols must be four digits, e.g. 2330.TW")
        bars: list[Bar] = []
        for month in _month_starts(start, end):
            payload = await self._get_json(
                "/exchangeReport/STOCK_DAY",
                {
                    "response": "json",
                    "date": month.strftime("%Y%m%d"),
                    "stockNo": code,
                },
            )
            if payload.get("stat") != "OK":
                continue
            title = str(payload.get("title") or "")
            name_match = re.search(rf"\b{re.escape(code)}\s+(.+?)\s+各日成交資訊", title)
            if name_match:
                self.instrument_names[f"{code}.TW"] = name_match.group(1).strip()
            bars.extend(self.parse_monthly_stock(payload, f"{code}.TW"))
        return [bar for bar in bars if start <= bar.trading_date <= end]

    async def fetch_index_bars(
        self,
        *,
        start: date,
        end: date,
    ) -> list[Bar]:
        bars: list[Bar] = []
        for month in _month_starts(start, end):
            payload = await self._get_json(
                "/indicesReport/MI_5MINS_HIST",
                {
                    "response": "json",
                    "date": month.strftime("%Y%m%d"),
                },
            )
            if payload.get("stat") != "OK":
                continue
            bars.extend(self.parse_monthly_index(payload))
        return [bar for bar in bars if start <= bar.trading_date <= end]

    async def fetch_corporate_actions(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> list[CorporateAction]:
        code = symbol.strip().upper().removesuffix(".TW")
        payload = await self._get_json(
            "/exchangeReport/TWT49U",
            {
                "response": "json",
                "startDate": start.strftime("%Y%m%d"),
                "endDate": end.strftime("%Y%m%d"),
            },
        )
        actions = (
            self.parse_corporate_actions(payload, f"{code}.TW")
            if payload.get("stat") == "OK"
            else []
        )
        return [action for action in actions if start <= action.ex_date <= end]

    async def fetch_market_day(self, trading_date: date) -> TwseDailyMarket | None:
        requested = trading_date.strftime("%Y%m%d")
        market_payload = await self._get_json(
            "/rwd/zh/afterTrading/MI_INDEX",
            {
                "date": requested,
                "type": "ALLBUT0999",
                "response": "json",
            },
        )
        if market_payload.get("stat") != "OK":
            return None
        parsed = self.parse_daily_market(market_payload)
        if parsed is None:
            return None
        return await self.enrich_market_day(parsed)

    async def fetch_institutional_flow(
        self,
        symbol: str,
        trading_date: date,
        *,
        include_holding_ratio: bool = True,
    ) -> InstitutionalFlow | None:
        code = symbol.strip().upper().removesuffix(".TW")
        requested = trading_date.strftime("%Y%m%d")
        flow_payload = await self._get_json(
            "/rwd/zh/fund/T86",
            {
                "date": requested,
                "selectType": "ALLBUT0999",
                "response": "json",
            },
        )
        holding_ratio = (
            await self.fetch_foreign_holding_ratio(code, trading_date)
            if include_holding_ratio
            else None
        )
        return self.parse_stock_institutional_flow(
            flow_payload,
            f"{code}.TW",
            trading_date=trading_date,
            foreign_holding_ratio=holding_ratio,
        )

    async def fetch_foreign_holding_ratio(
        self,
        code: str,
        trading_date: date,
    ) -> float | None:
        payload = await self._get_json(
            "/rwd/zh/fund/MI_QFIIS",
            {
                "date": trading_date.strftime("%Y%m%d"),
                "selectType": "ALLBUT0999",
                "response": "json",
            },
        )
        return self.parse_foreign_holding_ratio(payload, code)

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
                return await self.fetch_institutional_flow(
                    symbol,
                    item,
                    include_holding_ratio=False,
                )

        result = await asyncio.gather(*(fetch_one(item) for item in dates))
        flows = [item for item in result if item is not None]
        # The ownership report is much larger than T86. Fetch only the latest
        # sessions needed for the five-day ownership-change factor.
        code = symbol.strip().upper().removesuffix(".TW")
        for index, item in enumerate(flows[-7:], start=max(len(flows) - 7, 0)):
            ratio = await self.fetch_foreign_holding_ratio(code, item.trading_date)
            flows[index] = item.model_copy(update={"foreign_holding_ratio": ratio})
        return flows

    async def enrich_market_day(self, parsed: TwseDailyMarket) -> TwseDailyMarket:
        requested = parsed.trading_date.strftime("%Y%m%d")
        flow_payload = await self._get_json(
            "/rwd/zh/fund/BFI82U",
            {"dayDate": requested, "type": "day", "response": "json"},
        )
        margin_payload = await self._get_json(
            "/rwd/zh/marginTrading/MI_MARGN",
            {"date": requested, "selectType": "MS", "response": "json"},
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

    async def fetch_market_quotes(self, trading_date: date) -> TwseDailyMarket | None:
        payload = await self._get_json(
            "/rwd/zh/afterTrading/MI_INDEX",
            {
                "date": trading_date.strftime("%Y%m%d"),
                "type": "ALLBUT0999",
                "response": "json",
            },
        )
        return self.parse_daily_market(payload) if payload.get("stat") == "OK" else None

    @staticmethod
    def parse_monthly_stock(payload: dict[str, Any], symbol: str) -> list[Bar]:
        result: list[Bar] = []
        for row in payload.get("data", []):
            try:
                trading_date = _roc_date(row[0])
                result.append(
                    Bar(
                        symbol=symbol.upper(),
                        trading_date=trading_date,
                        open=_number(row[3]),
                        high=_number(row[4]),
                        low=_number(row[5]),
                        close=_number(row[6]),
                        adjusted_close=None,
                        volume=_integer(row[1]),
                        available_at=_available_at(trading_date),
                        source=TWSE_SOURCE,
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        return result

    @staticmethod
    def parse_monthly_index(payload: dict[str, Any]) -> list[Bar]:
        result: list[Bar] = []
        for row in payload.get("data", []):
            try:
                trading_date = _roc_date(row[0])
                result.append(
                    Bar(
                        symbol="^TWII",
                        trading_date=trading_date,
                        open=_number(row[1]),
                        high=_number(row[2]),
                        low=_number(row[3]),
                        close=_number(row[4]),
                        adjusted_close=None,
                        volume=0,
                        available_at=_available_at(trading_date),
                        source=TWSE_SOURCE,
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
        code = symbol.upper().removesuffix(".TW")
        result: list[CorporateAction] = []
        for row in payload.get("data", []):
            try:
                if str(row[1]).strip() != code:
                    continue
                ex_date = _roc_date(str(row[0]))
                result.append(
                    CorporateAction(
                        symbol=f"{code}.TW",
                        ex_date=ex_date,
                        previous_close=_number(row[3]),
                        reference_price=_number(row[4]),
                        event_type=str(row[6]).strip(),
                        available_at=_available_at(ex_date),
                        source=TWSE_SOURCE,
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        return result

    @staticmethod
    def parse_daily_market(payload: dict[str, Any]) -> TwseDailyMarket | None:
        requested = str(payload.get("date") or "")
        if len(requested) != 8:
            return None
        trading_date = date.fromisoformat(
            f"{requested[:4]}-{requested[4:6]}-{requested[6:]}"
        )
        tables = payload.get("tables") or []
        index_close: float | None = None
        quote_rows: list[list[Any]] = []
        turnover_value = 0.0
        for table in tables:
            fields = table.get("fields") or []
            data = table.get("data") or []
            if fields and fields[0] == "指數":
                for row in data:
                    if row and row[0] == "發行量加權股價指數":
                        index_close = _number(row[1])
                        break
            if fields and fields[:3] == ["證券代號", "證券名稱", "成交股數"]:
                quote_rows = data
            if fields and fields[:2] == ["成交統計", "成交金額(元)"]:
                for row in data:
                    if row and str(row[0]).startswith("證券合計"):
                        turnover_value = _number(row[1])
                        break
        if index_close is None or not quote_rows or turnover_value <= 0:
            return None

        quotes: list[TwseSecurityQuote] = []
        for row in quote_rows:
            try:
                code = str(row[0]).strip()
                if not _is_common_stock(code):
                    continue
                sign = re.sub(r"<[^>]+>", "", str(row[9])).strip()
                direction = 1 if "+" in sign else -1 if "-" in sign else 0
                quotes.append(
                    TwseSecurityQuote(
                        symbol=f"{code}.TW",
                        name=str(row[1]).strip(),
                        trading_date=trading_date,
                        volume=_integer(row[2]),
                        turnover_value=_number(row[4]),
                        open=_number(row[5]),
                        high=_number(row[6]),
                        low=_number(row[7]),
                        close=_number(row[8]),
                        direction=direction,
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        if not quotes:
            return None
        return TwseDailyMarket(
            trading_date=trading_date,
            index_close=index_close,
            quotes=tuple(quotes),
            turnover_value=turnover_value,
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
        if payload.get("stat") != "OK":
            raise ValueError("institutional flow report unavailable")
        foreign = trust = dealer = 0.0
        found: set[str] = set()
        for row in payload.get("data", []):
            name = str(row[0])
            value = _number(row[3])
            if name.startswith("外資及陸資(不含"):
                foreign = value
                found.add("foreign")
            elif name == "投信":
                trust = value
                found.add("trust")
            elif name.startswith("自營商("):
                dealer += value
                found.add("dealer")
        if found != {"foreign", "trust", "dealer"}:
            raise ValueError("institutional flow report fields changed")
        return foreign, trust, dealer

    @staticmethod
    def parse_stock_institutional_flow(
        payload: dict[str, Any],
        symbol: str,
        *,
        trading_date: date,
        foreign_holding_ratio: float | None = None,
    ) -> InstitutionalFlow | None:
        code = symbol.upper().removesuffix(".TW")
        for row in payload.get("data", []):
            try:
                if str(row[0]).strip() != code:
                    continue
                dealer_buy = _integer(row[12]) + _integer(row[15])
                dealer_sell = _integer(row[13]) + _integer(row[16])
                return InstitutionalFlow(
                    symbol=f"{code}.TW",
                    trading_date=trading_date,
                    foreign_buy=_integer(row[2]),
                    foreign_sell=_integer(row[3]),
                    foreign_net=_integer(row[4]),
                    investment_trust_buy=_integer(row[8]),
                    investment_trust_sell=_integer(row[9]),
                    investment_trust_net=_integer(row[10]),
                    dealer_buy=dealer_buy,
                    dealer_sell=dealer_sell,
                    dealer_net=_integer(row[11]),
                    total_net=_integer(row[18]),
                    foreign_holding_ratio=foreign_holding_ratio,
                    available_at=_available_at(trading_date),
                    source=TWSE_SOURCE,
                )
            except (IndexError, TypeError, ValueError):
                return None
        return None

    @staticmethod
    def parse_foreign_holding_ratio(
        payload: dict[str, Any],
        code: str,
    ) -> float | None:
        for row in payload.get("data", []):
            try:
                if str(row[0]).strip() == code:
                    return _number(row[7])
            except (IndexError, TypeError, ValueError):
                return None
        return None

    @staticmethod
    def parse_margin_balances(payload: dict[str, Any]) -> tuple[float, float]:
        if payload.get("stat") != "OK":
            raise ValueError("margin report unavailable")
        tables = payload.get("tables") or []
        rows = (tables[0].get("data") or []) if tables else []
        margin = short = None
        for row in rows:
            label = str(row[0])
            if label == "融資金額(仟元)":
                margin = _number(row[5]) * 1_000
            elif label == "融券(交易單位)":
                short = _number(row[5])
        if margin is None or short is None:
            raise ValueError("margin report fields changed")
        return margin, short
