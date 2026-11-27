"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { parseScreenerParams, type ScreenerState, serializeScreenerParams } from "./screener-model";

/**
 * The whole screen (preset, market, sort, filters, industries, q) lives in the query string,
 * so a screen can be bookmarked or shared and survives reload. `update` takes a transition on
 * the latest state, so several edits in one tick (debounced inputs) compose instead of racing.
 */
export function useScreenerParams() {
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const text = search.toString();
  const state = useMemo(() => parseScreenerParams(new URLSearchParams(text)), [text]);
  const latest = useRef(state);
  useEffect(() => {
    latest.current = state;
  }, [state]);

  const update = useCallback(
    (transition: (current: ScreenerState) => ScreenerState) => {
      const next = transition(latest.current);
      latest.current = next;
      const query = serializeScreenerParams(next).toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router],
  );

  return { state, update };
}
