"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Card } from "@/components/card";
import { ErrorBanner } from "@/components/error-banner";
import { SegmentedControl } from "@/components/segmented-control";
import {
  cancelOrder,
  type CorporateAction,
  type Fill,
  listCorporateActions,
  listFills,
  listOrders,
  type Order,
  type OrderStatus,
} from "@/lib/api/paper";
import { formatPrice } from "@/lib/format";
import { stockHref } from "@/lib/stock-routes";
import {
  formatDateTime,
  formatMoney,
  formatSessionDate,
  formatSignedMoney,
  SIDE_LABEL,
  SOURCE_LABEL,
  STATUS_LABEL,
  toneClass,
} from "./paper-math";
import { usePagedList, usePolled } from "./use-paper-data";

const code = (symbol: string) => symbol.replace(/\.TWO?$/, "");
const PAGE = 20;

type Tab = "working" | "done" | "fills" | "actions";

function Stock({ symbol, name }: { symbol: string; name?: string }) {
  return (
    <>
      <span className="paper-stock-name">{name || code(symbol)}</span>
      <span className="paper-stock-code num">{code(symbol)}</span>
    </>
  );
}

const Side = ({ side }: { side: Order["side"] }) => (
  <span className={`paper-side ${side}`}>{SIDE_LABEL[side]}</span>
);

const priceText = (order: Order) =>
  order.order_type === "market" ? "市價" : `限價 ${formatPrice(order.limit_price ?? 0)}`;

const STATUS_CHIP: Record<OrderStatus, string> = {
  working: "chip accent",
  filled: "chip plain",
  cancelled: "chip tone-flat",
  expired: "chip tone-flat",
  rejected: "chip tone-warn",
};

export function StatusChip({ status }: { status: OrderStatus }) {
  return <span className={STATUS_CHIP[status]}>{STATUS_LABEL[status]}</span>;
}

export function OrdersTable({
  orders,
  mode,
  cancelling,
  onCancel,
}: {
  orders: Order[];
  mode: "working" | "done";
  cancelling?: string | null;
  onCancel?: (order: Order) => void;
}) {
  return (
    <div className="table-wrap paper-table-wrap" tabIndex={0} role="region" aria-label={mode === "working" ? "委託中" : "已完成委託"}>
      <table className="data-table paper-table paper-orders-table">
        <thead>
          <tr>
            <th scope="col">時間</th>
            <th scope="col">股票</th>
            <th scope="col">買賣</th>
            <th scope="col">張數</th>
            <th scope="col">類型／價格</th>
            <th scope="col">有效日</th>
            <th scope="col">{mode === "working" ? "操作" : "狀態"}</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr key={order.id}>
              <td className="num">{formatDateTime(order.created_at)}</td>
              <th scope="row" className="paper-stock-cell">
                <Stock symbol={order.symbol} name={order.name} />
                {order.source === "ai" && (
                  <span className="paper-ai-badge" title="由 AI 提案帶入的委託">
                    AI
                  </span>
                )}
              </th>
              <td><Side side={order.side} /></td>
              <td className="num">{order.lots}</td>
              <td className="num">{priceText(order)}</td>
              <td className="num">{formatSessionDate(order.session_date)}</td>
              <td className="paper-status-cell">
                {mode === "working" ? (
                  <button
                    type="button"
                    className="button small"
                    disabled={cancelling === order.id}
                    onClick={() => onCancel?.(order)}
                    aria-label={`取消 ${order.name} ${SIDE_LABEL[order.side]}委託`}
                  >
                    {cancelling === order.id ? "取消中…" : "取消"}
                  </button>
                ) : (
                  <>
                    <StatusChip status={order.status} />
                    {order.reject_reason && <small className="paper-reason">{order.reject_reason}</small>}
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FillsTable({ fills, names }: { fills: Fill[]; names?: ReadonlyMap<string, string> }) {
  return (
    <div className="table-wrap paper-table-wrap" tabIndex={0} role="region" aria-label="成交紀錄">
      <table className="data-table paper-table paper-fills-table">
        <thead>
          <tr>
            <th scope="col">時間</th>
            <th scope="col">股票</th>
            <th scope="col">買賣</th>
            <th scope="col">股數</th>
            <th scope="col">成交價</th>
            <th scope="col">金額</th>
            <th scope="col">手續費</th>
            <th scope="col">交易稅</th>
            <th scope="col">已實現損益</th>
          </tr>
        </thead>
        <tbody>
          {fills.map((fill) => (
            <tr key={fill.id}>
              <td className="num">{formatDateTime(fill.filled_at)}</td>
              <th scope="row" className="paper-stock-cell">
                <Stock symbol={fill.symbol} name={fill.name ?? names?.get(fill.symbol)} />
              </th>
              <td>
                <Side side={fill.side} />
                {fill.day_trade && <small className="paper-sub">當沖</small>}
              </td>
              <td className="num">{formatMoney(fill.shares)}</td>
              <td className="num">
                {formatPrice(fill.price)}
                <small className="paper-sub" title="成交價所依據的報價來源">{SOURCE_LABEL[fill.price_source]}</small>
              </td>
              <td className="num">{formatMoney(fill.amount)}</td>
              <td className="num">{formatMoney(fill.commission)}</td>
              <td className="num">{formatMoney(fill.tax)}</td>
              <td className={`num ${toneClass(fill.realized_pnl)}`}>
                {fill.realized_pnl == null ? "—" : formatSignedMoney(fill.realized_pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The simplification the simulator makes, shown once under the 除權息 list. */
export const DIVIDEND_NOTE =
  "模擬於除息日以全額入帳現金股利（實際約數週後發放，並可能扣二代健保補充保費與所得稅）；" +
  "配股不足一股的零股捨去；現金增資認購不模擬。";

export function CorporateActionsTable({ actions }: { actions: CorporateAction[] }) {
  return (
    <div className="table-wrap paper-table-wrap" tabIndex={0} role="region" aria-label="除權息紀錄">
      <table className="data-table paper-table paper-actions-table">
        <thead>
          <tr>
            <th scope="col">除權息日</th>
            <th scope="col">股票</th>
            <th scope="col">參與股數</th>
            <th scope="col">現金股利</th>
            <th scope="col">配股</th>
          </tr>
        </thead>
        <tbody>
          {actions.map((action) => (
            <tr key={action.id}>
              <td className="num">{formatSessionDate(action.ex_date)}</td>
              <th scope="row" className="paper-stock-cell">
                <Link href={stockHref(action.symbol)} className="paper-link" title="開啟個股頁">
                  <Stock symbol={action.symbol} name={action.name} />
                </Link>
              </th>
              <td className="num">{formatMoney(action.entitled_shares)}</td>
              <td className={`num ${action.cash_amount > 0 ? toneClass(action.cash_amount) : ""}`}>
                {action.cash_amount > 0 ? `現金股利入帳 NT$${formatMoney(action.cash_amount)}` : "—"}
              </td>
              <td className="num">{action.shares_added > 0 ? `配股 +${formatMoney(action.shares_added)} 股` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LoadMore({ hasMore, loading, onClick }: { hasMore: boolean; loading: boolean; onClick: () => void }) {
  if (!hasMore) return null;
  return (
    <div className="paper-more">
      <button type="button" className="button" onClick={onClick} disabled={loading}>
        {loading ? "載入中…" : "載入更多"}
      </button>
    </div>
  );
}

const EMPTY: Record<Tab, string> = {
  working: "目前沒有委託中的單。",
  done: "還沒有已完成的委託。",
  fills: "還沒有成交紀錄。",
  actions: "還沒有除權息紀錄。持股須在除權息日前一個交易日收盤仍持有才會參與。",
};

/**
 * 委託中 / 已完成 / 成交紀錄 / 除權息. Working orders poll every few seconds while any exist; the
 * other two tabs load only when opened. `refreshToken` bumps after the page places or cancels
 * an order so everything refetches at once; `onWorkingChanged` tells the page a working order
 * left the book (filled / expired) so balances refetch too.
 */
export function OrdersPanel({
  accountId,
  workingHint,
  refreshToken,
  names,
  onWorkingChanged,
  onCancelled,
}: {
  accountId: string;
  /** `working_orders` from the account figures: picks the polling pace. */
  workingHint: number;
  refreshToken: number;
  names: ReadonlyMap<string, string>;
  onWorkingChanged: () => void;
  onCancelled: () => void;
}) {
  const [tab, setTab] = useState<Tab>("working");
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const working = usePolled(
    `working|${accountId}`,
    async (signal) => (await listOrders(accountId, { status: "working", limit: 100, offset: 0 }, { signal })).items,
    workingHint > 0 ? 4000 : 15000,
  );
  const done = usePagedList(
    tab === "done" ? `done|${accountId}` : null,
    (offset, limit, signal) => listOrders(accountId, { status: "done", limit, offset }, { signal }),
    PAGE,
  );
  const fills = usePagedList(
    tab === "fills" ? `fills|${accountId}` : null,
    (offset, limit, signal) => listFills(accountId, { limit, offset }, { signal }),
    PAGE,
  );

  const actions = usePagedList(
    tab === "actions" ? `actions|${accountId}` : null,
    (offset, limit, signal) => listCorporateActions(accountId, { limit, offset }, { signal }),
    PAGE,
  );

  const { reload: reloadWorking } = working;
  const { reload: reloadDone } = done;
  const { reload: reloadFills } = fills;
  const { reload: reloadActions } = actions;
  const firstToken = useRef(refreshToken);
  useEffect(() => {
    if (refreshToken === firstToken.current) return;
    reloadWorking();
    reloadDone();
    reloadFills();
    reloadActions();
  }, [refreshToken, reloadWorking, reloadDone, reloadFills, reloadActions]);

  const workingCount = working.data?.length ?? null;
  const lastCount = useRef<number | null>(null);
  useEffect(() => {
    if (workingCount === null) return;
    if (lastCount.current !== null && workingCount < lastCount.current) {
      reloadDone();
      reloadFills();
      onWorkingChanged();
    }
    lastCount.current = workingCount;
  }, [workingCount, reloadDone, reloadFills, onWorkingChanged]);

  const cancel = async (order: Order) => {
    setCancelling(order.id);
    setCancelError(null);
    try {
      await cancelOrder(accountId, order.id);
      onCancelled();
    } catch (error) {
      setCancelError(error instanceof Error ? error.message : "取消失敗");
      reloadWorking();
    } finally {
      setCancelling(null);
    }
  };

  const options = useMemo(
    () => [
      { value: "working" as const, label: workingCount ? `委託中 ${workingCount}` : "委託中" },
      { value: "done" as const, label: "已完成" },
      { value: "fills" as const, label: "成交紀錄" },
      { value: "actions" as const, label: "除權息" },
    ],
    [workingCount],
  );

  const active = tab === "working" ? working : tab === "done" ? done : tab === "fills" ? fills : actions;
  const error = active.error;
  const loading = tab === "working" ? working.loading && !working.data : active.loading;

  return (
    <Card
      title="委託與成交"
      aside={<SegmentedControl label="委託與成交" value={tab} options={options} onChange={setTab} />}
      className="paper-orders"
    >
      {cancelError && (
        <p className="inline-error" role="alert">
          {cancelError}
        </p>
      )}
      {error && <ErrorBanner message={error.message} onDismiss={() => setCancelError(null)} onRetry={active.reload} />}
      <div aria-live="polite">
        {loading ? (
          <div className="paper-skeleton-rows" aria-busy="true">
            {[0, 1, 2].map((i) => (
              <span key={i} className="skeleton block" style={{ height: "2.25rem" }} />
            ))}
          </div>
        ) : tab === "working" ? (
          working.data && working.data.length > 0 ? (
            <OrdersTable orders={working.data} mode="working" cancelling={cancelling} onCancel={cancel} />
          ) : (
            <p className="paper-empty">{EMPTY.working}</p>
          )
        ) : tab === "done" ? (
          done.items.length > 0 ? (
            <>
              <OrdersTable orders={done.items} mode="done" />
              <LoadMore hasMore={done.hasMore} loading={done.loadingMore} onClick={done.loadMore} />
            </>
          ) : (
            !error && <p className="paper-empty">{EMPTY.done}</p>
          )
        ) : tab === "fills" ? (
          fills.items.length > 0 ? (
            <>
              <FillsTable fills={fills.items} names={names} />
              <LoadMore hasMore={fills.hasMore} loading={fills.loadingMore} onClick={fills.loadMore} />
            </>
          ) : (
            !error && <p className="paper-empty">{EMPTY.fills}</p>
          )
        ) : actions.items.length > 0 ? (
          <>
            <CorporateActionsTable actions={actions.items} />
            <LoadMore hasMore={actions.hasMore} loading={actions.loadingMore} onClick={actions.loadMore} />
          </>
        ) : (
          !error && <p className="paper-empty">{EMPTY.actions}</p>
        )}
        {tab === "actions" && <p className="paper-note">{DIVIDEND_NOTE}</p>}
      </div>
    </Card>
  );
}
