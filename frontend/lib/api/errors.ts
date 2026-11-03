export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * The backend returned HTTP 503 `sync_pending` (or an SSE `error` event with
 * status 503): history for the symbol is being fetched by a background worker
 * and the same request should succeed shortly.
 */
export class SyncPendingError extends ApiError {
  /** Seconds from the Retry-After header; null when absent or unreadable. */
  readonly retryAfterSeconds: number | null;
  readonly symbol: string | null;

  constructor(
    message: string,
    options: { retryAfterSeconds?: number | null; symbol?: string | null } = {},
  ) {
    super(message, 503);
    this.name = "SyncPendingError";
    this.retryAfterSeconds = options.retryAfterSeconds ?? null;
    this.symbol = options.symbol ?? null;
  }
}

/** Automatic retries were exhausted while the data was still syncing. */
export class SyncTimeoutError extends Error {
  constructor(attempts: number, symbol: string | null) {
    super(
      `${symbol ? `${symbol} ` : ""}的歷史資料仍在同步中，已自動嘗試 ${attempts} 次。請稍後再按一次重新嘗試。`,
    );
    this.name = "SyncTimeoutError";
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
