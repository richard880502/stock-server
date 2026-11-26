"use client";

import { useEffect, useState } from "react";
import { getSignal } from "@/lib/api/endpoints";
import type { Signal } from "@/lib/api/types";
import { benchmarkForSymbol } from "@/lib/symbols";

const MAX_CONCURRENT = 4;
// Module-level: switching rows or remounting the page never refetches a known signal.
const cache = new Map<string, Signal>();
const failed = new Set<string>();
const inflight = new Set<string>();
const listeners = new Set<() => void>();

const keyOf = (symbol: string, asOf: string, benchmark: string) => `${symbol}|${asOf}|${benchmark}`;

/** Deterministic signals for the rail symbols, fetched with bounded concurrency; failures are silent. */
export function useRailSignals(symbols: string[], asOf: string, benchmark: string): ReadonlyMap<string, Signal> {
  const [, bump] = useState(0);
  const list = symbols.join(",");
  useEffect(() => {
    const notify = () => bump((n) => n + 1);
    listeners.add(notify);
    return () => void listeners.delete(notify);
  }, []);
  useEffect(() => {
    let cancelled = false;
    const queue = list
      .split(",")
      .filter(Boolean)
      .filter((symbol) => {
        const key = keyOf(symbol, asOf, benchmarkForSymbol(symbol, benchmark));
        return !cache.has(key) && !failed.has(key) && !inflight.has(key);
      });
    const worker = async () => {
      for (let symbol = queue.shift(); symbol && !cancelled; symbol = queue.shift()) {
        const bench = benchmarkForSymbol(symbol, benchmark);
        const key = keyOf(symbol, asOf, bench);
        if (inflight.has(key)) continue;
        inflight.add(key);
        try {
          cache.set(key, await getSignal({ symbol, asOf, benchmark: bench }));
        } catch {
          failed.add(key);
        } finally {
          inflight.delete(key);
          listeners.forEach((listener) => listener());
        }
      }
    };
    for (let i = 0; i < Math.min(MAX_CONCURRENT, queue.length); i++) void worker();
    return () => {
      cancelled = true;
    };
  }, [list, asOf, benchmark]);
  const result = new Map<string, Signal>();
  for (const symbol of list.split(",").filter(Boolean)) {
    const signal = cache.get(keyOf(symbol, asOf, benchmarkForSymbol(symbol, benchmark)));
    if (signal) result.set(symbol, signal);
  }
  return result;
}
