"use client";

import { useMemo } from "react";
import { Card } from "@/components/card";
import { ChartFrame, ChartLegend } from "@/components/charts/chart-frame";
import { compactTwd, linear, niceTicks, sampleIndices } from "@/components/charts/scale";
import { SegmentedControl } from "@/components/segmented-control";
import type { EquityPoint } from "@/lib/api/paper";
import { shortDate } from "@/lib/format";
import { type EquityRange, formatMoney, rebaseBenchmark } from "./paper-math";

const STRATEGY = "var(--series-strategy)";
const BASELINE = "var(--series-baseline)";

const RANGES: { value: EquityRange; label: string }[] = [
  { value: "1m", label: "1 月" },
  { value: "3m", label: "3 月" },
  { value: "all", label: "全部" },
];

/** Account equity (daily snapshots + today's live point) against 加權指數 rebased to the same start. */
export function EquityCard({
  points,
  loading,
  stale,
  range,
  onRange,
}: {
  points: EquityPoint[] | null;
  loading: boolean;
  stale: boolean;
  range: EquityRange;
  onRange: (range: EquityRange) => void;
}) {
  const series = useMemo(() => rebaseBenchmark(points ?? []), [points]);
  const count = series.equity.length;
  return (
    <Card
      title="權益曲線"
      sub="每日收盤快照加上今天的即時點；加權指數以同一起點換算"
      aside={<SegmentedControl label="曲線區間" value={range} options={RANGES} onChange={onRange} />}
      className="paper-equity"
    >
      <div className={stale ? "is-refreshing" : undefined}>
        {points === null && loading ? (
          <span className="skeleton block paper-chart-skeleton" aria-label="載入中" />
        ) : count < 2 ? (
          <p className="paper-empty paper-chart-empty">明天起這裡會畫出你的權益曲線</p>
        ) : (
          <EquityLines series={series} />
        )}
      </div>
    </Card>
  );
}

function EquityLines({ series }: { series: ReturnType<typeof rebaseBenchmark> }) {
  const { dates, equity, benchmark, hasBenchmark } = series;
  const n = equity.length;
  const values = [...equity, ...benchmark.filter((v): v is number => v !== null)];
  const ticks = niceTicks(Math.min(...values), Math.max(...values), 4);
  const domain: [number, number] = [ticks[0], ticks[ticks.length - 1]];
  return (
    <figure className="chart-figure paper-chart">
      <ChartFrame
        count={n}
        label={`模擬帳戶權益曲線${hasBenchmark ? "與加權指數" : ""}，${dates[0]} 至 ${dates[n - 1]}`}
        margin={{ top: 12, right: 16, bottom: 26, left: 62 }}
        xAt={(i, plot) => plot.left + (i / (n - 1)) * (plot.right - plot.left)}
        defaultIndex={n - 1}
        readout={(i) => ({
          title: dates[i],
          rows: [
            { label: "總權益", value: formatMoney(equity[i]), color: STRATEGY },
            ...(benchmark[i] != null
              ? [{ label: "加權指數（換算）", value: formatMoney(benchmark[i] as number), color: BASELINE, dashed: true }]
              : []),
          ],
        })}
        markers={(i, ctx) => {
          const y = linear(domain, [ctx.plot.bottom, ctx.plot.top]);
          const x = ctx.plot.left + (i / (n - 1)) * (ctx.plot.right - ctx.plot.left);
          return (
            <>
              {benchmark[i] != null && <circle cx={x} cy={y(benchmark[i] as number)} r={4} fill={BASELINE} className="chart-dot" />}
              <circle cx={x} cy={y(equity[i])} r={4.5} fill={STRATEGY} className="chart-dot" />
            </>
          );
        }}
      >
        {({ plot, width }) => {
          const x = (i: number) => plot.left + (i / (n - 1)) * (plot.right - plot.left);
          const y = linear(domain, [plot.bottom, plot.top]);
          const equityPath = equity.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
          // The benchmark can start later than the account: break the path at gaps.
          let benchPath = "";
          let pen = false;
          benchmark.forEach((v, i) => {
            if (v === null) {
              pen = false;
              return;
            }
            benchPath += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
            pen = true;
          });
          const labels = sampleIndices(n, width < 520 ? 3 : 5);
          return (
            <>
              {ticks.map((tick) => (
                <g key={tick}>
                  <line className="chart-grid" x1={plot.left} x2={plot.right} y1={y(tick)} y2={y(tick)} />
                  <text x={plot.left - 8} y={y(tick) + 4} textAnchor="end">
                    {compactTwd(tick)}
                  </text>
                </g>
              ))}
              {hasBenchmark && <path d={benchPath} className="chart-line" stroke={BASELINE} strokeDasharray="5 4" />}
              <path d={equityPath} className="chart-line" stroke={STRATEGY} />
              {labels.map((i, k) => (
                <text
                  key={i}
                  x={x(i)}
                  y={plot.bottom + 18}
                  textAnchor={k === 0 ? "start" : k === labels.length - 1 ? "end" : "middle"}
                >
                  {shortDate(dates[i])}
                </text>
              ))}
              <circle cx={x(n - 1)} cy={y(equity[n - 1])} r={4} fill={STRATEGY} className="chart-dot" />
            </>
          );
        }}
      </ChartFrame>
      <ChartLegend
        items={[
          { label: "總權益", color: STRATEGY },
          ...(hasBenchmark ? [{ label: "加權指數（換算至同一起點）", color: BASELINE, dashed: true }] : []),
        ]}
      />
    </figure>
  );
}
