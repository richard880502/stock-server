"use client";

import { useState } from "react";
import {
  createDebateAnalysis,
  streamLlmAnalysis,
  type MarketCode,
} from "@/lib/api/endpoints";
import { ApiError } from "@/lib/api/errors";
import type { AnalysisRequest, DebateReport } from "@/lib/api/types";
import { useSyncRetry } from "@/lib/use-sync-retry";

type Query = { symbol: string; benchmark: string; asOf: string; market: MarketCode };

export type LlmError = { message: string; status: number | null };

const toLlmError = (error: unknown, fallback: string): LlmError => ({
  message: error instanceof Error ? error.message : fallback,
  status: error instanceof ApiError ? error.status : null,
});

/**
 * Streamed operation guide plus bull/bear debate; both retry on 503 sync_pending.
 * Failures stay local (`streamError` / `debateError`) so the panel can show them
 * next to the button instead of in the page-level banner.
 */
export function useLlmAnalysis(query: Query, backtestJobId: string | null) {
  const [streamedGuide, setStreamedGuide] = useState("");
  const [streamModel, setStreamModel] = useState<string | null>(null);
  const [debateReport, setDebateReport] = useState<DebateReport | null>(null);
  const [streamError, setStreamError] = useState<LlmError | null>(null);
  const [debateError, setDebateError] = useState<LlmError | null>(null);
  const stream = useSyncRetry();
  const debate = useSyncRetry();

  const requestBody = (): AnalysisRequest => ({
    symbol: query.symbol.trim(),
    as_of: query.asOf,
    market: query.market,
    benchmark: query.benchmark.trim() || null,
    strategy_version: "regime_v1",
    backtest_job_id: backtestJobId,
  });

  const resetStream = () => {
    stream.cancel();
    setStreamedGuide("");
    setStreamModel(null);
    setStreamError(null);
  };

  const runStream = async () => {
    setStreamError(null);
    setStreamedGuide("");
    setStreamModel(null);
    const body = requestBody();
    try {
      await stream.run((signal) => {
        // A retried attempt starts from an empty guide.
        setStreamedGuide("");
        setStreamModel(null);
        return streamLlmAnalysis(
          body,
          {
            onMeta: setStreamModel,
            onDelta: (text) => setStreamedGuide((current) => current + text),
          },
          { signal },
        );
      });
    } catch (error) {
      setStreamError(toLlmError(error, "LLM 分析失敗"));
    }
  };

  const runDebate = async () => {
    setDebateError(null);
    setDebateReport(null);
    const body = requestBody();
    try {
      const result = await debate.run((signal) => createDebateAnalysis(body, { signal }));
      if (!result.aborted) setDebateReport(result.value);
    } catch (error) {
      setDebateError(toLlmError(error, "多空辯論分析失敗"));
    }
  };

  return {
    streamedGuide,
    streamModel,
    streaming: stream.busy,
    streamSyncWait: stream.wait,
    cancelStreamSync: stream.cancel,
    streamError,
    runStream,
    resetStream,
    debateReport,
    debateError,
    debating: debate.busy,
    debateSyncWait: debate.wait,
    cancelDebateSync: debate.cancel,
    runDebate,
  };
}
