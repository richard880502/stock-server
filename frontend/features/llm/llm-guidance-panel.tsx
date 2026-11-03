"use client";

import { SyncPendingNotice } from "@/components/sync-pending-notice";
import { useDashboard } from "@/lib/dashboard-context";
import { LlmPanelHeader } from "./llm-panel-header";

export function LlmGuidancePanel() {
  const { signalData, llmStatus, backtest, llm } = useDashboard();
  const { streamedGuide, streamModel, streaming, streamSyncWait } = llm;

  return (
    <section className="llm-panel">
      <LlmPanelHeader
        eyebrow="LLM ANALYST"
        title="讓模型解讀證據，而不是發明證據。"
        llmStatus={llmStatus}
        action={
          <button
            type="button"
            className="secondary-button"
            onClick={llm.runStream}
            disabled={!signalData.signal || streaming || !llmStatus?.configured}
          >
            {streaming ? "正在產生…" : "串流操作指南"}
          </button>
        }
      />

      {streamSyncWait ? (
        <SyncPendingNotice wait={streamSyncWait} onCancel={llm.cancelStreamSync} />
      ) : streamedGuide || streaming ? (
        <div className="llm-stream-result" aria-live="polite">
          <div className="stream-heading">
            <span className="stream-pulse" aria-hidden="true" />
            <span>即時操作指南</span>
            <small>{streamModel ?? llmStatus?.model ?? "準備分析上下文"}</small>
          </div>
          <p className={streaming ? "is-streaming" : ""}>
            {streamedGuide || "正在整合量化證據…"}
          </p>
        </div>
      ) : (
        <div className="llm-empty">
          <div className="prompt-preview">
            <span>分析上下文</span>
            <code>個股訊號</code>
            <b aria-hidden="true">＋</b>
            <code>市場寬度與資金</code>
            <b aria-hidden="true">＋</b>
            <code>法人與分點籌碼</code>
            <b aria-hidden="true">＋</b>
            <code>異常證據</code>
            <b aria-hidden="true">＋</b>
            <code>{backtest.job?.status === "completed" ? "已完成回測" : "回測（選配）"}</code>
          </div>
          <p>
            {llmStatus?.configured
              ? "量化證據已準備完成，可交由模型產生條件式操作指南。"
              : "在後端設定 LLM 後，這裡會產生繁體中文的情境分析與風險提醒。"}
          </p>
        </div>
      )}
    </section>
  );
}
