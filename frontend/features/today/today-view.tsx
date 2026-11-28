"use client";

import { marketSession, SESSION_LABEL, taipeiClock } from "@/features/terminal/rail-session";
import { useNow } from "@/features/terminal/rail-now";
import { usePolled } from "@/features/paper/use-paper-data";
import { useDashboard } from "@/lib/dashboard-context";
import { listAccounts } from "@/lib/api/paper";
import { useWatchlist } from "@/lib/use-watchlist";
import { CandidatesCard } from "./candidates-card";
import { MarketCards } from "./market-cards";
import { PaperCard } from "./paper-card";
import { RecentCard } from "./recent-card";
import { SectorsCard } from "./sectors-card";
import { paperSummary } from "./today-model";
import { WatchlistCard } from "./watchlist-card";

function SessionLine() {
  const now = useNow(1000);
  return (
    <p className="today-status" aria-live="off">
      {now ? (
        <>
          <span>{SESSION_LABEL[marketSession(now)]}</span>
          <span aria-hidden="true">·</span>
          <span className="mono">台北時間 {taipeiClock(now)}</span>
        </>
      ) : (
        <span aria-hidden="true">&nbsp;</span>
      )}
    </p>
  );
}

/** 今日總覽: what to look at today. Every card loads on its own and never blocks its neighbours. */
export function TodayView() {
  const { recentSymbols } = useDashboard();
  const watchlist = useWatchlist();
  // Optional module: a 403 (not a Google sign-in) or any failure hides the card entirely.
  const accounts = usePolled("today-paper-accounts", (signal) => listAccounts({ signal }));
  const paperHidden = accounts.error !== null && !accounts.loading;
  const summary = paperSummary(accounts.data?.accounts);
  // The watchlist card already lists the recent stocks when the watchlist is unavailable.
  const showRecent = watchlist.phase !== "unavailable" && recentSymbols.length > 0;
  const hasSide = !paperHidden || showRecent;

  return (
    <div className="today">
      <div className="view-heading today-heading">
        <h1>今日總覽</h1>
        <SessionLine />
      </div>
      <MarketCards />
      <div className="today-main">
        <WatchlistCard />
        <CandidatesCard />
      </div>
      <div className={`today-lower${hasSide ? "" : " solo"}`}>
        <SectorsCard />
        {hasSide && (
          <div className="today-side">
            {!paperHidden && <PaperCard loading={accounts.loading} summary={summary} />}
            {showRecent && <RecentCard recent={recentSymbols} />}
          </div>
        )}
      </div>
    </div>
  );
}
