"use client";

import { ProfileChart } from "./charts/profile-chart";
import { getStockShortInterest, type StockShortInterest } from "@/lib/api/profile";
import { formatNumber } from "@/lib/format";
import {
  dayTradeReason,
  emptyReason,
  formatInt,
  formatPercent,
  formatSignedPercent,
  latestShortDay,
  toneOf,
} from "./model";
import { Boundary, DataDate, EmptyLine, Notes, Tile, TileGrid } from "./parts";
import { profileKey, useProfileResource } from "./use-profile-resource";

const DAYS = 60;

export function ShortBody({ data }: { data: StockShortInterest }) {
  const days = data.days;
  const latest = latestShortDay(data);
  const dayTradeNote = dayTradeReason(data.symbol, days);
  const dates = days.map((d) => d.date);
  const hasSbl = days.some((d) => d.sbl_balance_lots !== null);
  const insiders = data.insiders;
  return (
    <>
      <DataDate>{latest ? `資料日 ${latest.date}` : `查詢日 ${data.as_of}`}・借券為交易所公告餘額，換算為張</DataDate>
      <TileGrid>
        <Tile
          label="借券賣出餘額"
          value={latest ? `${formatInt(latest.sbl_balance_lots)} 張` : "—"}
          note={data.sbl_pct_of_shares === null ? "佔股本需要發行股數" : `佔發行股數 ${formatNumber(data.sbl_pct_of_shares, 2)}%`}
        />
        <Tile label="借券 5 日變化" value={formatSignedPercent(data.sbl_change_5d_pct)} tone={toneOf(data.sbl_change_5d_pct)} note={data.sbl_change_5d_pct === null ? "需要 5 個交易日以上的資料" : undefined} />
        <Tile label="借券 20 日變化" value={formatSignedPercent(data.sbl_change_20d_pct)} tone={toneOf(data.sbl_change_20d_pct)} note={data.sbl_change_20d_pct === null ? "需要 20 個交易日以上的資料" : undefined} />
        <Tile
          label="回補天數"
          value={data.days_to_cover === null ? "—" : `${formatNumber(data.days_to_cover, 2)} 天`}
          note="借券餘額 ÷ 近 20 日均量"
        />
      </TileGrid>
      {hasSbl ? (
        <ProfileChart
          title="借券賣出餘額與融券餘額"
          dates={dates}
          series={[
            {
              key: "sbl",
              label: "借券餘額（張，左軸）",
              kind: "line",
              values: days.map((d) => d.sbl_balance_lots),
              color: "var(--series-strategy)",
              format: (v) => `${formatInt(v)} 張`,
            },
            {
              key: "margin",
              label: "融券餘額（張，右軸）",
              kind: "line",
              axis: "right",
              values: days.map((d) => d.margin_short_balance_lots),
              color: "var(--series-ma5)",
              dash: "6 4",
              legendDash: true,
              format: (v) => `${formatInt(v)} 張`,
            },
          ]}
          tickFormat={(v) => formatInt(v)}
          rightTickFormat={(v) => formatInt(v)}
          extraRows={(i) => [
            { label: "當日借券賣出", value: `${formatInt(days[i].sbl_sold_lots)} 張` },
            { label: "當日還券", value: `${formatInt(days[i].sbl_returned_lots)} 張` },
          ]}
          markLast
          height={210}
        />
      ) : (
        <EmptyLine>{emptyReason("short")}</EmptyLine>
      )}

      <h3 className="pf-subtitle">現股當沖</h3>
      <TileGrid>
        <Tile label="當沖比 5 日" value={formatPercent(data.day_trade_ratio_5d_pct)} note="成交量加權" />
        <Tile label="當沖比 20 日" value={formatPercent(data.day_trade_ratio_20d_pct)} note={data.day_trade_ratio_20d_pct === null && !dayTradeNote ? "需要 20 個交易日以上的資料" : "成交量加權"} />
        <Tile
          label="董監質押比例"
          value={insiders?.pledge_ratio_pct == null ? "—" : formatPercent(insiders.pledge_ratio_pct, 2)}
          note={
            insiders
              ? `${insiders.period} 月資料・質押 ${formatInt(insiders.pledged_shares / 1000)} 張 / 持股 ${formatInt(insiders.holding_shares / 1000)} 張`
              : "尚無董監持股申報資料"
          }
        />
      </TileGrid>
      {dayTradeNote ? (
        <EmptyLine>{dayTradeNote}</EmptyLine>
      ) : (
        <ProfileChart
          title="當沖比（每日）"
          dates={dates}
          series={[
            {
              key: "dt",
              label: "當沖比",
              kind: "bar",
              values: days.map((d) => d.day_trade_ratio_pct),
              color: "var(--series-baseline)",
              format: (v) => formatPercent(v),
            },
          ]}
          tickFormat={(v) => `${formatNumber(v, 0)}%`}
          note="當沖成交股數於 T+1、T+2 可能被交易所更新"
          height={170}
        />
      )}
      <Notes notes={data.notes} />
    </>
  );
}

export function ShortTab({ symbol, asOf }: { symbol: string; asOf: string }) {
  const resource = useProfileResource<StockShortInterest>(profileKey("short", symbol, asOf, String(DAYS)), (signal) =>
    getStockShortInterest(symbol, asOf, DAYS, { signal }),
  );
  return (
    <Boundary resource={resource} label="放空與當沖">
      {(data) => <ShortBody data={data} />}
    </Boundary>
  );
}
