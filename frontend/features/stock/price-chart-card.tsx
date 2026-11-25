"use client";

import { Card } from "@/components/card";
import { CandleChart, MA_PERIODS, type MaPeriod, RANGE_PRESETS, type RangeKey, type TradeMarker } from "@/components/charts/candle-chart";
import { SegmentedControl } from "@/components/segmented-control";
import { readStorage, writeStorage } from "@/lib/safe-storage";
import { type PriceFeed, useCandles } from "@/lib/price-feed";
import { useSyncExternalStore } from "react";

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

function parseRange(stored: string | null, fallback: RangeKey): RangeKey {
  return RANGE_PRESETS.some((p) => p.key === stored) ? (stored as RangeKey) : fallback;
}

const DEFAULT_MA = "5,20,60";
function parseMa(stored: string): MaPeriod[] {
  return stored
    .split(",")
    .map(Number)
    .filter((n): n is MaPeriod => (MA_PERIODS as readonly number[]).includes(n));
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
}: {
  symbol: string;
  name?: string | null;
  asOf?: string;
  markers?: TradeMarker[];
  title?: string;
  sub?: string;
  defaultRange?: RangeKey;
  feed?: PriceFeed;
}) {
  const { candles, live, loading, error } = useCandles(symbol, asOf, feed);
  const storedRange = useSyncExternalStore(subscribePrefs, () => readStorage(RANGE_KEY), () => null);
  const storedMa = useSyncExternalStore(subscribePrefs, () => readStorage(MA_KEY) ?? DEFAULT_MA, () => DEFAULT_MA);
  const range = parseRange(storedRange, defaultRange);
  const ma = parseMa(storedMa);

  const chooseRange = (value: RangeKey) => savePref(RANGE_KEY, value);
  const toggleMa = (period: MaPeriod) => {
    const next = ma.includes(period) ? ma.filter((p) => p !== period) : [...ma, period].sort((a, b) => a - b);
    savePref(MA_KEY, next.join(","));
  };

  const subtitle =
    sub ?? `${name ? `${name} · ` : ""}還原權值 · 資料截止 ${asOf ?? "最新"} · 往左拖可看更早（最多三年）`;

  return (
    <Card title={title} sub={subtitle} className="price-chart-card">
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
              onClick={() => toggleMa(period)}
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
    </Card>
  );
}
