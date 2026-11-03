import type { RateLimitError } from "./errors";

// Callers flatten errors to plain strings before they reach a banner. Remember
// which messages were daily-limit answers (and their Retry-After) so every
// banner can render them as a calm notice instead of a failure.
const seen = new Map<string, number | null>();

export function rememberRateLimit(error: RateLimitError): void {
  seen.set(error.message, error.retryAfterSeconds);
}

/** `null` when the message is not a daily-limit answer. */
export function rateLimitInfo(message: string): { retryAfterSeconds: number | null } | null {
  return seen.has(message) ? { retryAfterSeconds: seen.get(message) ?? null } : null;
}

/** "約 3 小時" style duration for a Retry-After value. */
export function formatWait(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分鐘`;
  return `${Math.round(seconds / 3600)} 小時`;
}
