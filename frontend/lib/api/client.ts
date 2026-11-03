import { withAuthHeaders } from "./auth";
import { ApiError, RateLimitError, SyncPendingError } from "./errors";
import { rememberRateLimit } from "./rate-limit";

export const API_URL =
  process.env.NEXT_PUBLIC_QUANT_API_URL ?? "http://127.0.0.1:18100";

export function apiUrl(path: string): string {
  return `${API_URL}${path}`;
}

/** Retry-After as seconds, accepting either delta-seconds or an HTTP date. */
export function parseRetryAfter(value: string | null): number | null {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return seconds > 0 ? Math.ceil(seconds) : null;
  const date = Date.parse(value);
  if (Number.isNaN(date)) return null;
  const delta = Math.ceil((date - Date.now()) / 1000);
  return delta > 0 ? delta : null;
}

let unauthorizedHandler: (() => void) | null = null;

/** Registered by the session store: called when an API call answers HTTP 401. */
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/** Turn a non-2xx response (and its already-parsed JSON body) into an Error. */
export function errorFromResponse(response: Response, payload: unknown): ApiError {
  const body =
    payload && typeof payload === "object"
      ? (payload as { detail?: unknown; sync_pending?: unknown; symbol?: unknown })
      : null;
  const detail = typeof body?.detail === "string" ? body.detail : null;
  if (response.status === 503 && body?.sync_pending === true) {
    return new SyncPendingError(detail ?? "資料同步中", {
      retryAfterSeconds: parseRetryAfter(response.headers.get("Retry-After")),
      symbol: typeof body.symbol === "string" ? body.symbol : null,
    });
  }
  if (response.status === 429) {
    const error = new RateLimitError(
      detail ?? "今日使用次數已達上限，請稍後再試。",
      parseRetryAfter(response.headers.get("Retry-After")),
    );
    rememberRateLimit(error);
    return error;
  }
  if (response.status === 401) unauthorizedHandler?.();
  return new ApiError(
    detail ?? `服務回應異常（HTTP ${response.status}）`,
    response.status,
  );
}

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, withAuthHeaders(init));
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw errorFromResponse(response, payload);
  return payload as T;
}

export function postJson<T>(
  url: string,
  body: unknown,
  init?: RequestInit,
): Promise<T> {
  return fetchJson<T>(url, {
    ...init,
    method: "POST",
    headers: { ...init?.headers, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
