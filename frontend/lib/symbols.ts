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

const SYMBOL_SHAPE = /^(\^[A-Z0-9]+|\d{4,6}[A-Z]?(\.TWO?)?)$/;

/**
 * Turn free text ("2330", "6488.two", "^twii") into an API symbol, or null when
 * it does not look like one. A bare stock number defaults to the listed market (.TW).
 */
export function normalizeSymbolInput(text: string): string | null {
  const upper = text.trim().toUpperCase();
  if (!SYMBOL_SHAPE.test(upper)) return null;
  return /^\d/.test(upper) && !upper.includes(".") ? `${upper}.TW` : upper;
}
