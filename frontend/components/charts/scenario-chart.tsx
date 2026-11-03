"use client";

import { useMemo } from "react";
import type { ForecastReport } from "@/lib/api/types";
import { formatPrice, shortDate } from "@/lib/format";
import { ChartFrame, ChartLegend } from "./chart-frame";
import { linear, niceTicks } from "./scale";

const HISTORY = "var(--series-history)";
const MEDIAN = "var(--series-strategy)";
const BAND = "var(--series-band)";
/** Most recent closes drawn before the forecast origin. */
const MAX_HISTORY = 60;

/**
 * Recent closes, then the 10–90% scenario band around the median path. The
 * forecast region gets a fixed share of the width (and is shaded and labelled)
 * so a 1–5 day horizon stays readable next to 60 days of history.
 */
export function ScenarioChart({ report }: { report: ForecastReport }) {
  const model = useMemo(() => {
    const history = report.history.slice(-MAX_HISTORY);
    const points = report.points;
    const values = [
      ...history.map((p) => p.close),
      ...points.flatMap((p) => [p.lower, p.upper]),
    ];
    const ticks = niceTicks(Math.min(...values), Math.max(...values), 4);
    const forecastShare = Math.min(0.45, Math.max(0.22, 0.18 + points.length * 0.03));
    return { history, points, ticks, forecastShare };
  }, [report]);

  const { history, points, ticks, forecastShare } = model;
  const H = history.length;
  const total = H + points.length;
  if (H === 0 || points.length === 0) return null;

  // x for data index i: history spans [0, 1 - share], forecast the rest.
  const frac = (i: number) => {
    const split = 1 - forecastShare;
    if (i < H) return H === 1 ? 0 : (i / (H - 1)) * split;
    return split + ((i - H + 1) / points.length) * forecastShare;
  };

  return (
    <figure className="chart-figure">
      <ChartFrame
        count={total}
        label={`${report.symbol} 近期收盤價、預測中位數與 10% 至 90% 情境區間，預測 ${report.horizon} 個交易日`}
        margin={{ top: 22, right: 18, bottom: 28, left: 58 }}
        xAt={(i, plot) => plot.left + frac(i) * (plot.right - plot.left)}
        defaultIndex={total - 1}
        readout={(i) => {
          if (i < H) {
            return {
              title: history[i].trading_date,
              rows: [{ label: "收盤價", value: formatPrice(history[i].close), color: HISTORY }],
            };
          }
          const p = points[i - H];
          return {
            title: `${p.trading_date}（預測）`,
            rows: [
              { label: "中位情境", value: formatPrice(p.median), color: MEDIAN },
              { label: "偏高 (90%)", value: formatPrice(p.upper), color: "var(--text-3)" },
              { label: "偏低 (10%)", value: formatPrice(p.lower), color: "var(--text-3)" },
            ],
            note: "情境區間，不是保證",
          };
        }}
        markers={(i, ctx) => {
          const y = linear([ticks[0], ticks[ticks.length - 1]], [ctx.plot.bottom, ctx.plot.top]);
          const x = ctx.plot.left + frac(i) * (ctx.plot.right - ctx.plot.left);
          const value = i < H ? history[i].close : points[i - H].median;
          return <circle cx={x} cy={y(value)} r={4.5} fill={i < H ? HISTORY : MEDIAN} className="chart-dot" />;
        }}
      >
        {(ctx) => {
          const { plot } = ctx;
          const w = plot.right - plot.left;
          const x = (i: number) => plot.left + frac(i) * w;
          const y = linear([ticks[0], ticks[ticks.length - 1]], [plot.bottom, plot.top]);
          const originX = x(H - 1);
          const originY = y(history[H - 1].close);
          const histPath = history.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.close).toFixed(1)}`).join("");
          const medianPath =
            `M${originX.toFixed(1)},${originY.toFixed(1)}` +
            points.map((p, i) => `L${x(H + i).toFixed(1)},${y(p.median).toFixed(1)}`).join("");
          const bandPath =
            `M${originX.toFixed(1)},${originY.toFixed(1)}` +
            points.map((p, i) => `L${x(H + i).toFixed(1)},${y(p.upper).toFixed(1)}`).join("") +
            [...points].reverse().map((p, i) => `L${x(H + points.length - 1 - i).toFixed(1)},${y(p.lower).toFixed(1)}`).join("") +
            "Z";
          const endMedian = points[points.length - 1].median;
          return (
            <>
              <rect className="chart-forecast-zone" x={originX} y={plot.top} width={plot.right - originX} height={plot.bottom - plot.top} />
              {ticks.map((tick) => (
                <g key={tick}>
                  <line className="chart-grid" x1={plot.left} x2={plot.right} y1={y(tick)} y2={y(tick)} />
                  <text x={plot.left - 8} y={y(tick) + 4} textAnchor="end">
                    {formatPrice(tick)}
                  </text>
                </g>
              ))}
              <text x={originX + 6} y={plot.top - 7} className="chart-zone-label">
                預測區（橫軸放大）
              </text>
              <path d={bandPath} fill={BAND} />
              <path d={histPath} className="chart-line" stroke={HISTORY} />
              <path d={medianPath} className="chart-line" stroke={MEDIAN} />
              <circle cx={originX} cy={originY} r={4} fill={HISTORY} className="chart-dot" />
              <circle cx={x(total - 1)} cy={y(endMedian)} r={4} fill={MEDIAN} className="chart-dot" />
              <text x={plot.left} y={plot.bottom + 18} textAnchor="start">
                {history[0].trading_date}
              </text>
              <text x={originX} y={plot.bottom + 18} textAnchor="middle">
                {shortDate(history[H - 1].trading_date)}
              </text>
              <text x={plot.right} y={plot.bottom + 18} textAnchor="end">
                {shortDate(points[points.length - 1].trading_date)}
              </text>
            </>
          );
        }}
      </ChartFrame>
      <ChartLegend
        items={[
          { label: "歷史收盤價", color: HISTORY },
          { label: "預測中位數", color: MEDIAN },
          { label: "10–90% 情境區間", color: BAND, band: true },
        ]}
      />
    </figure>
  );
}
