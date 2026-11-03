"use client";

import { useMemo } from "react";
import { ChevronDownIcon } from "@/components/icons";
import type { EquityPoint } from "@/lib/api/types";
import { formatNumber, formatPrice, shortDate } from "@/lib/format";
import { ChartFrame, ChartLegend } from "./chart-frame";
import { compactTwd, linear, niceTicks, sampleIndices } from "./scale";

const STRATEGY = "var(--series-strategy)";
const BASELINE = "var(--series-baseline)";

/** Contiguous index ranges where the strategy holds a position. */
function holdingSpans(points: EquityPoint[]): [number, number][] {
  const spans: [number, number][] = [];
  let start: number | null = null;
  points.forEach((point, i) => {
    if (point.position > 0 && start === null) start = i;
    if (point.position <= 0 && start !== null) {
      spans.push([start, i - 1]);
      start = null;
    }
  });
  if (start !== null) spans.push([start, points.length - 1]);
  return spans;
}

export function EquityChart({
  strategy,
  buyHold,
}: {
  strategy: EquityPoint[];
  buyHold: EquityPoint[];
}) {
  const model = useMemo(() => {
    const baselineByDate = new Map(buyHold.map((p) => [p.trading_date, p.equity]));
    const values = [...strategy.map((p) => p.equity), ...buyHold.map((p) => p.equity)];
    const ticks = niceTicks(Math.min(...values), Math.max(...values), 4);
    return { baselineByDate, ticks, spans: holdingSpans(strategy) };
  }, [strategy, buyHold]);

  if (!strategy.length) return null;
  const { ticks, spans, baselineByDate } = model;
  const n = strategy.length;
  const first = strategy[0];
  const last = strategy[n - 1];
  const hasBaseline = buyHold.length > 0;

  return (
    <figure className="chart-figure">
      <ChartFrame
        count={n}
        label={`策略淨值與同標的買入持有走勢，${first.trading_date} 至 ${last.trading_date}`}
        margin={{ top: 14, right: 18, bottom: 28, left: 58 }}
        xAt={(i, plot) => plot.left + (n === 1 ? 0 : (i / (n - 1)) * (plot.right - plot.left))}
        defaultIndex={n - 1}
        readout={(i) => {
          const point = strategy[i];
          const base = baselineByDate.get(point.trading_date);
          return {
            title: point.trading_date,
            rows: [
              { label: "策略淨值", value: formatNumber(point.equity, 0), color: STRATEGY },
              ...(base !== undefined
                ? [{ label: "買入持有", value: formatNumber(base, 0), color: BASELINE, dashed: true }]
                : []),
            ],
            note: point.position > 0 ? "策略持有中" : "策略空手",
          };
        }}
        markers={(i, ctx) => {
          const y = linear([ticks[0], ticks[ticks.length - 1]], [ctx.plot.bottom, ctx.plot.top]);
          const x = ctx.plot.left + (n === 1 ? 0 : (i / (n - 1)) * (ctx.plot.right - ctx.plot.left));
          const base = baselineByDate.get(strategy[i].trading_date);
          return (
            <>
              {base !== undefined && (
                <circle cx={x} cy={y(base)} r={4} fill={BASELINE} className="chart-dot" />
              )}
              <circle cx={x} cy={y(strategy[i].equity)} r={4.5} fill={STRATEGY} className="chart-dot" />
            </>
          );
        }}
      >
        {(ctx) => {
          const { plot } = ctx;
          const x = (i: number) => plot.left + (n === 1 ? 0 : (i / (n - 1)) * (plot.right - plot.left));
          const y = linear([ticks[0], ticks[ticks.length - 1]], [plot.bottom, plot.top]);
          const line = (points: { equity: number }[], xs: (i: number) => number) =>
            points.map((p, i) => `${i === 0 ? "M" : "L"}${xs(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join("");
          const bx = (i: number) =>
            plot.left + (buyHold.length <= 1 ? 0 : (i / (buyHold.length - 1)) * (plot.right - plot.left));
          const labelIdx = sampleIndices(n, ctx.width < 520 ? 3 : 5);
          return (
            <>
              {spans.map(([a, b]) => (
                <rect
                  key={a}
                  className="chart-hold"
                  x={x(a)}
                  y={plot.top}
                  width={Math.max(2, x(b) - x(a))}
                  height={plot.bottom - plot.top}
                />
              ))}
              {ticks.map((tick) => (
                <g key={tick}>
                  <line className="chart-grid" x1={plot.left} x2={plot.right} y1={y(tick)} y2={y(tick)} />
                  <text x={plot.left - 8} y={y(tick) + 4} textAnchor="end">
                    {compactTwd(tick)}
                  </text>
                </g>
              ))}
              {hasBaseline && (
                <path d={line(buyHold, bx)} className="chart-line" stroke={BASELINE} strokeDasharray="5 4" />
              )}
              <path d={line(strategy, x)} className="chart-line" stroke={STRATEGY} />
              {labelIdx.map((i, k) => (
                <text
                  key={i}
                  x={x(i)}
                  y={plot.bottom + 18}
                  textAnchor={k === 0 ? "start" : k === labelIdx.length - 1 ? "end" : "middle"}
                >
                  {i === 0 || i === n - 1 ? strategy[i].trading_date : shortDate(strategy[i].trading_date)}
                </text>
              ))}
              <circle cx={x(n - 1)} cy={y(last.equity)} r={4} fill={STRATEGY} className="chart-dot" />
            </>
          );
        }}
      </ChartFrame>
      <ChartLegend
        items={[
          { label: "策略淨值", color: STRATEGY },
          ...(hasBaseline ? [{ label: "同標的買入持有", color: BASELINE, dashed: true }] : []),
          ...(spans.length ? [{ label: "策略持有期間", color: "var(--chart-hold)", band: true }] : []),
        ]}
      />
      <details className="disclosure chart-table">
        <summary>
          <span>以表格檢視淨值（取樣）</span>
          <ChevronDownIcon size={16} />
        </summary>
        <div className="disclosure-body">
          <div className="table-wrap" tabIndex={0} role="region" aria-label="淨值取樣表">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">日期</th>
                  <th scope="col">策略淨值</th>
                  {hasBaseline && <th scope="col">買入持有</th>}
                </tr>
              </thead>
              <tbody>
                {sampleIndices(n, 16).map((i) => (
                  <tr key={strategy[i].trading_date}>
                    <td>{strategy[i].trading_date}</td>
                    <td>{formatPrice(strategy[i].equity)}</td>
                    {hasBaseline && (
                      <td>
                        {baselineByDate.has(strategy[i].trading_date)
                          ? formatPrice(baselineByDate.get(strategy[i].trading_date)!)
                          : "—"}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </details>
    </figure>
  );
}
