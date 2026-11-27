"use client";

import { useCallback, useEffect, useState } from "react";
import { getScreenerMeta } from "@/lib/api/endpoints";
import { isAbortError } from "@/lib/api/errors";
import type { ScreenerMeta } from "@/lib/api/types";

// The catalogue only changes with a backend deploy: fetch it once per page load.
let cached: ScreenerMeta | null = null;

export function useScreenerMeta() {
  const [meta, setMeta] = useState<ScreenerMeta | null>(cached);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (cached) return;
    const controller = new AbortController();
    getScreenerMeta({ signal: controller.signal })
      .then((data) => {
        cached = data;
        setMeta(data);
        setError(null);
      })
      .catch((cause) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setError(cause instanceof Error ? cause.message : "無法取得選股設定");
      });
    return () => controller.abort();
  }, [attempt]);

  const retry = useCallback(() => {
    setError(null);
    setAttempt((n) => n + 1);
  }, []);
  return { meta, error, retry };
}
