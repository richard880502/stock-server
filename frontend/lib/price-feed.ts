"use client";

import { useEffect, useState } from "react";
import { getBars } from "./api/endpoints";
import { type Candle, historyStart, wantsLive } from "./candles";

export type { Candle } from "./candles";

/**
 * Where the chart gets its prices. The daily feed reads stored, point-in-time bars;
 * a live feed (e.g. a Shioaji SSE stream once API keys are connected) implements
 * `subscribe` as well and pushes updates to the latest candle.
 */
export interface PriceFeed {
  id: string;
  history(symbol: string, opts: { asOf?: string; start?: string; signal?: AbortSignal }): Promise<Candle[]>;
  /** Optional live updates of the most recent candle. Returns an unsubscribe function. */
  subscribe?(symbol: string, onCandle: (candle: Candle) => void): () => void;
}

export const dailyFeed: PriceFeed = {
  id: "daily",
  async history(symbol, { asOf, start, signal }) {
    const series = await getBars(symbol, { asOf, start, adjust: "back" }, { signal });
    return series.bars.map((bar) => ({
      time: bar.trading_date,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      volume: bar.volume,
    }));
  },
};

export type CandleState = {
  candles: Candle[] | null;
  /** Latest live update for the last candle, when the feed streams. */
  live: Candle | null;
  loading: boolean;
  error: string | null;
};

type Loaded = { key: string; candles: Candle[] | null; error: string | null };

/** Loads up to three years of candles ending at `asOf` and follows live updates if the feed has them. */
export function useCandles(
  symbol: string | null,
  asOf: string | undefined,
  feed: PriceFeed = dailyFeed,
): CandleState {
  const key = `${feed.id}|${symbol ?? ""}|${asOf ?? ""}`;
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [live, setLive] = useState<{ key: string; candle: Candle } | null>(null);

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    feed
      .history(symbol, { asOf, start: historyStart(asOf), signal: controller.signal })
      .then((candles) => setLoaded({ key, candles, error: null }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : "無法載入 K 線資料";
        setLoaded({ key, candles: null, error: message });
      });
    // Live updates only make sense when looking at today, not a historical as-of date.
    const today = new Date().toISOString().slice(0, 10);
    const unsubscribe =
      feed.subscribe && wantsLive(asOf, today)
        ? feed.subscribe(symbol, (candle) => setLive({ key, candle }))
        : undefined;
    return () => {
      controller.abort();
      unsubscribe?.();
    };
  }, [key, symbol, asOf, feed]);

  const current = loaded?.key === key ? loaded : null;
  return {
    candles: current?.candles ?? null,
    live: live?.key === key ? live.candle : null,
    loading: Boolean(symbol) && current === null,
    error: current?.error ?? null,
  };
}
