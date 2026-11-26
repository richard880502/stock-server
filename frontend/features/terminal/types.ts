import type { LiveQuote, LiveQuoteStatus } from "@/lib/live-quotes";

/** One row of the left rail: a watchlist stock, or a recent symbol when signed out. */
export type RailItem = {
  symbol: string;
  name: string;
  /** Last official daily close; null when the stock has no daily data yet. */
  close: number | null;
  /** Ratio (-0.0359 = -3.59%), the same unit as LiveQuote.change_pct. */
  dayChangePct: number | null;
  /** Up to 20 recent daily closes for the fallback sparkline. */
  recentCloses: number[];
};

export type LiveState = {
  /** False when the server has live quotes switched off (`/auth/config`). */
  enabled: boolean;
  status: LiveQuoteStatus;
  quotes: ReadonlyMap<string, LiveQuote>;
};
