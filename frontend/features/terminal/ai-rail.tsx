"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import { SparkIcon } from "@/components/icons";
import { StanceChip } from "@/components/status-chip";
import { SyncPendingNotice } from "@/components/sync-pending-notice";
import { EvidenceColumns, ScenarioRow } from "@/features/llm/judgement-detail";
import { LlmErrorNotice } from "@/features/llm/llm-error";
import type { DebateReport } from "@/lib/api/types";
import { useDashboard } from "@/lib/dashboard-context";
import type { LiveQuote } from "@/lib/live-quotes";
import { aiCache, aiCacheKey, generatedLabel } from "./ai-cache";
import { AiFollowUp } from "./ai-followup";
import { AI_DISCLAIMER, BacktestLink, PriceDrift, SignalSection } from "./ai-parts";
import "./ai-rail.css";

const MAX_RISKS = 3;

/** The few risks worth showing up front: judge warnings, then the risk review's invalidations. */
function topRisks(report: DebateReport): string[] {
  const all = [
    ...report.judgement.risk_warnings,
    ...(report.risk_review?.invalidation_conditions ?? []),
  ];
  return [...new Set(all)].slice(0, MAX_RISKS);
}

/**
 * Right rail of 看盤: the free deterministic signal, then AI text that only runs when the
 * viewer asks (Claude calls cost money), then follow-up questions. No order entry: the app
 * never trades, and every AI suggestion points to the backtest for verification.
 */
export function AiRail({ symbol, quote }: { symbol: string; name: string | null; quote?: LiveQuote }) {
  const { query, signalData, llm, llmStatus } = useDashboard();
  const key = aiCacheKey(symbol, query.asOf);
  const entry = useSyncExternalStore(
    aiCache.subscribe,
    () => aiCache.get(key),
    () => undefined,
  );

  // Prices are recorded when a result finishes; read the latest without re-running effects.
  const latestPrice = useRef<number | null>(null);
  useEffect(() => {
    latestPrice.current = quote?.price ?? null;
  }, [quote?.price]);

  const { streaming, streamedGuide, streamModel, streamError } = llm;
  useEffect(() => {
    if (streaming || streamError || !streamedGuide) return;
    if (aiCache.get(key)?.guide?.text === streamedGuide) return;
    aiCache.saveGuide(key, {
      text: streamedGuide,
      model: streamModel,
      generatedAt: Date.now(),
      price: latestPrice.current,
    });
  }, [key, streaming, streamError, streamedGuide, streamModel]);

  const { debating, debateReport, debateError } = llm;
  useEffect(() => {
    if (debating || debateError || !debateReport) return;
    if (aiCache.get(key)?.debate?.report === debateReport) return;
    aiCache.saveDebate(key, {
      report: debateReport,
      generatedAt: Date.now(),
      price: latestPrice.current,
    });
  }, [key, debating, debateError, debateReport]);

  const signal =
    signalData.signal && signalData.signal.symbol.toUpperCase() === symbol.toUpperCase()
      ? signalData.signal
      : null;
  const dataAsOf = signal?.as_of;

  // LLM output is held by the dashboard only for the current query; otherwise fall back to the cache.
  const guideLive = streaming || Boolean(streamedGuide) || Boolean(llm.streamSyncWait) || Boolean(streamError);
  const guideText = guideLive ? streamedGuide : (entry?.guide?.text ?? "");
  const guideModel = guideLive ? streamModel : (entry?.guide?.model ?? null);
  const guideMeta = !streaming ? entry?.guide : undefined;
  const debateLive = debating || Boolean(debateReport) || Boolean(llm.debateSyncWait) || Boolean(debateError);
  const report = debateLive ? debateReport : (entry?.debate?.report ?? null);
  const debateMeta = !debating ? entry?.debate : undefined;

  const configured = Boolean(llmStatus?.configured);
  const unconfigured = llmStatus !== null && !configured;

  return (
    <aside className="terminal-ai ai-rail" aria-label="AI 交易建議">
      <header className="ai-head">
        <h2>AI 交易建議</h2>
        <span className="ai-model mono">{configured ? (llmStatus?.model ?? "LLM") : "量化訊號"}</span>
      </header>

      <SignalSection
        signal={signal}
        loading={signalData.loading}
        syncing={Boolean(signalData.syncWait)}
        marketEnvironment={signalData.marketEnvironment}
        chipFlow={signalData.chipFlow}
      />

      <section className="ai-section" aria-labelledby="ai-llm-title">
        <h3 id="ai-llm-title" className="ai-section-title">
          AI 判讀
          <small>按下才會呼叫模型</small>
        </h3>

        {unconfigured ? (
          <p className="ai-empty">LLM 尚未設定；量化訊號不受影響。後端設定模型後，這裡會提供 AI 判讀與多空辯論。</p>
        ) : (
          <>
            <div className="ai-actions">
              <button
                type="button"
                className="button small primary"
                onClick={() => void llm.runStream()}
                disabled={!configured || !signal || streaming}
                title={guideText ? "會再次呼叫模型" : undefined}
              >
                <SparkIcon size={14} />
                {streaming ? "產生中…" : guideText ? "重新產生 AI 判讀" : "產生 AI 判讀"}
              </button>
              <button
                type="button"
                className="button small"
                onClick={() => void llm.runDebate()}
                disabled={!configured || !signal || debating}
                title={report ? "會再次呼叫模型" : "多空雙方各自舉證，通常需要數十秒"}
              >
                {debating ? "辯論中…" : report ? "重新辯論" : "多空辯論"}
              </button>
            </div>

            {llm.streamSyncWait ? (
              <SyncPendingNotice wait={llm.streamSyncWait} onCancel={llm.cancelStreamSync} />
            ) : streamError && !streaming ? (
              <LlmErrorNotice error={streamError} onRetry={() => void llm.runStream()} />
            ) : guideText || streaming ? (
              <div className="ai-block" aria-live="polite" aria-busy={streaming}>
                <div className="ai-block-head">
                  <span className={`stream-pulse${streaming ? " on" : ""}`} aria-hidden="true" />
                  <strong>{streaming ? "即時產生中" : "操作指南"}</strong>
                  <small className="mono">{guideModel ?? llmStatus?.model ?? ""}</small>
                </div>
                {guideText ? (
                  <p className={`ai-text stream-text${streaming ? " is-streaming" : ""}`}>{guideText}</p>
                ) : (
                  <div className="ai-skeleton">
                    <span className="skeleton line" />
                    <span className="skeleton line" style={{ width: "80%" }} />
                  </div>
                )}
                {guideMeta && guideText && (
                  <>
                    <p className="ai-note">{generatedLabel(guideMeta.generatedAt)}</p>
                    <PriceDrift generatedPrice={guideMeta.price} livePrice={quote?.price} dataAsOf={dataAsOf} />
                  </>
                )}
                {!streaming && guideText && <BacktestLink />}
              </div>
            ) : null}

            {llm.debateSyncWait ? (
              <SyncPendingNotice wait={llm.debateSyncWait} onCancel={llm.cancelDebateSync} />
            ) : debateError && !debating ? (
              <LlmErrorNotice error={debateError} onRetry={() => void llm.runDebate()} />
            ) : debating ? (
              <div className="ai-block ai-skeleton" role="status" aria-label="多空辯論進行中">
                <span className="skeleton line" style={{ width: "30%" }} />
                <span className="skeleton line" />
                <span className="skeleton line" style={{ width: "85%" }} />
              </div>
            ) : report ? (
              <div className="ai-block">
                <div className="ai-block-head">
                  <strong>多空辯論裁決</strong>
                  <StanceChip stance={report.judgement.stance} />
                </div>
                <p className="ai-headline">{report.judgement.headline}</p>
                <p className="ai-text">{report.judgement.operation_guide}</p>
                {report.data_sufficient === false && (
                  <p className="ai-warn">市場資料為示範資料或不足，請審慎解讀。</p>
                )}
                {topRisks(report).length > 0 && (
                  <>
                    <h4 className="ai-sub">主要風險</h4>
                    <ul className="ai-list">
                      {topRisks(report).map((risk) => (
                        <li key={risk}>{risk}</li>
                      ))}
                    </ul>
                  </>
                )}
                <details className="ai-more">
                  <summary>展開完整辯論</summary>
                  <div className="ai-debate-full">
                    <h4 className="ai-sub">多方論點</h4>
                    <p>{report.bull_case.thesis}</p>
                    <h4 className="ai-sub">空方論點</h4>
                    <p>{report.bear_case.thesis}</p>
                    <EvidenceColumns evidenceLabel="裁決證據" judgement={report.judgement} />
                    <ScenarioRow scenarios={report.judgement.scenarios} />
                    {report.risk_review && (
                      <>
                        <h4 className="ai-sub">風險審查</h4>
                        <p>{report.risk_review.summary}</p>
                      </>
                    )}
                    <p className="ai-note">
                      證據截止日：{report.as_of ?? "未提供"} · 模型 {report.model}
                      {report.judge_model !== report.model ? `／裁決 ${report.judge_model}` : ""}
                    </p>
                  </div>
                </details>
                {debateMeta && (
                  <>
                    <p className="ai-note">{generatedLabel(debateMeta.generatedAt)}</p>
                    <PriceDrift generatedPrice={debateMeta.price} livePrice={quote?.price} dataAsOf={dataAsOf} />
                  </>
                )}
                <BacktestLink />
              </div>
            ) : null}

            {!guideText && !streaming && !report && !debating && !streamError && !debateError && (
              <p className="ai-empty">量化證據已備妥。需要時再產生 AI 判讀或多空辯論。</p>
            )}
          </>
        )}
        <p className="ai-note ai-disclaimer">{AI_DISCLAIMER}</p>
      </section>

      {!unconfigured && (
        <AiFollowUp key={key} cacheKey={key} report={report} messages={entry?.messages ?? []} />
      )}
    </aside>
  );
}
