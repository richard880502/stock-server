import { InfoIcon } from "@/components/icons";
import type { FullAnalysisState } from "@/lib/api/types";
import type { SyncWait } from "@/lib/sync-retry";

/**
 * Where the full analysis (signal, backtest, forecast) stands for a stock whose
 * quick view is on screen. While the signal request is retrying 503 sync_pending
 * the countdown from useSyncRetry is shown; when the retry succeeds the parent
 * swaps this whole panel for the normal signal view.
 */
export function AnalysisStatus({
  state,
  wait,
  loading,
  onCancel,
  onReload,
}: {
  /** From the overview; null while it is loading or failed. */
  state: FullAnalysisState | null;
  wait: SyncWait | null;
  /** A signal request is in flight. */
  loading: boolean;
  onCancel: () => void;
  onReload: () => void;
}) {
  if (wait) {
    const progress = Math.min(100, (wait.attempt / wait.maxAttempts) * 100);
    return (
      <div className="notice notice-sync">
        <span className="spinner" aria-hidden="true" />
        <div className="notice-body">
          <div role="status">
            <strong>完整分析（訊號、回測、預測）準備中，完成後會自動顯示</strong>
            <p>下方先提供行情、法人與融資融券快照；不需要重新整理頁面。</p>
          </div>
          <div className="sync-progress">
            <div
              className="sync-bar"
              role="progressbar"
              aria-label="自動重試進度"
              aria-valuemin={0}
              aria-valuemax={wait.maxAttempts}
              aria-valuenow={wait.attempt}
            >
              <span style={{ width: `${progress}%` }} />
            </div>
            <p className="sync-meta num">
              第 {wait.attempt} / {wait.maxAttempts} 次嘗試 · {wait.secondsLeft} 秒後自動重試
            </p>
          </div>
        </div>
        <div className="notice-actions">
          <button type="button" className="button small" onClick={onCancel}>
            取消
          </button>
        </div>
      </div>
    );
  }

  if (state === "insufficient") {
    return (
      <div className="notice">
        <InfoIcon className="notice-icon" size={20} />
        <div className="notice-body" role="status">
          <strong>上市時間不足，暫無法計算訊號</strong>
          <p>
            這檔股票的歷史交易日還不夠，訊號、回測與預測都需要更長的資料。下方的行情與籌碼快照仍可參考，累積足夠資料後即可使用完整分析。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="notice">
      <InfoIcon className="notice-icon" size={20} />
      <div className="notice-body" role="status">
        <strong>{state === "ready" ? "完整分析尚未顯示" : "完整分析尚未載入"}</strong>
        <p>
          {state === "ready"
            ? "這檔股票的完整分析資料已就緒；若上方有錯誤訊息，請依訊息調整資料截止日後重新載入。"
            : "自動等待已結束，背景可能仍在準備中。稍後再按一次重新載入。"}
        </p>
      </div>
      <div className="notice-actions">
        <button type="button" className="button small" disabled={loading} onClick={onReload}>
          {loading && <span className="spinner small" aria-hidden="true" />}
          重新載入
        </button>
      </div>
    </div>
  );
}
