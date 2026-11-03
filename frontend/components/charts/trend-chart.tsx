"use client";

import { useId, useMemo } from "react";
import { shortDate } from "@/lib/format";
import { ChartFrame } from "./chart-frame";
import { linear, niceTicks, sampleIndices } from "./scale";

const MARGIN = { top: 12, right: 10, bottom: 24, left: 46 };
const HEIGHT = 150;
const compactHeight = () => HEIGHT;

type Plot = { left: number; right: number; top: number; bottom: number };

export type TrendPoint = {
  date: string;
  /** null = the source has not published this session yet (drawn as a gap, never as 0). */
  value: number | null;
};

function xLabels(dates: string[], xAt: (i: number) => number, width: number, bottom: number) {
  const idx = sampleIndices(dates.length, width < 340 ? 2 : 3);
  return idx.map((i, k) => (
    <text
      key={i}
      x={xAt(i)}
      y={bottom + 17}
      textAnchor={k === 0 ? "start" : k === idx.length - 1 ? "end" : "middle"}
    >
      {shortDate(dates[i])}
    </text>
  ));
}

/** A single series as bars (zero-based) or a line (auto range) with gaps for unpublished sessions. */
export function TrendChart({
  points,
  variant,
  label,
  seriesLabel,
  color,
  format,
  axis,
  missingNote,
}: {
  points: TrendPoint[];
  variant: "bars" | "line";
  label: string;
  seriesLabel: string;
  color: string;
  /** Tooltip value text, unit included. */
  format: (value: number) => string;
  /** Axis tick text. */
  axis: (value: number) => string;
  missingNote: string;
}) {
  const hatchId = `hatch-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const n = points.length;
  const ticks = useMemo(() => {
    const values = points.map((p) => p.value).filter((v): v is number => v !== null);
    if (values.length === 0) return [0, 1];
    const min = variant === "bars" ? Math.min(0, ...values) : Math.min(...values);
    const max = variant === "bars" ? Math.max(0, ...values) : Math.max(...values);
    return niceTicks(min, max, 3);
  }, [points, variant]);

  const gw = (plot: Plot) => (plot.right - plot.left) / n;
  const xBars = (i: number, plot: Plot) => plot.left + (i + 0.5) * gw(plot);
  const xLine = (i: number, plot: Plot) =>
    plot.left + (n === 1 ? (plot.right - plot.left) / 2 : (i / (n - 1)) * (plot.right - plot.left));
  const xAt = variant === "bars" ? xBars : xLine;

  return (
    <ChartFrame
      className="chart-compact"
      count={n}
      label={label}
      margin={MARGIN}
      heightFor={compactHeight}
      xAt={xAt}
      defaultIndex={n - 1}
      readout={(i) => {
        const p = points[i];
        return {
          title: p.date,
          rows: [
            {
              label: seriesLabel,
              value: p.value === null ? "尚未公布" : format(p.value),
              color,
            },
          ],
          note: p.value === null ? missingNote : undefined,
        };
      }}
      markers={(i, ctx) => {
        const v = points[i].value;
        if (v === null) return null;
        const y = linear([ticks[0], ticks[ticks.length - 1]], [ctx.plot.bottom, ctx.plot.top]);
        return <circle cx={xAt(i, ctx.plot)} cy={y(v)} r={4} fill={color} className="chart-dot" />;
      }}
    >
      {(ctx) => {
        const { plot, width } = ctx;
        const y = linear([ticks[0], ticks[ticks.length - 1]], [plot.bottom, plot.top]);
        const w = gw(plot);
        const barW = Math.max(1.5, w * 0.72);
        let d = "";
        let pen = false;
        if (variant === "line") {
          points.forEach((p, i) => {
            if (p.value === null) {
              pen = false;
              return;
            }
            d += `${pen ? "L" : "M"}${xLine(i, plot).toFixed(1)},${y(p.value).toFixed(1)}`;
            pen = true;
          });
        }
        return (
          <>
            <defs>
              <pattern id={hatchId} patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
                <line className="chart-hatch-line" x1="0" y1="0" x2="0" y2="6" />
              </pattern>
            </defs>
            {ticks.map((tick) => (
              <g key={tick}>
                <line
                  className={tick === 0 && variant === "bars" ? "chart-zero" : "chart-grid"}
                  x1={plot.left}
                  x2={plot.right}
                  y1={y(tick)}
                  y2={y(tick)}
                />
                <text x={plot.left - 8} y={y(tick) + 4} textAnchor="end">
                  {axis(tick)}
                </text>
              </g>
            ))}
            {points.map((p, i) =>
              p.value === null ? (
                <rect
                  key={p.date}
                  x={xAt(i, plot) - Math.max(w, 3) * 0.45}
                  width={Math.max(w, 3) * 0.9}
                  y={plot.top}
                  height={plot.bottom - plot.top}
                  fill={`url(#${hatchId})`}
                />
              ) : variant === "bars" ? (
                <rect
                  key={p.date}
                  x={xBars(i, plot) - barW / 2}
                  width={barW}
                  y={Math.min(y(0), y(p.value))}
                  height={Math.max(0.75, Math.abs(y(p.value) - y(0)))}
                  fill={color}
                />
              ) : null,
            )}
            {variant === "line" && <path className="chart-line" d={d} stroke={color} />}
            {xLabels(
              points.map((p) => p.date),
              (i) => xAt(i, plot),
              width,
              plot.bottom,
            )}
          </>
        );
      }}
    </ChartFrame>
  );
}

export type BreadthPoint = { date: string; advancing: number; declining: number; unchanged: number };

/** Advancing issues above the axis, declining below: the only red / green here is up / down. */
export function BreadthChart({ points, label }: { points: BreadthPoint[]; label: string }) {
  const n = points.length;
  const ticks = useMemo(() => {
    const maxUp = Math.max(0, ...points.map((p) => p.advancing));
    const maxDown = Math.max(0, ...points.map((p) => p.declining));
    return niceTicks(-maxDown, maxUp, 4);
  }, [points]);
  const gw = (plot: Plot) => (plot.right - plot.left) / n;
  const xAt = (i: number, plot: Plot) => plot.left + (i + 0.5) * gw(plot);

  return (
    <ChartFrame
      className="chart-compact"
      count={n}
      label={label}
      margin={MARGIN}
      heightFor={compactHeight}
      xAt={xAt}
      defaultIndex={n - 1}
      readout={(i) => {
        const p = points[i];
        return {
          title: p.date,
          rows: [
            { label: "上漲家數", value: `${p.advancing.toLocaleString("zh-TW")} 檔`, color: "var(--up)" },
            { label: "下跌家數", value: `${p.declining.toLocaleString("zh-TW")} 檔`, color: "var(--down)" },
            { label: "平盤", value: `${p.unchanged.toLocaleString("zh-TW")} 檔`, color: "var(--series-neutral)" },
          ],
        };
      }}
    >
      {(ctx) => {
        const { plot, width } = ctx;
        const y = linear([ticks[0], ticks[ticks.length - 1]], [plot.bottom, plot.top]);
        const barW = Math.max(1.5, gw(plot) * 0.72);
        return (
          <>
            {ticks.map((tick) => (
              <g key={tick}>
                <line className={tick === 0 ? "chart-zero" : "chart-grid"} x1={plot.left} x2={plot.right} y1={y(tick)} y2={y(tick)} />
                <text x={plot.left - 8} y={y(tick) + 4} textAnchor="end">
                  {Math.abs(tick).toLocaleString("zh-TW")}
                </text>
              </g>
            ))}
            {points.map((p, i) => (
              <g key={p.date}>
                <rect
                  x={xAt(i, plot) - barW / 2}
                  width={barW}
                  y={y(p.advancing)}
                  height={Math.max(0.75, y(0) - y(p.advancing))}
                  fill="var(--up)"
                />
                <rect
                  x={xAt(i, plot) - barW / 2}
                  width={barW}
                  y={y(0)}
                  height={Math.max(0.75, y(-p.declining) - y(0))}
                  fill="var(--down)"
                />
              </g>
            ))}
            {xLabels(
              points.map((p) => p.date),
              (i) => xAt(i, plot),
              width,
              plot.bottom,
            )}
          </>
        );
      }}
    </ChartFrame>
  );
}
