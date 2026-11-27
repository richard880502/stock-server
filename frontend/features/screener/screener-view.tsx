"use client";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import { EmptyState } from "@/components/empty-state";
import { ErrorBanner } from "@/components/error-banner";
import { AlertIcon, ArrowDownIcon, ArrowUpIcon, InfoIcon, SearchIcon } from "@/components/icons";
import { getScreener } from "@/lib/api/endpoints";
import { useDashboard } from "@/lib/dashboard-context";
import { formatLots } from "@/lib/format";
import { useCommittedAsOf } from "@/features/market/use-committed-as-of";
import { FilterBar } from "./filter-bar";
import { PresetRow } from "./preset-row";
import { ResultCards, ResultsSkeleton, ResultsTable, usePhoneLayout } from "./results";
import {
  applySort,
  buildScreenerQuery,
  clearFilters,
  type Effective,
  isNumberMetric,
  isPending,
  PAGE_SIZE,
  resolveColumn,
  resolveEffective,
  resultDates,
  type ScreenerState,
  selectPreset,
  toggleSort,
  visibleColumns,
  withPage,
} from "./screener-model";
import { useScreenerMeta } from "./use-screener-meta";
import { useScreenerParams } from "./use-screener-params";
import { useScreenerResults } from "./use-screener-results";

export function ScreenerView() {
  const { query, refresh } = useDashboard();
  const router = useRouter();
  const { state, update } = useScreenerParams();
  const { meta, error: metaError, retry: retryMeta } = useScreenerMeta();
  const asOf = useCommittedAsOf(query.asOf);
  const phone = usePhoneLayout();

  const effective = useMemo(() => resolveEffective(state, meta), [state, meta]);
  const base = useMemo(
    () => (meta ? buildScreenerQuery(state, effective, { asOf: asOf || null }) : null),
    [meta, state, effective, asOf],
  );
  const results = useScreenerResults(base ? base.toString() : null, (offset, signal) =>
    getScreener(withPage(base ?? new URLSearchParams(), PAGE_SIZE, offset), { signal }),
  );

  const edit = useCallback(
    (change: (current: ScreenerState, effective: Effective) => ScreenerState) =>
      update((current) => change(current, resolveEffective(current, meta))),
    [update, meta],
  );

  const open = useCallback(
    (symbol: string, to: string) => {
      refresh({ symbol });
      router.push(to);
    },
    [refresh, router],
  );
  const onSignal = useCallback((symbol: string) => open(symbol, "/"), [open]);
  const onBacktest = useCallback((symbol: string) => open(symbol, "/backtest"), [open]);

  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const onToggle = useCallback((symbol: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (!next.delete(symbol)) next.add(symbol);
      return next;
    });
  }, []);

  const onSort = useCallback(
    (key: string, higherIsBetter: boolean | null) =>
      edit((current, eff) => {
        const next = toggleSort({ sort: eff.sort, order: eff.order }, key, higherIsBetter);
        return applySort(current, next.sort, next.order);
      }),
    [edit],
  );

  const [dismissed, setDismissed] = useState<string | null>(null);
  const error = metaError ?? results.error;
  const retry = () => {
    if (metaError) retryMeta();
    if (results.error) results.retry();
  };

  const columns = useMemo(
    () => visibleColumns(effective).map((key) => resolveColumn(meta, key)),
    [effective, meta],
  );
  const data = results.data;
  const entries = results.entries;
  const pending = isPending(data);
  const dates = data ? resultDates(data) : null;
  const notice = data?.notice ?? null;
  const empty = data !== null && entries.length === 0 && !pending && !results.loading;
  const refreshing = results.stale || (results.loading && entries.length > 0);

  return (
    <div className="sc-page">
      <div className="view-heading">
        <h1>選股</h1>
        <p>
          <span className="sc-sub-long">從全市場約 2,000 檔裡，用量化因子與資金流向找出值得進一步研究的股票。排名是計算結果，不是買賣建議。</span>
          <span className="sc-sub-short">用量化因子與資金流向篩出值得研究的股票。排名不是買賣建議。</span>
        </p>
      </div>

      {meta && (
        <PresetRow
          presets={meta.presets}
          selected={effective.preset?.key ?? null}
          onSelect={(key) => edit((current) => selectPreset(current, key))}
        />
      )}
      {meta && <FilterBar meta={meta} state={state} effective={effective} edit={edit} />}

      {error && error !== dismissed && (
        <ErrorBanner message={error} onDismiss={() => setDismissed(error)} onRetry={retry} />
      )}

      {pending && (
        <div className="notice notice-sync" role="status">
          <span className="spinner" aria-hidden="true" />
          <div className="notice-body">
            <strong>選股資料正在計算，通常幾分鐘內完成</strong>
            <p>頁面每 30 秒會自動更新，不需要重新整理。</p>
          </div>
        </div>
      )}

      <section className="card sc-results" aria-label="選股結果" aria-live="polite">
        {data && !pending && (
          <header className="sc-results-head">
            <p className="sc-summary num">
              符合 <strong>{formatLots(data.matched)}</strong> 檔 / 全市場 {formatLots(data.universe)} 檔
              {dates?.dataThrough && <> · 資料截止 {dates.dataThrough}</>}
              {dates?.flowThrough && <> · 籌碼資料至 {dates.flowThrough}</>}
            </p>
            {phone && meta && (
              <div className="sc-sort-select">
                <label className="sr-only" htmlFor="sc-sort">排序依據</label>
                <select id="sc-sort" className="input" value={effective.sort} onChange={(event) => onSort(event.target.value, null)}>
                  {["composite", ...meta.factors.map((f) => f.key), ...meta.metrics.filter((m) => isNumberMetric(m)).map((m) => m.key)]
                    .filter((key, i, all) => all.indexOf(key) === i)
                    .map((key) => (
                      <option key={key} value={key}>{resolveColumn(meta, key).label}</option>
                    ))}
                </select>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={effective.order === "desc" ? "目前由高到低，切換為由低到高" : "目前由低到高，切換為由高到低"}
                  onClick={() => onSort(effective.sort, null)}
                >
                  {effective.order === "desc" ? <ArrowDownIcon size={16} /> : <ArrowUpIcon size={16} />}
                </button>
              </div>
            )}
          </header>
        )}
        {notice && (
          <p className="callout info" role="status">
            <InfoIcon size={16} />
            <span>{notice}</span>
          </p>
        )}
        {data && dates?.lagging && !pending && (
          <p className="callout info" role="status">
            <InfoIcon size={16} />
            <span>
              {dates.dataThrough} 的三大法人買賣超尚未公布，資金流向相關欄位目前計算到 {dates.flowThrough}。交易所通常在收盤後約 17:00 才公布，公布後重新整理即可納入。
            </span>
          </p>
        )}

        {!meta || (results.loading && entries.length === 0 && !pending && !error) ? (
          <ResultsSkeleton />
        ) : empty ? (
          <EmptyState
            icon={<SearchIcon size={22} />}
            title="沒有符合條件的股票"
            action={
              <button type="button" className="button" onClick={() => edit(clearFilters)}>
                放寬條件（清除全部篩選）
              </button>
            }
          >
            目前的篩選太嚴格，或這個日期沒有資料。可以清除部分條件，或換一個策略快選再看看。
          </EmptyState>
        ) : entries.length > 0 && meta ? (
          <>
            {phone ? (
              <ResultCards entries={entries} meta={meta} columns={columns} sort={effective.sort} order={effective.order} expanded={expanded} refreshing={refreshing} onToggle={onToggle} onSort={onSort} onSignal={onSignal} onBacktest={onBacktest} />
            ) : (
              <ResultsTable entries={entries} meta={meta} columns={columns} sort={effective.sort} order={effective.order} expanded={expanded} refreshing={refreshing} onToggle={onToggle} onSort={onSort} onSignal={onSignal} onBacktest={onBacktest} />
            )}
            <div className="sc-more">
              {results.moreError && (
                <p className="inline-error" role="alert">
                  <AlertIcon size={14} /> {results.moreError}
                </p>
              )}
              {results.hasMore ? (
                <button type="button" className="button" disabled={results.loadingMore || refreshing} onClick={() => void results.loadMore()}>
                  {results.loadingMore ? "載入中…" : `載入更多（已顯示 ${entries.length} / ${data ? formatLots(data.matched) : entries.length} 檔）`}
                </button>
              ) : (
                <p className="note">已顯示全部 {entries.length} 檔。</p>
              )}
            </div>
          </>
        ) : null}
      </section>

      <footer className="market-foot" aria-label="資料說明">
        <p>
          分數是各因子在全市場的百分位排名（0–100），每天收盤後依官方公開資料重新計算，不是即時行情；缺資料的欄位顯示「—」，不會當作 0。
        </p>
        <p>排名與亮點是量化研究參考，不構成投資建議。三大法人是公開的法人合計，並非個別券商分點。</p>
      </footer>
    </div>
  );
}
