import { withAuthHeaders } from "./auth";
import { apiUrl, errorFromResponse, fetchJson, postJson } from "./client";
import { ApiError, SyncPendingError } from "./errors";
import { readSse } from "./sse";
import type {
  AnalysisRequest,
  BacktestJob,
  BacktestRequest,
  ChipFlow,
  DataStatus,
  DebateReport,
  ForecastReport,
  ForecastRequest,
  ForecastStatus,
  LlmStatus,
  LlmStreamPayload,
  LoginResponse,
  MarketEnvironment,
  Signal,
  SyncSymbolRequest,
} from "./types";

type Init = Pick<RequestInit, "signal">;

const enc = encodeURIComponent;

export type MarketCode = "TW" | "TWO";

export async function login(password: string): Promise<LoginResponse> {
  // Deliberately unauthenticated: no stored key exists yet.
  const response = await fetch(apiUrl("/api/v1/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      response.status === 401
        ? "密碼不正確，請使用 Quant Signal 的登入密碼。"
        : `登入服務暫時無法使用（HTTP ${response.status}），請稍後重試。`,
      response.status,
    );
  }
  return payload as LoginResponse;
}

export function getSignal(
  query: { symbol: string; asOf: string; benchmark: string },
  init?: Init,
): Promise<Signal> {
  const params = new URLSearchParams({ as_of: query.asOf });
  if (query.benchmark.trim()) params.set("benchmark", query.benchmark.trim());
  return fetchJson<Signal>(
    apiUrl(`/api/v1/signals/${enc(query.symbol.trim())}?${params}`),
    init,
  );
}

export function getMarketEnvironment(
  market: MarketCode,
  asOf: string,
  init?: Init,
): Promise<MarketEnvironment> {
  return fetchJson<MarketEnvironment>(
    apiUrl(`/api/v1/market-environments/${market}?as_of=${enc(asOf)}`),
    init,
  );
}

export function getChipFlow(
  symbol: string,
  asOf: string,
  init?: Init,
): Promise<ChipFlow> {
  return fetchJson<ChipFlow>(
    apiUrl(`/api/v1/chip-flows/${enc(symbol.trim())}?as_of=${enc(asOf)}`),
    init,
  );
}

export const getLlmStatus = () => fetchJson<LlmStatus>(apiUrl("/api/v1/llm/status"));
export const getDataStatus = () => fetchJson<DataStatus>(apiUrl("/api/v1/data/status"));

export const createDebateAnalysis = (body: AnalysisRequest, init?: Init) =>
  postJson<DebateReport>(apiUrl("/api/v1/debate-analyses"), body, init);

export const createBacktest = (body: BacktestRequest, init?: Init) =>
  postJson<BacktestJob>(apiUrl("/api/v1/backtests"), body, init);

export const getBacktest = (id: string, init?: Init) =>
  fetchJson<BacktestJob>(apiUrl(`/api/v1/backtests/${id}`), init);

export const getForecastStatus = () =>
  fetchJson<ForecastStatus>(apiUrl("/api/v1/forecasts/status"));

export const createForecast = (body: ForecastRequest, init?: Init) =>
  postJson<ForecastReport>(apiUrl("/api/v1/forecasts"), body, init);

/** Synchronous official daily-bar sync; unrelated to the 503 sync_pending flow. */
export const syncSymbol = (body: SyncSymbolRequest, init?: Init) =>
  postJson<unknown>(apiUrl("/api/v1/sync/symbol"), body, init);

/**
 * POST /api/v1/llm-analyses/stream. Resolves once the stream ends. Throws
 * SyncPendingError for an HTTP 503 sync_pending response or an SSE `error`
 * event with status 503, and ApiError for any other failure.
 */
export async function streamLlmAnalysis(
  body: AnalysisRequest,
  handlers: { onMeta: (model: string) => void; onDelta: (text: string) => void },
  init?: Init,
): Promise<void> {
  const response = await fetch(
    apiUrl("/api/v1/llm-analyses/stream"),
    withAuthHeaders({
      ...init,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => null);
    throw errorFromResponse(response, payload);
  }

  let streamError = null as ApiError | null;
  await readSse(response.body, ({ event, data }) => {
    const payload = JSON.parse(data) as LlmStreamPayload;
    if (event === "meta" && payload.model) handlers.onMeta(payload.model);
    if (event === "delta" && payload.text) handlers.onDelta(payload.text);
    if (event === "error") {
      const message = payload.message ?? "LLM 串流分析失敗";
      streamError =
        payload.status === 503
          ? new SyncPendingError(message, { symbol: body.symbol })
          : new ApiError(message, payload.status ?? 500);
    }
  });
  if (streamError) throw streamError;
}
