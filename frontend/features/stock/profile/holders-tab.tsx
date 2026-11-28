"use client";

import { ProfileChart } from "./charts/profile-chart";
import { getStockHolders, type StockHolders } from "@/lib/api/profile";
import { formatNumber, formatSigned } from "@/lib/format";
import { emptyReason, formatInt, formatPercent, formatYiFromTwd, holdersHistoryNote, marketCap, toneOf, weeklyChange } from "./model";
import { Boundary, DataDate, EmptyLine, Tile, TileGrid } from "./parts";
import { profileKey, useProfileResource } from "./use-profile-resource";

const WEEKS = 52;

export function HoldersBody({ data, close }: { data: StockHolders; close: number | null }) {
  const series = data.series;
  const latest = series.length > 0 ? series[series.length - 1] : null;
  const d400 = weeklyChange(series, (p) => p.pct_ge400);
  const d1000 = weeklyChange(series, (p) => p.pct_ge1000);
  const cap = marketCap(data.shares_outstanding, close);
  const note = holdersHistoryNote(data);
  return (
    <>
      <DataDate>
        {latest ? `集保週資料 ${latest.data_date}` : "集保週資料尚無"}
        {data.foreign_data_date ? `・外資持股 ${data.foreign_data_date}` : ""}
      </DataDate>
      <TileGrid>
        <Tile
          label="≥ 400 張大戶持股"
          value={latest ? formatPercent(latest.pct_ge400, 2) : "—"}
          note={
            d400 === null ? "週變化需要兩週資料" : <span className={toneOf(d400)}>較上週 {formatSigned(d400, 2)} 個百分點</span>
          }
        >
          {latest && <span className="stat-note">{formatInt(latest.holders_ge400)} 人</span>}
        </Tile>
        <Tile
          label="≥ 1,000 張大戶持股"
          value={latest ? formatPercent(latest.pct_ge1000, 2) : "—"}
          note={
            d1000 === null ? "週變化需要兩週資料" : <span className={toneOf(d1000)}>較上週 {formatSigned(d1000, 2)} 個百分點</span>
          }
        >
          {latest && <span className="stat-note">{formatInt(latest.holders_ge1000)} 人</span>}
        </Tile>
        <Tile
          label="外資持股比例"
          value={formatPercent(data.foreign_holding_pct, 2)}
          note={data.foreign_holding_pct === null ? "尚無外資持股資料" : undefined}
        />
        <Tile
          label="發行股數"
          value={data.shares_outstanding === null ? "—" : `${formatNumber(data.shares_outstanding / 1e8, 1)} 億股`}
          note={cap === null ? "收盤價未取得，不計市值" : `市值約 ${formatYiFromTwd(cap, 0)}（以選股快照收盤價估）`}
        />
      </TileGrid>
      {series.length === 0 ? (
        <EmptyLine>{emptyReason("holders", data.note)}</EmptyLine>
      ) : (
        <ProfileChart
          title="大戶持股比例（週）"
          dates={series.map((p) => p.data_date)}
          series={[
            {
              key: "g400",
              label: "≥ 400 張",
              kind: "line",
              values: series.map((p) => p.pct_ge400),
              color: "var(--series-strategy)",
              format: (v) => formatPercent(v, 2),
            },
            {
              key: "g1000",
              label: "≥ 1,000 張",
              kind: "line",
              values: series.map((p) => p.pct_ge1000),
              color: "var(--series-ma5)",
              dash: "6 4",
              legendDash: true,
              format: (v) => formatPercent(v, 2),
            },
          ]}
          tickFormat={(v) => `${formatNumber(v, v % 1 === 0 ? 0 : 1)}%`}
          extraRows={(i) => [
            { label: "≥ 400 張人數", value: `${formatInt(series[i].holders_ge400)} 人` },
            { label: "≥ 1,000 張人數", value: `${formatInt(series[i].holders_ge1000)} 人` },
            { label: "總股東人數", value: `${formatInt(series[i].total_holders)} 人` },
          ]}
          height={200}
        />
      )}
      {note && <p className="pf-reason">{note}</p>}
      {data.note && <p className="pf-reason">{data.note}</p>}
    </>
  );
}

export function HoldersTab({ symbol, asOf, close }: { symbol: string; asOf: string; close: number | null }) {
  const resource = useProfileResource<StockHolders>(profileKey("holders", symbol, asOf, String(WEEKS)), (signal) =>
    getStockHolders(symbol, asOf, WEEKS, { signal }),
  );
  return (
    <Boundary resource={resource} label="大戶與外資">
      {(data) => <HoldersBody data={data} close={close} />}
    </Boundary>
  );
}
