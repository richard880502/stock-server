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

const FULL_SYMBOL = /^\d{4,6}[A-Z]?\.TWO?$/;
const BARE_CODE = /^\d{4,6}[A-Z]?$/;

function decodeSegment(segment: string | null | undefined): string | null {
  if (!segment) return null;
  try {
    return decodeURIComponent(segment).trim().toUpperCase();
  } catch {
    return null;
  }
}

/**
 * The symbol named by a `/stock/<segment>` path segment. Unlike `symbolFromParam` this needs the
 * market suffix: a bare code cannot tell 上市 from 上櫃, so the stock page looks it up and
 * redirects to the full symbol (see `bareCodeFromSegment`) instead of guessing `.TW`.
 */
export function symbolFromSegment(segment: string | null | undefined): string | null {
  const text = decodeSegment(segment);
  return text && FULL_SYMBOL.test(text) ? text : null;
}

/** `2330` / `00631L` when the segment is a stock code without its market suffix, else null. */
export function bareCodeFromSegment(segment: string | null | undefined): string | null {
  const text = decodeSegment(segment);
  return text && BARE_CODE.test(text) ? text : null;
}

/** The stock a pathname is about (`/stock/2330.TW/` → `2330.TW`), or null on any other route. */
export function symbolFromPath(pathname: string | null | undefined): string | null {
  const path = pathname ?? "";
  if (!path.startsWith(STOCK_ROUTE_PREFIX)) return null;
  const segment = path.slice(STOCK_ROUTE_PREFIX.length).replace(/\/+$/, "");
  return segment.includes("/") ? null : symbolFromSegment(segment);
}

export const isStockRoute = (pathname: string | null | undefined) => (pathname ?? "").startsWith(STOCK_ROUTE_PREFIX);
