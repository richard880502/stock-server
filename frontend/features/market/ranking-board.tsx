"use client";

import { useState } from "react";
import { Card } from "@/components/card";
import { Direction } from "@/components/direction";
import { EmptyState } from "@/components/empty-state";
import { AlertIcon, ClockIcon, InfoIcon } from "@/components/icons";
import { WatchlistStar } from "@/components/watchlist-star";
import type { MarketMetric, MarketRankings, MarketScope, RankingEntry } from "@/lib/api/types";
import {
  directionOf,
  formatLots,
  formatPrice,
  formatSigned,
  formatSignedLots,
  formatTwdCompact,
  shortDate,
} from "@/lib/format";
import { METRICS, windowLabel } from "./metrics";

const COLLAPSED_ROWS = 10;

/** The metric value with its unit, signed where the sign carries meaning. */
export function formatMetricValue(value: number, unit: MarketRankings["unit"], signed: boolean) {
  if (unit === "元") return formatTwdCompact(value);
  if (unit === "%") return `${signed ? formatSigned(value, 2) : value.toFixed(2)}%`;
  return `${signed ? formatSignedLots(value) : formatLots(value)} 張`;
}

function valueHeading(metric: MarketMetric, window: number) {
  const { kind, label } = METRICS[metric];
  if (kind === "flow") return `${windowLabel(window)}買賣超`;
  if (kind === "price") return `${windowLabel(window)}漲跌幅`;
  if (kind === "credit") return `${windowLabel(window)}增減`;
  return label;
}

function BoardSkeleton() {
  return (
    <div aria-hidden="true" className="board-skeleton">
      {Array.from({ length: 8 }, (_, i) => (
        <span key={i} className="skeleton block" style={{ height: 40 }} />
      ))}
    </div>
  );
}

function Row({
  entry,
  scale,
  unit,
  signed,
  scope,
  showTurnover,
  onPick,
}: {
  entry: RankingEntry;
  scale: number;
  unit: MarketRankings["unit"];
  signed: boolean;
  scope: MarketScope;
  showTurnover: boolean;
  onPick: (symbol: string) => void;
}) {
  const code = entry.symbol.replace(/\.TWO?$/, "");
  const direction = signed ? directionOf(entry.value) : "neutral";
  const width = scale > 0 ? Math.max(2, (Math.abs(entry.value) / scale) * 100) : 0;
  return (
    <tr className="rank-row" onClick={() => onPick(entry.symbol)}>
      <td className="col-rank num">{entry.rank}</td>
      <th scope="row" className="col-stock">
        <div className="stock-cell">
          {/* The click bubbles to the row, so Enter / Space on the button works too. */}
          <button
            type="button"
            className="stock-link"
            aria-label={`${entry.name || code}（${code}），查看今日訊號`}
          >
            <span className="stock-code num">{code}</span>
            <span className="stock-name">{entry.name}</span>
            {scope === "ALL" && (
              <span className="market-tag">{entry.market === "TWO" ? "上櫃" : "上市"}</span>
            )}
          </button>
          <WatchlistStar symbol={entry.symbol} variant="icon" stopPropagation />
        </div>
      </th>
      <td className="col-close num" data-label="收盤">
        {formatPrice(entry.close)}
      </td>
      <td className="col-change" data-label="當日漲跌">
        <Direction value={entry.day_change_pct} className="num">
          {formatSigned(entry.day_change_pct, 2)}%
        </Direction>
      </td>
      <td className="col-value">
        <span
          className={`value-num num ${direction === "up" ? "text-up" : direction === "down" ? "text-down" : ""}`}
        >
          {formatMetricValue(entry.value, unit, signed)}
        </span>
        <span className="value-bar" aria-hidden="true">
          <span className={`value-bar-fill ${direction}`} style={{ width: `${width}%` }} />
        </span>
      </td>
      {showTurnover && (
        <td className="col-turnover num" data-label="當日成交值">
          {formatTwdCompact(entry.turnover_value)}
        </td>
      )}
    </tr>
  );
}

/** One ranked list: a real table on wide containers, compact cards on narrow ones (see market.css). */
export function RankingBoard({
  title,
  metric,
  scope,
  window,
  data,
  loading,
  stale,
  failed,
  onPick,
}: {
  title: string;
  metric: MarketMetric;
  scope: MarketScope;
  window: number;
  data: MarketRankings | null;
  loading: boolean;
  /** `data` answers an earlier query; dim it until the new one arrives. */
  stale: boolean;
  /** The request failed and there is nothing to show; the error banner explains why. */
  failed: boolean;
  onPick: (symbol: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const info = METRICS[metric];
  // A different metric has different columns: show a skeleton instead of stale rows.
  const usable = data !== null && data.metric === metric ? data : null;
  const entries = usable?.entries ?? [];
  const shown = expanded ? entries : entries.slice(0, COLLAPSED_ROWS);
  const scale = Math.max(0, ...shown.map((entry) => Math.abs(entry.value)));
  const signed = info.kind !== "volume";
  const showTurnover = metric !== "turnover";

  let sub: string | null = null;
  if (usable?.data_through) {
    sub =
      usable.window > 1 && usable.window_start
        ? `${windowLabel(window)} · ${shortDate(usable.window_start)}–${shortDate(usable.data_through)}`
        : `${shortDate(usable.data_through)} 收盤`;
  }

  return (
    <Card
      title={title}
      sub={sub ?? undefined}
      className="rank-board"
      aria-label={title}
      aside={
        usable && usable.trading_days > 0 ? (
          <span className="chip plain num">{usable.trading_days} 個交易日</span>
        ) : undefined
      }
    >
      {!usable && (loading || data !== null) ? (
        <BoardSkeleton />
      ) : !usable ? (
        failed ? (
          <EmptyState icon={<AlertIcon size={22} />} title="暫時無法載入這份排行">
            詳細原因請見上方訊息，按「重試」即可重新載入。
          </EmptyState>
        ) : null
      ) : entries.length === 0 ? (
        usable.backfill_pending ? (
          <EmptyState icon={<ClockIcon size={22} />} title="資料回補中" live>
            這個範圍的歷史資料還在下載，完成前列表可能是空的。頁面會每 15 秒自動更新。
          </EmptyState>
        ) : (
          <EmptyState icon={<InfoIcon size={22} />} title="沒有符合條件的資料">
            {scope === "TWO" ? "上櫃" : scope === "TW" ? "上市" : "這個範圍"}在
            {usable.data_through ? ` ${usable.data_through} ` : "此期間"}沒有可排行的個股。可以換一個指標、期間或日期再看看。
          </EmptyState>
        )
      ) : (
        <div className={stale || loading ? "is-refreshing" : undefined} aria-busy={stale || loading}>
          <table className="rank-table">
            <caption className="sr-only">
              {title}，{windowLabel(window)}，共 {entries.length} 檔
            </caption>
            <thead>
              <tr>
                <th scope="col" className="col-rank">
                  排名
                </th>
                <th scope="col" className="col-stock">
                  股票
                </th>
                <th scope="col" className="col-close">
                  收盤
                </th>
                <th scope="col" className="col-change">
                  當日漲跌
                </th>
                <th scope="col" className="col-value">
                  {valueHeading(metric, window)}
                </th>
                {showTurnover && (
                  <th scope="col" className="col-turnover">
                    當日成交值
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {shown.map((entry) => (
                <Row
                  key={entry.symbol}
                  entry={entry}
                  scale={scale}
                  unit={usable.unit}
                  signed={signed}
                  scope={scope}
                  showTurnover={showTurnover}
                  onPick={onPick}
                />
              ))}
            </tbody>
          </table>
          {entries.length > COLLAPSED_ROWS && (
            <button
              type="button"
              className="button small board-more"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? "收合" : `顯示全部 ${entries.length} 檔`}
            </button>
          )}
        </div>
      )}
    </Card>
  );
}
