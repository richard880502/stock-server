"use client";

import { ProfileChart } from "./charts/profile-chart";
import { getStockFutures, type StockFutures } from "@/lib/api/profile";
import { formatNumber } from "@/lib/format";
import { emptyReason, formatInt, formatSignedPercent, latestFuturesDay, toneOf } from "./model";
import { Boundary, DataDate, EmptyLine, Notes, Tile, TileGrid } from "./parts";
import { profileKey, useProfileResource } from "./use-profile-resource";

const DAYS = 60;

export function FuturesBody({ data }: { data: StockFutures }) {
  if (!data.has_futures) {
    return (
      <>
        <EmptyLine>{emptyReason("futures")}</EmptyLine>
        <Notes notes={data.notes} />
      </>
    );
  }
  const latest = latestFuturesDay(data);
  const days = data.days;
  const dates = days.map((d) => d.date);
  const basis = latest?.basis_pct ?? null;
  return (
    <>
      <DataDate>
        {latest ? `資料日 ${latest.date}` : "尚無資料"}・未平倉與成交量為標準契約等值口數
      </DataDate>
      <TileGrid>
        <Tile label="未平倉（等值口）" value={latest ? formatInt(latest.open_interest) : "—"} />
        <Tile label="未平倉 1 日變化" value={formatSignedPercent(data.oi_change_1d, 2)} tone={toneOf(data.oi_change_1d)} note={data.oi_change_1d === null ? "需要連續兩個交易日" : undefined} />
        <Tile label="未平倉 5 日變化" value={formatSignedPercent(data.oi_change_5d, 2)} tone={toneOf(data.oi_change_5d)} note={data.oi_change_5d === null ? "需要 6 個交易日以上的資料" : undefined} />
        <Tile
          label="近月基差"
          value={formatSignedPercent(basis, 2)}
          tone={toneOf(basis)}
          note={latest?.near_month ? `近月 ${latest.near_month}・(結算價 − 現貨) ÷ 現貨` : "現貨或結算價缺漏時為空"}
        />
      </TileGrid>
      <h3 className="pf-subtitle">未平倉（折線，左軸）與成交量（長條，右軸）</h3>
      <ProfileChart
        title="個股期貨未平倉與成交量"
        dates={dates}
        series={[
          {
            key: "vol",
            label: "成交量（口）",
            kind: "bar",
            axis: "right",
            values: days.map((d) => d.volume),
            color: "var(--series-baseline)",
            format: (v) => formatInt(v),
          },
          {
            key: "oi",
            label: "未平倉（口）",
            kind: "line",
            values: days.map((d) => d.open_interest),
            color: "var(--series-strategy)",
            format: (v) => formatInt(v),
          },
        ]}
        tickFormat={(v) => formatInt(v)}
        rightTickFormat={(v) => formatInt(v)}
        extraRows={(i) => [{ label: "近月", value: days[i].near_month ?? "—" }]}
        markLast
        height={210}
      />
      <h3 className="pf-subtitle">近月基差 %（零軸之上為期貨高於現貨）</h3>
      {days.some((d) => d.basis_pct !== null) ? (
        <ProfileChart
          title="近月基差"
          dates={dates}
          series={[
            {
              key: "basis",
              label: "基差 %",
              kind: "line",
              values: days.map((d) => d.basis_pct),
              color: "var(--series-ma20)",
              format: (v) => `${formatNumber(v, 2)}%`,
            },
          ]}
          includeZero
          tickFormat={(v) => `${formatNumber(v, 1)}%`}
          hideLegend
          markLast
          height={150}
        />
      ) : (
        <EmptyLine>現貨收盤價或近月結算價缺漏，這段期間算不出基差。</EmptyLine>
      )}
      <Notes notes={data.notes} />
    </>
  );
}

export function FuturesTab({ symbol, asOf }: { symbol: string; asOf: string }) {
  const resource = useProfileResource<StockFutures>(profileKey("futures", symbol, asOf, String(DAYS)), (signal) =>
    getStockFutures(symbol, asOf, DAYS, { signal }),
  );
  return (
    <Boundary resource={resource} label="期貨">
      {(data) => <FuturesBody data={data} />}
    </Boundary>
  );
}
