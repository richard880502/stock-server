"use client";

import { useEffect, useRef } from "react";
import type { WatchlistItem } from "@/lib/api/types";
import type { LiveQuote, LiveQuoteStatus } from "@/lib/live-quotes";
import { IntradayChart } from "@/features/stock/intraday-chart";
import "./intraday-drawer.css";

export function IntradayDrawer({ items, symbol, onSelect, onClose, quote, status }: {
  items: WatchlistItem[]; symbol: string; onSelect: (symbol: string) => void; onClose: () => void;
  quote?: LiveQuote; status: LiveQuoteStatus;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element?.showModal();
    return () => { element?.close(); previousFocus?.focus(); };
  }, []);
  return (
    <dialog ref={dialog} className="intraday-drawer" aria-labelledby="intraday-drawer-title" onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <header className="intraday-drawer-header">
        <h2 id="intraday-drawer-title">即時 K 線</h2>
        <button type="button" className="icon-button" onClick={onClose} aria-label="關閉即時 K 線">×</button>
      </header>
      <div className="intraday-drawer-content">
        <label htmlFor="intraday-symbol">自選股票</label>
        <select id="intraday-symbol" className="input" value={symbol} onChange={(event) => onSelect(event.target.value)}>
          {items.map((item) => <option key={item.symbol} value={item.symbol}>{item.name || item.symbol}（{item.symbol.replace(/\.TWO?$/, "")}）</option>)}
        </select>
        <IntradayChart symbol={symbol} quote={quote} status={status} />
      </div>
    </dialog>
  );
}
