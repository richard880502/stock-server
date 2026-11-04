"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useDashboard } from "@/lib/dashboard-context";
import { ErrorBanner } from "./error-banner";
import { QueryBar } from "./query-bar";
import { SyncPendingNotice } from "./sync-pending-notice";
import { Topbar } from "./topbar";

/** Chrome shared by every route: sticky header + query bar, notices, content, footer. */
export function DashboardFrame({ children }: { children: ReactNode }) {
  const { error, setError, refresh, signalData } = useDashboard();
  const { marketEnvironment, syncWait, cancelSync } = signalData;
  // The signal view shows its own countdown inside the quick stock view; the watchlist page
  // is unrelated to a signal load that happens to be retrying in the background.
  const path = (usePathname() ?? "/").replace(/\/+$/, "");
  const onSignalRoute = path === "";
  // The watchlist page has its own "add a stock" search; the analysis query bar does not apply.
  const onWatchlistRoute = path === "/watchlist";

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        跳到主要內容
      </a>
      <header className="app-header">
        <div className="container">
          <Topbar />
          {!onWatchlistRoute && <QueryBar />}
        </div>
      </header>

      <main id="main" className="container page" tabIndex={-1}>
        {syncWait && !onSignalRoute && !onWatchlistRoute && <SyncPendingNotice wait={syncWait} onCancel={cancelSync} />}
        {error && (
          <ErrorBanner message={error} onDismiss={() => setError(null)} onRetry={() => refresh()} />
        )}
        {children}
      </main>

      <footer className="app-footer">
        <div className="container footer-inner">
          <p>
            Quant Signal 是研究工具：分數、信心度與情境皆為模型輸出，不構成投資建議，也不保證未來結果。
          </p>
          <p className="num">
            市場資料時間{" "}
            {marketEnvironment?.data_available_at.replace("T", " ").slice(0, 16) ?? "—"}
          </p>
        </div>
      </footer>
    </div>
  );
}
