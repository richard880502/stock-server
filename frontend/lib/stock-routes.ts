import { normalizeSymbolInput } from "./symbols";

/**
 * Every stock has its own page at `/stock/<symbol>` (e.g. `/stock/2330.TW`, `/stock/6488.TWO`).
 * The path is the only source of truth for "which stock is open": components link here instead
 * of setting shared state and navigating to the home page.
 */
export const STOCK_ROUTE_PREFIX = "/stock/";

export type StockSection = "summary" | "chart" | "evidence" | "profile" | "chips";

/** Link to a stock's page, optionally at one of its sections. */
export function stockHref(symbol: string, section?: StockSection): string {
  const path = `${STOCK_ROUTE_PREFIX}${encodeURIComponent(symbol.trim().toUpperCase())}`;
  return section && section !== "summary" ? `${path}#${section}` : path;
}

/** Strategy backtest / scenario forecast preset to one stock. */
export const backtestHref = (symbol: string) => `/backtest?symbol=${encodeURIComponent(symbol.trim().toUpperCase())}`;
export const forecastHref = (symbol: string) => `/forecast?symbol=${encodeURIComponent(symbol.trim().toUpperCase())}`;

/**
 * The API symbol named by a `/stock/<segment>` path segment (or a `?symbol=` value), or null
 * when it is not a stock code. A bare number means the listed market: `2330` → `2330.TW`.
 */
export function symbolFromParam(segment: string | null | undefined): string | null {
  if (!segment) return null;
  let text = segment;
  try {
    text = decodeURIComponent(segment);
  } catch {
    return null;
  }
  const symbol = normalizeSymbolInput(text);
  return symbol && !symbol.startsWith("^") ? symbol : null;
}

/** The stock a pathname is about (`/stock/2330.TW/` → `2330.TW`), or null on any other route. */
export function symbolFromPath(pathname: string | null | undefined): string | null {
  const path = pathname ?? "";
  if (!path.startsWith(STOCK_ROUTE_PREFIX)) return null;
  const segment = path.slice(STOCK_ROUTE_PREFIX.length).replace(/\/+$/, "");
  return segment.includes("/") ? null : symbolFromParam(segment);
}

export const isStockRoute = (pathname: string | null | undefined) => (pathname ?? "").startsWith(STOCK_ROUTE_PREFIX);
