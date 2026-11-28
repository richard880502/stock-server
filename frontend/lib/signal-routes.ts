/**
 * Routes that render the analysis symbol's signal, chip flow and market environment
 * (`/signals`, `/chip-flows`, `/market-environments`). Every other route skips those three
 * requests, so its own data is not queued behind ~0.5-1 s of server work.
 *
 * - `/` — the stock view (verdict, evidence, chip panel, AI section).
 * - `/watchlist` — its 看盤 view (also reached through the `/terminal` redirect) shows the AI
 *   rail and the market-environment strip; the 列表 view does not, but the view is chosen on
 *   the client, so the route as a whole counts.
 *
 * Not on the list (they read only the query, never `signalData`): `/market`, `/screener`,
 * `/backtest`, `/forecast`.
 */
export const SIGNAL_DATA_ROUTES = ["/", "/watchlist"] as const;

/** Strip trailing slashes; the root becomes "/". */
export function normalizePath(pathname: string | null | undefined): string {
  const trimmed = (pathname ?? "/").replace(/\/+$/, "");
  return trimmed === "" ? "/" : trimmed;
}

export function routeUsesSignalData(pathname: string | null | undefined): boolean {
  return (SIGNAL_DATA_ROUTES as readonly string[]).includes(normalizePath(pathname));
}
