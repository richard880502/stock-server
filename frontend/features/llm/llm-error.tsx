import { AlertIcon } from "@/components/icons";
import { RateLimitNotice } from "@/components/rate-limit-notice";
import { rateLimitInfo } from "@/lib/api/rate-limit";
import type { LlmError } from "./use-llm-analysis";

/** Upstream model failures are common (cold start, overload); say so plainly. */
export function LlmErrorNotice({ error, onRetry }: { error: LlmError; onRetry?: () => void }) {
  const limit = error.status === 429 ? rateLimitInfo(error.message) : null;
  if (limit) {
    return <RateLimitNotice message={error.message} retryAfterSeconds={limit.retryAfterSeconds} />;
  }
  const upstream = error.status !== null && error.status >= 500;
  return (
    <div className="notice notice-error" role="alert">
      <AlertIcon className="notice-icon" size={20} />
      <div className="notice-body">
        <strong>{upstream ? "模型服務暫時無法回應" : "這次分析沒有完成"}</strong>
        <p>
          {upstream
            ? "上游 LLM 服務回報錯誤（可能是暫時過載或啟動中）。量化訊號與其他區塊不受影響，稍後再試一次即可。"
            : error.message}
        </p>
        {upstream && (
          <details className="notice-detail">
            <summary>技術細節</summary>
            <p className="mono">{error.message}</p>
          </details>
        )}
      </div>
      {onRetry && (
        <div className="notice-actions">
          <button type="button" className="button small" onClick={onRetry}>
            再試一次
          </button>
        </div>
      )}
    </div>
  );
}
