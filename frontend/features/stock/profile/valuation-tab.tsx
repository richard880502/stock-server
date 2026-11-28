"use client";

import { ProfileChart } from "./charts/profile-chart";
import { getStockFundamentals, type StockFundamentals, type ValuationSection } from "@/lib/api/profile";
import { formatNumber } from "@/lib/format";
import { emptyReason } from "./model";
import { Boundary, DataDate, EmptyLine, Notes, PercentileBar, Tile, TileGrid } from "./parts";
import { profileKey, useProfileResource } from "./use-profile-resource";

export function useFundamentals(symbol: string, asOf: string, enabled = true) {
  return useProfileResource<StockFundamentals>(
    enabled ? profileKey("fundamentals", symbol, asOf) : null,
    (signal) => getStockFundamentals(symbol, asOf, { signal }),
  );
}

type Metric = {
  key: "pe" | "pb" | "yield";
  label: string;
  value: number | null;
  percentile: number | null;
  unit: string;
  pick: (point: ValuationSection["history"][number]) => number | null;
  missing?: string;
};

export function valuationMetrics(v: ValuationSection): Metric[] {
  return [
    {
      key: "pe",
      label: "本益比",
      value: v.pe_ratio,
      percentile: v.pe_percentile_250d,
      unit: "倍",
      pick: (p) => p.pe_ratio,
      missing: "虧損，無本益比",
    },
    { key: "pb", label: "股價淨值比", value: v.pb_ratio, percentile: v.pb_percentile_250d, unit: "倍", pick: (p) => p.pb_ratio },
    {
      key: "yield",
      label: "殖利率",
      value: v.dividend_yield_pct,
      percentile: v.yield_percentile_250d,
      unit: "%",
      pick: (p) => p.dividend_yield_pct,
      missing: "沒有股利資料",
    },
  ];
}

export function ValuationBody({ data }: { data: StockFundamentals }) {
  const v = data.valuation;
  if (!v) return <EmptyLine>{emptyReason("valuation")}</EmptyLine>;
  const dates = v.history.map((p) => p.date);
  return (
    <>
      <DataDate>資料日 {v.date}・分位為該股自身最近 250 個交易日的相對位置</DataDate>
      <TileGrid wide>
        {valuationMetrics(v).map((m) => {
          const series = v.history.map(m.pick);
          const text = (n: number) => `${formatNumber(n, 2)}${m.unit === "%" ? "%" : ""}`;
          return (
            <Tile
              key={m.key}
              label={m.label}
              value={
                m.value === null ? (
                  <span className="pf-value-missing">{m.missing ?? "—"}</span>
                ) : (
                  <>
                    {formatNumber(m.value, 2)}
                    <span className="pf-unit">{m.unit}</span>
                  </>
                )
              }
            >
              <PercentileBar
                value={m.value}
                percentile={m.percentile}
                label={m.label}
                progress={{ observations: v.observations, required: v.observations_required }}
              />
              {series.some((x) => x !== null) && (
                <ProfileChart
                  title={`${m.label}歷史（週）`}
                  dates={dates}
                  series={[{ key: m.key, label: m.label, kind: "line", values: series, color: "var(--series-strategy)", format: text }]}
                  tickFormat={(n) => formatNumber(n, n % 1 === 0 ? 0 : 1)}
                  height={130}
                  markLast
                  hideLegend
                />
              )}
              {series.filter((x) => x !== null).length < 4 && (
                <span className="stat-note">歷史僅 {v.history.length} 週，逐週累積中。</span>
              )}
            </Tile>
          );
        })}
      </TileGrid>
    </>
  );
}

export function ValuationTab({ symbol, asOf }: { symbol: string; asOf: string }) {
  const resource = useFundamentals(symbol, asOf);
  return (
    <Boundary resource={resource} label="估值">
      {(data) => (
        <>
          <ValuationBody data={data} />
          <Notes notes={data.notes} />
        </>
      )}
    </Boundary>
  );
}
