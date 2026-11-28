"use client";

import { ChartFrame, ChartLegend, type Dash } from "@/components/charts/chart-frame";
import { linear, sampleIndices } from "@/components/charts/scale";
import { barRect, dualAxisTicks, extentOf, linePath, singleAxisTicks, slotWidths, summarizeSeries } from "./geometry";

export type ChartSeries = {
  key: string;
  label: string;
  kind: "line" | "bar";
  /** Bars and lines can sit on a secondary (right) axis; zero is aligned across both. */
  axis?: "left" | "right";
  values: (number | null)[];
  color: string;
  format: (value: number) => string;
  /** SVG dash pattern of a line, so identity is not colour alone. */
  dash?: string;
  legendDash?: Dash;
  /** Bars take the up / down colour of their sign (Taiwan: red positive, green negative). */
  toneBars?: boolean;
};

export type ProfileChartProps = {
  /** Accessible name; the latest value and range of the first series are appended. */
  title: string;
  /** ISO date or period per category, used in tooltips and the screen-reader summary. */
  dates: string[];
  /** Axis text per category (default: the date without its year). */
  xLabels?: string[];
  series: ChartSeries[];
  /** Force a zero baseline (default: yes when any series is bars). */
  includeZero?: boolean;
  tickFormat?: (value: number) => string;
  rightTickFormat?: (value: number) => string;
  axisTitle?: string;
  rightAxisTitle?: string;
  height?: number;
  /** Mark the newest value of the first line series with a dot. */
  markLast?: boolean;
  /** Extra tooltip rows for a category (e.g. holder counts). */
  extraRows?: (index: number) => { label: string; value: string }[];
  /** Tooltip footnote. */
  note?: string;
  hideLegend?: boolean;
};

const defaultTick = (value: number) =>
  Math.abs(value) >= 1000 ? value.toLocaleString("zh-TW", { maximumFractionDigits: 0 }) : String(Number(value.toFixed(2)));

const shortLabel = (text: string) => (/^\d{4}-\d{2}-\d{2}$/.test(text) ? text.slice(5).replace("-", "/") : text);

/**
 * One small chart for every profile tab: any mix of bars and lines on a left and an optional
 * right axis (zero aligned), built on the shared ChartFrame for hover, touch and arrow-key
 * reading. Missing values break lines and leave a gap in bars; they are never drawn as zero.
 */
export function ProfileChart({
  title,
  dates,
  xLabels,
  series,
  includeZero,
  tickFormat = defaultTick,
  rightTickFormat,
  axisTitle,
  rightAxisTitle,
  height = 190,
  markLast = false,
  extraRows,
  note,
  hideLegend,
}: ProfileChartProps) {
  const n = dates.length;
  const hasBars = series.some((s) => s.kind === "bar");
  const zero = includeZero ?? hasBars;
  const dual = series.some((s) => s.axis === "right");
  const leftSeries = series.filter((s) => s.axis !== "right");
  const rightSeries = series.filter((s) => s.axis === "right");

  const ticks = dual
    ? dualAxisTicks(extentOf(...leftSeries.map((s) => s.values)), extentOf(...rightSeries.map((s) => s.values)))
    : { left: singleAxisTicks(extentOf(...series.map((s) => s.values)), zero), right: [] as number[] };
  const lastTick = ticks.left.length - 1;
  const margin = { top: 14, right: dual ? 50 : 14, bottom: 26, left: 52 };
  const rightFormat = rightTickFormat ?? tickFormat;

  const first = series[0];
  const label = first ? summarizeSeries(`${title}（${first.label}）`, dates, first.values, first.format) : title;
  const xAt = (i: number, plot: { left: number; right: number }) =>
    plot.left + ((i + 0.5) * (plot.right - plot.left)) / Math.max(n, 1);
  const lineSeries = series.filter((s) => s.kind === "line");
  const markSeries = markLast ? lineSeries[0] : undefined;

  return (
    <figure className="pf-chart">
      {!hideLegend && series.length > 1 && (
        <ChartLegend
          items={series.map((s) => ({ label: s.label, color: s.color, dashed: s.legendDash, band: s.kind === "bar" }))}
        />
      )}
      <ChartFrame
        count={n}
        label={label}
        margin={margin}
        heightFor={() => height}
        xAt={xAt}
        defaultIndex={n - 1}
        className="chart-compact"
        readout={(i) => ({
          title: dates[i],
          rows: [
            ...series.map((s) => {
              const v = s.values[i];
              return {
                label: s.label,
                value: v === null || v === undefined ? "—" : s.format(v),
                color: s.toneBars && v ? (v > 0 ? "var(--up)" : "var(--down)") : s.color,
                dashed: s.legendDash,
              };
            }),
            ...(extraRows?.(i).map((row) => ({ ...row, color: "var(--text-3)" })) ?? []),
          ],
          note,
        })}
        markers={(i, ctx) =>
          series
            .filter((s) => s.kind === "line")
            .map((s) => {
              const v = s.values[i];
              if (v === null || v === undefined) return null;
              const y = s.axis === "right" ? yRight(ctx.plot) : yLeft(ctx.plot);
              return <circle key={s.key} cx={xAt(i, ctx.plot)} cy={y(v)} r={4} fill={s.color} className="chart-dot" />;
            })
        }
      >
        {({ plot, width }) => {
          const y = yLeft(plot);
          const yR = yRight(plot);
          const { slot, bar } = slotWidths(plot.right - plot.left, n);
          const labelIdx = sampleIndices(n, width < 520 ? 3 : 5);
          const barSeries = series.filter((s) => s.kind === "bar");
          const barW = barSeries.length > 1 ? Math.max(1.5, (bar * 1.1) / barSeries.length) : bar;
          return (
            <>
              {ticks.left.map((tick, k) => (
                <g key={`l${k}`}>
                  <line
                    className={tick === 0 ? "chart-zero" : "chart-grid"}
                    x1={plot.left}
                    x2={plot.right}
                    y1={y(tick)}
                    y2={y(tick)}
                  />
                  <text x={plot.left - 8} y={y(tick) + 4} textAnchor="end">
                    {tickFormat(tick)}
                  </text>
                  {dual && (
                    <text x={plot.right + 8} y={yR(ticks.right[k]) + 4} textAnchor="start">
                      {rightFormat(ticks.right[k])}
                    </text>
                  )}
                </g>
              ))}
              {axisTitle && (
                <text className="chart-panel-title" x={plot.left - margin.left + 2} y={plot.top - 4} textAnchor="start">
                  {axisTitle}
                </text>
              )}
              {dual && rightAxisTitle && (
                <text className="chart-panel-title" x={plot.right + margin.right - 2} y={plot.top - 4} textAnchor="end">
                  {rightAxisTitle}
                </text>
              )}
              {barSeries.map((s, k) =>
                s.values.map((v, i) => {
                  if (v === null || !Number.isFinite(v)) return null;
                  const yy = s.axis === "right" ? yR : y;
                  const center = plot.left + (i + 0.5) * slot + (k - (barSeries.length - 1) / 2) * barW;
                  const rect = barRect(center, Math.max(1, barW - (barW > 4 ? 1 : 0)), yy(0), yy(v));
                  const fill = s.toneBars ? (v > 0 ? "var(--up)" : v < 0 ? "var(--down)" : "var(--flat)") : s.color;
                  return <rect key={`${s.key}-${dates[i]}`} {...rect} fill={fill} opacity={s.axis === "right" ? 0.55 : 1} />;
                }),
              )}
              {lineSeries.map((s) => {
                const yy = s.axis === "right" ? yR : y;
                return (
                  <g key={s.key}>
                    <path
                      className="chart-line"
                      d={linePath(s.values, (i) => xAt(i, plot), yy)}
                      stroke={s.color}
                      strokeDasharray={s.dash}
                    />
                    {s.values.map((v, i) =>
                      v !== null && s.values[i - 1] == null && s.values[i + 1] == null ? (
                        <circle key={dates[i]} cx={xAt(i, plot)} cy={yy(v)} r={3.5} fill={s.color} className="chart-dot" />
                      ) : null,
                    )}
                  </g>
                );
              })}
              {markSeries &&
                (() => {
                  let at = -1;
                  markSeries.values.forEach((v, i) => {
                    if (v !== null) at = i;
                  });
                  if (at < 0) return null;
                  const yy = markSeries.axis === "right" ? yR : y;
                  return (
                    <g>
                      <line
                        className="pf-current-line"
                        x1={plot.left}
                        x2={plot.right}
                        y1={yy(markSeries.values[at] as number)}
                        y2={yy(markSeries.values[at] as number)}
                      />
                      <circle
                        cx={xAt(at, plot)}
                        cy={yy(markSeries.values[at] as number)}
                        r={4.5}
                        fill={markSeries.color}
                        className="chart-dot"
                      />
                    </g>
                  );
                })()}
              {labelIdx.map((i) => (
                <text key={`x${i}`} x={xAt(i, plot)} y={plot.bottom + 17} textAnchor="middle">
                  {xLabels?.[i] ?? shortLabel(dates[i])}
                </text>
              ))}
            </>
          );
        }}
      </ChartFrame>
    </figure>
  );

  function yLeft(plot: { top: number; bottom: number }) {
    return linear([ticks.left[0], ticks.left[lastTick]], [plot.bottom, plot.top]);
  }
  function yRight(plot: { top: number; bottom: number }) {
    const rt = dual ? ticks.right : ticks.left;
    return linear([rt[0], rt[rt.length - 1]], [plot.bottom, plot.top]);
  }
}
