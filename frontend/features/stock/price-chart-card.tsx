"use client";

import { Card } from "@/components/card";
import { CandleChart, MA_PERIODS, type MaPeriod, RANGE_PRESETS, type RangeKey, type TradeMarker } from "@/components/charts/candle-chart";
import { SegmentedControl } from "@/components/segmented-control";
import { readStorage, writeStorage } from "@/lib/safe-storage";
import { type PriceFeed, useCandles } from "@/lib/price-feed";
import { DEFAULT_MA, parseMa, parseRange, toggleMa } from "./chart-prefs";
import { useMemo, useState, useSyncExternalStore } from "react";
import { today } from "@/lib/format";
import { useLiveQuotes } from "@/lib/live-quotes";
import { IntradayChart } from "./intraday-chart";

const RANGE_KEY = "quant_signal_chart_range";
const MA_KEY = "quant_signal_chart_ma";

// Chart preferences live in localStorage; useSyncExternalStore keeps server render and
// hydration on the defaults, then switches to the stored value without an effect.
const prefListeners = new Set<() => void>();
function subscribePrefs(listener: () => void) {
  prefListeners.add(listener);
  return () => prefListeners.delete(listener);
}
function savePref(key: string, value: string) {
  writeStorage(key, value);
  prefListeners.forEach((listener) => listener());
}

function subscribeToday(listener: () => void) {
  const timer = window.setInterval(listener, 60_000);
  return () => window.clearInterval(timer);
}

function LivePriceChart({ symbol }: { symbol: string }) {
  const symbols = useMemo(() => [symbol], [symbol]);
  const live = useLiveQuotes(symbols);
  return <IntradayChart symbol={symbol} quote={live.quotes.get(symbol.toUpperCase())} status={live.status} />;
}

/** Daily K-line card: range presets, MA toggles, volume pane; remembers the viewer's choices. */
export function PriceChartCard({
  symbol,
  name,
  asOf,
  markers,
  title = "日 K 線",
  sub,
  defaultRange = "6m",
  feed,
  liveAsOf,
}: {
  symbol: string;
  name?: string | null;
  asOf?: string;
  markers?: TradeMarker[];
  title?: string;
  sub?: string;
  defaultRange?: RangeKey;
  feed?: PriceFeed;
  /** Opt in only for current individual research; daily data may end earlier. */
  liveAsOf?: string;
}) {
  const currentDate = useSyncExternalStore(subscribeToday, today, () => "");
  const [selection, setSelection] = useState<{ symbol: string; date: string } | null>(null);
  const canShowLive = !!liveAsOf && liveAsOf === currentDate && markers === undefined;
  const liveMode = canShowLive && selection?.symbol === symbol && selection.date === liveAsOf;
  const { candles, live, loading, error } = useCandles(symbol, asOf, feed);
  const storedRange = useSyncExternalStore(subscribePrefs, () => readStorage(RANGE_KEY), () => null);
  const storedMa = useSyncExternalStore(subscribePrefs, () => readStorage(MA_KEY) ?? DEFAULT_MA, () => DEFAULT_MA);
  const range = parseRange(storedRange, defaultRange);
  const ma = parseMa(storedMa);

  const chooseRange = (value: RangeKey) => savePref(RANGE_KEY, value);
  const onToggleMa = (period: MaPeriod) => savePref(MA_KEY, toggleMa(ma, period).join(","));

  const subtitle =
    sub ?? `${name ? `${name} · ` : ""}還原權值 · 資料截止 ${asOf ?? "最新"} · 往左拖可看更早（最多三年）`;

  return (
    <Card title={liveMode ? "即時 K 線" : title} sub={liveMode ? `${name ? `${name} · ` : ""}即時分鐘成交 · 台北時間 · 當日資料` : subtitle} className="price-chart-card">
      {canShowLive && <SegmentedControl<"daily" | "live"> label="價格圖模式" value={liveMode ? "live" : "daily"} options={[{ value: "daily", label: "日 K 線" }, { value: "live", label: "即時 K 線" }]} onChange={(mode) => setSelection(mode === "live" ? { symbol, date: liveAsOf } : null)} />}
      {liveMode ? <LivePriceChart symbol={symbol} /> : <>
      <div className="price-chart-controls">
        <SegmentedControl<RangeKey>
          label="顯示區間"
          value={range}
          options={RANGE_PRESETS.map((p) => ({ value: p.key, label: p.label }))}
          onChange={chooseRange}
        />
        <div className="ma-toggles" role="group" aria-label="均線">
          {MA_PERIODS.map((period) => (
            <button
              key={period}
              type="button"
              className={`ma-toggle ma-${period}`}
              aria-pressed={ma.includes(period)}
              onClick={() => onToggleMa(period)}
            >
              MA{period}
            </button>
          ))}
        </div>
      </div>
      {error ? (
        <p className="note">{error}</p>
      ) : candles && candles.length > 0 ? (
        <CandleChart
          candles={candles}
          live={live}
          markers={markers}
          maPeriods={ma}
          range={range}
          label={`${symbol}${name ? ` ${name}` : ""} 日 K 線`}
        />
      ) : (
        <span className="skeleton block" style={{ height: 420 }} aria-busy={loading} aria-label="正在載入 K 線" />
      )}
      </>}
    </Card>
  );
}
