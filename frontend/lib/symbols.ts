import type { MarketCode } from "./api/endpoints";

export const DEFAULT_SYMBOL = "2330.TW";
export const DEFAULT_BENCHMARK = "^TWII";
export const OTC_BENCHMARK = "^TWOII";

export function marketOf(symbol: string): MarketCode {
  const normalized = symbol.trim().toUpperCase();
  return normalized.endsWith(".TWO") || normalized === OTC_BENCHMARK ? "TWO" : "TW";
}

/** Swap the default benchmark when the symbol moves between TW and TWO. */
export function benchmarkForSymbol(symbol: string, benchmark: string): string {
  if (symbol.endsWith(".TWO") && benchmark === DEFAULT_BENCHMARK) return OTC_BENCHMARK;
  if (symbol.endsWith(".TW") && benchmark === OTC_BENCHMARK) return DEFAULT_BENCHMARK;
  return benchmark;
}
