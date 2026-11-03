import type { SyncWait } from "@/lib/sync-retry";

/**
 * Friendly replacement for the error banner while the backend is fetching
 * history for a symbol (HTTP 503 sync_pending). Retries run automatically.
 */
export function SyncPendingNotice({
  wait,
  onCancel,
}: {
  wait: SyncWait;
  onCancel: () => void;
}) {
  const progress = Math.min(100, (wait.attempt / wait.maxAttempts) * 100);
  return (
    <div className="notice notice-sync">
      <span className="spinner" aria-hidden="true" />
      <div className="notice-body">
        <div role="status">
          <strong>資料同步中{wait.symbol ? `｜${wait.symbol}` : ""}</strong>
          <p>
            {wait.message || "此標的的歷史資料不足，背景程序正在抓取，完成後會自動重新載入。"}
          </p>
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
