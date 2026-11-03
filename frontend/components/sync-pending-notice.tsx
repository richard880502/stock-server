import type { SyncWait } from "@/lib/sync-retry";

/**
 * Friendly replacement for the red error banner while the backend is fetching
 * history for a symbol (HTTP 503 sync_pending). Retries run automatically.
 */
export function SyncPendingNotice({
  wait,
  onCancel,
}: {
  wait: SyncWait;
  onCancel: () => void;
}) {
  return (
    <div className="sync-notice">
      <span className="sync-spinner" aria-hidden="true" />
      <div className="sync-copy">
        <div role="status">
          <strong>資料同步中{wait.symbol ? `｜${wait.symbol}` : ""}</strong>
          <p>
            {wait.message || "此標的的歷史資料不足，背景程序正在抓取，完成後會自動重新載入。"}
          </p>
        </div>
        <p className="sync-meta">
          第 {wait.attempt} / {wait.maxAttempts} 次嘗試 · {wait.secondsLeft} 秒後自動重試
        </p>
      </div>
      <button type="button" className="sync-cancel" onClick={onCancel}>
        取消
      </button>
    </div>
  );
}
