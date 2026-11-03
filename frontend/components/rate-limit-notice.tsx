import { formatWait } from "@/lib/api/rate-limit";
import { ClockIcon, XIcon } from "./icons";

/**
 * Calm replacement for the error banner when the daily quota for an expensive
 * action is used up (HTTP 429). Informational: nothing is retried automatically.
 */
export function RateLimitNotice({
  message,
  retryAfterSeconds,
  onDismiss,
}: {
  message: string;
  retryAfterSeconds: number | null;
  onDismiss?: () => void;
}) {
  return (
    <div className="notice notice-limit" role="status">
      <ClockIcon className="notice-icon" size={20} />
      <div className="notice-body">
        <strong>今日使用額度已用完</strong>
        <p>{message}</p>
        {retryAfterSeconds !== null && (
          <p className="notice-detail">約 {formatWait(retryAfterSeconds)}後可再使用。其他功能不受影響。</p>
        )}
      </div>
      {onDismiss && (
        <div className="notice-actions">
          <button
            type="button"
            className="icon-button small"
            onClick={onDismiss}
            aria-label="關閉提示"
          >
            <XIcon size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
