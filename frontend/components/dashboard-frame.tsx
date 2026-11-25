"use client";

import { usePathname } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { useDashboard } from "@/lib/dashboard-context";
import { shouldShowCompactQuery } from "@/lib/default-as-of";
import { CommandPalette, useCommandPaletteShortcut } from "./command-palette";
import { ErrorBanner } from "./error-banner";
import { focusSymbolField, QueryBar, QuerySummary } from "./query-bar";
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
  const [paletteOpen, setPaletteOpen] = useState(false);
  const openPalette = useCallback(() => setPaletteOpen(true), []);
  useCommandPaletteShortcut(openPalette);

  // Phones show only the one-line query summary until expanded; on desktop the full form stays in
  // the page flow and the summary slides in under the sticky topbar once the form has scrolled away.
  const [queryExpanded, setQueryExpanded] = useState(false);
  const [compactShown, setCompactShown] = useState(false);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      setCompactShown((was) => shouldShowCompactQuery(window.scrollY, was));
    };
    const onScroll = () => {
      if (!frame) frame = window.requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);
  const editQuery = useCallback(() => {
    const phone = window.matchMedia("(max-width: 640px)").matches;
    if (phone && queryExpanded) {
      setQueryExpanded(false);
      return;
    }
    setQueryExpanded(true);
    window.scrollTo({ top: 0 });
    // The form is display:none on phones until expanded, so focus after it renders.
    window.requestAnimationFrame(focusSymbolField);
  }, [queryExpanded]);
  // "/" focuses the symbol field; on phones the field must be expanded first.
  useEffect(() => {
    if (onWatchlistRoute) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      if (window.matchMedia("(max-width: 640px)").matches) {
        setQueryExpanded(true);
        window.requestAnimationFrame(focusSymbolField);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onWatchlistRoute]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        跳到主要內容
      </a>
      <header className="app-header">
        <div className="container">
          <Topbar onOpenPalette={openPalette} />
          {!onWatchlistRoute && (
            <QuerySummary visible={compactShown} expanded={queryExpanded} onEdit={editQuery} />
          )}
        </div>
      </header>
      {!onWatchlistRoute && (
        <div className="query-wrap" data-expanded={queryExpanded}>
          <div className="container">
            <QueryBar expanded={queryExpanded} />
          </div>
        </div>
      )}

      <main id="main" className="container page" tabIndex={-1}>
        {syncWait && !onSignalRoute && !onWatchlistRoute && <SyncPendingNotice wait={syncWait} onCancel={cancelSync} />}
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
          <p className="num">
            市場資料時間{" "}
            {marketEnvironment?.data_available_at.replace("T", " ").slice(0, 16) ?? "—"}
          </p>
        </div>
      </footer>
    </div>
  );
}
