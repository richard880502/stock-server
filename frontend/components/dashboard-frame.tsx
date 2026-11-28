"use client";

import { type ReactNode, useCallback, useState } from "react";
import { useDashboard } from "@/lib/dashboard-context";
import { CommandPalette, useCommandPaletteShortcut } from "./command-palette";
import { ErrorBanner } from "./error-banner";
import { Topbar } from "./topbar";

/** Chrome shared by every route: sticky header, notices, content, footer. */
export function DashboardFrame({ children }: { children: ReactNode }) {
  const { error, setError, refresh, signalData, needsSignal } = useDashboard();
  const { marketEnvironment } = signalData;
  const [paletteOpen, setPaletteOpen] = useState(false);
  const openPalette = useCallback(() => setPaletteOpen(true), []);
  // Deliberately no frame-level SyncPendingNotice: the signal, chip flow and market environment
  // load only on /stock/* and /watchlist (lib/signal-routes.ts), and both show their own wait
  // state. On every other route a notice would be about a stock the page is not showing.
  useCommandPaletteShortcut(openPalette);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        跳到主要內容
      </a>
      <header className="app-header">
        <div className="container">
          <Topbar onOpenPalette={openPalette} />
        </div>
      </header>

      <main id="main" className="container page" tabIndex={-1}>
        {error && (
          <ErrorBanner message={error} onDismiss={() => setError(null)} onRetry={() => refresh()} />
        )}
        {children}
      </main>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />

      <footer className="app-footer">
        <div className="container footer-inner">
          <p>
            Quant Signal 是研究工具：分數、信心度與情境皆為模型輸出，不構成投資建議，也不保證未來結果。
          </p>
          {needsSignal && (
            <p className="num">
              市場資料時間{" "}
              {marketEnvironment?.data_available_at.replace("T", " ").slice(0, 16) ?? "—"}
            </p>
          )}
        </div>
      </footer>
    </div>
  );
}
