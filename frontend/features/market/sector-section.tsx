"use client";

import { useEffect, useMemo, useState } from "react";
import { Card } from "@/components/card";
import { Direction } from "@/components/direction";
import { EmptyState } from "@/components/empty-state";
import { ClockIcon, InfoIcon } from "@/components/icons";
import { SegmentedControl } from "@/components/segmented-control";
import { WatchlistStar } from "@/components/watchlist-star";
import { getMarketSectors, getSectorStocks } from "@/lib/api/endpoints";
import type { SectorRow, SectorStockEntry, SectorStockSort } from "@/lib/api/types";
import {
  directionOf,
  formatNumber,
  formatPrice,
  formatSigned,
  formatSignedLots,
  formatSignedYi,
  formatTwdCompact,
  today,
} from "@/lib/format";
import {
  dataTimeLabel,
  isTradingSession,
  QUADRANTS,
  SECTOR_REFRESH_MS,
  SECTOR_WINDOWS,
  type SectorSortKey,
  sectorReturn,
  sortSectors,
} from "./sector-math";
import { SectorRrgChart } from "./sector-rrg";
import { useMarketResource } from "./use-market-resource";

const WINDOW_OPTIONS = SECTOR_WINDOWS.map((value) => ({ value, label: `${value}日` }));
const STOCK_SORTS: { value: SectorStockSort; label: string }[] = [
  { value: "window_return", label: "區間漲幅" },
  { value: "day_change", label: "當日漲跌" },
  { value: "turnover", label: "成交值" },
  { value: "institutional", label: "法人買超" },
];
const toneClass = (value: number) =>
  directionOf(value) === "up" ? "text-up" : directionOf(value) === "down" ? "text-down" : "";

/** Share difference (0-1) as percentage points at display precision, so a tiny move reads 0.00 not −0.00. */
const roundedPoints = (share: number) => Math.round(share * 10_000) / 100;

type Column ={ key: SectorSortKey; label: string; numeric: boolean };

function columns(window: number): Column[] {
  return [
    { key: "name", label: "族群", numeric: false },
    { key: "ret", label: `${window}日漲跌`, numeric: true },
    { key: "share", label: "成交占比", numeric: true },
    { key: "shareChange", label: "占比變化", numeric: true },
    { key: "breadth", label: "上漲家數", numeric: true },
    { key: "institutional", label: "法人買賣超", numeric: true },
  ];
}

function SortHeader({
  column,
  active,
  direction,
  onSort,
}: {
  column: Column;
  active: boolean;
  direction: "asc" | "desc";
  onSort: (key: SectorSortKey) => void;
}) {
  return (
    <th
      scope="col"
      className={column.numeric ? "num-col" : "name-col"}
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
    >
      <button type="button" className="sort-button" onClick={() => onSort(column.key)}>
        {column.label}
        <span aria-hidden="true" className="sort-mark">
          {active ? (direction === "asc" ? "▲" : "▼") : ""}
        </span>
      </button>
    </th>
  );
}

function SectorTableRow({
  row,
  window,
  selected,
  onSelect,
}: {
  row: SectorRow;
  window: number;
  selected: boolean;
  onSelect: (code: string) => void;
}) {
  const ret = sectorReturn(row, window);
  return (
    <tr className={`sector-row${selected ? " is-selected" : ""}`} onClick={() => onSelect(row.code)}>
      <th scope="row" className="name-col">
        <button
          type="button"
          className="sector-name"
          aria-pressed={selected}
          aria-label={`${row.name}，${row.stock_count} 檔，查看成分股`}
        >
          <span>{row.name}</span>
          <small className="num">{row.stock_count} 檔</small>
        </button>
        {row.rrg && (
          <span className={`quadrant-chip q-${row.rrg.quadrant}`}>{QUADRANTS[row.rrg.quadrant].label}</span>
        )}
      </th>
      <td className="num-col" data-label={`${window}日漲跌`}>
        {ret === null ? (
          "—"
        ) : (
          <Direction value={ret} className="num">
            {formatSigned(ret, 2)}%
          </Direction>
        )}
      </td>
      <td className="num-col num" data-label="成交占比">
        {(row.turnover_share * 100).toFixed(1)}%
      </td>
      <td className="num-col" data-label="占比變化">
        {row.turnover_share_change === null ? (
          "—"
        ) : (
          <Direction value={roundedPoints(row.turnover_share_change)} className="num">
            {formatSigned(roundedPoints(row.turnover_share_change), 2)} 點
          </Direction>
        )}
      </td>
      <td className="num-col" data-label="上漲家數">
        {row.breadth_up_pct === null ? (
          "—"
        ) : (
          <span className="breadth num" title={`上漲 ${row.up_count}／下跌 ${row.down_count}／平盤 ${row.flat_count}`}>
            {Math.round(row.breadth_up_pct)}%
            <span className="breadth-bar" aria-hidden="true">
              <span style={{ width: `${row.breadth_up_pct}%` }} />
            </span>
          </span>
        )}
      </td>
      <td className="num-col num" data-label="法人買賣超">
        {row.institutional_net_value === null ? (
          <span className="muted" title="這段期間的法人報告尚未公布">
            未公布
          </span>
        ) : (
          <span className={toneClass(row.institutional_net_value)}>{formatSignedYi(row.institutional_net_value)} 億</span>
        )}
      </td>
    </tr>
  );
}

function StockRows({
  entries,
  onPick,
}: {
  entries: SectorStockEntry[];
  onPick: (symbol: string) => void;
}) {
  return (
    <>
      {entries.map((entry) => {
        const code = entry.symbol.replace(/\.TWO?$/, "");
        return (
          <tr key={entry.symbol} className="sector-stock-row" onClick={() => onPick(entry.symbol)}>
            <th scope="row" className="name-col">
              <div className="stock-cell">
                <button
                  type="button"
                  className="stock-link"
                  aria-label={`${entry.name || code}（${code}），查看今日訊號`}
                >
                  <span className="stock-code num">{code}</span>
                  <span className="stock-name" title={entry.name}>
                    {entry.name}
                  </span>
                  <span className="market-tag">{entry.market === "TWO" ? "上櫃" : "上市"}</span>
                </button>
                <WatchlistStar symbol={entry.symbol} variant="icon" stopPropagation />
              </div>
            </th>
            <td className="num-col num" data-label="收盤">
              {formatPrice(entry.close)}
            </td>
            <td className="num-col" data-label="當日">
              {entry.day_change_pct === null ? (
                "—"
              ) : (
                <Direction value={entry.day_change_pct} className="num">
                  {formatSigned(entry.day_change_pct, 2)}%
                </Direction>
              )}
            </td>
            <td className="num-col" data-label="區間">
              {entry.window_return_pct === null ? (
                "—"
              ) : (
                <span className={`num ${toneClass(entry.window_return_pct)}`}>
                  {formatSigned(entry.window_return_pct, 2)}%
                </span>
              )}
            </td>
            <td className="num-col num" data-label="成交值">
              {formatTwdCompact(entry.turnover_value)}
            </td>
            <td className="num-col num" data-label="法人（張）">
              {entry.institutional_net_lots === null ? (
                <span className="muted">未公布</span>
              ) : (
                <span className={toneClass(entry.institutional_net_lots)}>
                  {formatSignedLots(entry.institutional_net_lots)}
                </span>
              )}
            </td>
          </tr>
        );
      })}
    </>
  );
}

export function SectorSection({ asOf, onPick }: { asOf: string; onPick: (symbol: string) => void }) {
  const [window, setWindow] = useState<number>(5);
  const [sortKey, setSortKey] = useState<SectorSortKey>("share");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [selected, setSelected] = useState<string | null>(null);
  const [stockSort, setStockSort] = useState<SectorStockSort>("window_return");

  const sectors = useMarketResource(`sectors|${window}|${asOf}`, (signal) =>
    getMarketSectors({ window, asOf }, { signal }),
  );
  const stocks = useMarketResource(
    selected ? `sector-stocks|${selected}|${window}|${stockSort}|${asOf}` : null,
    (signal) => getSectorStocks({ code: selected ?? "", window, sort: stockSort, asOf }, { signal }),
  );

  // Follow the intraday collector: re-ask every 10 minutes while the market is open.
  const { retry: reloadSectors } = sectors;
  const { retry: reloadStocks } = stocks;
  const viewingToday = !asOf || asOf >= today();
  useEffect(() => {
    if (!viewingToday) return;
    const timer = globalThis.setInterval(() => {
      if (!isTradingSession(new Date())) return;
      reloadSectors();
      reloadStocks();
    }, SECTOR_REFRESH_MS);
    return () => globalThis.clearInterval(timer);
  }, [viewingToday, reloadSectors, reloadStocks]);

  const data = sectors.data;
  const rows = useMemo(
    () => (data ? sortSectors(data.sectors, sortKey, direction, window) : []),
    [data, sortKey, direction, window],
  );
  const selectedRow = data?.sectors.find((row) => row.code === selected) ?? null;

  const onSort = (key: SectorSortKey) => {
    if (key === sortKey) setDirection((d) => (d === "desc" ? "asc" : "desc"));
    else {
      setSortKey(key);
      setDirection(key === "name" ? "asc" : "desc");
    }
  };
  const toggle = (code: string) => setSelected((current) => (current === code ? null : code));
  const busy = sectors.stale || sectors.loading;
  const live = data?.mode === "intraday";

  return (
    <section className="sector-section" aria-label="族群輪動">
      <div className="view-heading sector-heading">
        <h2>族群輪動</h2>
        <p>看資金與強弱在產業之間怎麼換手：成交占比、漲跌、上漲家數，以及相對大盤的強弱象限。</p>
      </div>

      <Card
        title="族群強弱"
        sub={
          data && data.data_through ? (
            <>
              <span className={`chip ${live ? "live" : "plain"} num`}>{dataTimeLabel(data)}</span>
              {live && <span className="refresh-note"> 每 10 分鐘自動更新</span>}
            </>
          ) : undefined
        }
        aside={
          <SegmentedControl label="族群統計期間" value={window} options={WINDOW_OPTIONS} onChange={setWindow} />
        }
        className="sector-card"
      >
        {data?.intraday_status === "unavailable" && (
          <p className="callout info" role="status">
            <ClockIcon size={16} />
            <span>盤中族群資料尚未啟用，以下為最近收盤資料</span>
          </p>
        )}
        {data && !data.classification_ready && (
          <p className="callout info" role="status">
            <InfoIcon size={16} />
            <span>產業分類還在同步中（每週更新一次），完成後這裡會出現族群列表。</span>
          </p>
        )}
        {sectors.error && !data && <EmptyState icon={<InfoIcon size={22} />} title="暫時無法載入族群資料">{sectors.error}</EmptyState>}
        {!data && !sectors.error && (
          <div aria-hidden="true" className="board-skeleton">
            {Array.from({ length: 8 }, (_, i) => (
              <span key={i} className="skeleton block" style={{ height: 40 }} />
            ))}
          </div>
        )}
        {data && rows.length === 0 && data.classification_ready && (
          <EmptyState icon={data.backfill_pending ? <ClockIcon size={22} /> : <InfoIcon size={22} />} title={data.backfill_pending ? "資料回補中" : "沒有可用的族群資料"}>
            {data.backfill_pending ? "歷史資料還在下載，完成後族群列表會自動出現。" : "這個日期沒有可分析的個股資料。"}
          </EmptyState>
        )}
        {rows.length > 0 && data && (
          <div className={busy ? "is-refreshing" : undefined} aria-busy={busy}>
            <p className="scroll-hint">表格可左右滑動，查看其他欄位</p>
            <div className="table-wrap sector-table-wrap" tabIndex={0} role="region" aria-label="族群強弱表">
              <table className="sector-table">
                <caption className="sr-only">
                  各產業的{window}日漲跌、成交占比與變化、上漲家數比例、法人買賣超，點選族群查看成分股
                </caption>
                <thead>
                  <tr>
                    {columns(window).map((column) => (
                      <SortHeader
                        key={column.key}
                        column={column}
                        active={sortKey === column.key}
                        direction={direction}
                        onSort={onSort}
                      />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <SectorTableRow
                      key={row.code}
                      row={row}
                      window={window}
                      selected={row.code === selected}
                      onSelect={toggle}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            <p className="estimate-note">
              <InfoIcon size={14} />
              <span>
                「資金流向」以各族群的成交金額占全市場比例近似，並非實際資金進出；占比變化＝目前占比減去近 20 個交易日平均（單位：百分點）。
                漲跌為族群內個股等權平均；法人買賣超為三大法人合計
                {data.institutional_through ? `（資料至 ${data.institutional_through.slice(5).replace("-", "/")}）` : ""}
                ，盤中不含當日。另有 {formatNumber(data.unclassified_count, 0)} 檔未歸類，已排除。
              </span>
            </p>
          </div>
        )}
      </Card>

      {data && rows.some((row) => row.rrg) && (
        <Card
          title="相對強弱象限"
          sub="橫軸：相對大盤的強弱；縱軸：強弱的動能。尾巴是最近 5 個交易日的軌跡"
          className="sector-card"
        >
          <SectorRrgChart rows={data.sectors} selected={selected} onSelect={toggle} />
        </Card>
      )}

      {selected && (
        <Card
          title={`${selectedRow?.name ?? stocks.data?.name ?? "族群"}成分股`}
          sub={
            selectedRow?.rrg
              ? `${QUADRANTS[selectedRow.rrg.quadrant].label}象限 · ${QUADRANTS[selectedRow.rrg.quadrant].hint}`
              : undefined
          }
          aside={
            <SegmentedControl label="成分股排序" value={stockSort} options={STOCK_SORTS} onChange={setStockSort} wrap />
          }
          className="sector-card sector-stocks"
        >
          {!stocks.data ? (
            <div aria-hidden="true" className="board-skeleton">
              {Array.from({ length: 5 }, (_, i) => (
                <span key={i} className="skeleton block" style={{ height: 40 }} />
              ))}
            </div>
          ) : stocks.data.entries.length === 0 ? (
            <EmptyState icon={<InfoIcon size={22} />} title="沒有成分股資料">
              這個族群在目前資料日沒有可排序的個股。
            </EmptyState>
          ) : (
            <div
              className={`table-wrap sector-table-wrap${stocks.stale || stocks.loading ? " is-refreshing" : ""}`}
              tabIndex={0}
              role="region"
              aria-label={`${stocks.data.name}成分股`}
            >
              <table className="sector-table sector-stock-table">
                <thead>
                  <tr>
                    <th scope="col" className="name-col">
                      股票
                    </th>
                    <th scope="col" className="num-col">
                      收盤
                    </th>
                    <th scope="col" className="num-col">
                      當日
                    </th>
                    <th scope="col" className="num-col">
                      {window}日漲幅
                    </th>
                    <th scope="col" className="num-col">
                      成交值
                    </th>
                    <th scope="col" className="num-col">
                      法人（張）
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <StockRows entries={stocks.data.entries} onPick={onPick} />
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </section>
  );
}
