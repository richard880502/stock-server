"use client";

import { useState } from "react";
import {
  createDebateAnalysis,
  streamLlmAnalysis,
  type MarketCode,
} from "@/lib/api/endpoints";
import type { AnalysisRequest, DebateReport } from "@/lib/api/types";
import { useSyncRetry } from "@/lib/use-sync-retry";

type Query = { symbol: string; benchmark: string; asOf: string; market: MarketCode };

/** Streamed operation guide plus bull/bear debate; both retry on 503 sync_pending. */
export function useLlmAnalysis(
  query: Query,
  backtestJobId: string | null,
  onError: (message: string | null) => void,
) {
  const [streamedGuide, setStreamedGuide] = useState("");
  const [streamModel, setStreamModel] = useState<string | null>(null);
  const [debateReport, setDebateReport] = useState<DebateReport | null>(null);
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
  };

  const runStream = async () => {
    onError(null);
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
      onError(error instanceof Error ? error.message : "LLM 分析失敗");
    }
  };

  const runDebate = async () => {
    onError(null);
    setDebateReport(null);
    const body = requestBody();
    try {
      const result = await debate.run((signal) => createDebateAnalysis(body, { signal }));
      if (!result.aborted) setDebateReport(result.value);
    } catch (error) {
      onError(error instanceof Error ? error.message : "多空辯論分析失敗");
    }
  };

  return {
    streamedGuide,
    streamModel,
    streaming: stream.busy,
    streamSyncWait: stream.wait,
    cancelStreamSync: stream.cancel,
    runStream,
    resetStream,
    debateReport,
    debating: debate.busy,
    debateSyncWait: debate.wait,
    cancelDebateSync: debate.cancel,
    runDebate,
  };
}
