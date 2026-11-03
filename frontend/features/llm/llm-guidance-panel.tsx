"use client";

import { EmptyState } from "@/components/empty-state";
import { SparkIcon } from "@/components/icons";
import { SyncPendingNotice } from "@/components/sync-pending-notice";
import { useDashboard } from "@/lib/dashboard-context";
import { LlmErrorNotice } from "./llm-error";

const CONTEXT_ITEMS = ["個股訊號", "市場寬度與資金", "法人與分點籌碼", "異常證據"];

export function LlmGuidancePanel() {
  const { signalData, llmStatus, backtest, llm } = useDashboard();
  const { streamedGuide, streamModel, streaming, streamSyncWait } = llm;
  const ready = Boolean(signalData.signal) && Boolean(llmStatus?.configured);

  return (
    <div className="llm-body">
      <div className="llm-toolbar">
        <p className="note">
          依據{CONTEXT_ITEMS.join("、")}
          {backtest.job?.status === "completed" ? "與已完成的回測" : "（可選：回測結果）"}
          ，產生條件式的操作框架。
        </p>
        <button
          type="button"
          className="button primary"
          onClick={llm.runStream}
          disabled={!ready || streaming}
        >
          {streaming ? "正在產生…" : streamedGuide ? "重新產生" : "產生操作指南"}
        </button>
      </div>

      {llm.streamError && !streaming && (
        <LlmErrorNotice error={llm.streamError} onRetry={llm.runStream} />
      )}

      {streamSyncWait ? (
        <SyncPendingNotice wait={streamSyncWait} onCancel={llm.cancelStreamSync} />
      ) : streamedGuide || streaming ? (
        <div className="llm-stream-result" aria-live="polite" aria-busy={streaming}>
          <div className="stream-meta">
            <span className={`stream-pulse${streaming ? " on" : ""}`} aria-hidden="true" />
            <span>{streaming ? "即時產生中" : "操作指南"}</span>
            <small className="mono">{streamModel ?? llmStatus?.model ?? "準備分析上下文"}</small>
          </div>
          {streamedGuide ? (
            <p className={`stream-text${streaming ? " is-streaming" : ""}`}>{streamedGuide}</p>
          ) : (
            <div className="stream-wait">
              <span className="skeleton line" />
              <span className="skeleton line" style={{ width: "85%" }} />
              <span className="skeleton line" style={{ width: "60%" }} />
            </div>
          )}
        </div>
      ) : llm.streamError ? null : (
        <EmptyState icon={<SparkIcon size={22} />} title={llmStatus?.configured ? "尚未產生操作指南" : "LLM 尚未設定"}>
          {llmStatus?.configured
            ? "量化證據已備妥。按下「產生操作指南」，模型會用繁體中文整理成條件式的情境與風險提醒。"
            : "在後端設定 LLM 後，這裡會產生繁體中文的情境分析與風險提醒；量化訊號不受影響。"}
        </EmptyState>
      )}
    </div>
  );
}
