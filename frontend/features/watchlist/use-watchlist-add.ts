"use client";

import { useCallback, useState } from "react";
import type { WatchlistToggleResult } from "@/lib/use-watchlist";

export type AddNote = { ok: boolean; text: string } | null;

/** The "pick a stock in the search box -> add it to the watchlist" flow shared by the list and the 看盤 rail. */
export function useWatchlistAdd(setMember: (symbol: string, member: boolean) => Promise<WatchlistToggleResult>) {
  const [note, setNote] = useState<AddNote>(null);
  const add = useCallback(
    async (symbol: string, name?: string) => {
      setNote(null);
      const result = await setMember(symbol, true);
      const code = symbol.replace(/\.TWO?$/, "");
      setNote(
        result.ok
          ? { ok: true, text: `已加入 ${name ? `${name}（${code}）` : symbol}` }
          : { ok: false, text: result.message },
      );
    },
    [setMember],
  );
  return { add, note };
}
