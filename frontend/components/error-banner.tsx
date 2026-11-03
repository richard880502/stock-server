export function ErrorBanner({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss: () => void;
}) {
  return (
    <div className="error-banner" role="alert">
      <strong>目前無法完成這項操作</strong>
      <span>{message}</span>
      <button type="button" onClick={onDismiss} aria-label="關閉錯誤訊息">
        ×
      </button>
    </div>
  );
}
