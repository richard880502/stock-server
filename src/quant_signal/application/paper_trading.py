"""Paper trading (模擬交易) for signed-in users: accounts, orders, valuation.

A simulation only. Prices come from our own tables (see ``paper_market``); there is no
broker call anywhere in this module. Cash and positions are derived from the fills and
the cash ledger, never edited in place.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from quant_signal.application.paper_actions import (
    list_corporate_actions,
    load_book,
    realized_by_fill,
)
from quant_signal.application.paper_market import (
    SYMBOL_RE,
    MarketView,
    PriceInfo,
    account_start_date,
    benchmark_closes,
    load_market,
    market_of,
    previous_snapshot,
    price_info,
)
from quant_signal.application.paper_ports import PaperRepository
from quant_signal.domain.paper import (
    MAX_ACCOUNTS,
    MAX_LOTS_PER_ORDER,
    AccountCreate,
    AccountDetail,
    AccountPatch,
    AccountSummary,
    Benchmark,
    EquityCurve,
    EquityPoint,
    Fill,
    FillList,
    Order,
    OrderList,
    OrderPreview,
    OrderRequest,
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    Position,
)
from quant_signal.domain.paper_actions import CorporateActionList
from quant_signal.domain.paper_proposals import ProposalRecord
from quant_signal.domain.tw_trading import (
    BOARD_LOT,
    BOND_ETF_TAX_EXEMPT_THROUGH,
    DEFAULT_COSTS,
    LEVERAGED_ETF_LIMIT_PCT,
    PRICE_LIMIT_PCT,
    price_limit_pct_for,
    tick_size,
)
from quant_signal.quant.paper_book import Book
from quant_signal.quant.paper_calendar import (
    SESSION_CLOSE,
    SESSION_OPEN,
    latest_session_day,
    local,
    session_for_order,
)
from quant_signal.quant.paper_matching import (
    compute_fill,
    day_trade_shares,
    estimate_price,
    limit_price_problem,
    limits_for_symbol,
    reserve_for_buy,
    resolve_reference,
    symbol_is_etf,
    to_money,
)

D = Decimal
NO_LIMIT_RESERVE_FACTOR = D("1.10")  # a market buy of a limitless symbol reserves last x 1.10
VOLUME_WARNING_SHARE = 0.01
DONE_STATUSES = ["filled", "cancelled", "expired", "rejected"]


class PaperError(Exception):
    """Base for errors the REST layer maps to HTTP statuses."""


class PaperNotFound(PaperError):
    pass


class PaperConflict(PaperError):
    pass


class PaperInvalid(PaperError):
    pass


def _utc(moment: datetime | None) -> datetime | None:
    return None if moment is None else moment.astimezone(UTC)


def _f(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _pct(numerator: float | int, denominator: float | int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


@dataclass
class Valuation:
    account: PaperAccountRecord
    book: Book
    cash: int
    reserved: int
    working: list[PaperOrderRecord]
    positions: list[Position]
    market_value: int
    equity: int
    prices_as_of: datetime | None


@dataclass
class Evaluation:
    """Outcome of validating one order request (shared by preview and placement)."""

    symbol: str
    name: str
    view: MarketView | None
    shares: int
    session_date: date
    queued: bool
    reject_reason: str | None = None
    reference: Decimal | None = None
    limit_up: Decimal | None = None
    limit_down: Decimal | None = None
    tick: Decimal | None = None
    estimated_price: Decimal | None = None
    last_price: Decimal | None = None
    reserve: int = 0
    reserve_amount: int = 0
    available_cash: int = 0
    estimated_amount: int | None = None
    estimated_commission: int | None = None
    estimated_tax: int | None = None
    estimated_total: int | None = None
    warnings: list[str] = field(default_factory=list)


def _bands(*, etf: bool) -> list[dict]:
    """Tick bands read off ``tw_trading.tick_size`` (never restated by hand)."""
    edges = [D("0"), D("10"), D("50"), D("100"), D("500"), D("1000")]
    bands = []
    for i, low in enumerate(edges):
        probe = low if low > 0 else D("0.01")
        bands.append(
            {
                "min": float(low),
                "max": float(edges[i + 1]) if i + 1 < len(edges) else None,
                "tick": float(tick_size(probe, etf=etf)),
            }
        )
    merged: list[dict] = []
    for band in bands:  # ETF bands collapse where the tick does not change
        if merged and merged[-1]["tick"] == band["tick"]:
            merged[-1]["max"] = band["max"]
        else:
            merged.append(band)
    return merged


def paper_rules(slippage_ticks: int, max_accounts: int) -> dict:
    """The effective cost / tick / limit / slippage rules as data."""
    costs = DEFAULT_COSTS
    limit_pct = float(PRICE_LIMIT_PCT)
    return {
        "board_lot": BOARD_LOT,
        "commission_rate": float(costs.commission_rate),
        "min_commission": costs.min_commission,
        "stock_tax_rate": float(costs.stock_tax_rate),
        "etf_tax_rate": float(costs.etf_tax_rate),
        "bond_etf_tax_exempt_through": BOND_ETF_TAX_EXEMPT_THROUGH.isoformat(),
        "day_trade_tax_rate": float(costs.day_trade_tax_rate),
        "price_limit_pct": limit_pct,
        "slippage_ticks": slippage_ticks,
        "tick_bands": _bands(etf=False),
        "etf_tick_bands": _bands(etf=True),
        "session": {
            "open": SESSION_OPEN.strftime("%H:%M"),
            "close": SESSION_CLOSE.strftime("%H:%M"),
            "timezone": "Asia/Taipei",
        },
        "max_accounts": max_accounts,
        "notes": [
            f"1 張 = {BOARD_LOT:,} 股，僅支援整張、現股做多，不可融資融券或放空。",
            f"手續費 {costs.commission_rate * 100:.4f}% 乘上帳戶折扣，"
            f"每筆最低 NT${costs.min_commission}，小數無條件捨去。",
            f"賣出證交稅：股票 {costs.stock_tax_rate * 100:.1f}%、"
            f"ETF {costs.etf_tax_rate * 100:.1f}%；"
            f"現股當沖（需開啟）股票 {costs.day_trade_tax_rate * 100:.2f}%。",
            f"漲跌幅限制為參考價 ±{limit_pct * 100:.0f}%（槓桿型 ETF 代號尾碼 L 為 "
            f"±{float(LEVERAGED_ETF_LIMIT_PCT) * 100:.0f}%、反向型 ETF 尾碼 R 為 "
            f"±{limit_pct * 100:.0f}%），限價必須落在升降單位上。",
            f"債券 ETF（代號尾碼 B）賣出免徵證交稅，現行法定期限至 "
            f"{BOND_ETF_TAX_EXEMPT_THROUGH.isoformat()}，之後自動依 ETF 稅率計算。",
            "其餘 ETF 多含國外成分、可能沒有漲跌幅限制，無法由代號分辨（例如 0050 實際有 ±10% "
            "漲跌幅，但國外成分 ETF 沒有），因此不檢查漲跌幅、也不判斷鎖漲跌停；"
            "預覽不會顯示漲跌停價，市價買單改以最新成交價的 1.10 倍預留資金。"
            "模擬撮合只用真實成交價，不會產生市場上沒有成交過的價格。",
            f"委託當日有效（ROD），依即時行情撮合，成交價另加 {slippage_ticks} 檔滑價。",
            "非交易時段下的委託會排入下一個交易日開盤撮合；漲停鎖死不會買進、跌停鎖死不會賣出。",
            "全部成交或不成交，不會部分成交；這是模擬交易，不會送出任何真實委託。",
        ],
    }


class PaperService:
    def __init__(
        self,
        repo: PaperRepository,
        *,
        slippage_ticks: int = 1,
        max_accounts: int = MAX_ACCOUNTS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = repo
        self.slippage_ticks = slippage_ticks
        self.max_accounts = max_accounts
        self.clock = clock or (lambda: datetime.now(UTC))

    # --- accounts ------------------------------------------------------------------

    async def _own(self, user_id: UUID, account_id: UUID) -> PaperAccountRecord:
        account = await self.repo.get_paper_account(account_id)
        if account is None or account.archived or account.user_id != user_id:
            raise PaperNotFound("找不到這個模擬帳戶")
        return account

    async def create_account(self, user_id: UUID, body: AccountCreate) -> AccountSummary:
        account = PaperAccountRecord(
            user_id=user_id,
            name=body.name,
            initial_cash=body.initial_cash,
            commission_discount=body.commission_discount,
            allow_day_trade=body.allow_day_trade,
            created_at=self.clock(),
        )
        outcome = await self.repo.create_paper_account(account, max_accounts=self.max_accounts)
        if outcome == "limit":
            raise PaperConflict(f"每位使用者最多 {self.max_accounts} 個模擬帳戶，請先刪除一個")
        if outcome == "duplicate":
            raise PaperConflict(f"已有名為「{account.name}」的模擬帳戶")
        return (await self._summaries([account]))[0]

    async def list_accounts(self, user_id: UUID) -> list[AccountSummary]:
        return await self._summaries(await self.repo.list_paper_accounts(user_id))

    async def get_account(self, user_id: UUID, account_id: UUID) -> AccountDetail:
        account = await self._own(user_id, account_id)
        now = self.clock()
        views = await self._market_for([account])
        valuation = await self._value(account, views, now)
        summary = await self._summary(valuation, now)
        return AccountDetail(
            **summary.model_dump(),
            positions=valuation.positions,
            benchmark=await self._benchmark(account),
        )

    async def update_account(
        self, user_id: UUID, account_id: UUID, patch: AccountPatch
    ) -> AccountSummary:
        account = await self._own(user_id, account_id)
        outcome = await self.repo.update_paper_account(
            account.id,
            name=patch.name,
            commission_discount=patch.commission_discount,
            allow_day_trade=patch.allow_day_trade,
        )
        if outcome == "duplicate":
            raise PaperConflict(f"已有名為「{patch.name}」的模擬帳戶")
        if outcome == "insufficient":
            raise PaperConflict(
                "調高手續費折數後，可用資金不足以預留所有未成交買單的費用，"
                "請先取消部分委託或維持原折數"
            )
        if outcome == "missing":
            raise PaperNotFound("找不到這個模擬帳戶")
        return (await self._summaries([await self._own(user_id, account_id)]))[0]

    async def reset_account(self, user_id: UUID, account_id: UUID) -> AccountSummary:
        account = await self._own(user_id, account_id)
        updated = await self.repo.reset_paper_account(account.id, now=self.clock())
        if updated is None:
            raise PaperNotFound("找不到這個模擬帳戶")
        return (await self._summaries([updated]))[0]

    async def archive_account(self, user_id: UUID, account_id: UUID) -> None:
        account = await self._own(user_id, account_id)
        if not await self.repo.archive_paper_account(account.id, now=self.clock()):
            raise PaperNotFound("找不到這個模擬帳戶")

    # --- valuation -----------------------------------------------------------------

    async def _market_for(
        self, accounts: list[PaperAccountRecord], extra: list[str] | None = None
    ) -> dict[str, MarketView]:
        symbols: set[str] = set(extra or [])
        for account in accounts:
            book = await load_book(self.repo, account.id, account.epoch)
            symbols.update(p.symbol for p in book.open_positions())
        return await load_market(self.repo, sorted(symbols))

    async def _value(
        self, account: PaperAccountRecord, views: dict[str, MarketView], now: datetime
    ) -> Valuation:
        book = await load_book(self.repo, account.id, account.epoch)
        cash, working = await self.repo.paper_cash_and_working(account.id, account.epoch)
        reserved = sum(o.reserved_cash for o in working if o.side == "buy")
        reserved_sells = _reserved_sells(working)
        today = local(now).date()
        rows: list[Position] = []
        total_mv = 0
        newest: datetime | None = None
        priced: list[tuple] = []
        for state in book.open_positions():
            view = views.get(state.symbol)
            info = price_info(view, now) if view else None
            price = info.price if info else state.avg_cost.quantize(D("0.01"))
            market_value = to_money(D(state.shares) * price)
            total_mv += market_value
            priced.append((state, view, info, price, market_value))
            if info and info.time and (newest is None or info.time > newest):
                newest = info.time
        equity = cash + total_mv
        for state, view, info, price, market_value in priced:
            locked_today = 0 if account.allow_day_trade else state.bought_on.get(today, 0)
            available = max(0, state.shares - reserved_sells.get(state.symbol, 0) - locked_today)
            reference = info.reference if info else None
            rows.append(
                Position(
                    symbol=state.symbol,
                    name=(view.name if view and view.name else state.symbol),
                    market=market_of(state.symbol),  # type: ignore[arg-type]
                    shares=state.shares,
                    lots=state.shares // BOARD_LOT,
                    available_shares=available,
                    avg_cost=round(float(state.avg_cost), 2),
                    cost_basis=state.cost,
                    price=float(price),
                    price_time=_utc(info.time) if info else None,
                    price_delayed=True if info is None else info.delayed,
                    market_value=market_value,
                    unrealized_pnl=market_value - state.cost,
                    unrealized_pct=_pct(market_value - state.cost, state.cost)
                    if state.cost
                    else None,
                    day_change_pct=(
                        round((float(price) / float(reference) - 1) * 100, 2) if reference else None
                    ),
                    weight_pct=_pct(market_value, equity) if equity > 0 else 0.0,
                    dividend_income=state.dividends,
                )
            )
        return Valuation(
            account=account,
            book=book,
            cash=cash,
            reserved=reserved,
            working=working,
            positions=rows,
            market_value=total_mv,
            equity=equity,
            prices_as_of=newest,
        )

    async def _summary(self, valuation: Valuation, now: datetime) -> AccountSummary:
        account = valuation.account
        initial = account.initial_cash
        total_pnl = valuation.equity - initial
        realized = valuation.book.realized_total
        unrealized = sum(p.unrealized_pnl for p in valuation.positions)
        day_ref = latest_session_day(now)
        snapshots = await self.repo.list_paper_snapshots(
            account.id, epoch=account.epoch, since=day_ref - timedelta(days=14)
        )
        previous = previous_snapshot(snapshots, day_ref)
        base = previous.equity if previous else initial
        day_pnl = valuation.equity - base
        return AccountSummary(
            id=account.id,
            name=account.name,
            initial_cash=initial,
            cash=valuation.cash,
            available_cash=valuation.cash - valuation.reserved,
            market_value=valuation.market_value,
            equity=valuation.equity,
            unrealized_pnl=unrealized,
            realized_pnl=realized,
            dividend_income=valuation.book.dividend_income,
            total_pnl=total_pnl,
            total_return_pct=_pct(total_pnl, initial),
            day_pnl=day_pnl,
            day_return_pct=_pct(day_pnl, base),
            positions_count=len(valuation.positions),
            working_orders=len(valuation.working),
            commission_discount=float(account.commission_discount),
            allow_day_trade=account.allow_day_trade,
            created_at=_utc(account.created_at),  # type: ignore[arg-type]
            prices_as_of=_utc(valuation.prices_as_of),
        )

    async def _summaries(self, accounts: list[PaperAccountRecord]) -> list[AccountSummary]:
        if not accounts:
            return []
        now = self.clock()
        views = await self._market_for(accounts)
        return [await self._summary(await self._value(a, views, now), now) for a in accounts]

    async def _benchmark(self, account: PaperAccountRecord) -> Benchmark | None:
        start = account_start_date(account)
        closes = await benchmark_closes(self.repo, start - timedelta(days=10))
        if not closes:
            return None
        base_days = [d for d in closes if d <= start]
        newest_day = max(closes)
        if not base_days or newest_day <= max(base_days):
            return None
        base = closes[max(base_days)]
        return Benchmark(
            name="加權指數", return_pct=_pct(closes[newest_day] - base, base), since=start
        )

    # --- orders --------------------------------------------------------------------

    async def evaluate(
        self,
        account: PaperAccountRecord,
        request: OrderRequest,
        *,
        now: datetime,
        valuation: Valuation | None = None,
    ) -> Evaluation:
        symbol = await self._normalize(request.symbol)
        session_date, queued = session_for_order(now)
        shares = request.lots * BOARD_LOT if request.lots > 0 else 0
        views = await load_market(self.repo, [symbol], with_volume=True)
        view = views.get(symbol)
        result = Evaluation(
            symbol=symbol,
            name=(view.name if view and view.name else symbol),
            view=view,
            shares=shares,
            session_date=session_date,
            queued=queued,
        )
        if view is None or not view.known:
            symbol = request.symbol.strip().upper()
            result.reject_reason = (
                f"找不到股票 {symbol}"
                "（目前支援上市櫃普通股與 ETF；ETN、權證、特別股、受益證券尚未收錄）"
            )
            return result
        etf = symbol_is_etf(symbol)
        result.tick = tick_size(
            view.quote.price if view.quote else (view.official_close or D("1")), etf=etf
        )
        quote = view.quote
        result.reference = resolve_reference(
            session_date,
            quote_reference=(D(str(quote.reference)) if quote and quote.reference else None),
            quote_price=(D(str(quote.price)) if quote else None),
            quote_date=view.quote_date,
            official_close=view.official_close,
            official_date=view.official_date,
        )
        limitless = price_limit_pct_for(symbol) is None
        if result.reference is not None and not limitless:
            result.limit_down, result.limit_up = limits_for_symbol(result.reference, symbol)
        info = price_info(view, now)
        last = info.price if info else None
        result.last_price = last
        if request.lots < 1:
            result.reject_reason = "張數必須為 1 張以上"
        elif request.lots > MAX_LOTS_PER_ORDER:
            result.reject_reason = f"單筆委託最多 {MAX_LOTS_PER_ORDER:,} 張"
        elif result.reference is None and not limitless:
            result.reject_reason = "查無參考價，暫時無法計算漲跌停，請稍後再試"
        elif (
            limitless and last is None and request.side == "buy" and request.order_type == "market"
        ):
            result.reject_reason = "查無報價，暫時無法預留資金，請稍後再試或改用限價單"
        elif request.order_type == "limit":
            assert request.limit_price is not None
            result.reject_reason = limit_price_problem(
                request.limit_price,
                etf=etf,
                limit_down=result.limit_down,
                limit_up=result.limit_up,
            )
        result.estimated_price = estimate_price(
            request.side,
            request.order_type,
            request.limit_price,
            last,
            etf=etf,
            slippage_ticks=self.slippage_ticks,
            limit_down=result.limit_down,
            limit_up=result.limit_up,
        )
        valuation = valuation or await self._value(account, {symbol: view}, now)
        result.available_cash = valuation.cash - valuation.reserved
        if shares and result.estimated_price is not None:
            self._estimate(result, account, request, valuation, session_date, now)
        if result.reject_reason is None and shares:
            result.reject_reason = self._funds_or_shares_problem(
                result, account, request, valuation, session_date
            )
        self._warnings(result, request, view, info, now)
        return result

    def _estimate(
        self,
        result: Evaluation,
        account: PaperAccountRecord,
        request: OrderRequest,
        valuation: Valuation,
        session_date: date,
        now: datetime,
    ) -> None:
        price = result.estimated_price
        assert price is not None
        dt_qty = 0
        if request.side == "sell" and account.allow_day_trade:
            book = valuation.book
            dt_qty = day_trade_shares(
                result.shares,
                bought_today=book.bought_on(result.symbol, session_date),
                sold_today=book.sold_on(result.symbol, session_date),
            )
        amounts = compute_fill(
            request.side,
            result.shares,
            price,
            symbol=result.symbol,
            commission_discount=account.commission_discount,
            day_trade_qty=dt_qty,
            trade_date=session_date,
        )
        result.estimated_amount = amounts.amount
        result.estimated_commission = amounts.commission
        result.estimated_tax = amounts.tax
        result.estimated_total = (
            amounts.amount + amounts.commission
            if request.side == "buy"
            else amounts.amount - amounts.commission - amounts.tax
        )
        if request.side == "buy":
            cap = request.limit_price if request.order_type == "limit" else result.limit_up
            if cap is None and result.last_price is not None:  # no daily limit: last x 1.10
                cap = result.last_price * NO_LIMIT_RESERVE_FACTOR
            if cap is not None:
                result.reserve_amount = to_money(D(result.shares) * cap)
                result.reserve = reserve_for_buy(result.shares, cap, account.commission_discount)

    def _funds_or_shares_problem(
        self,
        result: Evaluation,
        account: PaperAccountRecord,
        request: OrderRequest,
        valuation: Valuation,
        session_date: date,
    ) -> str | None:
        if request.side == "buy":
            if result.reserve > result.available_cash:
                return (
                    f"可用資金不足：此單需預留 NT${result.reserve:,}，"
                    f"目前可用 NT${result.available_cash:,}"
                )
            return None
        held = valuation.book.held(result.symbol)
        reserved = _reserved_sells(valuation.working).get(result.symbol, 0)
        locked = (
            0 if account.allow_day_trade else valuation.book.bought_on(result.symbol, session_date)
        )
        available = held - reserved - locked
        if result.shares > available:
            detail = f"持有 {held:,} 股"
            if reserved:
                detail += f"、已掛賣出 {reserved:,} 股"
            if locked:
                detail += f"、當日買進 {locked:,} 股不可賣出（未開啟現股當沖）"
            return f"可賣股數不足：此單 {result.shares:,} 股，{detail}"
        return None

    def _warnings(
        self,
        result: Evaluation,
        request: OrderRequest,
        view: MarketView,
        info: PriceInfo | None,
        now: datetime,
    ) -> None:
        warnings = result.warnings
        if view.avg_volume_shares and result.shares > view.avg_volume_shares * VOLUME_WARNING_SHARE:
            warnings.append("此筆張數超過該股 20 日均量的 1%，實際市場可能無法全數成交")
        if info is not None and info.delayed:
            warnings.append("此檔目前沒有即時報價（約每 10 分鐘更新），撮合可能延遲")
        if result.queued:
            warnings.append(
                f"目前非交易時段，委託將排入 {result.session_date.isoformat()} 開盤後撮合"
            )
        if info is not None and result.limit_up is not None and request.side == "buy":
            if info.price >= result.limit_up:
                warnings.append("此股目前鎖漲停，買單不會成交")
        if info is not None and result.limit_down is not None and request.side == "sell":
            if info.price <= result.limit_down:
                warnings.append("此股目前鎖跌停，賣單不會成交")

    async def _normalize(self, raw: str) -> str:
        symbol = raw.strip().upper()
        if not SYMBOL_RE.fullmatch(symbol):
            raise PaperInvalid("股票代號格式不正確，請輸入如 2330 或 2330.TW")
        if "." in symbol:
            return symbol
        found = [
            *await self.repo.search_market_stocks(symbol, limit=20),
            *await self.repo.search_etf_instruments(symbol, limit=20),
        ]
        matches = [item.symbol for item in found if item.symbol.split(".")[0] == symbol]
        return matches[0] if matches else f"{symbol}.TW"

    @staticmethod
    def _check_shape(request: OrderRequest) -> None:
        if request.order_type == "limit" and request.limit_price is None:
            raise PaperInvalid("限價單必須提供限價")
        if request.order_type == "market" and request.limit_price is not None:
            raise PaperInvalid("市價單不可帶限價")

    async def _proposal_to_accept(
        self, account: PaperAccountRecord, request: OrderRequest, now: datetime
    ) -> ProposalRecord:
        """The pending proposal ``request`` would accept, or ``PaperConflict`` (409)."""
        assert request.proposal_id is not None
        # Read-only on purpose: a commit here would release the account lock taken by
        # ``place_order``. Overdue proposals are caught by the expiry comparison below.
        proposal = await self.repo.get_paper_proposal(  # type: ignore[attr-defined]
            request.proposal_id
        )
        if proposal is None or proposal.account_id != account.id:
            raise PaperConflict("找不到這個提案，或它不屬於這個帳戶")
        if proposal.status != "pending" or proposal.epoch != account.epoch:
            raise PaperConflict("此提案已不是待決定狀態（已採用、拒絕、過期或被新提案取代）")
        if proposal.expires_at <= now:
            raise PaperConflict("此提案已過期")
        symbol = await self._normalize(request.symbol)
        if proposal.symbol != symbol or proposal.side != request.side:
            raise PaperConflict("委託的股票或買賣方向與提案不同，無法以此提案下單")
        return proposal

    async def preview(self, user_id: UUID, account_id: UUID, request: OrderRequest) -> OrderPreview:
        self._check_shape(request)
        account = await self._own(user_id, account_id)
        now = self.clock()
        if request.proposal_id is not None:
            await self._proposal_to_accept(account, request, now)  # validates, changes nothing
        result = await self.evaluate(account, request, now=now)
        info = price_info(result.view, now) if result.view else None
        after = None
        if result.estimated_total is not None:
            after = (
                result.available_cash - result.reserve
                if request.side == "buy"
                else result.available_cash + result.estimated_total
            )
        return OrderPreview(
            ok=result.reject_reason is None,
            reject_reason=result.reject_reason,
            symbol=result.symbol,
            name=result.name,
            side=request.side,
            order_type=request.order_type,
            lots=request.lots,
            shares=result.shares,
            reference_price=_f(result.reference),
            last_price=_f(info.price) if info else None,
            last_price_time=_utc(info.time) if info else None,
            price_delayed=info.delayed if info else bool(result.view and result.view.known),
            limit_up=_f(result.limit_up),
            limit_down=_f(result.limit_down),
            tick=_f(result.tick),
            estimated_price=_f(result.estimated_price),
            estimated_amount=result.estimated_amount,
            estimated_commission=result.estimated_commission,
            estimated_tax=result.estimated_tax,
            estimated_total=result.estimated_total,
            reserve=result.reserve if request.side == "buy" else 0,
            available_cash_after=after,
            session_date=result.session_date,
            will_queue_for_next_session=result.queued,
            warnings=result.warnings,
        )

    async def place_order(self, user_id: UUID, account_id: UUID, request: OrderRequest) -> Order:
        self._check_shape(request)
        account = await self._own(user_id, account_id)
        await self.repo.lock_paper_account(account.id)  # serialise concurrent placements
        # The account may have been reset, renamed or archived while we waited for the lock.
        account = await self._own(user_id, account_id)
        now = self.clock()
        proposal = None
        if request.proposal_id is not None:
            proposal = await self._proposal_to_accept(account, request, now)
        result = await self.evaluate(account, request, now=now)
        rejected = result.reject_reason is not None
        order = PaperOrderRecord(
            id=uuid4(),
            account_id=account.id,
            epoch=account.epoch,
            symbol=result.symbol,
            name=result.name,
            side=request.side,
            order_type=request.order_type,
            limit_price=request.limit_price if request.order_type == "limit" else None,
            lots=request.lots,
            status="rejected" if rejected else "working",
            source="manual" if proposal is None else "ai",
            session_date=result.session_date,
            created_at=now,
            updated_at=now,
            reject_reason=result.reject_reason,
            reserved_cash=0 if rejected or request.side == "sell" else result.reserve,
            reserve_amount=0 if rejected or request.side == "sell" else result.reserve_amount,
            note=request.note,
        )
        if request.lots < 1:
            # Keep the stored row valid (lots > 0) while still showing what was asked.
            order = order.model_copy(update={"lots": 1})
        if proposal is not None and not rejected:
            # The order row and the proposal's "accepted" commit together or not at all.
            if not await self.repo.accept_paper_proposal(  # type: ignore[attr-defined]
                proposal.id, order, now=now
            ):
                raise PaperConflict("此提案已不是待決定狀態（可能剛被採用、過期或取代）")
        else:
            await self.repo.add_paper_order(order)
        return self._order(order, None, None)

    async def cancel_order(self, user_id: UUID, account_id: UUID, order_id: UUID) -> Order:
        account = await self._own(user_id, account_id)
        order = await self.repo.get_paper_order(order_id)
        if order is None or order.account_id != account.id:
            raise PaperNotFound("找不到這筆委託")
        if not await self.repo.finish_paper_order(order.id, status="cancelled", now=self.clock()):
            current = await self.repo.get_paper_order(order.id)
            label = {
                "filled": "已成交",
                "cancelled": "已取消",
                "expired": "已過期",
                "rejected": "已被拒絕",
            }.get(current.status if current else "", "已結束")
            raise PaperConflict(f"此委託{label}，無法取消")
        updated = await self.repo.get_paper_order(order.id)
        assert updated is not None
        return self._order(updated, None, None)

    async def list_orders(
        self,
        user_id: UUID,
        account_id: UUID,
        *,
        status: Literal["working", "done", "all"] = "all",
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
    ) -> OrderList:
        account = await self._own(user_id, account_id)
        statuses = {"working": ["working"], "done": DONE_STATUSES, "all": None}[status]
        orders, total = await self.repo.list_paper_orders(
            account.id,
            statuses=statuses,
            epoch=None if include_archived else account.epoch,
            limit=limit,
            offset=offset,
        )
        fills, realized = await self._fills_and_pnl(account)
        by_order = {f.order_id: f for f in fills}
        return OrderList(
            orders=[
                self._order(o, by_order.get(o.id), realized.get(by_order[o.id].id))
                if o.id in by_order
                else self._order(o, None, None)
                for o in orders
            ],
            has_more=offset + len(orders) < total,
        )

    async def list_fills(
        self,
        user_id: UUID,
        account_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
    ) -> FillList:
        account = await self._own(user_id, account_id)
        fills, realized = await self._fills_and_pnl(account)
        if not include_archived:
            fills = [f for f in fills if f.epoch == account.epoch]
        newest_first = sorted(fills, key=lambda f: (f.filled_at, str(f.id)), reverse=True)
        page = newest_first[offset : offset + limit]
        return FillList(
            fills=[self._fill(f, realized.get(f.id)) for f in page],
            has_more=offset + len(page) < len(newest_first),
        )

    async def list_corporate_actions(
        self,
        user_id: UUID,
        account_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
    ) -> CorporateActionList:
        account = await self._own(user_id, account_id)
        return await list_corporate_actions(
            self.repo, account, limit=limit, offset=offset, include_archived=include_archived
        )

    async def _fills_and_pnl(
        self, account: PaperAccountRecord
    ) -> tuple[list[PaperFillRecord], dict[UUID, int]]:
        fills = await self.repo.list_paper_fills(account.id, epoch=None)
        actions = await self.repo.list_paper_corporate_actions(account.id, epoch=None)
        return fills, realized_by_fill(fills, actions)

    @staticmethod
    def _fill(fill: PaperFillRecord, realized: int | None) -> Fill:
        return Fill(
            id=fill.id,
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=fill.side,
            shares=fill.shares,
            price=float(fill.price),
            amount=fill.amount,
            commission=fill.commission,
            tax=fill.tax,
            day_trade=fill.day_trade,
            filled_at=_utc(fill.filled_at),  # type: ignore[arg-type]
            price_source=fill.price_source,
            price_time=_utc(fill.price_time),  # type: ignore[arg-type]
            realized_pnl=realized if fill.side == "sell" else None,
        )

    def _order(
        self, order: PaperOrderRecord, fill: PaperFillRecord | None, realized: int | None
    ) -> Order:
        return Order(
            id=order.id,
            symbol=order.symbol,
            name=order.name,
            side=order.side,
            order_type=order.order_type,
            limit_price=_f(order.limit_price),
            lots=order.lots,
            status=order.status,
            source=order.source,
            session_date=order.session_date,
            created_at=_utc(order.created_at),  # type: ignore[arg-type]
            updated_at=_utc(order.updated_at),  # type: ignore[arg-type]
            reject_reason=order.reject_reason,
            reserved_cash=order.reserved_cash,
            note=order.note,
            fill=None if fill is None else self._fill(fill, realized),
        )

    # --- equity curve --------------------------------------------------------------

    async def equity(self, user_id: UUID, account_id: UUID, days: int = 120) -> EquityCurve:
        account = await self._own(user_id, account_id)
        days = max(1, min(days, 3650))
        now = self.clock()
        today = local(now).date()
        snapshots = await self.repo.list_paper_snapshots(
            account.id, epoch=account.epoch, since=today - timedelta(days=days)
        )
        points = [
            EquityPoint(
                date=s.trading_date,
                equity=s.equity,
                cash=s.cash,
                market_value=s.market_value,
                benchmark_close=s.benchmark_close,
            )
            for s in snapshots
        ]
        views = await self._market_for([account])
        valuation = await self._value(account, views, now)
        live_day = latest_session_day(now)
        closes = await benchmark_closes(self.repo, live_day - timedelta(days=10))
        newest = closes[max(closes)] if closes else None
        points = [p for p in points if p.date != live_day]
        if live_day >= account_start_date(account):
            points.append(
                EquityPoint(
                    date=live_day,
                    equity=valuation.equity,
                    cash=valuation.cash,
                    market_value=valuation.market_value,
                    benchmark_close=closes.get(live_day, newest),
                    live=True,
                )
            )
        return EquityCurve(points=points)


def _reserved_sells(working: list[PaperOrderRecord]) -> dict[str, int]:
    held: dict[str, int] = {}
    for order in working:
        if order.side == "sell":
            held[order.symbol] = held.get(order.symbol, 0) + order.lots * BOARD_LOT
    return held
