"use client";

import { useEffect, useMemo, useState } from "react";
import { withAuthHeaders } from "@/lib/api/auth";
import { apiUrl, errorFromResponse } from "@/lib/api/client";
import { readSse } from "@/lib/api/sse";

export type LiveQuote = {
  symbol: string;
  price: number;
  change_pct: number | null;
  total_volume: number | null;
  trade_time: string;
  received_at: string;
  candle?: { time: number; open: number; high: number; low: number; close: number; volume: number; is_partial: true } | null;
};
export type LiveQuoteStatus = "disabled" | "connecting" | "live" | "unavailable" | "paused";
type State = { status: LiveQuoteStatus; quotes: Map<string, LiveQuote>; message?: string };

/** One authenticated stream per watchlist, suspended while the tab is hidden. */
export function useLiveQuotes(symbols: string[]): State {
  const key = useMemo(() => [...new Set(symbols.map((s) => s.toUpperCase()))].sort().slice(0, 25).join(","), [symbols]);
  const [state, setState] = useState<State>({ status: "connecting", quotes: new Map() });
  const [visible, setVisible] = useState(() => typeof document === "undefined" || !document.hidden);
  useEffect(() => {
    const change = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", change);
    return () => document.removeEventListener("visibilitychange", change);
  }, []);
  useEffect(() => {
    if (!key || !visible) return;
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let delay = 1000;
    const allowed = new Set(key.split(","));
    const connect = async () => {
      controller = new AbortController();
      setState((previous) => ({ status: "connecting", quotes: new Map([...previous.quotes].filter(([symbol]) => allowed.has(symbol))) }));
      let terminal = false;
      try {
        const response = await fetch(apiUrl(`/api/v1/quotes/stream?${new URLSearchParams({ symbols: key })}`), withAuthHeaders({ signal: controller.signal, headers: { Accept: "text/event-stream" } }));
        if (!response.ok || !response.body) throw errorFromResponse(response, await response.json().catch(() => null));
        await readSse(response.body, ({ event, data }) => {
          if (event !== "state" || cancelled) return;
          try {
            const payload = JSON.parse(data) as { status: LiveQuoteStatus; quotes: LiveQuote[]; message?: string };
            if (!["disabled", "connecting", "live", "unavailable"].includes(payload.status) || !Array.isArray(payload.quotes)) return;
            const quotes = new Map<string, LiveQuote>();
            if (payload.status !== "disabled") for (const quote of payload.quotes) {
              if (quote && allowed.has(quote.symbol) && typeof quote.price === "number" && Number.isFinite(quote.price)) quotes.set(quote.symbol, quote);
            }
            setState({ status: payload.status, quotes, message: payload.message });
            if (payload.status === "live") delay = 1000;
            if (payload.status === "disabled") terminal = true;
          } catch { /* Ignore malformed events and wait for the next snapshot. */ }
        });
      } catch (error) {
        if (!cancelled && !controller.signal.aborted) setState((previous) => ({ ...previous, status: "unavailable", message: error instanceof Error ? error.message : "連線中斷" }));
      }
      if (!cancelled && !terminal) {
        setState((previous) => ({ ...previous, status: "unavailable", message: previous.message || "連線中斷，將自動重試。" }));
        retryTimer = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 30_000);
      }
    };
    void connect();
    return () => { cancelled = true; controller?.abort(); if (retryTimer) clearTimeout(retryTimer); };
  }, [key, visible]);
  if (!key) return { status: "disabled", quotes: new Map() };
  return visible ? state : { ...state, status: "paused" };
}
