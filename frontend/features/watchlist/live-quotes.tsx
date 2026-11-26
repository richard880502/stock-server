"use client";

import { useEffect, useMemo, useState } from "react";
import { withAuthHeaders } from "@/lib/api/auth";
import { apiUrl, errorFromResponse } from "@/lib/api/client";
import { readSse } from "@/lib/api/sse";
import type { WatchlistItem } from "@/lib/api/types";
import { formatPrice, formatSignedPct } from "@/lib/format";
import "./live-quotes.css";

type Quote = {
  symbol: string;
  price: number;
  change_pct: number | null;
  total_volume: number | null;
  trade_time: string;
  received_at: string;
};
type QuoteState = {
  status: "disabled" | "connecting" | "live" | "unavailable";
  quotes: Quote[];
  message?: string;
};

const timeFormat = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function displayTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "時間未提供" : timeFormat.format(date);
}

function isStale(value: string, now: number) {
  const timestamp = Date.parse(value);
  return !Number.isFinite(timestamp) || now - timestamp > 30_000;
}

export function LiveQuotes({ items }: { items: WatchlistItem[] }) {
  const symbols = useMemo(
    () => [...new Set(items.map((item) => item.symbol.toUpperCase()))].sort().slice(0, 25).join(","),
    [items],
  );
  const [state, setState] = useState<QuoteState>({ status: "connecting", quotes: [] });
  const [now, setNow] = useState(() => Date.now());
  const [visible, setVisible] = useState(() => typeof document === "undefined" || !document.hidden);

  useEffect(() => {
    const change = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", change);
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => {
      document.removeEventListener("visibilitychange", change);
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!symbols || !visible) return;
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let delay = 1000;

    const connect = async () => {
      controller = new AbortController();
      setState((previous) => ({ ...previous, status: "connecting", message: undefined }));
      let terminal = false;
      try {
        const url = apiUrl(`/api/v1/quotes/stream?${new URLSearchParams({ symbols })}`);
        const response = await fetch(url, withAuthHeaders({
          signal: controller.signal,
          headers: { Accept: "text/event-stream" },
        }));
        if (!response.ok || !response.body) {
          throw errorFromResponse(response, await response.json().catch(() => null));
        }
        await readSse(response.body, ({ event, data }) => {
          if (event !== "state" || cancelled) return;
          try {
            const payload = JSON.parse(data) as QuoteState;
            if (!["disabled", "connecting", "live", "unavailable"].includes(payload.status) || !Array.isArray(payload.quotes)) return;
            const allowed = new Set(symbols.split(","));
            const quotes = payload.status === "disabled" ? [] : payload.quotes.filter(
              (quote) => quote && allowed.has(quote.symbol) && typeof quote.price === "number" && Number.isFinite(quote.price),
            );
            setState({ ...payload, quotes });
            if (payload.status === "live") delay = 1000;
            if (payload.status === "disabled") terminal = true;
          } catch {
            // Ignore malformed events and wait for the next snapshot.
          }
        });
      } catch (error) {
        if (!cancelled && !controller.signal.aborted) {
          setState((previous) => ({
            ...previous,
            status: "unavailable",
            message: error instanceof Error ? error.message : "連線中斷",
          }));
        }
      }
      if (!cancelled && !terminal) {
        setState((previous) => ({ ...previous, status: "unavailable", message: "連線中斷，將自動重試。" }));
        retryTimer = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 30_000);
      }
    };
    void connect();
    return () => {
      cancelled = true;
      controller?.abort();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [symbols, visible]);

  if (!symbols) return null;
  const label = !visible ? "分頁背景中，已暫停連線" : {
    disabled: "即時報價未啟用",
    connecting: "連線中",
    live: "已連線",
    unavailable: "即時報價暫時無法使用",
  }[state.status];
  const names = new Map(items.map((item) => [item.symbol.toUpperCase(), item.name]));
  const quotes = new Map(state.quotes.map((quote) => [quote.symbol, quote]));

  return (
    <section className="live-quotes" aria-labelledby="live-quotes-title">
      <div className="live-quotes-heading">
        <h2 id="live-quotes-title">最新成交</h2>
        <span role="status">{label}</span>
      </div>
      <p className="live-quotes-note">來源：Shioaji · 時間為台北時間 · 報價超過 30 秒標示為逾時</p>
      {state.message && visible && <p className="live-quotes-note">{state.message}</p>}
      {state.status !== "disabled" && (
        <ul className="live-quotes-list">
          {symbols.split(",").map((symbol) => {
            const quote = quotes.get(symbol);
            const stale = quote && (
              isStale(quote.trade_time, now) || isStale(quote.received_at, now) || !visible || state.status !== "live"
            );
            return (
              <li key={symbol}>
                <div><strong>{names.get(symbol) || symbol}</strong> <span>{symbol.replace(/\.TWO?$/, "")}</span></div>
                {quote ? (
                  <>
                    <div className="live-quotes-price">
                      {formatPrice(quote.price)}{" "}
                      <span>{typeof quote.change_pct === "number" && Number.isFinite(quote.change_pct) ? formatSignedPct(quote.change_pct, 2) : "—"}</span>
                    </div>
                    <small>
                      成交 {displayTime(quote.trade_time)} · {stale ? "資料逾時或連線中斷" : `更新 ${displayTime(quote.received_at)}`}
                      {typeof quote.total_volume === "number" && Number.isFinite(quote.total_volume) ? ` · 累計量 ${quote.total_volume.toLocaleString("zh-TW")} 張` : ""}
                    </small>
                  </>
                ) : <p className="live-quotes-note">等待成交</p>}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
