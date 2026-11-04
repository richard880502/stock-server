"use client";

import { useId, useState } from "react";
import { useWatchlist } from "@/lib/use-watchlist";
import { StarIcon } from "./icons";

/**
 * Star toggle for the signed-in user's watchlist (自選股).
 *  - `labeled`: button with text plus an "n/25" counter, for page headers.
 *  - `icon`: compact star for table rows; hidden when the watchlist is unavailable.
 * Without a Google session the control stays visible but inert (aria-disabled,
 * so it keeps keyboard focus and its explanation); server refusals such as the
 * 25-stock cap appear as inline text.
 */
export function WatchlistStar({
  symbol,
  variant = "labeled",
  stopPropagation = false,
}: {
  symbol: string;
  variant?: "labeled" | "icon";
  /** Keep the click from reaching a clickable table row. */
  stopPropagation?: boolean;
}) {
  const watchlist = useWatchlist();
  const [note, setNote] = useState<{ symbol: string; text: string } | null>(null);
  const noteId = useId();

  const member = watchlist.has(symbol);
  const busy = watchlist.pending.has(symbol.toUpperCase());
  const unavailable = watchlist.phase === "unavailable";
  const loading = watchlist.phase === "loading";
  const inert = unavailable || loading || busy;
  const label = member ? "移出自選股" : "加入自選股";
  const visibleNote =
    note?.symbol === symbol ? note.text : unavailable && variant === "labeled" ? watchlist.message : null;

  if (variant === "icon" && unavailable) return null;

  const onClick = async (event: { stopPropagation: () => void }) => {
    if (stopPropagation) event.stopPropagation();
    if (inert) return;
    setNote(null);
    const result = await watchlist.setMember(symbol, !member);
    if (!result.ok) setNote({ symbol, text: result.message });
  };

  const title = unavailable ? (watchlist.message ?? undefined) : undefined;

  return (
    <span className={`star-wrap ${variant}`}>
      <button
        type="button"
        className={`${variant === "icon" ? "icon-button small" : "button small"} star-button`}
        aria-pressed={member}
        aria-label={variant === "icon" ? `${label}：${symbol}` : undefined}
        aria-disabled={inert || undefined}
        aria-busy={busy || undefined}
        aria-describedby={visibleNote ? noteId : undefined}
        title={title ?? (variant === "icon" ? label : undefined)}
        onClick={onClick}
      >
        <StarIcon filled={member} size={16} />
        {variant === "labeled" && <span>{label}</span>}
      </button>
      {variant === "labeled" && watchlist.phase === "ready" && (
        <span className="star-count num" title="自選股數量 / 上限">
          {watchlist.count}/{watchlist.limit}
        </span>
      )}
      <span id={noteId} className="star-note" role="status">
        {visibleNote}
      </span>
    </span>
  );
}
