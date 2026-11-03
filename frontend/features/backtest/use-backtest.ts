"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { createBacktest, getBacktest, type MarketCode } from "@/lib/api/endpoints";
import { isAbortError } from "@/lib/api/errors";
import type { BacktestJob } from "@/lib/api/types";

export type BacktestForm = {
  start: string;
  feeBps: number;
  slippageBps: number;
  sellTaxBps: number;
  initialCapital: number;
  entryScore: number;
  exitScore: number;
  useMarketFilter: boolean;
  useChipFilter: boolean;
};

const initialForm: BacktestForm = {
  start: "2025-01-01",
  feeBps: 5,
  slippageBps: 5,
  sellTaxBps: 0,
  initialCapital: 1_000_000,
  entryScore: 70,
  exitScore: 45,
  useMarketFilter: false,
  useChipFilter: false,
};

function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

export function useBacktest(
  query: { symbol: string; benchmark: string; asOf: string; market: MarketCode },
  onError: (message: string | null) => void,
) {
  const [form, setForm] = useState<BacktestForm>(initialForm);
  const [job, setJob] = useState<BacktestJob | null>(null);
  const [loading, setLoading] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const updateForm = useCallback(
    (patch: Partial<BacktestForm>) => setForm((current) => ({ ...current, ...patch })),
    [],
  );

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setJob(null);
    setLoading(false);
  }, []);

  useEffect(() => reset, [reset]);

  const poll = async (jobId: string, signal: AbortSignal) => {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const next = await getBacktest(jobId, { signal });
      setJob(next);
      if (next.status === "failed") throw new Error(next.error ?? "回測失敗");
      if (next.status === "completed") return;
      await wait(1_000, signal);
    }
    throw new Error("回測仍在執行，請稍後再查詢");
  };

  const run = async (event?: FormEvent) => {
    event?.preventDefault();
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setJob(null);
    onError(null);
    try {
      const created = await createBacktest(
        {
          symbol: query.symbol.trim(),
          benchmark: query.benchmark.trim() || null,
          market: form.useMarketFilter ? query.market : null,
          strategy_version: "regime_v1",
          start: form.start,
          end: query.asOf,
          entry_score: form.entryScore,
          exit_score: form.exitScore,
          fee_bps: form.feeBps,
          slippage_bps: form.slippageBps,
          sell_tax_bps: form.sellTaxBps,
          initial_capital: form.initialCapital,
          use_chip_filter: form.useChipFilter,
          chip_entry_score: 55,
          chip_exit_score: 40,
        },
        { signal: controller.signal },
      );
      setJob(created);
      await poll(created.id, controller.signal);
    } catch (error) {
      if (isAbortError(error)) return;
      onError(error instanceof Error ? error.message : "無法完成回測");
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setLoading(false);
      }
    }
  };

  return { form, updateForm, job, loading, run, reset };
}
