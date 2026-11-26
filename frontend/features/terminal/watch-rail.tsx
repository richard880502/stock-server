"use client";

import type { LiveState, RailItem } from "./types";

/** Placeholder: compact watchlist rail with live prices. */
export function WatchRail({
  items,
  selected,
  onSelect,
}: {
  items: RailItem[];
  signedIn: boolean;
  live: LiveState;
  selected: string;
  onSelect: (symbol: string) => void;
}) {
  return (
    <aside className="terminal-rail" aria-label="自選清單">
      {items.map((item) => (
        <button key={item.symbol} type="button" aria-pressed={item.symbol === selected} onClick={() => onSelect(item.symbol)}>
          {item.symbol} {item.name}
        </button>
      ))}
    </aside>
  );
}
