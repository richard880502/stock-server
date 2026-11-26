"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useState, useSyncExternalStore } from "react";
import { SegmentedControl } from "@/components/segmented-control";
import { TerminalView } from "@/features/terminal/terminal-view";
import { useDashboard } from "@/lib/dashboard-context";
import { readStorage, writeStorage } from "@/lib/safe-storage";
import { VIEW_KEY, type WatchView, resolveView } from "./view-mode";
import { WatchlistView } from "./watchlist-view";

const OPTIONS: { value: WatchView; label: string }[] = [
  { value: "terminal", label: "看盤" },
  { value: "list", label: "列表" },
];

const storageListeners = new Set<() => void>();
function subscribeStored(listener: () => void) {
  storageListeners.add(listener);
  return () => {
    storageListeners.delete(listener);
  };
}

const NARROW_QUERY = "(max-width: 759.98px)";
function subscribeNarrow(listener: () => void) {
  const media = window.matchMedia(NARROW_QUERY);
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}

/**
 * 自選股: one page, two views of the same watchlist. 看盤 = the three-column terminal,
 * 列表 = the research table. Only the active view is mounted, so only one live-quote
 * stream is open.
 */
export function WatchlistPage() {
  const search = useSearchParams();
  const { refresh } = useDashboard();
  // null on the server: the default depends on the screen, so wait for the client
  // rather than flash the wrong view (and open its quote stream) on phones.
  const narrow = useSyncExternalStore<boolean | null>(
    subscribeNarrow,
    () => window.matchMedia(NARROW_QUERY).matches,
    () => null,
  );
  const stored = useSyncExternalStore(subscribeStored, () => readStorage(VIEW_KEY), () => null);
  const [clicked, setClicked] = useState<WatchView | null>(null);

  const choose = useCallback((next: WatchView) => {
    setClicked(next);
    writeStorage(VIEW_KEY, next);
    storageListeners.forEach((listener) => listener());
  }, []);
  // "Open in 看盤" is a one-off jump, not a change of the viewer's preferred view.
  const openInTerminal = useCallback(
    (symbol: string) => {
      refresh({ symbol });
      setClicked("terminal");
    },
    [refresh],
  );

  if (narrow === null) {
    return (
      <div className="view-heading watch-heading">
        <h1>自選股</h1>
      </div>
    );
  }
  const view = resolveView({ clicked, param: search?.get("view") ?? null, stored, narrow });
  const switcher = <SegmentedControl label="自選股檢視" value={view} options={OPTIONS} onChange={choose} />;
  return view === "terminal" ? (
    <TerminalView switcher={switcher} />
  ) : (
    <WatchlistView switcher={switcher} onOpenTerminal={openInTerminal} />
  );
}
