import { rateLimitInfo } from "@/lib/api/rate-limit";
import { AlertIcon, XIcon } from "./icons";
import { RateLimitNotice } from "./rate-limit-notice";

/** Translate the common raw failures (network, short history) into plain Traditional Chinese. */
function friendlyMessage(message: string): { text: string; detail?: string } {
  if (/failed to fetch|load failed|networkerror|network request failed/i.test(message)) {
    return {
      text: "無法連線到量化服務。請確認服務已啟動、網路正常，稍後再試一次。",
      detail: message,
    };
  }
  const required = message.match(/at least (\d+) point-in-time/i);
  if (required) {
    return {
      text: `截止日之前的歷史資料不足（至少需要 ${required[1]} 個交易日）。請選擇較晚的資料截止日，或先同步更多歷史資料。`,
      detail: message,
    };
  }
  return { text: message };
}

export function ErrorBanner({
  message,
  onDismiss,
  onRetry,
}: {
  message: string;
  onDismiss: () => void;
  onRetry?: () => void;
}) {
  const limit = rateLimitInfo(message);
  if (limit) {
    return (
      <RateLimitNotice
        message={message}
        retryAfterSeconds={limit.retryAfterSeconds}
        onDismiss={onDismiss}
      />
    );
  }
  const { text, detail } = friendlyMessage(message);
  return (
    <div className="notice notice-error" role="alert">
      <AlertIcon className="notice-icon" size={20} />
      <div className="notice-body">
        <strong>目前無法完成這項操作</strong>
        <p>{text}</p>
        {detail && <p className="notice-detail mono">{detail}</p>}
      </div>
      <div className="notice-actions">
        {onRetry && (
          <button type="button" className="button small" onClick={onRetry}>
            重試
          </button>
        )}
        <button
          type="button"
          className="icon-button small"
          onClick={onDismiss}
          aria-label="關閉錯誤訊息"
        >
          <XIcon size={16} />
        </button>
      </div>
    </div>
  );
}
