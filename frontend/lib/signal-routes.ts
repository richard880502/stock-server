import { isStockRoute, symbolFromPath } from "./stock-routes";

/**
 * Routes that render the analysis symbol's signal, chip flow and market environment
 * (`/signals`, `/chip-flows`, `/market-environments`). Every other route skips those three
 * requests, so its own data is not queued behind ~0.5-1 s of server work.
 *
 * - `/stock/<symbol>` — the stock page (verdict, evidence, chip panel, AI advice).
 * - `/watchlist` — its 看盤 view (also reached through the `/terminal` redirect) shows the AI
 *   rail and the market-environment strip; the 列表 view does not, but the view is chosen on
 *   the client, so the route as a whole counts.
 *
 * Not on the list (they read only the query, never `signalData`): `/` (今日總覽 fetches the
 * market environment of both markets itself), `/market`, `/screener`, `/paper`, `/backtest`,
 * `/forecast`.
 */
export const SIGNAL_DATA_ROUTES = ["/watchlist"] as const;

/** Strip trailing slashes; the root becomes "/". */
export function normalizePath(pathname: string | null | undefined): string {
  const trimmed = (pathname ?? "/").replace(/\/+$/, "");
  return trimmed === "" ? "/" : trimmed;
}

export function routeUsesSignalData(pathname: string | null | undefined): boolean {
  const path = normalizePath(pathname);
  // A stock route whose code is not a stock (`/stock/abc`) shows an empty state and loads nothing.
  if (isStockRoute(path)) return symbolFromPath(path) !== null;
  return (SIGNAL_DATA_ROUTES as readonly string[]).includes(path);
}
