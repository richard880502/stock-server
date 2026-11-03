"use client";

import { useId, useMemo } from "react";
import type { MarketFlowDay } from "@/lib/api/types";
import { formatSigned, shortDate } from "@/lib/format";
import { ChartFrame, ChartLegend, type Dash } from "./chart-frame";
import { linear, niceTicks, sampleIndices } from "./scale";

export type FlowSeries = {
  key: "foreign" | "trust" | "dealer";
  label: string;
  color: string;
  /** SVG dash pattern of the cumulative line, so identity is not colour alone. */
  dash?: string;
  legendDash?: Dash;
  pick: (day: MarketFlowDay) => number;
};

export const FLOW_SERIES: FlowSeries[] = [
  { key: "foreign", label: "外資", color: "var(--series-foreign)", pick: (d) => d.foreign_net_value },
  {
    key: "trust",
    label: "投信",
    color: "var(--series-trust)",
    dash: "6 4",
    legendDash: true,
    pick: (d) => d.trust_net_value,
  },
  {
    key: "dealer",
    label: "自營商",
    color: "var(--series-dealer)",
    dash: "1 4.5",
    legendDash: "dotted",
    pick: (d) => d.dealer_net_value,
  },
];

const MARGIN = { top: 26, right: 12, bottom: 26, left: 50 };
const PANEL_GAP = 44;
const CUM_HEIGHT = 150;
const dailyHeight = (width: number) => (width < 520 ? 180 : 230);

type Plot = { left: number; right: number; top: number; bottom: number };

const tickText = (value: number, step: number) => formatSigned(value, step >= 1 ? 0 : 1);

function pathOf(values: (number | null)[], x: (i: number) => number, y: (v: number) => number) {
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
}

/**
 * Daily net buy / sell of the three institutional investors in 億 NT$ (an
 * estimate), as grouped bars, with an optional second panel of running totals.
 * Sessions whose report is not out yet are hatched and break the lines; they are
 * never drawn as zero.
 */
export function FlowChart({
  days,
  cumulative,
  label,
}: {
  days: MarketFlowDay[];
  cumulative: boolean;
  label: string;
}) {
  const hatchId = `hatch-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const n = days.length;

  const model = useMemo(() => {
    const daily = FLOW_SERIES.map((s) => days.map((d) => (d.institutional_reported ? s.pick(d) / 1e8 : null)));
    const cum = daily.map((values) => {
      let sum = 0;
      return values.map((v) => (v === null ? null : (sum += v)));
    });
    const extent = (rows: (number | null)[][]) => {
      const all = rows.flat().filter((v): v is number => v !== null);
      return niceTicks(Math.min(0, ...all), Math.max(0, ...all), 4);
    };
    return { daily, cum, dailyTicks: extent(daily), cumTicks: extent(cum) };
  }, [days]);

  const { daily, cum, dailyTicks, cumTicks } = model;
  const step = Math.abs(dailyTicks[1] - dailyTicks[0]);
  const cumStep = Math.abs(cumTicks[1] - cumTicks[0]);

  const panels = (plot: Plot, width: number) => {
    const p1 = { top: plot.top, bottom: plot.top + dailyHeight(width) };
    const p2 = cumulative ? { top: p1.bottom + PANEL_GAP, bottom: plot.bottom } : null;
    return { p1, p2 };
  };
  const groupW = (plot: Plot) => (plot.right - plot.left) / n;
  const xAt = (i: number, plot: Plot) => plot.left + (i + 0.5) * groupW(plot);

  return (
    <ChartFrame
      count={n}
      label={label}
      margin={MARGIN}
      heightFor={(width) =>
        MARGIN.top + dailyHeight(width) + MARGIN.bottom + (cumulative ? PANEL_GAP + CUM_HEIGHT : 0)
      }
      xAt={xAt}
      defaultIndex={n - 1}
      readout={(i) => {
        const day = days[i];
        if (!day.institutional_reported) {
          return {
            title: day.trading_date,
            rows: [{ label: "三大法人", value: "尚未公布", color: "var(--series-neutral)" }],
            note: "公布後才會補上，不視為 0",
          };
        }
        const total = FLOW_SERIES.reduce((sum, s) => sum + s.pick(day), 0) / 1e8;
        return {
          title: day.trading_date,
          rows: [
            ...FLOW_SERIES.map((s, k) => ({
              label: s.label,
              value: `${formatSigned(daily[k][i] ?? 0, 1)} 億`,
              color: s.color,
            })),
            { label: "合計", value: `${formatSigned(total, 1)} 億`, color: "var(--series-neutral)" },
            ...(cumulative
              ? FLOW_SERIES.map((s, k) => ({
                  label: `累計${s.label}`,
                  value: `${formatSigned(cum[k][i] ?? 0, 1)} 億`,
                  color: s.color,
                  dashed: s.legendDash,
                }))
              : []),
          ],
          note: "金額為估算值",
        };
      }}
      markers={(i, ctx) => {
        const { plot, width } = ctx;
        const { p1, p2 } = panels(plot, width);
        const gw = groupW(plot);
        const x = xAt(i, plot);
        const yCum = p2 ? linear([cumTicks[0], cumTicks[cumTicks.length - 1]], [p2.bottom, p2.top]) : null;
        return (
          <>
            <rect
              className="chart-hover-band"
              x={x - gw / 2}
              width={gw}
              y={p1.top}
              height={plot.bottom - p1.top}
            />
            {yCum &&
              FLOW_SERIES.map((s, k) =>
                cum[k][i] === null ? null : (
                  <circle key={s.key} cx={x} cy={yCum(cum[k][i] as number)} r={4} fill={s.color} className="chart-dot" />
                ),
              )}
          </>
        );
      }}
    >
      {(ctx) => {
        const { plot, width } = ctx;
        const { p1, p2 } = panels(plot, width);
        const gw = groupW(plot);
        const barW = Math.max(1.5, (gw * 0.78) / FLOW_SERIES.length);
        const y1 = linear([dailyTicks[0], dailyTicks[dailyTicks.length - 1]], [p1.bottom, p1.top]);
        const y2 = p2 ? linear([cumTicks[0], cumTicks[cumTicks.length - 1]], [p2.bottom, p2.top]) : null;
        const labelIdx = sampleIndices(n, width < 520 ? 3 : 6);
        const axisBottom = p2 ? p2.bottom : p1.bottom;
        const gaps = days.map((d, i) => (d.institutional_reported ? null : i)).filter((i): i is number => i !== null);

        const grid = (ticks: number[], tickStep: number, y: (v: number) => number) =>
          ticks.map((tick) => (
            <g key={tick}>
              <line
                className={tick === 0 ? "chart-zero" : "chart-grid"}
                x1={plot.left}
                x2={plot.right}
                y1={y(tick)}
                y2={y(tick)}
              />
              <text x={plot.left - 8} y={y(tick) + 4} textAnchor="end">
                {tickText(tick, tickStep)}
              </text>
            </g>
          ));

        return (
          <>
            <defs>
              <pattern id={hatchId} patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
                <line className="chart-hatch-line" x1="0" y1="0" x2="0" y2="6" />
              </pattern>
            </defs>
            <text className="chart-panel-title" x={plot.left} y={plot.top - 10}>
              每日淨買賣超（億 NT$，估算）
            </text>
            {grid(dailyTicks, step, y1)}
            {gaps.map((i) => (
              <rect
                key={`gap1-${i}`}
                x={xAt(i, plot) - gw * 0.45}
                width={gw * 0.9}
                y={p1.top}
                height={p1.bottom - p1.top}
                fill={`url(#${hatchId})`}
              />
            ))}
            {days.map((day, i) =>
              day.institutional_reported
                ? FLOW_SERIES.map((s, k) => {
                    const v = daily[k][i] as number;
                    return (
                      <rect
                        key={`${day.trading_date}-${s.key}`}
                        x={xAt(i, plot) - (barW * FLOW_SERIES.length) / 2 + k * barW}
                        width={Math.max(1, barW - (barW > 3 ? 0.75 : 0))}
                        y={Math.min(y1(0), y1(v))}
                        height={Math.max(0.75, Math.abs(y1(v) - y1(0)))}
                        fill={s.color}
                      />
                    );
                  })
                : null,
            )}
            {y2 && p2 && (
              <>
                <text className="chart-panel-title" x={plot.left} y={p2.top - 12}>
                  累計淨買賣超（億 NT$，自區間第一日起）
                </text>
                {grid(cumTicks, cumStep, y2)}
                {gaps.map((i) => (
                  <rect
                    key={`gap2-${i}`}
                    x={xAt(i, plot) - gw * 0.45}
                    width={gw * 0.9}
                    y={p2.top}
                    height={p2.bottom - p2.top}
                    fill={`url(#${hatchId})`}
                  />
                ))}
                {FLOW_SERIES.map((s, k) => (
                  <path
                    key={s.key}
                    className="chart-line"
                    d={pathOf(cum[k], (i) => xAt(i, plot), y2)}
                    stroke={s.color}
                    strokeDasharray={s.dash}
                  />
                ))}
              </>
            )}
            {labelIdx.map((i, k) => (
              <text
                key={i}
                x={xAt(i, plot)}
                y={axisBottom + 18}
                textAnchor={k === 0 ? "start" : k === labelIdx.length - 1 ? "end" : "middle"}
              >
                {shortDate(days[i].trading_date)}
              </text>
            ))}
          </>
        );
      }}
    </ChartFrame>
  );
}

export function FlowLegend({ cumulative, hasGap }: { cumulative: boolean; hasGap: boolean }) {
  return (
    <ChartLegend
      items={[
        ...FLOW_SERIES.map((s) =>
          cumulative
            ? { label: s.label, color: s.color, dashed: s.legendDash }
            : { label: s.label, color: s.color, band: true },
        ),
        ...(hasGap ? [{ label: "資料尚未公布", color: "var(--chart-axis)", hatch: true }] : []),
      ]}
    />
  );
}
