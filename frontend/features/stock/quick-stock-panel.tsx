"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Card } from "@/components/card";
import { TrendChart } from "@/components/charts/trend-chart";
import { Direction } from "@/components/direction";
import { EmptyState } from "@/components/empty-state";
import { AlertIcon, InfoIcon } from "@/components/icons";
import { ResearchTable } from "@/components/research-table";
import { SegmentedControl } from "@/components/segmented-control";
import { Stat, StatGrid } from "@/components/stat";
import { WatchlistStar } from "@/components/watchlist-star";
import type { StockDayPoint, StockOverview } from "@/lib/api/types";
import {
  formatLots,
  formatPrice,
  formatSigned,
  formatSignedLots,
  shortDate,
} from "@/lib/format";
import type { SyncWait } from "@/lib/sync-retry";
import { WATCHLIST_COVERAGE_NOTE } from "@/lib/use-watchlist";
import { AnalysisStatus } from "./analysis-status";
import { flowsLagNote, Lots, PctChange } from "./flow-format";
import { useStockOverview } from "./use-stock-overview";

const marketLabel = { TW: "上市", TWO: "上櫃" } as const;
const RECENT_SESSIONS = 15;

const RANGE_OPTIONS = [
  { value: 60, label: "近 60 日" },
  { value: 120, label: "近 120 日" },
  { value: 0, label: "全部" },
];

const TABLE_HEADERS = [
  "日期",
  "收盤",
  "漲跌 %",
  "成交量（張）",
  "外資（張）",
  "投信（張）",
  "自營商（張）",
  "三大法人（張）",
  "融資餘額（張）",
  "融券餘額（張）",
];

/** Balance with the day's change underneath; "尚未公布" while the credit report is pending. */
function Balance({ balance, change }: { balance: number | null; change: number | null }) {
  if (balance === null) return <span className="text-muted">尚未公布</span>;
  return (
    <span className="balance-cell">
      <span>{formatLots(balance)}</span>
      {change !== null && <small>{formatSigned(change, 0)}</small>}
    </span>
  );
}

function SessionRow({ day }: { day: StockDayPoint }) {
  return (
    <tr>
      <td>{shortDate(day.trading_date)}</td>
      <td>{formatPrice(day.close)}</td>
      <td>
        <PctChange value={day.change_pct} arrow={false} />
      </td>
      <td>{formatLots(day.volume_lots)}</td>
      <td>
        <Lots value={day.foreign_net_lots} />
      </td>
      <td>
        <Lots value={day.trust_net_lots} />
      </td>
      <td>
        <Lots value={day.dealer_net_lots} />
      </td>
      <td>
        <Lots value={day.total_net_lots} />
      </td>
      <td>
        <Balance balance={day.margin_balance} change={day.margin_change} />
      </td>
      <td>
        <Balance balance={day.short_balance} change={day.short_change} />
      </td>
    </tr>
  );
}

function QuickSkeleton() {
  return (
    <div aria-busy="true" aria-label="正在載入快速檢視" className="quick-skeleton">
      <section className="card">
        <span className="skeleton line" style={{ width: "35%" }} />
        <span className="skeleton block" style={{ width: "45%", height: 48, marginTop: 14 }} />
      </section>
      <section className="card">
        <span className="skeleton block" style={{ height: 150 }} />
      </section>
    </div>
  );
}

function PriceChart({ days }: { days: StockDayPoint[] }) {
  const [range, setRange] = useState(120);
  const shown = useMemo(() => (range === 0 ? days : days.slice(-range)), [days, range]);
  const points = useMemo(
    () => shown.map((day) => ({ date: day.trading_date, value: day.close })),
    [shown],
  );
  return (
    <Card
      title="收盤價走勢"
      sub={
        shown.length > 0
          ? `${shortDate(shown[0].trading_date)}–${shortDate(shown[shown.length - 1].trading_date)} · ${shown.length} 個交易日`
          : undefined
      }
      aside={
        <SegmentedControl label="走勢區間" value={range} options={RANGE_OPTIONS} onChange={setRange} />
      }
    >
      {points.length > 0 && (
        <figure className="chart-figure">
          <TrendChart
            variant="line"
            label="每日收盤價（元）"
            seriesLabel="收盤價"
            color="var(--series-strategy)"
            points={points}
            format={(value) => `${formatPrice(value)} 元`}
            axis={(value) => formatPrice(value)}
            missingNote=""
          />
        </figure>
      )}
    </Card>
  );
}

function OverviewBody({ data }: { data: StockOverview }) {
  const { summary } = data;
  const recent = useMemo(() => data.days.slice(-RECENT_SESSIONS).reverse(), [data.days]);
  const lag = flowsLagNote(summary.flows_through, data.data_through);
  const flowNote = summary.flows_through ? `至 ${shortDate(summary.flows_through)}` : "尚未公布";

  return (
    <>
      <Card>
        <StatGrid>
          <Stat
            label="近 5 日報酬"
            value={<PctChange value={summary.return_5d_pct} />}
            valueClassName="num"
          />
          <Stat
            label="近 20 日報酬"
            value={<PctChange value={summary.return_20d_pct} />}
            valueClassName="num"
          />
          <Stat
            label="外資 5 日買賣超"
            value={
              summary.foreign_net_5d_lots === null ? (
                <span className="text-muted">尚未公布</span>
              ) : (
                <Direction value={summary.foreign_net_5d_lots}>
                  {formatSignedLots(summary.foreign_net_5d_lots)} 張
                </Direction>
              )
            }
            note={flowNote}
          />
          <Stat
            label="三大法人 5 日買賣超"
            value={
              summary.total_net_5d_lots === null ? (
                <span className="text-muted">尚未公布</span>
              ) : (
                <Direction value={summary.total_net_5d_lots}>
                  {formatSignedLots(summary.total_net_5d_lots)} 張
                </Direction>
              )
            }
            note={flowNote}
          />
          <Stat
            label="20 日均量"
            value={
              summary.avg_volume_20d_lots === null ? "—" : `${formatLots(summary.avg_volume_20d_lots)} 張`
            }
          />
        </StatGrid>
        {lag && (
          <p className="note quick-lag">
            <InfoIcon size={14} /> {lag}
          </p>
        )}
      </Card>

      <PriceChart days={data.days} />

      <Card
        title={`最近 ${recent.length} 個交易日`}
        sub="由新到舊 · 買賣超與融資融券單位皆為張（1 張 = 1,000 股）"
      >
        <ResearchTable label="最近交易日行情與籌碼" headers={TABLE_HEADERS} className="table-tall">
          {recent.map((day) => (
            <SessionRow key={day.trading_date} day={day} />
          ))}
        </ResearchTable>
        <p className="note quick-table-note">
          「尚未公布」表示交易所該日報表還沒出來，不是 0。
        </p>
      </Card>
    </>
  );
}

/**
 * Instant view of any listed stock, built from the whole-market snapshot, while
 * the full signal is prepared in the background. Rendered by the signal view
 * in place of the bare "waiting" notice.
 */
export function QuickStockPanel({
  symbol,
  syncWait,
  signalLoading,
  onCancelSync,
  onReload,
}: {
  symbol: string;
  /** Countdown while the signal request retries 503 sync_pending. */
  syncWait: SyncWait | null;
  signalLoading: boolean;
  onCancelSync: () => void;
  onReload: () => void;
}) {
  // A new attempt means the background worker may have moved on: refresh quietly.
  const { data, error } = useStockOverview(symbol, syncWait?.attempt ?? 0);
  const status = (
    <AnalysisStatus
      state={data?.full_analysis ?? null}
      wait={syncWait}
      loading={signalLoading}
      onCancel={onCancelSync}
      onReload={onReload}
    />
  );

  if (!data && error) {
    return (
      <>
        {syncWait && status}
        <EmptyState
          icon={<AlertIcon size={22} />}
          title={error.status === 404 ? `找不到 ${symbol}` : `暫時無法顯示 ${symbol} 的快速檢視`}
          action={
            error.status === 404 ? (
              <Link className="button" href="/market">
                從市場排行找股票
              </Link>
            ) : (
              <button type="button" className="button" disabled={signalLoading} onClick={onReload}>
                重新載入
              </button>
            )
          }
        >
          {error.message}
          {error.status === 404 ? ` ${WATCHLIST_COVERAGE_NOTE}` : ""}
        </EmptyState>
      </>
    );
  }

  if (!data) {
    return (
      <>
        {syncWait && status}
        <QuickSkeleton />
      </>
    );
  }

  const close = data.summary.last_close;
  return (
    <>
      <section className="card quick-head" aria-labelledby="quick-title">
        <div className="quick-id">
          <h1 id="quick-title" className="quick-title">
            <span className="mono quick-symbol">{data.symbol}</span>
            <span className="quick-name">{data.name}</span>
          </h1>
          <span className="chip plain">{marketLabel[data.market]}</span>
          <span className="chip tone-warn" title="先用全市場快照顯示行情與籌碼">
            快速檢視
          </span>
          <div className="quick-actions">
            <WatchlistStar symbol={data.symbol} />
          </div>
        </div>
        <div className="quick-price">
          <span className="quick-close num">{formatPrice(close)}</span>
          <span className="quick-change num">
            <PctChange value={data.summary.day_change_pct} />
          </span>
          <span className="quick-asof num">收盤 {data.data_through}</span>
        </div>
      </section>

      {status}

      <OverviewBody data={data} />

      <p className="note quick-foot">
        快速檢視取自全市場每日快照，只含行情、三大法人與融資融券；訊號、回測與預測需完整歷史資料。
        {WATCHLIST_COVERAGE_NOTE}{" "}
        <Link className="button link" href="/market">
          看看市場排行
        </Link>
      </p>
    </>
  );
}
