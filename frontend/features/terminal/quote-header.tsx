"use client";

import type { Signal } from "@/lib/api/types";
import type { LiveQuote } from "@/lib/live-quotes";
import type { LiveState } from "./types";

/** Placeholder: big price, change and day stats for the selected stock. */
export function QuoteHeader({
  symbol,
  name,
}: {
  symbol: string;
  name: string | null;
  quote?: LiveQuote;
  signal: Signal | null;
  live: LiveState;
}) {
  return (
    <div className="terminal-quote">
      {symbol} {name}
    </div>
  );
}
