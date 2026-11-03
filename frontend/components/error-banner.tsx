import { AlertIcon, XIcon } from "./icons";

/** Network failures surface as bare "Failed to fetch"; say what actually happened. */
function friendlyMessage(message: string): { text: string; detail?: string } {
  if (/failed to fetch|load failed|networkerror|network request failed/i.test(message)) {
    return {
      text: "無法連線到量化服務。請確認服務已啟動、網路正常，稍後再試一次。",
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
