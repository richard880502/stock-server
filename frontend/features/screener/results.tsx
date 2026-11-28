"use client";

import Link from "next/link";
import { Fragment, useSyncExternalStore } from "react";
import { Direction } from "@/components/direction";
import { ArrowDownIcon, ArrowUpIcon, ChevronDownIcon } from "@/components/icons";
import { WatchlistStar } from "@/components/watchlist-star";
import type { ScreenerEntry, ScreenerMeta, ScreenerOrder } from "@/lib/api/types";
import { formatPrice, formatSigned } from "@/lib/format";
import { stockHref } from "@/lib/stock-routes";
import { RowDetail } from "./row-detail";
import { codeOf, type ColumnDef, resolveColumn } from "./screener-model";
import { ColumnValue, FlagBadges, HighlightChips, PriceCell, ScoreBar } from "./screener-parts";

/** Matches the 640px breakpoint in screener.css: phones get cards instead of a table. */
const PHONE_QUERY = "(max-width: 640px)";
const subscribePhone = (onChange: () => void) => {
  const query = globalThis.matchMedia(PHONE_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
};
export const usePhoneLayout = () =>
  useSyncExternalStore(subscribePhone, () => globalThis.matchMedia(PHONE_QUERY).matches, () => false);

export type ResultsProps = {
  entries: ScreenerEntry[];
  meta: ScreenerMeta;
  columns: ColumnDef[];
  sort: string;
  order: ScreenerOrder;
  expanded: ReadonlySet<string>;
  /** Dim the rows: they answer an earlier query. */
  refreshing: boolean;
  onToggle: (symbol: string) => void;
  onSort: (key: string, higherIsBetter: boolean | null) => void;
};

function StockLabel({ entry }: { entry: ScreenerEntry }) {
  const code = codeOf(entry.symbol);
  return (
    <div className="sc-stock">
      <Link
        href={stockHref(entry.symbol)}
        className="stock-link sc-stock-link"
        aria-label={`${entry.name || code}（${code}），開啟個股頁`}
      >
        <span className="stock-code num">{code}</span>
        <span className="stock-name" title={entry.name}>{entry.name}</span>
      </Link>
      <span className="sc-stock-meta">
        <span className="market-tag">{entry.market === "TWO" ? "上櫃" : "上市"}</span>
        <FlagBadges flags={entry.flags} />
        {entry.industry_name && <span className="sc-industry">{entry.industry_name}</span>}
      </span>
    </div>
  );
}

function SortHeader({
  column,
  label,
  fullLabel,
  sort,
  order,
  onSort,
}: {
  column: Pick<ColumnDef, "key" | "higherIsBetter" | "description" | "sortable">;
  label: string;
  /** Accessible / tooltip name when `label` is a shortened form. */
  fullLabel?: string;
  sort: string;
  order: ScreenerOrder;
  onSort: ResultsProps["onSort"];
}) {
  const active = sort === column.key;
  const title = [fullLabel && fullLabel !== label ? fullLabel : null, column.description || null].filter(Boolean).join("：") || undefined;
  if (!column.sortable) return <th scope="col" title={title}>{label}</th>;
  return (
    <th scope="col" aria-sort={active ? (order === "desc" ? "descending" : "ascending") : "none"} className="sc-th-sortable">
      <button
        type="button"
        className="sc-sort-button"
        title={title}
        aria-label={fullLabel && fullLabel !== label ? fullLabel : undefined}
        onClick={() => onSort(column.key, column.higherIsBetter)}
      >
        <span>{label}</span>
        <span className="sc-sort-arrow" aria-hidden="true">
          {active ? order === "desc" ? <ArrowDownIcon size={12} /> : <ArrowUpIcon size={12} /> : null}
        </span>
        <span className="sr-only">{active ? `，目前${order === "desc" ? "由高到低" : "由低到高"}，按一下反向排序` : "，按一下排序"}</span>
      </button>
    </th>
  );
}

function ExpandButton({ entry, open, onToggle }: { entry: ScreenerEntry; open: boolean; onToggle: (symbol: string) => void }) {
  return (
    <button
      type="button"
      className="icon-button small sc-expand"
      aria-expanded={open}
      aria-controls={`sc-detail-${entry.symbol}`}
      aria-label={`${open ? "收合" : "展開"} ${entry.name || codeOf(entry.symbol)} 的詳細資料`}
      onClick={() => onToggle(entry.symbol)}
    >
      <ChevronDownIcon size={16} />
    </button>
  );
}

export function ResultsTable(props: ResultsProps) {
  const { entries, meta, columns, sort, order, expanded, refreshing, onToggle, onSort } = props;
  const changeColumn = meta.metrics.some((m) => m.key === "change_pct") ? resolveColumn(meta, "change_pct") : null;
  const composite = resolveColumn(meta, "composite");
  const span = 7 + columns.length;
  return (
    <div className={`sc-table-wrap${refreshing ? " is-refreshing" : ""}`} aria-busy={refreshing}>
      <table className="sc-table">
        <caption className="sr-only">選股結果，共 {entries.length} 檔，可按欄位標題排序</caption>
        <thead>
          <tr>
            <th scope="col" className="sc-col-expand"><span className="sr-only">展開詳細資料</span></th>
            <th scope="col" className="sc-col-rank">排名</th>
            <th scope="col" className="sc-col-stock">股票</th>
            <th scope="col" className="sc-col-close">收盤</th>
            {changeColumn?.sortable ? (
              <SortHeader column={changeColumn} label="漲跌%" sort={sort} order={order} onSort={onSort} />
            ) : (
              <th scope="col">漲跌%</th>
            )}
            <SortHeader column={composite} label="綜合" sort={sort} order={order} onSort={onSort} />
            {columns.map((column) => (
              <SortHeader key={column.key} column={column} label={column.short} fullLabel={column.label} sort={sort} order={order} onSort={onSort} />
            ))}
            <th scope="col" className="sc-col-star"><span className="sr-only">自選股</span></th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const open = expanded.has(entry.symbol);
            return (
              <Fragment key={entry.symbol}>
                <tr className={`sc-row${open ? " is-open" : ""}${entry.highlights.length > 0 ? " has-highlights" : ""}`}>
                  <td className="sc-col-expand"><ExpandButton entry={entry} open={open} onToggle={onToggle} /></td>
                  <td className="sc-col-rank num">{entry.rank}</td>
                  <th scope="row" className="sc-col-stock"><StockLabel entry={entry} /></th>
                  <td className="num">{formatPrice(entry.close)}</td>
                  <td>
                    {entry.change_pct === null ? (
                      <span className="text-muted">—</span>
                    ) : (
                      <Direction value={entry.change_pct} className="num">{formatSigned(entry.change_pct, 2)}%</Direction>
                    )}
                  </td>
                  <td><ScoreBar value={entry.composite} /></td>
                  {columns.map((column) => (
                    <td key={column.key}><ColumnValue entry={entry} column={column} /></td>
                  ))}
                  <td className="sc-col-star"><WatchlistStar symbol={entry.symbol} variant="icon" /></td>
                </tr>
                {entry.highlights.length > 0 && (
                  <tr className={`sc-highlight-row${open ? " is-open" : ""}`}>
                    <td colSpan={span}><HighlightChips highlights={entry.highlights} /></td>
                  </tr>
                )}
                {open && (
                  <tr className="sc-detail-row" id={`sc-detail-${entry.symbol}`}>
                    <td colSpan={span}>
                      <RowDetail entry={entry} meta={meta} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Phone layout: one card per stock, the composite and the preset's first three columns. */
export function ResultCards(props: ResultsProps) {
  const { entries, meta, columns, expanded, refreshing, onToggle } = props;
  const lead = columns.slice(0, 3);
  return (
    <ol className={`sc-cards${refreshing ? " is-refreshing" : ""}`} aria-busy={refreshing} aria-label="選股結果">
      {entries.map((entry) => {
        const open = expanded.has(entry.symbol);
        return (
          <li key={entry.symbol} className="sc-card">
            <div className="sc-card-top">
              <span className="sc-card-rank num">{entry.rank}</span>
              <StockLabel entry={entry} />
              <span className="sc-card-price"><PriceCell entry={entry} /></span>
            </div>
            <dl className="sc-card-metrics">
              <div>
                <dt>綜合</dt>
                <dd><ScoreBar value={entry.composite} /></dd>
              </div>
              {lead.map((column) => (
                <div key={column.key}>
                  <dt title={column.label}>{column.short}</dt>
                  <dd><ColumnValue entry={entry} column={column} /></dd>
                </div>
              ))}
            </dl>
            {entry.highlights.length > 0 && <HighlightChips highlights={entry.highlights} />}
            <div className="sc-card-foot">
              <WatchlistStar symbol={entry.symbol} variant="icon" />
              <button
                type="button"
                className="button small"
                aria-expanded={open}
                aria-controls={`sc-detail-${entry.symbol}`}
                onClick={() => onToggle(entry.symbol)}
              >
                {open ? "收合" : "詳細資料"}
                <ChevronDownIcon size={14} />
              </button>
            </div>
            {open && (
              <div id={`sc-detail-${entry.symbol}`}>
                <RowDetail entry={entry} meta={meta} />
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function ResultsSkeleton() {
  return (
    <div aria-hidden="true" className="board-skeleton">
      {Array.from({ length: 8 }, (_, i) => (
        <span key={i} className="skeleton block" style={{ height: 52 }} />
      ))}
    </div>
  );
}
