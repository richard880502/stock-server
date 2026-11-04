"use client";

import { useEffect, useState } from "react";
import { getBackfillStatus } from "@/lib/api/endpoints";
import { isAbortError } from "@/lib/api/errors";
import type { BackfillStatus } from "@/lib/api/types";
import { isActive } from "@/lib/backfill";

/** Same cadence as the market resources: ask again while a backfill is running or queued. */
export const BACKFILL_STATUS_POLL_MS = 15_000;
/** A first load slower than this earns a skeleton; faster ones never flash one. */
const SLOW_MS = 400;

type State = { rows: BackfillStatus[] | null; settled: boolean; slow: boolean };

/**
 * Whole-market backfill status per exchange. `rows` is null until the endpoint has
 * answered once (and stays null if it never does: callers fall back to their old
 * behaviour). It re-polls every 15 s only while some exchange is running or queued,
 * and after a failure keeps showing the last answer, retrying only if that was active.
 */
export function useBackfillStatus(enabled = true): State {
  const [state, setState] = useState<State>({ rows: null, settled: false, slow: false });

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let timer: number | undefined;
    let slowTimer: number | undefined = window.setTimeout(
      () => setState((s) => (s.settled ? s : { ...s, slow: true })),
      SLOW_MS,
    );
    let active = false;
    const run = async () => {
      try {
        const rows = await getBackfillStatus({ signal: controller.signal });
        if (!Array.isArray(rows)) throw new Error("unexpected backfill-status payload");
        active = rows.some(isActive);
        setState({ rows, settled: true, slow: false });
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState((s) => ({ ...s, settled: true, slow: false }));
      }
      window.clearTimeout(slowTimer);
      if (active) timer = window.setTimeout(run, BACKFILL_STATUS_POLL_MS);
    };
    void run();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      window.clearTimeout(slowTimer);
      slowTimer = undefined;
    };
  }, [enabled]);

  return state;
}
