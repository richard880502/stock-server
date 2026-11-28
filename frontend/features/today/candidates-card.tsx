"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Card } from "@/components/card";
import { Direction } from "@/components/direction";
import { SegmentedControl } from "@/components/segmented-control";
import { getScreener } from "@/lib/api/endpoints";
import type { ScreenerMeta, ScreenerResponse } from "@/lib/api/types";
import { stockHref } from "@/lib/stock-routes";
import { usePolled } from "@/features/paper/use-paper-data";
import {
  buildScreenerQuery,
  codeOf,
  formatScore,
  INITIAL_STATE,
  resolveEffective,
  withPage,
} from "@/features/screener/screener-model";
import { useScreenerMeta } from "@/features/screener/use-screener-meta";
import {
  CANDIDATE_DISCLAIMER,
  CANDIDATE_ROWS,
  candidatePresets,
  screenerHref,
  supportingMetric,
} from "./today-model";
import { CardError, CardSkeleton, MoreLink } from "./today-parts";

export function CandidateRows({
  data,
  presetKey,
  meta,
}: {
  data: ScreenerResponse;
  presetKey: string;
  meta: ScreenerMeta;
}) {
  const entries = (data.entries ?? []).slice(0, CANDIDATE_ROWS);
  if (data.snapshot_pending && entries.length === 0 && !data.notice) {
    return <p className="note">今日的篩選資料計算中，稍後會自動更新。</p>;
  }
  return (
    <>
      {data.notice && <p className="callout today-notice">{data.notice}</p>}
      {entries.length === 0 ? (
        <p className="note">目前沒有符合這個條件的股票。</p>
      ) : (
        <ol className="today-rows">
          {entries.map((entry) => {
            const support = supportingMetric(entry, presetKey, meta);
            return (
              <li key={entry.symbol}>
                <Link href={stockHref(entry.symbol)} className="today-row today-cand" aria-label={`${entry.name}（${codeOf(entry.symbol)}）個股頁`}>
                  <span className="today-row-id">
                    <span className="today-code mono">{codeOf(entry.symbol)}</span>
                    <span className="today-name">{entry.name}</span>
                  </span>
                  <span className="today-score-cell num" title="綜合分數">
                    {formatScore(entry.composite)}
                  </span>
                  <span className="today-support num">
                    {support ? (
                      <>
                        <span className="today-support-label">{support.label}</span>
                        {support.tone === "flat" ? (
                          <span>{support.text}</span>
                        ) : (
                          <Direction value={support.tone === "up" ? 1 : -1}>{support.text}</Direction>
                        )}
                      </>
                    ) : null}
                  </span>
                </Link>
              </li>
            );
          })}
        </ol>
      )}
    </>
  );
}

function CandidatesLoaded({ meta }: { meta: ScreenerMeta }) {
  const presets = useMemo(() => candidatePresets(meta), [meta]);
  const [chosen, setChosen] = useState<string | null>(null);
  const presetKey = presets.find((p) => p.key === chosen)?.key ?? presets[0]?.key ?? null;
  const preset = presets.find((p) => p.key === presetKey) ?? null;

  const query = useMemo(() => {
    if (!presetKey) return null;
    const state = { ...INITIAL_STATE, preset: presetKey };
    return withPage(buildScreenerQuery(state, resolveEffective(state, meta)), CANDIDATE_ROWS, 0);
  }, [presetKey, meta]);

  const resource = usePolled(query ? `today-screener|${query}` : null, (signal) =>
    getScreener(query as URLSearchParams, { signal }),
  );
  const data = resource.stale ? null : resource.data;

  if (!presetKey) {
    return <p className="note">目前沒有可用的選股策略。</p>;
  }
  return (
    <>
      <SegmentedControl
        label="候選股策略"
        value={presetKey}
        options={presets.map((p) => ({ value: p.key, label: p.label }))}
        onChange={setChosen}
        wrap
      />
      <p className="today-meta today-preset-desc">{preset?.description}</p>
      <div className="today-body today-cands-body">
        {resource.error ? (
          <CardError message={resource.error.message} onRetry={resource.reload} />
        ) : !data ? (
          <CardSkeleton label="今日候選載入中" lines={5} />
        ) : (
          <CandidateRows data={data} presetKey={presetKey} meta={meta} />
        )}
      </div>
      {data && (
        <p className="today-meta">
          {data.data_through ? `資料至 ${data.data_through} · ` : ""}
          {CANDIDATE_DISCLAIMER}
        </p>
      )}
      <MoreLink href={screenerHref(presetKey)}>到選股看全部 →</MoreLink>
    </>
  );
}

export function CandidatesCard() {
  const { meta, error, retry } = useScreenerMeta();
  return (
    <Card title="今日候選" sub="多因子選股的前五名" className="today-card today-cands">
      {error && !meta ? (
        <CardError message={error} onRetry={retry} />
      ) : !meta ? (
        <CardSkeleton label="選股策略載入中" lines={5} />
      ) : (
        <CandidatesLoaded meta={meta} />
      )}
    </Card>
  );
}
