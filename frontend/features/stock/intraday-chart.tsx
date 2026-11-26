"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type UTCTimestamp,
  type Time,
} from "lightweight-charts";
import { withAuthHeaders } from "@/lib/api/auth";
import { apiUrl, errorFromResponse } from "@/lib/api/client";
import type { LiveQuote } from "@/lib/live-quotes";
import { aggregateBars, mergeBars, type Bar } from "./intraday-data";
import "./intraday-chart.css";

type HistoryBar = {
  minute: string;
  open: number;
  high: number;
  low: number;
  close: number;
  observed_volume_change_lots: number;
  is_partial: boolean;
  last_trade_time: string;
};
type Point = { time: number; price: number };
type Drawing =
  | { kind: "horizontal"; price: number }
  | { kind: "trend"; start: Point; end: Point };
type Tool = "cursor" | "horizontal" | "trend";
const taipeiTime = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
function formatTime(time: number) {
  return taipeiTime.format(new Date(time * 1000));
}
const priceFormat = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 });
function formatPrice(price: number) {
  return priceFormat.format(price);
}
function token(host: HTMLElement, name: string) {
  const probe = document.createElement("span");
  probe.style.color = `var(${name})`;
  host.appendChild(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}
export function IntradayChart(props: {
  symbol: string;
  quote?: LiveQuote;
  status: string;
}) {
  return <IntradayChartBody key={props.symbol} {...props} />;
}
function IntradayChartBody({
  symbol,
  quote,
  status,
}: {
  symbol: string;
  quote?: LiveQuote;
  status: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candle = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const drawingSeries = useRef<ISeriesApi<"Line">[]>([]);
  const priceLines = useRef<IPriceLine[]>([]);
  const toolRef = useRef<Tool>("cursor");
  const startRef = useRef<Point | null>(null);
  const fitted = useRef(false);
  const userMoved = useRef(false);
  const [history, setHistory] = useState<Bar[]>([]);
  const [liveBars, setLiveBars] = useState<Bar[]>([]);
  const [period, setPeriod] = useState(1);
  const [tool, setTool] = useState<Tool>("cursor");
  const [drawings, setDrawings] = useState<Drawing[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  // Refetch history when the feed (re)connects, not on every status flicker.
  const isLive = status === "live";
  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const up = token(node, "--up"),
      down = token(node, "--down"),
      grid = token(node, "--border");
    const instance = createChart(node, {
      autoSize: true,
      height: 340,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: token(node, "--text-2"),
        attributionLogo: false,
      },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: Time) =>
          typeof time === "number" ? formatTime(time) : "",
      },
      localization: {
        timeFormatter: (time: Time) =>
          typeof time === "number" ? formatTime(time) : "",
      },
    });
    const candles = instance.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      wickUpColor: up,
      wickDownColor: down,
      borderUpColor: up,
      borderDownColor: down,
    });
    const volumes = instance.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: "volume" },
        priceLineVisible: false,
        lastValueVisible: false,
      },
      1,
    );
    instance.panes()[1]?.setStretchFactor(0.25);
    // Keep the whole session in view while the container settles (drawer slide-in,
    // phone rotation) until the viewer pans or zooms on their own.
    const markMoved = () => {
      userMoved.current = true;
    };
    node.addEventListener("pointerdown", markMoved);
    node.addEventListener("wheel", markMoved, { passive: true });
    const resize = new ResizeObserver(() => {
      if (fitted.current && !userMoved.current) instance.timeScale().fitContent();
    });
    resize.observe(node);
    chart.current = instance;
    candle.current = candles;
    volume.current = volumes;
    instance.subscribeClick((event) => {
      if (
        toolRef.current === "cursor" ||
        typeof event.time !== "number" ||
        !event.point
      )
        return;
      const price = candles.coordinateToPrice(event.point.y);
      if (price === null) return;
      const point = { time: event.time, price };
      if (toolRef.current === "horizontal")
        setDrawings((current) => [...current, { kind: "horizontal", price }]);
      else if (!startRef.current) {
        startRef.current = point;
        setPending(true);
      } else {
        const start = startRef.current;
        if (start.time !== point.time)
          setDrawings((current) => [
            ...current,
            { kind: "trend", start, end: point },
          ]);
        startRef.current = null;
        setPending(false);
      }
    });
    return () => {
      resize.disconnect();
      node.removeEventListener("pointerdown", markMoved);
      node.removeEventListener("wheel", markMoved);
      instance.remove();
      chart.current = null;
      candle.current = null;
      volume.current = null;
      drawingSeries.current = [];
      priceLines.current = [];
    };
  }, []);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    async function load() {
      try {
        const params = new URLSearchParams({ symbol, limit: "300" });
        const response = await fetch(
          apiUrl(`/api/v1/quotes/minutes?${params}`),
          withAuthHeaders({ signal: controller.signal }),
        );
        if (!response.ok)
          throw errorFromResponse(
            response,
            await response.json().catch(() => null),
          );
        const payload = (await response.json()) as { bars: HistoryBar[] };
        const bars = payload.bars
          .map((bar) => ({
            time: Date.parse(bar.minute) / 1000,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
            volume: bar.observed_volume_change_lots * 1000,
            is_partial: bar.is_partial,
            lastTradeTime: Date.parse(bar.last_trade_time) || 0,
          }))
          .filter(
            (bar) =>
              Number.isFinite(bar.time) &&
              [bar.open, bar.high, bar.low, bar.close, bar.volume].every(
                Number.isFinite,
              ),
          );
        if (active) {
          setHistory(bars);
          setError(null);
        }
      } catch (err) {
        if (active && !controller.signal.aborted)
          setError(err instanceof Error ? err.message : "分鐘資料載入失敗");
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
    };
  }, [symbol, refresh, isLive]);
  useEffect(() => {
    const timer = window.setInterval(
      () => setRefresh((value) => value + 1),
      30_000,
    );
    return () => window.clearInterval(timer);
  }, [symbol]);
  const latest = quote?.symbol === symbol ? quote.candle : undefined;
  useEffect(() => {
    if (!latest || !Number.isFinite(latest.time)) return;
    const current: Bar = {
      ...latest,
      lastTradeTime: Date.parse(quote?.trade_time || "") || 0,
    };
    // Capture each stream update so completed minutes remain visible between history polls.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLiveBars((previous) => {
      // Each stream snapshot repeats the candle; skip unchanged ones so the chart
      // is not redrawn every second.
      const last = previous[previous.length - 1];
      if (
        last &&
        last.time === current.time &&
        last.close === current.close &&
        last.high === current.high &&
        last.low === current.low &&
        last.volume === current.volume
      )
        return previous;
      return mergeBars(previous, [current]).slice(-600);
    });
  }, [latest, quote?.trade_time]);
  const data = useMemo(
    () => aggregateBars(mergeBars(history, liveBars), period),
    [history, liveBars, period],
  );
  // Re-fit when the bar count changes meaning; keep the user's zoom on live updates.
  // (The body is keyed by symbol, so a new symbol starts unfitted anyway.)
  useEffect(() => {
    fitted.current = false;
    userMoved.current = false;
  }, [period]);
  useEffect(() => {
    const node = host.current;
    if (!candle.current || !volume.current || !node) return;
    const up = token(node, "--up"),
      down = token(node, "--down");
    candle.current.setData(
      data.map((bar) => ({ ...bar, time: bar.time as UTCTimestamp })),
    );
    volume.current.setData(
      data.map((bar) => ({
        time: bar.time as UTCTimestamp,
        value: bar.volume,
        color: bar.close >= bar.open ? up : down,
      })),
    );
    // Wait for the history: a live tick often lands first, and fitting a single
    // bar would leave the whole morning off-screen once the history arrives.
    if (data.length && !fitted.current && !loading) {
      chart.current?.timeScale().fitContent();
      fitted.current = true;
    }
  }, [data, loading]);
  useEffect(() => {
    const instance = chart.current,
      series = candle.current,
      node = host.current;
    if (!instance || !series || !node) return;
    drawingSeries.current.forEach((line) => instance.removeSeries(line));
    priceLines.current.forEach((line) => series.removePriceLine(line));
    drawingSeries.current = [];
    priceLines.current = [];
    const color = token(node, "--accent");
    for (const drawing of drawings) {
      if (drawing.kind === "horizontal")
        priceLines.current.push(
          series.createPriceLine({
            price: drawing.price,
            color,
            lineWidth: 2,
            axisLabelVisible: true,
            title: "",
          }),
        );
      else {
        const line = instance.addSeries(LineSeries, {
          color,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        line.setData(
          [drawing.start, drawing.end]
            .sort((a, b) => a.time - b.time)
            .map((point) => ({
              time: point.time as UTCTimestamp,
              value: point.price,
            })),
        );
        drawingSeries.current.push(line);
      }
    }
  }, [drawings]);
  function selectTool(value: Tool) {
    toolRef.current = value;
    startRef.current = null;
    setPending(false);
    setTool(value);
  }
  const last = data[data.length - 1];
  return (
    <section className="intraday-chart" aria-label={`${symbol} 分時走勢`}>
      <div className="intraday-heading">
        <h3>分時走勢</h3>
        <div className="intraday-controls" aria-label="時間週期">
          <button
            type="button"
            aria-pressed={period === 1}
            onClick={() => setPeriod(1)}
          >
            1 分
          </button>
          <button
            type="button"
            aria-pressed={period === 5}
            onClick={() => setPeriod(5)}
          >
            5 分
          </button>
        </div>
      </div>
      <p className="intraday-note">
        台北時間 · 成交量為觀察到的累計量變化，可能不完整。
        {last?.is_partial ? " 最新 K 棒為部分資料。" : ""}
      </p>
      {last && (
        <p className="intraday-legend">
          {formatTime(last.time)}　開 {formatPrice(last.open)}　高 {formatPrice(last.high)}　低{" "}
          {formatPrice(last.low)}　收 {formatPrice(last.close)}　量 {(last.volume / 1000).toLocaleString("zh-TW")}{" "}
          張
        </p>
      )}
      <div className="intraday-tools" aria-label="畫線工具">
        <button
          type="button"
          aria-pressed={tool === "cursor"}
          onClick={() => selectTool("cursor")}
        >
          瀏覽
        </button>
        <button
          type="button"
          aria-pressed={tool === "horizontal"}
          onClick={() => selectTool("horizontal")}
        >
          水平線
        </button>
        <button
          type="button"
          aria-pressed={tool === "trend"}
          onClick={() => selectTool("trend")}
        >
          趨勢線
        </button>
        <button
          type="button"
          disabled={!drawings.length && !pending}
          onClick={() => {
            if (pending) {
              startRef.current = null;
              setPending(false);
            } else setDrawings((current) => current.slice(0, -1));
          }}
        >
          復原
        </button>
        <button
          type="button"
          disabled={!drawings.length && !pending}
          onClick={() => {
            startRef.current = null;
            setPending(false);
            setDrawings([]);
          }}
        >
          清除
        </button>
      </div>
      {tool !== "cursor" && (
        <p className="intraday-note">
          {tool === "horizontal"
            ? "點選圖表設定價格線。"
            : pending
              ? "點選趨勢線終點。"
              : "點選趨勢線起點與終點。"}
        </p>
      )}
      <div className="intraday-canvas" ref={host} />
      {loading && !data.length && <p role="status">載入分鐘資料中…</p>}
      {!loading && !data.length && <p>目前沒有分鐘成交資料。</p>}
      {error && (
        <p role="status" className="intraday-error">
          歷史分鐘資料暫時無法取得：{error}。將自動重試。
        </p>
      )}
    </section>
  );
}
