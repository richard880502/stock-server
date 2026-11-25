"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  LineSeries,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { subscribeTheme } from "@/lib/theme";
import type { Candle } from "@/lib/price-feed";

export type TradeMarker = { time: string; side: "buy" | "sell"; label?: string };

export const MA_PERIODS = [5, 20, 60] as const;
export type MaPeriod = (typeof MA_PERIODS)[number];

/** Visible window presets, in trading days ("all" = everything loaded). */
export const RANGE_PRESETS = [
  { key: "3m", label: "3月", bars: 63 },
  { key: "6m", label: "6月", bars: 126 },
  { key: "1y", label: "1年", bars: 252 },
  { key: "all", label: "全部", bars: Infinity },
] as const;
export type RangeKey = (typeof RANGE_PRESETS)[number]["key"];

type Palette = {
  text: string;
  muted: string;
  grid: string;
  border: string;
  up: string;
  down: string;
  ma: Record<MaPeriod, string>;
};

/** Resolve design tokens (which use light-dark()) to concrete colours for the canvas. */
function readPalette(element: HTMLElement): Palette {
  const probe = document.createElement("span");
  probe.style.display = "none";
  element.appendChild(probe);
  const resolve = (token: string) => {
    probe.style.color = `var(${token})`;
    return getComputedStyle(probe).color;
  };
  const palette: Palette = {
    text: resolve("--text-2"),
    muted: resolve("--text-3"),
    grid: resolve("--border"),
    border: resolve("--border-strong"),
    up: resolve("--up"),
    down: resolve("--down"),
    ma: { 5: resolve("--series-ma5"), 20: resolve("--series-ma20"), 60: resolve("--series-ma60") },
  };
  probe.remove();
  return palette;
}

function withAlpha(color: string, alpha: number): string {
  const match = color.match(/rgba?\(([^)]+)\)/);
  if (!match) return color;
  const [r, g, b] = match[1].split(/[ ,/]+/).filter(Boolean);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function movingAverage(candles: Candle[], period: number) {
  const out: { time: Time; value: number }[] = [];
  let sum = 0;
  candles.forEach((candle, index) => {
    sum += candle.close;
    if (index >= period) sum -= candles[index - period].close;
    if (index >= period - 1) out.push({ time: candle.time as Time, value: sum / period });
  });
  return out;
}

function mergeLive(candles: Candle[], live: Candle | null): Candle[] {
  if (!live || candles.length === 0) return candles;
  const last = candles[candles.length - 1];
  if (live.time === last.time) return [...candles.slice(0, -1), live];
  if (live.time > last.time) return [...candles, live];
  return candles;
}

const formatPrice = (value: number) =>
  value.toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const formatVolume = (shares: number) => `${Math.round(shares / 1000).toLocaleString("zh-TW")} 張`;

type Legend = { candle: Candle; change: number | null; ma: Partial<Record<MaPeriod, number>> };

/**
 * Daily candlestick chart (lightweight-charts) with moving averages, a volume pane and
 * optional buy / sell markers. Red = up, green = down (Taiwan convention). `live`
 * replaces or appends the latest candle so a streaming feed can drive it later.
 */
export function CandleChart({
  candles,
  live = null,
  markers = [],
  maPeriods = MA_PERIODS,
  range = "6m",
  height = 420,
  label,
}: {
  candles: Candle[];
  live?: Candle | null;
  markers?: TradeMarker[];
  maPeriods?: readonly MaPeriod[];
  range?: RangeKey;
  height?: number;
  /** Accessible name, e.g. "2330.TW 日 K 線". */
  label: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<{
    candle: ISeriesApi<"Candlestick">;
    volume: ISeriesApi<"Histogram">;
    ma: Partial<Record<MaPeriod, ISeriesApi<"Line">>>;
    markers: ISeriesMarkersPluginApi<Time>;
  } | null>(null);
  const paletteRef = useRef<Palette | null>(null);
  const [legend, setLegend] = useState<Legend | null>(null);
  const [themeVersion, setThemeVersion] = useState(0);

  const data = useMemo(() => mergeLive(candles, live), [candles, live]);
  const maData = useMemo(
    () => Object.fromEntries(MA_PERIODS.map((p) => [p, movingAverage(data, p)])) as Record<MaPeriod, { time: Time; value: number }[]>,
    [data],
  );

  // Create the chart once.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const palette = readPalette(host);
    paletteRef.current = palette;
    const chart = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: palette.text,
        fontFamily: getComputedStyle(host).fontFamily,
        fontSize: 12,
        attributionLogo: false,
        panes: { separatorColor: palette.grid, separatorHoverColor: palette.border },
      },
      grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: palette.grid },
      timeScale: { borderColor: palette.grid, rightOffset: 4, minBarSpacing: 2 },
      localization: { locale: "zh-TW", dateFormat: "yyyy-MM-dd", priceFormatter: formatPrice },
    });
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: palette.up,
      downColor: palette.down,
      borderUpColor: palette.up,
      borderDownColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
      priceLineVisible: true,
    });
    const ma: Partial<Record<MaPeriod, ISeriesApi<"Line">>> = {};
    for (const period of MA_PERIODS) {
      ma[period] = chart.addSeries(LineSeries, {
        color: palette.ma[period],
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
    }
    const volume = chart.addSeries(
      HistogramSeries,
      { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false },
      1,
    );
    chart.panes()[1]?.setStretchFactor(0.25);
    seriesRef.current = { candle, volume, ma, markers: createSeriesMarkers(candle, []) };
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Follow theme changes (pinned toggle or the system scheme).
  useEffect(() => {
    const bump = () => setThemeVersion((v) => v + 1);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", bump);
    const unsubscribe = subscribeTheme(bump);
    return () => {
      media.removeEventListener("change", bump);
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    const host = hostRef.current;
    if (!chart || !series || !host || themeVersion === 0) return;
    const palette = readPalette(host);
    paletteRef.current = palette;
    chart.applyOptions({
      layout: { textColor: palette.text, panes: { separatorColor: palette.grid } },
      grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
      rightPriceScale: { borderColor: palette.grid },
      timeScale: { borderColor: palette.grid },
    });
    series.candle.applyOptions({
      upColor: palette.up,
      downColor: palette.down,
      borderUpColor: palette.up,
      borderDownColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
    });
    for (const period of MA_PERIODS) series.ma[period]?.applyOptions({ color: palette.ma[period] });
  }, [themeVersion]);

  // Data, volume colours, moving averages and markers.
  useEffect(() => {
    const series = seriesRef.current;
    const palette = paletteRef.current;
    if (!series || !palette) return;
    series.candle.setData(data.map(({ time, open, high, low, close }) => ({ time: time as Time, open, high, low, close })));
    series.volume.setData(
      data.map((c) => ({
        time: c.time as Time,
        value: c.volume,
        color: withAlpha(c.close >= c.open ? palette.up : palette.down, 0.45),
      })),
    );
    for (const period of MA_PERIODS) {
      series.ma[period]?.setData(maPeriods.includes(period) ? maData[period] : []);
    }
    const known = new Set(data.map((c) => c.time));
    const seriesMarkers: SeriesMarker<Time>[] = markers
      .filter((m) => known.has(m.time))
      .sort((a, b) => a.time.localeCompare(b.time))
      .map((m) =>
        m.side === "buy"
          ? { time: m.time as Time, position: "belowBar", shape: "arrowUp", color: palette.up, text: m.label ?? "買" }
          : { time: m.time as Time, position: "aboveBar", shape: "arrowDown", color: palette.down, text: m.label ?? "賣" },
      );
    series.markers.setMarkers(seriesMarkers);
  }, [data, maData, maPeriods, markers, themeVersion]);

  // Visible window: re-apply only when the preset or the loaded series changes,
  // not on every live tick (that would fight the user's own panning).
  const seriesKey = `${candles[0]?.time ?? ""}|${candles.length}`;
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || candles.length === 0) return;
    const preset = RANGE_PRESETS.find((p) => p.key === range) ?? RANGE_PRESETS[1];
    const to = candles.length - 1 + 4;
    const from = Number.isFinite(preset.bars) ? Math.max(0, candles.length - preset.bars) : 0;
    const apply = () => chart.timeScale().setVisibleLogicalRange({ from, to });
    apply();
    // autoSize measures the container asynchronously; the first measurement re-fits the
    // whole series, so apply the window again once the chart has its real width.
    const host = hostRef.current;
    if (!host) return;
    let applied = false;
    const observer = new ResizeObserver(() => {
      if (applied || host.clientWidth === 0) return;
      applied = true;
      requestAnimationFrame(apply);
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, [range, seriesKey, candles.length]);

  // Legend follows the crosshair, falling back to the latest candle.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const byTime = new Map(data.map((c, i) => [c.time, i]));
    const legendAt = (index: number): Legend | null => {
      const candle = data[index];
      if (!candle) return null;
      const previous = data[index - 1];
      const ma: Partial<Record<MaPeriod, number>> = {};
      for (const period of maPeriods) {
        const point = maData[period].find((p) => p.time === candle.time);
        if (point) ma[period] = point.value;
      }
      return { candle, change: previous ? candle.close / previous.close - 1 : null, ma };
    };
    setLegend(legendAt(data.length - 1));
    const onMove = (param: MouseEventParams<Time>) => {
      const index = param.time !== undefined ? byTime.get(String(param.time)) : undefined;
      setLegend(legendAt(index ?? data.length - 1));
    };
    chart.subscribeCrosshairMove(onMove);
    return () => chart.unsubscribeCrosshairMove(onMove);
  }, [data, maData, maPeriods]);

  const last = data[data.length - 1];
  const first = data[0];

  return (
    <div className="candle-chart">
      {legend && (
        <div className="candle-legend num" aria-hidden="true">
          <span className="candle-legend-date">{legend.candle.time}</span>
          <span>開 {formatPrice(legend.candle.open)}</span>
          <span>高 {formatPrice(legend.candle.high)}</span>
          <span>低 {formatPrice(legend.candle.low)}</span>
          <span>收 {formatPrice(legend.candle.close)}</span>
          {legend.change !== null && (
            <span className={legend.change >= 0 ? "tone-up" : "tone-down"}>
              {legend.change >= 0 ? "+" : "−"}
              {Math.abs(legend.change * 100).toFixed(2)}%
            </span>
          )}
          <span className="candle-legend-volume">量 {formatVolume(legend.candle.volume)}</span>
          {maPeriods.map((period) =>
            legend.ma[period] !== undefined ? (
              <span key={period} className={`candle-legend-ma ma-${period}`}>
                MA{period} {formatPrice(legend.ma[period]!)}
              </span>
            ) : null,
          )}
        </div>
      )}
      <div ref={hostRef} className="candle-host" style={{ height }} role="img" aria-label={label} />
      {last && first && (
        <p className="sr-only">
          {label}：{first.time} 至 {last.time} 共 {data.length} 根日 K，最新收盤 {formatPrice(last.close)}。
          {markers.length > 0 && `圖上標記 ${markers.length} 筆買賣點。`}
        </p>
      )}
    </div>
  );
}
